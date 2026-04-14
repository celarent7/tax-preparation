#!/usr/bin/env python3
"""
Tax Savings Finder - Analyze tax data to find potential savings opportunities.
Proactively identifies overlooked deductions, credits, and optimization strategies.
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Any
from dataclasses import dataclass
from enum import Enum

# Add the scripts directory to the path to import utils
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import load_tax_constants


class Priority(Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

@dataclass
class TaxOpportunity:
    category: str
    name: str
    description: str
    potential_savings: str
    priority: Priority
    action_required: str
    documentation_needed: List[str]


def get_savings_rules(tax_data: Dict) -> Dict:
    """Initialize savings rules from tax data."""
    return {
        "hsa_contribution": {
            "single_limit": tax_data["retirement_limits"]["hsa"]["single"],
            "family_limit": tax_data["retirement_limits"]["hsa"]["family"],
            "catchup_55_plus": tax_data["retirement_limits"]["hsa"]["catchup_55_plus"]
        },
        "ira_contribution": {
            "limit": tax_data["retirement_limits"]["ira"]["limit"],
            "catchup_50_plus": tax_data["retirement_limits"]["ira"]["catchup_50_plus"]
        },
        "401k_contribution": {
            "limit": tax_data["retirement_limits"]["401k"]["limit"],
            "catchup_50_plus": tax_data["retirement_limits"]["401k"]["catchup_50_plus"]
        },
        "standard_deduction": tax_data["standard_deductions"],
        "salt_cap": tax_data["salt_cap"],
        "medical_threshold": tax_data["credits"]["medical_threshold"],
        "charitable_cash_limit": tax_data["credits"]["charitable_cash_limit"],
        "eitc_limits": {
            "max_credit": tax_data["credits"]["eitc"]["max_credit"],
            "phaseout_single": tax_data["credits"]["eitc"]["phaseout_single"],
            "phaseout_mfj": tax_data["credits"]["eitc"]["phaseout_mfj"]
        },
        "savers_credit_limits": tax_data["credits"]["savers_credit"],
        "tax_brackets": tax_data["tax_brackets_processed"]
    }


def estimate_marginal_rate(taxable_income: float, filing_status: str, tax_brackets: Dict) -> float:
    """Determine marginal tax rate."""
    brackets = tax_brackets.get(filing_status, tax_brackets["single"])
    for bracket_top, rate in brackets:
        if taxable_income <= bracket_top:
            return rate
    return brackets[-1][1]


def analyze_retirement_contributions(data: Dict, rules: Dict) -> List[TaxOpportunity]:
    """Find retirement contribution opportunities."""
    opportunities = []

    age = data.get("age", 40)
    filing_status = data.get("filing_status", "single")
    agi = data.get("agi", 0)
    tax_brackets = rules["tax_brackets"]

    # HSA analysis
    if data.get("has_hdhp", False):
        hsa_contributed = data.get("hsa_contributions", 0)
        coverage = data.get("hsa_coverage", "single")
        limit = rules["hsa_contribution"]["family_limit" if coverage == "family" else "single_limit"]
        if age >= 55:
            limit += rules["hsa_contribution"]["catchup_55_plus"]

        remaining = limit - hsa_contributed
        if remaining > 0:
            marginal_rate = estimate_marginal_rate(agi, filing_status, tax_brackets)
            potential_tax_savings = remaining * marginal_rate

            opportunities.append(TaxOpportunity(
                category="Retirement",
                name="HSA Contribution",
                description=f"You can contribute ${remaining:,.0f} more to your HSA",
                potential_savings=f"${potential_tax_savings:,.0f} in tax savings",
                priority=Priority.HIGH,
                action_required=f"Contribute additional ${remaining:,.0f} to HSA before April 15",
                documentation_needed=["HSA contribution receipts", "HDHP coverage documentation"]
            ))

    # Traditional IRA analysis
    ira_contributed = data.get("ira_contributions", 0)
    ira_limit = rules["ira_contribution"]["limit"]
    if age >= 50:
        ira_limit += rules["ira_contribution"]["catchup_50_plus"]
    
    remaining_ira = ira_limit - ira_contributed
    if remaining_ira > 0 and data.get("eligible_for_ira_deduction", True):
        marginal_rate = estimate_marginal_rate(agi, filing_status, tax_brackets)
        potential_tax_savings = remaining_ira * marginal_rate
        
        opportunities.append(TaxOpportunity(
            category="Retirement",
            name="Traditional IRA Contribution",
            description=f"You can contribute ${remaining_ira:,.0f} more to a Traditional IRA",
            potential_savings=f"Up to ${potential_tax_savings:,.0f} in tax savings",
            priority=Priority.MEDIUM,
            action_required=f"Contribute ${remaining_ira:,.0f} before tax deadline",
            documentation_needed=["IRA contribution receipt"]
        ))

    return opportunities


def analyze_deductions(data: Dict, rules: Dict) -> List[TaxOpportunity]:
    """Find deduction optimization opportunities."""
    opportunities = []
    
    agi = data.get("agi", 0)
    filing_status = data.get("filing_status", "single")
    std_deduction = rules["standard_deduction"].get(filing_status, rules["standard_deduction"]["single"])
    
    # Bunching opportunity
    current_deductions = data.get("itemized_total", 0)
    if current_deductions > 0 and current_deductions < std_deduction and current_deductions > (std_deduction * 0.7):
        opportunities.append(TaxOpportunity(
            category="Deductions",
            name="Charitable Bunching",
            description="Your itemized deductions are close to but below the standard deduction.",
            potential_savings="Significant savings over a 2-year period",
            priority=Priority.MEDIUM,
            action_required="Consider 'bunching' charitable donations or property tax into every other year",
            documentation_needed=["Donation receipts", "Property tax records"]
        ))
        
    return opportunities


def main():
    parser = argparse.ArgumentParser(description="Tax Savings Finder")
    parser.add_argument("--data", required=True, help="Path to JSON file with user tax profile")
    parser.add_argument("--year", type=str, default="2025", choices=["2024", "2025"], help="Tax year")
    parser.add_argument("--output-format", choices=["json", "text"], default="text")

    args = parser.parse_args()

    # Load Tax Data for specified year
    try:
        tax_data = load_tax_constants(args.year)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    rules = get_savings_rules(tax_data)

    # Load User Data
    try:
        with open(args.data, "r") as f:
            user_data = json.load(f)
    except Exception as e:
        print(f"Error loading data file: {e}")
        sys.exit(1)

    # Perform analyses
    opportunities = []
    opportunities.extend(analyze_retirement_contributions(user_data, rules))
    opportunities.extend(analyze_deductions(user_data, rules))

    if args.output_format == "json":
        print(json.dumps([asdict(o) for o in opportunities], indent=2))
    else:
        print(f"\n--- {args.year} Tax Savings Opportunities ---")
        if not opportunities:
            print("No immediate savings opportunities identified.")
        else:
            for i, opt in enumerate(opportunities, 1):
                print(f"{i}. [{opt.category}] {opt.name} ({opt.priority.value})")
                print(f"   Description: {opt.description}")
                print(f"   Potential:   {opt.potential_savings}")
                print(f"   Action:      {opt.action_required}")
                print("-" * 45)


if __name__ == "__main__":
    main()
