#!/usr/bin/env python3
"""Move past-deadline offers to offers/archive/. Keeps the live feed honest.

History is preserved so "has this offer run before?" stays answerable.

    python -m scripts.expire_sweep --dry-run
    python -m scripts.expire_sweep --grace-days 3
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import date, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE in sys.path:
    sys.path.remove(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

OFFERS_DIR = os.path.join(_ROOT, "offers")
ARCHIVE_DIR = os.path.join(OFFERS_DIR, "archive")
STALE_WARN_DAYS = 45


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Archive expired offers.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--grace-days", type=int, default=1,
                        help="days past the deadline before archiving (default 1)")
    parser.add_argument("--today", default=None)
    args = parser.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else date.today()
    cutoff = today - timedelta(days=args.grace_days)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    archived, stale, expiring = [], [], []
    for name in sorted(os.listdir(OFFERS_DIR)):
        path = os.path.join(OFFERS_DIR, name)
        if not name.endswith(".json") or os.path.isdir(path):
            continue
        with open(path) as fh:
            offer = json.load(fh)

        deadline = (offer.get("dates") or {}).get("deadline")
        verified = (offer.get("provenance") or {}).get("last_verified")

        if verified:
            try:
                age = (today - date.fromisoformat(verified)).days
                if age > STALE_WARN_DAYS:
                    stale.append((offer["id"], age))
            except ValueError:
                pass

        if not deadline:
            continue
        try:
            parsed = date.fromisoformat(deadline)
        except ValueError:
            print(f"  {offer['id']}: unparseable deadline {deadline!r} — leaving in place")
            continue

        if parsed < cutoff:
            archived.append((offer["id"], deadline))
            if not args.dry_run:
                offer.setdefault("provenance", {})["archived_on"] = today.isoformat()
                target = os.path.join(ARCHIVE_DIR, name)
                shutil.move(path, target)
                # Rewrite with the archive stamp (schema allows extra keys only here,
                # so keep the stamp in a sidecar-free way: it lives in the archived copy).
                with open(target, "w") as fh:
                    json.dump(offer, fh, indent=2, ensure_ascii=False)
                    fh.write("\n")
        elif 0 <= (parsed - today).days <= 14:
            expiring.append((offer["id"], deadline))

    verb = "would archive" if args.dry_run else "archived"
    print(f"{verb} {len(archived)} expired offer(s)")
    for oid, deadline in archived:
        print(f"  {oid} (deadline {deadline})")
    if expiring:
        print(f"\n{len(expiring)} offer(s) expire within 14 days:")
        for oid, deadline in expiring:
            print(f"  {oid} (deadline {deadline})")
    if stale:
        print(f"\n{len(stale)} offer(s) not verified in over {STALE_WARN_DAYS} days:")
        for oid, age in sorted(stale, key=lambda pair: -pair[1]):
            print(f"  {oid} ({age} days)")

    # Surface counts for the workflow summary.
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write(f"archived={len(archived)}\n")
            fh.write(f"expiring={len(expiring)}\n")
            fh.write(f"stale={len(stale)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
