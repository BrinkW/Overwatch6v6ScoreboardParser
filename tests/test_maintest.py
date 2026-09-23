"""
Regression gate: maintest.png (the King's Row reference capture, whose answer
key is verified) must parse exactly for every implemented field.

Templates come from the OTHER fixtures only, so this also checks that what the
parser learned generalises to an image it has not seen.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.dont_write_bytecode = True

from build_templates import SAMPLES, build  # noqa: E402
from src import digits as D, layout as L  # noqa: E402
from src.parse import STATS, Models, load_rgb, parse  # noqa: E402

FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "maintest.json").read_text(encoding="utf-8"))
IMAGE = SAMPLES / "maintest.png"


@pytest.fixture(scope="module")
def result():
    t = build(exclude=("maintest.png",))
    return parse(IMAGE, Models(D.DigitClassifier(*t["digits"])))


def test_layout_geometry():
    lay = L.detect(load_rgb(IMAGE))
    assert (lay.x0, lay.y0, round(lay.h)) == (393, 210, 39)
    assert [r.team for r in lay.rows] == ["top"] * 6 + ["bottom"] * 6
    assert [round(v) for v in lay.columns.values()] == [965, 1030, 1096, 1199, 1318, 1438]
    pitches = {r.y1 - r.y0 + 1 for r in lay.rows}
    assert pitches <= {73, 74}


@pytest.mark.parametrize("field", STATS)
def test_stats(result, field):
    got = [r[field] for r in result["rows"]]
    want = [r[field] for r in FIXTURE["rows"]]
    assert got == want


def test_no_structural_problems(result):
    assert result["problems"] == []
