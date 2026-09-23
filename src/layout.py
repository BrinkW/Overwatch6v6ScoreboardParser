"""
Scoreboard layout: find the table and express every region of interest (ROI)
relative to it.

Anchors (see CLAUDE.md "Layout: anchor, don't hardcode"):
  - the white column-header bar: its LEFT edge, top and height. Its right edge
    is unreliable (it blends into other bright UI in some captures), so column
    positions come from the dark E/A/D/DMG/H/MIT labels printed inside it;
  - the two saturated team blocks below it (split by the "VS" gap);
  - the thin dark separator lines between rows, used to fit the row count.

All geometry is in units of the bar height `h` (39 px on a native 2560x1440
capture), measured from the bar's left edge `x0` / top `y0`, so it survives
rescaled or cropped captures. Rows are labelled top/bottom, never by colour:
team colours change with the colour-blind setting.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw

BAR_H_REF = 39  # px, native 2560x1440

# Horizontal row ROIs: (x0, x1) in h units from the bar's left edge.
ROW_X = {
    "role":      (0.05, 0.85),
    "portrait":  (0.90, 2.85),
    "ult":       (3.00, 4.20),    # top (friendly) team only
    "perk_left": (10.12, 11.60),
    "perk_right": (12.04, 13.52),
}
NAME_X = {"top": (4.35, 10.05), "bottom": (2.90, 10.05)}  # bottom team has no ult column
# Vertical row ROIs: (y0, y1) as fractions of the row pitch.
ROW_Y = {
    "role": (0.25, 0.75), "portrait": (0.02, 0.98), "ult": (0.20, 0.80),
    "name": (0.06, 0.74), "title": (0.66, 0.99),
    "perk_left": (0.14, 0.86), "perk_right": (0.14, 0.86),
    "stat": (0.30, 0.76),
}
STAT_COLS = ["E", "A", "D", "DMG", "H", "MIT"]
STAT_HALF_W = {"E": 0.85, "A": 0.85, "D": 0.85, "DMG": 1.55, "H": 1.55, "MIT": 1.55}

# Header ROIs, (x0, y0, x1, y1) in h units from the bar's top-left corner.
HEADER = {
    "ban_0": (-7.05, -4.05, -5.25, -2.30),
    "ban_1": (-4.95, -4.05, -3.15, -2.30),
    "ban_2": (-2.85, -4.05, -1.05, -2.30),
    "ban_3": (-0.75, -4.05, 1.05, -2.30),
    # "MODE | MAP  TIME: mm:ss" is one right-aligned group whose left end moves with
    # the text length, so it is read as one strip and split on "TIME".
    "mode_map_time": (38.0, -4.15, 54.6, -2.75),
    "rank_low": (48.8, -2.65, 51.0, -0.25),
    "rank_high": (51.9, -2.65, 54.4, -0.25),
}


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


E_TO_MIT_REF = 1438 - 965  # px between the E and MIT label centres, native 2560x1440


def detect(rgb: np.ndarray) -> Layout:
    x0, y0, h, _ = find_bar(rgb)
    columns = find_columns(rgb, x0, y0, h)
    # Blur (rescaled captures) shrinks the thresholded bar, so its measured height
    # understates the scale. The E..MIT label span is a long, blur-proof baseline.
    h_eff = BAR_H_REF * (columns["MIT"] - columns["E"]) / E_TO_MIT_REF
    y0 = int(round(y0 + h / 2 - h_eff / 2))
    x0 = int(round(columns["E"] - (965 - 393) / BAR_H_REF * h_eff))
    h = h_eff
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
            row.rois["name"] = box(NAME_X[team], ROW_Y["name"])
            row.rois["title"] = box(NAME_X[team], ROW_Y["title"])
            for c in STAT_COLS:
                cx, hw = columns[c], STAT_HALF_W[c] * h
                row.rois[c] = (int(cx - hw), int(ry0 + ROW_Y["stat"][0] * pitch),
                               int(cx + hw), int(ry0 + ROW_Y["stat"][1] * pitch))
            rows.append(row)
    header = {k: (int(x0 + v[0] * h), int(y0 + v[1] * h), int(x0 + v[2] * h), int(y0 + v[3] * h))
              for k, v in HEADER.items()}
    return Layout(x0, y0, float(h), columns, teams, rows, header)


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
