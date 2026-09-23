"""
Player names and titles on real rows, with templates that never come from the
screenshot being read.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.dont_write_bytecode = True

from build_templates import SAMPLES, build  # noqa: E402
from src import layout as L, text as T  # noqa: E402
from src.parse import load_rgb  # noqa: E402


def read_name(image: str, row: int, players=None, fallback=True) -> dict:
    t = build(exclude=(image,))
    models = T.TextModels(T.GlyphSet(*t["name_glyphs"]), T.GlyphSet(*t["title_glyphs"]),
                          t["players"] if players is None else players, [],
                          T.NameImages(*t["fallback_names"]) if fallback and "fallback_names" in t else None)
    rgb = load_rgb(SAMPLES / image)
    lay = L.detect(rgb)
    return models.read_name(T.text_block(rgb, lay.rows[row]), lay.scale)


@pytest.mark.parametrize("image,row,raw,player", [("test12.png", 3, "FREEDDM", "FREEDOM"),
                                                  ("test2.jpg", 6, "ANOYGDH", "ANDYGDH")])
def test_misread_snaps_to_known_player(image, row, raw, player):
    """An O/D misread one glyph off a known player is snapped to that player and flagged."""
    got = read_name(image, row, players=[player])
    assert (got["raw"], got["name"], got.get("snapped")) == (raw, player, True)


def test_no_snap_when_the_glyph_is_not_a_near_tie():
    got = read_name("test12.png", 3, players=["FREEDOX"])      # read FREEDDM: M is nothing like X
    assert got["name"] == "FREEDDM" and not got.get("snapped")


def test_fallback_font_name_matches_the_same_player_elsewhere():
    """SPEEDSPORT! (fallback font) in test10 is recognised from its image in test9."""
    got = read_name("test10.png", 4)
    assert (got["font"], got["name"]) == ("fallback", "SPEEDSPORT!")


def test_unknown_fallback_font_name_is_none():
    got = read_name("test5.png", 5)                            # 바람 appears in no other screenshot
    assert (got["font"], got["name"]) == ("fallback", None)
