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