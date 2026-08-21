#!/usr/bin/env python3
"""Plan -> .ics, plus the yearly tax summary.

One VEVENT per action, so the plan lands in whatever calendar app you already use.
No push-notification infrastructure to build or maintain.

    python -m scripts.calendar --out bonus-plan.ics
    python -m scripts.calendar --tax-summary tax-summary-2026.md --year 2026

NOTE: this module is named `calendar` to match the project spec, which shadows the
stdlib module of the same name. Every entry point here strips scripts/ from sys.path
and runs as `scripts.calendar`, so the stdlib module stays reachable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE in sys.path:
    sys.path.remove(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

PRODID = "-//bank-bonus-planner//EN"
ALARM_DAYS_BEFORE = 3


def _d(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _stamp(value: date) -> str:
    return value.strftime("%Y%m%d")


def _escape(text: str) -> str:
    return (str(text).replace("\\", "\\\\").replace(";", r"\;")
            .replace(",", r"\,").replace("\n", r"\n"))


def _fold(line: str) -> str:
    """RFC 5545: no line over 75 octets. Continuations start with a space."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    out, chunk = [], b""
    for char in line:
        raw = char.encode("utf-8")
        limit = 75 if not out else 74
        if len(chunk) + len(raw) > limit:
            out.append(chunk.decode("utf-8"))
            chunk = b""
        chunk += raw
    out.append(chunk.decode("utf-8"))
    return "\r\n ".join(out)


def _uid(*parts) -> str:
    digest = hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:20]
    return f"{digest}@bank-bonus-planner"


def _event(summary: str, day: date, description: str, *, alarm: bool, uid_parts: tuple,
           generated: date) -> list[str]:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{_uid(*uid_parts)}",
        f"DTSTAMP:{_stamp(generated)}T000000Z",
        f"DTSTART;VALUE=DATE:{_stamp(day)}",
        f"DTEND;VALUE=DATE:{_stamp(day + timedelta(days=1))}",
        f"SUMMARY:{_escape(summary)}",
        f"DESCRIPTION:{_escape(description)}",
        "TRANSP:TRANSPARENT",
    ]
    if alarm:
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"TRIGGER:-P{ALARM_DAYS_BEFORE}D",
            f"DESCRIPTION:{_escape(summary)}",
            "END:VALARM",
        ]
    lines.append("END:VEVENT")
    return lines


def plan_to_ics(plan: dict) -> str:
    generated = _d(plan["generated_on"])
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "X-WR-CALNAME:Bank bonus plan"]

    for item in plan["selected"]:
        label = f"{item['bank']} {item['account']}"
        base = [f"Bonus: ${item['bonus_amount']:,.0f}"]
        if item.get("headline_amount") and item["headline_amount"] != item["bonus_amount"]:
            base.append(f"(headline ${item['headline_amount']:,.0f} — planned at a lower tier)")
        if item.get("promo_code"):
            base.append(f"Promo code: {item['promo_code']}")
        if item.get("source_url"):
            base.append(item["source_url"])
        caveats = "\n".join(f"- {c}" for c in item.get("caveats", []))
        detail = "\n".join(base) + (f"\n\nCaveats:\n{caveats}" if caveats else "")

        lines += _event(
            f"Open {label} by {_d(item['open_by'])}",
            _d(item["open_date"]),
            detail + f"\n\nOffer ends {_d(item['open_by'])}.",
            alarm=True, uid_parts=(item["offer_id"], "open"), generated=generated)

        for n, when in enumerate(item.get("dd_dates", []), 1):
            lines += _event(
                f"DD #{n} of ${item['dd_amount_each']:,.0f} must land at {item['bank']}",
                _d(when),
                f"{n} of {item['required_deposits']} required deposits.\n"
                f"Window {_d(item['window_start'])} to {_d(item['window_end'])}.\n" + detail,
                alarm=True, uid_parts=(item["offer_id"], "dd", n), generated=generated)

        if item.get("capital_required"):
            lines += _event(
                f"Park ${item['capital_required']:,.0f} at {item['bank']}",
                _d(item["open_date"]),
                f"Balance must stay put until {_d(item['capital_free_date'])}.\n" + detail,
                alarm=True, uid_parts=(item["offer_id"], "capital"), generated=generated)

        lines += _event(
            f"Expected bonus post: {item['bank']} ${item['bonus_amount']:,.0f}",
            _d(item["bonus_post_date"]),
            "Checkpoint — if it hasn't posted, call the bank before the terms lapse.\n" + detail,
            alarm=False, uid_parts=(item["offer_id"], "post"), generated=generated)

        lines += _event(
            f"Safe to close {item['bank']} (hold period ended)",
            _d(item["safe_to_close_date"]),
            "Closing before this date risks a clawback of the bonus.\n" + detail,
            alarm=True, uid_parts=(item["offer_id"], "close"), generated=generated)

        if item.get("cooldown_ends"):
            lines += _event(
                f"Cooldown ends for {item['bank']} — re-eligible",
                _d(item["cooldown_ends"]),
                "You can chase this bonus again from today.\n" + detail,
                alarm=False, uid_parts=(item["offer_id"], "cooldown"), generated=generated)

    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def tax_summary(plan: dict, config: dict, year: int) -> str:
    """Bonuses are 1099-INT income. This is the number your accountant wants."""
    rows: list[tuple[str, str, float, str]] = []

    for entry in config.get("bank_history") or []:
        received = entry.get("bonus_received")
        if received and _d(received).year == year:
            rows.append((received, f"{entry['bank']} {entry.get('account') or ''}".strip(),
                         float(entry.get("bonus_amount") or 0), "received"))

    for item in plan.get("selected", []):
        posts = _d(item["bonus_post_date"])
        if posts.year == year:
            rows.append((posts.isoformat(), f"{item['bank']} {item['account']}",
                         float(item["bonus_amount"]), "projected"))

    rows.sort()
    received_total = sum(r[2] for r in rows if r[3] == "received")
    projected_total = sum(r[2] for r in rows if r[3] == "projected")

    out = [f"# Bank bonus tax summary — {year}", "",
           "Bank account signup bonuses are interest income. Banks generally issue a "
           "**1099-INT** (some use 1099-MISC) for any year in which a bonus posts, and they "
           "report it to the IRS whether or not you get the form.", "",
           f"- Already received in {year}: **${received_total:,.2f}**",
           f"- Projected to post in {year}: **${projected_total:,.2f}**",
           f"- Total {year} bonus income: **${received_total + projected_total:,.2f}**", ""]

    if rows:
        out += ["| Date | Bank / account | Amount | Status |", "|---|---|---:|---|"]
        out += [f"| {d} | {name} | ${amount:,.2f} | {status} |" for d, name, amount, status in rows]
    else:
        out.append(f"No bonuses received or projected in {year}.")

    out += ["", "---", "",
            "Projected rows are estimates from the plan, not statements of fact — a bonus "
            "counts in the tax year it actually posts. Not tax advice; see DISCLAIMER.md."]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    from scripts.planner import DEFAULT_APY, build_plan
    from scripts.userconfig import load_config, load_offers

    parser = argparse.ArgumentParser(description="Turn the plan into calendar events.")
    parser.add_argument("--plan", default=None, help="existing plan.json")
    parser.add_argument("--config", default=None)
    parser.add_argument("--out", default="bonus-plan.ics")
    parser.add_argument("--tax-summary", default=None, help="also write a tax summary here")
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--today", default=None)
    parser.add_argument("--apy", type=float, default=DEFAULT_APY)
    parser.add_argument("--objective", choices=["efficiency", "total"], default="efficiency")
    args = parser.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else date.today()
    config = load_config(args.config)
    if args.plan:
        with open(args.plan) as fh:
            plan = json.load(fh)
    else:
        plan = build_plan(load_offers(), config, today, apy=args.apy, objective=args.objective)

    with open(args.out, "w", newline="") as fh:
        fh.write(plan_to_ics(plan))
    events = plan_to_ics(plan).count("BEGIN:VEVENT")
    print(f"wrote {args.out}: {events} events across {len(plan['selected'])} offers")

    if args.tax_summary:
        year = args.year or today.year
        with open(args.tax_summary, "w") as fh:
            fh.write(tax_summary(plan, config, year))
        print(f"wrote {args.tax_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
