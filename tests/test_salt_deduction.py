#!/usr/bin/env python3
"""Tests for the OBBBA SALT deduction cap and its MAGI phase-down.

Covers audit findings:
  F1 — `salt_cap: 10000` for 2025. OBBBA raised the cap to $40,000 ($20,000 MFS),
       phased down by 30% of MAGI over $500,000 ($250,000 MFS), floor $10,000
       ($5,000 MFS).
  F2 — the draft-review checklist flagged Schedule A line 6 > $10,000 as an
       ERROR, i.e. it called a *correct* 2025 return wrong.

Primary source for every figure asserted here:
  IRS, "Instructions for Schedule A (Form 1040) (2025)",
  https://www.irs.gov/instructions/i1040sca (retrieved 2026-07-29):
  "The overall limit on the deduction for state and local income, sales, and
   property taxes has increased to $40,000 ($20,000 if married filing
   separately). The overall limit is reduced if your modified adjusted gross
   income is more than $500,000 ($250,000 if married filing separately) but
   will not be reduced below $10,000 ($5,000 if married filing separately)."

Expected values below are hand-computed from that rule, not read off the code.
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

from deduction_analyzer import analyze_salt_deduction, resolve_salt_cap  # noqa: E402
from utils import load_tax_constants  # noqa: E402


# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------

def test_2025_salt_block_matches_obbba():
    """F1: the 2025 constants carry the OBBBA SALT structure, not the $10K cap."""
    salt = load_tax_constants("2025")["salt"]
    assert salt["cap"] == 40000
    assert salt["cap_mfs"] == 20000
    assert salt["phasedown"] is True
    assert salt["phasedown_magi_start"] == 500000
    assert salt["phasedown_magi_start_mfs"] == 250000
    assert salt["phasedown_rate"] == 0.30
    assert salt["floor"] == 10000
    assert salt["floor_mfs"] == 5000


def test_2024_salt_block_is_pre_obbba():
    """F1: 2024 keeps the pre-OBBBA $10,000/$5,000 cap with no phase-down."""
    salt = load_tax_constants("2024")["salt"]
    assert salt["cap"] == 10000
    assert salt["cap_mfs"] == 5000
    assert salt["phasedown"] is False


# ---------------------------------------------------------------------------
# Cap resolution — hand-computed expected values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,magi,expected,why", [
    # Below the phase-down threshold: full cap.
    ("married_jointly", 0,        40000, "no MAGI given / below threshold"),
    ("married_jointly", 300000,   40000, "below $500K threshold"),
    ("married_jointly", 500000,   40000, "exactly at threshold, not over it"),
    # In the phase-down band: 40000 - 0.30 * (MAGI - 500000)
    ("married_jointly", 510000,   37000, "40000 - 0.30*10000 = 37000"),
    ("married_jointly", 550000,   25000, "40000 - 0.30*50000 = 25000"),
    ("married_jointly", 599000,   10300, "40000 - 0.30*99000 = 10300"),
    # Floor reached at MAGI 600000 (40000 - 0.30*100000 = 10000) and beyond.
    ("married_jointly", 600000,   10000, "phase-down exactly hits the floor"),
    ("married_jointly", 900000,   10000, "floored at 10000, never below"),
    ("single",          550000,   25000, "single uses the same $40K/$500K figures"),
    # MFS: half of everything.
    ("married_separately", 200000, 20000, "below $250K MFS threshold"),
    ("married_separately", 260000, 17000, "20000 - 0.30*10000 = 17000"),
    ("married_separately", 300000,  5000, "20000 - 0.30*50000 = 5000, at floor"),
    ("married_separately", 400000,  5000, "floored at 5000 MFS"),
])
def test_resolve_salt_cap_2025(status, magi, expected, why):
    """F1: cap resolution matches the statute at, below and above the band."""
    salt = load_tax_constants("2025")["salt"]
    assert resolve_salt_cap(salt, status, magi) == expected, why


@pytest.mark.parametrize("status,magi,expected", [
    ("married_jointly", 100000, 10000),
    ("married_jointly", 900000, 10000),   # no phase-down existed in 2024
    ("married_separately", 900000, 5000),
])
def test_resolve_salt_cap_2024_has_no_phasedown(status, magi, expected):
    """F1: a pre-OBBBA year must not phase the cap down."""
    salt = load_tax_constants("2024")["salt"]
    assert resolve_salt_cap(salt, status, magi) == expected


# ---------------------------------------------------------------------------
# Full SALT analysis
# ---------------------------------------------------------------------------

def test_salt_analysis_high_salt_mfj_2025_deducts_40k():
    """F1: a CA itemizer with $45K of SALT now deducts $40K, not $10K.

    This is the headline dollar impact in the audit: understating deductions
    by up to $30,000.
    """
    salt = load_tax_constants("2025")["salt"]
    result = analyze_salt_deduction(
        property_tax=12000, state_income_tax=33000, state_sales_tax=0,
        filing_status="married_jointly", salt_constants=salt, magi=250000,
    )
    assert result["salt_cap"] == 40000
    assert result["deductible_amount"] == 40000
    assert result["lost_to_cap"] == 5000       # 45000 total - 40000 cap


def test_salt_analysis_reports_phasedown_details():
    """F1: output must show the phase-down so a reviewer can audit the number."""
    salt = load_tax_constants("2025")["salt"]
    result = analyze_salt_deduction(
        property_tax=20000, state_income_tax=40000, state_sales_tax=0,
        filing_status="married_jointly", salt_constants=salt, magi=550000,
    )
    assert result["salt_cap"] == 25000
    assert result["phasedown_applied"] is True
    assert result["cap_before_phasedown"] == 40000
    assert result["deductible_amount"] == 25000
    assert result["lost_to_cap"] == 35000      # 60000 total - 25000 cap


def test_salt_income_or_sales_tax_election_preserved():
    """Regression: the larger of income vs sales tax is still used, not both."""
    salt = load_tax_constants("2025")["salt"]
    result = analyze_salt_deduction(
        property_tax=5000, state_income_tax=3000, state_sales_tax=9000,
        filing_status="single", salt_constants=salt, magi=100000,
    )
    assert result["used_sales_tax_method"] is True
    assert result["income_or_sales_used"] == 9000
    assert result["deductible_amount"] == 14000   # 5000 + 9000, under the cap


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_deduction_analyzer_cli_applies_40k_cap():
    """F1 end-to-end: the CLI reports the OBBBA cap for 2025."""
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "deduction_analyzer.py"),
         "--agi", "250000", "--filing-status", "mfj", "--year", "2025",
         "--property-tax", "12000", "--state-income-tax", "33000",
         "--output-format", "json"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["itemized_breakdown"]["salt"]["salt_cap"] == 40000
    assert payload["itemized_breakdown"]["salt"]["deductible_amount"] == 40000


def test_deduction_analyzer_cli_magi_drives_phasedown():
    """F1 end-to-end: --magi above $500K phases the cap down."""
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "deduction_analyzer.py"),
         "--agi", "550000", "--filing-status", "mfj", "--year", "2025",
         "--magi", "550000",
         "--property-tax", "20000", "--state-income-tax", "40000",
         "--output-format", "json"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    salt = json.loads(result.stdout)["itemized_breakdown"]["salt"]
    assert salt["salt_cap"] == 25000
    assert salt["phasedown_applied"] is True


# ---------------------------------------------------------------------------
# E1 — 2026 SALT cap primary-source resolution
# Primary source: IRC 164(b)(6)/(b)(7) as amended by Pub. L. 119-21 §70120
# (One Big Beautiful Bill Act), retrieved via Cornell LII current-law text.
# See scripts/tax_constants.json _sources.obbba_salt_2026 for the verbatim quote.
#
# Statute settles the two points secondary sources conflicted on:
#   * MFS cap ESCALATES ("half the applicable limitation amount"), not frozen.
#   * 2026 phase-down start is $505,000 (101% of $500,000), not $500,500.
# ---------------------------------------------------------------------------

def test_2026_salt_constants_resolved():
    """E1: 2026 SALT figures from Pub. L. 119-21 §70120 (see _sources.obbba_salt_2026).

    Verbatim from IRC 164(b)(7) (Cornell LII current text):
      (b)(7)(A)(ii)  "in the case of any taxable year beginning in calendar
                      year 2026, $40,400"
      (b)(6)(B)      "... (half the applicable limitation amount in the case
                      of a married individual filing a separate return)"
      (b)(7)(B)(ii)(II) "in the case of any taxable year beginning in
                        calendar year 2026, $505,000"
      (b)(7)(B)(i)   "... half the threshold amount in the case of a married
                      individual filing a separate return"
    """
    salt = load_tax_constants("2026")["salt"]
    assert salt["cap"] == 40400
    # MFS escalates (half of the general amount), NOT frozen at $20,000.
    assert salt["cap_mfs"] == salt["cap"] / 2 == 20200
    assert salt["phasedown_magi_start"] == 505000
    assert salt["phasedown_magi_start_mfs"] == salt["phasedown_magi_start"] / 2 == 252500
    assert salt["phasedown"] is True


def test_2026_salt_phasedown_computes():
    """E1: resolve_salt_cap no longer raises for 2026 and floors at $10,000."""
    salt = load_tax_constants("2026")["salt"]
    # Below the phase-down start -> full cap.
    assert resolve_salt_cap(salt, "single", magi=400000) == salt["cap"] == 40400
    # At the start -> full cap (boundary is inclusive: magi <= start).
    assert resolve_salt_cap(salt, "single", magi=505000) == 40400
    # Extreme MAGI -> floored at $10,000 (0.3 * (2,000,000 - 505,000) >> cap).
    assert resolve_salt_cap(salt, "single", magi=2000000) == salt["floor"] == 10000
    # MFS mirrors with half-cap ($20,200) and half-threshold ($252,500).
    # 250,000 <= 252,500 -> full MFS cap (boundary inclusive).
    assert resolve_salt_cap(salt, "mfs", magi=250000) == salt["cap_mfs"] == 20200
    # Extreme MAGI -> floored at $5,000 MFS.
    assert resolve_salt_cap(salt, "mfs", magi=2000000) == salt["floor_mfs"] == 5000
