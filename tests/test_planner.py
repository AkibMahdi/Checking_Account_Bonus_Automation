"""Unit tests for the pure scheduling engine."""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from scripts.planner import (  # noqa: E402
    build_plan, feasibility, hard_filters, min_capital_required,
    project_pay_dates, score_offer, tier_variants,
)

TODAY = date(2026, 8, 21)


def base_config(**overrides):
    config = {
        "profile": {
            "state": "NY", "max_concurrent_accounts": 3, "max_hard_pulls_per_6mo": 2,
            "min_bonus_threshold": 150, "avoid_banks": [], "chexsystems_sensitive": False,
            "allow_business_accounts": False, "max_liquid_capital": 20000, "horizon_days": 365,
        },
        "pay_schedule": {
            "cadence": "biweekly", "next_pay_date": "2026-08-28",
            "typical_dd_amount": 2400, "splittable": False, "max_split_accounts": 1,
        },
        "bank_history": [], "hard_pulls": [],
    }
    for section, values in overrides.items():
        config[section] = {**config.get(section, {}), **values} if isinstance(values, dict) else values
    return config


def make_offer(**overrides):
    offer = {
        "id": "test-checking-300-2026",
        "bank": {"name": "Test Bank", "type": "national_bank", "chexsystems": False},
        "account": {"name": "Checking", "category": "checking", "monthly_fee": None,
                    "fee_waiver": None, "personal_or_business": "personal"},
        "bonus": {"amount": 300, "currency": "USD", "tiers": None, "tier_mode": None,
                  "payout_days_after_completion": 15, "taxable_1099int": True},
        "requirements": {
            "direct_deposit": {"required": True, "count": 1, "min_amount_each": 500,
                               "min_amount_cumulative": None, "window_days": 90,
                               "window_starts": "account_open"},
            "min_balance": None, "debit_transactions": None, "bill_pay": None,
            "enrollment_required": None, "credit_card_funding_allowed": None},
        "clawback": {"min_hold_days_after_bonus": 180, "early_close_fee": 0, "notes": None},
        "eligibility": {"cooldown_months": 24, "cooldown_basis": "per_person",
                        "states_included": None, "states_excluded": [],
                        "existing_customer_allowed": False, "pull_type": "soft",
                        "in_branch_required": False, "promo_code": None},
        "dates": {"deadline": "2026-12-31", "enroll_by": None, "first_seen": "2026-06-01"},
        "provenance": {"source_url": "https://testbank.example/offer", "aggregator_urls": [],
                       "last_verified": "2026-08-15", "verification_method": "hand_entered",
                       "confidence": "high", "verified_by": "test"},
    }
    for section, values in overrides.items():
        if isinstance(values, dict) and isinstance(offer.get(section), dict):
            offer[section] = {**offer[section], **values}
        else:
            offer[section] = values
    return offer


# ---------------------------------------------------------------- pay dates
def test_biweekly_projection_is_every_14_days():
    dates = project_pay_dates(date(2026, 8, 28), "biweekly", date(2026, 10, 31))
    assert dates[:3] == [date(2026, 8, 28), date(2026, 9, 11), date(2026, 9, 25)]
    assert all((b - a).days == 14 for a, b in zip(dates, dates[1:]))


def test_biweekly_reality_check_30_vs_60_day_windows():
    """The spec's core arithmetic: ~2 pay dates in 30 days, ~4 in 60."""
    dates = project_pay_dates(date(2026, 9, 1), "biweekly", date(2027, 1, 1))
    in_30 = [d for d in dates if date(2026, 9, 1) <= d <= date(2026, 10, 1)]
    in_60 = [d for d in dates if date(2026, 9, 1) <= d <= date(2026, 10, 31)]
    assert len(in_30) == 3          # inclusive of both endpoints
    assert 4 <= len(in_60) <= 5


def test_semimonthly_gives_two_pay_dates_a_month():
    dates = project_pay_dates(date(2026, 9, 15), "semimonthly", date(2026, 11, 30))
    per_month = {}
    for d in dates:
        per_month[(d.year, d.month)] = per_month.get((d.year, d.month), 0) + 1
    assert all(count == 2 for month, count in per_month.items() if month != (2026, 9))


def test_monthly_handles_month_end_rollover():
    dates = project_pay_dates(date(2026, 1, 31), "monthly", date(2026, 4, 30))
    assert date(2026, 2, 28) in dates       # clamped, not skipped


def test_unknown_cadence_raises():
    with pytest.raises(ValueError):
        project_pay_dates(date(2026, 8, 28), "fortnightly", date(2026, 12, 1))


# ---------------------------------------------------------------- filters
def test_expired_deadline_is_dropped():
    offer = make_offer(dates={"deadline": "2026-08-01", "enroll_by": None, "first_seen": "2026-01-01"})
    assert "deadline passed" in hard_filters(offer, base_config(), TODAY)


def test_state_gating_both_directions():
    included = make_offer(eligibility={"states_included": ["CA", "TX"]})
    assert "not offered in NY" in hard_filters(included, base_config(), TODAY)
    excluded = make_offer(eligibility={"states_excluded": ["NY"]})
    assert "excluded in NY" in hard_filters(excluded, base_config(), TODAY)
    assert hard_filters(make_offer(), base_config(), TODAY) is None


def test_min_bonus_threshold():
    offer = make_offer(bonus={"amount": 100, "currency": "USD", "tiers": None})
    assert "below your" in hard_filters(offer, base_config(), TODAY)


def test_avoid_banks_matches_loosely():
    config = base_config(profile={"avoid_banks": ["test"]})
    assert "avoid_banks" in hard_filters(make_offer(), config, TODAY)


def test_chexsystems_sensitivity_only_drops_known_true():
    config = base_config(profile={"chexsystems_sensitive": True})
    known = make_offer(bank={"name": "Test Bank", "type": "national_bank", "chexsystems": True})
    unknown = make_offer(bank={"name": "Test Bank", "type": "national_bank", "chexsystems": None})
    assert "ChexSystems" in hard_filters(known, config, TODAY)
    assert hard_filters(unknown, config, TODAY) is None


def test_cooldown_blocks_then_clears():
    config = base_config(bank_history=[{"bank": "Test Bank", "account": "Checking",
                                        "bonus_received": "2025-06-01", "bonus_amount": 300}])
    assert "cooldown" in hard_filters(make_offer(), config, TODAY)
    evergreen = make_offer(dates={"deadline": None, "enroll_by": None, "first_seen": "2026-01-01"})
    assert hard_filters(evergreen, config, TODAY) is not None       # still inside the 24mo cooldown
    assert hard_filters(evergreen, config, date(2027, 7, 1)) is None  # cooldown elapsed


def test_in_progress_history_blocks_the_bank():
    config = base_config(bank_history=[{"bank": "Test Bank", "status": "in_progress"}])
    assert "in progress" in hard_filters(make_offer(), config, TODAY)


def test_business_accounts_are_opt_in():
    offer = make_offer(account={"name": "Biz", "category": "business_checking",
                                "personal_or_business": "business"})
    assert "business account" in hard_filters(offer, base_config(), TODAY)
    config = base_config(profile={"allow_business_accounts": True})
    assert hard_filters(offer, config, TODAY) is None


def test_capital_gate_uses_the_cheapest_tier():
    offer = make_offer(bonus={"amount": 1500, "currency": "USD", "tier_mode": "alternative",
                              "tiers": [{"amount": 300, "condition": "deposit 20k", "min_balance": 20000},
                                        {"amount": 1500, "condition": "deposit 100k", "min_balance": 100000}]},
                       requirements={"direct_deposit": {"required": False},
                                     "min_balance": {"amount": 20000}})
    assert min_capital_required(offer) == 20000
    assert hard_filters(offer, base_config(), TODAY) is None
    poor = base_config(profile={"max_liquid_capital": 5000})
    assert "cheapest path needs" in hard_filters(offer, poor, TODAY)


# ---------------------------------------------------------------- feasibility
def pays():
    return project_pay_dates(date(2026, 8, 28), "biweekly", date(2027, 12, 31))


def test_simple_offer_is_feasible_on_the_first_pay_date():
    feas = feasibility(make_offer(), date(2026, 8, 22), pays(), base_config(), 2400)
    assert feas.feasible
    assert feas.dd_dates == [date(2026, 8, 28)]
    assert feas.completion_date == date(2026, 8, 28)


def test_three_deposits_in_thirty_days_is_infeasible_for_biweekly():
    offer = make_offer(requirements={"direct_deposit": {
        "required": True, "count": 3, "min_amount_each": 500, "min_amount_cumulative": None,
        "window_days": 30, "window_starts": "account_open"}})
    feas = feasibility(offer, date(2026, 8, 22), pays(), base_config(), 2400)
    assert not feas.feasible
    assert "needs 3 deposit" in feas.reason


def test_cumulative_requirement_converts_to_a_deposit_count():
    offer = make_offer(requirements={"direct_deposit": {
        "required": True, "count": None, "min_amount_each": None,
        "min_amount_cumulative": 5000, "window_days": 90, "window_starts": "account_open"}})
    feas = feasibility(offer, date(2026, 8, 22), pays(), base_config(), 2400)
    assert feas.feasible and feas.required_deposits == 3      # ceil(5000 / 2400)


def test_cumulative_in_a_short_window_fails_the_way_sofi_does():
    offer = make_offer(requirements={"direct_deposit": {
        "required": True, "count": None, "min_amount_each": None,
        "min_amount_cumulative": 5000, "window_days": 25, "window_starts": "first_deposit"}})
    feas = feasibility(offer, date(2026, 8, 22), pays(), base_config(), 2400)
    assert not feas.feasible and "25-day window" in feas.reason


def test_per_deposit_minimum_blocks_a_small_paycheck():
    offer = make_offer(requirements={"direct_deposit": {
        "required": True, "count": 1, "min_amount_each": 3000, "min_amount_cumulative": None,
        "window_days": 90, "window_starts": "account_open"}})
    feas = feasibility(offer, date(2026, 8, 22), pays(), base_config(), 2400)
    assert not feas.feasible and "each deposit must be" in feas.reason


def test_window_starts_first_deposit_shifts_the_window():
    offer = make_offer(requirements={"direct_deposit": {
        "required": True, "count": 1, "min_amount_each": 500, "min_amount_cumulative": None,
        "window_days": 30, "window_starts": "first_deposit"}})
    feas = feasibility(offer, date(2026, 8, 22), pays(), base_config(), 2400)
    assert feas.window_start == date(2026, 8, 28)     # not the open date
    assert feas.window_end == date(2026, 9, 27)


def test_unknown_window_start_is_flagged_not_silently_assumed():
    offer = make_offer(requirements={"direct_deposit": {
        "required": True, "count": 1, "min_amount_each": 500, "min_amount_cumulative": None,
        "window_days": 90, "window_starts": None}})
    feas = feasibility(offer, date(2026, 8, 22), pays(), base_config(), 2400)
    assert feas.feasible
    assert any("window_starts is unknown" in c for c in feas.caveats)


def test_offer_that_cannot_be_opened_before_its_deadline():
    offer = make_offer(dates={"deadline": "2026-08-25", "enroll_by": None, "first_seen": "2026-01-01"})
    feas = feasibility(offer, date(2026, 9, 1), pays(), base_config(), 2400)
    assert not feas.feasible and "cannot open by" in feas.reason


def test_no_dd_offer_is_feasible_and_says_so():
    offer = make_offer(requirements={"direct_deposit": {"required": False},
                                     "min_balance": {"amount": 10000, "fund_within_days": 15,
                                                     "hold_days": 90}})
    feas = feasibility(offer, date(2026, 8, 22), pays(), base_config(), 2400)
    assert feas.feasible and any("park $10,000" in c for c in feas.caveats)


# ---------------------------------------------------------------- tiers
def test_alternative_tiers_expand_richest_first_and_respect_capital():
    offer = make_offer(bonus={"amount": 1500, "currency": "USD", "tier_mode": "alternative",
                              "tiers": [{"amount": 300, "condition": "20k", "min_balance": 20000},
                                        {"amount": 1500, "condition": "100k", "min_balance": 100000}]},
                       requirements={"direct_deposit": {"required": False},
                                     "min_balance": {"amount": 20000}})
    rich = tier_variants(offer, 200000)
    assert [v["bonus"]["amount"] for v, _ in rich] == [1500, 300]
    poor = tier_variants(offer, 25000)
    assert [v["bonus"]["amount"] for v, _ in poor] == [300]


def test_additive_tiers_are_not_credited_up_front():
    """FourLeaf: $350 now + $100 at 12mo + $100 at 24mo != $550 in 90 days."""
    offer = make_offer(bonus={"amount": 550, "currency": "USD", "tier_mode": "additive",
                              "tiers": [{"amount": 350, "condition": "90 days", "window_days": 90},
                                        {"amount": 100, "condition": "12mo", "window_days": 365},
                                        {"amount": 100, "condition": "24mo", "window_days": 730}]})
    variants = tier_variants(offer, None)
    assert len(variants) == 1
    assert variants[0][0]["bonus"]["amount"] == 350
    assert "additive" in variants[0][1]


def test_repeating_tier_spans_every_cycle():
    offer = make_offer(bonus={"amount": 600, "currency": "USD", "tier_mode": "repeating",
                              "tiers": [{"amount": 100, "condition": "per cycle",
                                         "window_days": 30, "repeatable_cycles": 6}]})
    variant, note = tier_variants(offer, None)[0]
    assert variant["bonus"]["amount"] == 600
    assert variant["requirements"]["direct_deposit"]["window_days"] == 180
    assert "per statement cycle" in note


# ---------------------------------------------------------------- scoring
def test_faster_completion_scores_higher_at_equal_bonus():
    fast = make_offer()
    slow = make_offer(requirements={"direct_deposit": {
        "required": True, "count": 4, "min_amount_each": 500, "min_amount_cumulative": None,
        "window_days": 120, "window_starts": "account_open"}})
    open_date = date(2026, 8, 22)
    fast_score = score_offer(fast, feasibility(fast, open_date, pays(), base_config(), 2400),
                             open_date, 0.04)
    slow_score = score_offer(slow, feasibility(slow, open_date, pays(), base_config(), 2400),
                             open_date, 0.04)
    assert fast_score["score"] > slow_score["score"]


def test_risk_penalties_accumulate():
    risky = make_offer(
        bank={"name": "Risky", "type": "national_bank", "chexsystems": True},
        eligibility={"pull_type": "hard"},
        provenance={"source_url": "https://x.example", "confidence": "low",
                    "last_verified": "2026-08-15", "verification_method": "user_reported",
                    "verified_by": "someone", "aggregator_urls": []})
    open_date = date(2026, 8, 22)
    result = score_offer(risky, feasibility(risky, open_date, pays(), base_config(), 2400),
                         open_date, 0.04)
    assert result["risk_penalty"] == pytest.approx(15 + 8 + 12)
    assert len(result["risk_reasons"]) == 3


def test_parked_capital_can_make_a_big_bonus_a_bad_deal():
    """$3,000 for parking $300,000 for 8 months is negative once cash has a price."""
    offer = make_offer(
        bonus={"amount": 3000, "currency": "USD", "tiers": None, "tier_mode": None},
        requirements={"direct_deposit": {"required": False},
                      "min_balance": {"amount": 300000, "fund_within_days": 60, "hold_days": 180}})
    open_date = date(2026, 8, 22)
    result = score_offer(offer, feasibility(offer, open_date, pays(), base_config(), 2400),
                         open_date, 0.04)
    assert result["capital_opportunity_cost"] > 3000
    assert result["net_bonus"] < 0


# ---------------------------------------------------------------- slotting
def test_concurrency_cap_is_respected():
    offers = [make_offer(id=f"offer-{i}-300-2026",
                         bank={"name": f"Bank {i}", "type": "national_bank", "chexsystems": False})
              for i in range(6)]
    config = base_config(profile={"max_concurrent_accounts": 2},
                         pay_schedule={"splittable": True, "max_split_accounts": 4})
    plan = build_plan(offers, config, TODAY)
    for day_offset in range(0, 365, 7):
        day = TODAY + timedelta(days=day_offset)
        active = [s for s in plan["selected"] if s["open_date"] <= day < s["bonus_post_date"]]
        assert len(active) <= 2


def test_deposit_splitting_limits_concurrent_dd_offers():
    offers = [make_offer(id=f"offer-{i}-300-2026",
                         bank={"name": f"Bank {i}", "type": "national_bank", "chexsystems": False})
              for i in range(4)]
    config = base_config(pay_schedule={"splittable": False, "max_split_accounts": 1})
    plan = build_plan(offers, config, TODAY)
    for item in plan["selected"]:
        overlapping = [s for s in plan["selected"]
                       if s is not item and s["needs_dd"]
                       and s["open_date"] <= item["open_date"] <= s["completion_date"]]
        assert not overlapping


def test_hard_pull_pacing_is_enforced():
    offers = [make_offer(id=f"pull-{i}-300-2026",
                         bank={"name": f"Bank {i}", "type": "national_bank", "chexsystems": False},
                         eligibility={"pull_type": "hard"}) for i in range(5)]
    config = base_config(profile={"max_hard_pulls_per_6mo": 1},
                         pay_schedule={"splittable": True, "max_split_accounts": 5})
    plan = build_plan(offers, config, TODAY)
    opens = sorted(s["open_date"] for s in plan["selected"] if s["hard_pull"])
    assert all((b - a).days >= 183 for a, b in zip(opens, opens[1:]))


def test_every_offer_is_either_selected_or_explained():
    offers = [make_offer(id=f"offer-{i}-300-2026",
                         bank={"name": f"Bank {i}", "type": "national_bank", "chexsystems": False})
              for i in range(8)]
    plan = build_plan(offers, base_config(), TODAY)
    accounted = {s["offer_id"] for s in plan["selected"]} | {r["offer_id"] for r in plan["rejected"]}
    assert accounted == {o["id"] for o in offers}
    assert all(r["reason"] for r in plan["rejected"])


def test_plan_dates_are_ordered_sensibly():
    plan = build_plan([make_offer()], base_config(), TODAY)
    item = plan["selected"][0]
    assert item["open_date"] <= item["completion_date"] < item["bonus_post_date"]
    assert item["bonus_post_date"] < item["safe_to_close_date"]
    assert item["cooldown_ends"] > item["bonus_post_date"]


def test_stale_next_pay_date_rolls_forward():
    config = base_config(pay_schedule={"next_pay_date": "2026-01-02"})
    plan = build_plan([make_offer()], config, TODAY)
    assert plan["assumptions"]["next_pay_date"] >= TODAY


def test_empty_offer_list_produces_an_empty_plan():
    plan = build_plan([], base_config(), TODAY)
    assert plan["selected"] == [] and plan["totals"]["gross_bonus"] == 0
