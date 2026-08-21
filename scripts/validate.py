#!/usr/bin/env python3
"""CI gate: schema validation + sanity checks over offers/*.json.

Exit 0 = clean, 1 = errors found. Warnings never fail the build.

    python -m scripts.validate                 # validate everything
    python -m scripts.validate offers/foo.json # validate one file
    python -m scripts.validate --check-links   # also HEAD every source_url (slow)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from urllib.parse import urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE in sys.path:
    sys.path.remove(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

OFFERS_DIR = os.path.join(_ROOT, "offers")
SCHEMA_PATH = os.path.join(_ROOT, "schema", "offer.schema.json")

# Hosts that are aggregators, not the bank itself. `confidence: high` may not cite these.
AGGREGATOR_HOSTS = {
    "doctorofcredit.com", "bankbonus.com", "nerdwallet.com", "hustlermoneyblog.com",
    "forbes.com", "moneysmylife.com", "mymoneyblog.com", "thewaystowealth.com",
    "finder.com", "bankrate.com", "cnbc.com", "reddit.com", "creditkarma.com",
    "businessinsider.com", "wsj.com", "investopedia.com", "gobankingrates.com",
}

MAX_BONUS = 10_000
MAX_WINDOW_DAYS = 730
STALE_DAYS = 45


class Problem:
    def __init__(self, level: str, offer_id: str, message: str):
        self.level = level
        self.offer_id = offer_id
        self.message = message

    def __str__(self) -> str:
        tag = "ERROR" if self.level == "error" else "warn "
        return f"[{tag}] {self.offer_id}: {self.message}"


def _host(url: str) -> str:
    try:
        h = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def _registrable(host: str) -> str:
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def load_schema() -> dict:
    with open(SCHEMA_PATH) as fh:
        return json.load(fh)


def schema_errors(offer: dict, schema: dict) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        return ["__skip__"]
    validator = jsonschema.Draft7Validator(schema)
    out = []
    for err in sorted(validator.iter_errors(offer), key=lambda e: list(e.path)):
        path = "/".join(str(p) for p in err.path) or "<root>"
        out.append(f"schema: {path}: {err.message}")
    return out


def sanity_checks(offer: dict, today: date) -> list[Problem]:
    oid = offer.get("id", "<no id>")
    problems: list[Problem] = []

    def err(msg):
        problems.append(Problem("error", oid, msg))

    def warn(msg):
        problems.append(Problem("warn", oid, msg))

    bonus = offer.get("bonus", {}) or {}
    amount = bonus.get("amount")
    if isinstance(amount, (int, float)):
        if not 0 <= amount <= MAX_BONUS:
            err(f"bonus.amount {amount} outside 0-{MAX_BONUS}")
        if amount == 0:
            warn("bonus.amount is 0 — is this really an offer?")

    tiers = bonus.get("tiers")
    if tiers:
        tier_max = max(t.get("amount", 0) for t in tiers)
        if isinstance(amount, (int, float)) and tier_max > amount:
            err(f"bonus.amount {amount} is less than the largest tier {tier_max}; "
                "headline amount must be the max achievable")

    req = offer.get("requirements", {}) or {}
    dd = req.get("direct_deposit", {}) or {}
    if dd.get("required"):
        if dd.get("window_days") is None:
            warn("direct_deposit.required but window_days is null — planner cannot time this")
        if dd.get("window_starts") is None:
            warn("direct_deposit.required but window_starts is null — timeline is ambiguous")
        if dd.get("min_amount_each") is None and dd.get("min_amount_cumulative") is None:
            warn("direct_deposit.required but neither min_amount_each nor "
                 "min_amount_cumulative is set")
    for field in ("min_amount_each", "min_amount_cumulative"):
        value = dd.get(field)
        if isinstance(value, (int, float)) and value < 0:
            err(f"direct_deposit.{field} is negative")
    window = dd.get("window_days")
    if isinstance(window, int) and not 1 <= window <= MAX_WINDOW_DAYS:
        err(f"direct_deposit.window_days {window} outside 1-{MAX_WINDOW_DAYS}")
    cum, each, count = (dd.get("min_amount_cumulative"), dd.get("min_amount_each"), dd.get("count"))
    if all(isinstance(v, (int, float)) for v in (cum, each, count)) and count:
        if each * count > cum * 1.0001:
            warn(f"min_amount_each({each}) x count({count}) exceeds "
                 f"min_amount_cumulative({cum}) — check which one actually binds")

    dates = offer.get("dates", {}) or {}
    deadline = _parse_date(dates.get("deadline"))
    if dates.get("deadline") and deadline is None:
        err(f"dates.deadline {dates.get('deadline')!r} is not a valid ISO date")
    if deadline and deadline < today:
        err(f"dates.deadline {deadline} is in the past — run expire-sweep to archive it")
    if deadline and (deadline - today).days <= 14:
        warn(f"dates.deadline {deadline} is within 14 days")
    enroll_by = _parse_date(dates.get("enroll_by"))
    if enroll_by and deadline and enroll_by > deadline:
        warn(f"enroll_by {enroll_by} is after deadline {deadline} — verify which gate binds")
    first_seen = _parse_date(dates.get("first_seen"))
    if first_seen and first_seen > today:
        err(f"dates.first_seen {first_seen} is in the future")

    prov = offer.get("provenance", {}) or {}
    src = prov.get("source_url") or ""
    if not src:
        err("provenance.source_url is required")
    last_verified = _parse_date(prov.get("last_verified"))
    if last_verified is None:
        err("provenance.last_verified missing or malformed")
    else:
        age = (today - last_verified).days
        if age > STALE_DAYS:
            warn(f"provenance.last_verified is {age} days old (>{STALE_DAYS}) — re-verify")
        if age < 0:
            err("provenance.last_verified is in the future")

    if prov.get("confidence") == "high":
        host = _registrable(_host(src))
        if not host:
            err("confidence: high but source_url has no host")
        elif host in AGGREGATOR_HOSTS:
            err(f"confidence: high but source_url is an aggregator ({host}); "
                "cite the bank's own domain or downgrade to medium")

    elig = offer.get("eligibility", {}) or {}
    inc, exc = elig.get("states_included"), elig.get("states_excluded") or []
    if inc is not None:
        if not inc:
            err("states_included is an empty list — use null for nationwide")
        overlap = set(inc) & set(exc)
        if overlap:
            err(f"states {sorted(overlap)} appear in both states_included and states_excluded")
    if elig.get("cooldown_months") is None:
        warn("eligibility.cooldown_months is null — planner cannot check re-eligibility")

    clawback = offer.get("clawback", {}) or {}
    hold = clawback.get("min_hold_days_after_bonus")
    if isinstance(hold, int) and hold > 730:
        warn(f"clawback.min_hold_days_after_bonus {hold} is over 2 years — verify")

    account = offer.get("account", {}) or {}
    if account.get("category") in ("business_checking", "business_savings") \
            and account.get("personal_or_business") != "business":
        err("business account category but personal_or_business is not 'business'")

    return problems


def check_link(url: str, timeout: float = 10.0) -> str | None:
    """Return an error string if the URL is clearly dead, else None."""
    try:
        import requests
    except ImportError:
        return None
    headers = {"User-Agent": "bank-bonus-planner/1.0 "
                             "(+https://github.com/AkibMahdi/Checking_Account_Bonus_Automation)"}
    try:
        resp = requests.head(url, allow_redirects=True, timeout=timeout, headers=headers)
        if resp.status_code >= 400:
            resp = requests.get(url, timeout=timeout, headers=headers, stream=True)
        if resp.status_code >= 400:
            return f"source_url returned HTTP {resp.status_code}"
    except Exception as exc:  # network flakiness must not be a hard CI failure
        return f"source_url unreachable ({type(exc).__name__})"
    return None


def validate_files(paths: list[str], today: date, check_links: bool = False):
    schema = load_schema()
    problems: list[Problem] = []
    seen_ids: dict[str, str] = {}
    seen_accounts: dict[tuple, str] = {}
    schema_available = True

    for path in sorted(paths):
        name = os.path.basename(path)
        try:
            with open(path) as fh:
                offer = json.load(fh)
        except json.JSONDecodeError as exc:
            problems.append(Problem("error", name, f"invalid JSON: {exc}"))
            continue

        oid = offer.get("id", "<no id>")
        for msg in schema_errors(offer, schema):
            if msg == "__skip__":
                schema_available = False
                continue
            problems.append(Problem("error", oid, msg))

        expected = os.path.splitext(name)[0]
        if oid != expected:
            problems.append(Problem("error", oid, f"id must match filename ({expected}.json)"))

        if oid in seen_ids:
            problems.append(Problem("error", oid, f"duplicate id (also in {seen_ids[oid]})"))
        seen_ids[oid] = name

        key = ((offer.get("bank", {}) or {}).get("name", "").lower(),
               (offer.get("account", {}) or {}).get("name", "").lower())
        if key != ("", "") and key in seen_accounts:
            problems.append(Problem("warn", oid,
                                    f"same bank+account as {seen_accounts[key]} — "
                                    "is one of them stale?"))
        seen_accounts[key] = name

        problems.extend(sanity_checks(offer, today))

        if check_links:
            src = (offer.get("provenance", {}) or {}).get("source_url")
            if src:
                msg = check_link(src)
                if msg:
                    problems.append(Problem("warn", oid, msg))

    if not schema_available:
        problems.append(Problem("warn", "<setup>",
                                "jsonschema not installed — structural validation skipped. "
                                "pip install -r requirements.txt"))
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate offer JSON files.")
    parser.add_argument("paths", nargs="*", help="offer files (default: offers/*.json)")
    parser.add_argument("--check-links", action="store_true",
                        help="HEAD every source_url (slow, network)")
    parser.add_argument("--today", default=None, help="override today's date (YYYY-MM-DD)")
    parser.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = parser.parse_args(argv)

    today = _parse_date(args.today) or date.today()
    paths = args.paths or [
        os.path.join(OFFERS_DIR, f)
        for f in os.listdir(OFFERS_DIR)
        if f.endswith(".json")
    ]
    if not paths:
        print("No offer files found.")
        return 1

    problems = validate_files(paths, today, check_links=args.check_links)
    errors = [p for p in problems if p.level == "error"]
    warnings = [p for p in problems if p.level == "warn"]

    for problem in problems:
        print(problem)

    print(f"\n{len(paths)} offer file(s): {len(errors)} error(s), {len(warnings)} warning(s)")
    if errors:
        return 1
    if args.strict and warnings:
        print("--strict: warnings treated as errors")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
