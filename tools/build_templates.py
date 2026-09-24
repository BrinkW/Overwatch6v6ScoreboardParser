"""
Build reference/templates/ from the answer-key fixtures.

    python tools/build_templates.py [--exclude IMAGE ...] [--out DIR]

Every learned template comes from a screenshot whose answer is known:
  - digits:        stat cells whose glyph count matches the known value, one
                   template per glyph (plus a blurred copy);
  - roles:         role icons labelled with the row's role;
  - letters:       the header's "MODE|MAP" glyphs, when their count matches the
                   known text;
  - time_digits:   the header's match-time digits, when the count matches;
  - division:      the rank badge, labelled with its division;
  - rank_emblems:  in-game emblem silhouettes, labelled with their tier (they
                   complement the tier sheet, whose rendering differs slightly);
  - name_glyphs:   display-font player-name glyphs, when the count matches;
  - title_glyphs:  title glyphs (spaces excluded), when the count matches;
  - fallback_names: whole-name images of fallback-font players;
  - players, titles: the names and titles themselves (known_text.json), which
                   names are snapped to and titles are matched against.
Answer keys come from two places: tests/fixtures/ (committed, images in
sample_screenshots/) and data/reviewed/ (git-ignored captures accepted with
tools/review.py, each key next to its image).
--exclude supports leave-one-image-out evaluation (tools/evaluate.py).
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

from src import digits as D, header as HD, icons as I, layout as L, text as T  # noqa: E402
from src.parse import STATS, load_rgb  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
SAMPLES = ROOT / "sample_screenshots"
REVIEWED = ROOT / "data" / "reviewed"
KINDS = ["digits", "roles", "letters", "time_digits", "division", "rank_emblems",
         "name_glyphs", "title_glyphs", "fallback_names"]
STRING_KINDS = ["players", "titles"]


def fixtures(exclude=()):
    """Every answer key: the committed fixtures, then reviewed captures."""
    for folder, images in ((FIXTURES, SAMPLES), (REVIEWED, REVIEWED)):
        for f in sorted(folder.glob("*.json")) if folder.exists() else []:
            fx = json.loads(f.read_text(encoding="utf-8"))
            if fx["image"] not in exclude:
                fx["_image_path"] = str(images / fx["image"])
                yield fx


def image_path(fx: dict) -> Path:
    return Path(fx.get("_image_path") or SAMPLES / fx["image"])


def harvest(fx: dict) -> dict[str, list]:
    """Labelled samples of every kind from one answer-key screenshot."""
    rgb = load_rgb(image_path(fx))
    lay = L.detect(rgb)
    got = {k: [] for k in KINDS + STRING_KINDS}
    for row, truth in zip(lay.rows, fx["rows"]):
        for c in STATS:
            if truth.get(c) is not None:
                got["digits"] += D.harvest(L.crop(rgb, row.rois[c]), truth[c])
        if truth.get("role"):
            got["roles"].append((I.role_descriptor(L.crop(rgb, row.rois["role"])), truth["role"]))
        harvest_text(rgb, row, truth, lay.scale, got)

    h = fx.get("header", {})
    grey, orange = HD.split_strip(L.crop(rgb, lay.header["mode_map_time"]))
    if h.get("mode") and h.get("map"):
        text = "".join(ch for ch in f"{h['mode']}|{h['map']}".upper() if not ch.isspace())
        glyphs = HD.text_glyphs(grey)
        if len(glyphs) == len(text):
            got["letters"] += list(zip(glyphs, text))
    if h.get("time"):
        digits = h["time"].replace(":", "")
        glyphs = HD.time_glyphs(orange)
        if len(glyphs) == len(digits):
            got["time_digits"] += list(zip(glyphs, digits))
    for key, rank in zip(("rank_low", "rank_high"), h.get("rank_range") or []):
        if not rank:
            continue
        tier, division = rank.rsplit(" ", 1)
        c = L.crop(rgb, lay.header[key])
        badge = HD.badge_descriptor(c)
        if badge is not None:
            got["division"].append((badge, int(division)))
        _, prof = HD.emblem_features(c[:int(c.shape[0] * HD.EMBLEM_FRACTION)])
        if prof is not None:
            got["rank_emblems"].append((prof, tier))
    return got


def harvest_text(rgb, row, truth: dict, scale: float, got: dict):
    name, title = truth.get("player"), truth.get("title")
    block = T.text_block(rgb, row)
    glyphs, band = T.name_line(block, scale)
    if name:
        got["players"].append(name)
        if T.is_display_name(name):
            if len(glyphs) == len(name):
                got["name_glyphs"] += list(zip(T.name_vectors(glyphs), name))
        elif glyphs:
            got["fallback_names"].append((T.fallback_vector(block, glyphs, band, scale), name))
    if title:
        got["titles"].append(title)
        if band is not None:
            tg, base = T.title_line(block, band, scale)
            chars = title.replace(" ", "")
            if tg and len(tg) == len(chars):
                got["title_glyphs"] += list(zip(T.title_vectors(tg, base, scale), chars))


def build(exclude=(), cache: dict | None = None) -> dict:
    """{kind: (templates, labels)} for every kind. `cache` maps image -> harvested
    samples, so leave-one-image-out evaluation doesn't recompute them."""
    cache = cache if cache is not None else {}
    pooled = {k: [] for k in KINDS}
    for fx in fixtures(exclude):
        if fx["image"] not in cache:
            cache[fx["image"]] = harvest(fx)
        for k in KINDS + STRING_KINDS:
            pooled.setdefault(k, [])
            pooled[k] += cache[fx["image"]][k]
    out = {k: (np.stack([x for x, _ in v]), np.array([y for _, y in v])) for k, v in pooled.items()
           if v and k in KINDS}
    out.update({k: sorted(set(pooled.get(k, []))) for k in STRING_KINDS})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--out", type=Path, default=ROOT / "reference" / "templates")
    args = ap.parse_args(argv)
    t = build(args.exclude)
    args.out.mkdir(parents=True, exist_ok=True)
    u8 = lambda a: (a * 255).round().astype(np.uint8)
    np.savez_compressed(args.out / "digits.npz", templates=u8(t["digits"][0]), labels=t["digits"][1].astype(np.uint8))
    np.savez_compressed(args.out / "roles.npz", templates=u8(t["roles"][0]), labels=t["roles"][1])
    np.savez_compressed(args.out / "letters.npz", templates=u8(t["letters"][0]), labels=t["letters"][1])
    np.savez_compressed(args.out / "time_digits.npz", templates=u8(t["time_digits"][0]), labels=t["time_digits"][1])
    np.savez_compressed(args.out / "division.npz", templates=t["division"][0].astype(np.float32),
                        labels=t["division"][1].astype(np.uint8))
    np.savez_compressed(args.out / "rank_emblems.npz", profiles=t["rank_emblems"][0].astype(np.float32),
                        tiers=t["rank_emblems"][1])
    for k in ("name_glyphs", "title_glyphs", "fallback_names"):
        np.savez_compressed(args.out / f"{k}.npz", templates=u8(t[k][0]), labels=t[k][1])
    (args.out / "known_text.json").write_text(
        json.dumps({k: t[k] for k in STRING_KINDS}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for k in STRING_KINDS:
        print(f"{k:13} {len(t[k]):5} known")
    for k in KINDS:
        labels = t[k][1]
        print(f"{k:13} {len(labels):5} templates, classes: {sorted(set(labels.tolist()))}")


if __name__ == "__main__":
    main()
