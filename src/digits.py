"""
Stat-column digits (E / A / D / DMG / H / MIT): a nearest-neighbour classifier,
not OCR. The digits come from one font at one size, so a few labelled examples
per class are enough, and the classifier cannot hallucinate a letter.

Templates are harvested from the answer-key fixtures: when a cell's segmented
glyph count equals the number of digits in its known value, each glyph gets
the matching digit as its label (see `harvest`).
"""

from __future__ import annotations

import numpy as np
from PIL import Image

GLYPH_H, GLYPH_W = 20, 14   # normalised glyph box (keeps aspect: "1" stays narrow)


def _runs(mask) -> list[tuple[int, int]]:
    m = np.concatenate([[False], np.asarray(mask, bool), [False]])
    d = np.diff(m.astype(np.int8))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0] - 1))


def whiteness(rgb: np.ndarray) -> np.ndarray:
    """Soft text mask in [0, 1] from the MINIMUM RGB channel.

    White text is high in all channels; every team colour (blue, red, cyan,
    yellow, orange) is saturated, so it is low in at least one. Unlike a hard
    chroma cut, min(RGB) falls off smoothly at blurred glyph edges, where text
    blends with the tinted background (the highlighted row of a rescaled
    capture broke glyphs apart under the chroma test)."""
    mn = rgb.astype(np.float32).min(2)
    return np.clip((mn - 50) / 130, 0, 1)


def glyph_boxes(w: np.ndarray, thresh: float = 0.45) -> list[tuple[int, int, int, int]]:
    """(x0, x1, y0, y1) of each glyph, left to right, commas removed.
    Column projection: this font has no touching glyphs at native size; merged
    glyphs from blurry captures are split by width afterwards."""
    if w.max() < 0.15:
        return []
    # Zeros in H/MIT are drawn dim grey (dimmer still when blurred): threshold
    # relative to the cell's own peak, not an absolute brightness.
    m = w > thresh * min(1.0, max(float(w.max()), 0.35))
    # Keep only the main text line; stray marks (cursor, ghost text) above or
    # below would otherwise join a digit's columns and stretch its box.
    band = [(s, e) for s, e in _runs(m.any(1))]
    if not band:
        return []
    s, e = max(band, key=lambda r: m[r[0]:r[1] + 1].sum())
    m = m.copy()
    m[:s] = False
    m[e + 1:] = False
    cols = m.any(0)
    spans, s = [], None
    for i, on in enumerate(np.append(cols, False)):
        if on and s is None:
            s = i
        elif not on and s is not None:
            if m[:, s:i].sum() >= 4:
                spans.append((s, i))
            s = None
    boxes = []
    for x0, x1 in spans:
        rows = np.where(m[:, x0:x1].any(1))[0]
        boxes.append((x0, x1, int(rows[0]), int(rows[-1]) + 1))
    if not boxes:
        return []
    full_h = max(b[3] - b[2] for b in boxes)
    boxes = [b for b in boxes if (b[3] - b[2]) > 0.6 * full_h]  # drops commas and specks
    boxes = [_trim_comma(m, b) for b in boxes]
    # Split glyphs merged by blur. Typical digit width comes from the height,
    # not the median width: narrow "1"s would drag a median down and split "0"s.
    typical = 0.72 * full_h
    col_mass = w.sum(0)
    out = []
    for x0, x1, y0, y1 in boxes:
        # the widest single digit ("4" under the min-channel mask) is ~1.05 h;
        # two blurred-together "1"s are ~1.15 h
        n = max(2, int(round((x1 - x0) / typical))) if (x1 - x0) > 1.55 * typical else 1
        # cut at the faintest column near each ideal cut, not at equal widths:
        # a narrow "1" merged with a wide digit would otherwise lose half the digit
        cuts = [x0]
        for k in range(1, n):
            ideal = x0 + (x1 - x0) * k / n
            lo, hi = int(ideal - 0.3 * typical), int(ideal + 0.3 * typical) + 1
            lo, hi = max(lo, cuts[-1] + 2), min(hi, x1 - 2)
            cuts.append(lo + int(np.argmin(col_mass[lo:hi])) if hi > lo else int(ideal))
        cuts.append(x1)
        out += [(cuts[k], cuts[k + 1], y0, y1) for k in range(n)]
    return out


def _trim_comma(m: np.ndarray, box):
    """Blur can fuse a thousands comma onto the neighbouring digit ("9," reads as
    "4"). Trim edge columns whose ink sits only in the bottom of the line."""
    x0, x1, y0, y1 = box
    low = y0 + 0.62 * (y1 - y0)

    def comma_col(x):
        ys = np.where(m[y0:y1, x])[0] + y0
        return len(ys) > 0 and ys.min() >= low

    while x1 - x0 > 3 and comma_col(x1 - 1):
        x1 -= 1
    while x1 - x0 > 3 and comma_col(x0):
        x0 += 1
    rows = np.where(m[y0:y1, x0:x1].any(1))[0]
    return (x0, x1, y0 + int(rows[0]), y0 + int(rows[-1]) + 1) if len(rows) else box


def normalise(w: np.ndarray, box) -> np.ndarray:
    """Crop one glyph, scale to GLYPH_H keeping aspect, centre in a GLYPH_W box."""
    x0, x1, y0, y1 = box
    g = w[y0:y1, x0:x1]
    h, wd = g.shape
    new_w = max(1, min(GLYPH_W, int(round(wd * GLYPH_H / h))))
    im = Image.fromarray((g * 255).astype(np.uint8)).resize((new_w, GLYPH_H), Image.BILINEAR)
    out = np.zeros((GLYPH_H, GLYPH_W), np.float32)
    off = (GLYPH_W - new_w) // 2
    out[:, off:off + new_w] = np.asarray(im, np.float32) / 255
    return out


def glyphs(cell_rgb: np.ndarray) -> list[np.ndarray]:
    w = whiteness(cell_rgb)
    return [normalise(w, b) for b in glyph_boxes(w)]


class DigitClassifier:
    def __init__(self, templates: np.ndarray, labels: np.ndarray):
        self.T = templates.reshape(len(templates), -1)
        self.y = np.asarray(labels)

    @classmethod
    def load(cls, path) -> "DigitClassifier":
        d = np.load(path)
        return cls(d["templates"].astype(np.float32) / 255, d["labels"].astype(int))

    def classify(self, g: np.ndarray) -> tuple[int, float]:
        """(digit, margin): margin = distance to the nearest OTHER digit minus distance to the best."""
        d = np.linalg.norm(self.T - g.ravel(), axis=1)
        best = int(np.argmin(d))
        label = int(self.y[best])
        other = d[self.y != label]
        margin = float(other.min() - d[best]) if len(other) else float("inf")
        return label, margin

    def read(self, cell_rgb: np.ndarray) -> tuple[int | None, float]:
        """(value, min margin over its glyphs). None if the cell has no glyphs."""
        gs = glyphs(cell_rgb)
        if not gs:
            return None, 0.0
        digits, margins = zip(*(self.classify(g) for g in gs))
        return int("".join(map(str, digits))), float(min(margins))


def harvest(cell_rgb: np.ndarray, value: int) -> list[tuple[np.ndarray, int]]:
    """Label a cell's glyphs from its known value; [] if the glyph count doesn't match.
    Each glyph is also added blurred, so templates cover soft (rescaled) captures."""
    gs = glyphs(cell_rgb)
    s = str(value)
    if len(gs) != len(s):
        return []
    out = []
    for g, ch in zip(gs, s):
        out.append((g, int(ch)))
        out.append((blur(g), int(ch)))
    return out


def blur(g: np.ndarray) -> np.ndarray:
    """Soften a normalised glyph the way a ~0.75x rescale does."""
    im = Image.fromarray((g * 255).astype(np.uint8))
    small = im.resize((GLYPH_W * 3 // 4, GLYPH_H * 3 // 4), Image.BILINEAR)
    return np.asarray(small.resize((GLYPH_W, GLYPH_H), Image.BILINEAR), np.float32) / 255
