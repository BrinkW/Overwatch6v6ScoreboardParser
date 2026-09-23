"""
Overwatch 2 scoreboard extraction — reference implementation of the pieces that
actually matter. Deliberately dependency-light: numpy + Pillow, plus optional
rapidfuzz / onnxruntime where noted.

The core insight: this is a FIXED-LAYOUT UI, not a natural scene. You are not
doing scene-text detection. You are doing (a) ROI extraction, (b) background
suppression, (c) closed-set classification. Treat it that way and accuracy goes
from ~85% to ~99.9%.
"""

from __future__ import annotations
import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# 1. TEXT ISOLATION  — the part you asked about
# ---------------------------------------------------------------------------
# Overwatch renders scoreboard text as near-white glyphs with a dark outline,
# over a strongly team-tinted background (blue ~200deg, red ~350deg) and, for
# players with a prestige nameplate, over an animated pink/red banner.
#
# Do NOT threshold on luminance: the banner has bright pink pixels that beat a
# luminance cutoff, and the blue background is darker than the red one so any
# global cutoff needs per-team tuning.
#
# DO threshold on CHROMA (saturation). The text is achromatic; every background
# element is not. Measured on a real 2560x1440 screenshot:
#
#     region                       bg median chroma   text median chroma
#     EVE       (flat blue)              103                  6
#     KIMIKO    (flat red)                87                  6
#     HAIPYDRAGON (banner + blue)         56                  6
#     PALEWHISPER (banner + red)          64                  6
#
# A ~10x separation with one constant. This is the single highest-leverage
# preprocessing step in the whole pipeline.

CHROMA_MAX = 45   # max (max(RGB) - min(RGB)) for a pixel to count as text
VALUE_MIN = 150   # min max(RGB); rejects the dark glyph outline and shadows


def text_mask(rgb: np.ndarray, chroma_max=CHROMA_MAX, value_min=VALUE_MIN) -> np.ndarray:
    """rgb: HxWx3 uint8 -> HxW bool, True where the pixel is white-ish text."""
    a = rgb.astype(np.int16)
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    return ((mx - mn) < chroma_max) & (mx > value_min)


def binarize_for_ocr(rgb: np.ndarray, upscale: int = 4) -> Image.Image:
    """Black text on white, upscaled. Recognizers want ~30-40px cap height;
    native scoreboard text is ~20px, so 3-4x LANCZOS before recognition."""
    m = text_mask(rgb)
    img = Image.fromarray(np.where(m, 0, 255).astype(np.uint8))
    if upscale > 1:
        img = img.resize((img.width * upscale, img.height * upscale), Image.LANCZOS)
    return img


# If you hit a UI element where chroma fails (rare — mostly the greyscale
# minimap or a white popup), fall back to Sauvola local thresholding rather
# than Otsu. Otsu is global and dies on the banner gradient.
def sauvola(gray: np.ndarray, window: int = 25, k: float = 0.2, r: float = 128.0):
    g = gray.astype(np.float64)
    pad = window // 2
    p = np.pad(g, pad, mode="reflect")
    c = np.cumsum(np.cumsum(p, 0), 1)
    c2 = np.cumsum(np.cumsum(p ** 2, 0), 1)

    def box(cs):
        return (cs[window:, window:] - cs[:-window, window:]
                - cs[window:, :-window] + cs[:-window, :-window])

    n = window * window
    mean = box(c) / n
    var = np.maximum(box(c2) / n - mean ** 2, 0)
    std = np.sqrt(var)
    thresh = mean * (1 + k * (std / r - 1))
    return (g > thresh[:g.shape[0], :g.shape[1]])


# ---------------------------------------------------------------------------
# 2. ROI LAYOUT — anchor, don't hardcode
# ---------------------------------------------------------------------------
# Hardcoded pixel coordinates break on ultrawide, on different UI scale, and on
# 5v5 vs 6v6 (row count and therefore row pitch change). Anchor on landmarks:
#
#   - the white column-header bar above the blue team (a long high-luminance,
#     low-chroma horizontal run) gives you the table's left/right edges and top
#   - the "VS" divider gives you the split between teams
#   - row pitch = (team block height) / row count; detect row count from the
#     number of distinct role-icon blobs in the left gutter
#
# Then express every ROI as a fraction of the table box. Below is the layout for
# a 2560x1440 capture, as fractions of the scoreboard table width/height, which
# you can scale to whatever you detect.

ROW_FRACTIONS = {
    #                    x0     x1     (fractions of table width)
    "role_icon":       (0.000, 0.035),
    "portrait":        (0.030, 0.100),
    "ult_charge":      (0.100, 0.135),   # friendly team only
    "nameplate":       (0.140, 0.345),   # prestige badge + name
    "name":            (0.170, 0.345),
    "title":           (0.140, 0.345),   # subtitle line, lower third of the row
    "perk_major":      (0.348, 0.400),   # LEFT icon
    "perk_minor":      (0.412, 0.464),   # RIGHT icon
    "E":               (0.470, 0.520),
    "A":               (0.525, 0.575),
    "D":               (0.580, 0.630),
    "DMG":             (0.635, 0.730),
    "H":               (0.735, 0.820),
    "MIT":             (0.825, 0.915),
}


# ---------------------------------------------------------------------------
# 3. NUMBERS — do not use OCR
# ---------------------------------------------------------------------------
# The stat columns are digits only, in a single tabular font, right-aligned,
# with comma thousands separators. Connected-component segmentation after the
# chroma mask gives you clean isolated glyphs. A 12-class classifier (0-9, comma,
# blank) on 16x16 binary crops hits ~99.9% with a 3-layer CNN, or even with plain
# nearest-neighbour on ~50 templates per class. It runs in microseconds and never
# hallucinates a 5 as an S.

def segment_glyphs(mask: np.ndarray, min_px: int = 8):
    """Column-projection segmentation. Returns list of (x0, x1) glyph spans.
    Simpler and more robust than connected components for a single text line,
    because the font has no touching glyphs at this size."""
    cols = mask.any(axis=0)
    spans, start = [], None
    for i, on in enumerate(cols):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if mask[:, start:i].sum() >= min_px:
                spans.append((start, i))
            start = None
    if start is not None:
        spans.append((start, len(cols)))
    return spans


def normalize_glyph(mask: np.ndarray, span, size: int = 16) -> np.ndarray:
    """Crop to the glyph's tight bbox, pad to square, resize. Deskewing helps:
    the scoreboard font is italic ~12 degrees. Shear-correct before resizing and
    your template distances tighten considerably."""
    x0, x1 = span
    sub = mask[:, x0:x1]
    rows = np.where(sub.any(axis=1))[0]
    if len(rows) == 0:
        return np.zeros((size, size), bool)
    sub = sub[rows[0]:rows[-1] + 1]
    h, w = sub.shape
    s = max(h, w)
    canvas = np.zeros((s, s), bool)
    canvas[(s - h) // 2:(s - h) // 2 + h, (s - w) // 2:(s - w) // 2 + w] = sub
    return np.array(Image.fromarray(canvas.astype(np.uint8) * 255)
                    .resize((size, size), Image.LANCZOS)) > 127


# ---------------------------------------------------------------------------
# 4. ICONS — template matching, never a VLM
# ---------------------------------------------------------------------------
# This is where I was guessing and you should not be. Perk icons are pure black
# glyphs on a white disc, rendered at a fixed size with no skin variation. That
# makes them a closed set of a few hundred templates and a trivially solvable
# nearest-neighbour problem — exact, not probabilistic.
#
# Build the reference library once (extract from game files, or scrape the
# community asset mirrors), then:

def icon_descriptor(rgb: np.ndarray, size: int = 32) -> np.ndarray:
    """Perk icons: binarize the dark glyph inside the white disc, resize, flatten.
    Mask out the disc border so the surrounding team color never leaks in."""
    img = Image.fromarray(rgb).resize((size, size), Image.LANCZOS)
    a = np.asarray(img).astype(np.int16)
    glyph = a.max(axis=2) < 128
    yy, xx = np.mgrid[0:size, 0:size]
    inside = ((yy - size / 2) ** 2 + (xx - size / 2) ** 2) < (size * 0.42) ** 2
    return (glyph & inside).astype(np.float32).ravel()


def match_icon(desc: np.ndarray, library: dict[str, np.ndarray]):
    """library: name -> descriptor. Returns (best_name, score, margin).
    The MARGIN between best and runner-up is your confidence signal — use it to
    route ambiguous crops to a human or to a VLM fallback."""
    names = list(library)
    M = np.stack([library[n] for n in names])
    d = np.linalg.norm(M - desc, axis=1)
    order = np.argsort(d)
    return names[order[0]], float(d[order[0]]), float(d[order[1]] - d[order[0]])


# HERO PORTRAITS are the hard case, because skins change them completely.
# Three ways out, in order of preference:
#
#   (a) Enumerate skins. Blizzard ships a portrait per (hero, skin); it is a few
#       thousand images total. Nearest-neighbour over all of them, then map back
#       to hero. This is exact and is what I should have done.
#
#   (b) Use the PERKS to identify the hero. Every perk belongs to exactly one
#       hero, so a single recognized perk icon determines the hero — and perk
#       icons are skin-invariant. This is a free, highly reliable cross-check.
#       (In my analysis this independently confirmed 10 of 11 heroes.)
#
#   (c) Role icon (tank/damage/support) narrows the candidate set ~3x before
#       either of the above.
#
# Combine all three and vote. Disagreement is a useful flag, not a problem.


# ---------------------------------------------------------------------------
# 5. CLOSED-SET SNAPPING — the cheapest accuracy you will ever buy
# ---------------------------------------------------------------------------
# Titles, hero names, map names and game modes are ALL drawn from known finite
# lists. Never accept raw recognizer output for them. Recognize, then snap:
#
#     from rapidfuzz import process, fuzz
#     best, score, _ = process.extractOne(raw, KNOWN_TITLES, scorer=fuzz.WRatio)
#     value = best if score > 80 else None
#
# "Unrelentiing Hero" -> "Unrelenting Hero". "Grandmaster 0pen Challenger" ->
# "Grandmaster Open Challenger". This converts a hard recognition problem into
# an easy retrieval problem.
#
# PLAYER NAMES cannot be snapped (they are free text), but they are heavily
# constrained: BattleTag rules mean 3-12 characters, must start with a letter,
# letters and digits only, no spaces, displayed uppercase. Restrict the decoder
# charset accordingly and drop the discriminator (#1234) — it is not rendered
# on the scoreboard.


# ---------------------------------------------------------------------------
# 6. VALIDATION — catch your own errors
# ---------------------------------------------------------------------------
def validate(rows: list[dict]) -> list[str]:
    """Cheap structural invariants that catch most extraction failures."""
    problems = []
    if len(rows) not in (10, 12):
        problems.append(f"expected 10 (5v5) or 12 (6v6) rows, got {len(rows)}")
    for team in ("blue", "red"):
        t = [r for r in rows if r["team"] == team]
        n = len(t)
        roles = [r["role"] for r in t]
        expect = {"tank": n // 3, "damage": n // 3, "support": n // 3}
        for role, k in expect.items():
            if roles.count(role) != k:
                problems.append(f"{team}: {roles.count(role)} {role}, expected {k}")
        for r in t:
            if r["role"] == "support" and r["H"] == 0 and r["E"] > 5:
                problems.append(f"{r['name']}: support with 0 healing — check H column")
            if r["D"] > 60 or r["E"] > 120:
                problems.append(f"{r['name']}: implausible E/D, likely a digit merge")
    return problems


# ---------------------------------------------------------------------------
# DEMO
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "scoreboard.png"
    rgb = np.asarray(Image.open(src).convert("RGB"))
    # EVE's name cell on a 2560x1440 capture
    roi = rgb[340:385, 560:780]
    binarize_for_ocr(roi).save("name_binarized.png")
    m = text_mask(roi)
    print("text pixels:", m.sum(), "spans:", len(segment_glyphs(m)))
