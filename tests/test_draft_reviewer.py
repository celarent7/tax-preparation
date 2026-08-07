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

from credit_eligibility_checker import get_credit_params  # noqa: E402
from estimated_tax_calculator import calculate_se_tax  # noqa: E402
from tax_calculator import calculate_tax  # noqa: E402
from utils import load_tax_constants  # noqa: E402
import qbi_calculator  # noqa: E402
from draft_reviewer import ReviewContext, Severity, check_excess_sdi  # noqa: E402

_TAX_DATA_2025 = load_tax_constants("2025")

# Capped-2023-shape SDI config (synthetic — CA 2023 constants are not yet in the
# constants file; see plan C3). Used to exercise the capped-year credit path
# without depending on data that does not exist yet.
CAPPED_CFG = {"rate": 0.009, "wage_cap": 153164,
              "excess_credit_multiple_employers": True}


def _review_ctx(year="2023"):
    """Build a ReviewContext for direct check_* calls.

    Only `year` and `exact_tolerance` are read by check_excess_sdi; the remaining
    fields are populated from the real 2025 constants so the dataclass is valid.
    """
    td = _TAX_DATA_2025
    return ReviewContext(
        year=year,
        tax_brackets=td["tax_brackets_processed"],
        ltcg_brackets=td["ltcg_brackets_processed"],
        standard_deductions=td["standard_deductions"],
        additional_deductions=td["additional_deduction_65_blind"],
        ctc_params=get_credit_params(td)["child_tax_credit"],
        salt_constants=td["salt"],
        capital_loss_limit=td["capital_loss_limit"],
        capital_loss_limit_mfs=td["capital_loss_limit_mfs"],
        ss_wage_base=td["se_tax"]["ss_wage_base"],
        se_tax_factor=td["se_tax"]["se_adjustment_factor"],
        se_ss_rate=td["se_tax"]["ss_rate"],
        se_medicare_rate=td["se_tax"]["medicare_rate"],
        add_medicare_threshold_single=td["se_tax"]["additional_medicare_threshold_single"],
        add_medicare_threshold_mfj=td["se_tax"]["additional_medicare_threshold_married"],
        hsa_limit_self=td["retirement_limits"]["hsa"]["single"],
        hsa_limit_family=td["retirement_limits"]["hsa"]["family"],
        charitable_cash_limit=td["credits"]["charitable_cash_limit"],
        charitable_property_limit=td["charitable_property_agi_limit"],
        educator_limit=td["educator_expense_limit"],
        student_loan_limit=td["student_loan_interest_limit"],
        year_constants=td,
    )


@pytest.fixture
def ca_2023_style_context():
    """A ReviewContext pinned to tax year 2023 (a capped SDI year)."""
    return _review_ctx("2023")

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


# ---------------------------------------------------------------------------
# check_total_tax must account for the Child Tax Credit (line 19)
# ---------------------------------------------------------------------------

def test_total_tax_accounts_for_child_tax_credit(draft, tmp_path):
    """A correct return with a nonzero CTC must not be falsely flagged.

    2 qualifying children -> CTC $4,400 (2025 OBBBA $2,200/child). Line 24
    = line 16 ($22,866.30) - CTC ($4,400) + line 23 SE tax ($5,651.82)
    = $24,118.12. The reviewer previously computed expected line 24 as just
    line 16 + line 23 ($28,518.12), which would falsely flag this correct
    return as a ~$4,400 error.
    """
    good = copy.deepcopy(draft)
    good["taxpayer_info"]["children_under_17"] = 2
    good["form_1040"]["line_19_child_tax_credit"] = 4400
    good["form_1040"]["line_24_total_tax"] = round(
        (EXPECTED_LINE_16 - 4400) + EXPECTED_SE_TAX, 2)

    findings = review(good, tmp_path)
    assert errors(findings) == [], (
        "correct return with CTC flagged errors: "
        + "; ".join(f"{f['field']}: {f['message']}" for f in errors(findings))
    )
    total = next(f for f in findings if f["field"] == "Total tax (Line 24)")
    assert total["computed_value"] == 24118.12


def test_total_tax_still_catches_a_wrong_total_with_ctc_present(draft, tmp_path):
    """The old line16+line23 formula (ignoring CTC) must now be flagged as
    wrong, since it overstates total tax by the CTC amount once CTC is
    nonzero."""
    bad = copy.deepcopy(draft)
    bad["taxpayer_info"]["children_under_17"] = 2
    bad["form_1040"]["line_19_child_tax_credit"] = 4400
    bad["form_1040"]["line_24_total_tax"] = round(EXPECTED_LINE_16 + EXPECTED_SE_TAX, 2)

    found = errors(review(bad, tmp_path))
    assert any(f["field"] == "Total tax (1040 Line 24)" for f in found)


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


# ---------------------------------------------------------------------------
# G4 — check_qbi recomputes the §199A deduction against the draft
# ---------------------------------------------------------------------------
#
# A self-consistent synthetic 2025 MFJ return with a Schedule C business, built
# FROM the same engine the reviewer calls so the "correct" draft reproduces
# exactly. W-2 wages ($50k) are deliberately kept under the SE base so the
# reviewer's check_se_tax (which consumes the SS wage base with W-2 wages first)
# and the QBI engine's calculate_se_tax (which does not) agree on the SE tax —
# keeping every line of the return internally consistent.
#
#   SE profit 100,000; SE health insurance 10,000; W-2 wages 50,000
#   SE base 92,350 -> SS 11,451.40 + Medicare 2,678.15 = 14,129.55; half 7,064.77
#   AGI 150,000 - 7,064.77 = 142,935.23; std (MFJ 2025) 31,500
#   taxable before QBI = 111,435.23  (below the MFJ §199A threshold -> Form 8995)
#   adjusted QBI = 100,000 - 7,064.77 - 10,000 = 82,935.23
#   component = 20% x 82,935.23 = 16,587.05; overall limit 20% x 111,435.23 =
#   22,287.05 -> deduction 16,587.05 (overall limit does not bind)
def _qbi_draft(line13_override=None, ca_qbi=None):
    """Build a self-consistent QBI-claiming draft; return (draft, true_deduction).

    ``line13_override`` lets a test state a WRONG 1040 line 13 while keeping every
    other line consistent with that stated value (so the ONLY QBI error is the
    line-13 mismatch, not a downstream taxable-income error). The reviewer
    recomputes QBI from the qbi block's real inputs, which carry the TRUE
    taxable_income_before_qbi regardless of the stated line 13.
    """
    td = _TAX_DATA_2025
    se_profit, se_health, wages = 100000.0, 10000.0, 50000.0
    std = 31500.0
    se = calculate_se_tax(se_profit, td["se_tax"])
    half_se, se_total = se["deduction"], se["total"]
    agi = (wages + se_profit) - half_se
    ti_before = agi - std

    profile = {
        "se_net_profit": se_profit,
        "se_tax_half": half_se,
        "se_health_insurance": se_health,
        "w2_wages_paid": 0.0,
        "ubia": 0.0,
        "is_sstb": False,
        "taxable_income_before_qbi": round(ti_before, 2),
        "net_capital_gain": 0.0,
        "reit_dividends": 0.0,
        "filing_status": "married_jointly",
    }
    qbi = qbi_calculator.compute_qbi_deduction(profile, td)
    true_deduction = qbi.deduction

    line13 = true_deduction if line13_override is None else line13_override
    line15 = round(ti_before - line13, 2)
    ord_tax, _ = calculate_tax(line15, "married_jointly", td["tax_brackets_processed"])
    line16 = round(ord_tax, 2)
    line23 = se_total
    line24 = round(line16 + line23, 2)

    draft = {
        "tax_year": 2025,
        "filing_status": "married_jointly",
        "taxpayer_info": {"age_65_plus": 0, "blind": 0, "children_under_17": 0},
        "form_1040": {
            "line_1a_wages": wages,
            "line_2b_taxable_interest": 0,
            "line_3a_qualified_dividends": 0,
            "line_3b_ordinary_dividends": 0,
            "line_7_capital_gain_loss": 0,
            "line_8_other_income": se_profit,
            "line_9_total_income": wages + se_profit,
            "line_10_adjustments": half_se,
            "line_11_agi": round(agi, 2),
            "line_12_deduction": std,
            "line_13_qbi_deduction": round(line13, 2),
            "line_15_taxable_income": line15,
            "line_16_tax": line16,
            "line_23_other_taxes": line23,
            "line_24_total_tax": line24,
            "line_25a_w2_withholding": 0,
            "line_25d_total_withholding": 0,
            "line_26_estimated_payments": 0,
            "line_33_total_payments": 0,
        },
        "schedule_1": {"line_3_business_income": se_profit,
                       "line_15_deductible_se_tax": half_se},
        "schedule_2": {"line_4_self_employment_tax": se_total},
        "schedule_se": {"net_profit": se_profit, "total_se_tax": se_total},
        "w2_records": [{"employer": "Synthetic", "box_1_wages": wages,
                       "box_2_federal_withheld": 0}],
        "qbi": {
            "se_net_profit": se_profit,
            "se_health_insurance": se_health,
            "w2_wages_paid": 0.0,
            "ubia": 0.0,
            "is_sstb": False,
            "reit_dividends": 0.0,
            "taxable_income_before_qbi": round(ti_before, 2),
            "net_capital_gain": 0.0,
        },
    }
    if ca_qbi is not None:
        draft["schedule_ca"] = {"qbi_adjustment": ca_qbi}
    return draft, true_deduction


def test_gate_g4_correct_qbi_draft_passes(tmp_path):
    """G4: a draft whose 1040 line 13 equals the engine recomputation is OK."""
    draft, _ = _qbi_draft()
    findings = review(draft, tmp_path)
    assert errors(findings) == [], (
        "self-consistent QBI draft flagged errors: "
        + "; ".join(f"{f['field']}: {f['message']}" for f in errors(findings)))
    qbi_ok = [f for f in findings if f["category"] == "QBI"
              and f["severity"] == "OK"
              and "Line 13" in f["field"]]
    assert qbi_ok, "expected an OK finding for the QBI deduction (Line 13)"


def test_gate_g4_qbi_line_13_too_high_flagged(tmp_path):
    """G4: a draft whose 1040 line 13 is $1,000 too high is an ERROR.

    The draft keeps every other line consistent with the OVERSTATED line 13, so
    no downstream check fires — the only error is the QBI recomputation, which
    uses the qbi block's true taxable_income_before_qbi.
    """
    draft, true_deduction = _qbi_draft()
    bad, _ = _qbi_draft(line13_override=true_deduction + 1000.0)

    findings = review(bad, tmp_path)
    qbi_errs = [f for f in errors(findings)
                if f["category"] == "QBI" and "Line 13" in f["field"]]
    assert qbi_errs, "an overstated line 13 must be flagged as a QBI error"
    # The diff should be the $1,000 overstatement (within the $1 tolerance).
    assert abs(qbi_errs[0]["difference"] - 1000.0) <= 1.0

    # And no OTHER errors — the only defect is the line-13 overstatement.
    other = [f for f in errors(findings) if f["category"] != "QBI"]
    assert other == [], (
        "unexpected non-QBI errors: "
        + "; ".join(f"{f['field']}: {f['message']}" for f in other))


def test_gate_g4_ca_state_column_qbi_flagged(tmp_path):
    """G4: claiming QBI in the CA state column (a negative Schedule CA
    adjustment) is an ERROR — California has no IRC 199A deduction; QBI must be
    ADDED BACK (positive adjustment), not subtracted."""
    _, true_deduction = _qbi_draft()
    draft, _ = _qbi_draft(ca_qbi=-true_deduction)

    findings = review(draft, tmp_path)
    ca_errs = [f for f in errors(findings)
               if f["category"] == "QBI" and "Schedule CA" in f["field"]]
    assert ca_errs, "a CA state-column QBI subtraction must be flagged"
    assert "no IRC 199A" in ca_errs[0]["message"]
    # The federal line 13 itself is still correct.
    line13_errs = [f for f in errors(findings)
                   if f["category"] == "QBI" and "Line 13" in f["field"]]
    assert line13_errs == []


# ---------------------------------------------------------------------------
# C1 — check_excess_sdi: Form 540 line 74 for multi-employer CA filers
# ---------------------------------------------------------------------------
#
# The credit only exists for a capped SDI year with 2+ employers. CA 2023
# constants are not in the constants file yet (plan C3), so the capped tests
# inject CAPPED_CFG directly via the check's injectable `sdi_config` parameter;
# the uncapped-2025 test exercises the default resolution path against the real
# CA 2025 sdi block (wage_cap: null).

def test_excess_sdi_missing_from_draft_is_error(ca_2023_style_context):
    """Two employers over the capped-year max, draft line 74 empty -> ERROR."""
    computed = {"state": "CA", "sdi_by_employer": [900.0, 900.0]}
    draft = {"filing_status": "mfj",
             "form_540": {"line_74_excess_sdi": 0}}
    findings = check_excess_sdi(draft, computed, ca_2023_style_context,
                                sdi_config=CAPPED_CFG)
    assert any(f.severity == Severity.ERROR and "line 74" in f.message.lower()
               for f in findings), (
        "expected an ERROR naming line 74; got: "
        + "; ".join(f"{f.severity.name}: {f.message}" for f in findings))


def test_excess_sdi_correct_on_draft_is_ok(ca_2023_style_context):
    """Draft carries the computed credit -> single OK finding, no errors."""
    computed = {"state": "CA", "sdi_by_employer": [900.0, 900.0]}
    # credit = 1800 - 0.009 x 153,164 = 1800 - 1378.48 = 421.52
    draft = {"filing_status": "mfj",
             "form_540": {"line_74_excess_sdi": 421.52}}
    findings = check_excess_sdi(draft, computed, ca_2023_style_context,
                                sdi_config=CAPPED_CFG)
    assert not any(f.severity == Severity.ERROR for f in findings)
    ok = [f for f in findings if f.severity == Severity.OK]
    assert ok and "line 74" in ok[0].message.lower()


def test_uncapped_2025_two_employers_info_only(ca_2023_style_context):
    """CA 2025 (no cap): no ERROR ever; at most an INFO explaining why.

    Uses the default sdi_config resolution (load_state_constants CA 2025), which
    returns wage_cap: null — the semantic null must be honored, never raised.
    """
    computed = {"state": "CA", "sdi_by_employer": [3000.0, 3000.0]}
    draft = {"filing_status": "mfj",
             "form_540": {"line_74_excess_sdi": 0}}
    ctx = _review_ctx("2025")
    findings = check_excess_sdi(draft, computed, ctx)
    assert not any(f.severity == Severity.ERROR for f in findings)
    assert all(f.severity == Severity.INFO for f in findings)
    if findings:
        assert "no wage cap" in findings[0].message.lower()


def test_non_ca_returns_no_findings(ca_2023_style_context):
    """A non-CA return has no excess-SDI check to perform."""
    computed = {"state": "TX", "sdi_by_employer": [500.0, 500.0]}
    draft = {"filing_status": "mfj",
             "form_540": {"line_74_excess_sdi": 0}}
    assert check_excess_sdi(draft, computed, ca_2023_style_context) == []
