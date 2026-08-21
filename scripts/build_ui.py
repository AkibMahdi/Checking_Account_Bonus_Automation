#!/usr/bin/env python3
"""Build the standalone Bonus Ladder page from web/ladder/* + the current offers.

Produces two files from one source, so the interface can never drift from the data:

  web/bonus-ladder.html   a complete page — double-click it, no server, no Python
  dist/ladder.part.html   the same page as an Artifact fragment (no doctype/head/body),
                          which is what gets published to claude.ai

    python -m scripts.build_ui
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE in sys.path:
    sys.path.remove(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.userconfig import load_offers  # noqa: E402

TEMPLATE_DIR = os.path.join(_ROOT, "web", "ladder")
STANDALONE = os.path.join(_ROOT, "web", "bonus-ladder.html")
FRAGMENT = os.path.join(_ROOT, "dist", "ladder.part.html")

DOC_OPEN = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n')
DOC_MID = '\n</head>\n<body>\n'
DOC_CLOSE = '\n</body>\n</html>\n'


def read(name: str) -> str:
    with open(os.path.join(TEMPLATE_DIR, name)) as fh:
        return fh.read()


def build(offers: list[dict], built_on: date) -> tuple[str, str]:
    head, body = read("head.html"), read("body.html")
    script = (read("app.js")
              .replace("__OFFERS_JSON__", json.dumps(offers, separators=(",", ":")))
              .replace("__BUILT_ON__", built_on.isoformat()))
    fragment = f"{head}\n{body}\n{script}\n"
    standalone = f"{DOC_OPEN}{head}{DOC_MID}{body}\n{script}{DOC_CLOSE}"
    return standalone, fragment


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the Bonus Ladder page.")
    parser.add_argument("--today", default=None)
    args = parser.parse_args(argv)
    built_on = date.fromisoformat(args.today) if args.today else date.today()

    offers = sorted(load_offers(), key=lambda o: -(o.get("bonus", {}).get("amount") or 0))
    standalone, fragment = build(offers, built_on)

    os.makedirs(os.path.dirname(FRAGMENT), exist_ok=True)
    with open(STANDALONE, "w") as fh:
        fh.write(standalone)
    with open(FRAGMENT, "w") as fh:
        fh.write(fragment)

    print(f"web/bonus-ladder.html  {len(standalone) // 1024} KB  ({len(offers)} offers inlined)")
    print(f"dist/ladder.part.html  {len(fragment) // 1024} KB  (Artifact fragment)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
