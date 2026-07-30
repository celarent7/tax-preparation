#!/usr/bin/env python3
"""End-to-end tests that no script silently falls back to 'single'.

Covers audit finding D2: only `tax_calculator.py` normalized filing-status
aliases. Everywhere else `.get(status, table["single"])` meant that a valid
alias like `mfj` was computed as a single filer — understating the 2025 MFJ
standard deduction by $15,750.

The Phase 1 acceptance gate is encoded in
`test_deduction_analyzer_mfj_uses_mfj_standard_deduction`.
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

from utils import load_tax_constants  # noqa: E402

SD_2025 = load_tax_constants("2025")["standard_deductions"]


def run(script, *args):
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, script), *args],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# deduction_analyzer.py — the Phase 1 gate
# ---------------------------------------------------------------------------

def test_deduction_analyzer_mfj_uses_mfj_standard_deduction():
    """D2 / Phase 1 gate: --filing-status mfj must use the MFJ standard deduction.

    Before the fix this reported $15,750 (single) instead of $31,500 (MFJ).
    """
    result = run("deduction_analyzer.py", "--agi", "100000",
                 "--filing-status", "mfj", "--year", "2025",
                 "--output-format", "json")
    assert result.returncode == 0, f"crashed: {result.stderr}"

    payload = json.loads(result.stdout)
    assert payload["standard_deduction"]["total"] == SD_2025["married_jointly"] == 31500
    assert payload["standard_deduction"]["total"] != SD_2025["single"]
    assert payload["filing_status"] == "married_jointly"


@pytest.mark.parametrize("alias,canonical", [
    ("mfj", "married_jointly"),
    ("married_filing_jointly", "married_jointly"),
    ("mfs", "married_separately"),
    ("hoh", "head_of_household"),
    ("qss", "qualifying_surviving_spouse"),
    ("single", "single"),
])
def test_deduction_analyzer_accepts_all_aliases(alias, canonical):
    """D2: every alias resolves to its own status's standard deduction."""
    result = run("deduction_analyzer.py", "--agi", "100000",
                 "--filing-status", alias, "--year", "2025",
                 "--output-format", "json")
    assert result.returncode == 0, f"alias {alias} crashed: {result.stderr}"
    payload = json.loads(result.stdout)
    assert payload["standard_deduction"]["base"] == SD_2025[canonical]


def test_deduction_analyzer_rejects_unknown_status():
    """D2: a bogus status must fail loudly, not compute as single."""
    result = run("deduction_analyzer.py", "--agi", "100000",
                 "--filing-status", "marreid", "--year", "2025")
    assert result.returncode != 0
    assert "marreid" in (result.stdout + result.stderr)


# ---------------------------------------------------------------------------
# estimated_tax_calculator.py
# ---------------------------------------------------------------------------

def test_estimated_tax_mfj_differs_from_single():
    """D2: MFJ brackets + standard deduction must actually be used."""
    common = ["--projected-income", "200000", "--prior-year-tax", "30000",
              "--prior-year-agi", "190000", "--year", "2025",
              "--output-format", "json"]

    mfj = json.loads(run("estimated_tax_calculator.py", *common,
                         "--filing-status", "mfj").stdout)
    single = json.loads(run("estimated_tax_calculator.py", *common,
                            "--filing-status", "single").stdout)

    # MFJ gets a bigger standard deduction and wider brackets => less tax
    assert mfj["projections"]["taxable_income"] < single["projections"]["taxable_income"]
    assert mfj["estimated_liability"]["income_tax"] < single["estimated_liability"]["income_tax"]


def test_estimated_tax_rejects_unknown_status():
    result = run("estimated_tax_calculator.py", "--projected-income", "100000",
                 "--prior-year-tax", "10000", "--filing-status", "nonsense")
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# tax_calculator.py (already normalized — guard against regression)
# ---------------------------------------------------------------------------

def test_tax_calculator_rejects_unknown_status():
    result = run("tax_calculator.py", "--gross-income", "100000",
                 "--filing-status", "nope")
    assert result.returncode != 0


def test_tax_calculator_mfj_alias_matches_canonical():
    alias = json.loads(run("tax_calculator.py", "--gross-income", "200000",
                           "--filing-status", "mfj", "--year", "2025",
                           "--output-format", "json").stdout)
    canonical = json.loads(run("tax_calculator.py", "--gross-income", "200000",
                               "--filing-status", "married_jointly", "--year", "2025",
                               "--output-format", "json").stdout)
    assert alias["tax"]["tax_before_credits"] == canonical["tax"]["tax_before_credits"]


# ---------------------------------------------------------------------------
# tax_savings_finder.py — status comes from the profile JSON, not a CLI flag
# ---------------------------------------------------------------------------

def test_savings_finder_rejects_unknown_status_in_profile(tmp_path):
    """D2: a bad filing_status in the data file must not compute as single."""
    profile = tmp_path / "bad_status.json"
    profile.write_text(json.dumps({
        "filing_status": "marreid", "agi": 150000, "itemized_total": 20000,
    }))
    result = run("tax_savings_finder.py", "--data", str(profile))
    assert result.returncode != 0
    assert "marreid" in (result.stdout + result.stderr)


def test_savings_finder_requires_filing_status(tmp_path):
    """D2: an absent filing_status is a missing required field, not 'single'."""
    profile = tmp_path / "no_status.json"
    profile.write_text(json.dumps({"agi": 150000, "itemized_total": 20000}))
    result = run("tax_savings_finder.py", "--data", str(profile))
    assert result.returncode != 0
