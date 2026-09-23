"""
Parse an Overwatch end-of-match scoreboard screenshot.

    python -m src.parse <image> [--debug]

Output follows the answer-key fixture schema (tests/fixtures/*.json), plus a
`margin` per recognised field and a `review` list of low-margin fields: the
margin between best and runner-up template is the confidence signal the rest
of the system routes on (CLAUDE.md).

Fields not implemented yet are returned as None.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from . import digits as D
from . import layout as L

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "reference" / "templates"
STATS = ["E", "A", "D", "DMG", "H", "MIT"]
REVIEW_MARGIN = {"stat": 0.5}   # below this, a field is listed for review


class Models:
    """Everything learned from the answer keys; built by tools/build_templates.py."""

    def __init__(self, digit_clf: D.DigitClassifier):
        self.digits = digit_clf

    @classmethod
    def load(cls, folder: Path = TEMPLATES) -> "Models":
        return cls(D.DigitClassifier.load(folder / "digits.npz"))


def load_rgb(path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"))


def parse(image, models: Models | None = None) -> dict:
    rgb = load_rgb(image) if not isinstance(image, np.ndarray) else image
    models = models or Models.load()
    lay = L.detect(rgb)
    rows, review = [], []
    for r in lay.rows:
        out = {"team": r.team, "player": None, "title": None, "hero": None, "role": None,
               "perks": [None, None], "margin": {}}
        for c in STATS:
            value, margin = models.digits.read(L.crop(rgb, r.rois[c]))
            out[c] = value
            out["margin"][c] = round(margin, 3)
            if value is None or margin < REVIEW_MARGIN["stat"]:
                review.append(f"{r.team}{r.index}.{c}")
        rows.append(out)
    return {
        "image": Path(image).name if not isinstance(image, np.ndarray) else None,
        "layout": {"x0": lay.x0, "y0": lay.y0, "scale": round(lay.scale, 3),
                   "rows": {t: sum(1 for r in lay.rows if r.team == t) for t in ("top", "bottom")}},
        "header": {"mode": None, "map": None, "time": None, "bans": [None] * 4, "rank_range": [None, None]},
        "rows": rows,
        "review": review,
        "problems": problems(rows),
    }


def problems(rows: list[dict]) -> list[str]:
    """Structural invariants (see CLAUDE.md "Always validate structurally")."""
    out = []
    if len(rows) not in (10, 12):
        out.append(f"expected 10 (5v5) or 12 (6v6) rows, got {len(rows)}")
    for r in rows:
        where = f"{r['team']} row {rows.index(r)}"
        if (r["D"] or 0) > 60 or (r["E"] or 0) > 120:
            out.append(f"{where}: implausible E/D ({r['E']}/{r['D']}), likely a digit merge")
        if any(r[c] is None for c in STATS):
            out.append(f"{where}: unreadable stat cell")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Parse a scoreboard screenshot.")
    ap.add_argument("image")
    ap.add_argument("--debug", action="store_true", help="also write debug/<image>_layout.png")
    args = ap.parse_args(argv)
    result = parse(args.image)
    if args.debug:
        (ROOT / "debug").mkdir(exist_ok=True)
        rgb = load_rgb(args.image)
        L.draw(rgb, L.detect(rgb)).save(ROOT / "debug" / (Path(args.image).name.replace(".", "_") + "_layout.png"))
    json.dump(result, sys.stdout, indent=1, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
