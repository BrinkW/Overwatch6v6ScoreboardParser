# Overwatch6v6ScoreboardParser

Reads an Overwatch 2 end-of-match scoreboard screenshot and returns structured
match data:
- **each player:** hero, role, both perks, name, title, and E / A / D / DMG / H / MIT;
- **the match:** mode, map, match length, hero bans and rank range.

It's a personal proof of concept, not a maintained tool. The code is here to
show the approach.

## The approach

A scoreboard is a fixed-layout UI drawn from closed sets, not a natural image,
so almost nothing here is general OCR or machine learning:

- **Layout:** found by anchoring. Every region is located relative to what it is
  attached to: the header bar, the stat column labels, the ban icon, the match
  timer. Both versions of the scoreboard UI are handled by one code path.
- **Recognition:** nearest-neighbour template matching against known sets:
  - stat digits and name letters;
  - role icons;
  - hero portraits and perk icons (reference art synced from the wiki);
  - bans, rank emblems, maps and titles.
- **Confidence:** every field carries a *margin*, the gap between the best and
  runner-up match. Low margins are flagged for review instead of being guessed.
- **Structural checks** catch misreads, e.g. more than two tanks on a team.

Method details and measurements are in [docs/extraction-notes.md](docs/extraction-notes.md).
Hero and perk identification is in
[docs/hero-and-perk-identification.md](docs/hero-and-perk-identification.md).

## Try it on the 14 test screenshots

Requires Python 3.10+.

```
pip install -r requirements.txt

python -m src.parse sample_screenshots/maintest.png   # parse one screenshot (JSON to stdout)
python tools/evaluate.py --public                     # score all 14 against their answer keys
pytest                                                # the test suite
```

`sample_screenshots/` holds 14 real scoreboards: 13 in the classic UI and one in
the newer UI. `tests/fixtures/` holds a hand-verified answer key for each.
`tools/evaluate.py` scores the parser **leave-one-image-out**: every template
used on a screenshot was learned from the *other* 13, so the result reflects
screenshots the parser hasn't seen.

| Field | Correct (leave-one-image-out) |
|---|---|
| E, A, D, DMG, H, MIT | 1008/1008 |
| Role, hero | 335/335 |
| Perks (incl. empty slots) | 336/336 |
| Mode, map, bans, rank range | 112/112 |
| Match time | 13/14 |
| Player name | 160/168 |
| Title | 163/168 |
| **All fields** | **2127/2141 (99.35%)** |

The remaining misses are cases that can't be learned from the other 13
screenshots. Examples: a digit that appears in only one match time, a player
name in a font no other screenshot uses, a title no other screenshot shows.
Each of them is flagged for review. With every screenshot available
(`--in-sample`), all 2141 fields are correct.

## Templates: public and local

The learned templates are built from answer keys by `tools/build_templates.py`:

| Folder | Built from | In git |
|---|---|---|
| `reference/templates/` | the 14 committed answer keys (`--public`) | yes |
| `data/templates/` | those plus captures reviewed locally | no |

The parser uses `data/templates/` when it exists, and the committed set
otherwise. A fresh clone runs on the public set.

```
python tools/build_templates.py --public   # rebuilds exactly the committed templates
```

## Growing the data (optional)

`tools/review.py` turns new screenshots into answer keys:
1. The parser drafts every field of each capture.
2. A local browser page shows the flagged fields next to their crops, and you
   correct only what's wrong.
3. Accepted captures and keys go to the git-ignored `data/` folder.

Then `python tools/build_templates.py` rebuilds the local templates. See
[docs/reviewing.md](docs/reviewing.md).

## Keeping reference data current

Heroes, perks and perk tiers change every patch. After each patch or season
launch, run:

```
python tools/sync_reference.py            # report what's new or changed
python tools/sync_reference.py --apply    # download icons + update reference/*.json
```

See [docs/reference-sync.md](docs/reference-sync.md) for what it checks and how
to read the report.

## Repository layout

| Path | What |
|---|---|
| `src/` | the parser: `layout.py` (regions), `digits.py`, `icons.py` (roles, portraits, perks), `text.py` (names, titles), `header.py` (bans, rank, map, time), `parse.py` (CLI) |
| `tools/` | template building, evaluation, reference sync, review tool |
| `reference/` | heroes, perks, maps and titles (synced), and the public templates |
| `assets/` | hero portraits, ban art, perk icons and rank emblems used as references |
| `sample_screenshots/`, `tests/fixtures/` | the 14 test screenshots and their answer keys |
| `docs/` | measurements, method notes and tool guides |
