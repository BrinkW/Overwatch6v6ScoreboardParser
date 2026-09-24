"""
Header fields: mode, map, match time, the four hero bans and the match rank range.

Everything here is closed-set recognition with a margin, like the rest of the
parser:
  - bans:  NN against assets/bans, the art composited onto the red ban tile;
  - rank:  tier from the emblem's hue (shortlist) and silhouette (decides);
           division from the whole badge (its frame is identical for every
           rank, so the digit is the only difference);
  - text:  letter and time-digit templates harvested from the answer keys; the
           read map and mode are snapped to reference/maps.json.
Measured numbers are in docs/extraction-notes.md section 3.
"""

from __future__ import annotations

import colorsys
import difflib
import json
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image

from .icons import nearest, portrait_descriptor

ROOT = Path(__file__).resolve().parent.parent


def _runs(mask) -> list[tuple[int, int]]:
    """Half-open (start, end) runs where mask is True."""
    m = np.concatenate([[False], np.asarray(mask, bool), [False]])
    d = np.diff(m.astype(np.int8))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))

# ---------------------------------------------------------------------------
# Rank tier: hue shortlists, silhouette decides
# ---------------------------------------------------------------------------
TIER_SHEET = ROOT / "assets" / "rankiconsnew.png"
SHEET_BG = np.array([53, 68, 89])
TIERS = ["Bronze", "Silver", "Gold", "Platinum", "Emerald", "Diamond", "Master", "Grandmaster", "Champion"]
HUE_WINDOW = 30.0        # degrees: tiers this close to the emblem's hue are candidates
HUE_SCALE = 40.0         # degrees of hue difference that cost as much as 1.0 of silhouette RMS
MIN_CHROMATIC_PX = 30    # fewer saturated pixels than this = achromatic (Silver)
EMBLEM_FRACTION = 0.66   # the emblem is the top 66% of a rank crop; the division badge is below


def _metal(rgb: np.ndarray) -> np.ndarray:
    """Bright, saturated emblem metal. Excludes white lens flares and dark backdrop."""
    a = rgb.astype(np.int16)
    mx, mn = a.max(2), a.min(2)
    return (mx > 150) & ((mx - mn) > 35)


def emblem_hue(rgb: np.ndarray) -> float | None:
    """Circular mean hue (degrees) of the emblem metal; None if achromatic."""
    m = _metal(rgb)
    if m.sum() < MIN_CHROMATIC_PX:
        return None
    px = rgb[m].astype(np.float32) / 255
    h = np.deg2rad([colorsys.rgb_to_hsv(*p)[0] * 360 for p in px[::2]])
    return float(np.rad2deg(np.arctan2(np.sin(h).mean(), np.cos(h).mean())) % 360)


def silhouette(mask: np.ndarray, n: int = 29) -> np.ndarray | None:
    """Width of the shape at n evenly spaced heights, as a fraction of its max width,
    followed by its aspect ratio (w/h). Scale-free."""
    ys, xs = np.where(mask)
    if len(ys) < 10:
        return None
    m = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    H, W = m.shape
    prof = []
    for r in np.linspace(0, H - 1, n).astype(int):
        xx = np.where(m[r])[0]
        prof.append((xx.max() - xx.min() + 1) / W if len(xx) else 0.0)
    return np.array(prof + [W / H / 2])   # aspect folded in, scaled to the profile's range


def _hue_gap(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def sheet_emblems() -> dict[str, np.ndarray]:
    """{tier: RGB crop} for each emblem on the tier sheet, located by column runs
    against its flat background (runs closer than 20 px merge, e.g. Silver's prongs)."""
    s = np.asarray(Image.open(TIER_SHEET).convert("RGB")).astype(int)
    fg = np.abs(s - SHEET_BG).sum(2)[:140] > 60
    runs, start = [], None
    for i, on in enumerate(np.append(fg.any(0), False)):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if runs and start - runs[-1][1] < 20:
                runs[-1] = (runs[-1][0], i)
            elif i - start > 25:
                runs.append((start, i))
            start = None
    if len(runs) != len(TIERS):
        raise ValueError(f"expected {len(TIERS)} emblems on {TIER_SHEET.name}, found {len(runs)}")
    return {t: s[:140, a:b].astype(np.uint8) for t, (a, b) in zip(TIERS, runs)}


def emblem_features(emblem_rgb: np.ndarray) -> tuple[float | None, np.ndarray | None]:
    """(hue, silhouette) of an emblem image (emblem only, no division badge)."""
    hue = emblem_hue(emblem_rgb)
    mask = _metal(emblem_rgb) if hue is not None else emblem_rgb.max(2) > 150
    return hue, silhouette(mask)


class TierReader:
    """Tier from the emblem: colour shortlists, shape decides.

    References per tier: the tier sheet (all nine tiers, Emerald included) plus
    emblems harvested from the answer-key screenshots (`extra`), because the game
    renders emblems slightly differently from the sheet. Score for a candidate =
    hue distance / HUE_SCALE + best silhouette RMS over its references; only
    tiers within HUE_WINDOW of the emblem's hue are candidates. Emerald and
    Master are 7 degrees apart, so for them the silhouette does the work (a narrow
    V vs a wide W)."""

    def __init__(self, extra: list[tuple[str, np.ndarray]] = ()):
        self.hue: dict[str, float | None] = {}
        self.profiles: dict[str, list[np.ndarray]] = {}
        for tier, crop in sheet_emblems().items():
            h, prof = emblem_features(crop)
            self.hue[tier], self.profiles[tier] = h, [prof]
        for tier, prof in extra:
            self.profiles[tier].append(prof)

    @classmethod
    def load(cls, path) -> "TierReader":
        d = np.load(path)
        return cls(list(zip([str(t) for t in d["tiers"]], d["profiles"])))

    def classify(self, emblem_rgb: np.ndarray) -> tuple[str | None, float, dict]:
        """(tier, margin, evidence). Margin = runner-up score minus best score (1.0
        when no other tier is within the hue window)."""
        hue, prof = emblem_features(emblem_rgb)
        if hue is None:
            return "Silver", 1.0, {"hue": None}
        scores = {}
        for tier, h in self.hue.items():
            if h is None or _hue_gap(h, hue) > HUE_WINDOW or prof is None:
                continue
            rms = min(float(np.sqrt(((prof - p) ** 2).mean())) for p in self.profiles[tier])
            scores[tier] = _hue_gap(h, hue) / HUE_SCALE + rms
        if not scores:
            return None, 0.0, {"hue": round(hue)}
        ranked = sorted(scores, key=scores.get)
        margin = scores[ranked[1]] - scores[ranked[0]] if len(ranked) > 1 else 1.0
        return ranked[0], margin, {"hue": round(hue), "scores": {t: round(scores[t], 3) for t in ranked}}


# ---------------------------------------------------------------------------
# Division: the whole badge
# ---------------------------------------------------------------------------
BADGE_HEIGHT = 0.22      # fraction of the rank crop's height, from the top of the badge's wings
BADGE_SIZE = (48, 20)


def badge_descriptor(rank_rgb: np.ndarray) -> np.ndarray | None:
    """The division badge (silver wings, hexagon, digit) from a rank crop.

    Matched whole: the frame is the same for every rank, so it cancels out and the
    digit is the only difference. Cutting the digit out failed: it touches the
    hexagon frame, and in small crops the hexagon doesn't always close. The crop
    has a fixed height anchored at the top of the wings (the rows where the silver
    spans widest), so crops line up between screenshots."""
    H = rank_rgb.shape[0]
    b = rank_rgb[int(H * EMBLEM_FRACTION):].astype(np.int16)
    g = b.min(2)
    bright = (g > 110) & ((b.max(2) - g) < 40)          # silver/white, not the coloured emblem
    count = bright.sum(1)
    if count.max() < 5:
        return None
    top = int(np.where(count >= 0.6 * count.max())[0].min())
    band = bright[top:top + int(round(BADGE_HEIGHT * H))]
    xs = np.where(band.any(0))[0]
    crop = g[top:top + band.shape[0], xs.min():xs.max() + 1].clip(0, 255).astype(np.uint8)
    v = np.asarray(Image.fromarray(crop).resize(BADGE_SIZE, Image.BILINEAR), np.float32).ravel()
    return (v - v.mean()) / (v.std() + 1e-6)


class DivisionReader:
    def __init__(self, templates: np.ndarray, labels):
        self.M, self.labels = templates, [int(x) for x in labels]

    @classmethod
    def load(cls, path) -> "DivisionReader":
        d = np.load(path)
        return cls(d["templates"], d["labels"])

    def read(self, rank_rgb: np.ndarray) -> tuple[int | None, float]:
        v = badge_descriptor(rank_rgb)
        if v is None:
            return None, 0.0
        label, margin, _ = nearest(self.M, self.labels, v)
        return int(label), margin


# ---------------------------------------------------------------------------
# Bans
# ---------------------------------------------------------------------------
BAN_TILE = (140, 20, 25)   # the red tile behind a ban portrait (on white/black: 14-51 of 51 correct)
BAN_INNER = 0.08           # cropped off each side: the bright red frame
EMPTY_BAN_CHROMA = 10      # an empty "no ban" slot is a grey icon


class BanReader:
    """The four ban portraits, matched against assets/bans. That art has a
    transparent background while the game draws the portrait on a red tile, so the
    art is composited onto that red first (51/51 on the reviewed screenshots)."""

    def __init__(self, bans_dir: Path = ROOT / "assets" / "bans"):
        self.labels, vecs = [], []
        for p in sorted(bans_dir.glob("*.png")):
            im = Image.open(p).convert("RGBA")
            base = Image.new("RGBA", im.size, BAN_TILE + (255,))
            base.alpha_composite(im)
            self.labels.append(p.stem)
            vecs.append(portrait_descriptor(np.asarray(base.convert("RGB"))))
        self.M = np.stack(vecs)

    def read(self, tile_rgb: np.ndarray) -> tuple[str, float]:
        a = tile_rgb.astype(np.int16)
        if float((a.max(2) - a.min(2)).mean()) < EMPTY_BAN_CHROMA:
            return "none", float("inf")
        h, w = tile_rgb.shape[:2]
        inner = tile_rgb[int(h * BAN_INNER):int(h * (1 - BAN_INNER)), int(w * BAN_INNER):int(w * (1 - BAN_INNER))]
        label, margin, _ = nearest(self.M, self.labels, portrait_descriptor(inner))
        return label, margin


# ---------------------------------------------------------------------------
# Mode | map and match time: one right-aligned strip
# ---------------------------------------------------------------------------
LETTER_H = LETTER_W = 20
TIME_SHEAR = 0.2           # undo the time digits' italic slant (they touch otherwise)


def split_strip(strip: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(light text mask left of the time, orange time-digit mask). The time digits
    are the only orange in the strip; "MODE | MAP" and "TIME:" are light grey in
    the classic UI and lavender (chroma ~43) in the tabbed one. The faint
    "PRESS F9 ..." ghost text is dimmer than the brightness threshold."""
    a = strip.astype(np.int16)
    mx, mn = a.max(2), a.min(2)
    orange = (a[..., 0] > 200) & (a[..., 1] > 60) & (a[..., 1] < 180) & (a[..., 2] < 90)
    cols = np.where(orange.sum(0) >= 2)[0]
    x_time = int(cols.min()) if len(cols) else a.shape[1]
    grey = (mx > 150) & ((mx - mn) < 60)
    return grey[:, :x_time], orange[:, max(0, x_time - 2):]


def _line_band(mask):
    rows = _runs(mask.any(1))
    return max(rows, key=lambda r: mask[r[0]:r[1]].sum()) if rows else None


def _norm_line(mask, band, span) -> np.ndarray:
    """A glyph normalised to the LINE height, not its own: an apostrophe stays small and high."""
    a, b = span
    g = mask[band[0]:band[1], a:b].astype(np.uint8) * 255
    sc = LETTER_H / max(1, band[1] - band[0])
    w = max(1, min(LETTER_W, round((b - a) * sc)))
    out = np.zeros((LETTER_H, LETTER_W), np.float32)
    off = (LETTER_W - w) // 2
    out[:, off:off + w] = np.asarray(Image.fromarray(g).resize((w, LETTER_H), Image.BILINEAR), np.float32) / 255
    return out


def text_glyphs(grey: np.ndarray, word_gap: int = 10) -> list[np.ndarray]:
    """Letter glyphs of "MODE|MAP", left to right, without the mode icon and the
    "TIME:" label. Spaces are not glyphs; "|" is."""
    band = _line_band(grey)
    if band is None:
        return []
    m = grey.copy()
    m[:band[0]] = False
    m[band[1]:] = False
    spans = [(a, b) for a, b in _runs(m.any(0)) if m[:, a:b].sum() > 4]
    words = []
    for a, b in spans:
        if words and a - words[-1][-1][1] <= word_gap:
            words[-1].append((a, b))
        else:
            words.append([(a, b)])
    if len(words) < 2:
        return []
    letters = [s for w in words[:-1] for s in w][1:]      # drop "TIME:" (last word) and the mode icon
    return [_norm_line(m, band, s) for s in letters]


def time_glyphs(orange: np.ndarray) -> list[np.ndarray]:
    """Digit glyphs of the match time, italic slant removed, colon dropped."""
    band = _line_band(orange)
    if band is None:
        return []
    m = orange[band[0]:band[1]]
    H, W = m.shape
    sheared = np.zeros((H, W + int(TIME_SHEAR * H) + 2), bool)
    for y in range(H):                       # tops lean right: move lower rows right to match
        s = int(round(TIME_SHEAR * y))
        sheared[y, s:s + W] = m[y]
    spans = [(a, b) for a, b in _runs(sheared.any(0)) if sheared[:, a:b].sum() > 6]
    spans = [s for s in spans if sheared[:, s[0]:s[1]].any(1).sum() > 0.6 * H]   # the colon's dots are short
    return [_norm_line(sheared, (0, H), s) for s in spans]


class GlyphReader:
    """NN over labelled glyph templates (letters, or time digits)."""

    def __init__(self, templates: np.ndarray, labels):
        self.M = templates.reshape(len(templates), -1)
        self.labels = [str(x) for x in labels]

    @classmethod
    def load(cls, path) -> "GlyphReader":
        d = np.load(path)
        return cls(d["templates"].astype(np.float32) / 255, d["labels"])

    def read(self, glyphs, allowed=None) -> tuple[str, float]:
        """(string, min margin). `allowed(i)` optionally restricts position i's labels."""
        out, margins = [], []
        for i, g in enumerate(glyphs):
            ok = None if allowed is None else np.array([c in allowed(i) for c in self.labels])
            label, margin, _ = nearest(self.M, self.labels, g.ravel(), ok)
            out.append(label)
            margins.append(margin)
        return "".join(out), (min(margins) if margins else 0.0)


def _fold(s: str) -> str:
    """Uppercase, accents and spaces removed: how snapping compares names."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return "".join(c for c in s.upper() if not c.isspace())


def snap(raw: str, options: list[str]) -> tuple[str | None, float, float]:
    """(best option, similarity 0-1, margin to the runner-up)."""
    if not options:
        return None, 0.0, 0.0
    scored = sorted(((difflib.SequenceMatcher(None, _fold(raw), _fold(o)).ratio(), o) for o in options), reverse=True)
    second = scored[1][0] if len(scored) > 1 else 0.0
    return scored[0][1], scored[0][0], scored[0][0] - second


# ---------------------------------------------------------------------------
# Everything together
# ---------------------------------------------------------------------------
class HeaderModels:
    def __init__(self, letters: GlyphReader, time_digits: GlyphReader, divisions: DivisionReader,
                 tiers: TierReader, bans: BanReader | None = None, maps_json: Path = ROOT / "reference" / "maps.json"):
        self.letters, self.time_digits, self.divisions, self.tiers = letters, time_digits, divisions, tiers
        self.bans = bans or BanReader()
        maps = json.loads(maps_json.read_text(encoding="utf-8"))["maps"] if maps_json.exists() else []
        self.map_mode = {m["name"]: m["mode"] for m in maps}
        self.modes = sorted({m["mode"] for m in maps})

    @classmethod
    def load(cls, folder: Path) -> "HeaderModels":
        return cls(GlyphReader.load(folder / "letters.npz"), GlyphReader.load(folder / "time_digits.npz"),
                   DivisionReader.load(folder / "division.npz"), TierReader.load(folder / "rank_emblems.npz"))


REVIEW = {"ban": 1.0, "tier": 0.05, "division": 1.0, "map_similarity": 0.6, "map_margin": 0.05, "time": 0.1}


def read_header(rgb: np.ndarray, layout, models: HeaderModels) -> tuple[dict, dict, list[str], list[str]]:
    """(header, margins, review, problems)."""
    from .layout import crop
    review, problems, margin = [], [], {}

    # 4 or 5 ban slots, however many the screen shows (layout.find_bans)
    bans = []
    for i in range(sum(k.startswith("ban_") for k in layout.header)):
        slug, m = models.bans.read(crop(rgb, layout.header[f"ban_{i}"]))
        bans.append(slug)
        margin[f"ban_{i}"] = round(min(m, 99.0), 3)
        if m < REVIEW["ban"]:
            review.append(f"header.ban_{i}")
    if not bans:
        problems.append("header: no ban slots found")

    rank = []
    for key in ("rank_low", "rank_high"):
        if key not in layout.header:
            rank.append(None)
            problems.append(f"header: {key} not found")
            continue
        c = crop(rgb, layout.header[key])
        tier, tm, _ = models.tiers.classify(c[:int(c.shape[0] * EMBLEM_FRACTION)])
        div, dm = models.divisions.read(c)
        rank.append(f"{tier} {div}" if tier and div else None)
        margin[f"{key}_tier"], margin[f"{key}_division"] = round(tm, 3), round(min(dm, 99.0), 3)
        if tier is None or tm < REVIEW["tier"]:
            review.append(f"header.{key}.tier")
        if div is None or dm < REVIEW["division"]:
            review.append(f"header.{key}.division")

    if "mode_map_time" not in layout.header:
        problems.append("header: match time not found")
        header = {"mode": None, "map": None, "time": None, "bans": bans, "rank_range": rank, "raw": {"mode_map": None}}
        return header, margin, review, problems
    grey, orange = split_strip(crop(rgb, layout.header["mode_map_time"]))
    raw, _ = models.letters.read(text_glyphs(grey))
    mode_raw, _, map_raw = raw.partition("|")
    map_name, sim, mmargin = snap(map_raw, list(models.map_mode))
    mode, msim, _ = snap(mode_raw, models.modes)
    margin.update(map_similarity=round(sim, 3), map_margin=round(mmargin, 3), mode_similarity=round(msim, 3))
    if sim < REVIEW["map_similarity"] or mmargin < REVIEW["map_margin"]:
        review.append("header.map")
    if map_name and mode and models.map_mode.get(map_name) != mode:
        problems.append(f"header: read mode {mode!r} but {map_name} is a {models.map_mode.get(map_name)} map")

    digits = time_glyphs(orange)
    n = len(digits)
    # the tens-of-seconds digit can only be 0-5
    t, tmargin = models.time_digits.read(digits, allowed=lambda i: "012345" if i == n - 2 else "0123456789")
    time = f"{t[:-2]}:{t[-2:]}" if len(t) >= 3 else None
    margin["time"] = round(tmargin, 3)
    if time is None:
        problems.append("header: match time unreadable")
    elif tmargin < REVIEW["time"]:
        review.append("header.time")

    header = {"mode": mode, "map": map_name, "time": time, "bans": bans, "rank_range": rank,
              "raw": {"mode_map": raw}}
    return header, margin, review, problems
