"""
Scoreboard layout: find the table and the header, and express every region of
interest (ROI) relative to what it is attached to on screen.

Two UIs exist: "classic", and "new" (late 2026; often, but not always, with a
HERO INFO | SCOREBOARD tab strip above it). They differ in table size, table
position, the name area's width and the header's placement; either can show 4
or 5 ban slots. Nothing here branches on the UI: every element is anchored to
what it is attached to on screen, so both are handled by one code path (see
CLAUDE.md "Layout: anchor, don't hardcode"). The UI is only reported, from the
table's own proportions (the name area's width), never from the tab strip.

Table anchors:
  - the white column-header bar: its LEFT edge, top and height. Its right edge
    is unreliable (it blends into other bright UI in some captures), so column
    positions come from the dark E/A/D/DMG/H/MIT labels printed inside it;
  - left side of a row (role, portrait, ult, start of the name): the bar's
    left edge;
  - right side (perk slots, end of the name, stats): the E column. The name
    area between them is wider in the new UI (15.6h vs 14.7h);
  - the two saturated team blocks below the bar (split by the "VS" gap), and
    the thin dark separator lines between rows, used to fit the row count.
Row geometry is in units of the bar height `h` (39 px in the classic UI at
2560x1440, 34 px in the new one), so it survives rescaled captures. Rows
are labelled top/bottom, never by colour: team colours change with the
colour-blind setting.

Header anchors (the header is pinned to the screen, not to the table):
  - bans: the red ban icon at the top left; ban slots follow it at a fixed
    pitch, 4 or 5 of them, each a red frame (or grey when a team didn't ban);
  - mode, map and match time: the orange time digits at the top right;
  - rank range: the white dash between the two rank emblems.
Header geometry is in units of the ban icon's diameter (41 px at 1440p).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw

BAR_H_REF = 39  # px, classic UI at 2560x1440
CLASSIC_BAR_TO_E = (965 - 393) / BAR_H_REF   # h from the bar's left edge to the E column (classic UI)

# Horizontal row ROIs, (x0, x1) in h units from the bar's left edge.
ROW_X = {
    "role":      (0.05, 0.85),
    "portrait":  (0.90, 2.85),
    "ult":       (3.00, 4.20),    # top (friendly) team only
}
# ... and from the E column (negative = left of it): identical in both UIs.
ROW_X_FROM_E = {
    "perk_left": (10.12 - CLASSIC_BAR_TO_E, 11.60 - CLASSIC_BAR_TO_E),
    "perk_right": (12.04 - CLASSIC_BAR_TO_E, 13.52 - CLASSIC_BAR_TO_E),
}
NAME_START = {"top": 4.35, "bottom": 2.90}      # from the bar edge; the bottom team has no ult column
NAME_END_FROM_E = 10.05 - CLASSIC_BAR_TO_E      # the name and title end just before the left perk slot
# Vertical row ROIs: (y0, y1) as fractions of the row pitch.
ROW_Y = {
    "role": (0.25, 0.75), "portrait": (0.02, 0.98), "ult": (0.20, 0.80),
    "name": (0.06, 0.74), "title": (0.66, 1.06),   # a little past the row: descenders (the next name starts at ~0.25)
    "perk_left": (0.14, 0.86), "perk_right": (0.14, 0.86),
    "stat": (0.30, 0.76),
}
STAT_COLS = ["E", "A", "D", "DMG", "H", "MIT"]
STAT_HALF_W = {"E": 0.85, "A": 0.85, "D": 0.85, "DMG": 1.55, "H": 1.55, "MIT": 1.55}

# Header geometry, in px at 1440p; scaled by the found ban icon (41 px across)
# for the bans, and by the time digits' height (32 px) for everything else.
BAN_ICON_REF, TIME_DIGIT_REF = 41, 32
BAN_FIRST_DX, BAN_PITCH = 65, 83        # first slot's left edge from the icon's left edge; slot pitch
BAN_DY, BAN_W, BAN_H = -14, 70, 68      # slot box relative to the icon's top edge
# A slot's 4-px border is frame (red, or grey for a team that didn't ban) on every
# side: >= 0.29 per side on real slots, 0.0 where there is no slot. Its inside is
# mostly not frame-coloured (<= 0.35): that rejects a bright grey scene behind an
# empty 5th position, which would be "frame" everywhere.
BAN_SIDE_MIN, BAN_INNER_MAX = 0.2, 0.5
MAX_BANS = 5
# "MODE | MAP  TIME: mm:ss" is one right-aligned group whose left end moves with
# the text length, so it is read as one strip (split on the orange time digits).
STRIP_FROM_TIME = (-628, -13, 20, 9)    # (left of right edge, above top, right of right edge, below bottom)
# The rank boxes, from the dash between the two emblems: (x0, y0, x1, y1) from its
# left/top edge. Wide enough for Grandmaster's wings (narrower boxes clipped 8 of
# 26 emblems); the dash itself is colourless, so the emblem mask ignores it.
RANK_FROM_DASH = {"rank_low": (-128, -49, 6, 49), "rank_high": (10, -49, 156, 49)}


def components(mask: np.ndarray, min_px: int = 1) -> list[tuple[np.ndarray, np.ndarray]]:
    """(ys, xs) of every 8-connected component of at least `min_px` pixels."""
    H, W = mask.shape
    seen = np.zeros_like(mask, bool)
    out = []
    for y0, x0 in zip(*np.where(mask)):
        if seen[y0, x0]:
            continue
        stack, cy, cx = [(y0, x0)], [], []
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            cy.append(y)
            cx.append(x)
            for ny in (y - 1, y, y + 1):
                for nx in (x - 1, x, x + 1):
                    if 0 <= ny < H and 0 <= nx < W and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        if len(cy) >= min_px:
            out.append((np.array(cy), np.array(cx)))
    return out


@dataclass
class Row:
    team: str   # "top" | "bottom"
    index: int  # 0-based within the team
    y0: int
    y1: int
    rois: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)


@dataclass
class Layout:
    x0: int       # bar left
    y0: int       # bar top
    h: float      # bar height (scale unit)
    columns: dict[str, float]            # stat column centres (x px)
    teams: dict[str, tuple[int, int]]    # team block (y0, y1)
    rows: list[Row]
    header: dict[str, tuple[int, int, int, int]]
    ui: str = "classic"   # "classic" or "new", from the table's proportions (UI_NAME_AREA)

    @property
    def scale(self) -> float:
        return self.h / BAR_H_REF


def _runs(mask) -> list[tuple[int, int]]:
    """Inclusive (start, end) index runs where mask is True."""
    m = np.concatenate([[False], np.asarray(mask, bool), [False]])
    d = np.diff(m.astype(np.int8))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0] - 1))


class LayoutError(RuntimeError):
    pass


def find_bar(rgb: np.ndarray) -> tuple[int, int, int, int]:
    """(x0, y0, h, x1_approx) of the white column-header bar."""
    a = rgb.astype(np.int16)
    mx, mn = a.max(2), a.min(2)
    white = ((mx - mn) < 25) & (mn > 200)
    H, W = white.shape
    wide = white.sum(1) > W * 0.25
    runs = [(s, e) for s, e in _runs(wide) if e - s >= 8]
    if not runs:
        raise LayoutError("no column-header bar found")
    s, e = max(runs, key=lambda r: r[1] - r[0])
    ymid = (s + e) // 2
    xr = max(_runs(white[ymid]), key=lambda r: r[1] - r[0])
    return int(xr[0]), int(s), int(e - s + 1), int(xr[1])


def find_columns(rgb, x0, y0, h) -> dict[str, float]:
    """Centres of the E A D DMG H MIT labels (dark text inside the bar)."""
    a = rgb[y0 + 2:y0 + h - 2, x0:x0 + int(32 * h)].astype(np.int16)
    dark = a.max(2) < 110
    cols = dark.mean(0) > 0.02
    groups = []
    for s, e in _runs(cols):
        if groups and s - groups[-1][1] <= 0.3 * h:
            groups[-1][1] = e
        else:
            groups.append([s, e])
    groups = [g for g in groups if g[0] > 12 * h]  # labels sit right of the perk columns
    if len(groups) < 6:
        raise LayoutError(f"found {len(groups)} header labels, expected 6")
    return {name: x0 + (g[0] + g[1]) / 2 for name, g in zip(STAT_COLS, groups[:6])}


def find_teams(rgb, y0, h, columns) -> dict[str, tuple[int, int]]:
    """Top and bottom team blocks: saturated runs in the stats area below the bar."""
    a = rgb.astype(np.int16)
    xs = slice(int(columns["E"] - h), int(columns["MIT"] + h))
    chroma = (a[:, xs].max(2) - a[:, xs].min(2)).mean(1)
    start = int(y0 + h)
    blocks = [(start + s, start + e) for s, e in _runs(chroma[start:] > 30) if e - s > 2 * h]
    if len(blocks) < 2:
        raise LayoutError(f"found {len(blocks)} team blocks, expected 2")
    return {"top": blocks[0], "bottom": blocks[1]}


def fit_rows(rgb, block, h, columns) -> list[tuple[int, int]]:
    """Split a team block into rows, choosing the row count whose evenly spaced
    boundaries best line up with the dark separator lines."""
    y0, y1 = block
    a = rgb[y0:y1 + 1].astype(np.int16)
    xs = slice(int(columns["E"] - h), int(columns["MIT"] + h))
    v = a[:, xs].max(2).mean(1)
    k = max(3, int(h / 3))
    base = np.array([np.median(v[max(0, i - k):i + k + 1]) for i in range(len(v))])
    dip = np.clip(base - v, 0, None)
    H = y1 - y0 + 1
    best, best_n = -1.0, None
    for n in (4, 5, 6, 7):
        pitch = H / n
        if not 1.4 * h <= pitch <= 2.6 * h:
            continue
        r = max(2, int(0.08 * pitch))
        score = np.mean([dip[int(i * pitch) - r:int(i * pitch) + r + 1].max() for i in range(1, n)])
        if score > best:
            best, best_n = score, n
    if best_n is None:
        raise LayoutError(f"team block of {H}px fits no row count")
    pitch = H / best_n
    return [(int(round(y0 + i * pitch)), int(round(y0 + (i + 1) * pitch)) - 1) for i in range(best_n)]


E_TO_MIT_REF = 1438 - 965  # px between the E and MIT label centres, classic UI at 2560x1440
# Bar edge to E column, in h: 14.67 in the classic UI, 15.63 in the new one (a wider
# name area). This tells the UIs apart whether or not the new UI's tab strip is shown.
UI_NAME_AREA = 15.15


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
def _red(a):
    return (a[..., 0] > 170) & (a[..., 1] < 80) & (a[..., 2] < 80)


def find_ban_icon(rgb: np.ndarray) -> tuple[int, int, int] | None:
    """(x, y, diameter) of the red ban icon left of the ban slots: the leftmost
    roughly square red component in the top-left of the screen."""
    H, W = rgb.shape[:2]
    top = rgb[:int(0.15 * H), :int(0.6 * W)].astype(np.int16)
    cands = []
    for ys, xs in components(_red(top), 30):
        w, h = np.ptp(xs) + 1, np.ptp(ys) + 1
        if h >= 0.01 * H and abs(w - h) <= 0.15 * max(w, h):
            cands.append((int(xs.min()), int(ys.min()), int(w)))
    return min(cands) if cands else None


def is_ban_slot(rgb: np.ndarray, box) -> bool:
    """A ban slot's frame: red (a ban) or grey (no ban) on all four sides of the
    box, around an inside that isn't frame-coloured."""
    t = crop(rgb, box).astype(np.int16)
    if t.shape[0] < 24 or t.shape[1] < 24:
        return False
    mx, mn = t.max(2), t.min(2)
    frame = ((t[..., 0] > 150) & (t[..., 1] < 90) & (t[..., 2] < 90)) | (((mx - mn) < 30) & (mx > 110))
    sides = (frame[:4], frame[-4:], frame[:, :4], frame[:, -4:])
    return min(float(sd.mean()) for sd in sides) >= BAN_SIDE_MIN and float(frame[10:-10, 10:-10].mean()) <= BAN_INNER_MAX


def find_bans(rgb: np.ndarray, icon) -> list[tuple[int, int, int, int]]:
    """Ban slot boxes, left to right: slots follow the ban icon at a fixed pitch
    until a position has no frame (4 or 5 slots)."""
    x, y, d = icon
    s = d / BAN_ICON_REF
    boxes = []
    for k in range(MAX_BANS):
        bx, by = round(x + (BAN_FIRST_DX + BAN_PITCH * k) * s), round(y + BAN_DY * s)
        box = (bx, by, bx + round(BAN_W * s), by + round(BAN_H * s))
        if not is_ban_slot(rgb, box):
            break
        boxes.append(box)
    return boxes


def find_time(rgb: np.ndarray) -> tuple[int, int, int, int] | None:
    """Bounding box of the orange match-time digits at the top right."""
    H, W = rgb.shape[:2]
    a = rgb[:int(0.15 * H), W // 2:].astype(np.int16)
    orange = (a[..., 0] > 200) & (a[..., 1] > 60) & (a[..., 1] < 180) & (a[..., 2] < 90)
    cs = [c for c in components(orange, 15)]
    if not cs:
        return None
    tallest = max(np.ptp(ys) for ys, _ in cs)
    digits = [(ys, xs) for ys, xs in cs if np.ptp(ys) >= 0.5 * tallest]
    ys = np.concatenate([c[0] for c in digits])
    xs = np.concatenate([c[1] for c in digits]) + W // 2
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def find_rank_dash(rgb: np.ndarray, time_box) -> tuple[int, int] | None:
    """(left, top) of the white dash between the two rank emblems: a short, wide,
    solid white bar below the time with emblem metal on both sides (highlights on
    the emblems themselves are white too, but sparse)."""
    _, _, xr, yb = time_box
    s = (time_box[3] - time_box[1] + 1) / TIME_DIGIT_REF
    x0, y0 = max(0, int(xr - 260 * s)), int(yb)
    a = rgb[y0:int(yb + 160 * s), x0:int(xr + 60 * s)].astype(np.int16)
    mx, mn = a.max(2), a.min(2)
    metal = (mx > 150) & ((mx - mn) > 35)
    reach = int(60 * s)
    for ys, xs in components(mn > 200, 10):
        w, h = np.ptp(xs) + 1, np.ptp(ys) + 1
        if not (12 * s <= w <= 40 * s and h <= 12 * s) or len(ys) < 0.8 * w * h:   # a solid bar
            continue
        band = slice(max(0, ys.min() - reach), ys.max() + reach)
        if metal[band, max(0, xs.min() - reach):xs.min()].any() and metal[band, xs.max() + 1:xs.max() + 1 + reach].any():
            return x0 + int(xs.min()), y0 + int(ys.min())
    return None


def find_header(rgb: np.ndarray) -> dict[str, tuple[int, int, int, int]]:
    """{roi: box}. Missing elements are simply absent."""
    header = {}
    icon = find_ban_icon(rgb)
    if icon is not None:
        for k, box in enumerate(find_bans(rgb, icon)):
            header[f"ban_{k}"] = box
    t = find_time(rgb)
    if t is not None:
        xl, yt, xr, yb = t
        s = (yb - yt + 1) / TIME_DIGIT_REF
        dx0, dy0, dx1, dy1 = STRIP_FROM_TIME
        header["mode_map_time"] = (int(xr + dx0 * s), int(yt + dy0 * s), int(xr + dx1 * s), int(yb + dy1 * s))
        dash = find_rank_dash(rgb, t)
        if dash is not None:
            for key, (a, b, c, d) in RANK_FROM_DASH.items():
                header[key] = (int(dash[0] + a * s), int(dash[1] + b * s), int(dash[0] + c * s), int(dash[1] + d * s))
    return header


def detect(rgb: np.ndarray) -> Layout:
    x0, y0, h, _ = find_bar(rgb)
    columns = find_columns(rgb, x0, y0, h)
    # Blur (rescaled captures) shrinks the thresholded bar, so its measured height
    # understates the scale. The E..MIT label span is a long, blur-proof baseline.
    h_eff = BAR_H_REF * (columns["MIT"] - columns["E"]) / E_TO_MIT_REF
    y0 = int(round(y0 + h / 2 - h_eff / 2))
    h = h_eff        # x0 stays the measured bar edge: the bar-to-E distance differs between UIs
    teams = find_teams(rgb, y0, h, columns)
    rows = []
    for team, block in teams.items():
        for i, (ry0, ry1) in enumerate(fit_rows(rgb, block, h, columns)):
            pitch = ry1 - ry0 + 1
            row = Row(team, i, ry0, ry1)

            def box(xr, yr):
                return (int(x0 + xr[0] * h), int(ry0 + yr[0] * pitch),
                        int(x0 + xr[1] * h), int(ry0 + yr[1] * pitch))

            for name, xr in ROW_X.items():
                if name == "ult" and team != "top":
                    continue
                row.rois[name] = box(xr, ROW_Y[name])
            to_e = (columns["E"] - x0) / h        # bar edge to E column, in h
            for name, xr in ROW_X_FROM_E.items():
                row.rois[name] = box((to_e + xr[0], to_e + xr[1]), ROW_Y[name])
            name_x = (NAME_START[team], to_e + NAME_END_FROM_E)
            row.rois["name"] = box(name_x, ROW_Y["name"])
            row.rois["title"] = box(name_x, ROW_Y["title"])
            for c in STAT_COLS:
                cx, hw = columns[c], STAT_HALF_W[c] * h
                row.rois[c] = (int(cx - hw), int(ry0 + ROW_Y["stat"][0] * pitch),
                               int(cx + hw), int(ry0 + ROW_Y["stat"][1] * pitch))
            rows.append(row)
    ui = "new" if (columns["E"] - x0) / h > UI_NAME_AREA else "classic"
    return Layout(x0, y0, float(h), columns, teams, rows, find_header(rgb), ui)


def crop(rgb: np.ndarray, box) -> np.ndarray:
    x0, y0, x1, y1 = box
    H, W = rgb.shape[:2]
    return rgb[max(0, y0):min(H, y1), max(0, x0):min(W, x1)]


COLOURS = {"role": "yellow", "portrait": "magenta", "ult": "orange", "name": "lime", "title": "cyan",
           "perk_left": "red", "perk_right": "red"}


def draw(rgb: np.ndarray, layout: Layout) -> Image.Image:
    """Debug overlay: every ROI outlined."""
    im = Image.fromarray(rgb).convert("RGB")
    d = ImageDraw.Draw(im)
    for r in layout.rows:
        d.line([(layout.x0, r.y0), (layout.x0 + 30 * layout.h, r.y0)], fill="white")
        for name, b in r.rois.items():
            d.rectangle(b, outline=COLOURS.get(name, "white"), width=2)
    for name, b in layout.header.items():
        d.rectangle(b, outline="yellow", width=2)
    return im
