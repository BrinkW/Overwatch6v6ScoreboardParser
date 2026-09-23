"""
Parse an Overwatch end-of-match scoreboard screenshot.

    python -m src.parse <image> [--debug]

Output follows the answer-key fixture schema (tests/fixtures/*.json), plus a
`margin` per recognised field and a `review` list of low-margin fields: the
margin between best and runner-up template is the confidence signal the rest
of the system routes on (CLAUDE.md).

Fields not implemented yet are returned as None.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from . import digits as D
from . import icons as I
from . import layout as L

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "reference" / "templates"
STATS = ["E", "A", "D", "DMG", "H", "MIT"]
# Below these margins a field is listed under `review`.
REVIEW_MARGIN = {"stat": 0.5, "role": 1.0, "portrait": 1.5, "perk": 0.05}
PERK_VOTE_CONFIDENT = 0.1   # a perk's hero vote only counts as disagreement above this margin


class Models:
    """Templates learned from the answer keys (digits, roles; tools/build_templates.py)
    plus libraries built from the synced reference assets (portraits, perks)."""

    def __init__(self, digit_clf: D.DigitClassifier, role_clf: I.RoleClassifier,
                 portraits: I.PortraitLibrary | None = None, perks: I.PerkLibrary | None = None):
        self.digits, self.roles = digit_clf, role_clf
        self.portraits = portraits or I.PortraitLibrary()
        self.perks = perks or I.PerkLibrary()
        self.roster = json.loads((ROOT / "reference" / "heroes.json").read_text(encoding="utf-8"))["heroes"]

    @classmethod
    def load(cls, folder: Path = TEMPLATES) -> "Models":
        return cls(D.DigitClassifier.load(folder / "digits.npz"), I.RoleClassifier.load(folder / "roles.npz"))


def load_rgb(path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"))


def parse(image, models: Models | None = None) -> dict:
    rgb = load_rgb(image) if not isinstance(image, np.ndarray) else image
    models = models or Models.load()
    lay = L.detect(rgb)
    rows, review = [], []
    for r in lay.rows:
        out = {"team": r.team, "player": None, "title": None, "hero": None, "role": None,
               "perks": [None, None], "margin": {}}
        where = f"{r.team}{r.index}"
        for c in STATS:
            value, margin = models.digits.read(L.crop(rgb, r.rois[c]))
            out[c] = value
            out["margin"][c] = round(margin, 3)
            if value is None or margin < REVIEW_MARGIN["stat"]:
                review.append(f"{where}.{c}")
        identify_hero(rgb, r, out, models, review)
        rows.append(out)
    return {
        "image": Path(image).name if not isinstance(image, np.ndarray) else None,
        "layout": {"x0": lay.x0, "y0": lay.y0, "scale": round(lay.scale, 3),
                   "rows": {t: sum(1 for r in lay.rows if r.team == t) for t in ("top", "bottom")}},
        "header": {"mode": None, "map": None, "time": None, "bans": [None] * 4, "rank_range": [None, None]},
        "rows": rows,
        "review": review,
        "problems": problems(rows),
    }


def identify_hero(rgb, r: L.Row, out: dict, models: Models, review: list):
    """Role, hero and perks for one row. See docs/hero-and-perk-identification.md.

    1. Role icon -> role.
    2. Portrait -> hero candidate with a margin (skin-invariant on the scoreboard).
    3. Each perk slot: empty (no white disc) -> "none"; otherwise the glyph gives an
       independent hero vote, restricted to heroes of the detected role.
    4. Decide: a confident portrait wins; otherwise agreeing perk votes decide;
       otherwise fall back to the portrait. Any conflict is flagged, never hidden.
    5. With the hero fixed, each glyph is identified among that hero's perks only.
    """
    where = f"{r.team}{r.index}"
    role, role_m = models.roles.classify(L.crop(rgb, r.rois["role"]))
    p_hero, p_m = models.portraits.classify(L.crop(rgb, r.rois["portrait"]))

    glyphs, votes = [], []
    for slot in ("perk_left", "perk_right"):
        crop = L.crop(rgb, r.rois[slot])
        v = None if I.slot_is_empty(crop) else I.perk_glyph_descriptor(crop)
        glyphs.append(v)
        votes.append(models.perks.vote(v, role) if v is not None else None)

    confident_votes = {h for h, m in filter(None, votes) if h is not None and m >= PERK_VOTE_CONFIDENT}
    if p_m >= REVIEW_MARGIN["portrait"] or not confident_votes:
        hero, decided_by = p_hero, "portrait"
    elif len(confident_votes) == 1:
        hero, decided_by = next(iter(confident_votes)), "perks"
    else:
        hero, decided_by = p_hero, "portrait (perks split)"

    flags = []
    if confident_votes - {hero}:
        flags.append(f"perk glyph points to {sorted(confident_votes - {hero})}, not {hero}")
    if p_hero != hero:
        flags.append(f"portrait says {p_hero} (margin {p_m:.2f})")
    roster_role = models.roster.get(hero, {}).get("role")
    if roster_role and roster_role != role:
        flags.append(f"role icon {role} but {hero} is {roster_role} in heroes.json")

    # Hard rule of the scoreboard: with two perks, left = major and right = minor;
    # a lone perk is always minor. So each slot proves the perk's tier AT CAPTURE time.
    two = all(v is not None for v in glyphs)
    slot_tiers = ["major" if two else "minor", "minor"]
    perks, detail, notes = [], [], []
    for v, slot_tier in zip(glyphs, slot_tiers):
        if v is None:
            perks.append("none")
            detail.append(None)
            continue
        entry, m, dist = models.perks.identify(v, hero, slot_tier)
        perks.append(entry["name"] if entry else None)   # None = a perk is there but unrecognised
        if entry is None:
            detail.append({"name": None, "unrecognised": True, "tier_at_capture": slot_tier,
                           "distance": round(dist, 3)})
        else:
            detail.append({"name": entry["name"], "tier_at_capture": slot_tier, "tier_now": entry["tier"],
                           "tier_swapped": entry["tier_swapped"], "patch_era": entry["patch_era"],
                           "margin": round(m, 3), "distance": round(dist, 3)})
            if slot_tier not in entry["tiers_ever"]:
                # The glyph is unambiguous and the slot rule is hard, so it is our tier
                # history that is incomplete (a patch the wiki change log doesn't record).
                notes.append(f"{hero} / {entry['name']} was {slot_tier} when captured; "
                             f"perks.json has no record of it ever being {slot_tier}")
        if entry is None or m < REVIEW_MARGIN["perk"]:
            review.append(f"{where}.perk")

    out.update(role=role, hero=hero, perks=perks, perk_detail=detail)
    out["margin"].update(role=round(role_m, 3), portrait=round(p_m, 3))
    out["hero_evidence"] = {"decided_by": decided_by, "portrait": [p_hero, round(p_m, 3)],
                            "perk_votes": [None if x is None else [x[0], round(x[1], 3)] for x in votes],
                            "role_icon": role}
    out["hero_flags"] = flags
    out["reference_notes"] = notes
    if role_m < REVIEW_MARGIN["role"]:
        review.append(f"{where}.role")
    if p_m < REVIEW_MARGIN["portrait"] or flags:
        review.append(f"{where}.hero")


def problems(rows: list[dict]) -> list[str]:
    """Structural invariants (see CLAUDE.md "Always validate structurally")."""
    out = []
    if len(rows) not in (10, 12):
        out.append(f"expected 10 (5v5) or 12 (6v6) rows, got {len(rows)}")
    # The only role limit is at most two tanks per team; any damage/support mix is legal.
    for team in ("top", "bottom"):
        tanks = sum(1 for r in rows if r["team"] == team and r["role"] == "tank")
        if tanks > 2:
            out.append(f"{team} team has {tanks} tanks; at most 2 are allowed")
    for r in rows:
        where = f"{r['team']} row {rows.index(r)}"
        if (r["D"] or 0) > 60 or (r["E"] or 0) > 120:
            out.append(f"{where}: implausible E/D ({r['E']}/{r['D']}), likely a digit merge")
        if any(r[c] is None for c in STATS):
            out.append(f"{where}: unreadable stat cell")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Parse a scoreboard screenshot.")
    ap.add_argument("image")
    ap.add_argument("--debug", action="store_true", help="also write debug/<image>_layout.png")
    args = ap.parse_args(argv)
    result = parse(args.image)
    if args.debug:
        (ROOT / "debug").mkdir(exist_ok=True)
        rgb = load_rgb(args.image)
        L.draw(rgb, L.detect(rgb)).save(ROOT / "debug" / (Path(args.image).name.replace(".", "_") + "_layout.png"))
    json.dump(result, sys.stdout, indent=1, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
