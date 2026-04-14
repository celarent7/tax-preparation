#!/usr/bin/env python3
"""
Deduction Analyzer - Compare standard vs itemized deductions and identify optimization opportunities.
"""

import argparse
import json
import os
import sys
from typing import Dict, List

# Add the scripts directory to the path to import utils
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import load_tax_constants


def analyze_medical_deduction(expenses: float, agi: float) -> Dict:
    """Calculate deductible medical expenses (>7.5% of AGI)."""
    threshold = agi * 0.075
    deductible = max(0, expenses - threshold)

    return {
        "total_expenses": expenses,
        "agi_threshold_75": round(threshold, 2),
        "deductible_amount": round(deductible, 2),
        "non_deductible": round(min(expenses, threshold), 2)
    }


def analyze_salt_deduction(
    property_tax: float,
    state_income_tax: float,
    state_sales_tax: float,
    filing_status: str,
    salt_cap: float = 10000,
    salt_cap_mfs: float = 5000
) -> Dict:
    """Analyze state and local tax deduction with cap."""
    cap = salt_cap_mfs if filing_status == "married_separately" else salt_cap

    # Can use income OR sales tax, not both
    income_or_sales = max(state_income_tax, state_sales_tax)
    used_sales_tax = state_sales_tax > state_income_tax

    total_before_cap = property_tax + income_or_sales
    deductible = min(total_before_cap, cap)
    lost_to_cap = max(0, total_before_cap - cap)

    return {
        "property_tax": property_tax,
        "state_income_tax": state_income_tax,
        "state_sales_tax": state_sales_tax,
        "used_sales_tax_method": used_sales_tax,
        "income_or_sales_used": round(income_or_sales, 2),
        "total_before_cap": round(total_before_cap, 2),
        "salt_cap": cap,
        "deductible_amount": round(deductible, 2),
        "lost_to_cap": round(lost_to_cap, 2)
    }


def analyze_mortgage_interest(
    interest_paid: float,
    acquisition_debt: float,
    filing_status: str,
    mortgage_debt_limit: float = 750000,
    mortgage_debt_limit_mfs: float = 375000
) -> Dict:
    """Analyze mortgage interest deduction."""
    debt_limit = mortgage_debt_limit_mfs if filing_status == "married_separately" else mortgage_debt_limit

    if acquisition_debt <= debt_limit:
        deductible = interest_paid
        limited = False
    else:
        # Pro-rate the interest
        ratio = debt_limit / acquisition_debt
        deductible = interest_paid * ratio
        limited = True

    return {
        "interest_paid": interest_paid,
        "acquisition_debt": acquisition_debt,
        "debt_limit": debt_limit,
        "deductible_amount": round(deductible, 2),
        "limited_by_debt_cap": limited
    }


def analyze_charitable(
    cash_donations: float,
    appreciated_property: float,
    agi: float
) -> Dict:
    """Calculate deductible charitable contributions."""
    # Simplified limits (typically 60% for cash, 30% for property)
    cash_limit = agi * 0.60
    property_limit = agi * 0.30

    deductible_cash = min(cash_donations, cash_limit)
    deductible_property = min(appreciated_property, property_limit)

    return {
        "cash_donations": cash_donations,
        "appreciated_property": appreciated_property,
        "cash_limit": round(cash_limit, 2),
        "property_limit": round(property_limit, 2),
        "deductible_amount": round(deductible_cash + deductible_property, 2),
        "carryover_potential": round(max(0, cash_donations - cash_limit) + max(0, appreciated_property - property_limit), 2)
    }


def main():
    parser = argparse.ArgumentParser(description="Deduction Analyzer")
    parser.add_argument("--agi", type=float, required=True, help="Adjusted Gross Income")
    parser.add_argument("--year", type=str, default="2025", choices=["2024", "2025"], help="Tax year")
    parser.add_argument("--filing-status", default="single", help="Filing status")
    parser.add_argument("--property-tax", type=float, default=0)
    parser.add_argument("--state-income-tax", type=float, default=0)
    parser.add_argument("--state-sales-tax", type=float, default=0)
    parser.add_argument("--mortgage-interest", type=float, default=0)
    parser.add_argument("--acquisition-debt", type=float, default=0)
    parser.add_argument("--charitable-cash", type=float, default=0)
    parser.add_argument("--charitable-property", type=float, default=0)
    parser.add_argument("--medical-expenses", type=float, default=0)
    parser.add_argument("--age-65-plus", type=int, default=0)
    parser.add_argument("--blind", type=int, default=0)
    parser.add_argument("--output-format", choices=["json", "text"], default="text")

    args = parser.parse_args()

    # Input Validation
    financial_inputs = [
        args.agi, args.property_tax, args.state_income_tax, args.state_sales_tax,
        args.mortgage_interest, args.acquisition_debt, args.charitable_cash,
        args.charitable_property, args.medical_expenses
    ]
    if any(val < 0 for val in financial_inputs):
        print("Error: All financial input values must be non-negative.")
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

    standard_deductions = tax_data["standard_deductions"]
    additional_deductions = tax_data["additional_deduction_65_blind"]
    salt_cap = tax_data["salt_cap"]
    salt_cap_mfs = tax_data["salt_cap_mfs"]
    mortgage_debt_limit = tax_data["mortgage_debt_limit"]
    mortgage_debt_limit_mfs = tax_data["mortgage_debt_limit_mfs"]

    # Analyze categories
    medical = analyze_medical_deduction(args.medical_expenses, args.agi)
    salt = analyze_salt_deduction(
        args.property_tax, args.state_income_tax, args.state_sales_tax, 
        args.filing_status, salt_cap, salt_cap_mfs
    )
    mortgage = analyze_mortgage_interest(
        args.mortgage_interest, args.acquisition_debt, args.filing_status,
        mortgage_debt_limit, mortgage_debt_limit_mfs
    )
    charitable = analyze_charitable(args.charitable_cash, args.charitable_property, args.agi)

    total_itemized = medical["deductible_amount"] + salt["deductible_amount"] + \
                     mortgage["deductible_amount"] + charitable["deductible_amount"]

    # Calculate standard deduction
    base_std = standard_deductions.get(args.filing_status, standard_deductions["single"])
    add_amt = additional_deductions.get(args.filing_status, 1950)
    standard_deduction = base_std + (add_amt * args.age_65_plus) + (add_amt * args.blind)

    use_itemized = total_itemized > standard_deduction
    benefit = abs(total_itemized - standard_deduction)

    result = {
        "year": args.year,
        "filing_status": args.filing_status,
        "agi": args.agi,
        "itemized_breakdown": {
            "medical": medical,
            "salt": salt,
            "mortgage": mortgage,
            "charitable": charitable,
            "total_itemized": round(total_itemized, 2)
        },
        "standard_deduction": {
            "base": base_std,
            "additional": round(standard_deduction - base_std, 2),
            "total": standard_deduction
        },
        "comparison": {
            "use_itemized": use_itemized,
            "deduction_to_use": round(max(standard_deduction, total_itemized), 2),
            "tax_savings_benefit": round(benefit, 2),
            "recommendation": "Itemize" if use_itemized else "Take standard deduction"
        }
    }

    if args.output_format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"\n--- {args.year} Deduction Analysis ---")
        print(f"Filing Status: {args.filing_status.replace('_', ' ').title()}")
        print(f"AGI:           ${args.agi:,.2f}")
        print("-" * 45)
        print(f"Standard Deduction:  ${standard_deduction:,.2f}")
        print(f"Total Itemized:      ${total_itemized:,.2f}")
        print("-" * 45)
        print(f"RESULT: {result['comparison']['recommendation']}")
        print(f"Benefit: ${benefit:,.2f} extra deduction")
        
        if not use_itemized and (total_itemized > standard_deduction * 0.8):
            print("\n💡 TIP: You are close to the itemizing threshold. Consider 'bunching' charitable")
            print("   contributions or property tax payments into alternating years.")

        print("\nItemized Breakdown:")
        print(f"  Medical (above 7.5%):  ${medical['deductible_amount']:,.2f}")
        print(f"  SALT (capped at $10k): ${salt['deductible_amount']:,.2f}")
        if salt['lost_to_cap'] > 0:
            print(f"    (Lost to SALT cap: ${salt['lost_to_cap']:,.2f})")
        print(f"  Mortgage Interest:     ${mortgage['deductible_amount']:,.2f}")
        print(f"  Charitable:            ${charitable['deductible_amount']:,.2f}")


if __name__ == "__main__":
    main()
