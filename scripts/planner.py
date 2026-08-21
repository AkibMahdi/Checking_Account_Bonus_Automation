#!/usr/bin/env python3
"""The scheduling engine.

Pure function: (offers[], user_config, today) -> plan. No network, no writes.
Everything below the `build_plan` line is deterministic and unit-testable.

    python -m scripts.planner                    # uses user-config.yaml, prints JSON
    python -m scripts.planner --out plan.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE in sys.path:
    sys.path.remove(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ---------------------------------------------------------------- assumptions
ACH_SETTLE_DAYS = 1          # a pay date lands in the account the same/next day
DEFAULT_PAYOUT_DAYS = 60     # when the bank doesn't state one
ASSUMED_HOLD_DAYS = 180      # when clawback.min_hold_days_after_bonus is null
DEFAULT_APY = 0.04           # opportunity cost of parked cash (HYSA-ish)
RISK_HARD_PULL = 15.0        # $/week penalties, tuned so they matter but don't dominate
RISK_CHEXSYSTEMS = 8.0
RISK_UNKNOWN_CHEX = 3.0
RISK_LOW_CONFIDENCE = 12.0
RISK_MEDIUM_CONFIDENCE = 4.0
RISK_UNKNOWN_WINDOW_START = 6.0


# ---------------------------------------------------------------- pay dates
def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, [31, 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def _last_day(year: int, month: int) -> date:
    return _add_months(date(year, month, 1), 1) - timedelta(days=1)


def project_pay_dates(next_pay: date, cadence: str, horizon_end: date) -> list[date]:
    """Every pay date from next_pay through horizon_end, inclusive."""
    dates: list[date] = []
    if cadence == "weekly":
        step, cursor = timedelta(days=7), next_pay
        while cursor <= horizon_end:
            dates.append(cursor)
            cursor += step
    elif cadence == "biweekly":
        step, cursor = timedelta(days=14), next_pay
        while cursor <= horizon_end:
            dates.append(cursor)
            cursor += step
    elif cadence == "monthly":
        cursor, n = next_pay, 0
        while cursor <= horizon_end:
            dates.append(cursor)
            n += 1
            cursor = _add_months(next_pay, n)
    elif cadence == "semimonthly":
        # Two pay dates a month: the day-of-month of next_pay, and ~15 days later.
        first_day = next_pay.day if next_pay.day <= 15 else next_pay.day - 15
        second_day = first_day + 15
        year, month = next_pay.year, next_pay.month
        while True:
            month_end = _last_day(year, month)
            for day in (first_day, second_day):
                candidate = date(year, month, min(day, month_end.day))
                if next_pay <= candidate <= horizon_end:
                    dates.append(candidate)
            if date(year, month, 1) > horizon_end:
                break
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    else:
        raise ValueError(f"unknown cadence: {cadence!r}")
    return sorted(set(dates))


# ---------------------------------------------------------------- helpers
def _get(obj, *path, default=None):
    for key in path:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(key)
        if obj is None:
            return default
    return obj


def _parse(value) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _norm_bank(name: str) -> str:
    import re
    name = (name or "").lower()
    name = re.sub(r"\b(bank|n\.a\.|na|inc|corp|the|federal|credit union|fcu)\b", " ", name)
    return re.sub(r"[^a-z0-9]+", "", name)


# ---------------------------------------------------------------- tiers
def _deep(obj):
    return json.loads(json.dumps(obj, default=str))


def min_capital_required(offer: dict) -> float:
    """Cheapest capital path into this offer — used by the hard filter."""
    base = (_get(offer, "requirements", "min_balance") or {}).get("amount") or 0
    tiers = _get(offer, "bonus", "tiers")
    if not tiers or _get(offer, "bonus", "tier_mode") != "alternative":
        return base
    needs = [t.get("min_balance") if t.get("min_balance") is not None else base for t in tiers]
    return min(needs) if needs else base


def tier_variants(offer: dict, capital: float | None) -> list[tuple[dict, str | None]]:
    """Expand an offer into the concrete variants a planner can actually schedule.

    alternative -> one variant per affordable tier, richest first.
    additive    -> the near-term tier only; later tiers become a caveat.
    repeating   -> one variant whose window spans every cycle.
    Returns [(effective_offer, note)].
    """
    tiers = _get(offer, "bonus", "tiers")
    mode = _get(offer, "bonus", "tier_mode")
    if not tiers:
        return [(offer, None)]

    if mode == "additive":
        first = min(tiers, key=lambda t: t.get("window_days") or 10**6)
        variant = _deep(offer)
        variant["bonus"]["amount"] = first["amount"]
        later = sum(t["amount"] for t in tiers if t is not first)
        dd = variant["requirements"]["direct_deposit"]
        if first.get("dd_cumulative") is not None:
            dd["min_amount_cumulative"] = first["dd_cumulative"]
        if first.get("dd_count") is not None:
            dd["count"] = first["dd_count"]
        if first.get("window_days"):
            dd["window_days"] = first["window_days"]
        note = (f"tiers are additive: ${first['amount']:,.0f} now, another ${later:,.0f} "
                f"only if you keep the account active for the full anniversary schedule — "
                f"scored on the ${first['amount']:,.0f} you can actually bank near-term")
        return [(variant, note)]

    if mode == "repeating":
        tier = tiers[0]
        cycles = tier.get("repeatable_cycles") or 1
        window = (tier.get("window_days") or 30) * cycles
        variant = _deep(offer)
        variant["bonus"]["amount"] = tier["amount"] * cycles
        dd = variant["requirements"]["direct_deposit"]
        dd["window_days"] = min(window, 730)
        if tier.get("dd_cumulative") is not None:
            dd["min_amount_cumulative"] = tier["dd_cumulative"] * cycles
        if variant["requirements"].get("min_balance") and tier.get("min_balance"):
            variant["requirements"]["min_balance"]["amount"] = tier["min_balance"]
        note = (f"${tier['amount']:,.0f} per statement cycle, up to {cycles} cycles "
                f"(~{window} days) — the full ${tier['amount'] * cycles:,.0f} is a "
                f"{window // 30}-month commitment, not a one-off")
        return [(variant, note)]

    # alternative
    variants = []
    for tier in sorted(tiers, key=lambda t: -t["amount"]):
        if tier["amount"] <= 0:
            continue
        need = tier.get("min_balance")
        if need is None:
            need = (_get(offer, "requirements", "min_balance") or {}).get("amount") or 0
        if need and capital is not None and need > capital:
            continue
        variant = _deep(offer)
        variant["bonus"]["amount"] = tier["amount"]
        dd = variant["requirements"]["direct_deposit"]
        if tier.get("dd_cumulative") is not None:
            dd["min_amount_cumulative"] = tier["dd_cumulative"]
            dd["required"] = True
        if tier.get("dd_count") is not None:
            dd["count"] = tier["dd_count"]
        if tier.get("window_days"):
            dd["window_days"] = tier["window_days"]
        if need:
            mb = variant["requirements"].get("min_balance") or {}
            mb["amount"] = need
            mb.setdefault("fund_within_days", tier.get("window_days"))
            mb.setdefault("hold_days", None)
            variant["requirements"]["min_balance"] = mb
        elif tier.get("dd_cumulative") is not None:
            variant["requirements"]["min_balance"] = None
        note = None
        if tier["amount"] != (_get(offer, "bonus", "amount") or tier["amount"]):
            note = (f"planned at the ${tier['amount']:,.0f} tier, not the "
                    f"${_get(offer, 'bonus', 'amount'):,.0f} headline: {tier['condition']}")
        variants.append((variant, note))
    return variants or [(offer, None)]


# ---------------------------------------------------------------- results
@dataclass
class Rejection:
    offer_id: str
    bank: str
    account: str
    amount: float
    reason: str
    stage: str


@dataclass
class Feasibility:
    feasible: bool
    reason: str = ""
    window_start: date | None = None
    window_end: date | None = None
    dd_dates: list[date] = field(default_factory=list)
    dd_amount_each: float = 0.0
    required_deposits: int = 0
    completion_date: date | None = None
    caveats: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- step 1
def hard_filters(offer: dict, config: dict, today: date) -> str | None:
    """Return a rejection reason, or None if the offer survives."""
    profile = config["profile"]
    account = offer.get("account", {}) or {}
    elig = offer.get("eligibility", {}) or {}

    deadline = _parse(_get(offer, "dates", "deadline"))
    if deadline and deadline < today:
        return f"deadline passed ({deadline})"
    enroll_by = _parse(_get(offer, "dates", "enroll_by"))
    if enroll_by and enroll_by < today:
        return f"enrollment window closed ({enroll_by})"

    amount = _get(offer, "bonus", "amount", default=0) or 0
    if amount < profile["min_bonus_threshold"]:
        return f"${amount:,.0f} is below your ${profile['min_bonus_threshold']:,.0f} threshold"

    state = profile["state"]
    included = elig.get("states_included")
    if included is not None and state not in included:
        return f"not offered in {state} (only {', '.join(included)})"
    if state in (elig.get("states_excluded") or []):
        return f"explicitly excluded in {state}"

    if account.get("personal_or_business") == "business" and not profile["allow_business_accounts"]:
        return "business account (set profile.allow_business_accounts: true to include)"

    bank_name = _get(offer, "bank", "name", default="")
    if any(_norm_bank(b) == _norm_bank(bank_name) for b in profile["avoid_banks"]):
        return f"{bank_name} is in your avoid_banks list"

    chex = _get(offer, "bank", "chexsystems")
    if profile["chexsystems_sensitive"] and chex is True:
        return f"{bank_name} pulls ChexSystems and you flagged yourself sensitive"

    capital = profile.get("max_liquid_capital")
    need = min_capital_required(offer)
    if need and capital is not None and need > capital:
        return f"cheapest path needs ${need:,.0f} parked, you have ${capital:,.0f}"

    # Cooldown from your own history.
    cooldown = elig.get("cooldown_months")
    for entry in config.get("bank_history") or []:
        if _norm_bank(entry.get("bank", "")) != _norm_bank(bank_name):
            continue
        if entry.get("status") == "in_progress":
            return f"you already have an open {bank_name} bonus in progress"
        received = _parse(entry.get("bonus_received"))
        if received and cooldown:
            eligible_on = _add_months(received, cooldown)
            if today < eligible_on:
                return (f"cooldown: you took a {bank_name} bonus on {received}, "
                        f"re-eligible {eligible_on}")
        if received and not cooldown:
            return (f"you took a {bank_name} bonus on {received} and this offer's "
                    "cooldown is unstated — verify before applying")
    return None


# ---------------------------------------------------------------- step 2
def feasibility(offer: dict, open_date: date, pay_dates: list[date], config: dict,
                dd_amount: float) -> Feasibility:
    """Can this offer's requirements actually be met if opened on open_date?"""
    dd = _get(offer, "requirements", "direct_deposit", default={}) or {}
    caveats: list[str] = []

    deadline = _parse(_get(offer, "dates", "deadline"))
    enroll_by = _parse(_get(offer, "dates", "enroll_by"))
    open_by = min([d for d in (deadline, enroll_by) if d], default=None)
    if open_by and open_date > open_by:
        return Feasibility(False, f"cannot open by {open_by}")

    if not dd.get("required"):
        # Balance-only or activity-only offer.
        min_balance = _get(offer, "requirements", "min_balance") or {}
        fund_days = min_balance.get("fund_within_days") or 30
        completion = open_date + timedelta(days=fund_days)
        if min_balance.get("amount"):
            caveats.append(
                f"park ${min_balance['amount']:,.0f} within {fund_days} days"
                + (f" and hold it {min_balance['hold_days']} days" if min_balance.get("hold_days") else ""))
        else:
            caveats.append("no direct deposit required")
        return Feasibility(True, "", open_date, completion, [], 0, 0, completion, caveats)

    window_days = dd.get("window_days")
    if not window_days:
        return Feasibility(False, "direct deposit required but window_days is unknown")

    starts = dd.get("window_starts")
    if starts is None:
        caveats.append("window_starts is unknown — timeline assumes it runs from account opening")
        starts = "account_open"

    earliest_dd = open_date + timedelta(days=ACH_SETTLE_DAYS)
    if starts == "first_deposit":
        first = next((d for d in pay_dates if d >= earliest_dd), None)
        if first is None:
            return Feasibility(False, "no pay date lands after this open date")
        window_start = first
    else:
        window_start = open_date
    window_end = window_start + timedelta(days=window_days)

    each_required = dd.get("min_amount_each")
    if each_required and dd_amount < each_required:
        return Feasibility(
            False,
            f"each deposit must be ${each_required:,.0f}; your available "
            f"${dd_amount:,.0f} per pay date falls short")

    required = dd.get("count") or 0
    cumulative = dd.get("min_amount_cumulative")
    if cumulative:
        if dd_amount <= 0:
            return Feasibility(False, "no direct deposit amount available")
        required = max(required, math.ceil(cumulative / dd_amount))
    required = max(required, 1)

    in_window = [d for d in pay_dates if window_start <= d <= window_end and d >= earliest_dd]
    if len(in_window) < required:
        detail = (f"needs {required} deposit(s) of ${dd_amount:,.0f} inside a "
                  f"{window_days}-day window; your {config['pay_schedule']['cadence']} "
                  f"schedule gives {len(in_window)}")
        if cumulative:
            detail += f" (${cumulative:,.0f} cumulative required)"
        return Feasibility(False, detail, window_start, window_end, in_window, dd_amount, required)

    dd_dates = in_window[:required]
    completion = dd_dates[-1]

    if cumulative and required > (dd.get("count") or 0):
        caveats.append(f"${cumulative:,.0f} cumulative needs {required} "
                       f"deposit{'s' if required != 1 else ''} at ${dd_amount:,.0f} each")
    if deadline and completion > deadline:
        caveats.append(f"final deposit ({completion}) lands after the offer's published "
                       f"end date ({deadline}) — confirm the bank allows this")

    debit = _get(offer, "requirements", "debit_transactions")
    if debit and debit.get("count"):
        window = debit.get("window_days") or window_days
        pace = window / debit["count"]
        note = f"{debit['count']} debit purchases in {window} days (~1 every {pace:.0f} days)"
        if debit.get("min_amount_each"):
            note += f", each ${debit['min_amount_each']:,.0f}+"
        caveats.append(note)

    bill_pay = _get(offer, "requirements", "bill_pay")
    if bill_pay and bill_pay.get("count"):
        caveats.append(f"{bill_pay['count']} bill payments required")

    min_balance = _get(offer, "requirements", "min_balance") or {}
    if min_balance.get("amount"):
        caveats.append(f"also park ${min_balance['amount']:,.0f}")

    if _get(offer, "requirements", "enrollment_required"):
        caveats.append("must enroll in the offer separately from opening the account")
    if _get(offer, "eligibility", "in_branch_required"):
        caveats.append("branch visit required")
    if _get(offer, "account", "monthly_fee"):
        caveats.append(f"${_get(offer, 'account', 'monthly_fee')}/mo fee — "
                       f"{_get(offer, 'account', 'fee_waiver') or 'no waiver stated'}")

    return Feasibility(True, "", window_start, window_end, dd_dates, dd_amount,
                       required, completion, caveats)


# ---------------------------------------------------------------- step 3
def payout_days(offer: dict) -> tuple[int, bool]:
    days = _get(offer, "bonus", "payout_days_after_completion")
    return (days, False) if isinstance(days, int) else (DEFAULT_PAYOUT_DAYS, True)


def hold_days(offer: dict) -> tuple[int, bool]:
    days = _get(offer, "clawback", "min_hold_days_after_bonus")
    return (days, False) if isinstance(days, int) else (ASSUMED_HOLD_DAYS, True)


def capital_cost(offer: dict, apy: float) -> float:
    """Opportunity cost of cash the offer forces you to park."""
    min_balance = _get(offer, "requirements", "min_balance") or {}
    amount = min_balance.get("amount") or 0
    if not amount:
        return 0.0
    days = (min_balance.get("hold_days") or 90) + (min_balance.get("fund_within_days") or 0)
    return amount * apy * (days / 365.0)


def score_offer(offer: dict, feas: Feasibility, open_date: date, apy: float) -> dict:
    amount = _get(offer, "bonus", "amount", default=0) or 0
    days = max((feas.completion_date - open_date).days, 1)
    parked = capital_cost(offer, apy)
    net = amount - parked
    efficiency = net / (days / 7.0)

    penalty = 0.0
    reasons = []
    if _get(offer, "eligibility", "pull_type") == "hard":
        penalty += RISK_HARD_PULL
        reasons.append("hard credit pull")
    chex = _get(offer, "bank", "chexsystems")
    if chex is True:
        penalty += RISK_CHEXSYSTEMS
        reasons.append("pulls ChexSystems")
    elif chex is None:
        penalty += RISK_UNKNOWN_CHEX
        reasons.append("ChexSystems behaviour unknown")
    confidence = _get(offer, "provenance", "confidence")
    if confidence == "low":
        penalty += RISK_LOW_CONFIDENCE
        reasons.append("low-confidence data")
    elif confidence == "medium":
        penalty += RISK_MEDIUM_CONFIDENCE
        reasons.append("aggregator-sourced data")
    if _get(offer, "requirements", "direct_deposit", "window_starts") is None \
            and _get(offer, "requirements", "direct_deposit", "required"):
        penalty += RISK_UNKNOWN_WINDOW_START
        reasons.append("window start semantics unknown")

    return {
        "score": round(efficiency - penalty, 2),
        "efficiency_per_week": round(efficiency, 2),
        "risk_penalty": round(penalty, 2),
        "risk_reasons": reasons,
        "time_to_complete_days": days,
        "capital_opportunity_cost": round(parked, 2),
        "net_bonus": round(net, 2),
    }


# ---------------------------------------------------------------- step 4/5
def build_plan(offers: list[dict], config: dict, today: date, *, apy: float = DEFAULT_APY,
               objective: str = "efficiency") -> dict:
    profile = config["profile"]
    pay = config["pay_schedule"]
    horizon_end = today + timedelta(days=profile["horizon_days"])

    next_pay = _parse(pay["next_pay_date"])
    if next_pay is None:
        raise ValueError("pay_schedule.next_pay_date is not a valid date")
    if next_pay < today:                       # roll a stale config forward
        cadence_days = {"weekly": 7, "biweekly": 14}.get(pay["cadence"])
        if cadence_days:
            behind = (today - next_pay).days
            next_pay += timedelta(days=cadence_days * math.ceil(behind / cadence_days))
        else:
            next_pay = _add_months(next_pay, 1)
    pay_dates = project_pay_dates(next_pay, pay["cadence"], horizon_end + timedelta(days=400))

    rejected: list[Rejection] = []
    candidates: list[dict] = []

    # -- Step 1: hard filters
    for offer in offers:
        reason = hard_filters(offer, config, today)
        if reason:
            rejected.append(Rejection(offer["id"], _get(offer, "bank", "name", default="?"),
                                      _get(offer, "account", "name", default="?"),
                                      _get(offer, "bonus", "amount", default=0) or 0,
                                      reason, "filter"))
        else:
            candidates.append(offer)

    # -- Step 2 + 3: feasibility at the earliest possible open date, then score
    dedicated_dd = float(pay["typical_dd_amount"])
    capital_total = profile.get("max_liquid_capital")
    ranked = []
    for offer in candidates:
        best = None
        last_reason = ""
        floor_reason = ""
        for variant, note in tier_variants(offer, capital_total):
            reachable = _get(variant, "bonus", "amount", default=0) or 0
            if reachable < profile["min_bonus_threshold"]:
                floor_reason = floor_reason or (
                    f"the ${reachable:,.0f} tier is under your "
                    f"${profile['min_bonus_threshold']:,.0f} threshold")
                continue
            feas = feasibility(variant, today + timedelta(days=1), pay_dates, config, dedicated_dd)
            if not feas.feasible:
                last_reason = last_reason or feas.reason
                continue
            score = score_offer(variant, feas, today + timedelta(days=1), apy)
            key = score["net_bonus"] if objective == "total" else score["score"]
            if best is None or key > best[3]:
                best = (variant, score, note, key)
        if best is None:
            reason = "; and ".join(r for r in (last_reason, floor_reason) if r) \
                or "no achievable tier"
            rejected.append(Rejection(offer["id"], _get(offer, "bank", "name", default="?"),
                                      _get(offer, "account", "name", default="?"),
                                      _get(offer, "bonus", "amount", default=0) or 0,
                                      reason, "infeasible"))
            continue
        ranked.append((best[0], best[1], best[2]))
    ranked.sort(
        key=lambda t: t[1]["net_bonus"] if objective == "total" else t[1]["score"],
        reverse=True)

    # -- Step 4: greedy slotting
    max_split = pay["max_split_accounts"] if pay["splittable"] else 1

    by_id = {o["id"]: o for o in offers}
    variant_by_id: dict[str, dict] = {}
    selected: list[dict] = []
    for offer, base_score, tier_note in ranked:
        variant_by_id[offer["id"]] = offer
        placed = None
        blockers: dict[str, int] = {}
        last_infeasible = ""
        cursor = today + timedelta(days=1)
        open_by = min([d for d in (_parse(_get(offer, "dates", "deadline")),
                                   _parse(_get(offer, "dates", "enroll_by"))) if d],
                      default=horizon_end)
        needs_dd = bool(_get(offer, "requirements", "direct_deposit", "required"))
        needs_capital = (_get(offer, "requirements", "min_balance") or {}).get("amount") or 0

        while cursor <= min(open_by, horizon_end):
            active = [s for s in selected if s["open_date"] <= cursor < s["bonus_post_date"]]
            if len(active) >= profile["max_concurrent_accounts"]:
                blockers["concurrency"] = blockers.get("concurrency", 0) + 1
                cursor = min((s["bonus_post_date"] for s in active), default=cursor + timedelta(days=1))
                continue

            pulls = sum(1 for s in selected
                        if s["hard_pull"] and 0 <= (cursor - s["open_date"]).days < 183)
            pulls += sum(1 for h in (config.get("hard_pulls") or [])
                         if (parsed := _parse(h.get("date"))) and 0 <= (cursor - parsed).days < 183)
            if _get(offer, "eligibility", "pull_type") == "hard" \
                    and pulls >= profile["max_hard_pulls_per_6mo"]:
                blockers["hard_pull_pacing"] = blockers.get("hard_pull_pacing", 0) + 1
                cursor += timedelta(days=7)
                continue

            if needs_capital and capital_total is not None:
                committed = sum(s["capital_required"] for s in selected
                                if s["open_date"] <= cursor < s["capital_free_date"])
                if committed + needs_capital > capital_total:
                    blockers["capital"] = blockers.get("capital", 0) + 1
                    cursor += timedelta(days=7)
                    continue

            dd_share = dedicated_dd
            if needs_dd:
                concurrent_dd = [s for s in active if s["needs_dd"]]
                if len(concurrent_dd) >= max_split:
                    blockers["deposit_splitting"] = blockers.get("deposit_splitting", 0) + 1
                    nxt = min((s["completion_date"] for s in concurrent_dd), default=None)
                    cursor = (nxt + timedelta(days=1)) if nxt and nxt > cursor \
                        else cursor + timedelta(days=1)
                    continue
                if pay["splittable"] and concurrent_dd:
                    dd_share = dedicated_dd / (len(concurrent_dd) + 1)

            feas = feasibility(offer, cursor, pay_dates, config, dd_share)
            if feas.feasible and needs_dd and dd_share < dedicated_dd:
                # Splitting the paycheck must not break an offer already in the plan.
                for other in active:
                    if not other["needs_dd"]:
                        continue
                    other_offer = variant_by_id.get(other["offer_id"], by_id[other["offer_id"]])
                    if not feasibility(other_offer, other["open_date"], pay_dates,
                                       config, dd_share).feasible:
                        feas = Feasibility(
                            False,
                            f"splitting your paycheck to fit this would break "
                            f"{other['bank']} {other['account']}")
                        break
            if feas.feasible:
                placed = (cursor, feas, dd_share)
                break
            last_infeasible = feas.reason or last_infeasible
            blockers["timing"] = blockers.get("timing", 0) + 1
            cursor += timedelta(days=1)

        if placed is None:
            label = {
                "concurrency": f"your {profile['max_concurrent_accounts']}-account concurrency cap",
                "deposit_splitting": (f"deposit splitting (you can feed at most {max_split} "
                                      "account(s) per pay cycle)"),
                "capital": "cash already committed to higher-ranked offers",
                "hard_pull_pacing": (f"hard-pull pacing ({profile['max_hard_pulls_per_6mo']} "
                                     "per 6 months)"),
                "timing": "deposit timing",
            }
            top = max(blockers, key=blockers.get) if blockers else "timing"
            reason = f"no open date fits — blocked mostly by {label[top]}"
            if top == "timing" and last_infeasible:
                reason = f"no open date fits — {last_infeasible}"
            elif open_by < horizon_end:
                reason += f"; must open by {open_by}"
            rejected.append(Rejection(
                offer["id"], _get(offer, "bank", "name", default="?"),
                _get(offer, "account", "name", default="?"),
                _get(offer, "bonus", "amount", default=0) or 0,
                reason, "unslotted"))
            continue

        open_date, feas, dd_share = placed
        pay_days, pay_assumed = payout_days(offer)
        hold, hold_assumed = hold_days(offer)
        bonus_post = feas.completion_date + timedelta(days=pay_days)
        safe_to_close = bonus_post + timedelta(days=hold)
        min_balance = _get(offer, "requirements", "min_balance") or {}
        capital_free = open_date + timedelta(
            days=(min_balance.get("fund_within_days") or 0) + (min_balance.get("hold_days") or 0)
        ) if min_balance.get("amount") else open_date

        cooldown = _get(offer, "eligibility", "cooldown_months")
        caveats = list(feas.caveats)
        if pay_assumed:
            caveats.append(f"payout timing not published — assuming {DEFAULT_PAYOUT_DAYS} days")
        if hold_assumed:
            caveats.append(f"no hold period published — assuming {ASSUMED_HOLD_DAYS} days "
                           "before closing is safe")
        if _get(offer, "clawback", "early_close_fee"):
            caveats.append(f"${_get(offer, 'clawback', 'early_close_fee')} early-close fee")
        if cooldown is None:
            caveats.append("cooldown unstated — assume you cannot repeat this soon")
        if _get(offer, "provenance", "confidence") != "high":
            caveats.append(f"{_get(offer, 'provenance', 'confidence')}-confidence data — "
                           "verify terms with the bank before opening")
        if dd_share < dedicated_dd:
            caveats.append(f"assumes your paycheck is split — ${dd_share:,.0f} to this account")

        if tier_note:
            caveats.insert(0, tier_note)
        score = score_offer(offer, feas, open_date, apy)
        selected.append({
            "offer_id": offer["id"],
            "bank": _get(offer, "bank", "name", default="?"),
            "account": _get(offer, "account", "name", default="?"),
            "bonus_amount": _get(offer, "bonus", "amount", default=0) or 0,
            "headline_amount": _get(by_id[offer["id"]], "bonus", "amount", default=0) or 0,
            "promo_code": _get(offer, "eligibility", "promo_code"),
            "source_url": _get(offer, "provenance", "source_url"),
            "confidence": _get(offer, "provenance", "confidence"),
            "open_date": open_date,
            "open_by": open_by,
            "window_start": feas.window_start,
            "window_end": feas.window_end,
            "needs_dd": bool(_get(offer, "requirements", "direct_deposit", "required")),
            "dd_dates": list(feas.dd_dates),
            "dd_amount_each": round(feas.dd_amount_each, 2),
            "required_deposits": feas.required_deposits,
            "completion_date": feas.completion_date,
            "bonus_post_date": bonus_post,
            "safe_to_close_date": safe_to_close,
            "cooldown_ends": _add_months(bonus_post, cooldown) if cooldown else None,
            "hard_pull": _get(offer, "eligibility", "pull_type") == "hard",
            "capital_required": min_balance.get("amount") or 0,
            "capital_free_date": capital_free,
            "caveats": caveats,
            **score,
        })

    selected.sort(key=lambda s: s["open_date"])

    total = sum(s["bonus_amount"] for s in selected)
    net = sum(s["net_bonus"] for s in selected)
    span_end = max((s["bonus_post_date"] for s in selected), default=today)
    weeks = max((span_end - today).days / 7.0, 1)

    return {
        "generated_on": today,
        "horizon_end": horizon_end,
        "assumptions": {
            "cadence": pay["cadence"],
            "next_pay_date": next_pay,
            "typical_dd_amount": pay["typical_dd_amount"],
            "splittable": pay["splittable"],
            "max_split_accounts": max_split,
            "max_concurrent_accounts": profile["max_concurrent_accounts"],
            "max_hard_pulls_per_6mo": profile["max_hard_pulls_per_6mo"],
            "state": profile["state"],
            "apy_used_for_capital_cost": apy,
            "objective": objective,
            "ach_settle_days": ACH_SETTLE_DAYS,
            "assumed_payout_days": DEFAULT_PAYOUT_DAYS,
            "assumed_hold_days": ASSUMED_HOLD_DAYS,
        },
        "totals": {
            "offers_considered": len(offers),
            "selected": len(selected),
            "gross_bonus": round(total, 2),
            "net_of_capital_cost": round(net, 2),
            "dollars_per_week": round(total / weeks, 2),
            "last_bonus_posts": span_end,
        },
        "selected": selected,
        "rejected": [r.__dict__ for r in rejected],
        "pay_dates": [d for d in pay_dates if d <= horizon_end],
    }


# ---------------------------------------------------------------- CLI
def _json_default(obj):
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"not JSON serialisable: {type(obj)}")


def main(argv=None) -> int:
    from scripts.userconfig import load_config, load_offers

    parser = argparse.ArgumentParser(description="Build your bonus plan.")
    parser.add_argument("--config", default=None, help="path to user-config.yaml")
    parser.add_argument("--offers", default=None, help="offers directory")
    parser.add_argument("--out", default=None, help="write plan JSON here")
    parser.add_argument("--today", default=None)
    parser.add_argument("--apy", type=float, default=DEFAULT_APY,
                        help="APY used to price parked capital (default 0.04)")
    parser.add_argument("--objective", choices=["efficiency", "total"], default="efficiency",
                        help="efficiency ranks by $/week (default); total maximises dollars "
                             "banked regardless of how long each takes")
    args = parser.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else date.today()
    config = load_config(args.config)
    offers = load_offers(args.offers) if args.offers else load_offers()
    plan = build_plan(offers, config, today, apy=args.apy, objective=args.objective)

    text = json.dumps(plan, indent=2, default=_json_default)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}: {plan['totals']['selected']} offers, "
              f"${plan['totals']['gross_bonus']:,.0f} gross")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
