"""B6: AMT quick screen — constants and computation.

Per the plan's Option B reconciliation, no new AMT data keys were added: the
existing `amt` block already carries the rates (`rates` array), the 28%
breakpoint (`rate_28_threshold` / `rate_28_threshold_mfs`) and the phase-out
rate (`phaseout_rate`). These tests lock those existing values against their
primary sources so a future edit cannot silently drift.

Primary sources:
  * IRC § 55(b)(1) — 26%/28% statutory rates (not indexed).
  * IRC § 55(d)(2) — 25% exemption phase-out rate, pre-OBBBA (2023-2025).
  * OBBBA § 70107 (Pub. L. 119-21) — doubled the phase-out rate to 50% for
    taxable years beginning after 12/31/2025 (i.e. 2026).
  * Rev. Proc. 2022-38 / 2023-34 / 2024-40 / 2025-32 § .11 / § .10 — the
    inflation-indexed 28% breakpoint and exemption amounts per year.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from utils import load_tax_constants  # noqa: E402


# ---------------------------------------------------------------------------
# Task 1 — AMT rate / breakpoint / phase-out constants (existing keys, verified)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("year", ["2023", "2024", "2025", "2026"])
def test_amt_rate_constants_present(year):
    """The 26%/28% two-rate AMT schedule is present for every supported year."""
    amt = load_tax_constants(year)["amt"]
    rates = amt["rates"]
    assert [r["rate"] for r in rates] == [0.26, 0.28], f"{year} AMT rates"
    # The 28% breakpoint is present (verified) and MFS is exactly half.
    bp = amt["rate_28_threshold"]
    assert bp is not None, f"{year} AMT 28% breakpoint must be verified, not null"
    assert amt["rate_28_threshold_mfs"] == bp / 2, f"{year} MFS breakpoint must be half"


@pytest.mark.parametrize("year,expected", [
    ("2023", 0.25), ("2024", 0.25), ("2025", 0.25), ("2026", 0.5),
])
def test_amt_phaseout_rate(year, expected):
    """Phase-out rate is 25% pre-OBBBA (IRC 55(d)(2)) and 50% for 2026 (OBBBA §70107)."""
    assert load_tax_constants(year)["amt"]["phaseout_rate"] == expected


def test_2025_breakpoint_matches_rev_proc():
    """Rev. Proc. 2024-40 § .11: 2025 28% bracket at $239,100 ($119,550 MFS)."""
    amt = load_tax_constants("2025")["amt"]
    assert amt["rate_28_threshold"] == 239100
    assert amt["rate_28_threshold_mfs"] == 119550


def test_2026_breakpoint_matches_rev_proc():
    """Rev. Proc. 2025-32 § .10: 2026 28% bracket at $244,500 ($122,250 MFS)."""
    amt = load_tax_constants("2026")["amt"]
    assert amt["rate_28_threshold"] == 244500
    assert amt["rate_28_threshold_mfs"] == 122250


def test_2023_amt_now_sourced():
    """Regression: 2023 AMT was previously absent from the Rev. Proc. backs list."""
    from utils import load_raw_constants
    backs = load_raw_constants()["_sources"]["rev_proc_2022_38_ty2023"]["backs"]
    assert "2023.amt" in backs

# ---------------------------------------------------------------------------
# Task 2 — amt_quick_test() pure function
# ---------------------------------------------------------------------------

from tax_calculator import amt_quick_test  # noqa: E402
from utils import UnverifiedConstantError  # noqa: E402


@pytest.fixture(scope="module")
def amt_2025():
    return load_tax_constants("2025")["amt"]


def test_case_a_no_amt(amt_2025):
    """Case A: no preferences, moderate income — TMT < regular tax.

    TI 150,000 + SALT 10,000 = AMTI 160,000; exemption 88,100 (no phase-out,
    160,000 < 626,350); base 71,900; TMT = 71,900 × 0.26 = 18,694.
    """
    r = amt_quick_test(150000, 10000, 0, 28762, "single", amt_2025)
    assert not r["form_6251_required"]
    assert r["tentative_minimum_tax"] == pytest.approx(18694, abs=1)


def test_case_b_iso_triggers(amt_2025):
    """Case B: big ISO spread — TMT exceeds regular tax.

    AMTI 460,000; exemption 88,100; base 371,900;
    TMT = 239,100 × 0.26 + (371,900 − 239,100) × 0.28 = 62,166 + 37,184 = 99,350.
    """
    r = amt_quick_test(150000, 10000, 300000, 28762, "single", amt_2025)
    assert r["form_6251_required"]
    assert r["tentative_minimum_tax"] == pytest.approx(99350, abs=1)
    assert r["tmt_exceeds_regular_by"] == pytest.approx(99350 - 28762, abs=1)


def test_case_c_exemption_phaseout(amt_2025):
    """Case C: AMTI 700,000 inside the phase-out zone.

    exemption = max(0, 88,100 − 0.25 × (700,000 − 626,350)) = 69,687.50.
    """
    r = amt_quick_test(690000, 10000, 0, 200000, "single", amt_2025)
    assert r["exemption_after_phaseout"] == pytest.approx(69687.50, abs=1)


def test_screen_language_present(amt_2025):
    """The output self-describes as a screen and names Form 6251 + ignored items."""
    r = amt_quick_test(150000, 0, 0, 30000, "single", amt_2025)
    assert "screen" in r["note"].lower() and "6251" in r["note"]
    assert len(r["ignored_items"]) >= 3


def test_mfs_uses_half_breakpoint(amt_2025):
    """MFS picks the MFS breakpoint (119,550), not the all-other one (239,100)."""
    r = amt_quick_test(150000, 10000, 300000, 28762, "mfs", amt_2025)
    # base = AMTI 460,000 − exemption 68,500 (mfs) = 391,500
    # TMT = 119,550 × 0.26 + (391,500 − 119,550) × 0.28 = 31,083 + 76,146 = 107,229
    assert r["tentative_minimum_tax"] == pytest.approx(107229, abs=1)
    assert r["form_6251_required"]


def test_null_breakpoint_raises():
    """A null (unverified) 28% breakpoint must raise — the screen never guesses."""
    amt = dict(load_tax_constants("2025")["amt"])
    amt["rate_28_threshold"] = None
    amt["rate_28_threshold_mfs"] = None
    with pytest.raises(UnverifiedConstantError):
        amt_quick_test(150000, 0, 50000, 30000, "single", amt)
