#!/usr/bin/env python3
"""
Draft Tax Return Reviewer — Cross-check a draft tax return against computed data
to catch errors, discrepancies, and missing items before filing.

Severity levels:
  🔴 ERROR   — Definite mistake that must be corrected
  🟡 WARNING — Likely mistake requiring human verification
  🟢 OK      — Value matches computed data
  ℹ️  INFO    — Advisory note
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

# Add the scripts directory to the path to import utils
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from credit_eligibility_checker import calculate_child_tax_credit, get_credit_params
from deduction_analyzer import resolve_salt_cap
from tax_calculator import calculate_preferential_tax, calculate_tax
from utils import (
    excess_sdi_credit,
    ca_529_nonqualified_withdrawal,
    get_status_value,
    load_state_constants,
    load_tax_constants,
    make_year_arg,
    normalize_filing_status,
    select_for_status,
    UnverifiedConstantError,
)


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

class Severity(Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    OK = "OK"
    INFO = "INFO"

    @property
    def icon(self) -> str:
        return {
            "ERROR": "🔴",
            "WARNING": "🟡",
            "OK": "🟢",
            "INFO": "ℹ️",
        }[self.value]


@dataclass
class Finding:
    severity: Severity
    category: str
    field: str
    message: str
    draft_value: Any = None
    computed_value: Any = None
    difference: Any = None


@dataclass
class ReviewContext:
    year: str
    tax_brackets: Dict
    ltcg_brackets: Dict
    standard_deductions: Dict
    additional_deductions: Dict
    ctc_params: Dict
    salt_constants: Dict
    capital_loss_limit: float
    capital_loss_limit_mfs: float
    ss_wage_base: float
    se_tax_factor: float
    se_ss_rate: float
    se_medicare_rate: float
    add_medicare_threshold_single: float
    add_medicare_threshold_mfj: float
    hsa_limit_self: float
    hsa_limit_family: float
    charitable_cash_limit: float
    charitable_property_limit: float
    educator_limit: float
    student_loan_limit: float
    year_constants: Optional[Dict] = None
    exact_tolerance: float = 1.0
    estimate_tolerance: float = 0.05


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def compare_values(draft: float, computed: float, tolerance: float = 1.0) -> Tuple[bool, float]:
    diff = draft - computed
    return abs(diff) <= tolerance, diff


# ---------------------------------------------------------------------------
# Core Review Logic
# ---------------------------------------------------------------------------

def _f(draft: Dict, key: str, default: float = 0.0) -> float:
    """Read a numeric line from the draft's form_1040 block."""
    return float(draft.get("form_1040", {}).get(key, default) or 0.0)


def _status(draft: Dict) -> str:
    if not draft.get("filing_status"):
        raise ValueError("Draft return must specify 'filing_status'.")
    return normalize_filing_status(draft["filing_status"])


def _ok(category, field, message, draft_value=None, computed_value=None):
    return Finding(Severity.OK, category, field, message, draft_value, computed_value)


def check_income(draft: Dict, computed: Dict, ctx: ReviewContext) -> List[Finding]:
    """Total income (line 9) must equal the sum of its components."""
    findings = []
    components = {
        "wages (1a)": _f(draft, "line_1a_wages"),
        "taxable interest (2b)": _f(draft, "line_2b_taxable_interest"),
        "ordinary dividends (3b)": _f(draft, "line_3b_ordinary_dividends"),
        "capital gain/loss (7)": _f(draft, "line_7_capital_gain_loss"),
        "other income (8)": _f(draft, "line_8_other_income"),
    }
    expected = sum(components.values())
    stated = _f(draft, "line_9_total_income")
    match, diff = compare_values(stated, expected, ctx.exact_tolerance)

    if not match:
        findings.append(Finding(
            Severity.ERROR, "Income", "Total income (1040 Line 9)",
            "Total income does not equal the sum of its components: "
            + ", ".join(f"{k} ${v:,.2f}" for k, v in components.items()),
            stated, round(expected, 2), round(diff, 2)))
    else:
        findings.append(_ok("Income", "Total income (Line 9)",
                            "Matches the sum of its components", stated, round(expected, 2)))

    # Wages should tie to the W-2 records when they are supplied.
    w2s = draft.get("w2_records") or []
    if w2s:
        w2_total = sum(float(w.get("box_1_wages", 0)) for w in w2s)
        match, diff = compare_values(_f(draft, "line_1a_wages"), w2_total, ctx.exact_tolerance)
        if not match:
            findings.append(Finding(
                Severity.ERROR, "Income", "Wages (Line 1a)",
                "Wages on the return do not match the sum of W-2 Box 1 amounts.",
                _f(draft, "line_1a_wages"), round(w2_total, 2), round(diff, 2)))
        else:
            findings.append(_ok("Income", "Wages (Line 1a)", "Matches W-2 Box 1 total",
                                _f(draft, "line_1a_wages"), round(w2_total, 2)))
    return findings


def check_agi(draft: Dict, computed: Dict, ctx: ReviewContext) -> List[Finding]:
    """AGI (line 11) = total income (9) - adjustments (10)."""
    expected = _f(draft, "line_9_total_income") - _f(draft, "line_10_adjustments")
    stated = _f(draft, "line_11_agi")
    match, diff = compare_values(stated, expected, ctx.exact_tolerance)
    if not match:
        return [Finding(Severity.ERROR, "AGI", "AGI (1040 Line 11)",
                        "AGI does not equal total income minus adjustments.",
                        stated, round(expected, 2), round(diff, 2))]
    return [_ok("AGI", "AGI (Line 11)", "Total income minus adjustments", stated, round(expected, 2))]


def check_deductions(draft: Dict, computed: Dict, ctx: ReviewContext) -> List[Finding]:
    """Standard deduction must match filing status, age and blindness."""
    findings = []
    status = _status(draft)
    stated = _f(draft, "line_12_deduction")

    base_sd = get_status_value(ctx.standard_deductions, status, "standard deduction")
    age_blind_amt = get_status_value(ctx.additional_deductions, status,
                                     "additional 65/blind deduction")
    info = draft.get("taxpayer_info", {})
    count = int(info.get("age_65_plus", 0)) + int(info.get("blind", 0))
    expected_sd = base_sd + age_blind_amt * count

    if draft.get("deductions", {}).get("itemizing") or draft.get("schedule_a"):
        findings.append(Finding(
            Severity.INFO, "Deductions", "Itemized deduction (Line 12)",
            f"Return itemizes. Standard deduction would have been ${expected_sd:,.2f} — "
            "confirm itemizing is actually better.",
            stated, expected_sd))
        return findings

    match, diff = compare_values(stated, expected_sd, ctx.exact_tolerance)
    if not match:
        findings.append(Finding(
            Severity.ERROR, "Deductions", "Standard deduction (Line 12)",
            f"Standard deduction is incorrect for {status} in {ctx.year}.",
            stated, expected_sd, round(diff, 2)))
    else:
        findings.append(_ok("Deductions", "Standard deduction (Line 12)",
                            f"Correct for {status} in {ctx.year}", stated, expected_sd))

    # Taxable income (15) = AGI (11) - deduction (12) - QBI deduction (13),
    # floored at zero. Line 13 is zero when the draft claims no QBI, so this
    # is backward-compatible; a draft that claims QBI must reflect it here.
    expected_ti = max(0.0, _f(draft, "line_11_agi") - stated - _f(draft, "line_13_qbi_deduction"))
    stated_ti = _f(draft, "line_15_taxable_income")
    match, diff = compare_values(stated_ti, expected_ti, ctx.exact_tolerance)
    if not match:
        findings.append(Finding(
            Severity.ERROR, "Deductions", "Taxable income (Line 15)",
            "Taxable income does not equal AGI minus the deduction minus QBI.",
            stated_ti, round(expected_ti, 2), round(diff, 2)))
    else:
        findings.append(_ok("Deductions", "Taxable income (Line 15)",
                            "AGI minus deduction", stated_ti, round(expected_ti, 2)))
    return findings


def check_tax(draft: Dict, computed: Dict, ctx: ReviewContext) -> List[Finding]:
    """Recompute line 16 through the full preferential-rate stack.

    This is the check SKILL.md Step 5 always promised. The reviewer used to
    verify only wages and the standard deduction (finding D3).
    """
    status = _status(draft)
    taxable_income = _f(draft, "line_15_taxable_income")

    qualified_dividends = _f(draft, "line_3a_qualified_dividends")
    long_term_gain = max(0.0, float(draft.get("schedule_d", {}).get("long_term_gain_loss", 0) or 0))
    preferential = min(qualified_dividends + long_term_gain, taxable_income)
    ordinary = taxable_income - preferential

    ordinary_tax, _ = calculate_tax(ordinary, status, ctx.tax_brackets)
    preferential_tax, _ = calculate_preferential_tax(
        ordinary, preferential,
        select_for_status(ctx.ltcg_brackets, status, "long-term capital gain brackets"))
    expected = round(ordinary_tax + preferential_tax, 2)

    stated = _f(draft, "line_16_tax")
    match, diff = compare_values(stated, expected, ctx.exact_tolerance)

    detail = (f"ordinary ${ordinary:,.2f} -> ${ordinary_tax:,.2f}; "
              f"preferential ${preferential:,.2f} -> ${preferential_tax:,.2f}")
    if not match:
        return [Finding(Severity.ERROR, "Tax", "Tax (1040 Line 16)",
                        f"Recomputed tax differs from the draft ({detail}).",
                        stated, expected, round(diff, 2))]
    return [_ok("Tax", "Tax (Line 16)", f"Matches recomputation ({detail})", stated, expected)]


def check_se_tax(draft: Dict, computed: Dict, ctx: ReviewContext) -> List[Finding]:
    """Recompute Schedule SE and its deductible half."""
    findings = []
    se = draft.get("schedule_se") or {}
    net_profit = float(se.get("net_profit", draft.get("schedule_1", {}).get("line_3_business_income", 0)) or 0)
    if net_profit <= 0:
        return findings

    base = net_profit * ctx.se_tax_factor
    wages = _f(draft, "line_1a_wages")
    remaining_ss_base = max(0.0, ctx.ss_wage_base - wages)
    ss_tax = min(base, remaining_ss_base) * ctx.se_ss_rate
    medicare_tax = base * ctx.se_medicare_rate
    expected = round(ss_tax + medicare_tax, 2)

    stated = float(se.get("total_se_tax", draft.get("schedule_2", {}).get("line_4_self_employment_tax", 0)) or 0)
    match, diff = compare_values(stated, expected, ctx.exact_tolerance)
    if not match:
        findings.append(Finding(
            Severity.ERROR, "SE Tax", "Self-employment tax (Schedule 2 Line 4)",
            f"Recomputed SE tax differs (base ${base:,.2f}, SS ${ss_tax:,.2f}, "
            f"Medicare ${medicare_tax:,.2f}). Note W-2 wages consume the Social "
            "Security wage base first.",
            stated, expected, round(diff, 2)))
    else:
        findings.append(_ok("SE Tax", "Self-employment tax", "Matches recomputation",
                            stated, expected))

    # Half of SE tax is deductible on Schedule 1.
    expected_ded = round(expected / 2, 2)
    stated_ded = float(draft.get("schedule_1", {}).get("line_15_deductible_se_tax", 0) or 0)
    if stated_ded:
        match, diff = compare_values(stated_ded, expected_ded, ctx.exact_tolerance)
        if not match:
            findings.append(Finding(
                Severity.ERROR, "SE Tax", "Deductible half of SE tax (Schedule 1 Line 15)",
                "Deductible portion should be exactly half of the SE tax.",
                stated_ded, expected_ded, round(diff, 2)))
        else:
            findings.append(_ok("SE Tax", "Deductible half of SE tax",
                                "Half of SE tax", stated_ded, expected_ded))
    return findings


def check_total_tax(draft: Dict, computed: Dict, ctx: ReviewContext) -> List[Finding]:
    """Total tax (line 24) = (tax (16) minus Child Tax Credit (19), floored at
    zero per the line 22 instructions) plus other taxes (23)."""
    tax_after_credits = max(0.0, _f(draft, "line_16_tax") - _f(draft, "line_19_child_tax_credit"))
    expected = tax_after_credits + _f(draft, "line_23_other_taxes")
    stated = _f(draft, "line_24_total_tax")
    match, diff = compare_values(stated, expected, ctx.exact_tolerance)
    if not match:
        return [Finding(Severity.ERROR, "Tax", "Total tax (1040 Line 24)",
                        "Total tax does not equal line 16 minus Child Tax Credit "
                        "plus other taxes.",
                        stated, round(expected, 2), round(diff, 2))]
    return [_ok("Tax", "Total tax (Line 24)", "Line 16 minus CTC plus other taxes",
                stated, round(expected, 2))]


def check_capital_gains(draft: Dict, computed: Dict, ctx: ReviewContext) -> List[Finding]:
    """Schedule D net loss is limited to $3,000 ($1,500 MFS) per year."""
    findings = []
    sched_d = draft.get("schedule_d")
    if not sched_d:
        return findings

    status = _status(draft)
    limit = ctx.capital_loss_limit_mfs if status == "married_separately" else ctx.capital_loss_limit
    total = float(sched_d.get("total_gain_loss", 0) or 0)
    reported = _f(draft, "line_7_capital_gain_loss")

    # Expected line 7 = the Schedule D total, capped at -limit when it's a
    # loss exceeding the annual limit (the excess carries forward instead).
    over_limit = total < -limit
    expected = -limit if over_limit else total

    if abs(reported - expected) > ctx.exact_tolerance:
        label = "Capital loss (1040 Line 7)" if total < 0 else "Capital gain (1040 Line 7)"
        message = (f"Net capital loss deduction is limited to ${limit:,.0f} per year; "
                   "the excess carries forward." if over_limit else
                   "Line 7 does not match the Schedule D total.")
        findings.append(Finding(
            Severity.ERROR, "Capital Gains", label, message,
            reported, expected, round(reported - expected, 2)))
    else:
        findings.append(_ok("Capital Gains", "Schedule D", "Consistent with Line 7",
                            reported, expected))
    return findings


def check_withholding(draft: Dict, computed: Dict, ctx: ReviewContext) -> List[Finding]:
    """Total withholding (25d) must tie to the W-2 Box 2 amounts."""
    findings = []
    w2s = draft.get("w2_records") or []
    if w2s:
        expected = sum(float(w.get("box_2_federal_withheld", 0)) for w in w2s)
        stated = _f(draft, "line_25a_w2_withholding")
        match, diff = compare_values(stated, expected, ctx.exact_tolerance)
        if not match:
            findings.append(Finding(
                Severity.ERROR, "Payments", "W-2 withholding (Line 25a)",
                "Withholding does not match the sum of W-2 Box 2 amounts.",
                stated, round(expected, 2), round(diff, 2)))
        else:
            findings.append(_ok("Payments", "W-2 withholding (Line 25a)",
                                "Matches W-2 Box 2 total", stated, round(expected, 2)))

    # Total payments (33) = withholding (25d) + estimated payments (26)
    expected_payments = _f(draft, "line_25d_total_withholding") + _f(draft, "line_26_estimated_payments")
    stated_payments = _f(draft, "line_33_total_payments")
    if stated_payments or expected_payments:
        match, diff = compare_values(stated_payments, expected_payments, ctx.exact_tolerance)
        if not match:
            findings.append(Finding(
                Severity.ERROR, "Payments", "Total payments (Line 33)",
                "Total payments do not equal withholding plus estimated payments.",
                stated_payments, round(expected_payments, 2), round(diff, 2)))
        else:
            findings.append(_ok("Payments", "Total payments (Line 33)",
                                "Withholding plus estimated payments",
                                stated_payments, round(expected_payments, 2)))
    return findings


def check_salt(draft: Dict, computed: Dict, ctx: ReviewContext) -> List[Finding]:
    """Schedule A SALT against the YEAR-CORRECT cap (findings F1/F2).

    The old checklist rule flagged any SALT above $10,000 as an error, which
    for 2025 condemns a correct return.
    """
    findings = []
    sched_a = draft.get("schedule_a")
    if not sched_a:
        return findings

    status = _status(draft)
    magi = _f(draft, "line_11_agi")
    cap = resolve_salt_cap(ctx.salt_constants, status, magi)
    stated = float(sched_a.get("line_6_salt_deduction", 0) or 0)

    if stated > cap + ctx.exact_tolerance:
        findings.append(Finding(
            Severity.ERROR, "Deductions", "SALT (Schedule A Line 6)",
            f"SALT deduction exceeds the {ctx.year} cap of ${cap:,.0f} for {status} "
            f"at MAGI ${magi:,.0f}.",
            stated, cap, round(stated - cap, 2)))
    else:
        findings.append(_ok("Deductions", "SALT (Schedule A Line 6)",
                            f"Within the {ctx.year} cap of ${cap:,.0f}", stated, cap))
    return findings


def check_credits(draft: Dict, computed: Dict, ctx: ReviewContext) -> List[Finding]:
    """Child Tax Credit recomputation (per task P4)."""
    findings = []
    children = int(draft.get("taxpayer_info", {}).get("children_under_17", 0) or 0)
    if not children:
        return findings

    result = calculate_child_tax_credit({
        "children_under_17": children,
        "agi": _f(draft, "line_11_agi"),
        "filing_status": _status(draft),
        "earned_income": _f(draft, "line_1a_wages"),
    }, ctx.ctc_params)

    stated = _f(draft, "line_19_child_tax_credit")
    match, diff = compare_values(stated, result.amount, ctx.exact_tolerance)
    if not match:
        findings.append(Finding(
            Severity.ERROR, "Credits", "Child Tax Credit (Line 19)",
            f"Recomputed CTC for {children} qualifying child(ren) differs.",
            stated, result.amount, round(diff, 2)))
    else:
        findings.append(_ok("Credits", "Child Tax Credit (Line 19)",
                            "Matches recomputation", stated, result.amount))
    return findings


def check_qbi(draft: Dict, computed: Dict, ctx: ReviewContext) -> List[Finding]:
    """Recompute the §199A QBI deduction and compare to 1040 line 13 / Form
    8995 line 15; flag a draft that claims QBI in the CA state column.

    The check only runs when the draft carries a ``qbi`` block (the QBI inputs:
    SE profit, wages/UBIA, SSTB flag, REIT dividends, etc.). A draft with no
    QBI block is left untouched — claiming zero QBI is not an error, only a
    missed deduction (surfaced by the savings finder, not the reviewer).

    Taxable income BEFORE the QBI deduction = line 15 + line 13, since line 15
    is computed after subtracting the QBI deduction from AGI-less-deductions.
    net_capital_gain defaults to qualified dividends + net LTCG (§1(h)).
    """
    qbi_block = draft.get("qbi")
    if not qbi_block:
        return []

    import qbi_calculator  # lazy: qbi_calculator -> estimated_tax_calculator -> tax_calculator
    status = _status(draft)
    stated_line13 = _f(draft, "line_13_qbi_deduction")
    # taxable income before QBI = line 15 (after QBI) + line 13 (the QBI itself).
    # The qbi block may carry an explicit taxable_income_before_qbi (the
    # "computed inputs"); use it when present so a draft with an overstated
    # line 13 still recomputes against the real inputs.
    derived_ti = _f(draft, "line_15_taxable_income") + stated_line13
    ti_before = float(qbi_block.get("taxable_income_before_qbi", derived_ti))

    qd = _f(draft, "line_3a_qualified_dividends")
    ltcg = max(0.0, float(draft.get("schedule_d", {}).get("long_term_gain_loss", 0) or 0))
    net_capital_gain = qbi_block.get("net_capital_gain", qd + ltcg)

    se_profit = qbi_block.get(
        "se_net_profit",
        float(draft.get("schedule_se", {}).get("net_profit", 0) or 0))

    profile = {
        "se_net_profit": se_profit,
        "se_health_insurance": qbi_block.get("se_health_insurance", 0),
        "se_retirement": qbi_block.get("se_retirement", 0),
        "w2_wages_paid": qbi_block.get("w2_wages_paid", 0),
        "ubia": qbi_block.get("ubia", 0),
        "is_sstb": qbi_block.get("is_sstb", False),
        "taxable_income_before_qbi": ti_before,
        "net_capital_gain": net_capital_gain,
        "reit_dividends": qbi_block.get("reit_dividends", 0),
        "qbi_loss_carryforward": qbi_block.get("qbi_loss_carryforward", 0),
        "filing_status": status,
    }

    try:
        result = qbi_calculator.compute_qbi_deduction(profile, ctx.year_constants)
    except (UnverifiedConstantError, ValueError) as e:
        return [Finding(Severity.WARNING, "QBI", "QBI deduction (1040 Line 13)",
                        f"Could not recompute the QBI deduction: {e}")]

    expected = result.deduction
    match, diff = compare_values(stated_line13, expected, ctx.exact_tolerance)
    if not match:
        findings = [Finding(
            Severity.ERROR, "QBI", "QBI deduction (1040 Line 13)",
            f"Draft QBI deduction differs from recomputation (Form {result.form}; "
            f"engine ${expected:,.2f}).",
            stated_line13, round(expected, 2), round(diff, 2))]
    else:
        findings = [_ok("QBI", "QBI deduction (Line 13)",
                        f"Matches recomputation (Form {result.form})",
                        stated_line13, round(expected, 2))]

    # Form 8995 / 8995-A line 15 (the form's own total) if the draft reports it.
    f8995 = draft.get("form_8995") or {}
    if f8995.get("line_15") is not None:
        stated15 = float(f8995["line_15"])
        m15, d15 = compare_values(stated15, expected, ctx.exact_tolerance)
        if not m15:
            findings.append(Finding(
                Severity.ERROR, "QBI", "Form 8995 Line 15",
                "Form 8995/8995-A line 15 differs from recomputation.",
                stated15, round(expected, 2), round(d15, 2)))

    # California has no IRC 199A deduction: QBI must be ADDED BACK on Schedule CA.
    # A draft that subtracts QBI in the CA state column (negative adjustment) is
    # wrong; a positive add-back or no entry is correct.
    ca = draft.get("schedule_ca") or {}
    ca_qbi = ca.get("qbi_adjustment")
    if ca_qbi is not None and float(ca_qbi) < 0:
        findings.append(Finding(
            Severity.ERROR, "QBI", "CA state-column QBI (Schedule CA)",
            "California has no IRC 199A deduction — the QBI deduction must be "
            "ADDED BACK on Schedule CA (540) (a positive adjustment), not "
            "subtracted in the state column."))
    return findings


def check_excess_sdi(draft: Dict, computed: Dict, ctx: ReviewContext,
                     sdi_config: Optional[Dict] = None) -> List[Finding]:
    """C1: verify the Form 540 line 74 excess-SDI credit for CA multi-W-2 filers.

    The credit exists only for a capped SDI year with two or more employers
    (SB 951 removed the cap starting 2024, so 2024+ cannot have an excess). See
    `utils.excess_sdi_credit` for the cap-driven computation.

    `sdi_config` is injectable for tests; by default it is resolved from the
    state constants for ctx.year. Years where CA data is absent produce no
    findings (the reviewer must not crash on a federal-only review).
    """
    if str(computed.get("state", "")).upper() != "CA":
        return []
    sdi_by_employer = computed.get("sdi_by_employer") or []
    if len([x for x in sdi_by_employer if float(x) > 0]) < 2:
        return []

    if sdi_config is None:
        try:
            sdi_config = load_state_constants("CA", ctx.year)["sdi"]
        except ValueError:
            return []  # CA data not available for this year — nothing to check

    result = excess_sdi_credit(sdi_by_employer, sdi_config)
    draft_credit = float(draft.get("form_540", {}).get("line_74_excess_sdi", 0) or 0)

    if not result["possible"]:
        if sdi_config.get("wage_cap") is None:
            return [Finding(Severity.INFO, "california", "line_74_excess_sdi",
                            result["note"])]
        return []
    ok, diff = compare_values(draft_credit, result["credit"], ctx.exact_tolerance)
    if ok:
        return [_ok("california", "line_74_excess_sdi",
                    f"Excess-SDI credit ${result['credit']:,.2f} present on line 74.",
                    draft_credit, result["credit"])]
    return [Finding(Severity.ERROR, "california", "line_74_excess_sdi",
                    (f"Draft misses ${result['credit'] - draft_credit:,.2f} of "
                     f"excess-SDI credit on Form 540 line 74 "
                     f"({len(sdi_by_employer)} employers withheld "
                     f"${result['total_withheld']:,.2f} vs the "
                     f"${result['max_required']:,.2f} per-year maximum)."),
                    draft_credit, result["credit"], diff)]


def check_529(draft: Dict, computed: Dict, ctx: ReviewContext,
              plan_529: Optional[Dict] = None) -> List[Finding]:
    """C4: verify a federally-qualified-but-CA-non-qualified 529 withdrawal is
    reflected on the CA draft.

    Fires only for a CA return when ``computed["plan_529_withdrawal_ca_nonqualified"]``
    is present — a dict ``{"withdrawal": x, "earnings": y}`` describing a
    federally-qualified withdrawal that is NON-qualified for California (K-12
    tuition, 529-to-Roth rollover — see the constants list). The earnings
    portion must be added back to CA income on Schedule CA (540) line 8z AND
    carries a 2.5% CA additional tax (Form FTB 3805P). A draft that omits the
    add-back is an ERROR; a draft that reports it is OK.

    ``plan_529`` is injectable for tests; by default it is resolved from the
    year's nonconformity block. Years without a ``plan_529`` sub-block (2023/
    2024 — 529 coverage is 2025-only per plan C4) produce no findings, so the
    reviewer never crashes on a year that does not yet carry 529 data.
    """
    if str(computed.get("state", "")).upper() != "CA":
        return []
    w = computed.get("plan_529_withdrawal_ca_nonqualified")
    if not w:
        return []

    if plan_529 is None:
        try:
            plan_529 = load_state_constants("CA", ctx.year)["nonconformity"].get(
                "plan_529")
        except ValueError:
            return []  # CA data not available for this year — nothing to check
        if plan_529 is None:
            return []  # year has no 529 sub-block (2023/2024) — out of scope

    withdrawal = float(w.get("withdrawal", 0))
    earnings = float(w.get("earnings", 0))
    result = ca_529_nonqualified_withdrawal(withdrawal, earnings, plan_529)
    expected_addback = result["ca_taxable_earnings"]
    stated_addback = float(
        draft.get("form_540", {}).get("line_529_earnings_addback", 0) or 0)

    ok, diff = compare_values(stated_addback, expected_addback,
                              ctx.exact_tolerance)
    if ok:
        return [_ok("california", "line_529_earnings_addback",
                    (f"529 non-qualified earnings ${expected_addback:,.2f} "
                     f"added back to CA income (Schedule CA line 8z); "
                     f"${result['additional_tax']:,.2f} CA additional tax "
                     f"(Form 3805P)."),
                    stated_addback, expected_addback)]
    return [Finding(
        Severity.ERROR, "california", "line_529_earnings_addback",
        (f"Draft omits the CA add-back for a federally-qualified-but-CA-non-"
         f"qualified 529 withdrawal: ${expected_addback:,.2f} of earnings must "
         f"be added to CA income on Schedule CA (540) line 8z, plus a "
         f"${result['additional_tax']:,.2f} CA additional tax on Form 3805P "
         f"(2.5% x earnings). The basis portion is tax-free."),
        stated_addback, expected_addback, diff)]


def run_review(draft: Dict, computed: Dict, ctx: ReviewContext) -> List[Finding]:
    all_findings = []
    for check in (check_income, check_agi, check_deductions, check_tax,
                  check_se_tax, check_total_tax, check_capital_gains,
                  check_withholding, check_salt, check_credits, check_qbi,
                  check_excess_sdi, check_529):
        all_findings.extend(check(draft, computed, ctx))
    return all_findings


def main():
    parser = argparse.ArgumentParser(description="Draft Tax Return Reviewer")
    parser.add_argument("--draft", required=True, help="Path to draft return JSON")
    parser.add_argument("--computed", required=False, default=None,
                        help="Optional computed tax data JSON for cross-reference")
    make_year_arg(parser)
    parser.add_argument("--output-format", choices=["text", "json"], default="text")

    args = parser.parse_args()

    # Load Tax Data for context
    try:
        tax_data = load_tax_constants(args.year)
    except Exception as e:
        print(f"Error loading tax constants: {e}")
        sys.exit(1)

    ctx = ReviewContext(
        year=args.year,
        tax_brackets=tax_data["tax_brackets_processed"],
        ltcg_brackets=tax_data["ltcg_brackets_processed"],
        standard_deductions=tax_data["standard_deductions"],
        additional_deductions=tax_data["additional_deduction_65_blind"],
        ctc_params=get_credit_params(tax_data)["child_tax_credit"],
        salt_constants=tax_data["salt"],
        capital_loss_limit=tax_data["capital_loss_limit"],
        capital_loss_limit_mfs=tax_data["capital_loss_limit_mfs"],
        ss_wage_base=tax_data["se_tax"]["ss_wage_base"],
        se_tax_factor=tax_data["se_tax"]["se_adjustment_factor"],
        se_ss_rate=tax_data["se_tax"]["ss_rate"],
        se_medicare_rate=tax_data["se_tax"]["medicare_rate"],
        add_medicare_threshold_single=tax_data["se_tax"]["additional_medicare_threshold_single"],
        add_medicare_threshold_mfj=tax_data["se_tax"]["additional_medicare_threshold_married"],
        hsa_limit_self=tax_data["retirement_limits"]["hsa"]["single"],
        hsa_limit_family=tax_data["retirement_limits"]["hsa"]["family"],
        charitable_cash_limit=tax_data["credits"]["charitable_cash_limit"],
        charitable_property_limit=tax_data["charitable_property_agi_limit"],
        educator_limit=tax_data["educator_expense_limit"],
        student_loan_limit=tax_data["student_loan_interest_limit"],
        year_constants=tax_data,
    )

    # Load Input Files
    try:
        with open(args.draft, "r") as f:
            draft_data = json.load(f)
        computed_data = {}
        if args.computed:
            with open(args.computed, "r") as f:
                computed_data = json.load(f)
    except Exception as e:
        print(f"Error loading input files: {e}")
        sys.exit(1)

    try:
        findings = run_review(draft_data, computed_data, ctx)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    if args.output_format == "json":
        from dataclasses import asdict

        def to_dict(finding):
            # asdict leaves Severity as an Enum member; `default=str` would
            # render it "Severity.ERROR" rather than "ERROR", which is the same
            # class of defect as finding D1. Flatten it explicitly.
            payload = asdict(finding)
            payload["severity"] = finding.severity.value
            return payload

        print(json.dumps([to_dict(f) for f in findings], indent=2, default=str))
    else:
        print(f"\n--- {args.year} Draft Return Review Summary ---")
        errors = [f for f in findings if f.severity == Severity.ERROR]
        warnings = [f for f in findings if f.severity == Severity.WARNING]
        
        print(f"Status: {len(errors)} Errors, {len(warnings)} Warnings\n")
        
        for f in findings:
            print(f"{f.severity.icon} [{f.category}] {f.field}: {f.message}")
            if f.draft_value is not None:
                print(f"    Draft:    {f.draft_value}")
                print(f"    Computed: {f.computed_value}")
                if f.difference:
                    print(f"    Diff:     {f.difference}")
            print()


if __name__ == "__main__":
    main()
