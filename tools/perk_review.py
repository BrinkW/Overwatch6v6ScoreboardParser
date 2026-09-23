"""
Contact sheets for checking perk identification by eye.

    python tools/perk_review.py        # writes debug/perk_review_<n>.png

Each tile: the in-game crop, the matched artwork (dark on white, like the game
draws it), the hero/perk name, the slot and the margin. Tiles are sorted by
margin, lowest (least certain) first.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from build_templates import SAMPLES, fixtures  # noqa: E402
from src import layout as L  # noqa: E402
from src.parse import Models, load_rgb, parse  # noqa: E402

TILE_W, TILE_H, COLS, PER_SHEET = 190, 104, 8, 64


def artwork(hero: str, name: str) -> Image.Image:
    perks = __import__("json").loads((ROOT / "reference" / "perks.json").read_text(encoding="utf-8"))["heroes"]
    icon = next(e["icon"] for e in perks[hero]["perks"] if e["name"] == name)
    a = np.asarray(Image.open(ROOT / "assets" / "perks" / icon).convert("RGBA"))[..., 3]
    return Image.fromarray(np.where(a > 128, 0, 255).astype(np.uint8)).convert("RGB").resize((72, 72))


def main():
    models = Models.load()
    tiles = []
    for fx in fixtures():
        rgb = load_rgb(SAMPLES / fx["image"])
        lay = L.detect(rgb)
        result = parse(rgb, models)
        for i, (row, got) in enumerate(zip(lay.rows, result["rows"])):
            for k, slot in enumerate(("perk_left", "perk_right")):
                d = got["perk_detail"][k]
                if d is None or d["name"] is None:
                    continue
                crop = Image.fromarray(L.crop(rgb, row.rois[slot])).resize((80, 72), Image.NEAREST)
                label = f"{fx['image'].split('.')[0]} {got['team'][0]}{row.index} {'LR'[k]}"
                tiles.append((d["margin"], crop, artwork(got["hero"], d["name"]),
                              f"{label}\n{got['hero']}/{d['name']}\nmargin {d['margin']}"))
    tiles.sort(key=lambda t: t[0])
    out = ROOT / "debug"
    out.mkdir(exist_ok=True)
    for s in range(0, len(tiles), PER_SHEET):
        chunk = tiles[s:s + PER_SHEET]
        rows = (len(chunk) + COLS - 1) // COLS
        sheet = Image.new("RGB", (COLS * TILE_W, rows * TILE_H), (60, 60, 60))
        d = ImageDraw.Draw(sheet)
        for j, (_, crop, art, text) in enumerate(chunk):
            x, y = (j % COLS) * TILE_W, (j // COLS) * TILE_H
            sheet.paste(crop, (x + 2, y + 2))
            sheet.paste(art, (x + 86, y + 2))
            d.text((x + 2, y + 76), text.split("\n")[1][:30], fill="white")
            d.text((x + 2, y + 88), text.split("\n")[0] + "  " + text.split("\n")[2].replace("margin ", "m="),
                   fill=(200, 200, 200))
        path = out / f"perk_review_{s // PER_SHEET + 1}.png"
        sheet.save(path)
        print(path, len(chunk), "tiles")


if __name__ == "__main__":
    main()
