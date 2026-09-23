"""
Score the parser against every answer-key fixture.

    python tools/evaluate.py            # leave-one-image-out (honest)
    python tools/evaluate.py --in-sample
    python tools/evaluate.py --overlays # also write debug/<image>_layout.png

Leave-one-image-out: templates used for an image never include glyphs from
that image. Fields that are null in a fixture are not scored.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from build_templates import SAMPLES, build, fixtures  # noqa: E402
from src import digits as D, layout as L  # noqa: E402
from src.parse import STATS, Models, load_rgb, parse  # noqa: E402

ROW_FIELDS = STATS  # extended as phases land


def models_for(images_excluded, cache):
    t = build(images_excluded, cache)
    return Models(D.DigitClassifier(*t["digits"]))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-sample", action="store_true", help="templates include the scored image")
    ap.add_argument("--overlays", action="store_true")
    args = ap.parse_args(argv)

    cache, per_field, mismatches = {}, defaultdict(lambda: [0, 0]), []
    full = None if not args.in_sample else models_for((), cache)
    for fx in fixtures():
        img = fx["image"]
        models = full or models_for((img,), cache)
        result = parse(SAMPLES / img, models)
        if args.overlays:
            (ROOT / "debug").mkdir(exist_ok=True)
            rgb = load_rgb(SAMPLES / img)
            L.draw(rgb, L.detect(rgb)).save(ROOT / "debug" / (img.replace(".", "_") + "_layout.png"))
        for i, (got, want) in enumerate(zip(result["rows"], fx["rows"])):
            for f in ROW_FIELDS:
                if want.get(f) is None:
                    continue
                per_field[f][1] += 1
                if got.get(f) == want[f]:
                    per_field[f][0] += 1
                else:
                    m = got.get("margin", {}).get(f)
                    mismatches.append(f"{img:14} {want['team']}{i % 6} {f:4} want {want[f]!r:>8} got {got.get(f)!r:>8}"
                                      + (f"  (margin {m})" if m is not None else ""))
        for p in result["problems"]:
            mismatches.append(f"{img:14} STRUCTURE {p}")

    mode = "in-sample" if args.in_sample else "leave-one-image-out"
    print(f"Field accuracy ({mode}, {len(list(fixtures()))} images)")
    tot_ok = tot_n = 0
    for f, (ok, n) in per_field.items():
        tot_ok, tot_n = tot_ok + ok, tot_n + n
        print(f"  {f:5} {ok:4}/{n:<4} {100 * ok / n:6.2f}%")
    print(f"  {'ALL':5} {tot_ok:4}/{tot_n:<4} {100 * tot_ok / max(tot_n, 1):6.2f}%")
    if mismatches:
        print(f"\nMismatches ({len(mismatches)}):")
        for m in mismatches:
            print("  " + m)
    return 0 if not mismatches else 1


if __name__ == "__main__":
    sys.exit(main())
