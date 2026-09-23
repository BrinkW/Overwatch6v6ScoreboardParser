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


class PortraitLibrary:
    """Every hero with an assets/heroes/<slug>.png: grows automatically with the reference sync."""

    def __init__(self, heroes_dir: Path = ROOT / "assets" / "heroes"):
        files = sorted(p for p in heroes_dir.iterdir() if p.suffix.lower() == ".png")
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


class PerkLibrary:
    """Every perk with an icon in reference/perks.json (live and legacy)."""

    def __init__(self, perks_json: Path = ROOT / "reference" / "perks.json",
                 heroes_json: Path = ROOT / "reference" / "heroes.json"):
        heroes = json.loads(heroes_json.read_text(encoding="utf-8"))["heroes"]
        data = json.loads(perks_json.read_text(encoding="utf-8"))["heroes"]
        self.entries, vecs = [], []
        for slug, h in data.items():
            for e in h["perks"]:
                alpha = np.asarray(Image.open(ROOT / "assets" / "perks" / e["icon"]).convert("RGBA"))[..., 3]
                v = _normalise_glyph(alpha.astype(np.float32) / 255)
                if v is None:
                    continue
                self.entries.append({"hero": slug, "role": heroes.get(slug, {}).get("role"), "name": e["name"],
                                     "tier": e["tier"], "tier_swapped": e["tier_swapped"],
                                     "patch_era": e["patch_era"]})
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

    def identify(self, v: np.ndarray, hero: str) -> tuple[dict | None, float, float]:
        """(perk, margin, distance): the perk chosen among `hero`'s perks only; margin
        to that hero's runner-up. perk is None when even the best match is further
        than UNKNOWN_GLYPH_DIST: the game has drawn an icon we have no artwork for."""
        allowed = np.array([h == hero for h in self.heroes])
        if not allowed.any():
            return None, 0.0, float("inf")
        _, margin, best = nearest(self.M, self.names, v, allowed)
        dist = float(np.linalg.norm(self.M[best] - v))
        return (self.entries[best] if dist <= UNKNOWN_GLYPH_DIST else None), margin, dist
