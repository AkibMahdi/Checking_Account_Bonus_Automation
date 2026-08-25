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
| `scripts/build_ui.py` | Inlines the offers into `web/bonus-ladder.html` — one file, no server. |
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

Extraction is hash-diffed, so steady-state runs make near-zero model calls.
`update-offers.yml` authenticates via **workload identity federation** — GitHub issues
this job a short-lived OIDC token, which Anthropic exchanges for API access, so no
static key is stored in the repo at all. See `docs/workload-identity.md` for how it's
wired up and how to fall back to a plain `ANTHROPIC_API_KEY` secret if you'd rather
not use WIF. For any *local* run of `scripts/extract.py`, export `ANTHROPIC_API_KEY`
yourself — never commit it, never put it in a file git can see.

**Discovery and extraction, at more than 24 offers' worth of scale.** The 24 offers
shipped in `offers/*.json` are a hand-verified seed set, not a ceiling — `discover.py`
and `extract.py` are what actually find and add more, and both got wider:

- `discover.py` now scrapes **12 aggregator index pages** (Doctor of Credit, Bankrate,
  Forbes Advisor, NerdWallet, Finder, MoneysMyLife, U.S. News, WalletHub, Fortune,
  BankBonus, HustlerMoneyBlog) instead of 5, up to 200 candidates a run. Every host it
  reads is in `AGGREGATOR_HOSTS` (`scripts/validate.py`), so nothing sourced from one
  can end up at `confidence: high` — only a bank's own domain earns that.
- `extract.py --workers N` (default **6**) processes URLs concurrently instead of one
  at a time — each is I/O-bound (a page fetch, sometimes an LLM call), so threads
  overlap real waiting time. `scripts/fetching.py` still enforces ~1 request/3 seconds
  *per host* underneath, so a higher worker count parallelizes across different banks
  and aggregators rather than hammering any single one harder. `--workers 1` gets back
  the old one-at-a-time behavior if you'd rather not run threads.
- `update-offers.yml`'s manual dispatch now exposes `workers`, `limit` (tracked-offer
  re-checks, default 150) and `new_limit` (newly-discovered URLs, default 60) as
  separate inputs, so you can scale a run up or down from the Actions tab without
  editing the workflow file.

None of this changes *when* the scheduled run fires — still Mondays 06:00 UTC — since
that's a real cost/frequency tradeoff (more LLM calls, more Actions minutes) that's
yours to make, not something to default up quietly. Bump `on.schedule.cron` in
`update-offers.yml` if you want it more often; hash-diffing keeps an unchanged page
essentially free either way, so the main cost of running more often is re-fetching
pages that mostly haven't changed, not re-running the model on them.

Both scripts only reach the open internet, which this repo's own CI runner has and a
sandboxed dev environment often doesn't — if `discover.py` reports `ProxyError` or
similar for every index page, that's a network-egress restriction in whatever
environment you're running it from, not a bug in the script. `workflow_dispatch` (the
"Run workflow" button on the Actions tab) is the reliable way to trigger an
unrestricted run on demand instead of waiting for Monday.

## The interface

**`web/bonus-ladder.html` — double-click it.** One self-contained file: every offer is
inlined, so there's no server, no build step and no Python. It runs the same filters,
feasibility check, scoring and slotting as `planner.py`, and adds a dated schedule chart
showing each account's requirement window, the wait for the bonus, and the hold period you
can't close inside. Settings live in `localStorage` — nothing is uploaded, because there is
nowhere to upload it to.

**Every field in `user-config.yaml` has a web equivalent — cloning the repo and hand-editing
YAML is optional, not required.** The sidebar covers the full `profile` and `pay_schedule`
blocks (state, minimum bonus, concurrent-account limit, cash you can park, pay cadence and
amount, how many accounts your paycheck can be split across, hard-pull pacing, how far ahead
to plan), plus an optional "Your history" panel for `bank_history` and `hard_pulls` — the two
things the planner needs but can't infer from the offer data itself. Add a past or in-progress
account and the sidebar's cooldown check picks it up immediately (same `_add_months`/
`_norm_bank` logic as `hard_filters()` in `planner.py`, ported line-for-line to
`hardFilter()` in `web/ladder/app.js`); log a hard pull and it counts toward the 6-month
pacing limit the same way `build_plan()`'s slotting loop does. Everything — including that
history — is `JSON.stringify`'d into `localStorage` on this one device and never touches a
network request; "Reset to defaults" clears it. There is deliberately no cloud sync between
devices, for the same reason there's no server: nowhere for the data to leave the browser to.

**Calendar export needs no terminal either.** The "Download calendar (.ics)" button builds
the same file `scripts/calendar.py` would, entirely client-side (see `planToICS()` in
`web/ladder/app.js`), and hands it to the browser as a normal download. Import it into
Google Calendar (Settings → Import & export → Import), double-click it for Apple Calendar,
or drag it into Outlook. There's no live sync — re-download after you change the plan and
re-import to update it — because a real two-way Google Calendar connection needs OAuth and
a server holding your token, which would break the "nothing leaves your browser" design of
this page. The one-file download is the version of this that doesn't need one.

**Sharing it as something people install, not a repo they clone.** The page is a real
(if minimal) PWA: `web/ladder/head.html` embeds a web manifest, an `apple-touch-icon`,
and the `apple-mobile-web-app-capable` meta tags, all as `data:` URIs — no separate
files, so this works wherever the single HTML file is hosted. On an iPhone: open the
page's URL in Safari, tap Share, tap **Add to Home Screen**. It gets its own icon (a
simple ladder glyph, `scripts/make_icons.py` if you want to redraw it) and opens
full-screen with no address bar — indistinguishable from an installed app, no App
Store involved. The same works on Android Chrome.

That needs a URL, though, not a local file (iOS Safari's Add to Home Screen doesn't
do much with `file://` pages) — two are already live once `build-dist.yml` runs on a
push to `main`:

- **GitHub Pages**, once you flip it on: repo Settings → Pages → Build and deployment
  → Source → **GitHub Actions** (one-time, GitHub doesn't enable this by default even
  though the workflow is already written). After that, `build-dist.yml` publishes this
  exact page to `https://<you>.github.io/<repo>/` on every push — that's the URL to
  actually hand someone.
- **The hosted Artifact** this was built and iterated in, which needs no GitHub setup
  at all and updates daily from the scheduled refresh: see the top of this repo's
  Claude Project for the current link.

Rebuild the page itself whenever the offer data changes:

```bash
python -m scripts.build_ui        # web/ladder/* + offers/*.json -> web/bonus-ladder.html
```

`web/index.html` is the older, lighter page that fetches `dist/offers.json` at runtime —
useful if you want the data served separately rather than baked in:

```bash
python -m scripts.build_dist && python -m http.server 8000
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
