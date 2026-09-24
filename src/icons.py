"""
Role icons, hero portraits and perk icons: all nearest-neighbour template
matching against closed sets, each returning a margin (runner-up distance minus
best distance), the confidence signal the rest of the parser routes on.

Measured on the 13 reviewed screenshots (156 rows):
  - role icon:  156/156 (templates harvested from the answer keys);
  - portrait:   156/156 against assets/heroes. The scoreboard shows each hero's
                standard portrait whatever skin is equipped, so this is the
                primary hero signal;
  - perk glyph: 290/292 name the right hero with no other help; empty slots
                are detected 20/20.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from .digits import whiteness

ROOT = Path(__file__).resolve().parent.parent
ROLES = ("tank", "damage", "support")

# Perk slot geometry, as fractions of the perk ROI's half-size.
DISC_TEST_R = 0.62   # where the white disc must be, if a perk is present
GLYPH_R = 0.72       # the glyph lies inside this radius; the grey ring lies outside
PERK_SIZE, PERK_BLUR = 24, 1.0
# Correct matches sit at distance <= 0.59 (median 0.16) on the reviewed screenshots;
# the one glyph whose in-game art differs from the wiki's (Baptiste, redrawn
# Automated Healing) sits at 0.73. Beyond this, say "unrecognised", don't guess.
UNKNOWN_GLYPH_DIST = 0.65
# Two perks whose art is the same glyph (e.g. Lingering and Ravenous Wraith) match at
# equal distance; within this tolerance the scoreboard's tier rules decide.
TIE_DIST = 0.01


# ---------------------------------------------------------------------------
# Generic nearest neighbour with a margin
# ---------------------------------------------------------------------------
def nearest(library: np.ndarray, labels: list, v: np.ndarray, allowed=None) -> tuple:
    """(best_label, margin, best_index): margin = distance to the best entry with a
    DIFFERENT label minus the best distance. `allowed` masks candidates."""
    d = np.linalg.norm(library - v, axis=1)
    if allowed is not None:
        d = np.where(allowed, d, np.inf)
    order = np.argsort(d)
    best = int(order[0])
    other = next((j for j in order[1:] if labels[j] != labels[best] and np.isfinite(d[j])), None)
    margin = float(d[other] - d[best]) if other is not None else float("inf")
    return labels[best], margin, best


# ---------------------------------------------------------------------------
# Role icon
# ---------------------------------------------------------------------------
def role_descriptor(crop: np.ndarray) -> np.ndarray:
    w = whiteness(crop)
    return np.asarray(Image.fromarray((w * 255).astype(np.uint8)).resize((16, 16), Image.BILINEAR),
                      np.float32).ravel() / 255


ROLE_BLANK = 0.05   # white share of a role cell: every icon >= 0.20; a blank cell 0.0


def role_is_blank(crop: np.ndarray) -> bool:
    """No role icon (a player who just swapped hero): the role cell is plain team colour."""
    return float((whiteness(crop) > 0.5).mean()) < ROLE_BLANK


class RoleClassifier:
    def __init__(self, templates: np.ndarray, labels):
        self.T, self.y = templates.reshape(len(templates), -1), list(labels)

    @classmethod
    def load(cls, path) -> "RoleClassifier":
        d = np.load(path)
        return cls(d["templates"].astype(np.float32) / 255, [str(x) for x in d["labels"]])

    def classify(self, crop) -> tuple[str, float]:
        label, margin, _ = nearest(self.T, self.y, role_descriptor(crop))
        return str(label), margin


# ---------------------------------------------------------------------------
# Portrait
# ---------------------------------------------------------------------------
def portrait_descriptor(img: np.ndarray, size: int = 24) -> np.ndarray:
    """Centre square, 24x24 colour, z-scored (brightness/contrast invariant)."""
    h, w = img.shape[:2]
    s = min(h, w)
    sq = img[(h - s) // 2:(h - s) // 2 + s, (w - s) // 2:(w - s) // 2 + s]
    a = np.asarray(Image.fromarray(sq).convert("RGB").resize((size, size), Image.BILINEAR), np.float32)
    return ((a - a.mean()) / (a.std() + 1e-6)).ravel()


# A player who has just swapped hero shows a translucent "?" silhouette over the
# team colour instead of a portrait (assets/heroes/mystery.png is that figure, for
# display). It is recognised by being tinted with one hue throughout: min(share of
# tinted pixels, hue concentration) is 0.999 for it and at most 0.79 for any of
# the 167 real portraits in the answer keys.
MYSTERY = "mystery"
MYSTERY_TINT = 0.93


def mystery_score(crop: np.ndarray) -> float:
    """min(share of pixels with chroma > 30, concentration of their hue), in [0, 1]."""
    a = crop.astype(np.float32)
    ch = a.max(2) - a.min(2)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    hue = np.arctan2(np.sqrt(3) * (g - b), 2 * r - g - b)
    w = ch * (ch > 30)
    conc = np.hypot((w * np.cos(hue)).sum(), (w * np.sin(hue)).sum()) / max(float(w.sum()), 1e-6)
    return min(float((ch > 30).mean()), float(conc))


# A dead player's portrait is dimmed and overlaid with a respawn countdown: a thin
# white-and-red ring at 0.275x the portrait width, with the seconds inside. On a
# thin circle at that radius 0.95-0.99 of the pixels are ring-coloured; on any
# plain portrait at most 0.73. The ring and digits cover the face, so such a
# portrait can't be trusted (masking them out made it worse: Mei matched Vendetta
# with margin 3.0); the perk glyphs decide instead.
RESPAWN_RING = 0.85
RING_RADIUS = (0.25, 0.32)      # searched radius range, x portrait width


def respawn_ring(crop: np.ndarray) -> tuple[float, float, float] | None:
    """(cy, cx, r) of a respawn countdown ring in a portrait crop, or None."""
    a = crop.astype(np.int16)
    h, w = a.shape[:2]
    on = (a.min(2) > 170) | ((a[..., 0] > 150) & (a[..., 1] < 90) & (a[..., 2] < 90))
    if on.mean() < 0.1:                     # cheap reject: a ring alone covers ~12% of the crop
        return None
    yy, xx = np.mgrid[0:h, 0:w]
    best = (0.0, None)
    k = max(2, round(0.08 * w))            # the ring sits up to ~4 px off the crop's centre
    for cy in np.arange(h / 2 - k, h / 2 + k + 1):
        for cx in np.arange(w / 2 - k, w / 2 + k + 1):
            d = np.hypot(yy - cy, xx - cx)
            for r in np.arange(RING_RADIUS[0] * w, RING_RADIUS[1] * w):
                v = float(on[np.abs(d - r) <= 1.5].mean())
                if v > best[0]:
                    best = (v, (float(cy), float(cx), float(r)))
    return best[1] if best[0] >= RESPAWN_RING else None


class PortraitLibrary:
    """Every hero with an assets/heroes/<slug>.png: grows automatically with the reference sync.
    The mystery figure is not a portrait: it is detected by mystery_score instead."""

    def __init__(self, heroes_dir: Path = ROOT / "assets" / "heroes"):
        files = sorted(p for p in heroes_dir.iterdir() if p.suffix.lower() == ".png" and p.stem != MYSTERY)
        self.labels = [p.stem for p in files]
        self.M = np.stack([portrait_descriptor(np.asarray(Image.open(p).convert("RGB"))) for p in files])

    def classify(self, crop) -> tuple[str, float]:
        label, margin, _ = nearest(self.M, self.labels, portrait_descriptor(crop))
        return label, margin


# ---------------------------------------------------------------------------
# Perks
# ---------------------------------------------------------------------------
def _normalise_glyph(g: np.ndarray) -> np.ndarray | None:
    """Tight bbox -> square -> 24x24 -> light blur -> unit length. Scale-free, so the
    128 px artwork and the ~28 px in-game glyph meet in the same space."""
    ys, xs = np.where(g > 0.5)
    if len(ys) < 5:
        return None
    m = g[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = m.shape
    s = max(h, w)
    c = np.zeros((s, s), np.float32)
    c[(s - h) // 2:(s - h) // 2 + h, (s - w) // 2:(s - w) // 2 + w] = m
    im = Image.fromarray((c * 255).astype(np.uint8)).resize((PERK_SIZE, PERK_SIZE), Image.BILINEAR)
    v = np.asarray(im.filter(ImageFilter.GaussianBlur(PERK_BLUR)), np.float32).ravel() / 255
    return v / (np.linalg.norm(v) + 1e-6)


def _radial(shape, r_frac):
    H, W = shape
    yy, xx = np.mgrid[:H, :W]
    return ((yy - H / 2) ** 2 + (xx - W / 2) ** 2) < (r_frac * min(H, W) / 2) ** 2


def slot_is_empty(crop: np.ndarray) -> bool:
    """A perk sits on a white disc; no disc means an empty slot."""
    white = crop.astype(np.int16).min(2) > 170
    return white[_radial(white.shape, DISC_TEST_R)].mean() < 0.25


def perk_glyph_descriptor(crop: np.ndarray) -> np.ndarray | None:
    """The dark glyph inside the disc, as soft darkness in [0, 1]."""
    mx = crop.astype(np.float32).max(2)
    dark = np.clip((170 - mx) / 120, 0, 1) * _radial(mx.shape, GLYPH_R)
    return _normalise_glyph(dark)


def _tiers_ever(e: dict) -> set[str]:
    """Every tier this perk is known to have held: its current tier plus both ends
    of each recorded move."""
    tiers = {e["tier"]}
    for m in e.get("tier_history", []):
        tiers |= {m["from"], m["to"]}
    return tiers


class PerkLibrary:
    """Every perk with an icon in reference/perks.json (live and legacy)."""

    def __init__(self, perks_json: Path = ROOT / "reference" / "perks.json",
                 heroes_json: Path = ROOT / "reference" / "heroes.json"):
        heroes = json.loads(heroes_json.read_text(encoding="utf-8"))["heroes"]
        data = json.loads(perks_json.read_text(encoding="utf-8"))["heroes"]
        self.entries, vecs = [], []
        for slug, h in data.items():
            for e in h["perks"]:
                # One template per art version: the wiki icon plus any newer official art
                # (alt_icons). They share the perk's name, so margins are measured against
                # OTHER perks, never between two versions of the same one.
                for icon in [e["icon"]] + [a["icon"] for a in e.get("alt_icons", [])]:
                    alpha = np.asarray(Image.open(ROOT / "assets" / "perks" / icon).convert("RGBA"))[..., 3]
                    v = _normalise_glyph(alpha.astype(np.float32) / 255)
                    if v is None:
                        continue
                    self.entries.append({"hero": slug, "role": heroes.get(slug, {}).get("role"), "name": e["name"],
                                         "tier": e["tier"], "tier_swapped": e["tier_swapped"],
                                         "tiers_ever": _tiers_ever(e), "patch_era": e["patch_era"], "icon": icon})
                    vecs.append(v)
        self.M = np.stack(vecs)
        self.heroes = [e["hero"] for e in self.entries]
        self.names = [f'{e["hero"]}/{e["name"]}' for e in self.entries]

    def vote(self, v: np.ndarray, role: str | None) -> tuple[str | None, float]:
        """Independent hero vote from one glyph: best perk among heroes of `role`
        (role narrows candidates ~3x); margin is to the best perk of another hero.
        No vote (None) if the glyph resembles nothing in the library."""
        allowed = None if role is None else np.array([e["role"] == role for e in self.entries])
        hero, margin, best = nearest(self.M, self.heroes, v, allowed)
        if float(np.linalg.norm(self.M[best] - v)) > UNKNOWN_GLYPH_DIST:
            return None, 0.0
        return hero, margin

    def candidates(self, v: np.ndarray, hero: str) -> list[tuple[dict, float]]:
        """`hero`'s perks ranked by distance to the glyph, one entry per perk name
        (its best-matching art version)."""
        best: dict[str, tuple[dict, float]] = {}
        for j, e in enumerate(self.entries):
            if e["hero"] != hero:
                continue
            d = float(np.linalg.norm(self.M[j] - v))
            if e["name"] not in best or d < best[e["name"]][1]:
                best[e["name"]] = (e, d)
        return sorted(best.values(), key=lambda c: c[1])


def _tier_pairs(a: dict, b: dict) -> set[tuple[str, str]]:
    """Tier assignments (a, b) under which the two perks are one major + one minor."""
    return {(ta, tb) for ta in a["tiers_ever"] for tb in b["tiers_ever"] if ta != tb}


def resolve_perks(cands: list[list[tuple[dict, float]] | None]) -> list[dict | None]:
    """Choose the perk in each slot from its ranked candidates.

    The glyph decides: only candidates within TIE_DIST of a slot's best match (i.e.
    perks drawn with the same icon, like Lingering/Ravenous Wraith or Phantom
    Step/Uprush) are ever in contention. Among those, the scoreboard's rules pick:
      - two perks are always one major + one minor (in either slot: the game's
        left/right order is not reliable for some heroes);
      - a lone perk is always minor.
    Remaining ties go to the current (live) perk, then the closer match; the losers
    are reported in `ambiguous_with` so a human can review.

    Returns, per slot, None for an empty slot or
    {"entry", "distance", "margin", "ambiguous_with", "tier_at_capture", "note"}
    ("entry" is None when even the best match is beyond UNKNOWN_GLYPH_DIST)."""
    near = []
    for c in cands:
        if c is None:
            near.append(None)
            continue
        near.append([x for x in c if x[1] <= c[0][1] + TIE_DIST])
    present = [i for i, c in enumerate(cands) if c is not None]

    def score(choice):  # lower is better
        return tuple(sum(v) for v in zip(*[(0 if e["patch_era"] == "current" else 1, d) for e, d in choice]))

    picks: dict[int, tuple[dict, float]] = {}
    tier_at: dict[int, str | None] = {}
    viable: dict[int, set[str]] = {}   # tied names the rules could NOT rule out, per slot
    note = None
    if len(present) == 2:
        i, k = present
        combos = [(a, b) for a in near[i] for b in near[k]]
        legal = [(a, b) for a, b in combos if _tier_pairs(a[0], b[0])]
        a, b = min(legal or combos, key=lambda ab: score(ab))
        picks[i], picks[k] = a, b
        viable[i] = {x[0]["name"] for x, _ in (legal or combos)}
        viable[k] = {y[0]["name"] for _, y in (legal or combos)}
        pairs = _tier_pairs(a[0], b[0])
        if len(pairs) == 1:   # e.g. Locked On (has been both) + Lift Off (only ever major)
            (tier_at[i], tier_at[k]), = pairs
        else:                 # both perks have held both tiers: the screenshot can't tell
            tier_at[i] = tier_at[k] = None
        if not pairs:
            note = (f"{a[0]['name']} and {b[0]['name']} were both only ever {sorted(a[0]['tiers_ever'])[0]}; "
                    "a two-perk row must hold one major and one minor, so perks.json's tier history is incomplete")
    elif len(present) == 1:
        i = present[0]
        minor = [x for x in near[i] if "minor" in x[0]["tiers_ever"]]
        picks[i] = min(minor or near[i], key=lambda x: score([x]))
        viable[i] = {x[0]["name"] for x in (minor or near[i])}
        tier_at[i] = "minor"
        if not minor:
            note = (f"{picks[i][0]['name']} is a lone perk, which is always minor, but perks.json never records it "
                    "as minor, so its tier history is incomplete")

    out = []
    for s, c in enumerate(cands):
        if c is None:
            out.append(None)
            continue
        e, d = picks[s]
        rest = [x[1] for x in c if x[0]["name"] != e["name"] and x not in near[s]]
        out.append({
            "entry": e if d <= UNKNOWN_GLYPH_DIST else None,
            "distance": d,
            "margin": max((rest[0] - d) if rest else float("inf"), 0.0),
            "ambiguous_with": sorted(viable.get(s, set()) - {e["name"]}),
            "tier_at_capture": tier_at.get(s),
            "note": note,
        })
    return out
