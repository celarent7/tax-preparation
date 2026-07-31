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