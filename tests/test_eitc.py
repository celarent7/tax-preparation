#!/usr/bin/env python3
"""Tests for the Earned Income Tax Credit computation.

Covers audit findings:
  F5 — the phase-in was hard-coded at 34% and the phase-out at 15.98% for ALL
       child counts. IRC 32(b)(1) sets 7.65/34/40/45% in and
       7.65/15.98/21.06/21.06% out. A zero-child filer's credit was overstated
       by roughly 4.4x.
  F6 — the constants' phaseout_single/phaseout_mfj values were identical for
       2024 and 2025 and matched neither year's Rev. Proc.
  Y1 — the checker had no --year argument and applied 2024 figures to any year.

Expected values are hand-computed from the published Rev. Proc. tables.
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

from credit_eligibility_checker import calculate_eitc, get_credit_params  # noqa: E402
from utils import load_tax_constants  # noqa: E402

EITC_2025 = get_credit_params(load_tax_constants("2025"))["eitc"]
EITC_2024 = get_credit_params(load_tax_constants("2024"))["eitc"]


def eitc(table=None, **overrides):
    data = {
        "qualifying_children_eitc": 0,
        "earned_income": 10000,
        "agi": 10000,
        "filing_status": "single",
        "age": 30,
        "investment_income": 0,
    }
    data.update(overrides)
    return calculate_eitc(data, table or EITC_2025)


# ---------------------------------------------------------------------------
# F5 — the zero-child credit
# ---------------------------------------------------------------------------

def test_zero_child_credit_capped_at_max_not_34_percent():
    """F5: the headline defect.

    2025, no children, earned income $10,000 (above the $8,490 earned income
    amount and below the $10,620 phase-out threshold), so the credit sits on
    the plateau at the maximum: $649.

    The old code computed min(10,000 x 0.34, 649) — the 34% rate belongs to
    one-child filers. It happened to be capped here, but the cap itself came
    from the same wrong table; below the plateau the 4.4x overstatement is
    fully visible (see the next test).
    """
    result = eitc(qualifying_children_eitc=0, earned_income=10000, agi=10000)
    assert result.amount == 649


def test_zero_child_phase_in_uses_7_65_percent():
    """F5: at $5,000 of earned income the credit is 7.65%, not 34%.

    Correct: 5,000 x 0.0765 = $382.50
    Old:     5,000 x 0.34   = $1,700.00  (4.4x too high)
    """
    result = eitc(qualifying_children_eitc=0, earned_income=5000, agi=5000)
    assert result.amount == pytest.approx(382.50)
    assert result.amount != pytest.approx(1700.00)


@pytest.mark.parametrize("children,earned,expected_rate", [
    (0, 5000, 0.0765),
    (1, 6000, 0.34),
    (2, 8000, 0.40),
    (3, 8000, 0.45),
])
def test_phase_in_rate_varies_by_child_count(children, earned, expected_rate):
    """F5: each child count phases in at its own statutory rate."""
    result = eitc(qualifying_children_eitc=children, earned_income=earned, agi=earned)
    assert result.amount == pytest.approx(earned * expected_rate, abs=0.01)


def test_phase_out_rate_varies_by_child_count():
    """F5: a two-child filer phases out at 21.06%, not 15.98%.

    2025, two children, MFJ. Threshold phase-out $30,470, max credit $7,152.
    At $40,000 of income: excess = 9,530.
      correct: 7,152 - 9,530 x 0.2106 = 7,152 - 2,007.02 = $5,144.98
      old:     7,152 - 9,530 x 0.1598 = 7,152 - 1,522.89 = $5,629.11
    """
    result = eitc(qualifying_children_eitc=2, earned_income=40000, agi=40000,
                  filing_status="married_jointly")
    assert result.amount == pytest.approx(5144.98, abs=0.02)


def test_plateau_pays_the_maximum_credit():
    """Between the earned income amount and the phase-out threshold: max credit.

    2025, one child, MFJ: earned income amount $12,730, threshold $30,470.
    At $20,000 the credit is the full $4,328.
    """
    result = eitc(qualifying_children_eitc=1, earned_income=20000, agi=20000,
                  filing_status="married_jointly")
    assert result.amount == 4328


def test_credit_is_zero_above_the_completed_phaseout():
    """2025, one child, single: completed phase-out is $50,434."""
    result = eitc(qualifying_children_eitc=1, earned_income=60000, agi=60000)
    assert result.eligible is False
    assert result.amount == 0


# ---------------------------------------------------------------------------
# F6 — year-specific data
# ---------------------------------------------------------------------------

def test_investment_income_limit_is_year_specific():
    """F6: the limit was hard-coded at the 2024 figure ($11,600).

    2025 is $11,950, so $11,800 of investment income disqualifies a 2024 filer
    but NOT a 2025 filer.
    """
    disqualified_2024 = eitc(table=EITC_2024, investment_income=11800,
                             qualifying_children_eitc=1, earned_income=20000, agi=20000)
    allowed_2025 = eitc(table=EITC_2025, investment_income=11800,
                        qualifying_children_eitc=1, earned_income=20000, agi=20000)

    assert disqualified_2024.eligible is False
    assert "exceeds" in disqualified_2024.notes[0]
    assert allowed_2025.eligible is True


def test_max_credit_differs_between_2024_and_2025():
    """F6: the old block had identical phaseout values for both years."""
    assert EITC_2024["1"]["max_credit"] == 4213
    assert EITC_2025["1"]["max_credit"] == 4328
    assert EITC_2024["1"]["threshold_phaseout_mfj"] != EITC_2025["1"]["threshold_phaseout_mfj"]


# ---------------------------------------------------------------------------
# Eligibility gates (preserved behavior)
# ---------------------------------------------------------------------------

def test_mfs_cannot_claim_eitc():
    result = eitc(filing_status="married_separately", qualifying_children_eitc=1)
    assert result.eligible is False


def test_childless_filer_must_be_25_to_64():
    assert eitc(qualifying_children_eitc=0, age=22).eligible is False
    assert eitc(qualifying_children_eitc=0, age=70).eligible is False
    assert eitc(qualifying_children_eitc=0, age=30).eligible is True


def test_no_earned_income_is_ineligible():
    assert eitc(earned_income=0, agi=0).eligible is False


# ---------------------------------------------------------------------------
# Y1 — the CLI is year-aware
# ---------------------------------------------------------------------------

def test_cli_accepts_year_and_uses_that_years_figures():
    """Y1: --year now exists and changes the answer."""
    def run(year):
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "credit_eligibility_checker.py"),
             "--year", year, "--agi", "50000", "--earned-income", "50000",
             "--filing-status", "mfj", "--children-under-17", "2",
             "--output-format", "json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    r2024 = run("2024")
    r2025 = run("2025")

    ctc_2024 = next(c for c in r2024["credits"] if c["name"] == "Child Tax Credit")
    ctc_2025 = next(c for c in r2025["credits"] if c["name"] == "Child Tax Credit")

    # CTC per child: $2,000 in 2024, $2,200 in 2025 (OBBBA)
    assert ctc_2024["amount"] == 4000
    assert ctc_2025["amount"] == 4400


def test_cli_rejects_unsupported_year():
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "credit_eligibility_checker.py"),
         "--year", "1999"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0


def test_cli_accepts_filing_status_aliases():
    """D2: the checker's --filing-status was restricted to 3 canonical values."""
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "credit_eligibility_checker.py"),
         "--year", "2025", "--filing-status", "mfj", "--agi", "50000",
         "--output-format", "json"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
