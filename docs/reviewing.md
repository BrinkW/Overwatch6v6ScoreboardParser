# Reviewing new captures

`tools/review.py` turns contributors' screenshots into answer keys. The parser
drafts every field; you only correct what it got wrong. Each reviewed capture
then becomes training data (templates) and test data (evaluation).

## Workflow

```
python tools/review.py OW-scoreboards_alex_2026-10-01_1830_12shots.zip   # import + review
python tools/build_templates.py                                          # learn from what you reviewed (data/templates/)
python tools/evaluate.py                                                 # leave-one-image-out accuracy, all keys
python tools/review.py --stats                                           # how good the drafts were
```

1. **Import.** Pass capture tool zips, folders or images. Each new capture is
   copied to `data/inbox/` and parsed into a draft. Exact duplicates are
   skipped, including captures already reviewed or rejected. A zip's
   `info.txt` records the contributor.
2. **Review.** Your browser opens on `http://127.0.0.1:8765/`. It is only
   reachable from this machine. For the field you are on, the page draws a box
   on the screenshot and shows a zoomed crop with the parser's evidence (what
   it read, its margin).
   - Fields the parser flagged have an **amber** outline. The page starts on
     the first one; **Alt+↓ / Alt+↑** jumps between flagged fields.
   - Fields you changed get a **blue** edge.
   - Heroes, perks, maps, modes, bans and ranks are dropdowns. Hero and perk
     icons show beside them, to compare with the crop.
   - Picking a map sets its mode. Picking a hero sets the role and narrows the
     perk list.
   - Blank means unknown (not scored), except the title, where blank means the
     row has no title. Perk slots have "— empty slot" and "? present,
     unidentified".
   - Bans show as many slots as the screen has (4 or 5). "+ 5th slot" /
     "− remove 5th slot" corrects the count.
   - A player who had just swapped hero shows a grey "?" portrait: pick
     **Mystery (swapped hero)**, which clears the role. Its perks must be empty.
3. **Accept** (**Ctrl+Enter**). The key is validated before it is written. For
   example, the map must match the mode, a perk must belong to its hero, stats
   must be whole numbers, and a team can have at most 2 tanks. The capture and
   its key move to `data/reviewed/`.
4. **Reject** a capture that isn't a usable scoreboard (wrong screen, cropped,
   covered). It moves to `data/rejected/`, with your reason.
5. **Rebuild the templates** after a session. `python tools/build_templates.py`
   writes the local build to `data/templates/`, which the parser and this tool
   use whenever it exists. Drafts still in the inbox are re-parsed with the new
   templates when you open them.

## Where things live

| Path | What |
|---|---|
| `data/inbox/` | captures waiting for review, with `<name>.draft.json` drafts |
| `data/reviewed/` | accepted captures and their answer keys (same schema as `tests/fixtures/`) |
| `data/rejected/` | rejected captures and `<name>.reason.txt` |
| `data/review_log.jsonl` | one line per accept: every field, the flagged ones, and what you changed |

`data/` is git-ignored: contributors' screenshots show other players' names,
and the images add about 2 MB each. **Back it up yourself.** Everything learned
from it stays there too (`data/templates/`, including `known_text.json`, which
lists player names and titles). The committed `reference/templates/` are built
only from the committed answer keys (`python tools/build_templates.py
--public`).

`--stats` compares each draft with your final key. Every capture was parsed
before its own key existed, so this is the parser's real accuracy on unseen
screenshots. It also shows how many wrong fields were flagged (the review
safety net) and how many flags were false alarms (review effort).
