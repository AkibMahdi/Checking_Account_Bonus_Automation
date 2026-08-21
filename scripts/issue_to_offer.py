#!/usr/bin/env python3
"""Contribution bot: GitHub issue-form body -> draft offer JSON (or a data point).

GitHub renders issue forms into markdown as `### Label\\n\\nvalue`. This parses that
back into fields, maps them onto the schema, and writes a draft file for a PR.
No LLM involved — the issue form already imposed the structure.

    python -m scripts.issue_to_offer --body-file issue.md --kind new-offer
    echo "$ISSUE_BODY" | python -m scripts.issue_to_offer --kind data-point
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE in sys.path:
    sys.path.remove(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

OFFERS_DIR = os.path.join(_ROOT, "offers")
NO_RESPONSE = {"_no response_", "_none_", "n/a", "na", "none", "", "not stated"}

FIELD_ALIASES = {
    "bank name": "bank_name",
    "bank type": "bank_type",
    "account name": "account_name",
    "account category": "account_category",
    "bonus amount (usd, numbers only)": "bonus_amount",
    "monthly fee (numbers only, blank if none)": "monthly_fee",
    "is a direct deposit required?": "dd_required",
    "number of direct deposits required": "dd_count",
    "minimum amount per deposit": "dd_min_each",
    "minimum cumulative deposits across the window": "dd_min_cumulative",
    "requirement window (days)": "dd_window_days",
    "when does the window start?": "dd_window_starts",
    "minimum balance required (usd)": "min_balance",
    "debit card transactions required (count)": "debit_transactions",
    "offer deadline (yyyy-mm-dd)": "deadline",
    "available only in these states (comma-separated, blank = nationwide)": "states_included",
    "cooldown before you can get this bonus again (months)": "cooldown_months",
    "credit pull": "pull_type",
    "promo code": "promo_code",
    "source url": "source_url",
    "anything else": "notes",
    # data point
    "offer id": "offer_id",
    "did it count?": "counted",
    "how was the money sent?": "method",
    "amount (usd)": "amount",
    "date it posted (yyyy-mm-dd)": "date",
    # correction
    "which field is wrong?": "field",
    "current value in the repo": "current_value",
    "correct value": "correct_value",
    "source for the correction": "source_url",
    "context": "notes",
}

AGGREGATOR_HOSTS = None  # imported lazily to keep this module standalone-friendly


def parse_issue_body(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    current = None
    buffer: list[str] = []
    for line in body.replace("\r\n", "\n").split("\n"):
        heading = re.match(r"^#{2,4}\s+(.*\S)\s*$", line)
        if heading:
            if current:
                fields[current] = "\n".join(buffer).strip()
            label = heading.group(1).strip().lower()
            current = FIELD_ALIASES.get(label, re.sub(r"[^a-z0-9]+", "_", label).strip("_"))
            buffer = []
        elif current:
            buffer.append(line)
    if current:
        fields[current] = "\n".join(buffer).strip()
    return {k: v for k, v in fields.items() if v.strip().lower() not in NO_RESPONSE}


def _num(fields: dict, key: str):
    raw = fields.get(key)
    if raw is None:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", raw)
    if not cleaned:
        return None
    value = float(cleaned)
    return int(value) if value.is_integer() else value


def _int(fields: dict, key: str):
    value = _num(fields, key)
    return int(value) if value is not None else None


def _str(fields: dict, key: str):
    value = (fields.get(key) or "").strip()
    return value or None


def _bool(fields: dict, key: str):
    value = (fields.get(key) or "").strip().lower()
    if value in ("yes", "true"):
        return True
    if value in ("no", "false"):
        return False
    return None


def slug(*parts) -> str:
    text = "-".join(str(p) for p in parts if p not in (None, ""))
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return re.sub(r"-{2,}", "-", text)


def build_offer(fields: dict, today: date) -> dict:
    from urllib.parse import urlparse
    from scripts.validate import AGGREGATOR_HOSTS as AGGS, _registrable

    source = _str(fields, "source_url") or ""
    host = _registrable((urlparse(source).hostname or "").lower().removeprefix("www."))
    confidence = "medium" if (not host or host in AGGS) else "high"

    states = _str(fields, "states_included")
    states_included = [s.strip().upper() for s in states.split(",") if s.strip()] if states else None

    amount = _num(fields, "bonus_amount") or 0
    deadline = _str(fields, "deadline")
    window_starts = _str(fields, "dd_window_starts")
    if window_starts in (None, "not stated"):
        window_starts = None

    min_balance = _num(fields, "min_balance")
    debit = _int(fields, "debit_transactions")

    offer = {
        "id": slug(fields.get("bank_name"), fields.get("account_name"), amount,
                   (deadline or today.isoformat())[:4]),
        "bank": {
            "name": _str(fields, "bank_name"),
            "type": _str(fields, "bank_type") or "national_bank",
            "chexsystems": None,
        },
        "account": {
            "name": _str(fields, "account_name"),
            "category": _str(fields, "account_category") or "checking",
            "monthly_fee": _num(fields, "monthly_fee"),
            "fee_waiver": None,
            "personal_or_business": "business" if str(
                fields.get("account_category", "")).startswith("business") else "personal",
            "min_opening_deposit": None,
        },
        "bonus": {
            "amount": amount,
            "currency": "USD",
            "tiers": None,
            "tier_mode": None,
            "payout_days_after_completion": None,
            "taxable_1099int": None,
        },
        "requirements": {
            "direct_deposit": {
                "required": bool(_bool(fields, "dd_required")),
                "count": _int(fields, "dd_count"),
                "min_amount_each": _num(fields, "dd_min_each"),
                "min_amount_cumulative": _num(fields, "dd_min_cumulative"),
                "window_days": _int(fields, "dd_window_days"),
                "window_starts": window_starts,
                "qualifying_notes": None,
            },
            "min_balance": {"amount": min_balance, "new_money": None,
                            "fund_within_days": None, "hold_days": None} if min_balance else None,
            "debit_transactions": {"count": debit, "min_amount_each": None,
                                   "window_days": _int(fields, "dd_window_days")} if debit else None,
            "bill_pay": None,
            "enrollment_required": None,
            "credit_card_funding_allowed": None,
        },
        "clawback": {"min_hold_days_after_bonus": None, "early_close_fee": None, "notes": None},
        "eligibility": {
            "cooldown_months": _int(fields, "cooldown_months"),
            "cooldown_basis": "per_person" if _int(fields, "cooldown_months") else None,
            "cooldown_notes": None,
            "states_included": states_included,
            "states_excluded": [],
            "existing_customer_allowed": None,
            "pull_type": _str(fields, "pull_type") or "unknown",
            "in_branch_required": None,
            "promo_code": _str(fields, "promo_code"),
        },
        "dates": {"deadline": deadline, "enroll_by": None, "first_seen": today.isoformat()},
        "provenance": {
            "source_url": source,
            "aggregator_urls": [],
            "last_verified": today.isoformat(),
            "verification_method": "issue_form",
            "confidence": confidence,
            "verified_by": "community",
            "content_hash": None,
        },
        "data_points": [],
        "notes": _str(fields, "notes"),
    }
    return offer


def apply_data_point(fields: dict, today: date) -> tuple[str, dict]:
    offer_id = _str(fields, "offer_id")
    path = os.path.join(OFFERS_DIR, f"{offer_id}.json")
    if not os.path.exists(path):
        raise SystemExit(f"unknown offer id: {offer_id}")
    with open(path) as fh:
        offer = json.load(fh)
    point = {
        "date": _str(fields, "date") or today.isoformat(),
        "counted": bool(_bool(fields, "counted")),
        "method": _str(fields, "method") or "unspecified",
        "amount": _num(fields, "amount"),
        "source": "community issue report",
        "notes": _str(fields, "notes"),
    }
    offer.setdefault("data_points", []).append(point)
    return path, offer


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Turn an issue-form body into repo data.")
    parser.add_argument("--body-file", default=None, help="file holding the issue body (default: stdin)")
    parser.add_argument("--kind", choices=["new-offer", "data-point", "correction"],
                        default="new-offer")
    parser.add_argument("--today", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else date.today()
    body = open(args.body_file).read() if args.body_file else sys.stdin.read()
    fields = parse_issue_body(body)
    if not fields:
        raise SystemExit("could not parse any fields out of the issue body")

    if args.kind == "data-point":
        path, offer = apply_data_point(fields, today)
        print(f"appending data point to {os.path.relpath(path, _ROOT)}")
        print(json.dumps(offer["data_points"][-1], indent=2))
        if not args.dry_run:
            with open(path, "w") as fh:
                json.dump(offer, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
                fh.write(f"path={os.path.relpath(path, _ROOT)}\n")
                fh.write(f"offer_id={offer['id']}\n")
        return 0

    if args.kind == "correction":
        # Corrections need a human: print a structured summary for the PR body.
        print(json.dumps({
            "offer_id": _str(fields, "offer_id"),
            "field": _str(fields, "field"),
            "current_value": _str(fields, "current_value"),
            "correct_value": _str(fields, "correct_value"),
            "source_url": _str(fields, "source_url"),
            "notes": _str(fields, "notes"),
        }, indent=2))
        return 0

    offer = build_offer(fields, today)
    path = os.path.join(OFFERS_DIR, f"{offer['id']}.json")
    if os.path.exists(path):
        print(f"warning: {offer['id']}.json already exists — this will overwrite it",
              file=sys.stderr)
    print(json.dumps(offer, indent=2, ensure_ascii=False))
    if not args.dry_run:
        with open(path, "w") as fh:
            json.dump(offer, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print(f"\nwrote {os.path.relpath(path, _ROOT)}", file=sys.stderr)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write(f"path=offers/{offer['id']}.json\n")
            fh.write(f"offer_id={offer['id']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
