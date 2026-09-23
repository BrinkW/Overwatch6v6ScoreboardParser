"""
Build reference/templates/ from the answer-key fixtures.

    python tools/build_templates.py [--exclude IMAGE ...] [--out DIR]

Digits: every stat cell whose glyph count matches its known value contributes
one labelled template per glyph (plus a blurred copy). --exclude supports
leave-one-image-out evaluation (tools/evaluate.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from src import digits as D, icons as I, layout as L  # noqa: E402
from src.parse import STATS, load_rgb  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
SAMPLES = ROOT / "sample_screenshots"


def fixtures(exclude=()):
    for f in sorted(FIXTURES.glob("*.json")):
        fx = json.loads(f.read_text(encoding="utf-8"))
        if fx["image"] not in exclude:
            yield fx


def build(exclude=(), cache: dict | None = None) -> dict:
    """{"digits": (templates, labels), "roles": (templates, labels)}.
    `cache` maps image -> harvested samples to avoid recomputation."""
    cache = cache if cache is not None else {}
    glyphs, labels, roles, role_labels = [], [], [], []
    for fx in fixtures(exclude):
        if fx["image"] not in cache:
            rgb = load_rgb(SAMPLES / fx["image"])
            lay = L.detect(rgb)
            got, got_roles = [], []
            for row, truth in zip(lay.rows, fx["rows"]):
                for c in STATS:
                    if truth.get(c) is not None:
                        got += D.harvest(L.crop(rgb, row.rois[c]), truth[c])
                if truth.get("role"):
                    got_roles.append((I.role_descriptor(L.crop(rgb, row.rois["role"])), truth["role"]))
            cache[fx["image"]] = (got, got_roles)
        got, got_roles = cache[fx["image"]]
        for g, lab in got:
            glyphs.append(g)
            labels.append(lab)
        for g, lab in got_roles:
            roles.append(g)
            role_labels.append(lab)
    return {"digits": (np.stack(glyphs), np.array(labels)),
            "roles": (np.stack(roles), np.array(role_labels))}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--out", type=Path, default=ROOT / "reference" / "templates")
    args = ap.parse_args(argv)
    t = build(args.exclude)
    args.out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.out / "digits.npz", templates=(t["digits"][0] * 255).round().astype(np.uint8),
                        labels=t["digits"][1].astype(np.uint8))
    print(f"digits: {len(t['digits'][1])} templates, per class "
          f"{np.bincount(t['digits'][1], minlength=10).tolist()} -> {args.out / 'digits.npz'}")
    np.savez_compressed(args.out / "roles.npz", templates=(t["roles"][0] * 255).round().astype(np.uint8),
                        labels=t["roles"][1])
    print(f"roles: {len(t['roles'][1])} templates -> {args.out / 'roles.npz'}")


if __name__ == "__main__":
    main()
