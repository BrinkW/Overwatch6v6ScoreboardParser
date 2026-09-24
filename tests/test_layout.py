"""
Layout across the two scoreboard UIs (classic, and tabbed: late 2026), and the
row states the tabbed capture test13 introduced: a mystery portrait (a player
who just swapped hero) and respawn timers over dead players' portraits.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.dont_write_bytecode = True

from build_templates import SAMPLES  # noqa: E402
from src import header as HD, icons as I, layout as L  # noqa: E402
from src.parse import load_rgb  # noqa: E402

SHOTS = sorted(p for p in SAMPLES.iterdir() if p.suffix in (".png", ".jpg"))


@pytest.fixture(scope="module")
def layouts():
    out = {}
    for p in SHOTS:
        rgb = load_rgb(p)
        out[p.name] = (rgb, L.detect(rgb))
    return out


def test_ui_detected(layouts):
    assert {n: lay.ui for n, (_, lay) in layouts.items()} == {n: ("tabbed" if n == "test13.png" else "classic")
                                                               for n in layouts}


def test_classic_header_boxes(layouts):
    h = layouts["maintest.png"][1].header
    assert [h[f"ban_{k}"] for k in range(4)] == [(118, 52, 188, 120), (201, 52, 271, 120),
                                                 (284, 52, 354, 120), (367, 52, 437, 120)]
    assert (h["rank_low"], h["rank_high"]) == ((2261, 102, 2395, 200), (2399, 102, 2545, 200))


def test_tabbed_geometry(layouts):
    rgb, lay = layouts["test13.png"]
    assert (lay.x0, round(lay.h, 1)) == (448, 34.1)          # smaller table, further right
    assert sorted(k for k in lay.header if k.startswith("ban_")) == [f"ban_{k}" for k in range(4)]
    assert lay.header["ban_0"] == (538, 25, 608, 93)
    assert (lay.header["rank_low"], lay.header["rank_high"]) == ((2261, 110, 2395, 208), (2399, 110, 2545, 208))
    # perk slots sit at the same offset from the E column in both UIs
    for name in ("maintest.png", "test13.png"):
        lay = layouts[name][1]
        assert round((lay.columns["E"] - lay.rows[0].rois["perk_left"][0]) / lay.h, 1) == 4.6


def test_every_capture_has_four_ban_slots(layouts):
    for name, (_, lay) in layouts.items():
        assert sum(k.startswith("ban_") for k in lay.header) == 4, name


def test_fifth_ban_slot_is_found():
    """No 5-ban capture exists yet: copy test13's 4th ban tile into the 5th position."""
    rgb = load_rgb(SAMPLES / "test13.png").copy()
    x0, y0, x1, y1 = L.detect(rgb).header["ban_3"]
    pitch = round(L.BAN_PITCH * 41 / L.BAN_ICON_REF)
    rgb[y0 - 2:y1 + 2, x0 + pitch - 2:x1 + pitch + 2] = rgb[y0 - 2:y1 + 2, x0 - 2:x1 + 2]
    lay = L.detect(rgb)
    assert sum(k.startswith("ban_") for k in lay.header) == 5
    assert HD.BanReader().read(L.crop(rgb, lay.header["ban_4"]))[0] == "junkrat"


def test_mystery_portrait_only_on_sonia(layouts):
    found = [(n, r.team, r.index) for n, (rgb, lay) in layouts.items() for r in lay.rows
             if I.mystery_score(L.crop(rgb, r.rois["portrait"])) >= I.MYSTERY_TINT]
    assert found == [("test13.png", "bottom", 5)]


def test_blank_role_only_on_sonia(layouts):
    found = [(n, r.team, r.index) for n, (rgb, lay) in layouts.items() for r in lay.rows
             if I.role_is_blank(L.crop(rgb, r.rois["role"]))]
    assert found == [("test13.png", "bottom", 5)]


def test_respawn_rings(layouts):
    found = [(n, r.team, r.index) for n, (rgb, lay) in layouts.items() for r in lay.rows
             if I.respawn_ring(L.crop(rgb, r.rois["portrait"])) is not None]
    assert found == [("test13.png", "bottom", 2), ("test13.png", "bottom", 3), ("test13.png", "bottom", 4)]


def test_mystery_asset_is_a_silhouette():
    from PIL import Image
    a = np.asarray(Image.open(ROOT / "assets" / "heroes" / "mystery.png"))
    assert a.shape == (256, 256, 4) and 0.2 < (a[..., 3] > 128).mean() < 0.6
