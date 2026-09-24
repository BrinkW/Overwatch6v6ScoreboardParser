# Extraction notes

Measured constants and method details. Derived from analysis of a 2560×1440
6v6 King's Row scoreboard capture, September 2026. `CLAUDE.md` holds the
summary; this file holds the numbers and the reasoning.

---

## 1. Chroma-based text isolation

Scoreboard text is near-white with a dark outline. Backgrounds are strongly
team-tinted, plus a blue prestige nameplate for some players (a chrome badge
with a yellow Roman numeral at its left end, sometimes with white or orange light
streaks). Chroma (`max(RGB) − min(RGB)`) separates them cleanly where luminance
does not.

| Region | Background median chroma | Text median chroma | Text pixel share |
|---|---|---|---|
| `EVE` — flat blue | 103 | 6 | 4.6% |
| `KIMIKO` — flat red | 87 | 6 | 8.6% |
| `HAIPYDRAGON` — banner over blue | 56 | 6 | 19.2% |
| `PALEWHISPER` — banner over red | 64 | 6 | 13.5% |
| `FIA` — name + subtitle, red | 84 | 10 | 4.6% |

Thresholds: `chroma < 45`, `value > 150`. The value test is what rejects the
dark glyph outline and drop shadow; without it you get hollow letterforms.

The banner rows show a higher text-pixel share because the prestige badge
(Roman numeral in a chrome frame) is also achromatic and survives the mask. It
sits left of the name at a predictable offset — crop it out by ROI or drop
components whose bounding box falls left of the name's x-origin.

**Subtitle/title line.** Rendered in a desaturated blue-grey (`#8fa8c4`-ish),
*not* white. Chroma ≈ 25–35, so it passes a `< 45` threshold but sits close to
the boundary. If you tighten chroma for the name line, use a separate looser
threshold for the title ROI, or key on the title's distinct hue instead.

---

## 2. Recognition strategy per field

### Digits (E / A / D / DMG / H / MIT)
Column-projection segmentation after the chroma mask. The display font has no
touching glyph pairs at this size, so projection beats connected components for
simplicity and robustness. Normalize each glyph: tight bbox → pad to square →
resize to 16×16. The font is italic ~12°; shear-correct before resizing and
template distances tighten noticeably.

12 classes: `0-9`, comma, blank. A 3-layer CNN or plain NN over ~50 templates per
class reaches ~99.9%. Runs in microseconds and cannot hallucinate `5` → `S`.

Watch for: comma thousands separators, right alignment, and `0` rendered in
columns that don't apply to the role (e.g. MIT for supports).

### Player names and titles (`src/text.py`)
Both lines are segmented into **connected components**, not column
projections. The italic display font's glyphs overlap in x, so projection
splits only 101 of 150 names correctly (133 with width splitting). But the
glyphs never touch, so components split all 150.

**Names, display font** (italic, condensed, A–Z 0–9):
- Mask `min(RGB) > 190`; the blue-grey title (~145) and the nameplate stay below.
- The name's glyphs are the components sharing the most common full-height
  extent: every uppercase glyph and digit spans the cap height (21–26 px at
  native size). That also drops the prestige badge, its yellow numeral and the
  nameplate streaks, which have other extents.
- The line's height moves: it sits higher when the row has a title.
- Each glyph is scaled to 24 rows (aspect kept, centred in 20 columns) and
  blurred by 1 px, then read by NN over templates from the answer keys.
- Result, leave-one-image-out: 146/150, with 2 glyph errors in about 1000:
  - O↔D ×2 (margin 0.2, flagged);
  - a lone 5 and a lone 8, which can't be learned leave-one-out.
- Q, 6 and 7 have never appeared in a name. A glyph further than 2.5 from
  every template (correct glyphs: ≤ 2.33) is flagged as an unseen character.
- **Known-player snap.** A read one glyph away from a known player (any name in
  the answer keys) is snapped to that player, and flagged, only if that glyph
  is a near tie: its distance to the player's letter is within 1.0 of the
  letter it was read as. This fixes both O/D misreads once the player is
  known. Near-duplicate players are real (RUDO vs RUDOLPH), so a confident read
  is never overridden.

**Names, fallback font** (any character outside A–Z 0–9: `SPEEDSPORT!`,
`BLACK!`, `바람`, `ʃR̂ƐƐĿǾ`, `DĔXŦER□`; 6 of 156 rows):
- Detected by slant: display-font glyphs lean 0.20–0.25 (x per y), the fallback
  font about 0. The display-font glyph distance agrees: 7.9 or more vs at most 1.5.
- No OCR: no engine reads `ʃR̂ƐƐĿǾ`, and players recur. The whole name image is
  matched against the known fallback-font players:
  - a soft mask, because the 1-px strokes make a hard mask shift with
    sub-pixel alignment;
  - cropped to its ink, scaled to 24 rows, blurred 1.5 px;
  - NN allowing a 1-px shift.
- The two SPEEDSPORT! crops are 5.0 apart; different names 14.7 or more; the
  match cutoff is 9.0. Only one repeat pair exists so far, so this cutoff is
  thinly validated.
- An unknown fallback-font name is `None` and flagged. Once it is in an answer
  key, it is recognised.

**Titles** (mixed-case bold sans, blue-grey, below the name):
- Mask `min(RGB) > 100` and chroma < 80, in the rows below the name line.
- Nameplate streaks cross this line and touch letters, so every horizontal run
  of 18 px or more is removed first; glyph strokes are shorter.
- Components larger than a glyph are dropped, the baseline is the most common
  component bottom, and i/j dots are merged into their stems.
- A title needs at least 3 glyphs on the baseline; otherwise the row has none.
  All 60 title-less rows read as none.
- Bold letters sometimes touch ("ll", "ss"), so the raw read is imperfect (66
  of 96 split exactly). It is only used to snap to the title list:
  - `reference/titles.json`: the wiki's Titles page plus the competitive reward
    pattern (`<Tier> <Tank|Damage|Support|Open Competitor|Open Challenger>`);
    22 of our 55 titles are not on the wiki;
  - plus every title in the answer keys (`known_text.json`).
- Snapping uses difflib with case, spaces and lookalikes (0/O, 1/I/l, 5/S, 8/B)
  folded. Result, leave-one-image-out: 152/156. All 4 misses are titles found
  only in the screenshot being read and on no list (Mythic Rat, Bottom 500,
  Wraith, Sharpshooter 77), and all 4 are flagged.
- Correct snaps go as low as 0.67 similarity, so there is no clean acceptance
  cutoff. Below 0.8 similarity, or a margin under 0.1 on an inexact read, the
  title goes to review.

### Perk and role icons
Pure black glyphs on a white disc, fixed size, no skin variation. The glyph is
~28 px inside a ~52 px disc at 1440p, and thin strokes are lost at that size.
Take soft darkness inside 0.72 × the half-crop (0.62 clipped large glyphs), crop
to the glyph's tight bounding box, resize to 24×24, blur σ=1, then nearest
neighbour. The same normalisation applied to the 128 px wiki artwork makes the
two comparable without any scale calibration. On the 13 reviewed screenshots:
290/292 glyphs name the right hero with no other help; with the hero known,
291/291 recognisable perks are identified; empty slots (no white disc) 20/20.
Role icons: 156/156 with templates harvested from the answer keys.

**The game's art can differ from the wikis'.** Baptiste's Automated Healing (a
turret with healing pluses in game) matched none of his wiki icons: both wikis
still carry the 2025-03 art. Comparing every live perk against Blizzard's
official hero pages (212 perks) found 9 redrawn icons: Automated Healing, Stim
Pack, One-Two, Blade Twisting, Quantum Entanglement and Wuyang's four perks. The
official art is kept alongside the old art as extra templates (`alt_icons` in
`perks.json`); the reference sync repeats this check. Matches further than 0.65
from every artwork are still reported as unrecognised rather than forced
(correct matches sit ≤ 0.59, median 0.16).

**Some perks share one icon.** Reaper's Lingering Wraith (minor, added Season 19)
reuses Ravenous Wraith's icon (major, removed Season 19), and Moira's Phantom
Step shares Uprush's. The one-major-one-minor rule settles the tie when the row's
other perk has only ever held one tier. Otherwise (Reaper with Shadow Blink,
Moira with Ethical Nourishment, both of which have held both tiers) the
screenshot can't tell them apart: the parser prefers the live perk, lists the
alternative in `ambiguous_with`, and flags the row for review. Answer keys
accept either name (`"Phantom Step|Uprush"`).

### Hero portraits
**The scoreboard shows each hero's standard illustrated portrait, whatever skin
is equipped.** Matching the portrait against `assets/heroes/<slug>.png` (24×24
colour, z-scored) identifies 156/156 rows, skinned ones included, with a minimum
margin of 2.7 (median 16.6). The 3D ban-screen art (`assets/bans`) gets only
48/156, so it is the wrong library for rows. The portrait is therefore the
primary hero signal; perk glyphs and the role icon confirm it.

### Role composition
The only role limit is **at most two tanks per team**. Any damage/support mix is
legal; the reviewed screenshots include teams with 3 supports and with 3 damage
heroes. A 2/2/2 check would be wrong here.

---

## 3. Rank emblem fingerprinting

The "MATCH RANK RANGE" emblems can be identified robustly with two cheap
descriptors, both computed against the official badge art. This method was
validated end-to-end and resolved a case where a naive read was wrong.

**Hue histogram.** Mask to `value > 90 and chroma > 35`, bin hue in 10° buckets,
compare top bins. Reference values from official badge art:

| Tier | Dominant hue bins |
|---|---|
| Bronze | 20 |
| Silver | (achromatic) |
| Gold | 38 |
| Platinum | 190, 180, 200 |
| Diamond | 210, 220, 200 |
| Master | 140, 150, 160 |
| Grandmaster | 230, 220, 240 |
| Champion | 270, 280, 260, 290 |
| Top 500 | 52 |

**Silhouette profile.** Sample opaque width at 29 normalised heights, divide by
max width. Compare by RMS. Reference aspect ratios (w/h): Platinum 0.936,
Diamond 1.049, Master 1.244, Champion 1.372, Grandmaster 1.553.

Champion has a distinctive narrow spire above its wings — ~40% of max width for
the top ~8% of height. Grandmaster starts at full width immediately. That single
feature separates the two tiers that are otherwise easiest to confuse, and
missing it is what produced an incorrect first read.

On the analyzed capture: left emblem RMS 0.042 vs Master (next best 0.192);
right emblem RMS 0.119 vs Champion (next best 0.226). Both confirmed
independently by hue. Result: **Master 1 – Champion 4**.

Division numbers (1 = highest, 5 = lowest) are rendered as Arabic numerals in a
winged hexagon below the crest.

### As implemented (`src/header.py`, 13 reviewed screenshots)

**Tier: colour shortlists, shape decides.** References come from the tier sheet
`assets/rankiconsnew.png` (all nine tiers, Emerald included, no glowing disc)
plus in-game emblems harvested from the answer keys. The game renders emblems a
little differently from the sheet; Grandmaster in particular matched its own
sheet art poorly. Each candidate's score = hue gap / 40° + best silhouette RMS.
Only tiers within 30° of the emblem's hue are candidates. Hue and silhouette use
bright *saturated* metal pixels only. Measured hues, sheet → in game: Master
161° → 156–160°, Grandmaster 244° → 239°, Champion 288° → 276–277°. Result:
26/26 in-game emblems (leave-one-image-out), all 45 `assets/ranks` files and
all 9 sheet emblems.

**Emerald vs Master.** They are 7° apart in hue (154° vs 161°), so the silhouette
decides. Emerald is a narrow V (aspect 0.92), Master a wide W (1.24). The 5
in-game Master emblems score 0.07–0.12 as Master vs 0.40–0.47 as Emerald. `tests/test_ranks.py`
guards this. `assets/ranks/emerald1..5.png` are **generated, not official art**
(`tools/make_emerald_ranks.py`): Diamond's layout and badge, the disc rebuilt
from Diamond's radial colour profile and hue-rotated to Emerald's (luminance
kept), and the Emerald emblem from the tier sheet.

**Rank boxes** were widened: the original boxes clipped Grandmaster's wings
(8 of 26 emblems touched the crop edge). The white dash between the two emblems
is colourless, so the saturated-metal mask ignores it.

**Division: match the whole badge, not the digit.** The badge frame (wings,
hexagon) is identical for every rank, so it cancels out and only the digit
differs. It is cropped at a fixed height from the top of the wings. Result:
26/26 (min margin 5.3) against templates from the other screenshots. Cutting the
digit out was worse (25/26 at best): it touches the hexagon frame, and the hexagon
doesn't always close at this size. Templates cut from `assets/ranks` fail too
(2/26): their rendering differs.

### Other header fields

- **Bans:** `assets/bans` art has a transparent background, while the game draws
  it on a red tile, so the art is composited onto red (140, 20, 25) before matching: 51/51.
  Without that, Wuyang was read as Jetpack Cat. An empty
  "no ban" slot is a grey icon (mean saturation 0.5 vs 66–93).
- **Mode | map:** light-grey text left of the orange time. The glyph count is
  always the known text + 1 (the mode icon). Letters are normalised to the
  *line* height, so an apostrophe stays small, and read by NN over templates
  harvested from the answer keys. The read is snapped to `reference/maps.json`
  (37 maps, 7 modes, from the wiki). Letters never seen yet are misread ("OORAOO"
  for Dorado), but snapping absorbs that: 13/13 maps and modes.
- **Match time:** orange italic digits that touch; a shear of 0.2 separates them.
  The tens-of-seconds digit is constrained to 0–5. 13/13 in-sample, 11/13
  leave-one-image-out: 5 and 8 occur only once in the answer-key times.

---

## 4. Known-good ground truth (for pipeline tests)

From the analyzed capture — useful as a first regression fixture.

Map `King's Row`, mode `Hybrid`, match time `21:32`, 6v6, rank range
`Master 1 – Champion 4`. Bans: Zenyatta, Roadhog, Freja, Zarya.

| Team | Player | Hero | Role | E | A | D | DMG | H | MIT |
|---|---|---|---|---|---|---|---|---|---|
| Blue | GETORANGEDON | Tracer | damage | 15 | 0 | 17 | 9,940 | 0 | 0 |
| Blue | EVE | Lúcio | support | 17 | 20 | 9 | 10,733 | 24,430 | 6,821 |
| Blue | HAIPYDRAGON | Wrecking Ball | tank | 21 | 5 | 20 | 15,198 | 0 | 8,977 |
| Blue | JUNKYJUCEBOX | Sombra | damage | 18 | 2 | 16 | 18,372 | 840 | 4,017 |
| Blue | L3RNT | Winston | tank | 23 | 2 | 14 | 18,982 | 0 | 31,385 |
| Blue | SPURGE2 | Moira | support | 16 | 16 | 10 | 16,627 | 22,487 | 0 |
| Red | ONMYB1KE | Sojourn | damage | 65 | 0 | 6 | 27,088 | 0 | 0 |
| Red | DONKEYFARMER | Tracer | damage | 44 | 7 | 12 | 26,094 | 0 | 723 |
| Red | FIA | Kiriko | support | 7 | 64 | 3 | 2,264 | 27,459 | 0 |
| Red | KEXXAAR | Wuyang | support | 33 | 22 | 5 | 9,827 | 23,096 | 408 |
| Red | KIMIKO | D.Va | tank | 45 | 24 | 9 | 20,690 | 0 | 12,443 |
| Red | PALEWHISPER | Orisa | tank | 50 | 7 | 8 | 26,945 | 68 | 37,454 |

Titles present: `Unrelenting Hero` (HAIPYDRAGON), `6v6 Enthusiast` (L3RNT),
`Shinigami` (FIA), `Grandmaster Open Challenger` (KEXXAAR), `MVP` (PALEWHISPER).

Hero identifications are ~75–99% confident (Juno and Sojourn are the weakest);
verify against real ground truth before treating this as a gold fixture.

**Edge case worth keeping:** DONKEYFARMER's row renders **no perk icons at all**
despite 44 eliminations. Verified at the pixel level — the columns are empty,
not dim. Cause unknown (hero swap, disconnect/rejoin, or UI quirk). Your parser
must not assume every row has two perks.

---

## 5. Other UI elements on this screen

- **Ult charge** — circled percentage, friendly team only. Red rows have no such
  column, so don't index columns by absolute position across both teams.
- **Highlighted row** — the local player's row is drawn in a lighter tint and
  carries a microphone icon instead of a speaker icon.
- **Right-hand panel** — per-hero stats for the highlighted player (final blows,
  solo kills, weapon accuracy, critical hit accuracy, ability-specific counters).
  Label/value pairs, large type, dark background, trivially readable. Useful as a
  cross-check on the highlighted row's hero.
- **Play of the match banner** and **chat line** are rendered behind/below the
  table and will contaminate naive full-frame thresholding. ROI-crop first.

---

## 6. Two scoreboard UIs, and special row states

**Classic** (all captures up to test12) and **tabbed** (test13, late 2026: a
"HERO INFO | SCOREBOARD" tab strip). `layout.detect` handles both with one code
path by anchoring every element to what it is attached to. The UI is reported
as `layout.ui`, from the blue SCOREBOARD tab left of the ban icon. Only the
title text size is keyed on it.

| | Classic | Tabbed |
|---|---|---|
| Bar height `h` (from the E..MIT span) | 39 px | 34.1 px |
| Bar left edge | 393 | 448 |
| Bar edge → E column | 14.7h | 15.6h (wider name area) |
| Perk slots | 4.56h left of E | the same |
| Ban icon (red ⊘) | (53, 66), 41 px | (473, 39), 41 px |
| Ban slots | 4 | 4 or 5 |
| Time digits, right edge / top | 2502–2503 / 61 | 2509 / 43 |
| Rank dash (left, top) | (2389, 151) | (2389, 159) |
| Title x-height | 8 px | 10 px |

- **Table:**
  - Left-side ROIs (role, portrait, ult, name start) hang off the measured bar
    edge.
  - Right-side ROIs (perks, name end, stats) hang off the E column. The old code
    derived the bar edge as "E − 14.67h", which is only true in the classic UI.
- **The header is pinned to the screen, not the table,** so it is found by
  content (sizes in px at 1440p, scaled by the found element):
  - **Bans:** the leftmost square red component in the top-left is the ⊘ icon.
    - Slots start 65 px right of it at an 83 px pitch; each box is 70×68 px,
      14 px above the icon's top.
    - A slot exists if ≥ 30% of its 4-px border is frame: red, or grey for a team
      that didn't ban. Real slots score 0.50–0.66, empty positions 0.00.
    - The walk stops at the first empty position, so there are 4 or 5 slots.
  - **Mode, map and time:** the orange digits (32 px tall) at the top right.
    The strip reaches 628 px left of their right edge.
    - The tabbed UI's map text is lavender (chroma ~43), so the "grey" mask
      allows chroma < 60 (the classic text is ~3).
  - **Rank range:** the white dash between the emblems, a solid 23×7 px bar
    (fill 0.94) with emblem metal on both sides. White highlights on the
    emblems are sparse (fill ≤ 0.52).
    - The boxes are fixed offsets from it, which reproduce the classic boxes
      exactly.
- **Title size is set by the UI,** not the table. The tabbed table is 0.875x,
  yet its titles are 1.25x larger (ascenders 14 vs 10–11 px). Pooled
  per-screenshot estimates failed on the JPEG (8 and 10 px tied), so the title
  scale is keyed on `layout.ui`.

**Mystery hero** (a player who has just swapped hero):
- The portrait is a translucent "?" head-and-shoulders silhouette over the
  team colour, with no role icon and no perks. Recorded as `hero: "mystery"`,
  `role: null`.
- **Detection:** min(share of pixels with chroma > 30, concentration of their
  hue) is 0.999 on it, and at most 0.79 on the other 167 portraits
  (threshold 0.93).
- **Role:** a role cell with white share < 0.05 has no icon (every icon scores
  ≥ 0.20; the blank cell 0.0), so the role is null instead of guessed.
- **Asset:** `assets/heroes/mystery.png` is **not** the in-game art, which is on
  neither the wiki nor the official site. It is the "?" figure cut out of the
  wiki's `File:Achievement Mystery Swap.png` (839 px; triangle frame removed),
  for display. The figure has narrower shoulders than the in-game bust.
  Detection doesn't use it, and it is excluded from the portrait library.

**Respawn timers** (dead players, test13 bottom 3–5):
- The portrait is dimmed, with a white-and-red countdown ring (radius 0.275x the
  portrait width) and the seconds inside.
- **Detection:** on a thin circle at that radius, 0.95–0.99 of pixels are
  ring-coloured, against ≤ 0.73 on any other portrait (threshold 0.85).
- The ring covers the face, so the portrait is not trusted:
  - confident perk votes decide the hero, and the row is flagged;
  - masking the ring out made things worse (Mei matched Vendetta with margin
    3.0).

---

## 7. Open questions

- **Settled: perk slot order is not reliable** (confirmed by the user). A row
  with two perks always holds one major and one minor, and a lone perk is minor,
  but left/right order can't be trusted. Most rows show major on the left, but
  certain heroes and perk combinations consistently flip in screenshots from the
  same era: Juno's Locked On + Lift Off (7 of 8 rows), Moira's Ethical
  Nourishment + Reversal, Reaper's Soul Reaving + Shadow Blink, and Sojourn's
  Deceleration Field + Friction Generators. No slot-based tier inference is made.
  Under the one-major-one-minor rule, all 13 reviewed screenshots are consistent
  with `perks.json`'s tier history.
- Whether perk icons are ever rendered at a different size in 5v5 vs 6v6 layouts.
- Whether the tabbed UI appears at other resolutions and UI scales. Its header
  constants are measured at 2560×1440 only; they scale with the found ban icon and
  time digits.
- A real 5-ban capture: 5-slot detection is only tested on a synthetic copy of
  test13.
