# Extraction notes

Measured constants and method details. Derived from analysis of a 2560×1440
6v6 King's Row scoreboard capture, September 2026. `CLAUDE.md` holds the
summary; this file holds the numbers and the reasoning.

---

## 1. Chroma-based text isolation

Scoreboard text is near-white with a dark outline. Backgrounds are strongly
team-tinted, plus an animated pink/red prestige nameplate banner for some
players. Chroma (`max(RGB) − min(RGB)`) separates them cleanly where luminance
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

### Player names
The only genuinely free text. General OCR does poorly on OW2's condensed italic
display font. Two viable routes:

- Fine-tune Tesseract on synthetic renders. You have the font — generate ~50k
  names with the real styling (outline, glow, both team backgrounds, banner
  variants). Cheap and effective.
- Skip Tesseract entirely for PaddleOCR PP-OCRv4 or docTR, which handle
  stylized fonts far better out of the box.

Constrain the decoder charset to BattleTag-legal characters. The discriminator
(`#1234`) is not rendered on the scoreboard.

### Titles, hero names, maps, modes
Closed sets. Recognize, then snap:

```python
from rapidfuzz import process, fuzz
best, score, _ = process.extractOne(raw, KNOWN_TITLES, scorer=fuzz.WRatio)
value = best if score > 80 else None
```

Turns a hard recognition problem into an easy retrieval problem.

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
winged hexagon below the crest — treat as a digit-classifier field.

**Caveat.** Emerald was added between Platinum and Diamond on 2026-08-11 and had
no published badge art at time of writing. A green emblem could be Emerald
rather than Master; disambiguate on silhouette, not hue.

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

## 6. Open questions

- **Settled: perk slot order is not reliable** (confirmed by the user). A row
  with two perks always holds one major and one minor, and a lone perk is minor,
  but left/right order can't be trusted. Most rows show major on the left, but
  certain heroes and perk combinations consistently flip in screenshots from the
  same era: Juno's Locked On + Lift Off (7 of 8 rows), Moira's Ethical
  Nourishment + Reversal, Reaper's Soul Reaving + Shadow Blink, and Sojourn's
  Deceleration Field + Friction Generators. No slot-based tier inference is made.
  Under the one-major-one-minor rule, all 13 reviewed screenshots are consistent
  with `perks.json`'s tier history.
- Whether Emerald's badge art reuses a hue close to Master's.
- Whether perk icons are ever rendered at a different size in 5v5 vs 6v6 layouts.
