"""
Parse an Overwatch end-of-match scoreboard screenshot.

    python -m src.parse <image> [--debug]

Output follows the answer-key fixture schema (tests/fixtures/*.json), plus a
`margin` per recognised field and a `review` list of low-margin fields: the
margin between best and runner-up template is the confidence signal the rest
of the system routes on (CLAUDE.md).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from . import digits as D
from . import header as HD
from . import icons as I
from . import layout as L
from . import text as T

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "reference" / "templates"
STATS = ["E", "A", "D", "DMG", "H", "MIT"]
# Below these margins a field is listed under `review`.
REVIEW_MARGIN = {"stat": 0.5, "role": 1.0, "portrait": 1.5, "perk": 0.05, "name": 0.5}
PERK_VOTE_CONFIDENT = 0.1   # a perk's hero vote only counts as disagreement above this margin


class Models:
    """Templates learned from the answer keys (tools/build_templates.py) plus
    libraries built from the synced reference assets (portraits, perks, bans)."""

    def __init__(self, digit_clf: D.DigitClassifier, role_clf: I.RoleClassifier,
                 portraits: I.PortraitLibrary | None = None, perks: I.PerkLibrary | None = None,
                 header: HD.HeaderModels | None = None, text: T.TextModels | None = None):
        self.digits, self.roles, self.header, self.text = digit_clf, role_clf, header, text
        self.portraits = portraits or I.PortraitLibrary()
        self.perks = perks or I.PerkLibrary()
        self.roster = json.loads((ROOT / "reference" / "heroes.json").read_text(encoding="utf-8"))["heroes"]

    @classmethod
    def load(cls, folder: Path = TEMPLATES) -> "Models":
        return cls(D.DigitClassifier.load(folder / "digits.npz"), I.RoleClassifier.load(folder / "roles.npz"),
                   header=HD.HeaderModels.load(folder), text=T.TextModels.load(folder))


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
        if models.text is not None:
            read_text(rgb, r, out, models, lay.scale, review)
        rows.append(out)
    header = {"mode": None, "map": None, "time": None, "bans": [None] * 4, "rank_range": [None, None]}
    header_margin, header_problems = {}, []
    if models.header is not None:
        header, header_margin, header_review, header_problems = HD.read_header(rgb, lay, models.header)
        review += header_review
    return {
        "image": Path(image).name if not isinstance(image, np.ndarray) else None,
        "layout": {"x0": lay.x0, "y0": lay.y0, "scale": round(lay.scale, 3),
                   "rows": {t: sum(1 for r in lay.rows if r.team == t) for t in ("top", "bottom")}},
        "header": header,
        "header_margin": header_margin,
        "rows": rows,
        "review": review,
        "problems": problems(rows) + header_problems,
    }


def identify_hero(rgb, r: L.Row, out: dict, models: Models, review: list):
    """Role, hero and perks for one row. See docs/hero-and-perk-identification.md.

    1. Role icon -> role.
    2. Portrait -> hero candidate with a margin (skin-invariant on the scoreboard).
    3. Each perk slot: empty (no white disc) -> "none"; otherwise the glyph gives an
       independent hero vote, restricted to heroes of the detected role.
    4. Decide: a confident portrait wins; otherwise agreeing perk votes decide;
       otherwise fall back to the portrait. Any conflict is flagged, never hidden.
    5. With the hero fixed, each glyph is identified among that hero's perks only;
       ties between identically drawn perks are settled by the tier rules
       (two perks = one major + one minor; a lone perk = minor).
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

    # Scoreboard rules: two perks are always one major + one minor (their left/right
    # order is NOT reliable), and a lone perk is always minor. The glyph decides each
    # perk; the rules only settle ties between perks drawn with the same icon.
    resolved = I.resolve_perks([None if v is None else models.perks.candidates(v, hero) for v in glyphs])
    perks, detail, notes = [], [], []
    for res in resolved:
        if res is None:
            perks.append("none")
            detail.append(None)
            continue
        e = res["entry"]
        perks.append(e["name"] if e else None)   # None = a perk is there but unrecognised
        detail.append({
            "name": e["name"] if e else None, **({} if e else {"unrecognised": True}),
            "tier_at_capture": res["tier_at_capture"], "tier_now": e["tier"] if e else None,
            "tier_swapped": e["tier_swapped"] if e else None, "patch_era": e["patch_era"] if e else None,
            "margin": round(min(res["margin"], 99.0), 3), "distance": round(res["distance"], 3),
            "ambiguous_with": res["ambiguous_with"]})
        if res["note"] and res["note"] not in notes:
            notes.append(f"{hero}: {res['note']}")
        if e is None or res["margin"] < REVIEW_MARGIN["perk"] or res["ambiguous_with"]:
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


def read_text(rgb, r: L.Row, out: dict, models: Models, scale: float, review: list):
    """Player name and title (src/text.py)."""
    where = f"{r.team}{r.index}"
    name, title = T.read_text(rgb, r, models.text, scale)
    out["player"], out["title"] = name["name"], title["title"]
    out["margin"]["name"] = round(min(name["margin"], 99.0), 3)
    out["text_evidence"] = {"name": {k: v for k, v in name.items() if k not in ("name", "band", "margin")},
                            "title": {k: v for k, v in title.items() if k != "title"}}
    if name["name"] is None or name["margin"] < REVIEW_MARGIN["name"] or "note" in name or name.get("snapped"):
        review.append(f"{where}.player")
    if title["title"] is not None:
        out["margin"]["title"] = title.get("margin", 0.0)
        sim = title.get("similarity", 0.0)
        # an exact read of a listed title needs no margin; a close read does
        if sim < T.TITLE_SIMILARITY or (sim < 1.0 and title.get("margin", 0.0) < T.TITLE_MARGIN):
            review.append(f"{where}.title")


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
