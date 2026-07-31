"""B1: net-capital-loss offset ($3,000 / $1,500 MFS) and carryforward."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from tax_calculator import apply_capital_loss
from utils import load_tax_constants


@pytest.fixture(scope="module")
def limits_2025():
    data = load_tax_constants("2025")
    return data["capital_loss_limit"], data["capital_loss_limit_mfs"]


def test_mfj_8k_loss_offsets_3k_carries_5k(limits_2025):
    limit, limit_mfs = limits_2025
    r = apply_capital_loss(8000, "married_jointly", limit, limit_mfs)
    assert r["allowed_offset"] == 3000
    assert r["carryforward"] == 5000
    assert r["limit_used"] == limit


def test_mfs_2k_loss_offsets_1500_carries_500(limits_2025):
    limit, limit_mfs = limits_2025
    r = apply_capital_loss(2000, "married_separately", limit, limit_mfs)
    assert r["allowed_offset"] == 1500
    assert r["carryforward"] == 500
    assert r["limit_used"] == limit_mfs


def test_loss_below_limit_fully_absorbed(limits_2025):
    limit, limit_mfs = limits_2025
    r = apply_capital_loss(1200, "single", limit, limit_mfs)
    assert r["allowed_offset"] == 1200
    assert r["carryforward"] == 0


def test_zero_loss_is_noop(limits_2025):
    limit, limit_mfs = limits_2025
    r = apply_capital_loss(0, "single", limit, limit_mfs)
    assert r["allowed_offset"] == 0
    assert r["carryforward"] == 0


def test_unknown_status_raises(limits_2025):
    limit, limit_mfs = limits_2025
    with pytest.raises(ValueError):
        apply_capital_loss(5000, "widowed_maybe", limit, limit_mfs)