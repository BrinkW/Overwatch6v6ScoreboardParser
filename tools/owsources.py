"""
Fetch and parse the two external sources behind reference/perks.json and
reference/heroes.json:

  - the Overwatch wiki at overwatch.weirdgloop.org (heroes, perks, tiers, change
    logs, icon files), with overwatch.fandom.com as a fallback for files only;
  - owperks.com (which 4 perks are live per hero, their slot order, and the
    community pick shares);
  - overwatch.blizzard.com hero pages (the official, current art and tier of
    every live perk).

Standard library only, apart from Pillow for checking image dimensions.
See docs/reference-sync.md for why each rule below exists.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
from PIL import Image

WIKI_API = "https://overwatch.weirdgloop.org/api.php"
FANDOM_API = "https://overwatch.fandom.com/api.php"
OWPERKS = "https://owperks.com"
# Fandom rejects non-browser user agents (HTTP 402), so both use a browser-like UA.
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0 "
                    "(OW6v6ScoreboardParser reference-sync)"}
OPP = {"minor": "major", "major": "minor"}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
_cache: dict[str, bytes] = {}


def _get(url: str, retries: int = 3) -> bytes:
    if url in _cache:
        return _cache[url]
    for attempt in range(retries):
        try:
            data = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()
            _cache[url] = data
            time.sleep(0.2)  # be polite to both sites
            return data
        except urllib.error.HTTPError as e:
            if e.code == 404 or attempt == retries - 1:
                raise
        except urllib.error.URLError:
            if attempt == retries - 1:
                raise
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def _api(base: str, **params) -> dict:
    params["format"] = "json"
    return json.loads(_get(base + "?" + urllib.parse.urlencode(params)))


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------
def loose(s: str) -> str:
    """Normalised key for matching names across sources ("Widow’s Bite" == "widows_bite",
    "Lúcio" == "lucio")."""
    import unicodedata
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower())


def snake(s: str) -> str:
    """File / slug form. Apostrophes become "_" to match the existing assets (viper_s_sting)."""
    return re.sub(r"[^a-z0-9]+", "_", s.lower().replace("’", "'").replace("'", "_")).strip("_")


def hero_slug(name: str) -> str:
    """"D.Mon" -> "d_mon", "Soldier: 76" -> "soldier_76", "Lúcio" -> "lucio"."""
    import unicodedata
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return snake(ascii_name)


# ---------------------------------------------------------------------------
# Wiki: heroes and pages
# ---------------------------------------------------------------------------
def wiki_hero_names() -> list[str]:
    """Released heroes: members of Category:Heroes (minus the 'Heroes' overview page)."""
    d = _api(WIKI_API, action="query", list="categorymembers", cmtitle="Category:Heroes",
             cmlimit=500, cmnamespace=0)
    return [m["title"] for m in d["query"]["categorymembers"] if m["title"] != "Heroes"]


def wiki_page(title: str) -> str | None:
    d = _api(WIKI_API, action="parse", page=title, prop="wikitext", redirects=1)
    return d["parse"]["wikitext"]["*"] if "parse" in d else None


def clean(s: str) -> str:
    """Strip wiki markup from a field value."""
    s = re.sub(r"\{\{al\|([^}|]+)(?:\|([^}]+))?\}\}", lambda m: m.group(2) or m.group(1), s)
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", s)
    s = re.sub(r"'''?", "", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\{\{[^}]*\}\}", "", s)
    return re.sub(r"\s+", " ", s).strip()


def infobox_role(text: str) -> tuple[str | None, str | None]:
    role = re.search(r"^\|\s*role\s*=\s*(.+)$", text, re.M)
    sub = re.search(r"^\|\s*sub-?role\s*=\s*(.+)$", text, re.M)
    norm = lambda m: clean(m.group(1)).lower() or None if m else None
    return norm(role), norm(sub)


def _boxes(text: str):
    """Yield (fields, position) for every {{Ability details}} / {{Ability_details}} template.
    Brace-balanced, because effect text often contains nested templates."""
    for m in re.finditer(r"\{\{\s*Ability[ _]details", text):
        j, depth = m.start(), 0
        while j < len(text):
            if text.startswith("{{", j):
                depth += 1; j += 2; continue
            if text.startswith("}}", j):
                depth -= 1; j += 2
                if depth == 0:
                    break
                continue
            j += 1
        fields = {}
        for part in re.split(r"\n\s*\|", "\n" + text[m.end():j - 2]):
            if "=" in part:
                k, v = part.split("=", 1)
                fields[k.strip()] = v.strip()
        yield fields, m.start()


REMOVED_SECTION = re.compile(r"=+\s*Removed (?:Perks|abilities)\s*=+", re.I)
READDED = re.compile(r"re-?added|reintroduc", re.I)


def wiki_perks(text: str) -> list[dict]:
    """Every perk box on a hero page, live or removed.

    `removed` is True when the box sits in a "Removed Perks/abilities" section or
    carries |removed= / a "Removed in ..." note, UNLESS the note says it was
    re-added (the wiki leaves re-added perks in the removed section)."""
    rsec = REMOVED_SECTION.search(text)
    out = []
    for f, pos in _boxes(text):
        typ = f.get("ability_type", "").lower()
        if "perk" not in typ:
            continue
        rem = re.search(r"''\s*(Removed in[^']+)''", f.get("ability_details", ""))
        note = clean(rem.group(1)) if rem else None
        removed = bool(f.get("removed")) or bool(rem) or bool(rsec and pos > rsec.start())
        if note and READDED.search(note):
            removed = False
        out.append({"name": clean(f.get("ability_name", "")),
                    "image": f.get("ability_image", "").strip(),
                    "tier": "major" if "major" in typ else "minor",
                    "effect": clean(f.get("official_description", "")),
                    "removed": removed,
                    "removed_note": note})
    return out


def wiki_abilities(text: str) -> list[str]:
    return [clean(f["ability_name"]) for f, _ in _boxes(text)
            if "perk" not in f.get("ability_type", "").lower() and f.get("ability_name")]


def tier_history(text: str, perk_name: str) -> list[dict]:
    """Chronological [{date, from, to}] tier moves for one perk, from the Balance Change Log.

    Change-log layout: {{PatchTableElement|YYYY-MM-DD| ... {{al|Perk}} - Minor Perk
    followed by bullets. "Moved from X to Y" is explicit; "Changed to a Y Perk" /
    "Moved to Y" imply it came from the opposite tier."""
    date, cur, out = None, None, []
    for line in text.split("\n"):
        m = re.search(r"PatchTableElement\|(\d{4}-\d{2}-\d{2})", line)
        if m:
            date, cur = m.group(1), None
        m = re.match(r"\s*\{\{al\|([^}|]+)(?:\|[^}]*)?\}\}", line)
        if m:
            cur = m.group(1).strip()
            continue
        if not (cur and loose(cur) == loose(perk_name) and line.strip().startswith("*")):
            continue
        low = line.lower()
        m = re.search(r"moved from (minor|major)[^.]*?to (minor|major)", low)
        if m:
            out.append({"date": date, "from": m.group(1), "to": m.group(2)})
            continue
        m = re.search(r"(?:changed|moved) to an? (minor|major)|moved to (minor|major)", low)
        if m:
            to = m.group(1) or m.group(2)
            out.append({"date": date, "from": OPP[to], "to": to})
    return sorted(out, key=lambda x: x["date"] or "")


_ABILITY_FALLBACK = [("scoped", "primary fire (scoped)"), ("primary fire", "primary fire"),
                     ("secondary fire", "secondary fire"), ("quick melee", "quick melee"),
                     ("double jump", "double jump"), ("ultimate", "ultimate"), ("wall rid", "wall ride"),
                     ("health regeneration", "passive health regeneration")]


def derive_ability(effect: str, abilities: list[str]) -> str | None:
    """Best-effort name of the ability a perk modifies, from its effect text."""
    low = effect.lower()
    found = []
    for n in sorted(set(abilities), key=len, reverse=True):
        i = low.find(n.lower())
        if i >= 0 and not any(n.lower() in f.lower() for _, f in found):
            found.append((i, n))
    if "health pack" in low:
        found.append((low.find("health pack"), "health pack"))
    if found:
        return " / ".join(n for _, n in sorted(found))
    return next((label for k, label in _ABILITY_FALLBACK if k in low), None)


# ---------------------------------------------------------------------------
# owperks
# ---------------------------------------------------------------------------
def owperks_hero_paths() -> list[str]:
    """e.g. ["tanks/dmon", "damages/soldier-76", "supports/doctrine", ...]."""
    h = _get(OWPERKS + "/en").decode("utf-8")
    return sorted(set(re.findall(r'href="/en/((?:tanks|damages|supports)/[a-z0-9_-]+)"', h)))


def owperks_hero(path: str) -> dict | None:
    """{"name": display name, "live": [(perk, tier, pick_share 0-1, description)]}.

    The page lists the 2 minor perks, then the 2 major perks; that order is how
    tiers are read. The percentages are community-reported shares, not observed
    pick rates (see docs/reference-sync.md)."""
    try:
        h = _get(f"{OWPERKS}/en/{path}").decode("utf-8")
    except urllib.error.HTTPError:
        return None
    title = re.search(r"<title>Best (.+?) Perks - ", h)
    live = []
    heads = list(re.finditer(r'<h2 class="font-semibold[^"]*">([^<]+)</h2>', h))
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else m.end() + 3000
        v = re.search(r'<span class="font-bold[^"]*">(\d+(?:\.\d+)?)%</span>', h[m.end():end])
        name = html.unescape(m.group(1))
        d = re.search(r'\\"name\\":\\"' + re.escape(name) + r'\\",\\"description\\":\\"(.*?)\\"\}', h)
        live.append((name, "minor" if i < 2 else "major",
                     round(float(v.group(1)) / 100, 2) if v else None,
                     html.unescape(d.group(1)) if d else None))
    return {"name": html.unescape(title.group(1)) if title else None, "live": live}


# ---------------------------------------------------------------------------
# Blizzard's official hero pages: current perk art and tiers
# ---------------------------------------------------------------------------
BLIZZARD = "https://overwatch.blizzard.com/en-us/heroes/"


def blizzard_hero_paths() -> list[str]:
    """Hero page slugs on overwatch.blizzard.com (released heroes only)."""
    h = _get(BLIZZARD).decode("utf-8")
    return sorted(set(re.findall(r'href="/heroes/([a-z0-9-]+)"', h)))


def blizzard_perks(path: str) -> list[dict] | None:
    """[{name, tier, url}] for the live perks on a hero's official page.

    The page's "Perks" section lists each perk as
    <div class="perk-details (left|right) (minor|major) N">...<img alt="Name" src="...">.
    The icons are 128 px white-on-transparent PNGs, the same format as the wiki's,
    and they carry the game's CURRENT art, which the wikis sometimes lack."""
    try:
        h = _get(BLIZZARD + path + "/").decode("utf-8")
    except urllib.error.HTTPError:
        return None
    found = re.findall(r'<div class="perk-details (?:left|right) (minor|major) \d+">'
                       r'<div class="perk-icon-wrapper[^"]*"><div class="perk-icon perk-details-icon">'
                       r'<img alt="([^"]+)" src="([^"]+)"', h)
    return [{"name": html.unescape(n), "tier": t, "url": u} for t, n, u in found]


def fetch(url: str) -> bytes:
    return _get(url)


def alpha_difference(a: bytes, b: bytes) -> float:
    """Mean absolute difference of two icons' alpha masks at 128x128, in [0, 1].
    Re-encodes of the same art score < 0.01; redrawn art scores > 0.03."""
    def mask(data):
        im = Image.open(io.BytesIO(data)).convert("RGBA").getchannel("A").resize((128, 128), Image.BILINEAR)
        return np.asarray(im, np.float32) / 255
    return float(np.abs(mask(a) - mask(b)).mean())


# ---------------------------------------------------------------------------
# Verified file downloads
# ---------------------------------------------------------------------------
def file_info(titles: list[str], api: str = WIKI_API) -> dict[str, dict]:
    """imageinfo (url, sha1, width, height, mime) for each existing File: title, keyed by the title as given."""
    out = {}
    for i in range(0, len(titles), 40):
        chunk = titles[i:i + 40]
        d = _api(api, action="query", titles="|".join(chunk), prop="imageinfo",
                 iiprop="url|sha1|size|mime")
        norm = {n["to"]: n["from"] for n in d["query"].get("normalized", [])}
        for p in d["query"]["pages"].values():
            if p.get("imageinfo"):
                out[norm.get(p["title"], p["title"])] = p["imageinfo"][0]
    return out


def download_verified(title: str) -> tuple[bytes, dict] | None:
    """Exact original bytes of a wiki file, SHA1-checked against imageinfo.

    weirdgloop: the imageinfo URL carries a ?cache-buster that returns a
    re-compressed PNG; `?format=original` returns the original (the bare URL is
    inconsistent at the CDN edge). Fandom: serves lossy WebP under .png unless
    `&format=original` is appended."""
    for api in (WIKI_API, FANDOM_API):
        info = file_info([title], api).get(title)
        if not info:
            continue
        base = info["url"].split("?")[0]
        candidates = ([base + "?format=original", base] if api == WIKI_API
                      else [info["url"] + ("&" if "?" in info["url"] else "?") + "format=original"])
        for url in candidates:
            try:
                data = _get(url)
            except urllib.error.HTTPError:
                continue
            if hashlib.sha1(data).hexdigest() == info["sha1"]:
                return data, info
    return None


def image_size(data: bytes) -> tuple[tuple[int, int], str]:
    im = Image.open(io.BytesIO(data))
    return im.size, im.format
