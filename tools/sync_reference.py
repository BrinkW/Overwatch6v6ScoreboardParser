"""
Keep reference/heroes.json, reference/perks.json and assets/ in step with the game.

    python tools/sync_reference.py              # report only (nothing written or downloaded)
    python tools/sync_reference.py --apply      # download icons + update the JSON
    python tools/sync_reference.py --check-only # offline consistency check

Full documentation, including the rules this script enforces and why:
docs/reference-sync.md

Exit codes: 0 clean, 1 changes found/applied, 2 conflicts or validation errors
that need a human.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True  # keep tools/ free of __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))
import owsources as src  # noqa: E402

TODAY = datetime.date.today().isoformat()
PERK_KEY_ORDER = ["name", "icon", "alt_icons", "tier", "tier_swapped", "tier_history", "ability", "effect",
                  "patch_era", "pick_rate", "history_note", "icon_note", "note"]
ROLE_FROM_PATH = {"tanks": "tank", "damages": "damage", "supports": "support"}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
class Report:
    SECTIONS = ["Roster", "Hero icons", "Maps", "Titles", "New perks", "Official art", "Pending (no icon / not live yet)", "Retired perks",
                "Reactivated perks", "Tier changes", "Pick-rate changes", "Migrations", "Warnings",
                "CONFLICTS (need a human)", "VALIDATION ERRORS"]
    CHANGE = {"Roster", "Hero icons", "Maps", "Titles", "New perks", "Official art", "Retired perks", "Reactivated perks", "Tier changes",
              "Pick-rate changes", "Migrations"}

    def __init__(self):
        self.lines = {s: [] for s in self.SECTIONS}

    def add(self, section, msg):
        self.lines[section].append(msg)

    @property
    def changed(self):
        return any(self.lines[s] for s in self.CHANGE)

    @property
    def blocking(self):
        return bool(self.lines["CONFLICTS (need a human)"] or self.lines["VALIDATION ERRORS"])

    def print(self, apply):
        print(f"Reference sync {TODAY} ({'APPLY' if apply else 'report only - nothing written'})\n")
        for s in self.SECTIONS:
            if self.lines[s]:
                print(f"## {s} ({len(self.lines[s])})")
                for m in self.lines[s]:
                    print(f"  - {m}")
                print()
        if not any(self.lines.values()):
            print("Everything is accounted for. No changes.\n")


# ---------------------------------------------------------------------------
# JSON I/O (stable formatting so diffs stay readable and reruns are byte-identical)
# ---------------------------------------------------------------------------
def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def dump_perks(data: dict) -> str:
    lines = ["{", '  "_meta": ' + json.dumps(data["_meta"], ensure_ascii=False, indent=2).replace("\n", "\n  ") + ",",
             '  "heroes": {']
    heroes = sorted(data["heroes"].items())
    for i, (slug, h) in enumerate(heroes):
        lines.append(f'    "{slug}": {{"name": {json.dumps(h["name"], ensure_ascii=False)}, "perks": [')
        perks = sorted(h["perks"], key=lambda e: (e["patch_era"] != "current", e["tier"] != "minor", e["name"]))
        for j, e in enumerate(perks):
            ordered = {k: e[k] for k in PERK_KEY_ORDER if k in e}
            ordered.update({k: v for k, v in e.items() if k not in ordered})
            lines.append("      " + json.dumps(ordered, ensure_ascii=False) + ("," if j < len(perks) - 1 else ""))
        lines.append("    ]}" + ("," if i < len(heroes) - 1 else ""))
    lines += ["  }", "}"]
    return "\n".join(lines) + "\n"


def dump_roster(data: dict) -> str:
    lines = ["{", '  "_meta": ' + json.dumps(data["_meta"], ensure_ascii=False, indent=2).replace("\n", "\n  ") + ",",
             '  "heroes": {']
    heroes = sorted(data["heroes"].items())
    for i, (slug, h) in enumerate(heroes):
        lines.append(f'    "{slug}": ' + json.dumps(h, ensure_ascii=False) + ("," if i < len(heroes) - 1 else ""))
    lines += ["  }", "}"]
    return "\n".join(lines) + "\n"


def write_atomic(path: Path, text: str):
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


ROSTER_META = {
    "description": "Every hero the parser knows about. Maintained by tools/sync_reference.py (see docs/reference-sync.md).",
    "schema": {
        "name": "display name (matches the wiki page title)",
        "wiki_page": "page title on overwatch.weirdgloop.org",
        "owperks_path": "path on owperks.com/en/, or null if owperks has no page",
        "role": "tank | damage | support, from the wiki infobox. Can change (Sombra moves to support in Season 5): validate() must allow for the role at the screenshot's date.",
        "subrole": "wiki infobox sub-role",
        "released": "false for revealed-but-unreleased heroes (e.g. playtest-only)",
        "provisional_icons": "icons in assets/ that are stand-ins (e.g. resized fan art); the sync replaces them once an official 256x256 file exists",
        "blizzard_path": "hero page slug on overwatch.blizzard.com/en-us/heroes/ (source of current official perk art), or null",
    },
}


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------
class Sync:
    def __init__(self, root: Path, apply: bool, only: set[str], refresh_rates: bool):
        self.root, self.apply, self.only, self.refresh_rates = root, apply, only, refresh_rates
        self.perks_path = root / "reference" / "perks.json"
        self.roster_path = root / "reference" / "heroes.json"
        self.maps_path = root / "reference" / "maps.json"
        self.maps = load_json(self.maps_path, None)
        self.titles_path = root / "reference" / "titles.json"
        self.titles = load_json(self.titles_path, None)
        self.perks = load_json(self.perks_path, None)
        if self.perks is None:
            raise SystemExit(f"{self.perks_path} not found")
        self.roster = load_json(self.roster_path, {"_meta": ROSTER_META, "heroes": {}})
        self.report = Report()
        self.staged: dict[Path, bytes | None] = {}  # dest -> bytes (None in report mode)
        self.pages: dict[str, str | None] = {}
        self.meta = self.perks["_meta"]
        self.meta.setdefault("pick_rate_source", "owperks")
        self.pending = self.meta.setdefault("pending", {"_note": ""})

    # -- helpers ------------------------------------------------------------
    def page(self, slug):
        if slug not in self.pages:
            self.pages[slug] = src.wiki_page(self.roster["heroes"][slug]["wiki_page"])
        return self.pages[slug]

    def stage(self, dest: Path, title: str) -> bool:
        """Queue a verified download. In report mode only availability was checked."""
        if not self.apply:
            self.staged[dest] = None
            return True
        got = src.download_verified(title)
        if not got:
            self.report.add("Warnings", f"{title}: listed on the wiki but no copy matched its SHA1; not downloaded")
            return False
        self.staged[dest] = got[0]
        return True

    def stage_bytes(self, dest: Path, data: bytes):
        """Queue a download whose bytes are already in hand (official art has no SHA1 to verify against)."""
        self.staged[dest] = data if self.apply else None

    def exists(self, dest: Path) -> bool:
        return dest.exists() or dest in self.staged

    # -- step 1: roster ------------------------------------------------------
    def sync_roster(self):
        heroes = self.roster["heroes"]
        by_name = {src.loose(h["name"]): s for s, h in heroes.items()}
        released = src.wiki_hero_names()
        for name in released:
            slug = by_name.get(src.loose(name))
            if slug is None:
                slug = src.hero_slug(name)
                heroes[slug] = {"name": name, "wiki_page": name, "owperks_path": None, "role": None,
                                "subrole": None, "released": True, "provisional_icons": []}
                by_name[src.loose(name)] = slug
                self.report.add("Roster", f"NEW HERO {name} ({slug}) found on the wiki")
            elif not heroes[slug]["released"]:
                heroes[slug]["released"] = True
                self.report.add("Roster", f"{name} is now released (listed in the wiki's Category:Heroes)")
        known_paths = {h["owperks_path"] for h in heroes.values()}
        for path in src.owperks_hero_paths():
            if path in known_paths:
                continue
            key = src.loose(path.split("/")[1])
            slug = next((s for s, h in heroes.items() if key in (src.loose(h["name"]), src.loose(s))), None)
            if slug is None:
                ow = src.owperks_hero(path) or {}
                name = ow.get("name") or path.split("/")[1].replace("-", " ").title()
                slug = by_name.get(src.loose(name))
                if slug is None:
                    slug = src.hero_slug(name)
                    heroes[slug] = {"name": name, "wiki_page": name, "owperks_path": path,
                                    "role": ROLE_FROM_PATH[path.split("/")[0]], "subrole": None,
                                    "released": False, "provisional_icons": []}
                    by_name[src.loose(name)] = slug
                    self.report.add("Roster", f"NEW HERO {name} ({slug}) found on owperks, not yet released on the wiki")
                    continue
            if heroes[slug]["owperks_path"] != path:
                heroes[slug]["owperks_path"] = path
                self.report.add("Roster", f"{heroes[slug]['name']}: owperks page is /en/{path}")
        # official hero pages (released heroes only)
        for path in src.blizzard_hero_paths():
            key = src.loose(path)
            slug = next((s for s, h in heroes.items() if key in (src.loose(h["name"]), src.loose(s))), None)
            if slug is None:
                self.report.add("Warnings", f"overwatch.blizzard.com lists hero page '{path}' that matches no roster hero")
            elif heroes[slug].get("blizzard_path") != path:
                heroes[slug]["blizzard_path"] = path
                self.report.add("Roster", f"{heroes[slug]['name']}: official page is /en-us/heroes/{path}/")
        # roles, from the wiki infobox
        for slug in self.selected():
            h = heroes[slug]
            text = self.page(slug)
            if text is None:
                self.report.add("Warnings", f"{h['name']}: no wiki page titled '{h['wiki_page']}'")
                continue
            role, sub = src.infobox_role(text)
            if role is None and h["owperks_path"]:
                role = ROLE_FROM_PATH[h["owperks_path"].split("/")[0]]
            for field, val in (("role", role), ("subrole", sub)):
                if val and h.get(field) != val:
                    if h.get(field) is not None:
                        self.report.add("Roster", f"{h['name']}: {field} changed {h[field]} -> {val} "
                                                  "(check validate()'s role counts for older screenshots)")
                    h[field] = val

    def selected(self):
        return [s for s in sorted(self.roster["heroes"]) if not self.only or s in self.only]

    # -- step 1b: hero icons ------------------------------------------------
    def sync_hero_icons(self):
        for slug in self.selected():
            h = self.roster["heroes"][slug]
            names = [h["wiki_page"]] + ([h["wiki_page"].replace(":", "")] if ":" in h["wiki_page"] else [])
            for kind, folder, fmt in (("ban", "bans", "Icon-{}.png"), ("hero", "heroes", "{} Hero.png")):
                dest = self.root / "assets" / folder / f"{slug}.png"
                provisional = kind in h["provisional_icons"]
                if dest.exists() and not provisional:
                    continue
                titles = ["File:" + fmt.format(n) for n in names]
                infos = src.file_info(titles)
                title = next((t for t in titles if t in infos), None)
                if title is None:
                    level = "Warnings" if h["released"] else "Pending (no icon / not live yet)"
                    self.report.add(level, f"{h['name']}: no {kind} icon on the wiki yet ({titles[0]})"
                                    + (" - keeping the provisional one" if provisional else ""))
                    continue
                info = infos[title]
                if (info["width"], info["height"]) != (256, 256) or info.get("mime") != "image/png":
                    self.report.add("Warnings", f"{h['name']}: {title} is {info['width']}x{info['height']} "
                                    f"{info.get('mime')}, not a 256x256 PNG; not used"
                                    + (" (provisional icon kept)" if provisional else ""))
                    continue
                if provisional and dest.exists() and hashlib.sha1(dest.read_bytes()).hexdigest() == info["sha1"]:
                    h["provisional_icons"].remove(kind)
                    continue
                if self.stage(dest, title):
                    if provisional:
                        h["provisional_icons"].remove(kind)
                    self.report.add("Hero icons", f"{h['name']}: {'replace provisional' if provisional else 'add'} "
                                                  f"assets/{folder}/{slug}.png <- {title}")

    # -- step 2-4: perks ----------------------------------------------------
    def sync_perks(self):
        for key in [k for k in self.pending if k != "_note" and k not in self.roster["heroes"]]:
            del self.pending[key]  # legacy free-form keys; regenerated per slug below
        for slug in self.selected():
            self.sync_hero_perks(slug)
            self.sync_official_art(slug)

    def sync_hero_perks(self, slug):
        h = self.roster["heroes"][slug]
        text = self.page(slug) or ""
        wiki = [p for p in src.wiki_perks(text) if p["name"]]
        abilities = src.wiki_abilities(text)
        ow = src.owperks_hero(h["owperks_path"]) if h["owperks_path"] else None
        live = ow["live"] if ow else None
        if live is not None and len(live) != 4:
            self.report.add("CONFLICTS (need a human)", f"{h['name']}: owperks lists {len(live)} perks, expected 4; "
                                                        "live set not reconciled")
            live = None
        if live is None and h["released"]:
            self.report.add("Warnings", f"{h['name']}: no usable owperks data; live set and pick rates not checked")

        block = self.perks["heroes"].get(slug) or {"name": h["name"], "perks": []}
        known = {src.loose(e["name"]): e for e in block["perks"]}
        wmap = {src.loose(p["name"]): p for p in wiki}
        lmap = {src.loose(n): {"name": n, "tier": t, "rate": r, "desc": d} for n, t, r, d in live} if live else {}
        use_rates = self.meta["pick_rate_source"] == "owperks"
        pending, touched = [], set()

        # step 2: undocumented perks
        for key in sorted((set(wmap) | set(lmap)) - set(known)):
            w, l = wmap.get(key), lmap.get(key)
            name = (w or l)["name"]
            is_live = (l is not None) if live is not None else bool(w and not w["removed"])
            pend = {"name": name, "icon": None, "tier": l["tier"] if l else w["tier"],
                    "effect": (w and w["effect"]) or (l and l["desc"])}
            if is_live and use_rates and l and l["rate"] is not None:
                pend["pick_rate"] = l["rate"]
            if live is not None and not l and w and not w["removed"]:
                pend["reason"] = "wiki lists it as current but owperks does not show it live"
                pending.append(pend)
                self.report.add("CONFLICTS (need a human)",
                                f"{h['name']} / {name}: current on the wiki but not live on owperks. Held in pending. "
                                "Normal before a patch/rework goes live; if the patch is out, owperks is lagging")
                continue
            titles = []
            if w and w["image"]:
                titles.append("File:" + w["image"].replace("_", " ").strip())
            titles += [f"File:Perk {name}.png", f"File:Perk {name.replace(' ', '')}.png", f"File:{name}.png"]
            titles = list(dict.fromkeys(titles))
            infos = src.file_info(titles)
            title = next((t for t in titles if t in infos), None)
            if title is None:
                pend["reason"] = "no icon on the wiki yet"
                pending.append(pend)
                self.report.add("Pending (no icon / not live yet)", f"{h['name']} / {name}: no icon on the wiki yet")
                continue
            info = infos[title]
            dest = self.root / "assets" / "perks" / slug / f"{src.snake(name)}.png"
            if dest.exists() and hashlib.sha1(dest.read_bytes()).hexdigest() != info["sha1"]:
                self.report.add("CONFLICTS (need a human)",
                                f"{h['name']} / {name}: {dest.relative_to(self.root)} exists with different content "
                                f"than {title}; not overwritten, entry not added")
                continue
            if not dest.exists() and not self.stage(dest, title):
                pend["reason"] = "icon download failed verification"
                pending.append(pend)
                continue
            hist = src.tier_history(text, name) if text else []
            tier = l["tier"] if l else (hist[-1]["to"] if hist else w["tier"])
            last = hist[-1]["to"] if hist else (w["tier"] if w else tier)
            if last != tier:
                hist.append({"date": TODAY, "from": last, "to": tier})
            e = {"name": name, "icon": f"{slug}/{dest.name}", "tier": tier, "tier_swapped": bool(hist)}
            if hist:
                e["tier_history"] = hist
            e["ability"] = src.derive_ability(pend["effect"] or "", abilities)
            e["effect"] = pend["effect"]
            e["patch_era"] = "current" if is_live else "legacy"
            if "pick_rate" in pend:
                e["pick_rate"] = pend["pick_rate"]
            if w and w["removed_note"]:
                e["history_note"] = w["removed_note"]
            block["perks"].append(e)
            known[key] = e
            if is_live:
                touched.add(tier)
            size = f"{info['width']}x{info['height']}"
            self.report.add("New perks", f"{h['name']} / {name} ({tier}, {e['patch_era']}) <- {title}"
                            + ("" if size == "128x128" else f"  [icon is {size}, expected 128x128]"))

        # step 3: live set
        if live is not None:
            for key, e in known.items():
                if key in lmap and e["patch_era"] == "legacy":
                    e["patch_era"] = "current"
                    touched.add(lmap[key]["tier"])
                    self.report.add("Reactivated perks", f"{h['name']} / {e['name']}: live again on owperks")
                elif key not in lmap and e["patch_era"] == "current":
                    e["patch_era"] = "legacy"
                    e.pop("pick_rate", None)
                    w = wmap.get(key)
                    e.setdefault("history_note", (w and w["removed_note"]) or f"Retired (detected by sync {TODAY})")
                    touched.add(e["tier"])
                    self.report.add("Retired perks", f"{h['name']} / {e['name']} ({e['tier']}): no longer live")
            # step 4: tier changes
            for key, e in known.items():
                if key not in lmap or e["tier"] == lmap[key]["tier"]:
                    continue
                new = lmap[key]["tier"]
                wiki_hist = src.tier_history(text, e["name"]) if text else []
                date = next((m["date"] for m in reversed(wiki_hist) if m["to"] == new), None) or TODAY
                move = {"date": date, "from": e["tier"], "to": new}
                if move not in e.setdefault("tier_history", []):
                    e["tier_history"].append(move)
                e["tier_swapped"] = True
                touched.update({e["tier"], new})
                self.report.add("Tier changes", f"{h['name']} / {e['name']}: {e['tier']} -> {new} ({date}). "
                                                "Now exempt from the slot check")
                e["tier"] = new
            # pick rates: only for tier pairs touched by a change (or everything with --refresh-rates)
            if use_rates:
                for key, e in known.items():
                    l = lmap.get(key)
                    if not l or l["rate"] is None:
                        continue
                    if self.refresh_rates or e["tier"] in touched or "pick_rate" not in e:
                        if e.get("pick_rate") != l["rate"]:
                            self.report.add("Pick-rate changes", f"{h['name']} / {e['name']}: "
                                                                 f"{e.get('pick_rate')} -> {l['rate']}")
                            e["pick_rate"] = l["rate"]

        if block["perks"]:
            self.perks["heroes"][slug] = block
        if pending:
            self.pending[slug] = pending
        else:
            self.pending.pop(slug, None)

    # -- step 3b: official art --------------------------------------------------
    def sync_official_art(self, slug):
        """Compare every live perk with the art on its official hero page. The game's
        art is sometimes redrawn after the wikis uploaded theirs (Baptiste's Automated
        Healing, Soldier's Stim Pack, ...). New official art is ADDED as an extra
        reference (`alt_icons`); the old art stays, since screenshots from older
        patches still show it."""
        h = self.roster["heroes"][slug]
        path = h.get("blizzard_path")
        if not path:
            return
        official = src.blizzard_perks(path)
        if not official:
            self.report.add("Warnings", f"{h['name']}: no perks found on the official page /heroes/{path}/")
            return
        block = self.perks["heroes"].get(slug, {"perks": []})
        by_key = {src.loose(e["name"]): e for e in block["perks"]}
        for o in official:
            key = src.loose(o["name"])
            if key not in by_key:   # tolerate typos on the official page ("MEKA Mobilitiy")
                close = difflib.get_close_matches(key, list(by_key), n=1, cutoff=0.85)
                key = close[0] if close else key
            e = by_key.get(key)
            if e is None:
                self.report.add("CONFLICTS (need a human)",
                                f"{h['name']} / {o['name']}: live on the official site but not in perks.json")
                continue
            if e["patch_era"] == "current" and e["tier"] != o["tier"]:
                self.report.add("CONFLICTS (need a human)",
                                f"{h['name']} / {e['name']}: official site says {o['tier']}, perks.json says {e['tier']}")
            data = src.fetch(o["url"])
            refs = [e["icon"]] + [a["icon"] for a in e.get("alt_icons", [])]
            known = [self.root / "assets" / "perks" / r for r in refs if (self.root / "assets" / "perks" / r).exists()]
            if any(src.alpha_difference(data, p.read_bytes()) < 0.01 for p in known):
                continue
            sha = hashlib.sha1(data).hexdigest()[:8]
            rel = f"{slug}/{src.snake(e['name'])}__official_{sha}.png"
            self.stage_bytes(self.root / "assets" / "perks" / rel, data)
            e.setdefault("alt_icons", []).append({"icon": rel, "source": "overwatch.blizzard.com", "added": TODAY})
            self.report.add("Official art", f"{h['name']} / {e['name']}: official art differs from ours; "
                                            f"added {rel} as an extra reference (old art kept)")

    # -- maps ---------------------------------------------------------------
    def sync_maps(self):
        """reference/maps.json: every Standard Play map with its mode (plus former
        Assault/Clash maps), the closed list the header's map name is snapped to."""
        found = src.wiki_maps()
        if len(found) < 20:
            self.report.add("Warnings", f"the wiki Maps page yielded only {len(found)} maps; maps.json not updated")
            return
        old = {(m["name"], m["mode"], m["current"]) for m in (self.maps or {}).get("maps", [])}
        new = {(m["name"], m["mode"], m["current"]) for m in found}
        for name, mode, cur in sorted(new - old):
            self.report.add("Maps", f"add {name} ({mode}{'' if cur else ', former'})")
        for name, mode, cur in sorted(old - new):
            self.report.add("Maps", f"remove {name} ({mode}) - no longer on the wiki's Maps page")
        if new != old:
            self.maps = {"_meta": {"description": "Maps and their modes, for snapping the scoreboard header's map name. "
                                                  "Maintained by tools/sync_reference.py from the wiki's Maps page.",
                                   "current": "true = in the Standard Play pool; false = a former mode (Assault, Clash)"},
                         "maps": sorted(found, key=lambda m: (not m["current"], m["mode"], m["name"]))}

    # -- titles -------------------------------------------------------------
    # Competitive reward titles the wiki doesn't list, seen on scoreboards as
    # "<Tier> <Role>" ("Grandmaster Support", "Challenger Tank") and
    # "<Tier> Open Competitor|Challenger" ("Champion Open Competitor").
    RANK_TITLE_TIERS = ["Bronze", "Silver", "Gold", "Platinum", "Emerald", "Diamond", "Master", "Grandmaster",
                        "Champion", "Challenger"]
    RANK_TITLE_KINDS = ["Tank", "Damage", "Support", "Open Competitor", "Open Challenger"]

    def sync_titles(self):
        """reference/titles.json: the player titles a scoreboard title is snapped to."""
        found = src.wiki_titles()
        if len(found) < 100:
            self.report.add("Warnings", f"the wiki Titles page yielded only {len(found)} titles; titles.json not updated")
            return
        entries = [{"title": t["title"], "source": "wiki", "section": t["section"]} for t in found]
        have = {e["title"] for e in entries}
        pattern = [f"{t} {k}" for t in self.RANK_TITLE_TIERS for k in self.RANK_TITLE_KINDS]
        for t in pattern + ["Open Competitor", "Open Challenger"]:
            if t not in have:
                have.add(t)
                entries.append({"title": t, "source": "rank-pattern"})
        old = {e["title"] for e in (self.titles or {}).get("titles", [])}
        for t in sorted(have - old):
            self.report.add("Titles", f"add {t}")
        for t in sorted(old - have):
            self.report.add("Titles", f"remove {t} - no longer on the wiki's Titles page")
        if have != old:
            self.titles = {"_meta": {
                "description": "Player titles, for snapping the scoreboard's title line. Maintained by "
                               "tools/sync_reference.py from the wiki's Titles page, plus competitive reward "
                               "titles generated from the pattern seen on scoreboards (source rank-pattern). "
                               "Titles seen in the answer keys are added at build time "
                               "(reference/templates/known_text.json), so event titles the wiki lacks still snap."},
                "titles": entries}

    # -- migrations ---------------------------------------------------------
    def migrate(self):
        n = 0
        for h in self.perks["heroes"].values():
            for e in h["perks"]:
                if e["patch_era"] == "legacy" and "pick_rate" in e:
                    del e["pick_rate"]
                    n += 1
        if n:
            self.report.add("Migrations", f"removed the pick_rate field from {n} legacy perks "
                                          "(only live perks carry a pick rate)")

    # -- write --------------------------------------------------------------
    def write(self):
        for dest, data in self.staged.items():
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
        self.meta["last_synced"] = TODAY
        write_atomic(self.perks_path, dump_perks(self.perks))
        write_atomic(self.roster_path, dump_roster(self.roster))
        if self.maps is not None:
            write_atomic(self.maps_path, json.dumps(self.maps, ensure_ascii=False, indent=1) + "\n")
        if self.titles is not None:
            write_atomic(self.titles_path, json.dumps(self.titles, ensure_ascii=False, indent=1) + "\n")


# ---------------------------------------------------------------------------
# Validation (also the whole of --check-only)
# ---------------------------------------------------------------------------
def validate(root: Path, perks: dict, roster: dict, staged=()) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    staged = {Path(p) for p in staged}
    pdir = root / "assets" / "perks"
    files = {f"{d.name}/{f.name}" for d in pdir.iterdir() if d.is_dir() for f in d.iterdir()}
    files |= {f"{p.parent.name}/{p.name}" for p in staged if p.parent.parent == pdir}
    icons = [i for h in perks["heroes"].values() for e in h["perks"]
             for i in [e["icon"]] + [a["icon"] for a in e.get("alt_icons", [])]]
    for f in sorted(files - set(icons)):
        errors.append(f"assets/perks/{f} has no perks.json entry")
    for f in sorted(set(icons) - files):
        errors.append(f"perks.json entry points at missing icon assets/perks/{f}")
    for f in sorted({i for i in icons if icons.count(i) > 1}):
        errors.append(f"assets/perks/{f} is referenced by more than one entry")

    use_rates = perks["_meta"].get("pick_rate_source", "owperks") == "owperks"
    rh = roster.get("heroes", {})
    for slug, h in sorted(perks["heroes"].items()):
        if rh and slug not in rh:
            errors.append(f"perks.json hero '{slug}' is not in heroes.json")
        cur = [e for e in h["perks"] if e["patch_era"] == "current"]
        tiers = [e["tier"] for e in cur]
        if cur and (tiers.count("minor") != 2 or tiers.count("major") != 2):
            errors.append(f"{slug}: expected 2 minor + 2 major live perks, found {tiers}")
        for e in h["perks"]:
            where = f"{slug} / {e['name']}"
            if e["patch_era"] == "legacy" and "pick_rate" in e:
                errors.append(f"{where}: legacy perk still has a pick_rate field")
            if use_rates and e["patch_era"] == "current" and e.get("pick_rate") is None:
                errors.append(f"{where}: live perk has no pick_rate")
            if bool(e.get("tier_history")) != e["tier_swapped"]:
                errors.append(f"{where}: tier_swapped does not match tier_history")
            if e["patch_era"] == "current" and e.get("tier_history") and e["tier_history"][-1]["to"] != e["tier"]:
                errors.append(f"{where}: tier is {e['tier']} but last tier_history move is to "
                              f"{e['tier_history'][-1]['to']}")
        if use_rates and len(cur) == 4:
            for t in ("minor", "major"):
                s = sum(e.get("pick_rate") or 0 for e in cur if e["tier"] == t)
                if abs(s - 1) > 0.011:
                    errors.append(f"{slug}: {t} pick rates sum to {s:.2f}, expected 1.0")
    for slug, h in sorted(rh.items()):
        for folder in ("bans", "heroes"):
            p = root / "assets" / folder / f"{slug}.png"
            if not p.exists() and p not in staged:
                (errors if h["released"] else warnings).append(f"{h['name']}: missing assets/{folder}/{slug}.png")
    return errors, warnings


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="download icons and write the JSON files")
    ap.add_argument("--hero", action="append", default=[], metavar="SLUG", help="limit to these heroes (repeatable)")
    ap.add_argument("--refresh-rates", action="store_true",
                    help="refresh owperks pick rates for every live perk, not just pairs touched by a change")
    ap.add_argument("--check-only", action="store_true", help="offline consistency check, no network")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent,
                    help="repository root (default: this repo). Point at a copy to test.")
    args = ap.parse_args(argv)
    root = args.root.resolve()

    if args.check_only:
        perks = load_json(root / "reference" / "perks.json", None)
        roster = load_json(root / "reference" / "heroes.json", {"heroes": {}})
        errors, warnings = validate(root, perks, roster)
        for w in warnings:
            print("warning:", w)
        for e in errors:
            print("ERROR:", e)
        print(f"{len(errors)} error(s), {len(warnings)} warning(s)")
        return 2 if errors else 0

    s = Sync(root, args.apply, set(args.hero), args.refresh_rates)
    s.migrate()
    s.sync_roster()
    s.sync_hero_icons()
    s.sync_maps()
    s.sync_titles()
    s.sync_perks()
    # validation warnings are only missing icons of unreleased heroes, already reported by sync_hero_icons
    errors, _ = validate(root, s.perks, s.roster, s.staged)
    for e in errors:
        s.report.add("VALIDATION ERRORS", e)
    if args.apply:
        if s.report.lines["VALIDATION ERRORS"]:
            print("Validation failed; nothing written.\n")
        else:
            s.write()
    s.report.print(args.apply)
    if s.report.blocking:
        return 2
    return 1 if s.report.changed else 0


if __name__ == "__main__":
    sys.exit(main())
