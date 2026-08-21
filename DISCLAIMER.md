# Disclaimer

**This is not financial advice.** It is a data set and a scheduling tool. Every decision
is yours.

### The data may be wrong

Offer terms are extracted from public promotional pages by software, or entered by
strangers on the internet. Banks change terms without changing the URL. Pages vary by
IP address, by cookie, and by whether you are logged in. Some offers are targeted and
invisible to this project entirely.

Every offer file carries `provenance.confidence` and `provenance.last_verified`.
**Read both before acting.** `confidence: high` means a bank's own page said it on the
date shown — not that it still says it today. **Verify every requirement with the bank
before you open an account.**

### Bonuses are taxable income

Bank account bonuses are interest income. Banks generally issue a **1099-INT** (some use
1099-MISC) for the year the bonus posts, and they report it to the IRS whether or not the
form reaches you. `scripts/calendar.py --tax-summary` totals them for you. This project
gives you numbers, not tax advice — talk to an accountant.

### Opening many accounts has real consequences

- **ChexSystems.** Banks report account openings, closures and negative balances. Too many
  openings in a short period gets applications denied — sometimes for years, and not only
  at the bank that denied you.
- **Early closure fees.** Several banks charge $25–$50 if you close within 90–180 days.
- **Clawbacks.** Closing before the hold period ends can reverse a bonus you already spent.
- **Hard inquiries.** Some banks pull credit. The planner paces these, but the pacing is
  only as good as the `pull_type` data, which is frequently `unknown`.
- **Monthly fees.** A fee waiver you stop qualifying for after the bonus posts will quietly
  eat it.

### What the planner assumes

The plan is arithmetic over stated terms, not a prediction. In particular it assumes:

- your paycheck arrives on schedule and is coded as a qualifying direct deposit — the single
  biggest unknown in this space, and the reason the `data_points` field exists;
- ACH settles in about a day;
- when a bank publishes no payout timing, the bonus lands 60 days after the last requirement;
- when a bank publishes no hold period, 180 days is assumed before closing is safe;
- parked cash has an opportunity cost (4% APY by default, `--apy` to change it).

Every assumption the planner makes about a specific offer appears as a caveat on that
offer in the report. Read them.

### Scraping and copyright

This project extracts **facts** — amounts, dates, requirements — into its own schema.
Facts are not copyrightable. Aggregators' written summaries are, and are never republished
here. `robots.txt` is respected, requests are rate-limited to roughly one every three
seconds, and the User-Agent identifies the project and links back to it.

### No warranty

Provided as-is, with no warranty of any kind. The authors and contributors are not liable
for denied applications, lost bonuses, fees, clawbacks, tax consequences, or anything else
that follows from using this software or its data.
