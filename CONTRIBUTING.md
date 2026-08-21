# Contributing

The data is the product. Three ways to help, in ascending order of value.

## 1. Report an offer (issue form, no git required)

Open a [new-offer issue](../../issues/new?template=new-offer.yml). A bot converts the form
into a draft offer JSON and opens a PR.

**One rule matters more than all the others: leave a field blank if the page does not
state it.** Blank becomes `null`, and `null` is honest. A guessed `window_starts` produces
a confidently wrong plan, which is worse than no plan.

## 2. Correct an offer

Open a [correction issue](../../issues/new?template=offer-correction.yml) with the offer ID,
the field, and a source. Or edit the JSON and open a PR directly.

## 3. Report a data point — the highest-value contribution here

[Tell us what actually counted as a direct deposit](../../issues/new?template=data-point.yml).

"What counts as a DD" is the biggest unknown in the entire space. No bank documents it
accurately. A single first-hand report — "ACH push from Fidelity did not count at Capital
One, coded as a transfer" — is worth more than any promo page, and it accumulates into the
offer's `data_points` array where the next person will find it.

## Editing offer JSON directly

```bash
git clone https://github.com/AkibMahdi/Checking_Account_Bonus_Automation.git
cd Checking_Account_Bonus_Automation
pip install -r requirements.txt
# edit offers/<id>.json
python -m scripts.validate            # must pass; CI runs the same command
python -m pytest tests -q
```

### The rules the validator enforces

| Rule | Why |
|---|---|
| `id` matches the filename, `<bank>-<account>-<amount>-<year>` | stable references across the CDN bundle |
| `confidence: high` requires a source on the bank's own domain | aggregators paraphrase; banks are authoritative |
| `deadline` is not in the past | `expire-sweep` archives expired offers daily |
| bonus 0–10,000; window 1–730 days | catches unit errors and misplaced decimals |
| One offer file = one requirement path | see below |
| `bonus.amount` agrees with `bonus.tier_mode` | see below |

### One offer file = one requirement path

If a bank offers "$325 for $3,000 in direct deposits **or** $1,500 for parking $200,000",
that's **two files**. Mixing them makes `bonus.amount` meaningless and makes the planner's
feasibility check wrong for both paths. Cross-reference them in `notes`.

### `tier_mode` — read this before adding a tiered offer

- **`alternative`** (the common case) — mutually exclusive paths. `bonus.amount` is the
  largest tier. The planner picks the richest tier you can actually reach.
- **`additive`** — tiers stack over time, e.g. $350 now plus $100 at each anniversary.
  `bonus.amount` is their sum, but the planner only scores the near-term tier. Getting
  this wrong makes a two-year offer look like a 90-day sprint.
- **`repeating`** — one tier earned up to `repeatable_cycles` times. `bonus.amount` is
  amount × cycles, and the timeline spans every cycle.

### Fields people get wrong

- **`window_starts`** — `account_open`, `first_deposit`, `offer_enrollment` or
  `coupon_enrollment`. This one field changes the entire timeline. `null` beats a guess.
- **`min_amount_each` vs `min_amount_cumulative`** — "two deposits of $500" is per-deposit;
  "$5,000 in total direct deposits" is cumulative. For a biweekly earner these are
  completely different constraints.
- **`cooldown_basis`** — `per_person`, `per_household`, `per_ssn` or `per_account`.
- **`states_included: null`** means nationwide. An empty list is an error, not a synonym.

## Provenance

Every merged PR must leave `provenance.verified_by` accurate: `bot` for automated
extraction, `community` for issue submissions, your handle if you checked it yourself.
Bump `last_verified` when you confirm an offer is still live.

## Running the automated pipeline

```bash
export ANTHROPIC_API_KEY=...          # never commit this; CI uses a repository secret
python -m scripts.discover            # aggregator index pages -> data/discovered.json
python -m scripts.extract --refresh-all --dry-run   # fetch + hash, no LLM calls
python -m scripts.extract --queue data/discovered.json --limit 10
```

Extraction is hash-diffed: unchanged pages cost nothing, so steady-state weekly runs make
near-zero model calls.
