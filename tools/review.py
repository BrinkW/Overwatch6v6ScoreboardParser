"""
Review the parser's drafts of new screenshots and turn them into answer keys.

    python tools/review.py <zip | folder | image> ...   # import, then open the review page
    python tools/review.py                              # review what's already in the inbox
    python tools/review.py --stats                      # how the drafts compared with your corrections

Import copies each new capture into data/inbox/ (duplicates are skipped by
content hash, including captures already reviewed) and parses it into a draft
answer key. A capture tool zip also records its contributor (info.txt).

The review page (local only, http://127.0.0.1:<port>) shows the screenshot, a
zoomed crop of the field you are on, and every field in a form. Fields the
parser flagged are highlighted; closed sets (heroes, perks, maps, bans, ranks)
are dropdowns. Accept validates the key, writes it next to the capture in
data/reviewed/ (where tools/build_templates.py and tools/evaluate.py pick it
up) and logs which fields you changed to data/review_log.jsonl, so --stats
measures the parser on captures it had never seen. Reject moves a capture that
isn't a usable scoreboard to data/rejected/.

After a review session, rebuild the local templates (data/templates/) so the
parser learns from it:
    python tools/build_templates.py
Drafts made with older templates are re-parsed when opened.

data/ is git-ignored: contributors' screenshots stay on this machine.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import re
import shutil
import sys
import threading
import traceback
import webbrowser
import zipfile
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from PIL import Image  # noqa: E402

from build_templates import SAMPLES  # noqa: E402
from src import layout as L, text as T  # noqa: E402
from src.header import TIERS  # noqa: E402
from src.parse import STATS, Models, load_rgb, parse, template_dir  # noqa: E402

DATA = ROOT / "data"
INBOX, REVIEWED, REJECTED = DATA / "inbox", DATA / "reviewed", DATA / "rejected"
LOG = DATA / "review_log.jsonl"
PAGE = Path(__file__).with_name("review_page.html")
IMAGE_EXT = {".png", ".jpg", ".jpeg"}
ROW_KEYS = ["team", "player", "title", "hero", "role", "perks"] + STATS
TODAY = datetime.date.today().isoformat()


def load_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
def images_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.glob("*") if p.suffix.lower() in IMAGE_EXT) if folder.exists() else []


def known_hashes() -> set[str]:
    return {hashlib.sha1(p.read_bytes()).hexdigest()
            for folder in (INBOX, REVIEWED, REJECTED, SAMPLES) for p in images_in(folder)}


def unique_name(name: str) -> str:
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    taken = {p.name for folder in (INBOX, REVIEWED, REJECTED) for p in images_in(folder)}
    out, n = f"{stem}{ext}", 2
    while out in taken:
        out, n = f"{stem}_{n}{ext}", n + 1
    return out


def read_sources(paths: list[Path]):
    """(file name, bytes, source info) for every image in the given zips, folders and files."""
    for path in paths:
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as z:
                info = {}
                if "info.txt" in z.namelist():
                    try:
                        info = json.loads(z.read("info.txt").decode("utf-8"))
                    except ValueError:
                        pass
                source = {"zip": path.name, "contributor": info.get("contributor") or info.get("name")}
                for m in z.namelist():
                    if Path(m).suffix.lower() in IMAGE_EXT and not m.endswith("/"):
                        yield Path(m).name, z.read(m), source
        elif path.is_dir():
            for p in sorted(path.rglob("*")):
                if p.suffix.lower() in IMAGE_EXT:
                    yield p.name, p.read_bytes(), {"folder": str(path)}
        elif path.suffix.lower() in IMAGE_EXT:
            yield path.name, path.read_bytes(), {"file": path.name}
        else:
            print(f"  skipped {path}: not a zip, folder or image")


def import_sources(paths: list[Path], store: "Store") -> list[str]:
    INBOX.mkdir(parents=True, exist_ok=True)
    seen, new, dupes = known_hashes(), [], 0
    for name, data, source in read_sources(paths):
        h = hashlib.sha1(data).hexdigest()
        if h in seen:
            dupes += 1
            continue
        seen.add(h)
        dest = INBOX / unique_name(name)
        dest.write_bytes(data)
        new.append(dest.name)
        print(f"  parsing {dest.name} ...", flush=True)
        store.redraft(dest.name, source={**source, "imported": TODAY, "sha1": h})
    print(f"Imported {len(new)} new capture(s)" + (f"; {dupes} already known, skipped" if dupes else ""))
    return new


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------
def templates_stamp() -> float:
    return max((p.stat().st_mtime for p in template_dir().glob("*") if p.is_file()), default=0.0)


class Store:
    """The inbox: captures, their drafts and the parser models (loaded once)."""

    def __init__(self):
        self.lock = threading.Lock()
        self._models, self._models_stamp = None, None
        self._images: dict[str, Image.Image] = {}

    @property
    def models(self) -> Models:
        """The parser models, reloaded whenever the templates have been rebuilt."""
        stamp = templates_stamp()
        if self._models is None or self._models_stamp != stamp:
            self._models, self._models_stamp = Models.load(), stamp
        return self._models

    def draft_path(self, name: str) -> Path:
        return INBOX / (Path(name).stem + ".draft.json")

    def redraft(self, name: str, source=None) -> dict:
        path = INBOX / name
        old = load_json(self.draft_path(name), {})
        rgb = load_rgb(path)
        draft = {"image": name, "header": {"mode": None, "map": None, "time": None, "bans": [None] * 4,
                                           "rank_range": [None, None]},
                 "rows": [], "_source": source or old.get("_source"), "_size": [rgb.shape[1], rgb.shape[0]],
                 "_templates": templates_stamp()}
        try:
            with self.lock:
                res = parse(rgb, self.models)
                lay = L.detect(rgb)
            draft["header"] = {k: res["header"][k] for k in ("mode", "map", "time", "bans", "rank_range")}
            draft["rows"] = [{k: r[k] for k in ROW_KEYS} for r in res["rows"]]
            draft["_review"], draft["_problems"] = res["review"], res["problems"]
            draft["_raw"] = res["header"].get("raw")
            draft["_margins"] = {"header": res["header_margin"], "rows": [r["margin"] for r in res["rows"]]}
            draft["_notes"] = [{"name": r.get("text_evidence", {}).get("name"),
                                "title": r.get("text_evidence", {}).get("title"),
                                "hero": r.get("hero_flags")} for r in res["rows"]]
            draft["_boxes"] = {"header": {k: [int(v) for v in b] for k, b in lay.header.items()},
                               "rows": [{k: [int(v) for v in b] for k, b in r.rois.items()} for r in lay.rows]}
            hb = draft["_boxes"]["header"]
            if "ban_3" in hb and "ban_2" in hb and "ban_4" not in hb:   # where a 5th slot would be, for its crop
                step = hb["ban_3"][0] - hb["ban_2"][0]
                hb["ban_4"] = [hb["ban_3"][0] + step, hb["ban_3"][1], hb["ban_3"][2] + step, hb["ban_3"][3]]
        except Exception as e:     # a capture the parser can't handle is still reviewable (or rejectable)
            draft["_error"] = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        self.draft_path(name).write_text(dump(draft) + "\n", encoding="utf-8")
        return draft

    def draft(self, name: str) -> dict:
        d = load_json(self.draft_path(name))
        if d is None or d.get("_templates") != templates_stamp():
            d = self.redraft(name)
        return d

    def items(self) -> list[dict]:
        out = []
        for p in images_in(INBOX):
            d = load_json(self.draft_path(p.name), {})
            out.append({"name": p.name, "flagged": len(d.get("_review", [])),
                        "problems": len(d.get("_problems", [])), "error": d.get("_error"),
                        "stale": d.get("_templates") != templates_stamp()})
        return out

    def image(self, name: str) -> Image.Image:
        if name not in self._images:
            if len(self._images) > 8:
                self._images.pop(next(iter(self._images)))
            self._images[name] = Image.open(INBOX / name).convert("RGB")
        return self._images[name]

    def crop_png(self, name: str, box: list[int]) -> bytes:
        im = self.image(name)
        x0, y0, x1, y1 = box
        pad = 6
        c = im.crop((max(0, x0 - pad), max(0, y0 - pad), min(im.width, x1 + pad), min(im.height, y1 + pad)))
        z = max(2, min(6, 150 // max(1, c.height)))
        buf = io.BytesIO()
        c.resize((c.width * z, c.height * z), Image.LANCZOS).save(buf, "PNG")
        return buf.getvalue()


# ---------------------------------------------------------------------------
# Reference options and validation
# ---------------------------------------------------------------------------
def options() -> dict:
    roster = load_json(ROOT / "reference" / "heroes.json")["heroes"]
    perks = load_json(ROOT / "reference" / "perks.json")["heroes"]
    maps = load_json(ROOT / "reference" / "maps.json", {"maps": []})["maps"]
    known = load_json(template_dir() / "known_text.json", {"players": [], "titles": []})
    heroes = sorted(({"slug": s, "name": h["name"], "role": h["role"]} for s, h in roster.items()),
                    key=lambda h: h["name"].lower())
    # a player who has just swapped hero: "?" portrait, normally no role icon and no perks
    heroes.append({"slug": "mystery", "name": "Mystery (swapped hero)", "role": None})
    return {
        "heroes": heroes,
        "perks": {s: [{"name": e["name"], "tier": e["tier"], "era": e["patch_era"], "icon": e.get("icon")}
                      for e in h["perks"]] for s, h in perks.items()},
        "maps": [{"name": m["name"], "mode": m["mode"]} for m in maps],
        "titles": sorted(set(T.reference_titles()) | set(known["titles"]), key=str.lower),
        "players": known["players"],
        "tiers": TIERS,
        "stats": STATS,
    }


def validate(key: dict, opts: dict) -> list[str]:
    """Problems that must be fixed before a key is accepted."""
    errs = []
    heroes = {h["slug"]: h for h in opts["heroes"]}
    maps = {m["name"]: m["mode"] for m in opts["maps"]}
    h = key.get("header", {})
    if h.get("map") is not None and h["map"] not in maps:
        errs.append(f"map {h['map']!r} is not in maps.json")
    if h.get("mode") is not None and h["mode"] not in set(maps.values()):
        errs.append(f"mode {h['mode']!r} is not a known mode")
    if h.get("map") in maps and h.get("mode") and maps[h["map"]] != h["mode"]:
        errs.append(f"{h['map']} is a {maps[h['map']]} map, not {h['mode']}")
    if h.get("time") is not None and not re.fullmatch(r"\d{1,2}:[0-5]\d", h["time"]):
        errs.append(f"time {h['time']!r} is not m:ss")
    bans = h.get("bans") or []
    if len(bans) not in (4, 5):
        errs.append(f"there must be 4 or 5 ban slots, not {len(bans)}")
    for i, b in enumerate(bans):
        if b not in (None, "none") and (b not in heroes or b == "mystery"):
            errs.append(f"ban {i + 1}: unknown hero {b!r}")
    for i, r in enumerate(h.get("rank_range") or [None, None]):
        if r is not None and not re.fullmatch(rf"({'|'.join(opts['tiers'])}) [1-5]", r):
            errs.append(f"rank {'low' if i == 0 else 'high'} {r!r} is not '<Tier> <1-5>'")
    rows = key.get("rows", [])
    teams = [r.get("team") for r in rows]
    if len(rows) not in (10, 12) or teams.count("top") != teams.count("bottom"):
        errs.append(f"expected 5 or 6 rows per team, got {teams.count('top')} and {teams.count('bottom')}")
    tanks = defaultdict(int)
    for i, r in enumerate(rows):
        where = f"{r.get('team')} row {i % (len(rows) // 2 or 1) + 1}"
        if r.get("hero") is not None and r["hero"] not in heroes:
            errs.append(f"{where}: unknown hero {r['hero']!r}")
        if r.get("role") not in (None, "tank", "damage", "support"):
            errs.append(f"{where}: role {r.get('role')!r}")
        if r.get("hero") == "mystery" and any(p not in (None, "none") for p in r.get("perks") or []):
            errs.append(f"{where}: a mystery portrait can't have identified perks")
        tanks[r.get("team")] += r.get("role") == "tank"
        names = {p["name"] for p in opts["perks"].get(r.get("hero"), [])}
        perks = r.get("perks") or []
        if len(perks) != 2:
            errs.append(f"{where}: needs two perk slots")
        for p in perks:
            if p not in (None, "none") and not all(x in names for x in p.split("|")):
                errs.append(f"{where}: {p!r} is not a perk of {r.get('hero')}")
        for c in opts["stats"]:
            v = r.get(c)
            if v is not None and (not isinstance(v, int) or v < 0):
                errs.append(f"{where}: {c} must be a whole number")
        for f in ("player", "title"):
            if r.get(f) is not None and (not isinstance(r[f], str) or not r[f].strip()):
                errs.append(f"{where}: empty {f} (leave it blank for none)")
    for team, n in tanks.items():
        if n > 2:
            errs.append(f"{team} team has {n} tanks; at most 2 are allowed")
    return errs


# ---------------------------------------------------------------------------
# Accept / reject / log
# ---------------------------------------------------------------------------
def flatten(key: dict) -> dict[str, object]:
    """Every scored field of an answer key, by the name the review flags use."""
    out = {}
    h = key.get("header", {})
    for f in ("mode", "map", "time"):
        out[f"header.{f}"] = h.get(f)
    for i, b in enumerate(h.get("bans") or []):
        out[f"header.ban_{i}"] = b
    for k, r in zip(("rank_low", "rank_high"), h.get("rank_range") or []):
        out[f"header.{k}"] = r
    counters = defaultdict(int)
    for r in key.get("rows", []):
        where = f"{r['team']}{counters[r['team']]}"
        counters[r["team"]] += 1
        for f in ("player", "title", "hero", "role") + tuple(STATS):
            out[f"{where}.{f}"] = r.get(f)
        for side, p in zip(("perk_left", "perk_right"), r.get("perks") or []):
            out[f"{where}.{side}"] = p
    return out


def flagged_fields(review: list[str]) -> set[str]:
    """Review flags mapped onto flatten()'s field names."""
    out = set()
    for f in review:
        if f.endswith(".perk"):
            out |= {f + "_left", f + "_right"}
        elif f.startswith("header.rank_"):
            out.add(f.rsplit(".", 1)[0])
        elif f == "header.map":
            out |= {"header.map", "header.mode"}
        else:
            out.add(f)
    return out


def kind(field: str) -> str:
    f = field.split(".", 1)[1]
    if f.startswith("ban_"):
        return "ban"
    return {"rank_low": "rank", "rank_high": "rank", "perk_left": "perk", "perk_right": "perk"}.get(f, f)


def same(draft, final) -> bool:
    """A draft value is right if it equals the final one, or is one of its "A|B"
    alternatives (two perks drawn with the identical icon)."""
    return draft == final or (isinstance(final, str) and isinstance(draft, str) and draft in final.split("|"))


def accept(name: str, key: dict, store: Store) -> list[str]:
    errs = validate(key, options())
    if errs:
        return errs
    draft = store.draft(name)
    REVIEWED.mkdir(parents=True, exist_ok=True)
    final = {"image": name, "verified": True,
             "note": f"Reviewed with tools/review.py on {TODAY}, starting from the parser's draft. "
                     "null = unknown (never scored), except title: null = no title. "
                     "perks: \"none\" = empty slot, null = present but not identified.",
             "source": draft.get("_source"),
             "header": key["header"], "rows": [{k: r.get(k) for k in ROW_KEYS} for r in key["rows"]]}
    (REVIEWED / (Path(name).stem + ".json")).write_text(dump(final) + "\n", encoding="utf-8")
    shutil.move(str(INBOX / name), REVIEWED / name)
    store.draft_path(name).unlink(missing_ok=True)
    store._images.pop(name, None)
    before, after = flatten(draft), flatten(final)
    flags = flagged_fields(draft.get("_review", []))
    entry = {"image": name, "date": TODAY, "templates": draft.get("_templates"), "error": draft.get("_error"),
             "fields": sorted(after), "flagged": sorted(flags & set(after)),
             "changed": [{"field": f, "draft": before.get(f), "final": v, "flagged": f in flags}
                         for f, v in after.items() if not same(before.get(f), v)]}
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return []


def reject(name: str, reason: str, store: Store):
    REJECTED.mkdir(parents=True, exist_ok=True)
    shutil.move(str(INBOX / name), REJECTED / name)
    (REJECTED / (Path(name).stem + ".reason.txt")).write_text(reason + "\n", encoding="utf-8")
    store.draft_path(name).unlink(missing_ok=True)
    store._images.pop(name, None)


def stats() -> int:
    entries = [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines()] if LOG.exists() else []
    if not entries:
        print("No reviews logged yet (data/review_log.jsonl).")
        return 0
    per = defaultdict(lambda: [0, 0, 0, 0])      # fields, changed, flagged, changed & flagged
    for e in entries:
        changed = {c["field"]: c for c in e["changed"]}
        flagged = set(e["flagged"])
        fields = e["fields"]
        for f in fields:
            k = kind(f)
            per[k][0] += 1
            per[k][1] += f in changed
            per[k][2] += f in flagged
            per[k][3] += f in changed and f in flagged
    print(f"Parser drafts vs your corrections ({len(entries)} reviewed captures, never seen by the parser)\n")
    print(f"  {'field':8} {'right':>18} {'wrong':>6} {'flagged':>8} {'wrong+flagged':>14}")
    tot = [0, 0, 0, 0]
    for k, (n, ch, fl, both) in sorted(per.items()):
        tot = [a + b for a, b in zip(tot, (n, ch, fl, both))]
        print(f"  {k:8} {n - ch:5}/{n:<5} {100 * (n - ch) / n:5.1f}% {ch:5} {fl:8} {both:8}")
    n, ch, fl, both = tot
    print(f"  {'ALL':8} {n - ch:5}/{n:<5} {100 * (n - ch) / n:5.1f}% {ch:5} {fl:8} {both:8}")
    if ch:
        print(f"\n{both}/{ch} wrong fields were flagged; {fl - both}/{fl} flags were on fields that were right.")
    return 0


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
def make_handler(store: Store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, obj, code: int = 200):
            self.send(code, dump(obj).encode("utf-8"), "application/json; charset=utf-8")

        def inbox_name(self, raw: str) -> str | None:
            name = Path(unquote(raw)).name
            return name if (INBOX / name).is_file() else None

        def do_GET(self):
            u = urlparse(self.path)
            parts = [p for p in u.path.split("/") if p]
            try:
                if not parts:
                    return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                if parts == ["api", "items"]:
                    return self.send_json({"items": store.items(),
                                           "reviewed": len(images_in(REVIEWED)), "rejected": len(images_in(REJECTED))})
                if parts == ["api", "options"]:
                    return self.send_json(options())
                if len(parts) == 3 and parts[:2] == ["api", "draft"] and self.inbox_name(parts[2]):
                    return self.send_json(store.draft(self.inbox_name(parts[2])))
                if len(parts) == 2 and parts[0] == "image" and self.inbox_name(parts[1]):
                    name = self.inbox_name(parts[1])
                    return self.send(200, (INBOX / name).read_bytes(),
                                     "image/png" if name.lower().endswith(".png") else "image/jpeg")
                if len(parts) == 2 and parts[0] == "crop" and self.inbox_name(parts[1]):
                    box = [int(v) for v in parse_qs(u.query)["box"][0].split(",")]
                    return self.send(200, store.crop_png(self.inbox_name(parts[1]), box), "image/png")
                if parts[0] == "asset":
                    path = (ROOT / "assets" / "/".join(unquote(p) for p in parts[1:])).resolve()
                    if (ROOT / "assets").resolve() in path.parents and path.is_file():
                        return self.send(200, path.read_bytes(), "image/png")
                self.send(404, b"not found", "text/plain")
            except Exception as e:
                traceback.print_exc()
                self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)

        def do_POST(self):
            parts = [p for p in urlparse(self.path).path.split("/") if p]
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            try:
                if len(parts) == 3 and parts[:2] == ["api", "accept"] and self.inbox_name(parts[2]):
                    errs = accept(self.inbox_name(parts[2]), body, store)
                    return self.send_json({"ok": not errs, "errors": errs}, 200 if not errs else 400)
                if len(parts) == 3 and parts[:2] == ["api", "reject"] and self.inbox_name(parts[2]):
                    reject(self.inbox_name(parts[2]), body.get("reason") or "rejected", store)
                    return self.send_json({"ok": True})
                if len(parts) == 3 and parts[:2] == ["api", "redraft"] and self.inbox_name(parts[2]):
                    return self.send_json(store.redraft(self.inbox_name(parts[2])))
                self.send(404, b"not found", "text/plain")
            except Exception as e:
                traceback.print_exc()
                self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    return Handler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Review parser drafts of new screenshots.")
    ap.add_argument("sources", nargs="*", type=Path, help="capture tool zips, folders or images to import")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--stats", action="store_true", help="print draft accuracy from the review log and exit")
    args = ap.parse_args(argv)
    if args.stats:
        return stats()
    store = Store()
    if args.sources:
        import_sources(args.sources, store)
    pending = images_in(INBOX)
    if not pending:
        print("The inbox is empty. Import captures with: python tools/review.py <zip | folder | image> ...")
        return 0
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(store))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"{len(pending)} capture(s) to review at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    print("Stopped. Rebuild the templates to learn from what you reviewed: python tools/build_templates.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
