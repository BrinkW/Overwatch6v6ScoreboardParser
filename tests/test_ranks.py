"""
Rank tier recognition must hold for every tier, including Emerald, which has no
screenshots: its hue is only 7 degrees from Master's, so the emblem silhouette
has to separate them.
"""

import re
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from src import header as HD  # noqa: E402

READER = HD.TierReader.load(ROOT / "reference" / "templates" / "rank_emblems.npz")
RANK_FILES = sorted((ROOT / "assets" / "ranks").glob("*.png"))


def emblem_of(path: Path) -> np.ndarray:
    """A rank asset composited onto the dark header, emblem part only."""
    im = Image.open(path).convert("RGBA")
    bg = Image.new("RGBA", im.size, (20, 20, 26, 255))
    bg.alpha_composite(im)
    a = np.asarray(bg.convert("RGB"))
    return a[:int(a.shape[0] * HD.EMBLEM_FRACTION)]


@pytest.mark.parametrize("tier,crop", list(HD.sheet_emblems().items()))
def test_tier_sheet(tier, crop):
    assert READER.classify(crop)[0] == tier


@pytest.mark.parametrize("path", RANK_FILES, ids=[p.stem for p in RANK_FILES])
def test_rank_assets(path):
    want = re.match(r"[a-z]+", path.stem).group().capitalize()
    assert READER.classify(emblem_of(path))[0] == want


def test_emerald_assets_exist():
    assert [p.stem for p in RANK_FILES if p.stem.startswith("emerald")] == [f"emerald{d}" for d in range(1, 6)]


def test_emerald_and_master_separate_by_shape():
    """Both are green; the silhouette must keep them clearly apart in both directions."""
    for tier, other in (("Emerald", "Master"), ("Master", "Emerald")):
        path = next(p for p in RANK_FILES if p.stem == f"{tier.lower()}3")
        got, margin, evidence = READER.classify(emblem_of(path))
        assert got == tier
        assert other in evidence["scores"]          # both were shortlisted by hue ...
        assert margin > 0.1                         # ... and shape separated them clearly
