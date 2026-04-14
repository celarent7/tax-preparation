#!/usr/bin/env python3
"""
RSU Tax Calculator

Calculates federal and state tax liability for RSU compensation including:
- Ordinary income tax on vesting
- Capital gains tax on sales
- Net Investment Income Tax (NIIT)
- Additional Medicare Tax

Uses 2025 IRS tax brackets and rates.

Usage:
    python calculate_rsu_tax.py --filing-status single --total-income 300000 \
        --rsu-vesting-income 100000 --short-term-gains 5000 --long-term-gains 20000
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Tuple

# Add the scripts directory to the path to import utils
# This is in a subdirectory scripts/rsu/, so we need to go up two levels
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_tax_constants

# Load 2025 Tax Data
TAX_DATA = load_tax_constants()
MAP = TAX_DATA["filing_status_map"]

# Helper to map status to standard names used in tax_constants.json
def get_std_status(status: str) -> str:
    return MAP.get(status, status)

# Centralized constants from config
TAX_BRACKETS = {s: TAX_DATA["tax_brackets_processed"][get_std_status(s)] for s in MAP}
LTCG_BRACKETS = {s: TAX_DATA["ltcg_brackets_processed"][get_std_status(s)] for s in MAP}
NIIT_THRESHOLDS = {s: TAX_DATA["niit_thresholds"][get_std_status(s)] for s in MAP}
ADDITIONAL_MEDICARE_THRESHOLDS = {s: TAX_DATA["niit_thresholds"][get_std_status(s)] for s in MAP}
STANDARD_DEDUCTION = {s: TAX_DATA["standard_deductions"][get_std_status(s)] for s in MAP}
STATE_TAX_RATES = TAX_DATA["state_tax_rates"]


def calculate_ordinary_income_tax(taxable_income: float, filing_status: str) -> Tuple[float, List[Dict]]:
    """
    Calculate federal ordinary income tax using marginal brackets.

    Args:
        taxable_income: Taxable income after deductions
        filing_status: single, mfj, mfs, or hoh

    Returns:
        Tuple of (total_tax, breakdown_by_bracket)
    """
    brackets = TAX_BRACKETS.get(filing_status, TAX_BRACKETS["single"])
    total_tax = 0
    breakdown = []
    prev_limit = 0

    for limit, rate in brackets:
        if taxable_income <= prev_limit:
            break

        taxable_in_bracket = min(taxable_income, limit) - prev_limit
        if taxable_in_bracket > 0:
            tax_in_bracket = taxable_in_bracket * rate
            total_tax += tax_in_bracket
            breakdown.append({
                "bracket": f"${prev_limit:,.0f} - ${limit:,.0f}" if limit != float('inf') else f"Over ${prev_limit:,.0f}",
                "rate": f"{rate * 100:.0f}%",
                "taxable_amount": round(taxable_in_bracket, 2),
                "tax": round(tax_in_bracket, 2)
            })

        prev_limit = limit

    return round(total_tax, 2), breakdown


def calculate_ltcg_tax(taxable_income_before_gains: float, long_term_gains: float,
                       filing_status: str) -> Tuple[float, List[Dict]]:
    """
    Calculate long-term capital gains tax.
    LTCG is stacked on top of ordinary income.

    Args:
        taxable_income_before_gains: Ordinary taxable income
        long_term_gains: Long-term capital gains
        filing_status: single, mfj, mfs, or hoh

    Returns:
        Tuple of (total_ltcg_tax, breakdown_by_bracket)
    """
    brackets = LTCG_BRACKETS.get(filing_status, LTCG_BRACKETS["single"])
    total_tax = 0
    breakdown = []

    # LTCG is stacked on top of ordinary income
    start_point = taxable_income_before_gains
    remaining_gains = long_term_gains
    prev_limit = 0

    for limit, rate in brackets:
        if remaining_gains <= 0:
            break

        if start_point >= limit:
            prev_limit = limit
            continue

        # How much room in this bracket?
        room_in_bracket = limit - max(start_point, prev_limit)
        gains_in_bracket = min(remaining_gains, room_in_bracket)

        if gains_in_bracket > 0:
            tax_in_bracket = gains_in_bracket * rate
            total_tax += tax_in_bracket
            breakdown.append({
                "bracket": f"${prev_limit:,.0f} - ${limit:,.0f}" if limit != float('inf') else f"Over ${prev_limit:,.0f}",
                "rate": f"{rate * 100:.0f}%",
                "gains_amount": round(gains_in_bracket, 2),
                "tax": round(tax_in_bracket, 2)
            })
            remaining_gains -= gains_in_bracket

        prev_limit = limit

    return round(total_tax, 2), breakdown


def calculate_niit(magi: float, net_investment_income: float, filing_status: str) -> Dict:
    """
    Calculate Net Investment Income Tax (3.8%).

    Args:
        magi: Modified Adjusted Gross Income
        net_investment_income: Total investment income (cap gains, dividends, etc.)
        filing_status: single, mfj, mfs, or hoh

    Returns:
        Dictionary with NIIT calculation
    """
    threshold = NIIT_THRESHOLDS.get(filing_status, 200000)

    if magi <= threshold:
        return {
            "applies": False,
            "threshold": threshold,
            "magi": magi,
            "net_investment_income": net_investment_income,
            "niit": 0,
            "reason": f"MAGI (${magi:,.0f}) does not exceed threshold (${threshold:,.0f})"
        }

    excess_magi = magi - threshold
    niit_base = min(excess_magi, net_investment_income)
    niit = niit_base * 0.038

    return {
        "applies": True,
        "threshold": threshold,
        "magi": magi,
        "excess_over_threshold": round(excess_magi, 2),
        "net_investment_income": net_investment_income,
        "niit_base": round(niit_base, 2),
        "rate": "3.8%",
        "niit": round(niit, 2)
    }


def calculate_additional_medicare_tax(wages: float, filing_status: str) -> Dict:
    """
    Calculate Additional Medicare Tax (0.9%) on wages.

    Args:
        wages: Total wages including RSU vesting income
        filing_status: single, mfj, mfs, or hoh

    Returns:
        Dictionary with additional medicare tax calculation
    """
    threshold = ADDITIONAL_MEDICARE_THRESHOLDS.get(filing_status, 200000)

    if wages <= threshold:
        return {
            "applies": False,
            "threshold": threshold,
            "wages": wages,
            "additional_medicare_tax": 0,
            "reason": f"Wages (${wages:,.0f}) do not exceed threshold (${threshold:,.0f})"
        }

    excess_wages = wages - threshold
    additional_tax = excess_wages * 0.009

    return {
        "applies": True,
        "threshold": threshold,
        "wages": wages,
        "excess_over_threshold": round(excess_wages, 2),
        "rate": "0.9%",
        "additional_medicare_tax": round(additional_tax, 2)
    }


def estimate_state_tax(taxable_income: float, state: str, long_term_gains: float = 0) -> Dict:
    """
    Estimate state income tax (simplified calculation).

    Args:
        taxable_income: Total taxable income including cap gains
        state: State code (e.g., 'CA', 'WA')
        long_term_gains: Long-term gains (for states with special treatment)

    Returns:
        Dictionary with state tax estimate
    """
    state = state.upper()
    rate = STATE_TAX_RATES.get(state, 0)

    # Special handling for Washington
    if state == "WA" and long_term_gains > 270000:
        wa_cap_gains_tax = (long_term_gains - 270000) * 0.07
        return {
            "state": state,
            "income_tax": 0,
            "capital_gains_tax": round(wa_cap_gains_tax, 2),
            "total_state_tax": round(wa_cap_gains_tax, 2),
            "note": "WA has 7% capital gains tax on gains over $270,000 (2025)"
        }

    if rate == 0:
        return {
            "state": state,
            "income_tax": 0,
            "capital_gains_tax": 0,
            "total_state_tax": 0,
            "note": f"{state} has no state income tax" if state != "NONE" else "No state specified"
        }

    # California treats all cap gains as ordinary income
    if state == "CA":
        state_tax = taxable_income * rate
        return {
            "state": state,
            "rate": f"{rate * 100:.1f}%",
            "taxable_income": taxable_income,
            "estimated_tax": round(state_tax, 2),
            "total_state_tax": round(state_tax, 2),
            "note": "CA taxes capital gains as ordinary income. This is a simplified estimate using top marginal rate."
        }

    # Simplified calculation for other states
    state_tax = taxable_income * rate
    return {
        "state": state,
        "rate": f"{rate * 100:.2f}%",
        "taxable_income": taxable_income,
        "estimated_tax": round(state_tax, 2),
        "total_state_tax": round(state_tax, 2),
        "note": "This is a simplified estimate. Actual tax may vary based on brackets and deductions."
    }


def calculate_full_rsu_tax(
    filing_status: str,
    total_income: float,
    rsu_vesting_income: float,
    short_term_gains: float = 0,
    long_term_gains: float = 0,
    other_investment_income: float = 0,
    state: str = "NONE",
    itemized_deductions: float = 0
) -> Dict:
    """
    Calculate comprehensive RSU tax liability.

    Args:
        filing_status: single, mfj, mfs, or hoh
        total_income: Total W-2 income (includes RSU vesting)
        rsu_vesting_income: RSU vesting portion of income
        short_term_gains: Short-term capital gains from stock sales
        long_term_gains: Long-term capital gains from stock sales
        other_investment_income: Dividends, interest, etc.
        state: State code for state tax estimate
        itemized_deductions: If itemizing (0 = use standard)

    Returns:
        Dictionary with complete tax calculation
    """
    # Determine deduction
    standard_deduction = STANDARD_DEDUCTION.get(filing_status, STANDARD_DEDUCTION["single"])
    deduction = max(itemized_deductions, standard_deduction)
    deduction_type = "itemized" if itemized_deductions > standard_deduction else "standard"

    # AGI and MAGI
    agi = total_income + short_term_gains + long_term_gains + other_investment_income
    magi = agi  # For most purposes, MAGI = AGI

    # Taxable income for ordinary rates
    ordinary_income = total_income + short_term_gains
    taxable_ordinary_income = max(0, ordinary_income - deduction)

    # Calculate ordinary income tax
    ordinary_tax, ordinary_breakdown = calculate_ordinary_income_tax(
        taxable_ordinary_income, filing_status
    )

    # Calculate LTCG tax (stacked on top of ordinary income)
    ltcg_tax, ltcg_breakdown = calculate_ltcg_tax(
        taxable_ordinary_income, long_term_gains, filing_status
    )

    # Calculate NIIT
    net_investment_income = short_term_gains + long_term_gains + other_investment_income
    niit_result = calculate_niit(magi, net_investment_income, filing_status)

    # Calculate Additional Medicare Tax
    additional_medicare = calculate_additional_medicare_tax(total_income, filing_status)

    # State tax estimate
    total_taxable = taxable_ordinary_income + long_term_gains
    state_tax = estimate_state_tax(total_taxable, state, long_term_gains)

    # Total federal tax
    total_federal_tax = (
        ordinary_tax +
        ltcg_tax +
        niit_result.get("niit", 0) +
        additional_medicare.get("additional_medicare_tax", 0)
    )

    # Effective rates
    total_all_income = total_income + short_term_gains + long_term_gains + other_investment_income
    effective_federal_rate = (total_federal_tax / total_all_income * 100) if total_all_income > 0 else 0
    effective_total_rate = ((total_federal_tax + state_tax["total_state_tax"]) / total_all_income * 100) if total_all_income > 0 else 0

    return {
        "inputs": {
            "filing_status": filing_status,
            "total_income": total_income,
            "rsu_vesting_income": rsu_vesting_income,
            "non_rsu_income": total_income - rsu_vesting_income,
            "short_term_gains": short_term_gains,
            "long_term_gains": long_term_gains,
            "other_investment_income": other_investment_income,
            "state": state
        },
        "deductions": {
            "type": deduction_type,
            "amount": deduction,
            "standard_deduction": standard_deduction
        },
        "income_summary": {
            "agi": round(agi, 2),
            "magi": round(magi, 2),
            "taxable_ordinary_income": round(taxable_ordinary_income, 2),
            "long_term_capital_gains": round(long_term_gains, 2),
            "net_investment_income": round(net_investment_income, 2)
        },
        "federal_taxes": {
            "ordinary_income_tax": {
                "amount": ordinary_tax,
                "breakdown": ordinary_breakdown
            },
            "long_term_capital_gains_tax": {
                "amount": ltcg_tax,
                "breakdown": ltcg_breakdown
            },
            "net_investment_income_tax": niit_result,
            "additional_medicare_tax": additional_medicare,
            "total_federal_tax": round(total_federal_tax, 2)
        },
        "state_tax": state_tax,
        "totals": {
            "total_federal_tax": round(total_federal_tax, 2),
            "total_state_tax": round(state_tax["total_state_tax"], 2),
            "total_tax": round(total_federal_tax + state_tax["total_state_tax"], 2),
            "effective_federal_rate": f"{effective_federal_rate:.2f}%",
            "effective_total_rate": f"{effective_total_rate:.2f}%"
        },
        "rsu_specific": {
            "rsu_vesting_income": rsu_vesting_income,
            "estimated_tax_on_rsu_vesting": round(rsu_vesting_income * 0.30, 2),  # Rough estimate
            "typical_withholding_22_percent": round(rsu_vesting_income * 0.22, 2),
            "potential_underwithholding": round(rsu_vesting_income * 0.08, 2),  # Rough estimate
            "note": "RSU vesting tax is included in ordinary income. Consider if 22% withholding is sufficient."
        }
    }


def main():
    parser = argparse.ArgumentParser(
        description='Calculate comprehensive RSU tax liability',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Single filer with RSU income and capital gains:
    python calculate_rsu_tax.py --filing-status single --total-income 250000 \\
        --rsu-vesting-income 50000 --short-term-gains 5000 --long-term-gains 20000

  Married filing jointly in California:
    python calculate_rsu_tax.py --filing-status mfj --total-income 400000 \\
        --rsu-vesting-income 150000 --long-term-gains 50000 --state CA

Filing status options: single, mfj (married filing jointly), mfs (married filing separately), hoh (head of household)
        """
    )

    parser.add_argument('--filing-status', required=True,
                        choices=['single', 'mfj', 'mfs', 'hoh'],
                        help='Filing status')
    parser.add_argument('--total-income', type=float, required=True,
                        help='Total W-2 income (includes RSU vesting income)')
    parser.add_argument('--rsu-vesting-income', type=float, required=True,
                        help='RSU vesting income portion (already included in total-income)')
    parser.add_argument('--short-term-gains', type=float, default=0,
                        help='Short-term capital gains from stock sales')
    parser.add_argument('--long-term-gains', type=float, default=0,
                        help='Long-term capital gains from stock sales')
    parser.add_argument('--other-investment-income', type=float, default=0,
                        help='Other investment income (dividends, interest, etc.)')
    parser.add_argument('--state', type=str, default='NONE',
                        help='State code for state tax estimate (e.g., CA, WA, NY)')
    parser.add_argument('--itemized-deductions', type=float, default=0,
                        help='Itemized deductions (0 = use standard deduction)')
    parser.add_argument('--output', type=str, help='Output file path (optional)')

    args = parser.parse_args()

    result = calculate_full_rsu_tax(
        filing_status=args.filing_status,
        total_income=args.total_income,
        rsu_vesting_income=args.rsu_vesting_income,
        short_term_gains=args.short_term_gains,
        long_term_gains=args.long_term_gains,
        other_investment_income=args.other_investment_income,
        state=args.state,
        itemized_deductions=args.itemized_deductions
    )

    output_json = json.dumps(result, indent=2)

    if args.output:
        with open(args.output, 'w') as f:
            f.write(output_json)
        print(f"Results written to {args.output}")
    else:
        print(output_json)

    return result


if __name__ == '__main__':
    main()
