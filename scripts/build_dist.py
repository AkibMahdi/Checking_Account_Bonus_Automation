#!/usr/bin/env python3
"""Bundle offers/*.json into dist/ for the CDN and the static web UI.

Emits dist/offers.json, dist/offers.csv, dist/offers.rss.xml and dist/stats.json.

    python -m scripts.build_dist
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE in sys.path:
    sys.path.remove(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.userconfig import load_offers  # noqa: E402

DIST = os.path.join(_ROOT, "dist")
REPO = "https://github.com/AkibMahdi/Checking_Account_Bonus_Automation"
CDN = "https://cdn.jsdelivr.net/gh/AkibMahdi/Checking_Account_Bonus_Automation@main/dist/offers.json"

CSV_COLUMNS = [
    "id", "bank", "bank_type", "account", "category", "personal_or_business",
    "bonus_amount", "tier_mode", "dd_required", "dd_count", "dd_min_each",
    "dd_min_cumulative", "dd_window_days", "dd_window_starts", "min_balance",
    "debit_transactions", "deadline", "states_included", "states_excluded",
    "cooldown_months", "pull_type", "promo_code", "confidence", "last_verified", "source_url",
]


def _g(obj, *path, default=None):
    for key in path:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(key)
        if obj is None:
            return default
    return obj


def csv_row(offer: dict) -> dict:
    states_in = _g(offer, "eligibility", "states_included")
    return {
        "id": offer["id"],
        "bank": _g(offer, "bank", "name"),
        "bank_type": _g(offer, "bank", "type"),
        "account": _g(offer, "account", "name"),
        "category": _g(offer, "account", "category"),
        "personal_or_business": _g(offer, "account", "personal_or_business"),
        "bonus_amount": _g(offer, "bonus", "amount"),
        "tier_mode": _g(offer, "bonus", "tier_mode"),
        "dd_required": _g(offer, "requirements", "direct_deposit", "required"),
        "dd_count": _g(offer, "requirements", "direct_deposit", "count"),
        "dd_min_each": _g(offer, "requirements", "direct_deposit", "min_amount_each"),
        "dd_min_cumulative": _g(offer, "requirements", "direct_deposit", "min_amount_cumulative"),
        "dd_window_days": _g(offer, "requirements", "direct_deposit", "window_days"),
        "dd_window_starts": _g(offer, "requirements", "direct_deposit", "window_starts"),
        "min_balance": _g(offer, "requirements", "min_balance", "amount"),
        "debit_transactions": _g(offer, "requirements", "debit_transactions", "count"),
        "deadline": _g(offer, "dates", "deadline"),
        "states_included": "|".join(states_in) if states_in else "",
        "states_excluded": "|".join(_g(offer, "eligibility", "states_excluded") or []),
        "cooldown_months": _g(offer, "eligibility", "cooldown_months"),
        "pull_type": _g(offer, "eligibility", "pull_type"),
        "promo_code": _g(offer, "eligibility", "promo_code"),
        "confidence": _g(offer, "provenance", "confidence"),
        "last_verified": _g(offer, "provenance", "last_verified"),
        "source_url": _g(offer, "provenance", "source_url"),
    }


def build_stats(offers: list[dict], today: date) -> dict:
    by_conf: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for offer in offers:
        conf = _g(offer, "provenance", "confidence") or "unknown"
        by_conf[conf] = by_conf.get(conf, 0) + 1
        btype = _g(offer, "bank", "type") or "unknown"
        by_type[btype] = by_type.get(btype, 0) + 1
    amounts = [_g(offer, "bonus", "amount") or 0 for offer in offers]
    expiring = [o["id"] for o in offers
                if (d := _g(o, "dates", "deadline"))
                and 0 <= (date.fromisoformat(d) - today).days <= 30]
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "offer_count": len(offers),
        "total_bonus_value": sum(amounts),
        "median_bonus": sorted(amounts)[len(amounts) // 2] if amounts else 0,
        "max_bonus": max(amounts, default=0),
        "by_confidence": dict(sorted(by_conf.items())),
        "by_bank_type": dict(sorted(by_type.items())),
        "expiring_within_30_days": sorted(expiring),
    }


def build_rss(offers: list[dict], limit: int = 40) -> str:
    def esc(text: str) -> str:
        return (str(text).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    recent = sorted(offers, key=lambda o: (_g(o, "dates", "first_seen") or ""), reverse=True)[:limit]
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    items = []
    for offer in recent:
        first_seen = _g(offer, "dates", "first_seen")
        try:
            pub = datetime.fromisoformat(first_seen).replace(tzinfo=timezone.utc) \
                .strftime("%a, %d %b %Y %H:%M:%S +0000")
        except (TypeError, ValueError):
            pub = now
        dd = _g(offer, "requirements", "direct_deposit") or {}
        bits = [f"${_g(offer, 'bonus', 'amount'):,.0f} bonus"]
        if dd.get("required"):
            if dd.get("min_amount_cumulative"):
                bits.append(f"${dd['min_amount_cumulative']:,.0f} in direct deposits")
            elif dd.get("min_amount_each"):
                count = dd.get("count") or 1
                bits.append(f"{count} direct deposit(s) of ${dd['min_amount_each']:,.0f}")
            if dd.get("window_days"):
                bits.append(f"within {dd['window_days']} days")
        else:
            bits.append("no direct deposit required")
        balance = _g(offer, "requirements", "min_balance", "amount")
        if balance:
            bits.append(f"${balance:,.0f} minimum balance")
        deadline = _g(offer, "dates", "deadline")
        if deadline:
            bits.append(f"ends {deadline}")
        bits.append(f"confidence: {_g(offer, 'provenance', 'confidence')}")
        items.append(f"""    <item>
      <title>{esc(_g(offer, 'bank', 'name'))} {esc(_g(offer, 'account', 'name'))} — ${_g(offer, 'bonus', 'amount'):,.0f}</title>
      <link>{esc(_g(offer, 'provenance', 'source_url'))}</link>
      <guid isPermaLink="false">{esc(offer['id'])}</guid>
      <pubDate>{pub}</pubDate>
      <description>{esc('. '.join(bits))}.</description>
    </item>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Bank bonus tracker — new offers</title>
    <link>{REPO}</link>
    <description>Structured US bank account signup bonuses. Facts only, no aggregator prose.</description>
    <lastBuildDate>{now}</lastBuildDate>
    <generator>bank-bonus-planner</generator>
{chr(10).join(items)}
  </channel>
</rss>
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the dist bundle.")
    parser.add_argument("--out", default=DIST)
    parser.add_argument("--today", default=None)
    args = parser.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else date.today()
    offers = sorted(load_offers(), key=lambda o: o["id"])
    os.makedirs(args.out, exist_ok=True)

    bundle = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "schema": f"{REPO}/blob/main/schema/offer.schema.json",
        "cdn": CDN,
        "count": len(offers),
        "offers": offers,
    }
    with open(os.path.join(args.out, "offers.json"), "w") as fh:
        json.dump(bundle, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    with open(os.path.join(args.out, "offers.csv"), "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for offer in offers:
            writer.writerow(csv_row(offer))

    with open(os.path.join(args.out, "offers.rss.xml"), "w") as fh:
        fh.write(build_rss(offers))

    stats = build_stats(offers, today)
    with open(os.path.join(args.out, "stats.json"), "w") as fh:
        json.dump(stats, fh, indent=2)
        fh.write("\n")

    print(f"dist: {len(offers)} offers, ${stats['total_bonus_value']:,.0f} total value, "
          f"{len(stats['expiring_within_30_days'])} expiring within 30 days")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
