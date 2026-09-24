"""
Generate assets/ranks/emerald1.png .. emerald5.png.

Emerald (added between Platinum and Diamond on 2026-08-11) had no published
division art, so these files are GENERATED, not official. Each is built on
diamond<d>.png, the closest layout (emblem on a glowing disc above the division
badge), in three steps:

  1. the Emerald emblem is cut from the tier sheet assets/rankiconsnew.png
     (alpha from colour distance to the sheet's flat background, then the
     background un-mixed from the edge pixels);
  2. Diamond's disc is rebuilt from its radial colour profile (Diamond's own
     emblem pixels excluded, so none of it survives), hue-rotated from
     Diamond's hue to Emerald's;
  3. the Emerald emblem is scaled to the Diamond emblem's height and placed at
     its centre, and Diamond's badge (wings, hexagon, division number) is laid
     back on top unchanged.

    python tools/make_emerald_ranks.py
"""

from __future__ import annotations

import colorsys
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from src.header import SHEET_BG, sheet_emblems  # noqa: E402

RANKS = ROOT / "assets" / "ranks"
BADGE_TOP = 0.66      # badge rows start below this fraction of the image height (diamond: ~row 104 of 156)


def extract(rgb: np.ndarray) -> Image.Image:
    """Emblem as RGBA: alpha from distance to the sheet background, colour un-mixed."""
    a = rgb.astype(np.float32)
    dist = np.abs(a - SHEET_BG).sum(2)
    # Metal only: the sheet's soft glow reaches the crop edge and would leave a
    # visible square; the rebuilt disc supplies the glow instead.
    alpha = np.clip((dist - 45) / 50, 0, 1)
    safe = np.maximum(alpha, 1e-3)[..., None]
    col = np.clip((a - (1 - alpha[..., None]) * SHEET_BG) / safe, 0, 255)
    out = np.dstack([col, alpha * 255]).astype(np.uint8)
    ys, xs = np.where(alpha > 0.05)
    return Image.fromarray(out).crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))


def mean_hue(rgb: np.ndarray) -> float:
    a = rgb.astype(np.float32)
    mx, mn = a.max(2), a.min(2)
    px = a[(mx > 150) & ((mx - mn) > 35)] / 255
    h = np.deg2rad([colorsys.rgb_to_hsv(*p)[0] * 360 for p in px])
    return float(np.rad2deg(np.arctan2(np.sin(h).mean(), np.cos(h).mean())) % 360)


LUMA = np.array([0.2126, 0.7152, 0.0722])


def hue_rotate(rgba: np.ndarray, degrees: float) -> np.ndarray:
    """Rotate hue while keeping each pixel's luminance: at equal HSV value, green
    reads far brighter than blue, so a plain rotation would wash the disc out."""
    out = rgba.copy()
    flat = out[..., :3].reshape(-1, 3) / 255.0
    rot = np.array([colorsys.hsv_to_rgb(((h + degrees / 360) % 1), s, v)
                    for h, s, v in (colorsys.rgb_to_hsv(*p) for p in flat)])
    before, after = flat @ LUMA, rot @ LUMA
    rot = np.clip(rot * (before / np.maximum(after, 1e-4))[:, None], 0, 1)
    out[..., :3] = (rot.reshape(out.shape[0], out.shape[1], 3) * 255).round().astype(np.uint8)
    return out


def components(mask: np.ndarray, min_size: int) -> np.ndarray:
    """Keep only 8-connected components of at least `min_size` pixels."""
    keep = np.zeros_like(mask)
    seen = np.zeros_like(mask)
    H, W = mask.shape
    for y0, x0 in zip(*np.where(mask)):
        if seen[y0, x0]:
            continue
        stack, comp = [(y0, x0)], []
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            comp.append((y, x))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < H and 0 <= nx < W and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        if len(comp) >= min_size:
            ys, xs = zip(*comp)
            keep[list(ys), list(xs)] = True
    return keep


def build(d: int, emblem: Image.Image, hue_shift: float) -> Image.Image:
    base = np.asarray(Image.open(RANKS / f"diamond{d}.png").convert("RGBA")).astype(np.float32)
    H, W = base.shape[:2]
    rgb, al = base[..., :3], base[..., 3]
    mx, mn = rgb.max(2), rgb.min(2)
    badge_top = int(H * BADGE_TOP)

    # disc geometry: the opaque, colourful region above the badge
    disc_rows = np.where(((al > 40) & ((mx - mn) > 40)).any(1))[0]
    disc_rows = disc_rows[disc_rows < badge_top + 12]
    top, bottom = disc_rows.min(), disc_rows.max()
    R = (bottom - top) / 2
    cy, cx = top + R, (W - 1) / 2
    yy, xx = np.mgrid[:H, :W]
    r = np.hypot(yy - cy, xx - cx)

    # radial profile of the disc, excluding Diamond's emblem (its bright metal)
    emblem_px = (mx > 140) & (yy < badge_top)
    ring = np.zeros((int(R) + 4, 4), np.float32)
    ok = np.zeros(len(ring), bool)
    for i in range(len(ring)):
        sel = (np.abs(r - i) < 0.75) & ~emblem_px & (yy < badge_top + 12)
        if (sel & (al > 40)).sum() >= 3:
            ring[i, :3] = rgb[sel & (al > 40)].mean(0)
            ring[i, 3] = al[sel].mean()
            ok[i] = True
    for i in range(len(ring)):          # fill radii fully covered by the old emblem
        if not ok[i]:
            j = np.where(ok)[0][np.argmin(np.abs(np.where(ok)[0] - i))]
            ring[i] = ring[j]
    ri = np.clip(r.round().astype(int), 0, len(ring) - 1)
    disc = ring[ri]
    disc[r > len(ring) - 1, 3] = 0
    disc = hue_rotate(disc.clip(0, 255).astype(np.uint8), hue_shift)
    out = Image.fromarray(disc)

    # Emerald emblem where Diamond's was, scaled to its height
    ey, ex = np.where(emblem_px & (al > 40))
    eh = ey.max() - ey.min() + 1
    scale = eh / emblem.height
    em = emblem.resize((max(1, round(emblem.width * scale)), eh), Image.LANCZOS)
    out.alpha_composite(em, (int(round((ex.min() + ex.max()) / 2 - em.width / 2)), int(ey.min())))

    # Diamond's badge back on top, unchanged (it is colourless). Only its large
    # pieces (wings, hexagon, digit): the tip of Diamond's emblem reaches into
    # these rows as a few stray pixels.
    badge = components((yy >= badge_top) & ((mx - mn) < 30) & (al > 0), min_size=25)
    arr = np.asarray(out).copy()
    arr[badge] = base[badge].astype(np.uint8)
    return Image.fromarray(arr)


def main():
    emblems = sheet_emblems()
    shift = mean_hue(emblems["Emerald"]) - mean_hue(emblems["Diamond"])
    emblem = extract(emblems["Emerald"])
    for d in range(1, 6):
        img = build(d, emblem, shift)
        img.save(RANKS / f"emerald{d}.png")
        print(f"assets/ranks/emerald{d}.png {img.size}")
    print(f"(disc hue shifted {shift:+.0f} deg from Diamond)")


if __name__ == "__main__":
    sys.exit(main())
