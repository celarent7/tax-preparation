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
from utils import SUPPORTED_YEARS, load_tax_constants  # noqa: E402


def _sh_2025():
    """The 2025 federal safe-harbor block (estimated_tax_safe_harbor)."""
    return load_tax_constants("2025")["estimated_tax_safe_harbor"]


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
    assert high_income_threshold(status, _sh_2025()) == expected


# ---------------------------------------------------------------------------
# Safe harbor application
# ---------------------------------------------------------------------------

def test_mfs_above_75k_requires_110_percent():
    """F9: the exact band the bug got wrong — MFS AGI between $75K and $150K.

    Prior-year tax $20,000, prior-year AGI $80,000, MFS.
    Correct: 110% x 20,000 = $22,000. The old code said $20,000.
    """
    result = calculate_safe_harbor(20000, 80000, "married_separately", _sh_2025())
    assert result["applies_high_income"] is True
    assert result["minimum_required"] == 22000
    assert result["threshold_used"] == 75000


def test_mfs_below_75k_requires_100_percent():
    """MFS with prior-year AGI under $75,000 stays at 100%."""
    result = calculate_safe_harbor(20000, 70000, "married_separately", _sh_2025())
    assert result["applies_high_income"] is False
    assert result["minimum_required"] == 20000


def test_non_mfs_between_75k_and_150k_stays_at_100_percent():
    """Regression: the fix must not raise the bar for other statuses.

    A single filer with $80,000 prior AGI is NOT a high-income taxpayer.
    """
    result = calculate_safe_harbor(20000, 80000, "single", _sh_2025())
    assert result["applies_high_income"] is False
    assert result["minimum_required"] == 20000
    assert result["threshold_used"] == 150000


def test_non_mfs_above_150k_requires_110_percent():
    """Prior tax $30,000, AGI $160,000, single -> 110% x 30,000 = $33,000."""
    result = calculate_safe_harbor(30000, 160000, "single", _sh_2025())
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
    result = calculate_safe_harbor(10000, agi, status, _sh_2025())
    assert result["applies_high_income"] is applies


def test_unknown_filing_status_raises():
    """D2: no silent fallback when computing the threshold."""
    with pytest.raises(ValueError):
        calculate_safe_harbor(10000, 200000, "not_a_status", _sh_2025())


def test_safe_harbor_reads_figures_from_block():
    """Threshold and 110% come from the constants block, not module literals."""
    block = {"high_income_agi": 1, "high_income_agi_mfs": 1,
             "high_income_prior_year_pct": 1.5, "current_year_pct": 0.9}
    assert calculate_safe_harbor(1000, 2, "single", block)["minimum_required"] == 1500.0


@pytest.mark.parametrize("year", SUPPORTED_YEARS)
def test_every_year_has_estimated_tax_safe_harbor(year):
    block = load_tax_constants(year)["estimated_tax_safe_harbor"]
    for key in ("high_income_agi", "high_income_agi_mfs",
                "high_income_prior_year_pct", "current_year_pct"):
        assert block[key] is not None, key


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


# ---------------------------------------------------------------------------
# G3 — QBI subtraction (blueprint P3.2 / plan §3.2)
# ---------------------------------------------------------------------------
def _et_cli(extra):
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "estimated_tax_calculator.py"),
         "--projected-income", "190000", "--projected-se-income", "60000",
         "--prior-year-tax", "30000", "--prior-year-agi", "120000",
         "--filing-status", "single", "--year", "2025",
         "--output-format", "json"] + extra,
        capture_output=True, text=True,
    )


def test_gate_g3_qbi_subtracts_from_liability():
    """G3: with SE income, the §199A deduction is subtracted by default and the
    projected liability falls by ~computed_QBI × marginal rate vs a --no-qbi run.

    Single 2025, $60k SE + $130k W-2 job. Std $15,750; ½ SE tax $4,238.86 →
    taxable before QBI $170,011.14 (24% bracket, below the §199A threshold). The
    deduction $11,152.23 (20% × adjusted QBI $55,761.14) keeps taxable income in
    the 24% bracket, so the liability drops by QBI × 24% (within rounding of the
    two separately-rounded income taxes). Single-pass fixed point — QBI does
    not depend on itself, so no iteration (documented in the code).
    """
    with_qbi = json.loads(_et_cli([]).stdout)
    no_qbi = json.loads(_et_cli(["--no-qbi"]).stdout)

    assert with_qbi["qbi"] is not None
    assert with_qbi["qbi"]["form"] == "8995"
    assert no_qbi["qbi"] is None
    qbi = with_qbi["qbi"]["deduction"]
    assert qbi == 11152.23

    drop = (no_qbi["estimated_liability"]["total"]
            - with_qbi["estimated_liability"]["total"])
    # SE tax / Additional Medicare / NIIT are unchanged between the runs; only
    # income tax moves, and the deduction sits entirely in the 24% bracket.
    assert drop == pytest.approx(qbi * 0.24, abs=0.01)
    assert drop > 0


def test_gate_g3_qbi_not_subtracted_without_se_income():
    """No SE income → no QBI computation (the deduction only applies to a trade/business)."""
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "estimated_tax_calculator.py"),
         "--projected-income", "120000", "--prior-year-tax", "30000",
         "--filing-status", "single", "--year", "2025", "--output-format", "json"],
        capture_output=True, text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["qbi"] is None


# ---------------------------------------------------------------------------
# SE health insurance / SE retirement are above-the-line Schedule 1
# deductions and must reduce AGI, not just feed the QBI calc.
# ---------------------------------------------------------------------------

def test_se_health_insurance_reduces_agi():
    """Single 2025, $100k SE income, $20k SE health insurance premiums.

    Half SE tax = $7,064.77 (14,129.55 SE tax / 2). AGI = 100,000 - 7,064.77
    - 20,000 = 72,935.23. Previously the $20,000 deduction was fed into the
    QBI calc but never subtracted from AGI, overstating AGI (and therefore
    projected liability) by the full $20,000.
    """
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "estimated_tax_calculator.py"),
         "--projected-se-income", "100000", "--projected-income", "100000",
         "--se-health-insurance", "20000", "--prior-year-tax", "20000",
         "--filing-status", "single", "--year", "2025", "--output-format", "json",
         "--no-qbi"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["projections"]["agi"] == 72935.23


def test_se_retirement_reduces_agi():
    """Same base scenario, using --se-retirement instead of health insurance;
    both are above-the-line Schedule 1 deductions and reduce AGI identically."""
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "estimated_tax_calculator.py"),
         "--projected-se-income", "100000", "--projected-income", "100000",
         "--se-retirement", "20000", "--prior-year-tax", "20000",
         "--filing-status", "single", "--year", "2025", "--output-format", "json",
         "--no-qbi"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["projections"]["agi"] == 72935.23
