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
from utils import load_tax_constants


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
    standard_deductions: Dict
    add_deduction_married: float
    add_deduction_single: float
    salt_cap: float
    salt_cap_mfs: float
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
    exact_tolerance: float = 1.0
    estimate_tolerance: float = 0.05


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def compare_values(draft: float, computed: float, tolerance: float = 1.0) -> Tuple[bool, float]:
    diff = draft - computed
    return abs(diff) <= tolerance, diff


def calculate_tax(taxable_income: float, filing_status: str, brackets: List[Tuple[float, float]]) -> float:
    tax = 0
    prev_bracket = 0
    for bracket_top, rate in brackets:
        if taxable_income <= prev_bracket:
            break
        bracket_income = min(taxable_income - prev_bracket, bracket_top - prev_bracket)
        tax += bracket_income * rate
        prev_bracket = bracket_top
    return round(tax, 2)


# ---------------------------------------------------------------------------
# Core Review Logic
# ---------------------------------------------------------------------------

def check_income(draft: Dict, computed: Dict, ctx: ReviewContext) -> List[Finding]:
    findings = []
    
    # Box 1 wages vs W-2 records
    draft_wages = draft.get("income", {}).get("wages", 0)
    computed_wages = computed.get("income", {}).get("wages", 0)
    match, diff = compare_values(draft_wages, computed_wages, ctx.exact_tolerance)
    
    if not match:
        findings.append(Finding(
            Severity.ERROR, "Income", "Wages (1040 Line 1z)",
            f"Wages on draft return do not match W-2 records.",
            draft_wages, computed_wages, diff
        ))
    else:
        findings.append(Finding(Severity.OK, "Income", "Wages", "Matches W-2 records", draft_wages, computed_wages))

    return findings


def check_deductions(draft: Dict, computed: Dict, ctx: ReviewContext) -> List[Finding]:
    findings = []
    status = draft.get("filing_status", "single")
    
    # Standard vs Itemized
    draft_deduction = draft.get("deductions", {}).get("total", 0)
    
    # Calculate expected standard deduction
    base_sd = ctx.standard_deductions.get(status, ctx.standard_deductions["single"])
    age_blind_amt = ctx.add_deduction_married if "married" in status else ctx.add_deduction_single
    
    num_age_blind = draft.get("taxpayer_info", {}).get("age_65_plus", 0) + \
                    draft.get("taxpayer_info", {}).get("blind", 0)
    
    expected_sd = base_sd + (age_blind_amt * num_age_blind)
    
    # If not itemizing, draft should match expected SD
    if not draft.get("deductions", {}).get("itemizing", False):
        match, diff = compare_values(draft_deduction, expected_sd, ctx.exact_tolerance)
        if not match:
            findings.append(Finding(
                Severity.ERROR, "Deductions", "Standard Deduction",
                f"Standard deduction amount is incorrect for {status} status.",
                draft_deduction, expected_sd, diff
            ))
    
    return findings


def run_review(draft: Dict, computed: Dict, ctx: ReviewContext) -> List[Finding]:
    all_findings = []
    all_findings.extend(check_income(draft, computed, ctx))
    all_findings.extend(check_deductions(draft, computed, ctx))
    # In a full implementation, we would call check_tax, check_credits, etc.
    return all_findings


def main():
    parser = argparse.ArgumentParser(description="Draft Tax Return Reviewer")
    parser.add_argument("--draft", required=True, help="Path to draft return JSON")
    parser.add_argument("--computed", required=True, help="Path to computed tax data JSON")
    parser.add_argument("--year", type=str, default="2025", choices=["2024", "2025"], help="Tax year")
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
        standard_deductions=tax_data["standard_deductions"],
        add_deduction_married=tax_data["additional_deduction_65_blind"]["married_jointly"],
        add_deduction_single=tax_data["additional_deduction_65_blind"]["single"],
        salt_cap=tax_data["salt_cap"],
        salt_cap_mfs=tax_data["salt_cap_mfs"],
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
        student_loan_limit=tax_data["student_loan_interest_limit"]
    )

    # Load Input Files
    try:
        with open(args.draft, "r") as f:
            draft_data = json.load(f)
        with open(args.computed, "r") as f:
            computed_data = json.load(f)
    except Exception as e:
        print(f"Error loading input files: {e}")
        sys.exit(1)

    findings = run_review(draft_data, computed_data, ctx)

    if args.output_format == "json":
        from dataclasses import asdict
        print(json.dumps([asdict(f) for f in findings], indent=2, default=str))
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
