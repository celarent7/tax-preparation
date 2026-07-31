"""B5: Form 8959 wage/SE coordination for the 0.9% Additional Medicare Tax.

The Additional Medicare Tax (IRC 3101(b)(2); IRS Form 8959) is 0.9% on:
  Part I  — wages above the filing-status threshold, and
  Part II — self-employment income, taken at 92.35% (the SE base), above the
            threshold AS REDUCED by the wages already counted in Part I.

Charging 0.9% on raw (wages + SE) over a single shared threshold — the
previous projection approximation — overstates the tax whenever SE income is
present: it ignores the 7.65% SE haircut and misallocates the threshold.
These tests pin the Form 8959 math to hand-worksheet values.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from estimated_tax_calculator import additional_medicare_8959
from utils import load_tax_constants


@pytest.fixture(scope="module")
def se_cfg():
    return load_tax_constants("2025")["se_tax"]


def test_case_a_mfj_under_threshold_zero(se_cfg):
    r = additional_medicare_8959(150000, 100000, "mfj", se_cfg)
    assert r["tax"] == 0


def test_case_b_single_se_part_only(se_cfg):
    r = additional_medicare_8959(150000, 100000, "single", se_cfg)
    assert r["wage_part"] == 0
    assert r["se_part"] == pytest.approx(381.15, abs=0.01)
    assert r["tax"] == pytest.approx(381.15, abs=0.01)


def test_case_c_single_both_parts(se_cfg):
    r = additional_medicare_8959(230000, 50000, "single", se_cfg)
    assert r["wage_part"] == pytest.approx(270.00, abs=0.01)
    assert r["se_part"] == pytest.approx(415.58, abs=0.01)
    assert r["tax"] == pytest.approx(685.58, abs=0.01)


def test_se_loss_contributes_nothing(se_cfg):
    r = additional_medicare_8959(230000, -20000, "single", se_cfg)
    assert r["se_part"] == 0
    assert r["tax"] == pytest.approx(270.00, abs=0.01)


def test_pure_wages_matches_existing_helper(se_cfg):
    """With no SE income the 8959 result must equal calculate_additional_medicare."""
    from tax_calculator import calculate_additional_medicare
    from utils import additional_medicare_threshold
    old = calculate_additional_medicare(
        260000, additional_medicare_threshold("single", se_cfg),
        se_cfg["additional_medicare_rate"])
    new = additional_medicare_8959(260000, 0, "single", se_cfg)
    assert new["tax"] == old["tax"]