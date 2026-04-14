#!/usr/bin/env python3
"""
Estimated Tax Calculator - Calculate quarterly estimated tax payments.
Determines safe harbor amounts and generates payment schedule.
"""

import argparse
import json
import os
import sys
from datetime import date
from typing import Dict, List

# Add the scripts directory to the path to import utils
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import load_tax_constants


def get_due_dates(year: str) -> List:
    """Get quarterly due dates for a specific tax year."""
    if year == "2024":
        return [
            ("Q1", "2024-04-15", "Jan 1 - Mar 31"),
            ("Q2", "2024-06-17", "Apr 1 - May 31"),
            ("Q3", "2024-09-16", "Jun 1 - Aug 31"),
            ("Q4", "2025-01-15", "Sep 1 - Dec 31")
        ]
    else: # 2025 default
        return [
            ("Q1", "2025-04-15", "Jan 1 - Mar 31"),
            ("Q2", "2025-06-16", "Apr 1 - May 31"),
            ("Q3", "2025-09-15", "Jun 1 - Aug 31"),
            ("Q4", "2026-01-15", "Sep 1 - Dec 31")
        ]


def calculate_income_tax(taxable_income: float, filing_status: str, tax_brackets: Dict) -> float:
    """Calculate federal income tax."""
    brackets = tax_brackets.get(filing_status, tax_brackets["single"])

    tax = 0
    prev_bracket = 0

    for bracket_top, rate in brackets:
        if taxable_income <= prev_bracket:
            break
        bracket_income = min(taxable_income - prev_bracket, bracket_top - prev_bracket)
        tax += bracket_income * rate
        prev_bracket = bracket_top

    return round(tax, 2)


def calculate_se_tax(net_se_income: float, se_tax_data: Dict) -> Dict:
    """Calculate self-employment tax."""
    if net_se_income <= 0:
        return {"total": 0, "social_security": 0, "medicare": 0, "deduction": 0}

    # SE tax base is usually 92.35% of net SE income
    factor = se_tax_data.get("se_adjustment_factor", 0.9235)
    se_tax_base = net_se_income * factor

    # Social Security portion (capped at wage base)
    ss_taxable = min(se_tax_base, se_tax_data["ss_wage_base"])
    ss_tax = ss_taxable * se_tax_data["ss_rate"]

    # Medicare portion (no cap for basic rate)
    medicare_tax = se_tax_base * se_tax_data["medicare_rate"]

    total_se_tax = ss_tax + medicare_tax

    # Deductible portion (half of SE tax)
    se_deduction = total_se_tax / 2

    return {
        "total": round(total_se_tax, 2),
        "social_security": round(ss_tax, 2),
        "medicare": round(medicare_tax, 2),
        "deduction": round(se_deduction, 2)
    }


def calculate_safe_harbor(prior_year_tax: float, prior_year_agi: float) -> Dict:
    """Calculate safe harbor amounts to avoid underpayment penalty."""
    # Standard safe harbor: 100% of prior year tax
    standard_safe_harbor = prior_year_tax

    # High income safe harbor: 110% if AGI > $150k
    high_income_threshold = 150000
    if prior_year_agi > high_income_threshold:
        high_income_safe_harbor = prior_year_tax * 1.10
    else:
        high_income_safe_harbor = prior_year_tax

    return {
        "standard": round(standard_safe_harbor, 2),
        "high_income": round(high_income_safe_harbor, 2),
        "applies_high_income": prior_year_agi > high_income_threshold,
        "minimum_required": round(high_income_safe_harbor if prior_year_agi > high_income_threshold else standard_safe_harbor, 2)
    }


def main():
    parser = argparse.ArgumentParser(description="Estimated Tax Calculator")
    parser.add_argument("--projected-income", type=float, required=True, help="Total projected gross income for the year")
    parser.add_argument("--projected-se-income", type=float, default=0, help="Projected net self-employment income")
    parser.add_argument("--prior-year-tax", type=float, required=True, help="Total tax from prior year return")
    parser.add_argument("--prior-year-agi", type=float, default=0, help="AGI from prior year return")
    parser.add_argument("--filing-status", default="single", help="Filing status")
    parser.add_argument("--withholding-to-date", type=float, default=0, help="Federal withholding to date")
    parser.add_argument("--year", type=str, default="2025", choices=["2024", "2025"], help="Tax year for calculation")
    parser.add_argument("--output-format", choices=["json", "text"], default="text")

    args = parser.parse_args()

    # Load Tax Data for specified year
    try:
        tax_data = load_tax_constants(args.year)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    tax_brackets = tax_data["tax_brackets_processed"]
    standard_deductions = tax_data["standard_deductions"]
    se_tax_config = tax_data["se_tax"]

    # Calculate SE Tax
    se_results = calculate_se_tax(args.projected_se_income, se_tax_config)

    # Calculate AGI
    # Gross income includes SE income - adjustments (half of SE tax)
    agi = args.projected_income - se_results["deduction"]

    # Calculate Taxable Income
    std_deduction = standard_deductions.get(args.filing_status, standard_deductions["single"])
    taxable_income = max(0, agi - std_deduction)

    # Calculate Federal Income Tax
    income_tax = calculate_income_tax(taxable_income, args.filing_status, tax_brackets)

    # Total estimated tax liability
    total_estimated_tax = income_tax + se_results["total"]

    # Safe Harbor analysis
    safe_harbor = calculate_safe_harbor(args.prior_year_tax, args.prior_year_agi)

    # Calculate remaining amount to pay
    total_target = min(total_estimated_tax * 0.90, safe_harbor["minimum_required"])
    remaining_to_pay = max(0, total_target - args.withholding_to_date)
    quarterly_payment = remaining_to_pay / 4

    result = {
        "year": args.year,
        "projections": {
            "total_income": args.projected_income,
            "se_income": args.projected_se_income,
            "agi": round(agi, 2),
            "taxable_income": round(taxable_income, 2)
        },
        "estimated_liability": {
            "income_tax": income_tax,
            "self_employment_tax": se_results["total"],
            "total": round(total_estimated_tax, 2)
        },
        "safe_harbor": safe_harbor,
        "payment_plan": {
            "total_target_payment": round(total_target, 2),
            "withholding_to_date": args.withholding_to_date,
            "remaining_estimated_due": round(remaining_to_pay, 2),
            "quarterly_amount": round(quarterly_payment, 2),
            "due_dates": get_due_dates(args.year)
        }
    }

    if args.output_format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"\n--- {args.year} Estimated Tax Calculation ---")
        print(f"Projected Total Liability:  ${total_estimated_tax:,.2f}")
        print(f"  (Income Tax: ${income_tax:,.2f}, SE Tax: ${se_results['total']:,.2f})")
        print("-" * 45)
        print(f"Safe Harbor Requirement:    ${safe_harbor['minimum_required']:,.2f}")
        print(f"Target Total Payment:       ${total_target:,.2f} (90% of current or safe harbor)")
        print(f"Federal Withholding:        ${args.withholding_to_date:,.2f}")
        print("-" * 45)
        print(f"REMAINING TO PAY:           ${remaining_to_pay:,.2f}")
        print(f"QUARTERLY INSTALLMENT:      ${quarterly_payment:,.2f}")
        print("\nPayment Schedule:")
        for q, date_str, period in result["payment_plan"]["due_dates"]:
            print(f"  {q}: Due {date_str} (Period: {period})")


if __name__ == "__main__":
    main()
