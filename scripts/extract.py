#!/usr/bin/env python3
"""LLM extraction: bank promo page -> offer JSON. One generic extractor, no per-bank parsers.

Flow per URL:
  1. Fetch static HTML; escalate to Playwright only if the text body is suspiciously short.
  2. Strip nav/footer/scripts -> clean text.
  3. Hash the cleaned text. Unchanged since last run -> no LLM call, just bump last_verified.
  4. Changed -> send text + schema to the model with a strict "never infer" prompt.
  5. Validate against offer.schema.json. Reject and retry once on failure.
  6. Diff against the existing file. Write only if something actually changed.

Usage:
    export ANTHROPIC_API_KEY=...            # never commit this
    python -m scripts.extract --url https://bank.example/offer
    python -m scripts.extract --queue data/discovered.json --limit 20
    python -m scripts.extract --refresh-all --dry-run
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

from scripts.fetching import clean_text, content_hash, fetch, fetch_rendered  # noqa: E402
from scripts.validate import AGGREGATOR_HOSTS, _host, _registrable, load_schema  # noqa: E402

OFFERS_DIR = os.path.join(_ROOT, "offers")
HASHES_PATH = os.path.join(_ROOT, "data", "hashes.json")
MODEL = os.environ.get("BBP_MODEL", "claude-sonnet-4-5")
MAX_PAGE_CHARS = 60_000
MIN_TEXT_CHARS = 600          # below this we assume the page is JS-rendered

SYSTEM_PROMPT = """You extract bank account bonus terms into JSON. Return ONLY valid JSON \
matching the provided schema. No prose, no markdown fences, no explanation.

Rules:
- Use null for anything not explicitly stated on the page. Never infer, never guess, \
never fill a field from general knowledge about the bank.
- Amounts as numbers without currency symbols or commas.
- Dates as ISO 8601 (YYYY-MM-DD).
- If multiple bonus tiers exist, populate bonus.tiers as an array and set bonus.amount \
to the LARGEST achievable amount.
- One offer file describes ONE requirement path. If the page describes a direct-deposit \
path and a separate balance-funding path, extract the direct-deposit path and note the \
other in `notes`.
- requirements.direct_deposit.window_starts must be one of account_open, first_deposit, \
offer_enrollment, coupon_enrollment — or null if the page does not say. This field \
changes the entire plan timeline; leaving it null is far better than guessing.
- min_amount_each is per-deposit; min_amount_cumulative is the total across the window. \
"$5,000 in total direct deposits" is cumulative. "two deposits of $500 each" is per-deposit.
- provenance.confidence: "high" only if this page is on the bank's own domain, \
"medium" if it is an aggregator or third party.
- If the page shows no active offer, return exactly {"no_offer": true}."""

USER_TEMPLATE = """Schema:
{schema}

Source URL: {url}
Today's date: {today}

Page text:
<<<
{text}
>>>

Return the offer JSON now."""


def load_hashes() -> dict:
    try:
        with open(HASHES_PATH) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def save_hashes(hashes: dict) -> None:
    os.makedirs(os.path.dirname(HASHES_PATH), exist_ok=True)
    with open(HASHES_PATH, "w") as fh:
        json.dump(hashes, fh, indent=2, sort_keys=True)


def offer_by_source(url: str) -> tuple[str | None, dict | None]:
    """Find an existing offer file whose source_url matches."""
    if not os.path.isdir(OFFERS_DIR):
        return None, None
    for name in sorted(os.listdir(OFFERS_DIR)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(OFFERS_DIR, name)
        try:
            with open(path) as fh:
                offer = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if (offer.get("provenance", {}) or {}).get("source_url") == url:
            return path, offer
    return None, None


def slugify(*parts: str) -> str:
    slug = "-".join(str(p) for p in parts if p)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug).strip("-").lower()
    return re.sub(r"-{2,}", "-", slug)


def strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n", "", raw)
        raw = re.sub(r"\n```$", "", raw.strip())
    start, end = raw.find("{"), raw.rfind("}")
    return raw[start:end + 1] if start != -1 and end > start else raw


def call_llm(text: str, url: str, schema: dict, today: date, feedback: str | None = None) -> dict:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("anthropic SDK required: pip install -r requirements.txt") from exc
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Export it in your shell or put it in a local .env "
            "(gitignored). Never commit it, and in CI use a repository secret."
        )

    client = anthropic.Anthropic()
    prompt = USER_TEMPLATE.format(
        schema=json.dumps(schema, indent=None),
        url=url,
        today=today.isoformat(),
        text=text[:MAX_PAGE_CHARS],
    )
    if feedback:
        prompt += (f"\n\nYour previous answer failed validation:\n{feedback}\n"
                   "Return corrected JSON only.")

    kwargs = {
        "model": MODEL,
        "max_tokens": 4000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        # Deterministic extraction where the SDK still exposes it (dropped in anthropic>=1.0).
        resp = client.messages.create(temperature=0, **kwargs)
    except TypeError:
        resp = client.messages.create(**kwargs)
    body = "".join(block.text for block in resp.content if block.type == "text")
    return json.loads(strip_fences(body))


def finalise(offer: dict, url: str, today: date, chash: str, existing: dict | None) -> dict:
    """Fill the fields the model must not invent: id, provenance, first_seen."""
    prov = offer.setdefault("provenance", {})
    prov["source_url"] = url
    prov.setdefault("aggregator_urls", [])
    prov["last_verified"] = today.isoformat()
    prov["verification_method"] = "llm_extracted"
    prov["verified_by"] = "bot"
    prov["content_hash"] = chash

    host = _registrable(_host(url))
    if prov.get("confidence") == "high" and host in AGGREGATOR_HOSTS:
        prov["confidence"] = "medium"
    prov.setdefault("confidence", "medium")

    dates = offer.setdefault("dates", {})
    dates.setdefault("deadline", None)
    dates.setdefault("enroll_by", None)
    if existing:
        dates["first_seen"] = (existing.get("dates", {}) or {}).get("first_seen", today.isoformat())
    else:
        dates.setdefault("first_seen", today.isoformat())

    offer.setdefault("data_points", (existing or {}).get("data_points", []))

    if not offer.get("id"):
        if existing and existing.get("id"):
            offer["id"] = existing["id"]
        else:
            bank = (offer.get("bank", {}) or {}).get("name", "offer")
            account = (offer.get("account", {}) or {}).get("name", "")
            amount = (offer.get("bonus", {}) or {}).get("amount", "")
            year = (dates.get("deadline") or today.isoformat())[:4]
            offer["id"] = slugify(bank, account, amount, year)
    return offer


def validation_errors(offer: dict, schema: dict) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        return []
    validator = jsonschema.Draft7Validator(schema)
    return [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
            for e in validator.iter_errors(offer)]


def semantic_diff(old: dict, new: dict) -> list[str]:
    """Which meaningful fields changed? provenance timestamps don't count."""
    def flatten(obj, prefix=""):
        flat = {}
        for key, value in (obj or {}).items():
            if prefix == "" and key == "provenance":
                continue
            path = f"{prefix}{key}"
            if isinstance(value, dict):
                flat.update(flatten(value, path + "."))
            else:
                flat[path] = json.dumps(value, sort_keys=True) if isinstance(value, list) else value
        return flat

    a, b = flatten(old), flatten(new)
    changes = []
    for key in sorted(set(a) | set(b)):
        if a.get(key) != b.get(key):
            changes.append(f"{key}: {a.get(key)!r} -> {b.get(key)!r}")
    return changes


def process_url(url: str, *, today: date, schema: dict, hashes: dict,
                dry_run: bool = False, force: bool = False) -> str:
    print(f"\n{url}")
    path, existing = offer_by_source(url)

    html = fetch(url)
    if html is None:
        return "fetch_failed"
    text = clean_text(html)
    if len(text) < MIN_TEXT_CHARS:
        print(f"  only {len(text)} chars of text — escalating to a rendered fetch")
        rendered = fetch_rendered(url)
        if rendered:
            text = clean_text(rendered)
    if len(text) < 200:
        print("  page has essentially no text — giving up")
        return "empty_page"

    chash = content_hash(text)
    if not force and hashes.get(url) == chash:
        print("  unchanged (hash match) — no LLM call, bumping last_verified")
        if path and existing and not dry_run:
            existing.setdefault("provenance", {})["last_verified"] = today.isoformat()
            write_offer(path, existing)
        return "unchanged"

    print(f"  content changed ({len(text)} chars) — calling {MODEL}")
    if dry_run:
        print("  --dry-run: skipping the LLM call")
        return "would_extract"

    schema_errs: list[str] = []
    offer = None
    for attempt in (1, 2):
        try:
            candidate = call_llm(text, url, schema, today,
                                 feedback="\n".join(schema_errs) if schema_errs else None)
        except json.JSONDecodeError as exc:
            schema_errs = [f"response was not valid JSON: {exc}"]
            continue
        if candidate.get("no_offer"):
            print("  model reports no active offer on this page")
            hashes[url] = chash
            return "no_offer"
        candidate = finalise(candidate, url, today, chash, existing)
        schema_errs = validation_errors(candidate, schema)
        if not schema_errs:
            offer = candidate
            break
        print(f"  attempt {attempt} failed validation: {schema_errs[0]}")

    if offer is None:
        print("  rejected after retry — leaving the existing file untouched")
        return "invalid"

    hashes[url] = chash
    target = path or os.path.join(OFFERS_DIR, offer["id"] + ".json")

    if existing:
        changes = semantic_diff(existing, offer)
        if not changes:
            print("  extracted, but nothing meaningful changed — bumping last_verified only")
            existing.setdefault("provenance", {})["last_verified"] = today.isoformat()
            existing["provenance"]["content_hash"] = chash
            write_offer(target, existing)
            return "unchanged"
        print(f"  {len(changes)} field(s) changed:")
        for change in changes[:12]:
            print(f"    - {change}")
        write_offer(target, offer)
        return "updated"

    print(f"  new offer -> {os.path.relpath(target, _ROOT)}")
    write_offer(target, offer)
    return "created"


def write_offer(path: str, offer: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(offer, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Extract offer JSON from bank promo pages.")
    parser.add_argument("--url", action="append", default=[], help="page to extract (repeatable)")
    parser.add_argument("--queue", default=None, help="discovered.json produced by discover.py")
    parser.add_argument("--refresh-all", action="store_true",
                        help="re-check the source_url of every existing offer")
    parser.add_argument("--limit", type=int, default=0, help="max URLs this run")
    parser.add_argument("--force", action="store_true", help="ignore the hash cache")
    parser.add_argument("--dry-run", action="store_true", help="fetch and hash, never call the LLM")
    parser.add_argument("--today", default=None)
    args = parser.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else date.today()
    urls: list[str] = list(args.url)

    if args.queue:
        with open(args.queue) as fh:
            queue = json.load(fh)
        urls += [item["url"] for item in queue.get("candidates", []) if item.get("url")]

    if args.refresh_all:
        for name in sorted(os.listdir(OFFERS_DIR)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(OFFERS_DIR, name)) as fh:
                src = (json.load(fh).get("provenance", {}) or {}).get("source_url")
            if src:
                urls.append(src)

    seen, ordered = set(), []
    for url in urls:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    if args.limit:
        ordered = ordered[:args.limit]
    if not ordered:
        parser.error("nothing to do: pass --url, --queue or --refresh-all")

    schema, hashes = load_schema(), load_hashes()
    tally: dict[str, int] = {}
    for url in ordered:
        try:
            outcome = process_url(url, today=today, schema=schema, hashes=hashes,
                                  dry_run=args.dry_run, force=args.force)
        except RuntimeError as exc:
            print(f"  {exc}")
            return 2
        except Exception as exc:  # one bad page must not kill the run
            print(f"  unexpected error: {type(exc).__name__}: {exc}")
            outcome = "error"
        tally[outcome] = tally.get(outcome, 0) + 1

    if not args.dry_run:
        save_hashes(hashes)
    print("\n" + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
