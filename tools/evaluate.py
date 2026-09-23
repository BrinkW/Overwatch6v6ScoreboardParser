"""
Score the parser against every answer-key fixture.

    python tools/evaluate.py              # leave-one-image-out (honest)
    python tools/evaluate.py --in-sample
    python tools/evaluate.py --overlays   # also write debug/<image>_layout.png
    python tools/evaluate.py --perk-slots # tally perk tier by scoreboard slot

Leave-one-image-out: learned templates (digits, roles) used for an image never
include samples from that image. Portrait and perk libraries come from the
reference assets, not the fixtures. Fields that are null in a fixture are not
scored.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from build_templates import SAMPLES, build, fixtures  # noqa: E402
from src import digits as D, icons as I, layout as L  # noqa: E402
from src.parse import STATS, Models, load_rgb, parse  # noqa: E402

ROW_FIELDS = STATS + ["role", "hero"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-sample", action="store_true", help="templates include the scored image")
    ap.add_argument("--overlays", action="store_true")
    ap.add_argument("--perk-slots", action="store_true", help="tally perk tiers by slot (left/right)")
    args = ap.parse_args(argv)

    portraits, perk_lib = I.PortraitLibrary(), I.PerkLibrary()

    def models_for(excluded, cache):
        t = build(excluded, cache)
        return Models(D.DigitClassifier(*t["digits"]), I.RoleClassifier(*t["roles"]), portraits, perk_lib)

    cache, per_field, mismatches, flags = {}, defaultdict(lambda: [0, 0]), [], []
    slot_tally, slot_rows = Counter(), []
    full = models_for((), cache) if args.in_sample else None
    for fx in fixtures():
        img = fx["image"]
        result = parse(SAMPLES / img, full or models_for((img,), cache))
        if args.overlays:
            (ROOT / "debug").mkdir(exist_ok=True)
            rgb = load_rgb(SAMPLES / img)
            L.draw(rgb, L.detect(rgb)).save(ROOT / "debug" / (img.replace(".", "_") + "_layout.png"))
        for i, (got, want) in enumerate(zip(result["rows"], fx["rows"])):
            where = f"{img:12} {want['team']}{i % 6}"
            for f in ROW_FIELDS:
                if want.get(f) is None:
                    continue
                per_field[f][1] += 1
                if got.get(f) == want[f]:
                    per_field[f][0] += 1
                else:
                    m = got.get("margin", {}).get("portrait" if f == "hero" else f)
                    mismatches.append(f"{where} {f:5} want {want[f]!r:>14} got {got.get(f)!r:>14}"
                                      + (f"  (margin {m})" if m is not None else ""))
            for k, side in enumerate(("L", "R")):
                w = want["perks"][k]
                if w is None:
                    continue
                key = "perk (empty)" if w == "none" else "perk (name)"
                per_field[key][1] += 1
                if got["perks"][k] == w:
                    per_field[key][0] += 1
                else:
                    mismatches.append(f"{where} perk{side} want {w!r:>14} got {got['perks'][k]!r:>14}")
            for fl in got.get("hero_flags", []):
                flags.append(f"{where} {got['hero']:13} {fl}")
            if args.perk_slots:
                for k, side in enumerate(("left", "right")):
                    d = got["perk_detail"][k]
                    if d and d["name"] and not d["tier_swapped"]:
                        slot_tally[(side, d["tier"])] += 1
                    elif d and d["name"]:
                        slot_tally[(side, "swapped (not counted)")] += 1
                slot_rows.append(got)
        for p in result["problems"]:
            mismatches.append(f"{img:12} STRUCTURE {p}")

    mode = "in-sample" if args.in_sample else "leave-one-image-out"
    print(f"Field accuracy ({mode}, {len(list(fixtures()))} images)")
    tot_ok = tot_n = 0
    for f, (ok, n) in per_field.items():
        tot_ok, tot_n = tot_ok + ok, tot_n + n
        print(f"  {f:13} {ok:4}/{n:<4} {100 * ok / n:6.2f}%")
    print(f"  {'ALL':13} {tot_ok:4}/{tot_n:<4} {100 * tot_ok / max(tot_n, 1):6.2f}%")
    if mismatches:
        print(f"\nMismatches ({len(mismatches)}):")
        for m in mismatches:
            print("  " + m)
    if flags:
        print(f"\nHero flags ({len(flags)}): signals that disagreed")
        for f in flags:
            print("  " + f)
    if args.perk_slots:
        print("\nPerk tier by slot (perks whose tier never changed):")
        for side in ("left", "right"):
            print(f"  {side:5}  major {slot_tally[(side, 'major')]:3}   minor {slot_tally[(side, 'minor')]:3}"
                  f"   tier-swapped, not counted {slot_tally[(side, 'swapped (not counted)')]:3}")
        pure = [r["perk_detail"] for r in slot_rows
                if all(d and d["name"] and not d["tier_swapped"] for d in r["perk_detail"])]
        both = len(pure)
        fits = sum(1 for L_, R_ in pure if L_["tier"] == "major" and R_["tier"] == "minor")
        print(f"  rows with two non-swapped perks: {both}; of those, left=major & right=minor: {fits}")
    return 0 if not mismatches else 1


if __name__ == "__main__":
    sys.exit(main())
