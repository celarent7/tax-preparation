#!/usr/bin/env python3
"""
Tax Calculator - Calculate federal income tax based on filing status and income.
Supports 2024 and 2025 tax year brackets and standard deductions.
"""

import argparse
import json
import os
import sys
from typing import Dict, Tuple, List

# Add the scripts directory to the path to import utils
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import load_tax_constants


def calculate_tax(taxable_income: float, filing_status: str, tax_brackets: Dict) -> Tuple[float, List[Dict]]:
    """Calculate federal income tax and return breakdown by bracket."""
    brackets = tax_brackets.get(filing_status, tax_brackets["single"])

    tax = 0
    breakdown = []
    prev_bracket = 0
    remaining_income = taxable_income

    for bracket_top, rate in brackets:
        if remaining_income <= 0:
            break

        bracket_income = min(remaining_income, bracket_top - prev_bracket)
        bracket_tax = bracket_income * rate
        tax += bracket_tax

        if bracket_income > 0:
            breakdown.append({
                "bracket_floor": prev_bracket,
                "bracket_ceiling": bracket_top if bracket_top != float('inf') else "unlimited",
                "rate": rate,
                "income_in_bracket": round(bracket_income, 2),
                "tax_from_bracket": round(bracket_tax, 2)
            })

        remaining_income -= bracket_income
        prev_bracket = bracket_top

    return round(tax, 2), breakdown


def calculate_effective_rate(tax: float, gross_income: float) -> float:
    """Calculate effective tax rate."""
    if gross_income <= 0:
        return 0
    return round((tax / gross_income) * 100, 2)


def calculate_marginal_rate(taxable_income: float, filing_status: str, tax_brackets: Dict) -> float:
    """Determine marginal tax rate based on taxable income."""
    brackets = tax_brackets.get(filing_status, tax_brackets["single"])

    for bracket_top, rate in brackets:
        if taxable_income <= bracket_top:
            return rate

    return brackets[-1][1]


def calculate_standard_deduction(filing_status: str, standard_deductions: Dict, additional_deductions: Dict, 
                                 age_65_plus: int = 0, blind: int = 0) -> float:
    """Calculate standard deduction including additional amounts for age/blindness."""
    base = standard_deductions.get(filing_status, standard_deductions["single"])
    additional = additional_deductions.get(filing_status, 1950)

    return base + (additional * age_65_plus) + (additional * blind)


def main():
    parser = argparse.ArgumentParser(description="Calculate federal income tax")
    parser.add_argument("--gross-income", type=float, required=True, help="Gross income")
    parser.add_argument("--year", type=str, default="2025", choices=["2024", "2025"], help="Tax year")
    parser.add_argument("--filing-status", default="single", help="Filing status")
    parser.add_argument("--deductions", type=float, default=0,
                        help="Itemized deductions (if greater than standard, will use itemized)")
    parser.add_argument("--adjustments", type=float, default=0,
                        help="Above-the-line adjustments (IRA, student loan interest, etc.)")
    parser.add_argument("--age-65-plus", type=int, default=0,
                        help="Number of taxpayers 65+ (0, 1, or 2)")
    parser.add_argument("--blind", type=int, default=0,
                        help="Number of blind taxpayers (0, 1, or 2)")
    parser.add_argument("--credits", type=float, default=0,
                        help="Total tax credits")
    parser.add_argument("--output-format", choices=["json", "text"], default="text",
                        help="Output format")

    args = parser.parse_args()

    # Input Validation
    if args.gross_income < 0:
        print("Error: Gross income cannot be negative.")
        sys.exit(1)
    if any(val < 0 for val in [args.deductions, args.adjustments, args.credits]):
        print("Error: Deductions, adjustments, and credits must be non-negative.")
        sys.exit(1)
    if not (0 <= args.age_65_plus <= 2) or not (0 <= args.blind <= 2):
        print("Error: Age 65+ and blind counts must be between 0 and 2.")
        sys.exit(1)

    # Load Tax Data for specified year
    try:
        tax_data = load_tax_constants(args.year)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    tax_brackets = tax_data["tax_brackets_processed"]
    standard_deductions = tax_data["standard_deductions"]
    additional_deductions = tax_data["additional_deduction_65_blind"]

    # Normalize common filing status aliases
    aliases = {
        "mfj": "married_jointly",
        "mfs": "married_separately",
        "hoh": "head_of_household",
        "qss": "qualifying_surviving_spouse",
        "single": "single",
    }
    args.filing_status = aliases.get(args.filing_status.lower(), args.filing_status)

    # Validate filing status
    if args.filing_status not in tax_brackets:
        print(f"Error: Invalid filing status '{args.filing_status}'. Must be one of: {', '.join(tax_brackets.keys())}")
        sys.exit(1)

    # Calculate AGI
    agi = args.gross_income - args.adjustments

    # Determine deduction to use
    standard_deduction = calculate_standard_deduction(
        args.filing_status,
        standard_deductions,
        additional_deductions,
        args.age_65_plus,
        args.blind
    )
    deduction_used = max(standard_deduction, args.deductions)
    using_itemized = args.deductions > standard_deduction

    # Calculate taxable income
    taxable_income = max(0, agi - deduction_used)

    # Calculate tax
    tax_before_credits, breakdown = calculate_tax(taxable_income, args.filing_status, tax_brackets)

    # Apply credits
    tax_after_credits = max(0, tax_before_credits - args.credits)

    # Calculate rates
    effective_rate = calculate_effective_rate(tax_after_credits, args.gross_income)
    marginal_rate = calculate_marginal_rate(taxable_income, args.filing_status, tax_brackets)

    result = {
        "input": {
            "gross_income": args.gross_income,
            "year": args.year,
            "filing_status": args.filing_status,
            "adjustments": args.adjustments,
            "itemized_deductions": args.deductions,
            "credits": args.credits
        },
        "calculations": {
            "adjusted_gross_income": round(agi, 2),
            "standard_deduction": standard_deduction,
            "deduction_used": deduction_used,
            "using_itemized": using_itemized,
            "taxable_income": round(taxable_income, 2)
        },
        "tax": {
            "tax_before_credits": tax_before_credits,
            "credits_applied": min(args.credits, tax_before_credits),
            "tax_after_credits": tax_after_credits,
            "effective_rate_percent": effective_rate,
            "marginal_rate_percent": round(marginal_rate * 100, 2)
        },
        "breakdown": breakdown
    }

    if args.output_format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"\n--- {args.year} Federal Income Tax Calculation ---")
        print(f"Filing Status:          {args.filing_status.replace('_', ' ').title()}")
        print(f"Gross Income:           ${args.gross_income:,.2f}")
        print(f"Adjustments:            ${args.adjustments:,.2f}")
        print(f"AGI:                    ${agi:,.2f}")
        print(f"Deduction Used:         ${deduction_used:,.2f} ({'Itemized' if using_itemized else 'Standard'})")
        print(f"Taxable Income:         ${taxable_income:,.2f}")
        print("-" * 40)
        print(f"Tax Before Credits:     ${tax_before_credits:,.2f}")
        print(f"Credits Applied:        ${result['tax']['credits_applied']:,.2f}")
        print(f"Total Tax Liability:    ${tax_after_credits:,.2f}")
        print("-" * 40)
        print(f"Effective Tax Rate:     {effective_rate}%")
        print(f"Marginal Tax Rate:      {result['tax']['marginal_rate_percent']}%")
        print("\nTax Bracket Breakdown:")
        for b in breakdown:
            ceiling = format(b['bracket_ceiling'], ',.0f') if isinstance(b['bracket_ceiling'], (int, float)) else b['bracket_ceiling']
            print(f"  {int(b['rate']*100):>2}% on income up to {ceiling:>10}: ${b['tax_from_bracket']:,.2f}")


if __name__ == "__main__":
    main()
