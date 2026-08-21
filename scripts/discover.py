#!/usr/bin/env python3
"""Find which banks currently have an offer worth checking. Not a source of truth.

Scrapes aggregator index pages for bank name + rough amount + link, dedupes against
offers/*.json, and emits data/discovered.json as a work queue for extract.py.

Only *facts* leave this script: bank name, dollar figure, URL. Aggregator prose is
never copied into the repo.

Usage:
    python -m scripts.discover
    python -m scripts.discover --min-bonus 200 --out data/discovered.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from urllib.parse import urljoin, urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE in sys.path:
    sys.path.remove(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.fetching import USER_AGENT, clean_text, fetch  # noqa: E402,F401

OFFERS_DIR = os.path.join(_ROOT, "offers")
DEFAULT_OUT = os.path.join(_ROOT, "data", "discovered.json")

# Aggregator index pages. Each is a *pointer* source, never a data source.
INDEX_PAGES = [
    "https://www.doctorofcredit.com/best-bank-account-bonuses/",
    "https://www.doctorofcredit.com/category/bank-accounts/bank-bonuses/",
    "https://bankbonus.com/promotions/",
    "https://www.nerdwallet.com/best/banking/bank-account-promotions",
    "https://www.hustlermoneyblog.com/bank-bonus/",
]

AMOUNT_RE = re.compile(r"\$\s?([0-9]{2,5}(?:,[0-9]{3})*)")
BANK_HINT_RE = re.compile(
    r"\b(bank|credit union|federal|fcu|financial|chase|citi|sofi|ally|discover|axos|"
    r"truist|regions|huntington|keybank|bmo|pnc|wells fargo|capital one|us bank|u\.s\. bank|"
    r"td|m&t|fifth third|santander|schwab|chime|varo|current|upgrade|lendingclub|alliant|"
    r"first citizens|citizens|synovus|associated|webster|valley|zions|comerica)\b",
    re.I,
)
SKIP_URL_RE = re.compile(
    r"/(tag|category|author|page|feed|about|contact|privacy|terms|search)/|"
    r"\.(png|jpe?g|gif|svg|pdf|css|js)(\?|$)", re.I)


def existing_keys() -> set[tuple[str, str]]:
    keys = set()
    if not os.path.isdir(OFFERS_DIR):
        return keys
    for name in os.listdir(OFFERS_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(OFFERS_DIR, name)) as fh:
                offer = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        keys.add((norm(offer.get("bank", {}).get("name", "")),
                  norm(offer.get("account", {}).get("name", ""))))
    return keys


def existing_urls() -> set[str]:
    urls = set()
    if not os.path.isdir(OFFERS_DIR):
        return urls
    for name in os.listdir(OFFERS_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(OFFERS_DIR, name)) as fh:
                offer = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        prov = offer.get("provenance", {}) or {}
        if prov.get("source_url"):
            urls.add(prov["source_url"])
        urls.update(prov.get("aggregator_urls") or [])
    return urls


def norm(text: str) -> str:
    text = re.sub(r"\b(bank|n\.a\.|na|inc|corp|the)\b", " ", (text or "").lower())
    return re.sub(r"[^a-z0-9]+", "", text)


def guess_bank(text: str) -> str | None:
    match = BANK_HINT_RE.search(text or "")
    if not match:
        return None
    window = text[max(0, match.start() - 40):match.end() + 20]
    window = re.sub(r"[^A-Za-z0-9&.\s]", " ", window)
    words = [w for w in window.split() if w]
    if not words:
        return None
    # Keep the capitalised run around the hint word.
    hint = match.group(0).split()[0].lower()
    for i, word in enumerate(words):
        if word.lower().startswith(hint[:4]):
            start = max(0, i - 2)
            return " ".join(words[start:i + 2])[:60].strip()
    return " ".join(words[:4])[:60].strip()


def parse_index(html: str, base_url: str, min_bonus: float) -> list[dict]:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("  beautifulsoup4 not installed — skipping this index page")
        return []

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()

    found: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base_url, anchor["href"].split("#")[0])
        if not href.startswith("http") or SKIP_URL_RE.search(href):
            continue
        label = " ".join(anchor.get_text(" ").split())
        context = label
        parent = anchor.find_parent(["li", "tr", "p", "h1", "h2", "h3", "h4", "div"])
        if parent is not None:
            context = " ".join(parent.get_text(" ").split())[:400]
        amounts = [float(a.replace(",", "")) for a in AMOUNT_RE.findall(context)]
        amounts = [a for a in amounts if 50 <= a <= 10_000]
        if not amounts:
            continue
        amount = max(amounts)
        if amount < min_bonus:
            continue
        bank = guess_bank(context) or guess_bank(label)
        if not bank:
            continue
        entry = found.get(href)
        if entry is None or amount > entry["approx_bonus"]:
            found[href] = {
                "bank_guess": bank,
                "approx_bonus": amount,
                "url": href,
                "found_on": base_url,
                "link_text": label[:120],
            }
    return list(found.values())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build a work queue of candidate offer pages.")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--min-bonus", type=float, default=150.0)
    parser.add_argument("--index", action="append", default=[],
                        help="extra index page (repeatable)")
    parser.add_argument("--max-candidates", type=int, default=120)
    parser.add_argument("--include-known", action="store_true",
                        help="keep candidates we already have an offer file for")
    args = parser.parse_args(argv)

    pages = args.index or INDEX_PAGES
    known_urls = existing_urls()
    known_keys = existing_keys()

    candidates: dict[str, dict] = {}
    for page in pages:
        print(f"index: {page}")
        html = fetch(page)
        if not html:
            continue
        rows = parse_index(html, page, args.min_bonus)
        print(f"  {len(rows)} candidate link(s)")
        for row in rows:
            candidates.setdefault(row["url"], row)

    fresh, skipped = [], 0
    for row in candidates.values():
        key = (norm(row["bank_guess"]), "")
        already = row["url"] in known_urls or any(k[0] == key[0] for k in known_keys)
        if already and not args.include_known:
            skipped += 1
            continue
        row["already_tracked"] = already
        fresh.append(row)

    fresh.sort(key=lambda r: r["approx_bonus"], reverse=True)
    fresh = fresh[:args.max_candidates]

    payload = {
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "generated_on": date.today().isoformat(),
        "index_pages": pages,
        "min_bonus": args.min_bonus,
        "skipped_already_tracked": skipped,
        "candidates": fresh,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")

    print(f"\n{len(fresh)} candidate(s) queued, {skipped} already tracked "
          f"-> {os.path.relpath(args.out, _ROOT)}")
    for row in fresh[:15]:
        print(f"  ${row['approx_bonus']:>7,.0f}  {row['bank_guess'][:30]:<32} {row['url'][:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
