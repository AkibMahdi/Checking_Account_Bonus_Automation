"""Tests for validation, the calendar/tax output, extraction helpers and the issue bot."""
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from scripts import calendar as ics  # noqa: E402
from scripts.extract import semantic_diff, slugify, strip_fences  # noqa: E402
from scripts.fetching import clean_text, content_hash  # noqa: E402
from scripts.issue_to_offer import build_offer, parse_issue_body  # noqa: E402
from scripts.userconfig import load_offers  # noqa: E402
from scripts.validate import load_schema, sanity_checks, validate_files  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAY = date(2026, 8, 21)


# ---------------------------------------------------------------- real data
def test_every_shipped_offer_validates():
    paths = [os.path.join(ROOT, "offers", f)
             for f in os.listdir(os.path.join(ROOT, "offers")) if f.endswith(".json")]
    problems = validate_files(paths, TODAY)
    errors = [str(p) for p in problems if p.level == "error"]
    assert not errors, "\n".join(errors)


def test_offer_ids_are_unique_and_match_filenames():
    directory = os.path.join(ROOT, "offers")
    for name in os.listdir(directory):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(directory, name)) as fh:
            assert json.load(fh)["id"] == name[:-5]


def test_high_confidence_offers_never_cite_an_aggregator():
    from scripts.validate import AGGREGATOR_HOSTS, _host, _registrable
    for offer in load_offers():
        if offer["provenance"]["confidence"] == "high":
            host = _registrable(_host(offer["provenance"]["source_url"]))
            assert host not in AGGREGATOR_HOSTS, offer["id"]


def test_headline_amount_matches_the_tier_mode():
    for offer in load_offers():
        tiers = offer["bonus"].get("tiers")
        if not tiers:
            continue
        mode = offer["bonus"].get("tier_mode")
        amount = offer["bonus"]["amount"]
        if mode == "alternative":
            assert amount == max(t["amount"] for t in tiers), offer["id"]
        elif mode == "additive":
            assert amount == sum(t["amount"] for t in tiers), offer["id"]
        elif mode == "repeating":
            tier = tiers[0]
            assert amount == tier["amount"] * (tier.get("repeatable_cycles") or 1), offer["id"]


def test_the_dataset_covers_the_edge_cases_that_break_naive_schemas():
    offers = load_offers()
    assert any(o["bonus"].get("tier_mode") == "additive" for o in offers)
    assert any(o["bonus"].get("tier_mode") == "repeating" for o in offers)
    assert any(o["requirements"]["direct_deposit"].get("min_amount_cumulative") for o in offers)
    assert any(o["requirements"]["direct_deposit"].get("min_amount_each") for o in offers)
    assert any(not o["requirements"]["direct_deposit"]["required"] for o in offers)
    assert any(o["eligibility"].get("states_included") for o in offers)
    assert any(o["eligibility"].get("pull_type") == "hard" for o in offers)
    assert len({o["requirements"]["direct_deposit"].get("window_starts") for o in offers}) >= 3


# ---------------------------------------------------------------- validator
def minimal_offer(**over):
    offer = {
        "id": "x-checking-300-2026",
        "bank": {"name": "X", "type": "national_bank", "chexsystems": None},
        "account": {"name": "Checking", "category": "checking", "personal_or_business": "personal"},
        "bonus": {"amount": 300, "currency": "USD", "tiers": None},
        "requirements": {"direct_deposit": {"required": True, "count": 1, "min_amount_each": 500,
                                            "window_days": 90, "window_starts": "account_open"}},
        "clawback": {}, "eligibility": {"cooldown_months": 12, "states_excluded": []},
        "dates": {"deadline": "2026-12-31", "first_seen": "2026-01-01"},
        "provenance": {"source_url": "https://x.example/o", "last_verified": "2026-08-20",
                       "verification_method": "hand_entered", "confidence": "medium",
                       "verified_by": "test"},
    }
    for key, value in over.items():
        offer[key] = {**offer.get(key, {}), **value} if isinstance(value, dict) else value
    return offer


def errors_for(offer):
    return [p.message for p in sanity_checks(offer, TODAY) if p.level == "error"]


def test_validator_rejects_a_past_deadline():
    offer = minimal_offer(dates={"deadline": "2026-01-01", "first_seen": "2025-01-01"})
    assert any("in the past" in msg for msg in errors_for(offer))


def test_validator_rejects_high_confidence_on_an_aggregator():
    offer = minimal_offer(provenance={"confidence": "high",
                                      "source_url": "https://www.doctorofcredit.com/x"})
    assert any("aggregator" in msg for msg in errors_for(offer))


def test_validator_rejects_a_headline_below_its_own_tiers():
    offer = minimal_offer(bonus={"amount": 300, "currency": "USD",
                                 "tiers": [{"amount": 900, "condition": "more money"}]})
    assert any("less than the largest tier" in msg for msg in errors_for(offer))


def test_validator_rejects_out_of_range_bonuses():
    assert any("outside" in msg for msg in errors_for(minimal_offer(
        bonus={"amount": 99999, "currency": "USD", "tiers": None})))


def test_validator_rejects_contradictory_state_lists():
    offer = minimal_offer(eligibility={"states_included": ["NY"], "states_excluded": ["NY"]})
    assert any("both states_included" in msg for msg in errors_for(offer))


def test_validator_warns_when_window_semantics_are_missing():
    offer = minimal_offer(requirements={"direct_deposit": {"required": True, "count": 1,
                                                           "min_amount_each": 500,
                                                           "window_days": None,
                                                           "window_starts": None}})
    warnings = [p.message for p in sanity_checks(offer, TODAY) if p.level == "warn"]
    assert any("window_days is null" in w for w in warnings)
    assert any("window_starts is null" in w for w in warnings)


def test_shipped_schema_is_valid_json_schema():
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft7Validator.check_schema(load_schema())


# ---------------------------------------------------------------- calendar
def sample_plan():
    return {
        "generated_on": "2026-08-21",
        "selected": [{
            "offer_id": "x-checking-300-2026", "bank": "X Bank; Ltd", "account": "Checking, Plus",
            "bonus_amount": 300, "headline_amount": 300, "promo_code": "CODE1",
            "source_url": "https://x.example/o", "open_date": "2026-08-22", "open_by": "2026-12-31",
            "window_start": "2026-08-22", "window_end": "2026-11-20",
            "dd_dates": ["2026-08-28", "2026-09-11"], "dd_amount_each": 2400,
            "required_deposits": 2, "completion_date": "2026-09-11",
            "bonus_post_date": "2026-09-26", "safe_to_close_date": "2027-03-25",
            "cooldown_ends": "2028-09-26", "capital_required": 0,
            "capital_free_date": "2026-08-22", "caveats": ["a caveat"],
        }],
        "rejected": [], "totals": {}, "assumptions": {},
    }


def test_ics_has_one_event_per_action():
    text = ics.plan_to_ics(sample_plan())
    assert text.count("BEGIN:VEVENT") == 6      # open, 2 DDs, post, close, cooldown
    assert text.startswith("BEGIN:VCALENDAR") and text.rstrip().endswith("END:VCALENDAR")


def test_ics_escapes_special_characters_and_folds_long_lines():
    text = ics.plan_to_ics(sample_plan())
    assert r"X Bank\; Ltd" in text
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))


def test_ics_uids_are_stable_across_runs():
    assert ics.plan_to_ics(sample_plan()) == ics.plan_to_ics(sample_plan())


def test_dd_events_carry_a_three_day_alarm():
    text = ics.plan_to_ics(sample_plan())
    assert "TRIGGER:-P3D" in text


def test_tax_summary_separates_received_from_projected():
    config = {"bank_history": [{"bank": "Old Bank", "account": "Checking",
                                "bonus_received": "2026-02-01", "bonus_amount": 250}]}
    text = ics.tax_summary(sample_plan(), config, 2026)
    assert "$250.00" in text and "$300.00" in text
    assert "Total 2026 bonus income: **$550.00**" in text
    assert "1099-INT" in text


def test_tax_summary_ignores_other_years():
    text = ics.tax_summary(sample_plan(), {"bank_history": []}, 2030)
    assert "No bonuses received or projected in 2030" in text


# ---------------------------------------------------------------- extraction
def test_hash_is_stable_under_whitespace_and_case():
    a = clean_text("<html><body><main><p>Get $300   now</p></main></body></html>")
    assert content_hash(a) == content_hash("  GET $300 NOW  ")


def test_clean_text_strips_chrome():
    html = "<body><nav>menu</nav><script>x()</script><main><p>Bonus $500</p></main><footer>f</footer></body>"
    assert clean_text(html).strip() == "Bonus $500"


def test_strip_fences_survives_a_chatty_model():
    assert json.loads(strip_fences('```json\n{"a": 1}\n```')) == {"a": 1}
    assert json.loads(strip_fences('Here you go:\n{"a": 1}\nHope that helps!')) == {"a": 1}


def test_semantic_diff_ignores_provenance_churn():
    old = {"bonus": {"amount": 300}, "provenance": {"last_verified": "2026-01-01"}}
    new = {"bonus": {"amount": 300}, "provenance": {"last_verified": "2026-08-21"}}
    assert semantic_diff(old, new) == []
    new["bonus"]["amount"] = 400
    assert semantic_diff(old, new) == ["bonus.amount: 300 -> 400"]


def test_slugify_produces_schema_legal_ids():
    import re
    slug = slugify("U.S. Bank", "Smartly Checking", 450, 2026)
    assert re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", slug)


# ---------------------------------------------------------------- issue bot
ISSUE = """### Bank name

Example Bank

### Bank type

regional_bank

### Account name

Everyday Checking

### Account category

checking

### Bonus amount (USD, numbers only)

$350

### Monthly fee (numbers only, blank if none)

_No response_

### Is a direct deposit required?

yes

### Number of direct deposits required

2

### Minimum amount per deposit

500

### Requirement window (days)

90

### When does the window start?

not stated

### Available only in these states (comma-separated, blank = nationwide)

ny, nj

### Credit pull

soft

### Source URL

https://www.doctorofcredit.com/example-bank-350/

### Anything else

Reported by a reader.
"""


def test_issue_form_round_trips_into_a_valid_offer():
    jsonschema = pytest.importorskip("jsonschema")
    offer = build_offer(parse_issue_body(ISSUE), TODAY)
    jsonschema.Draft7Validator(load_schema()).validate(offer)


def test_issue_bot_never_upgrades_an_aggregator_to_high_confidence():
    offer = build_offer(parse_issue_body(ISSUE), TODAY)
    assert offer["provenance"]["confidence"] == "medium"
    assert offer["provenance"]["verification_method"] == "issue_form"


def test_issue_bot_keeps_not_stated_as_null():
    offer = build_offer(parse_issue_body(ISSUE), TODAY)
    assert offer["requirements"]["direct_deposit"]["window_starts"] is None


def test_issue_bot_normalises_states_and_strips_currency_symbols():
    offer = build_offer(parse_issue_body(ISSUE), TODAY)
    assert offer["eligibility"]["states_included"] == ["NY", "NJ"]
    assert offer["bonus"]["amount"] == 350


def test_issue_bot_drops_no_response_fields():
    fields = parse_issue_body(ISSUE)
    assert "monthly_fee" not in fields


# ---------------------------------------------------------------- discovery
INDEX_HTML = """
<html><body>
<nav><a href="/tag/chase/">Chase tag</a></nav>
<ul>
  <li><a href="https://www.doctorofcredit.com/chase-400-checking-bonus/">Chase Total Checking</a>
      — $400 bonus, direct deposit required</li>
  <li><a href="/us-bank-450-checking/">U.S. Bank Smartly Checking</a> $450 with $8,000 in deposits</li>
  <li><a href="https://example.com/logo.png">a picture</a> $900</li>
  <li><a href="https://www.doctorofcredit.com/tiny-credit-union-25/">Tiny Credit Union</a> $25</li>
  <li><a href="https://www.doctorofcredit.com/no-amount-here/">Some Bank offer</a> no figure</li>
</ul>
</body></html>
"""


def test_discover_extracts_only_facts_and_skips_junk_links():
    pytest.importorskip("bs4")
    from scripts.discover import parse_index
    rows = parse_index(INDEX_HTML, "https://www.doctorofcredit.com/best/", min_bonus=150)
    urls = {r["url"] for r in rows}
    assert "https://www.doctorofcredit.com/chase-400-checking-bonus/" in urls
    assert "https://www.doctorofcredit.com/us-bank-450-checking/" in urls   # relative resolved
    assert not any(u.endswith(".png") for u in urls)                        # asset skipped
    assert not any("/tag/" in u for u in urls)                              # index page skipped
    assert not any("tiny-credit-union" in u for u in urls)                  # below min_bonus
    assert not any("no-amount-here" in u for u in urls)                     # no figure to extract
    row = next(r for r in rows if "chase" in r["url"])
    assert row["approx_bonus"] == 400
    assert set(row) == {"bank_guess", "approx_bonus", "url", "found_on", "link_text"}


def test_discover_dedupes_against_tracked_offers():
    from scripts.discover import norm
    assert norm("U.S. Bank") == norm("US Bank")
    assert norm("M&T Bank") == norm("M&T")
    assert norm("Chase") != norm("Citibank")


# ---------------------------------------------------------------- ui build
def test_ui_build_inlines_every_offer_and_leaves_no_placeholders():
    from scripts.build_ui import build
    from datetime import date as _date
    standalone, fragment = build(load_offers(), _date(2026, 8, 21))
    assert "__OFFERS_JSON__" not in standalone and "__BUILT_ON__" not in standalone
    assert standalone.startswith("<!doctype html>") and standalone.rstrip().endswith("</html>")
    assert not fragment.lstrip().startswith("<!doctype")          # Artifact fragment
    assert "<title>Bonus Ladder</title>" in fragment
    for offer in load_offers():
        assert offer["id"] in standalone


def test_ui_has_no_external_dependencies_beyond_google_fonts():
    import re
    from scripts.build_ui import build
    from datetime import date as _date
    standalone, _ = build(load_offers(), _date(2026, 8, 21))
    hosts = set(re.findall(r'(?:src|href)="https?://([^/"]+)', standalone))
    assert hosts <= {"fonts.googleapis.com", "fonts.gstatic.com"}, hosts


def test_ui_ships_client_side_ics_export():
    """The web UI's own .ics download must keep working with no server involved —
    see docs on why this replaced needing `python -m scripts.calendar` for casual use."""
    from scripts.build_ui import build
    from datetime import date as _date
    standalone, _ = build(load_offers(), _date(2026, 8, 21))
    assert 'id="downloadIcs"' in standalone
    for fn in ("planToICS", "downloadICS", "icsEvent", "icsFold", "icsEscape"):
        assert f"function {fn}(" in standalone


def test_ui_ships_full_profile_config_no_terminal_needed():
    """Every knob user-config.yaml exposes (profile, pay_schedule, bank_history, hard_pulls)
    must be settable in the web UI too, so cloning the repo and hand-editing YAML is never
    required just to personalize the plan. All of it stays in localStorage — see loadCfg()/
    saveCfg() in web/ladder/app.js and the privacy line in web/ladder/body.html."""
    from scripts.build_ui import build
    from datetime import date as _date
    standalone, _ = build(load_offers(), _date(2026, 8, 21))
    for field_id in ("state", "minBonus", "concurrent", "capital", "cadence", "nextPay",
                      "ddAmount", "skipBanks", "splittable", "business", "chex",
                      "maxSplit", "hardPulls6mo", "horizonDays"):
        assert f'id="{field_id}"' in standalone, f"missing sidebar control for {field_id}"
    assert 'id="historyRows"' in standalone and 'id="addHistoryRow"' in standalone
    assert 'id="hardPullRows"' in standalone and 'id="addHardPull"' in standalone
    for fn in ("renderHistoryUI", "toggleMaxSplitField"):
        assert f"function {fn}(" in standalone
    # the two Python-only planner behaviours this closes the gap on
    assert "cooldown:" in standalone and "hard pull" in standalone
