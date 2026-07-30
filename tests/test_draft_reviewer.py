#!/usr/bin/env python3
"""Tests for the draft return reviewer (task P7) and the PHASE 2 ACCEPTANCE GATE.

Covers audit finding D3: the reviewer was a stub that checked only wages and the
standard deduction, with a comment reading "In a full implementation, we would
call check_tax, check_credits, etc." — while SKILL.md Step 5 presented it to the
user as the automated cross-check gate.

=========================== PHASE 2 GATE WORKSHEET ===========================
Synthetic 2025 MFJ return in examples/test_draft_review.json:
    W-2 wages                       120,000.00
    Schedule C net profit            40,000.00
    Qualified dividends               8,000.00
    Taxable interest                  2,000.00
    Net long-term capital gain       25,000.00
    Total income                    195,000.00

Schedule SE:
    SE base   = 40,000 x 0.9235                        = 36,940.00
    Social Security portion = 36,940 x 0.124           =  4,580.56
      (W-2 wages of 120,000 leave 56,100 of the 176,100
       wage base available, so the full base is taxed)
    Medicare portion        = 36,940 x 0.029           =  1,071.26
    Total SE tax                                       =  5,651.82
    Deductible half                                    =  2,825.91

    AGI = 195,000.00 - 2,825.91                        = 192,174.09
    Standard deduction (MFJ 2025, OBBBA)               =  31,500.00
    Taxable income                                     = 160,674.09

Qualified Dividends and Capital Gain Tax Worksheet:
    Preferential = 8,000 QD + 25,000 LTCG              =  33,000.00
    Ordinary     = 160,674.09 - 33,000                 = 127,674.09

    Ordinary tax (MFJ 2025 brackets):
        10% x 23,850                                   =   2,385.00
        12% x (96,950 - 23,850 = 73,100)               =   8,772.00
        22% x (127,674.09 - 96,950 = 30,724.09)        =   6,759.30
                                                 total =  17,916.30
    Preferential slice occupies [127,674.09, 160,674.09],
    entirely inside the 15% band (96,700 -> 600,050):
        15% x 33,000                                   =   4,950.00

    Line 16 tax                                        =  22,866.30
    Line 23 other taxes (SE tax)                       =   5,651.82
    LINE 24 TOTAL TAX                                  =  28,518.12

NIIT: AGI 192,174.09 < 250,000 MFJ threshold           ->      0.00
Additional Medicare: 120,000 + 40,000 < 250,000        ->      0.00
==============================================================================
"""

import copy
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
FIXTURE = os.path.join(ROOT, "examples", "test_draft_review.json")
sys.path.insert(0, SCRIPTS)

# Hand-computed expected values from the worksheet above.
EXPECTED_SE_TAX = 5651.82
EXPECTED_SE_DEDUCTION = 2825.91
EXPECTED_AGI = 192174.09
EXPECTED_TAXABLE_INCOME = 160674.09
EXPECTED_ORDINARY_TAX = 17916.30
EXPECTED_PREFERENTIAL_TAX = 4950.00
EXPECTED_LINE_16 = 22866.30
EXPECTED_TOTAL_TAX = 28518.12


@pytest.fixture
def draft():
    with open(FIXTURE) as f:
        return json.load(f)


def review(draft_dict, tmp_path, year="2025", output="json"):
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(draft_dict))
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "draft_reviewer.py"),
         "--draft", str(path), "--year", year, "--output-format", output],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if output == "json" else result.stdout


def errors(findings):
    return [f for f in findings if f["severity"] == "ERROR"]


# ---------------------------------------------------------------------------
# THE GATE
# ---------------------------------------------------------------------------

def test_gate_known_good_return_produces_no_errors(draft, tmp_path):
    """PHASE 2 GATE: the reviewer reproduces a known-good 2025 MFJ return.

    Every line of the fixture was hand-computed in this module's docstring; the
    reviewer independently recomputes them and must agree on all of them.
    """
    findings = review(draft, tmp_path)
    assert errors(findings) == [], (
        "known-good return flagged errors: "
        + "; ".join(f"{f['field']}: {f['message']}" for f in errors(findings))
    )


def test_gate_total_tax_within_one_dollar(draft, tmp_path):
    """PHASE 2 GATE: total tax reproduced to within $1 of the hand computation."""
    findings = review(draft, tmp_path)
    total = next(f for f in findings if f["field"] == "Total tax (Line 24)")
    assert abs(total["computed_value"] - EXPECTED_TOTAL_TAX) <= 1.00
    assert abs(total["draft_value"] - EXPECTED_TOTAL_TAX) <= 1.00


def test_gate_ltcg_stacking_matches_hand_computation(draft, tmp_path):
    """PHASE 2 GATE: the stacking worksheet result matches the hand computation.

    Ordinary $127,674.09 -> $17,916.30 and preferential $33,000 -> $4,950.00,
    summing to the $22,866.30 on line 16.
    """
    findings = review(draft, tmp_path)
    tax_finding = next(f for f in findings if f["field"] == "Tax (Line 16)")

    assert tax_finding["computed_value"] == EXPECTED_LINE_16
    # The message carries the two components so the number is auditable.
    assert f"${EXPECTED_ORDINARY_TAX:,.2f}" in tax_finding["message"]
    assert f"${EXPECTED_PREFERENTIAL_TAX:,.2f}" in tax_finding["message"]


def test_gate_se_tax_matches_hand_computation(draft, tmp_path):
    findings = review(draft, tmp_path)
    se = next(f for f in findings if f["field"] == "Self-employment tax")
    assert se["computed_value"] == EXPECTED_SE_TAX


# ---------------------------------------------------------------------------
# D3 — the checks actually detect errors
# ---------------------------------------------------------------------------

def test_detects_wrong_tax_line(draft, tmp_path):
    """A tax figure computed at ordinary rates on the gains is caught.

    If the preparer taxed the $33,000 of preferential income as ordinary income
    it would land near $25,120 instead of $22,866.30.
    """
    bad = copy.deepcopy(draft)
    bad["form_1040"]["line_16_tax"] = 25120.00
    bad["form_1040"]["line_24_total_tax"] = 25120.00 + EXPECTED_SE_TAX

    found = errors(review(bad, tmp_path))
    assert any(f["field"] == "Tax (1040 Line 16)" for f in found)


def test_detects_wrong_standard_deduction(draft, tmp_path):
    """The pre-OBBBA $30,000 MFJ figure must be flagged for 2025 (Y3)."""
    bad = copy.deepcopy(draft)
    bad["form_1040"]["line_12_deduction"] = 30000

    found = errors(review(bad, tmp_path))
    assert any("Standard deduction" in f["field"] for f in found)


def test_detects_total_income_that_does_not_add_up(draft, tmp_path):
    bad = copy.deepcopy(draft)
    bad["form_1040"]["line_9_total_income"] = 190000

    found = errors(review(bad, tmp_path))
    assert any("Total income" in f["field"] for f in found)


def test_detects_wages_not_matching_w2(draft, tmp_path):
    bad = copy.deepcopy(draft)
    bad["w2_records"][0]["box_1_wages"] = 125000

    found = errors(review(bad, tmp_path))
    assert any("Wages" in f["field"] for f in found)


def test_detects_wrong_se_tax(draft, tmp_path):
    """SE tax computed without regard to the W-2 wage-base offset, etc."""
    bad = copy.deepcopy(draft)
    bad["schedule_se"]["total_se_tax"] = 6120.00
    bad["schedule_2"]["line_4_self_employment_tax"] = 6120.00

    found = errors(review(bad, tmp_path))
    assert any("Self-employment tax" in f["field"] for f in found)


def test_detects_capital_loss_over_the_limit(draft, tmp_path):
    """Schedule D net loss deduction is capped at $3,000 ($1,500 MFS)."""
    bad = copy.deepcopy(draft)
    bad["schedule_d"] = {"short_term_gain_loss": -12000, "long_term_gain_loss": 0,
                         "total_gain_loss": -12000}
    bad["form_1040"]["line_7_capital_gain_loss"] = -12000

    found = errors(review(bad, tmp_path))
    assert any("Capital loss" in f["field"] for f in found)


def test_allows_capital_loss_at_the_limit(draft, tmp_path):
    bad = copy.deepcopy(draft)
    bad["schedule_d"] = {"short_term_gain_loss": -12000, "long_term_gain_loss": 0,
                         "total_gain_loss": -12000}
    bad["form_1040"]["line_7_capital_gain_loss"] = -3000

    found = errors(review(bad, tmp_path))
    assert not any("Capital loss" in f["field"] for f in found)


def test_detects_withholding_not_matching_w2(draft, tmp_path):
    bad = copy.deepcopy(draft)
    bad["w2_records"][0]["box_2_federal_withheld"] = 19000

    found = errors(review(bad, tmp_path))
    assert any("W-2 withholding" in f["field"] for f in found)


# ---------------------------------------------------------------------------
# F1/F2 — SALT is checked against the YEAR-CORRECT cap
# ---------------------------------------------------------------------------

def test_salt_of_40k_is_accepted_for_2025(draft, tmp_path):
    """F2: a $40,000 SALT deduction is CORRECT for 2025 and must not error.

    The old checklist rule flagged anything above $10,000 as a 🔴 ERROR.
    """
    d = copy.deepcopy(draft)
    d["schedule_a"] = {"line_6_salt_deduction": 40000}

    found = errors(review(d, tmp_path))
    assert not any("SALT" in f["field"] for f in found)


def test_salt_above_the_cap_is_flagged(draft, tmp_path):
    d = copy.deepcopy(draft)
    d["schedule_a"] = {"line_6_salt_deduction": 45000}

    found = errors(review(d, tmp_path))
    assert any("SALT" in f["field"] for f in found)


def test_salt_of_40k_is_flagged_for_2024(draft, tmp_path):
    """The same $40,000 IS an error for 2024, when the cap was $10,000."""
    d = copy.deepcopy(draft)
    d["tax_year"] = 2024
    d["schedule_a"] = {"line_6_salt_deduction": 40000}
    # 2024 figures so the rest of the return does not drown out the SALT finding
    d["form_1040"]["line_12_deduction"] = 29200

    found = errors(review(d, tmp_path, year="2024"))
    assert any("SALT" in f["field"] for f in found)


# ---------------------------------------------------------------------------
# Credits
# ---------------------------------------------------------------------------

def test_child_tax_credit_recomputed(draft, tmp_path):
    """P7: check_credits recomputes the CTC (2 children x $2,200 = $4,400)."""
    d = copy.deepcopy(draft)
    d["taxpayer_info"]["children_under_17"] = 2
    d["form_1040"]["line_19_child_tax_credit"] = 4400

    found = errors(review(d, tmp_path))
    assert not any("Child Tax Credit" in f["field"] for f in found)


def test_wrong_child_tax_credit_flagged(draft, tmp_path):
    """The pre-OBBBA $2,000/child figure is wrong for 2025 (F4)."""
    d = copy.deepcopy(draft)
    d["taxpayer_info"]["children_under_17"] = 2
    d["form_1040"]["line_19_child_tax_credit"] = 4000    # 2 x $2,000

    found = errors(review(d, tmp_path))
    assert any("Child Tax Credit" in f["field"] for f in found)


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

def test_reviewer_requires_filing_status(tmp_path):
    path = tmp_path / "no_status.json"
    path.write_text(json.dumps({"form_1040": {"line_1a_wages": 1000}}))
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "draft_reviewer.py"),
         "--draft", str(path), "--year", "2025"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0


def test_text_output_renders(draft, tmp_path):
    out = review(draft, tmp_path, output="text")
    assert "Draft Return Review Summary" in out
    assert "0 Errors" in out
