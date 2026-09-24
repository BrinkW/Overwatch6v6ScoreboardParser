"""
Layout across the two scoreboard UIs (classic, and new: late 2026, with or
without its HERO INFO | SCOREBOARD tab strip), 4 or 5 ban slots in either, and
the row states test13 introduced: a mystery portrait (a player who just swapped
hero) and respawn timers over dead players' portraits.
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
    assert {n: lay.ui for n, (_, lay) in layouts.items()} == {n: ("new" if n == "test13.png" else "classic")
                                                               for n in layouts}


def test_classic_header_boxes(layouts):
    h = layouts["maintest.png"][1].header
    assert [h[f"ban_{k}"] for k in range(4)] == [(118, 52, 188, 120), (201, 52, 271, 120),
                                                 (284, 52, 354, 120), (367, 52, 437, 120)]
    assert (h["rank_low"], h["rank_high"]) == ((2261, 102, 2395, 200), (2399, 102, 2545, 200))


def test_new_ui_geometry(layouts):
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


def copy_ban_to_fifth(rgb: np.ndarray, src: str = "ban_3") -> np.ndarray:
    """No 5-ban capture exists yet: copy a ban slot into the 5th position."""
    rgb = rgb.copy()
    x0, y0, x1, y1 = L.detect(rgb).header[src]
    pitch = round(L.BAN_PITCH * 41 / L.BAN_ICON_REF)
    rgb[y0 - 2:y1 + 2, x0 + pitch - 2:x1 + pitch + 2] = rgb[y0 - 2:y1 + 2, x0 - 2:x1 + 2]
    return rgb


def without_tabs(rgb: np.ndarray) -> np.ndarray:
    """The new UI without its HERO INFO | SCOREBOARD tabs: paint them over with the
    header strip's navy."""
    rgb = rgb.copy()
    icon = L.find_ban_icon(rgb)
    rgb[:icon[1] + 2 * icon[2], :icon[0] - 5] = (14, 20, 37)
    return rgb


def bans(rgb):
    lay = L.detect(rgb)
    reader = HD.BanReader()
    return lay.ui, [reader.read(L.crop(rgb, lay.header[f"ban_{k}"]))[0]
                    for k in range(sum(k.startswith("ban_") for k in lay.header))]


@pytest.mark.parametrize("image,ui,four", [
    ("maintest.png", "classic", ["zenyatta", "roadhog", "freja", "zarya"]),
    ("test13.png", "new", ["roadhog", "jetpack_cat", "freja", "junkrat"]),
])
@pytest.mark.parametrize("tabs", [True, False])
@pytest.mark.parametrize("n", [4, 5])
def test_four_or_five_bans_in_both_uis(image, ui, four, tabs, n):
    rgb = load_rgb(SAMPLES / image)
    if not tabs:
        if ui == "classic":
            pytest.skip("the classic UI has no tab strip")
        rgb = without_tabs(rgb)
    if n == 5:
        rgb = copy_ban_to_fifth(rgb)
    assert bans(rgb) == (ui, four + four[3:] if n == 5 else four)


def test_fifth_slot_without_a_ban_is_grey():
    """test11's 4th slot is a team that didn't ban (grey frame); as a 5th slot it still counts."""
    ui, got = bans(copy_ban_to_fifth(load_rgb(SAMPLES / "test11.png")))
    assert got[3:] == ["none", "none"]


def test_bright_scene_is_not_a_fifth_slot():
    """A light grey scene where no 5th slot exists must not pass as a grey (no-ban) frame."""
    rgb = load_rgb(SAMPLES / "maintest.png").copy()
    x0, y0, x1, y1 = L.detect(rgb).header["ban_3"]
    rgb[y0 - 10:y1 + 10, x1 + 5:x1 + 200] = (185, 188, 190)
    assert len(bans(rgb)[1]) == 4


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
