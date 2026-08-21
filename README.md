# Bank Bonus Tracker & Churn Planner

Track every US bank account signup bonus, then get a personalised, calendar-ready plan for
cycling through them on your own pay schedule.

**Shared data is public and crowdsourced. Your data never leaves your machine.**
No accounts, no server, no database. Clone it, edit one YAML file, run one command.

```bash
git clone https://github.com/AkibMahdi/Checking_Account_Bonus_Automation.git
cd Checking_Account_Bonus_Automation
pip install -r requirements.txt
cp user-config.example.yaml user-config.yaml   # edit this — it's gitignored
python -m scripts.report                       # the plan, in your terminal
python -m scripts.calendar --out bonus-plan.ics --tax-summary tax-summary-2026.md
```

## What you get

```
Open by     Account                          Bonus  DDs  Last DD     Bonus posts  Safe to close  Score
----------  -------------------------------  -----  ---  ----------  -----------  -------------  -----
2026-08-22  M&T Bank MyChoice Plus Checking  $400   1    2026-08-28  2026-11-26   2027-05-25     464
2026-08-22  FourLeaf Free Checking           $350*  1    2026-08-28  2026-10-27   2027-04-25     405
2026-08-22  Capital One 360 Performance Sav  $300*  —    2026-09-06  2026-11-05   2027-05-04      26
...
```

Then a step-by-step block per offer with every deposit date, promo code and caveat, and a
**"why not"** section explaining every offer that got dropped and exactly why:

```
No room in the calendar (5)
Account                          Bonus   Reason
-------------------------------  ------  -----------------------------------------------------------
U.S. Bank Smartly Checking       $450    no open date fits — blocked mostly by deposit splitting
                                         (you can feed at most 2 accounts per pay cycle);
                                         must open by 2026-09-08

Infeasible on your pay schedule (1)
SoFi Checking and Savings        $400    needs 3 deposits of $2,400 inside a 25-day window;
                                         your biweekly schedule gives 2
```

The rejection reasons are the point. A ranking you cannot interrogate is a ranking you
cannot trust.

## How it works

```
offers/*.json ──► planner.py ──► plan ──► report.py   (markdown / console)
   ▲                  ▲                └─► calendar.py (.ics + tax summary)
   │                  │
   │            user-config.yaml  (yours, gitignored, never uploaded)
   │
discover.py ──► extract.py ──► validate.py ──► CI
(aggregator      (LLM, hash-     (schema +
 index pages)     diffed)         sanity)
```

| Script | Does |
|---|---|
| `scripts/discover.py` | Scrapes aggregator index pages for bank + amount + link. Emits a work queue. Never a source of truth. |
| `scripts/extract.py` | One generic LLM extractor instead of hundreds of CSS selectors. Hash-diffs page text, so unchanged pages cost nothing. |
| `scripts/validate.py` | Schema validation plus sanity bounds, provenance rules and duplicate detection. The CI gate. |
| `scripts/planner.py` | Pure `(offers, config, today) → plan`. Filters, feasibility, scoring, greedy slotting. No I/O. |
| `scripts/calendar.py` | One VEVENT per action, plus the 1099-INT tax summary. |
| `scripts/report.py` | The table above, plus every rejection reason. |
| `scripts/build_dist.py` | Bundles `dist/offers.json`, `.csv`, `.rss.xml`, `stats.json`. |
| `scripts/issue_to_offer.py` | Turns a GitHub issue form into a draft offer JSON. No LLM — the form already imposed the structure. |

## What the planner actually knows

**Filters** — expired deadlines, state gating, cooldowns from your own history, banks you
avoid, ChexSystems sensitivity, business accounts, capital you don't have, bonuses below
your threshold.

**Feasibility** — projects your pay dates forward and counts how many land inside each
offer's requirement window. This is where most offers die:

> A 60-day window yields ~4 biweekly pay dates, a 30-day window ~2. An offer wanting
> **$5,000 in direct deposits within 25 days** needs $2,500 per paycheck. If you earn
> less, it is not a hard offer — it is an impossible one, and it gets flagged as such
> rather than silently dropped.

It also knows that `window_starts` changes everything (`account_open` vs `first_deposit`
vs `offer_enrollment`), and that `$5,000 cumulative` and `2 deposits of $500` are entirely
different constraints.

**Scoring** — `$/week − risk`, where risk prices hard pulls, ChexSystems, and
aggregator-sourced data. Parked cash is priced too: a $3,000 bonus for holding $300,000 for
eight months is *negative* at 4% APY, and the planner says so.

**Slotting** — walks the calendar, respecting concurrent-account limits, hard-pull pacing
over a trailing 6 months, capital already committed, and how many accounts your employer
can actually split a paycheck across. A slot frees when the bonus **posts**, not when the
account closes; the hold period is tracked separately so you don't trigger a clawback.

Prefer total dollars over speed? `--objective total`.

## Tiered offers

`bonus.tier_mode` exists because hand-writing real offers broke the naive model:

- **`alternative`** — mutually exclusive paths. The planner picks the richest tier your
  cash and paycheck can actually reach.
- **`additive`** — $350 now plus $100 at each anniversary. Headline $550, but only $350 is
  bankable in 90 days, and the planner scores it that way.
- **`repeating`** — $100 per statement cycle × 6 cycles. Headline $600 over six months, not
  one.

## Data

24 hand-verified offers covering national banks, regionals, online banks, a credit union,
neobanks and business accounts — deliberately chosen to stress the schema: tiered bonuses,
cumulative-vs-per-deposit thresholds, four different window-start semantics, state gating,
clawbacks, evergreen offers with no deadline, and pure balance-park offers with no direct
deposit at all.

Consumable without cloning:

```
https://cdn.jsdelivr.net/gh/AkibMahdi/Checking_Account_Bonus_Automation@main/dist/offers.json
```

Also `dist/offers.csv`, `dist/offers.rss.xml` (new offers) and `dist/stats.json`.

## Automation

| Workflow | When | Does |
|---|---|---|
| `validate.yml` | every PR | schema, sanity bounds, dead links, tests |
| `update-offers.yml` | Mondays 06:00 UTC | discover → extract → validate → auto-commit high-confidence changes, PR everything else |
| `expire-sweep.yml` | daily | archives past-deadline offers to `offers/archive/` |
| `build-dist.yml` | merge to main | rebuilds the bundle, publishes the web UI to GitHub Pages |
| `issue-to-offer.yml` | issue opened | converts a contribution form into a PR |

Extraction is hash-diffed, so steady-state runs make near-zero model calls. Set
`ANTHROPIC_API_KEY` as a **repository secret** — never in a file that git can see.

## Web UI

`web/` is a zero-build static page that reads `dist/offers.json` and runs the same planning
logic in the browser. Config lives in `localStorage`; nothing is uploaded, because there is
nowhere to upload it to.

```bash
python -m scripts.build_dist && python -m http.server -d . 8000
# then open http://localhost:8000/web/
```

## Known limits

- **Targeted offers are invisible.** Personalised offers are cookie- and login-gated. This
  project does not pretend otherwise.
- **Geo-gated pages** vary by IP. Those offers stay `confidence: medium`.
- **Silent term changes** are why every run hashes the page text rather than trusting a
  one-time scrape.
- **What counts as a direct deposit** is the biggest unknown in the space and no bank
  documents it honestly. That's what the `data_points` array is for —
  [report yours](../../issues/new?template=data-point.yml).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The highest-value contribution is a data point:
what actually counted as a direct deposit at a given bank.

## Not financial advice

Bonuses are taxable income. Excessive account opening can trigger ChexSystems denials.
Terms change without notice. Verify everything with the bank before you act.
Read [DISCLAIMER.md](DISCLAIMER.md).

MIT licensed.
