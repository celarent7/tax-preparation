#!/usr/bin/env python3
"""Tests for the estimated-tax safe harbor.

Covers audit finding F9: the 110% high-income safe-harbor threshold was
$150,000 for every filing status. For married filing separately it is $75,000,
so an MFS filer with prior-year AGI between $75,000 and $150,000 was told
100% of prior-year tax was enough when the law requires 110% — an underpayment
penalty the plugin would have caused.

Primary source (retrieved 2026-07-29):
  IRS, Form 1040-ES, https://www.irs.gov/pub/irs-pdf/f1040es.pdf
  "Higher income taxpayers. If your adjusted gross income (AGI) for 2025 was
   more than $150,000 ($75,000 if your filing status for 2026 is married filing
   separately), substitute 110% for 100% in (2b) under General Rule, earlier."

The overall required-annual-payment formula
  min(90% x current-year tax, prior-year tax x (1.10 or 1.00))
was already correct and is deliberately preserved (ground rule 4).
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

from estimated_tax_calculator import (  # noqa: E402
    calculate_safe_harbor,
    high_income_threshold,
)


# ---------------------------------------------------------------------------
# The threshold itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,expected", [
    ("single", 150000),
    ("married_jointly", 150000),
    ("head_of_household", 150000),
    ("qualifying_surviving_spouse", 150000),
    ("married_separately", 75000),
    ("mfs", 75000),                     # alias must resolve too
])
def test_high_income_threshold_by_status(status, expected):
    """F9: MFS uses $75,000; every other status uses $150,000."""
    assert high_income_threshold(status) == expected


# ---------------------------------------------------------------------------
# Safe harbor application
# ---------------------------------------------------------------------------

def test_mfs_above_75k_requires_110_percent():
    """F9: the exact band the bug got wrong — MFS AGI between $75K and $150K.

    Prior-year tax $20,000, prior-year AGI $80,000, MFS.
    Correct: 110% x 20,000 = $22,000. The old code said $20,000.
    """
    result = calculate_safe_harbor(20000, 80000, "married_separately")
    assert result["applies_high_income"] is True
    assert result["minimum_required"] == 22000
    assert result["threshold_used"] == 75000


def test_mfs_below_75k_requires_100_percent():
    """MFS with prior-year AGI under $75,000 stays at 100%."""
    result = calculate_safe_harbor(20000, 70000, "married_separately")
    assert result["applies_high_income"] is False
    assert result["minimum_required"] == 20000


def test_non_mfs_between_75k_and_150k_stays_at_100_percent():
    """Regression: the fix must not raise the bar for other statuses.

    A single filer with $80,000 prior AGI is NOT a high-income taxpayer.
    """
    result = calculate_safe_harbor(20000, 80000, "single")
    assert result["applies_high_income"] is False
    assert result["minimum_required"] == 20000
    assert result["threshold_used"] == 150000


def test_non_mfs_above_150k_requires_110_percent():
    """Prior tax $30,000, AGI $160,000, single -> 110% x 30,000 = $33,000."""
    result = calculate_safe_harbor(30000, 160000, "single")
    assert result["applies_high_income"] is True
    assert result["minimum_required"] == 33000


@pytest.mark.parametrize("status,agi,applies", [
    ("married_separately", 75000, False),    # "more than" is strict
    ("married_separately", 75001, True),
    ("single", 150000, False),
    ("single", 150001, True),
])
def test_threshold_boundary_is_strictly_greater_than(status, agi, applies):
    """The statute says 'more than', so equality does not trigger 110%."""
    result = calculate_safe_harbor(10000, agi, status)
    assert result["applies_high_income"] is applies


def test_unknown_filing_status_raises():
    """D2: no silent fallback when computing the threshold."""
    with pytest.raises(ValueError):
        calculate_safe_harbor(10000, 200000, "not_a_status")


# ---------------------------------------------------------------------------
# The preserved required-annual-payment formula (ground rule 4)
# ---------------------------------------------------------------------------

def test_required_payment_is_lesser_of_90_percent_current_and_safe_harbor():
    """The min(90% current, safe harbor) formula is unchanged.

    Hand-computed via the CLI: MFS, projected income $120,000, prior tax
    $40,000, prior AGI $100,000 (so 110% applies -> safe harbor $44,000).
    90% of the projected liability will be well under $44,000, so the target
    must be the 90% figure, not the safe harbor.
    """
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "estimated_tax_calculator.py"),
         "--projected-income", "120000", "--prior-year-tax", "40000",
         "--prior-year-agi", "100000", "--filing-status", "mfs",
         "--year", "2025", "--output-format", "json"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    assert payload["safe_harbor"]["minimum_required"] == 44000
    assert payload["safe_harbor"]["applies_high_income"] is True

    ninety_percent = round(payload["estimated_liability"]["total"] * 0.90, 2)
    assert payload["payment_plan"]["total_target_payment"] == min(ninety_percent, 44000)
