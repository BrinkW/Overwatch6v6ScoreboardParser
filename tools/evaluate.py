"""
Score the parser against every answer-key fixture.

    python tools/evaluate.py              # leave-one-image-out (honest)
    python tools/evaluate.py --in-sample
    python tools/evaluate.py --overlays   # also write debug/<image>_layout.png
    python tools/evaluate.py --tier-gaps  # rows whose perks contradict perks.json's tier history

Leave-one-image-out: learned templates (stat digits, roles, header letters, time
digits, division badges, rank emblems, name and title glyphs, fallback-font
names) and the known players and titles used for an image never include
samples from that image, so a recurring player must be read, not remembered.
Portrait, perk and ban libraries come from the reference assets, not the
fixtures. Fields that are null in a fixture are not scored, except `title`,
where null means the row has no title.
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
from src import digits as D, header as HD, icons as I, layout as L, text as T  # noqa: E402
from src.parse import STATS, Models, load_rgb, parse  # noqa: E402

ROW_FIELDS = STATS + ["role", "hero", "player", "title"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-sample", action="store_true", help="templates include the scored image")
    ap.add_argument("--overlays", action="store_true")
    ap.add_argument("--tier-gaps", action="store_true",
                    help="list rows whose perks can't be one major + one minor under perks.json's tier history")
    args = ap.parse_args(argv)

    portraits, perk_lib, bans = I.PortraitLibrary(), I.PerkLibrary(), HD.BanReader()
    ref_titles = T.reference_titles()

    def models_for(excluded, cache):
        t = build(excluded, cache)
        header = HD.HeaderModels(HD.GlyphReader(*t["letters"]), HD.GlyphReader(*t["time_digits"]),
                                 HD.DivisionReader(*t["division"]),
                                 HD.TierReader(list(zip(t["rank_emblems"][1], t["rank_emblems"][0]))), bans)
        text = T.TextModels(T.GlyphSet(*t["name_glyphs"]), T.GlyphSet(*t["title_glyphs"]), t["players"],
                            ref_titles + t["titles"], T.NameImages(*t["fallback_names"]) if "fallback_names" in t else None)
        return Models(D.DigitClassifier(*t["digits"]), I.RoleClassifier(*t["roles"]), portraits, perk_lib, header, text)

    cache, per_field, mismatches, flags = {}, defaultdict(lambda: [0, 0]), [], []
    gaps = Counter()
    snapped = []
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
                if f not in want or (want[f] is None and f != "title"):   # a null title means "no title"
                    continue
                per_field[f][1] += 1
                if got.get(f) == want[f]:
                    per_field[f][0] += 1
                else:
                    m = got.get("margin", {}).get({"hero": "portrait", "player": "name"}.get(f, f))
                    mismatches.append(f"{where} {f:5} want {want[f]!r:>14} got {got.get(f)!r:>14}"
                                      + (f"  (margin {m})" if m is not None else ""))
            for k, side in enumerate(("L", "R")):
                w = want["perks"][k]
                if w is None:
                    continue
                key = "perk (empty)" if w == "none" else "perk (name)"
                per_field[key][1] += 1
                # "A|B": either name is correct (two perks drawn with the identical icon
                # whose tiers don't settle which one it is)
                if got["perks"][k] in w.split("|"):
                    per_field[key][0] += 1
                else:
                    mismatches.append(f"{where} perk{side} want {w!r:>14} got {got['perks'][k]!r:>14}")
            if got.get("text_evidence", {}).get("name", {}).get("snapped"):
                raw = got["text_evidence"]["name"]["raw"]
                snapped.append(f"{where} read {raw!r} -> known player {got['player']!r}"
                               + ("" if got["player"] == want.get("player") else f" (WRONG, want {want.get('player')!r})"))
            for fl in got.get("hero_flags", []):
                flags.append(f"{where} {got['hero']:13} {fl}")
            for note in got.get("reference_notes", []):
                gaps[note] += 1
        hw, hg = fx.get("header", {}), result["header"]
        for f in ("mode", "map", "time"):
            if hw.get(f) is None:
                continue
            per_field[f][1] += 1
            if hg.get(f) == hw[f]:
                per_field[f][0] += 1
            else:
                mismatches.append(f"{img:12} header {f:5} want {hw[f]!r} got {hg.get(f)!r}  (raw {hg.get('raw')})")
        for f, n in (("bans", 4), ("rank_range", 2)):
            for k in range(n):
                w = (hw.get(f) or [None] * n)[k]
                if w is None:
                    continue
                key = "ban" if f == "bans" else "rank"
                per_field[key][1] += 1
                if hg[f][k] == w:
                    per_field[key][0] += 1
                else:
                    mismatches.append(f"{img:12} header {key}{k} want {w!r} got {hg[f][k]!r}")
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
    if snapped:
        print(f"\nNames snapped to a known player ({len(snapped)}):")
        for s in snapped:
            print("  " + s)
    if flags:
        print(f"\nHero flags ({len(flags)}): signals that disagreed")
        for f in flags:
            print("  " + f)
    if args.tier_gaps and gaps:
        print(f"\nTier data gaps ({sum(gaps.values())}): these rows break the tier rules (two perks = one major +")
        print("one minor; a lone perk = minor) under perks.json's tier history, so the history is incomplete:")
        for note, n in sorted(gaps.items()):
            print(f"  {n:2}x  {note}")
    return 0 if not mismatches else 1


if __name__ == "__main__":
    sys.exit(main())
