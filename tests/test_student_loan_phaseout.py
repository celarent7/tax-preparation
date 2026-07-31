"""B3: student-loan interest MAGI phase-out (IRC 221)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from utils import load_tax_constants


@pytest.mark.parametrize("year", ["2023", "2024", "2025", "2026"])
def test_phaseout_block_exists_and_is_ordered(year):
    b = load_tax_constants(year)["student_loan_interest_phaseout"]
    for lo, hi in ((b["magi_start"], b["magi_end"]),
                   (b["magi_start_mfj"], b["magi_end_mfj"])):
        if lo is None or hi is None:
            pytest.skip(f"{year} band unverified (null) — raising is tested elsewhere")
        assert 0 < lo < hi


def test_2025_band_matches_rev_proc_2024_40():
    b = load_tax_constants("2025")["student_loan_interest_phaseout"]
    assert (b["magi_start"], b["magi_end"]) == (85000, 100000)
    assert (b["magi_start_mfj"], b["magi_end_mfj"]) == (170000, 200000)


# ---------------------------------------------------------------------------
# student_loan_interest_deduction() helper (IRC 221 phase-out + MFS bar)
# ---------------------------------------------------------------------------

from deduction_analyzer import student_loan_interest_deduction  # noqa: E402


@pytest.fixture(scope="module")
def data_2025():
    return load_tax_constants("2025")


def test_full_deduction_below_band(data_2025):
    r = student_loan_interest_deduction(3000, 60000, "single", data_2025)
    assert r["eligible"] and r["deduction"] == 2500 and not r["phaseout_applied"]


def test_interest_below_limit_used_as_is(data_2025):
    r = student_loan_interest_deduction(900, 60000, "single", data_2025)
    assert r["deduction"] == 900


def test_mid_band_prorated(data_2025):
    # single band 85k-100k; MAGI 92.5k is exactly halfway -> 50% of $2,500,
    # rounded UP to the nearest $10 hmm — IRC 221 uses straight proration with
    # no rounding rule on the deduction itself; assert the unrounded math.
    r = student_loan_interest_deduction(2500, 92500, "single", data_2025)
    assert r["phaseout_applied"] and r["deduction"] == 1250


def test_above_band_zero(data_2025):
    r = student_loan_interest_deduction(2500, 150000, "single", data_2025)
    assert r["fully_phased_out"] and r["deduction"] == 0


def test_mfs_statutorily_ineligible(data_2025):
    r = student_loan_interest_deduction(2500, 40000, "mfs", data_2025)
    assert not r["eligible"] and r["deduction"] == 0
    assert "separately" in r["note"].lower()


def test_unverified_band_raises():
    """If a year's band is null (unverified), computing must raise, not guess."""
    from utils import UnverifiedConstantError
    data = load_tax_constants("2026")
    b = data["student_loan_interest_phaseout"]
    if b["magi_start"] is not None:
        pytest.skip("2026 band verified — nothing to assert")
    with pytest.raises(UnverifiedConstantError):
        student_loan_interest_deduction(2500, 90000, "single", data)


def test_null_band_synthetic_raises():
    """Acceptance #4: a null (unverified) band must raise, never silently pay.

    Exercises the raise path directly so the contract holds regardless of
    whether any real year is currently unverified.
    """
    from utils import UnverifiedConstantError
    data = load_tax_constants("2025")
    data["student_loan_interest_phaseout"] = {
        "magi_start": None, "magi_end": None,
        "magi_start_mfj": None, "magi_end_mfj": None,
    }
    with pytest.raises(UnverifiedConstantError):
        student_loan_interest_deduction(2500, 90000, "single", data)