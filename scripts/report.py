#!/usr/bin/env python3
"""Plan -> console table or markdown, plus the "why not" section.

The rejection list is the point: a ranking you can't interrogate is a ranking
you can't trust.

    python -m scripts.report                     # console
    python -m scripts.report --format markdown --out plan.md
    python -m scripts.report --plan plan.json    # reuse a saved plan
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE in sys.path:
    sys.path.remove(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

STAGE_LABEL = {
    "filter": "Filtered out",
    "infeasible": "Infeasible on your pay schedule",
    "unslotted": "No room in the calendar",
}


def _d(value) -> str:
    if not value:
        return "—"
    return value if isinstance(value, str) else value.isoformat()


def _money(value) -> str:
    return f"${value:,.0f}"


def _table(rows: list[list[str]], headers: list[str]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()
    sep = "  ".join("-" * widths[i] for i in range(len(headers)))
    body = ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows]
    return "\n".join([line, sep, *body])


def _md_table(rows: list[list[str]], headers: list[str]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(c.replace("|", "\\|") for c in row) + " |" for row in rows]
    return "\n".join(out)


def render(plan: dict, fmt: str = "console") -> str:
    md = fmt == "markdown"
    table = _md_table if md else _table
    h1 = (lambda t: f"# {t}") if md else (lambda t: f"\n{t}\n{'=' * len(t)}")
    h2 = (lambda t: f"\n## {t}") if md else (lambda t: f"\n{t}\n{'-' * len(t)}")
    out: list[str] = []

    totals = plan["totals"]
    assume = plan["assumptions"]
    out.append(h1("Bank bonus plan"))
    out.append(f"Generated {_d(plan['generated_on'])} · horizon {_d(plan['horizon_end'])}")
    out.append("")
    out.append(f"**{totals['selected']} offers** out of {totals['offers_considered']} tracked · "
               f"**{_money(totals['gross_bonus'])} gross** "
               f"({_money(totals['net_of_capital_cost'])} net of parked-cash opportunity cost) · "
               f"{_money(totals['dollars_per_week'])}/week · last bonus posts "
               f"{_d(totals['last_bonus_posts'])}")
    out.append("")
    out.append(f"Assumes {assume['cadence']} pay of {_money(assume['typical_dd_amount'])} "
               f"starting {_d(assume['next_pay_date'])}, "
               f"{'splittable across ' + str(assume['max_split_accounts']) + ' accounts' if assume['splittable'] else 'not splittable'}, "
               f"max {assume['max_concurrent_accounts']} concurrent accounts, "
               f"state {assume['state']}.")

    if plan["selected"]:
        out.append(h2("The plan"))
        rows = []
        for item in plan["selected"]:
            rows.append([
                _d(item["open_date"]),
                f"{item['bank']} {item['account']}"[:38],
                _money(item["bonus_amount"]) + ("*" if item.get("headline_amount")
                                                and item["headline_amount"] != item["bonus_amount"] else ""),
                str(item["required_deposits"]) if item["needs_dd"] else "—",
                _d(item["completion_date"]),
                _d(item["bonus_post_date"]),
                _d(item["safe_to_close_date"]),
                f"{item['score']:.0f}",
            ])
        out.append(table(rows, ["Open by", "Account", "Bonus", "DDs", "Last DD",
                                "Bonus posts", "Safe to close", "Score"]))
        if any(i.get("headline_amount") and i["headline_amount"] != i["bonus_amount"]
               for i in plan["selected"]):
            out.append("")
            out.append("* planned at a lower tier than the headline — see the caveats below.")

        out.append(h2("Step by step"))
        for n, item in enumerate(plan["selected"], 1):
            title = f"{n}. {item['bank']} — {item['account']} ({_money(item['bonus_amount'])})"
            out.append(f"\n**{title}**" if md else f"\n{title}")
            out.append(f"   Open on or after {_d(item['open_date'])}"
                       f" (offer ends {_d(item['open_by'])})")
            if item["promo_code"]:
                out.append(f"   Promo code: {item['promo_code']}")
            if item["needs_dd"]:
                out.append(f"   Requirement window: {_d(item['window_start'])} → "
                           f"{_d(item['window_end'])}")
                for i, dd in enumerate(item["dd_dates"], 1):
                    out.append(f"   DD #{i} of {_money(item['dd_amount_each'])} by {_d(dd)}")
            if item["capital_required"]:
                out.append(f"   Park {_money(item['capital_required'])} "
                           f"until {_d(item['capital_free_date'])}")
            out.append(f"   Bonus expected {_d(item['bonus_post_date'])}; "
                       f"safe to close {_d(item['safe_to_close_date'])}")
            if item["cooldown_ends"]:
                out.append(f"   Re-eligible {_d(item['cooldown_ends'])}")
            out.append(f"   {_money(item['efficiency_per_week'])}/week "
                       f"− {_money(item['risk_penalty'])} risk = score {item['score']:.0f}"
                       + (f" ({', '.join(item['risk_reasons'])})" if item["risk_reasons"] else ""))
            for caveat in item["caveats"]:
                out.append(f"   ! {caveat}")
            out.append(f"   {item['source_url']}")
    else:
        out.append(h2("The plan"))
        out.append("Nothing qualified. See the rejection list below — usually it's the "
                   "state filter or the min_bonus_threshold.")

    out.append(h2("Why not — every offer that didn't make it"))
    by_stage: dict[str, list[dict]] = {}
    for rej in plan["rejected"]:
        by_stage.setdefault(rej["stage"], []).append(rej)
    for stage in ("unslotted", "infeasible", "filter"):
        items = by_stage.get(stage)
        if not items:
            continue
        out.append(f"\n**{STAGE_LABEL[stage]}** ({len(items)})" if md
                   else f"\n{STAGE_LABEL[stage]} ({len(items)})")
        rows = [[f"{r['bank']} {r['account']}"[:38], _money(r["amount"]), r["reason"]]
                for r in sorted(items, key=lambda r: -r["amount"])]
        out.append(table(rows, ["Account", "Bonus", "Reason"]))

    out.append("")
    out.append("---" if md else "")
    out.append("Not financial advice. Bonuses are taxable income; terms change without "
               "notice; verify every requirement with the bank before opening an account. "
               "See DISCLAIMER.md.")
    return "\n".join(out)


def main(argv=None) -> int:
    from scripts.planner import DEFAULT_APY, build_plan
    from scripts.userconfig import load_config, load_offers

    parser = argparse.ArgumentParser(description="Render the plan.")
    parser.add_argument("--plan", default=None, help="existing plan.json (skips replanning)")
    parser.add_argument("--config", default=None)
    parser.add_argument("--format", choices=["console", "markdown"], default="console")
    parser.add_argument("--out", default=None)
    parser.add_argument("--today", default=None)
    parser.add_argument("--apy", type=float, default=DEFAULT_APY)
    parser.add_argument("--objective", choices=["efficiency", "total"], default="efficiency")
    args = parser.parse_args(argv)

    if args.plan:
        with open(args.plan) as fh:
            plan = json.load(fh)
    else:
        today = date.fromisoformat(args.today) if args.today else date.today()
        plan = build_plan(load_offers(), load_config(args.config), today,
                          apy=args.apy, objective=args.objective)

    text = render(plan, args.format)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
