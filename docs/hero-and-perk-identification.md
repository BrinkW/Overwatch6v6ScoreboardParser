# Hero and perk identification

How the parser decides each player's **role**, **hero** and **two perks**. The
code is `src/icons.py` (the matchers) and `identify_hero()` in `src/parse.py`
(the decision). The measured numbers are from the 13 reviewed screenshots
(156 player rows).

## Inputs per row

`src/layout.py` finds four small regions in every player row, positioned from
the column-header bar, so they work at any resolution:

| Region | What's in it |
|---|---|
| `role` | the tank / damage / support icon at the far left |
| `portrait` | the hero picture |
| `perk_left`, `perk_right` | two white discs with a black glyph, or nothing if no perk was picked |

Everything below is **nearest-neighbour template matching**: turn the crop into a
fixed-size vector, find the closest reference vector, and report a **margin**.
The margin is the distance to the closest reference with a *different* answer,
minus the distance to the best one. A big margin means an unambiguous match. A
small one sends the field to the `review` list.

## Step 1: role icon

- **Descriptor:** keep only white pixels (the minimum RGB channel, which ignores
  every team colour), then shrink to 16×16.
- **References:** role crops harvested from the answer keys, whose roles are
  known, stored in `reference/templates/roles.npz` by `tools/build_templates.py`.
- **Result:** 156/156, even when each screenshot is judged only by templates from
  the others. The smallest margin is 4.6, a very safe gap.

## Step 2: portrait → candidate hero

- **Key fact:** the scoreboard shows each hero's **standard illustrated
  portrait, whatever skin the player uses.** So one reference image per hero is
  enough, and that's exactly what `assets/heroes/<slug>.png` is.
- **Descriptor:** centre square of the portrait, shrunk to 24×24 colour, then
  normalised to zero mean and unit variance, so brightness and contrast don't
  matter.
- **References:** every `assets/heroes/*.png`. New heroes arrive automatically
  through the reference sync; nothing needs training.
- **Result:** 156/156, including skinned rows. The smallest margin is 2.7 and the
  median 16.6. The ban-screen 3D art only gets 48/156, so it isn't used.

## Step 3: perk slots

For each of the two slots:

1. **Empty?** A perk sits on a white disc. If less than a quarter of the central
   area is white, the slot is empty and the perk is `"none"` (20/20 correct).
2. **Glyph descriptor:** take the dark pixels inside 0.72 of the disc radius
   (outside it is the grey ring). Crop to the glyph's tight bounding box, pad to
   square, shrink to 24×24, blur slightly, and scale to unit length. The 128 px
   wiki artwork goes through the identical steps using its alpha channel. That's
   why the ~28 px in-game glyph and the artwork can be compared with no scale
   calibration.
3. **Independent hero vote:** compare the glyph with *every* perk (live and
   legacy) belonging to heroes of the role found in step 1. A perk can have
   several art versions: the wiki's icon plus any newer official art from
   Blizzard's hero page (`alt_icons` in `perks.json`, e.g. Baptiste's redrawn
   Automated Healing). Every version is a template for the same perk. The hero
   owning the best match gets the vote, with a margin measured against the best
   perk of any *other* hero. With no help from the portrait, this alone names
   the right hero for 290/292 glyphs.
   - If the glyph is further than 0.65 from everything, it casts no vote.

## Step 4: decide the hero

```
if portrait margin ≥ 1.5 or no perk cast a confident vote:
    hero = portrait's answer                 ("decided_by": "portrait")
elif the confident perk votes all name one hero:
    hero = that hero                         ("decided_by": "perks")
else:
    hero = portrait's answer                 ("decided_by": "portrait (perks split)")
```

A perk vote is "confident" when its margin is ≥ 0.1. The decision is never
silent about conflicts. `hero_flags` lists every disagreement:
- a confident perk vote for a different hero;
- a portrait that lost the decision;
- a role icon that doesn't match the hero's role in `reference/heroes.json`
  (expect this for Sombra around her Season 5 move from damage to support).

On the reviewed screenshots there are **zero flags**: all three signals agree
on every row.

## Step 5: identify the perks

With the hero fixed, each glyph is matched again, this time **only against that
hero's perks** (current and legacy, every art version). Restricting it to a
handful of candidates removes cross-hero look-alikes (e.g. Kiriko's Fortune
Teller vs Freja's Tracking Instinct).

**The slot proves the tier.** This is a hard rule of the scoreboard: with two
perks, left = major and right = minor; a lone perk is minor. So each perk's
tier *at capture time* is known from its slot, even when the perk has since
changed tier. The parser uses that in two ways:

- **Tie-break between perks that share an icon.** Reaper's Lingering Wraith
  (minor, added Season 19) reuses the icon of Ravenous Wraith (major, removed in
  the same patch), and Moira's Phantom Step shares Uprush's. When matches are
  within 0.01 of each other, the one that has held the slot's tier wins.
  Example: a Wraith icon in the left slot is Ravenous Wraith, from a pre-Season-19
  capture.
- **Reference-data check.** A clear glyph in a slot whose tier `perks.json` has
  never recorded for that perk means our tier history is incomplete (a patch the
  wiki change log doesn't mention). The perk is *not* re-labelled; the row gets a
  `reference_notes` entry, and `python tools/evaluate.py --tier-gaps` totals them.

The reported perk has:

- `name`, `tier_at_capture` (from the slot), `tier_now`, `tier_swapped` and
  `patch_era` (from `reference/perks.json`);
- `margin` against that hero's runner-up perk, and `distance` to the chosen art.

If the best match is further than **0.65**, the perk is reported as
**unrecognised** (`None`) rather than forced onto the wrong name. On the reviewed
screenshots, correct matches sit at distance ≤ 0.59 (median 0.16). Result:
292/292 perks identified, including Baptiste's redrawn Automated Healing, which
only matched once the official art was added.

## Output per row

Actual output for test1.png, top row 1 (MUFFIN):

```json
"role": "support", "hero": "juno",
"perks": ["Locked On", "Lift Off"],
"perk_detail": [{"name": "Locked On", "tier_at_capture": "major", "tier_now": "minor",
                 "tier_swapped": true, "patch_era": "current", "margin": 0.605, "distance": 0.199},
                {"name": "Lift Off", "tier_at_capture": "minor", "tier_now": "major",
                 "tier_swapped": false, "patch_era": "current", "margin": 0.638, "distance": 0.118}],
"hero_evidence": {"decided_by": "portrait", "portrait": ["juno", 25.917],
                  "perk_votes": [["juno", 0.45], ["juno", 0.53]], "role_icon": "support"},
"hero_flags": [],
"reference_notes": ["juno / Lift Off was minor when captured; perks.json has no record of it ever being minor"],
"margin": {"role": 4.49, "portrait": 25.917, ...}
```

This capture predates the Season 18 change that made Locked On minor, which the
tier history knows about. It also shows Lift Off as minor at that time, which the
wiki's change log never recorded, hence the reference note.

Fields below their review margin (role < 1.0, portrait < 1.5, perk < 0.05, any
flag, any unrecognised perk) are listed in the result's `review` array.

## Structural check

The only role limit is **at most two tanks per team**. Any damage/support mix
is legal, so a third tank is flagged in `problems` and nothing else is.

## Known limits and next steps

- **Art the game redraws after the official site updates** would still yield
  "unrecognised" until the next reference sync picks it up (the sync compares
  every live perk with Blizzard's hero pages and adds new art automatically).
  Legacy perks aren't on the official site, so a redrawn legacy icon would need
  a confirmed in-game crop added as a template.
- **A brand-new hero** is recognised only after the reference sync fetches their
  portrait and perk icons. Until then the portrait matches the closest existing
  hero with a low margin and is flagged for review.
- **Perk-name answer keys** were produced by this matcher, checked by eye, and
  reviewed by the user (corrections: Baptiste's Automated Healing; Ravenous
  Wraith follows from the slot rule). Regenerate the review sheets with
  `python tools/perk_review.py`.
- **Tier history gaps** (`--tier-gaps`) are candidates for manual additions to
  `perks.json`'s `tier_history` once their patch dates are known.
