#!/usr/bin/env python3
"""Regenerate the Bonus Ladder's PWA icon set.

Simple, on-brand, and reproducible: a ladder glyph in --ink on --bg (the same
dark-first palette as web/ladder/head.html), drawn flat so it holds up at
favicon size and reads clearly as a home-screen icon on iOS/Android.

    python -m scripts.make_icons

Regenerates web/ladder/icons/icon-{180,192,512}.png. After running this,
re-embed the icons (and rebuild the manifest / apple-touch-icon data: URIs
in web/ladder/head.html) the same way they were built the first time — see
the "Icons and PWA install" section of docs/ for the embed step, or just ask
whatever's doing this work to redo it from these PNGs.
"""
from __future__ import annotations

import os
from PIL import Image, ImageDraw

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
ICONS_DIR = os.path.join(_ROOT, "web", "ladder", "icons")

BG = (14, 13, 11, 255)      # --bg
INK = (243, 240, 233, 255)  # --ink

SIZES = (180, 192, 512)


def make(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), BG)
    d = ImageDraw.Draw(img)
    margin = size * 0.26
    rail_w = max(2, round(size * 0.055))
    left_x, right_x = margin, size - margin
    top_y, bot_y = margin * 0.9, size - margin * 0.9

    d.rounded_rectangle([left_x - rail_w / 2, top_y, left_x + rail_w / 2, bot_y],
                         radius=rail_w / 2, fill=INK)
    d.rounded_rectangle([right_x - rail_w / 2, top_y, right_x + rail_w / 2, bot_y],
                         radius=rail_w / 2, fill=INK)

    rungs, rung_h = 4, max(2, round(size * 0.045))
    for i in range(rungs):
        t = top_y + (bot_y - top_y) * (i + 0.5) / rungs
        d.rounded_rectangle([left_x - rail_w / 2, t - rung_h / 2, right_x + rail_w / 2, t + rung_h / 2],
                             radius=rung_h / 2, fill=INK)
    return img


def main() -> int:
    os.makedirs(ICONS_DIR, exist_ok=True)
    for size in SIZES:
        path = os.path.join(ICONS_DIR, f"icon-{size}.png")
        make(size).save(path)
        print(f"wrote {os.path.relpath(path, _ROOT)} ({size}x{size})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
