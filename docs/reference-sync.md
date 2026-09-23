# Reference sync

`tools/sync_reference.py` keeps the hero roster, perk list and icon library in
step with the game. Heroes, perks and perk tiers change every patch. A hero,
perk or icon the reference data doesn't know about makes the parser fail
silently, so run this check regularly.

It maintains:

| File | What |
|---|---|
| `reference/heroes.json` | Roster: every hero, their role/subrole, wiki page, owperks page, release status |
| `reference/perks.json` | Every perk that has an icon, live and removed, with tier, tier history and pick rate |
| `assets/bans/<slug>.png` | 256×256 ban icon (3D render), wiki `File:Icon-<Name>.png` |
| `assets/heroes/<slug>.png` | 256×256 illustrated portrait, wiki `File:<Name> Hero.png` |
| `assets/perks/<slug>/<perk>.png` | 128×128 perk icon, wiki `File:Perk <Name>.png` (or the file the hero page names) |

## When to run it

- After every patch that adds or changes perks (usually mid-season patches).
- On every season launch. **Next: Season 5, 2026-10-06.** Doctrine releases,
  Sombra moves to Support with new perks, and Roadhog is reworked.
- When a new hero is revealed or released.
- Any time the parser meets a perk icon it can't match.

## How to run it

```
pip install -r requirements.txt           # once (only Pillow is needed for the sync)

python tools/sync_reference.py            # 1. report: prints what it found and would change
python tools/sync_reference.py --apply    # 2. apply: downloads icons, updates the JSON
python tools/sync_reference.py --check-only   # offline consistency check (no network)
```

Always run the report first, read it, then `--apply`. With `--apply`, if
validation fails nothing is written. The script never deletes files or perk
entries.

| Option | Effect |
|---|---|
| `--apply` | download new icons and write both JSON files |
| `--hero SLUG` | limit the hero and perk checks to these heroes (repeatable), e.g. `--hero sombra --hero roadhog` |
| `--refresh-rates` | refresh the owperks pick rate of *every* live perk, not only pairs touched by a change |
| `--check-only` | validate files against each other offline; fast, suitable before a commit |
| `--root DIR` | run against a copy of the repo (used for testing) |

A full run takes about a minute, mostly waiting on network requests.

**Exit codes:**
- `0`: everything is accounted for;
- `1`: changes found (report) or applied (`--apply`);
- `2`: conflicts or validation errors a human must look at.

## What it does

1. **Roster.** Reads the wiki's `Category:Heroes` (released heroes) and the
   hero list on owperks.com (which also shows revealed, unreleased heroes). A
   name not in `heroes.json` is a **new hero**:
   - its slug is lowercase with underscores (`D.Mon` → `d_mon`);
   - its role and subrole are read from the wiki infobox;
   - it gets `released: false` if it is not in `Category:Heroes` yet.

   Role changes on existing heroes are updated and reported.
2. **Hero icons.** For any hero missing a ban or hero icon, or whose icon is
   listed in `provisional_icons`, it fetches the wiki files. Only exact
   256×256 PNGs are accepted, and it never resizes. A provisional icon is
   replaced (and delisted) as soon as a proper one exists.
3. **Perks.** For each hero it compares three things:
   - every perk box on the wiki page, live and removed;
   - the 4 live perks on owperks;
   - `perks.json`.

   A perk the JSON doesn't know is **undocumented**. The script finds its icon
   (the file the wiki box names, then `Perk <Name>.png` variants), downloads
   and verifies it, and adds a full entry: tier, tier history, effect,
   ability, era and pick rate.
4. **Live set.** owperks is the source of truth for which 4 perks are live.
   - A perk that became live is marked `current`.
   - A `current` perk that is no longer live is **retired**: it becomes
     `legacy`, loses its `pick_rate` field, and gets a `history_note`.
   - A perk whose owperks slot tier differs from the JSON gets its `tier`
     updated, `tier_swapped: true`, and a dated `tier_history` entry. It is now
     exempt from the slot check (see below).
5. **Validate**, then write (with `--apply`) and print the report.

## Rules

- **Only live perks carry a pick rate.** Exactly 4 per hero (2 minor, 2 major),
  and each pair sums to 1.0. When a new perk takes a slot, it gets its owperks
  rate, and the perk it displaced loses the `pick_rate` field (the field is
  removed, not set to null). The other perk in that tier gets its rate
  refreshed so the pair still sums to 1.0.
- **This pick-rate rule only applies while `_meta.pick_rate_source` is
  `"owperks"`.** owperks figures are community-reported and skewed against
  real lobbies. Once we compute rates from our own parsed screenshots, set
  `pick_rate_source` to `"local"`: the sync then never adds, changes or
  removes `pick_rate`. It still retires perks and tracks tiers.
- **Tier-swapped perks skip the slot check.** A perk that has been both minor
  and major can legally appear in either scoreboard slot, depending on the
  patch the screenshot came from. See `_meta.slot_check_policy` in
  `perks.json`.
- **Pending.** A perk that can't be added yet goes to `_meta.pending[<slug>]`
  with a `reason`: either no icon on the wiki yet, or the wiki calls it current
  but owperks doesn't show it live. Pending is regenerated every run, and
  entries are promoted automatically once the blocker clears. Don't hand-edit
  it.

## Reading the report

| Section | Meaning / what to do |
|---|---|
| Roster, Hero icons, New perks, Retired perks, Reactivated perks, Tier changes, Pick-rate changes, Migrations | Changes. Check they look right, then `--apply`. |
| Pending | Normal for revealed or unreleased content (e.g. Doctrine before launch). Nothing to do. |
| Warnings | Something unusual but not blocking, e.g. a wiki icon at the wrong size. Worth a look. |
| **CONFLICTS** | The sources disagree; see below. The script held back instead of guessing. |
| **VALIDATION ERRORS** | The files contradict each other. `--apply` refuses to write until fixed. |

### Resolving common conflicts

- **"current on the wiki but not live on owperks"**
  - Before a patch goes live: expected, because wiki editors add announced
    changes early (Sombra's Season 5 perks show up this way until Oct 6).
    Nothing to do.
  - After the patch is out: owperks is lagging. Wait a few days, or add the
    entry by hand following the schema, then run `--check-only`.
- **"owperks lists N perks, expected 4"**: owperks changed its page layout or
  is mid-update. Check the page, and fix the parser in `tools/owsources.py`
  (`owperks_hero`) if the layout changed.
- **"exists with different content"**: a perk icon file with that name is
  already there but differs from the wiki's. Compare the two images by eye;
  replace the file if the wiki's is the newer art.

### Manual steps the script can't do

- **Provisional icons.** If you add a stand-in icon (e.g. resized fan art for
  an unreleased hero), add its kind (`"ban"` or `"hero"`) to that hero's
  `provisional_icons` in `heroes.json`. The sync then replaces it once an
  official file appears. Doctrine's ban icon is provisional right now.
- **Non-standard icon sizes.** Reported, never auto-resized. Three perk icons
  are still non-128px because no other size has ever been uploaded:
  Lúcio / Beat Drop (96), Reinhardt / Ignited Fury (96),
  Roadhog / Shrapnel Launcher (212×128).
- **Role changes** (e.g. Sombra → Support). The sync updates `heroes.json`,
  but the parser's role checks (`src/extract.py::validate`) must still accept
  the role a hero had at the screenshot's date.

## Sources and download gotchas

- **Wiki:** https://overwatch.weirdgloop.org via its MediaWiki `api.php`. Use
  it in preference to Fandom, which lags behind (e.g. it never had D.Mon's
  portrait). Fandom is only a fallback for file downloads.
- **Getting exact files.** Every download is SHA1-checked against the wiki's
  `imageinfo`:
  - weirdgloop's image URLs carry a `?xxxxx` cache-buster that returns a
    re-compressed PNG. The script requests `?format=original` instead.
  - Fandom serves lossy WebP under a `.png` name unless `&format=original` is
    added.
  - Old file revisions (`/images/archive/...`) never return the original bytes,
    so the script only ever takes the current revision.
- **owperks.com.** Each hero page lists the 2 minor perks, then the 2 major
  perks; the tier comes from that order. The percentages are community-reported
  shares, not observed pick rates.
- **Wiki quirks the parser handles:**
  - perks that were re-added stay in the "Removed Perks" section, with a
    "re-added in …" note;
  - "Changed to a Minor Perk" in the change log means it was major before;
  - a wiki perk box can lag the change log (Hanzo's Dragon Fury): the latest
    change-log move, and owperks' slot order, win.

## Code

- `tools/sync_reference.py`: the command, the per-step logic, validation, and
  JSON formatting (stable, one perk per line, so reruns are byte-identical and
  diffs stay readable).
- `tools/owsources.py`: HTTP, wiki and owperks parsing, verified downloads.
