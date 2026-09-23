"""
Player names and titles.

Both lines are read the same way: segment glyphs as connected components of a
text mask, classify each glyph by NN over templates harvested from the answer
keys, and keep the margin. Column projection is not enough here: the italic
display font's glyphs overlap in x, but they never touch.

  - Name, display font (italic, condensed, A-Z 0-9): every glyph spans the full
    cap height, which also separates them from the prestige badge and nameplate
    streaks. A read one glyph away from a known player (a name in the answer
    keys) is snapped to it when the glyph that differs is a near tie.
  - Name, fallback font (any character outside that set, e.g. "SPEEDSPORT!",
    "바람"): upright instead of italic. No OCR: the whole name image is matched
    against known fallback-font players; an unknown one is None + review.
  - Title: mixed-case sans below the name, snapped to the title list
    (reference/titles.json plus titles seen in the answer keys).
Measured numbers are in docs/extraction-notes.md section 2.
"""

from __future__ import annotations

import collections
import difflib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from .header import _fold, _runs
from .layout import crop

ROOT = Path(__file__).resolve().parent.parent

NAME_MIN = 190            # min(RGB) of name text; the blue-grey title (~145) and nameplate stay below
TITLE_MIN, TITLE_CHROMA = 100, 80
GLYPH_H, GLYPH_W = 24, 20
FALLBACK_SLANT = 0.1      # display-font glyphs lean 0.20-0.25 (x per y); the fallback font is upright
FALLBACK_H, FALLBACK_W = 24, 240
# Native-size geometry (bar height 39 px), scaled by layout.scale:
NAME_MIN_CAP = 12         # a name glyph is at least this tall (cap height is 21-26)
STREAK_RUN = 18           # a horizontal run this long in the title line is a nameplate streak
TITLE_GLYPH_MAX = (14, 18)  # (h, w): anything bigger in the title line is not a glyph
TITLE_ASCENT, TITLE_DESCENT = 14, 4   # rows above / below the title baseline
TITLE_WORD_GAP = 4

# A glyph within this distance of the known player's letter (compared with the
# letter it was read as) is close enough to snap the name to that player.
SNAP_COST = 1.0
# A glyph further than this from every template is probably a character not yet
# seen in the answer keys (correct glyphs: at most 2.33; an unseen 5: 3.3).
UNSEEN_GLYPH = 2.5
# Titles: below these the snapped title goes to review.
TITLE_SIMILARITY, TITLE_MARGIN = 0.8, 0.1
TITLE_ACCEPT = 0.6        # below this similarity no title in the list is plausible: keep the raw read
FALLBACK_MATCH = 9.0      # same player in two screenshots: 5.0 apart; different players: 14.7 or more


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------
def components(mask: np.ndarray, min_px: int = 1) -> list[tuple[np.ndarray, np.ndarray]]:
    """(ys, xs) of every 8-connected component of at least `min_px` pixels."""
    H, W = mask.shape
    seen = np.zeros_like(mask, bool)
    out = []
    for y0, x0 in zip(*np.where(mask)):
        if seen[y0, x0]:
            continue
        stack, cy, cx = [(y0, x0)], [], []
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            cy.append(y)
            cx.append(x)
            for ny in (y - 1, y, y + 1):
                for nx in (x - 1, x, x + 1):
                    if 0 <= ny < H and 0 <= nx < W and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        if len(cy) >= min_px:
            out.append((np.array(cy), np.array(cx)))
    return out


def text_block(rgb: np.ndarray, row) -> np.ndarray:
    """The name and title lines of one row (name ROI top to title ROI bottom)."""
    x0, y0, x1, _ = row.rois["name"]
    return crop(rgb, (x0, y0, x1, row.rois["title"][3])).astype(np.int16)


def name_line(block: np.ndarray, scale: float = 1.0):
    """(glyphs, band): the name's glyph components left to right, and the line's
    (top, bottom) rows. Glyphs are the components spanning the most common
    full-height extent; the badge, its numeral and streaks have other extents."""
    cs = [c for c in components(block.min(2) > NAME_MIN, 8) if np.ptp(c[0]) >= NAME_MIN_CAP * scale]
    if not cs:
        return [], None
    tol = max(2, round(2 * scale))
    extents = {(ys.min(), ys.max()) for ys, _ in cs}
    band = max(extents, key=lambda e: sum(abs(ys.min() - e[0]) <= tol and abs(ys.max() - e[1]) <= tol
                                          for ys, _ in cs))
    glyphs = [(ys, xs) for ys, xs in cs if abs(ys.min() - band[0]) <= tol and abs(ys.max() - band[1]) <= tol]
    return sorted(glyphs, key=lambda c: c[1].min()), band


def slant(glyphs) -> float:
    """Median lean of the glyphs (x offset of the top relative to the bottom, per row)."""
    out = []
    for ys, xs in glyphs:
        h = np.ptp(ys)
        if h >= 6:
            out.append((xs[ys <= ys.min() + 3].mean() - xs[ys >= ys.max() - 3].mean()) / h)
    return float(np.median(out)) if out else 0.0


def title_line(block: np.ndarray, band, scale: float = 1.0):
    """(glyphs, baseline) of the title line below the name, or ([], None).
    Nameplate streaks are cut out first (long horizontal runs), and i/j dots
    are merged into their stems."""
    top = band[1] + 2
    sub = block[top:]
    if sub.shape[0] < 4:
        return [], None
    mn = sub.min(2)
    m = (mn > TITLE_MIN) & ((sub.max(2) - mn) < TITLE_CHROMA)
    run = STREAK_RUN * scale
    for y in range(m.shape[0]):
        for a, b in _runs(m[y]):
            if b - a + 1 >= run:
                m[y, a:b + 1] = False
    max_h, max_w = TITLE_GLYPH_MAX[0] * scale, TITLE_GLYPH_MAX[1] * scale
    cs = [(ys, xs) for ys, xs in components(m, 3) if np.ptp(ys) <= max_h and np.ptp(xs) <= max_w]
    bottoms = collections.Counter(ys.max() for ys, _ in cs if np.ptp(ys) >= 4 * scale)
    if not bottoms:
        return [], None
    base = max(bottoms, key=lambda b: sum(n for k, n in bottoms.items() if abs(k - b) <= 1))
    asc = TITLE_ASCENT * scale
    keep = [(ys, xs) for ys, xs in cs
            if abs(ys.max() - base) <= 4 * scale or (base - asc < ys.max() < base)]
    is_dot = [np.ptp(ys) <= 3 * scale and np.ptp(xs) <= 5 * scale and ys.max() < base - 5 * scale for ys, xs in keep]
    glyphs = [[ys, xs] for (ys, xs), d in zip(keep, is_dot) if not d]
    for (ys, xs), d in zip(keep, is_dot):
        if not d or not glyphs:
            continue
        cx = xs.mean()
        g = min(glyphs, key=lambda g: abs(g[1].mean() - cx))
        if abs(g[1].mean() - cx) <= 5 * scale:
            g[0], g[1] = np.concatenate([g[0], ys]), np.concatenate([g[1], xs])
    glyphs = sorted(((ys + top, xs) for ys, xs in glyphs if np.ptp(ys) >= 3), key=lambda c: c[1].min())
    return glyphs, base + top


def glyph_vector(ys, xs, top: int, height: int) -> np.ndarray:
    """A glyph's pixels in rows [top, top+height), scaled to GLYPH_H rows (aspect
    kept, centred in GLYPH_W columns) and blurred by 1 px."""
    x0 = xs.min()
    m = np.zeros((height, xs.max() - x0 + 1), np.uint8)
    ok = (ys >= top) & (ys < top + height)
    m[ys[ok] - top, xs[ok] - x0] = 255
    w = max(1, min(GLYPH_W, round(m.shape[1] * GLYPH_H / height)))
    im = Image.fromarray(m).resize((w, GLYPH_H), Image.BILINEAR).filter(ImageFilter.GaussianBlur(1))
    out = np.zeros((GLYPH_H, GLYPH_W), np.float32)
    off = (GLYPH_W - w) // 2
    out[:, off:off + w] = np.asarray(im, np.float32) / 255
    return out.ravel()


def name_vectors(glyphs) -> list[np.ndarray]:
    return [glyph_vector(ys, xs, ys.min(), np.ptp(ys) + 1) for ys, xs in glyphs]


def title_vectors(glyphs, base: int, scale: float = 1.0) -> list[np.ndarray]:
    top = base - round(TITLE_ASCENT * scale)
    height = round((TITLE_ASCENT + TITLE_DESCENT) * scale) + 1
    return [glyph_vector(ys, xs, top, height) for ys, xs in glyphs]


def fallback_vector(block: np.ndarray, glyphs, band, scale: float = 1.0) -> np.ndarray:
    """The whole name image, matched as one unit: a soft text mask (the fallback
    font's 1-px strokes make a hard mask shift with sub-pixel alignment) between
    the first and last glyph with room for accents, cropped to its ink, scaled to
    FALLBACK_H rows, blurred and left-aligned on a canvas with a 2-px border."""
    x0 = min(xs.min() for _, xs in glyphs)
    x1 = max(xs.max() for _, xs in glyphs)
    y0 = max(0, band[0] - round(8 * scale))
    y1 = band[1] + round(3 * scale)
    s = np.clip((block[y0:y1 + 1, x0:x1 + 1].min(2).astype(np.float32) - 120) / 100, 0, 1)
    rows, cols = np.where((s > 0.5).any(1))[0], np.where((s > 0.5).any(0))[0]
    s = s[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    w = max(1, min(FALLBACK_W - 4, round(s.shape[1] * FALLBACK_H / s.shape[0])))
    im = Image.fromarray((s * 255).astype(np.uint8)).resize((w, FALLBACK_H), Image.BILINEAR)
    out = np.zeros((FALLBACK_H + 4, FALLBACK_W), np.float32)
    out[2:2 + FALLBACK_H, 2:2 + w] = np.asarray(im.filter(ImageFilter.GaussianBlur(1.5)), np.float32) / 255
    return out.ravel()


class NameImages:
    """Whole-name images of known fallback-font players; NN allowing a 1-px shift."""

    def __init__(self, templates: np.ndarray, labels):
        self.M = np.asarray(templates, np.float32).reshape(len(templates), FALLBACK_H + 4, FALLBACK_W)
        self.labels = [str(x) for x in labels]

    @classmethod
    def load(cls, path) -> "NameImages":
        d = np.load(path)
        return cls(d["templates"].astype(np.float32) / 255, d["labels"])

    def match(self, v: np.ndarray) -> tuple[str | None, float, float]:
        """(name, distance, margin to the nearest other name)."""
        if not len(self.labels):
            return None, float("inf"), 0.0
        a = v.reshape(FALLBACK_H + 4, FALLBACK_W)
        shifts = [np.roll(np.roll(a, dy, 0), dx, 1) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
        d = np.array([min(np.linalg.norm(s - t) for s in shifts) for t in self.M])
        i = int(np.argmin(d))
        other = [x for x, lab in zip(d, self.labels) if lab != self.labels[i]]
        return self.labels[i], float(d[i]), float(min(other) - d[i]) if other else float("inf")


def words(glyphs, labels, scale: float = 1.0) -> str:
    """Join glyph labels, with a space wherever the gap between glyphs is a word gap."""
    out, prev = "", None
    for (_, xs), ch in zip(glyphs, labels):
        if prev is not None and xs.min() - prev >= TITLE_WORD_GAP * scale:
            out += " "
        out += ch
        prev = xs.max()
    return out


# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------
class GlyphSet:
    """Labelled glyph templates; NN with margin."""

    def __init__(self, templates: np.ndarray, labels):
        self.M = np.asarray(templates, np.float32).reshape(len(templates), -1)
        self.labels = np.array([str(x) for x in labels])

    @classmethod
    def load(cls, path) -> "GlyphSet":
        d = np.load(path)
        return cls(d["templates"].astype(np.float32) / 255, d["labels"])

    def classify(self, v: np.ndarray) -> tuple[str, float, float]:
        """(label, distance, margin to the nearest other label)."""
        d = np.linalg.norm(self.M - v, axis=1)
        i = int(np.argmin(d))
        other = d[self.labels != self.labels[i]]
        return self.labels[i], float(d[i]), float(other.min() - d[i]) if other.size else float("inf")

    def distance(self, v: np.ndarray, label: str) -> float:
        d = np.linalg.norm(self.M[self.labels == label] - v, axis=1)
        return float(d.min()) if d.size else float("inf")


def is_display_name(name: str) -> bool:
    """True if the game draws this name in the display font (A-Z, 0-9 only)."""
    return all(c.isascii() and c.isalnum() for c in name) and name == name.upper()


def _fold_title(s: str) -> str:
    """Case, accents and spaces ignored; lookalike glyphs (0/O, 1/I/l, 5/S, 8/B) equal."""
    return _fold(s).translate(str.maketrans("01L58", "OIISB"))


class TextModels:
    def __init__(self, name_glyphs: GlyphSet, title_glyphs: GlyphSet, players=(), titles=(),
                 fallback: NameImages | None = None):
        self.names, self.title_glyphs, self.fallback = name_glyphs, title_glyphs, fallback
        self.players = sorted({p for p in players if is_display_name(p)})
        self.titles = sorted(set(titles))

    @classmethod
    def load(cls, folder: Path, titles_json: Path = ROOT / "reference" / "titles.json") -> "TextModels":
        known = json.loads((folder / "known_text.json").read_text(encoding="utf-8"))
        fb = folder / "fallback_names.npz"
        return cls(GlyphSet.load(folder / "name_glyphs.npz"), GlyphSet.load(folder / "title_glyphs.npz"),
                   known["players"], reference_titles(titles_json) + known["titles"],
                   NameImages.load(fb) if fb.exists() else None)

    # -- names --------------------------------------------------------------
    def read_name(self, block: np.ndarray, scale: float = 1.0) -> dict:
        glyphs, band = name_line(block, scale)
        if not glyphs:
            return {"name": None, "font": None, "margin": 0.0, "band": None, "note": "no name found"}
        if slant(glyphs) < FALLBACK_SLANT:
            return {**self._fallback_name(block, glyphs, band, scale), "band": band}
        vecs = name_vectors(glyphs)
        reads = [self.names.classify(v) for v in vecs]
        raw = "".join(r[0] for r in reads)
        out = {"name": raw, "font": "display", "raw": raw, "margin": min(r[2] for r in reads), "band": band}
        unseen = [i for i, r in enumerate(reads) if r[1] > UNSEEN_GLYPH]
        if unseen:
            out["note"] = f"glyph {unseen} unlike any known character"
        if raw in self.players:
            return out
        # One glyph off a known player, and that glyph is a near tie between the two letters?
        best = None
        for p in self.players:
            if len(p) != len(raw):
                continue
            diff = [i for i, (a, b) in enumerate(zip(raw, p)) if a != b]
            if len(diff) == 1:
                i = diff[0]
                cost = self.names.distance(vecs[i], p[i]) - reads[i][1]
                if cost <= SNAP_COST and (best is None or cost < best[0]):
                    best = (cost, p)
        if best:
            out.update(name=best[1], snapped=True, margin=0.0)
        return out

    def _fallback_name(self, block, glyphs, band, scale) -> dict:
        out = {"name": None, "font": "fallback", "margin": 0.0}
        if self.fallback is None or not len(self.fallback.labels):
            return {**out, "note": "fallback font, no known fallback-font players"}
        name, dist, margin = self.fallback.match(fallback_vector(block, glyphs, band, scale))
        if dist > FALLBACK_MATCH:
            return {**out, "distance": round(dist, 3), "note": "fallback font, not a known player"}
        return {**out, "name": name, "distance": round(dist, 3), "margin": min(margin, 99.0)}

    # -- titles -------------------------------------------------------------
    def read_title(self, block: np.ndarray, band, scale: float = 1.0) -> dict:
        glyphs, base = title_line(block, band, scale) if band else ([], None)
        on_base = sum(abs(ys.max() - base) <= 1 for ys, _ in glyphs) if glyphs else 0
        if on_base < 3:
            return {"title": None}
        labels = [self.title_glyphs.classify(v)[0] for v in title_vectors(glyphs, base, scale)]
        raw = words(glyphs, labels, scale)
        if not self.titles:
            return {"title": raw, "raw": raw, "similarity": 0.0, "margin": 0.0}
        scored = sorted(((difflib.SequenceMatcher(None, _fold_title(raw), _fold_title(t)).ratio(), t)
                         for t in self.titles), reverse=True)
        sim, best = scored[0]
        margin = sim - (scored[1][0] if len(scored) > 1 else 0.0)
        return {"title": best if sim >= TITLE_ACCEPT else raw, "raw": raw,
                "similarity": round(sim, 3), "margin": round(margin, 3)}


def reference_titles(path: Path = ROOT / "reference" / "titles.json") -> list[str]:
    if not path.exists():
        return []
    return [t["title"] for t in json.loads(path.read_text(encoding="utf-8"))["titles"]]


def read_text(rgb: np.ndarray, row, models: TextModels, scale: float = 1.0) -> tuple[dict, dict]:
    """(name read, title read) for one row."""
    block = text_block(rgb, row)
    name = models.read_name(block, scale)
    title = models.read_title(block, name.get("band"), scale)
    return name, title
