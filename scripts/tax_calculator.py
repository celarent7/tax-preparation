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
from utils import (
    get_status_value,
    load_tax_constants,
    make_year_arg,
    normalize_filing_status,
    select_for_status,
    additional_medicare_threshold,
)


def calculate_tax(taxable_income: float, filing_status: str, tax_brackets: Dict) -> Tuple[float, List[Dict]]:
    """Calculate federal income tax and return breakdown by bracket."""
    brackets = select_for_status(tax_brackets, filing_status, "tax brackets")

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


def calculate_preferential_tax(ordinary_income: float, preferential_income: float,
                               ltcg_brackets: List[Tuple[float, float]]) -> Tuple[float, List[Dict]]:
    """Tax the preferential income stacked ON TOP of ordinary income.

    Implements the stacking rule behind the Qualified Dividends and Capital Gain
    Tax Worksheet: long-term capital gain and qualified dividends are taxed at
    0/15/20%, but the rate band is determined by TOTAL taxable income, so the
    preferential slice occupies the interval
        [ordinary_income, ordinary_income + preferential_income]
    and each LTCG bracket taxes only its overlap with that interval.

    Previously the plugin had no preferential-rate path at all: LTCG and
    qualified dividends were taxed as ordinary income (finding F3).
    """
    if preferential_income <= 0:
        return 0.0, []

    total = ordinary_income + preferential_income
    tax = 0.0
    breakdown = []
    prev_ceiling = 0.0

    for ceiling, rate in ltcg_brackets:
        # Overlap between this bracket's band and the preferential slice.
        band_low = max(ordinary_income, prev_ceiling)
        band_high = min(total, ceiling)
        amount = max(0.0, band_high - band_low)

        if amount > 0:
            bracket_tax = amount * rate
            tax += bracket_tax
            breakdown.append({
                "rate": rate,
                "bracket_floor": prev_ceiling,
                "bracket_ceiling": ceiling if ceiling != float("inf") else "unlimited",
                "preferential_income_in_bracket": round(amount, 2),
                "tax_from_bracket": round(bracket_tax, 2),
            })

        prev_ceiling = ceiling
        if ceiling >= total:
            break

    return round(tax, 2), breakdown


def calculate_niit(magi: float, net_investment_income: float, threshold: float,
                   rate: float = 0.038) -> Dict:
    """Net Investment Income Tax: 3.8% of the LESSER of NII or MAGI over threshold.

    IRC 1411. Thresholds are statutory and not inflation-indexed. The plugin
    carried niit_thresholds in its constants but never used them (finding F3).
    """
    excess_magi = max(0.0, magi - threshold)
    base = min(max(0.0, net_investment_income), excess_magi)
    return {
        "applies": base > 0,
        "threshold": threshold,
        "magi": round(magi, 2),
        "net_investment_income": round(max(0.0, net_investment_income), 2),
        "excess_magi_over_threshold": round(excess_magi, 2),
        "taxable_base": round(base, 2),
        "rate": rate,
        "tax": round(base * rate, 2),
    }


def calculate_additional_medicare(wages: float, threshold: float,
                                  rate: float = 0.009) -> Dict:
    """Additional Medicare Tax: 0.9% on wages/SE income above the threshold.

    IRC 3101(b)(2). Employers withhold it only above $200,000 regardless of
    filing status, so an MFJ couple is frequently over- or under-withheld.
    """
    excess = max(0.0, wages - threshold)
    return {
        "applies": excess > 0,
        "threshold": threshold,
        "wages": round(wages, 2),
        "excess_over_threshold": round(excess, 2),
        "rate": rate,
        "tax": round(excess * rate, 2),
    }


def assess_amt_risk(taxable_income: float, salt_lost_to_cap: float,
                    iso_bargain_element: float, amt_constants: Dict,
                    filing_status: str) -> Dict:
    """Flag AMT exposure WITHOUT computing Form 6251 (finding F8, task P2.6).

    Full AMT is explicitly out of scope (docs/limitations.md). This emits a
    warning object so a large ISO exercise or a deep exemption phase-out is at
    least surfaced rather than silently ignored.
    """
    status = normalize_filing_status(filing_status)
    if status == "married_jointly" or status == "qualifying_surviving_spouse":
        exemption = amt_constants.get("exemption_mfj")
        phaseout_start = amt_constants.get("phaseout_start_mfj")
    elif status == "married_separately":
        exemption = amt_constants.get("exemption_mfs")
        phaseout_start = amt_constants.get("phaseout_start_mfs")
    else:
        exemption = amt_constants.get("exemption_single")
        phaseout_start = amt_constants.get("phaseout_start_single")

    reasons = []
    # ISO bargain element is the classic AMT preference item.
    if iso_bargain_element > 0:
        reasons.append(
            f"ISO exercise with a ${iso_bargain_element:,.0f} bargain element is an "
            "AMT preference item added back on Form 6251."
        )
    # SALT is deductible for regular tax but not for AMT.
    if salt_lost_to_cap > 0:
        reasons.append(
            f"${salt_lost_to_cap:,.0f} of SALT was lost to the cap; SALT is not "
            "deductible at all for AMT."
        )
    if phaseout_start is not None and taxable_income > phaseout_start:
        reasons.append(
            f"Taxable income ${taxable_income:,.0f} exceeds the AMT exemption "
            f"phase-out threshold of ${phaseout_start:,.0f}."
        )

    rough_amti = taxable_income + iso_bargain_element + salt_lost_to_cap
    if exemption is not None and rough_amti > (phaseout_start or 0) * 0.5 and iso_bargain_element > 0:
        reasons.append("A Form 6251 computation is recommended for this return.")

    return {
        "amt_risk": bool(reasons),
        "reasons": reasons,
        "exemption_for_status": exemption,
        "phaseout_start": phaseout_start,
        "note": ("This is a risk FLAG only — the plugin does not compute Form 6251. "
                 "See docs/limitations.md."),
    }


def calculate_effective_rate(tax: float, gross_income: float) -> float:
    """Calculate effective tax rate."""
    if gross_income <= 0:
        return 0
    return round((tax / gross_income) * 100, 2)


def calculate_marginal_rate(taxable_income: float, filing_status: str, tax_brackets: Dict) -> float:
    """Determine marginal tax rate based on taxable income."""
    brackets = select_for_status(tax_brackets, filing_status, "tax brackets")

    for bracket_top, rate in brackets:
        if taxable_income <= bracket_top:
            return rate

    return brackets[-1][1]


def calculate_standard_deduction(filing_status: str, standard_deductions: Dict, additional_deductions: Dict, 
                                 age_65_plus: int = 0, blind: int = 0) -> float:
    """Calculate standard deduction including additional amounts for age/blindness.

    Both lookups raise on an unknown filing status. The previous
    `additional_deductions.get(status, 1950)` hard-coded the 2024 single
    amount as a default for unknown statuses (finding F12).
    """
    base = get_status_value(standard_deductions, filing_status, "standard deduction")
    additional = get_status_value(
        additional_deductions, filing_status, "additional 65/blind deduction"
    )

    return base + (additional * age_65_plus) + (additional * blind)


def main():
    parser = argparse.ArgumentParser(description="Calculate federal income tax")
    parser.add_argument("--gross-income", type=float, required=True, help="Gross income")
    make_year_arg(parser)
    parser.add_argument("--filing-status", default="single",
                        help="Filing status (single, mfj, mfs, hoh, qss or canonical form)")
    parser.add_argument("--deductions", type=float, default=0,
                        help="Itemized deductions (if greater than standard, will use itemized)")
    parser.add_argument("--adjustments", type=float, default=0,
                        help="Above-the-line adjustments (IRA, student loan interest, etc.)")
    parser.add_argument("--age-65-plus", type=int, default=0,
                        help="Number of taxpayers 65+ (0, 1, or 2)")
    parser.add_argument("--blind", type=int, default=0,
                        help="Number of blind taxpayers (0, 1, or 2)")
    parser.add_argument("--credits", type=float, default=0,
                        help="[deprecated] Total tax credits, treated as non-refundable. "
                             "Use --nonrefundable-credits / --refundable-credits.")
    parser.add_argument("--nonrefundable-credits", type=float, default=0,
                        help="Credits that cannot reduce tax below zero (CTC non-refundable part, AOTC 60%%, etc.)")
    parser.add_argument("--refundable-credits", type=float, default=0,
                        help="Credits payable even with no tax liability (EITC, ACTC, AOTC 40%%)")

    # Preferential-rate income (finding F3)
    parser.add_argument("--ltcg", type=float, default=0,
                        help="Net long-term capital gain (taxed at preferential rates)")
    parser.add_argument("--qualified-dividends", type=float, default=0,
                        help="Qualified dividends (taxed at preferential rates)")
    parser.add_argument("--wages", type=float, default=None,
                        help="W-2 wages, for the Additional Medicare Tax (defaults to gross income)")
    parser.add_argument("--net-investment-income", type=float, default=None,
                        help="Net investment income for NIIT (defaults to LTCG + qualified dividends)")
    parser.add_argument("--magi", type=float, default=None,
                        help="Modified AGI for NIIT (defaults to AGI)")

    # AMT trigger inputs (flag only — no Form 6251 computation)
    parser.add_argument("--iso-bargain-element", type=float, default=0,
                        help="ISO exercise spread, for the AMT risk flag")
    parser.add_argument("--salt-lost-to-cap", type=float, default=0,
                        help="SALT disallowed by the cap, for the AMT risk flag")

    parser.add_argument("--output-format", choices=["json", "text"], default="text",
                        help="Output format")

    args = parser.parse_args()

    # Input Validation
    if args.gross_income < 0:
        print("Error: Gross income cannot be negative.")
        sys.exit(1)
    if any(val < 0 for val in [args.deductions, args.adjustments, args.credits,
                               args.nonrefundable_credits, args.refundable_credits,
                               args.ltcg, args.qualified_dividends]):
        print("Error: Deductions, adjustments, credits and preferential income must be non-negative.")
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

    # Normalize filing status via the shared alias map (finding D2)
    try:
        args.filing_status = normalize_filing_status(args.filing_status)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    if args.filing_status not in tax_brackets:
        print(f"Error: No {args.year} tax brackets for filing status "
              f"'{args.filing_status}'. Available: {', '.join(sorted(tax_brackets))}")
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

    # --- Split taxable income into ordinary and preferential slices (F3) ---
    # Preferential income cannot exceed taxable income; deductions are applied
    # against ordinary income first, which is what the worksheet does.
    preferential_income = min(args.ltcg + args.qualified_dividends, taxable_income)
    ordinary_income = taxable_income - preferential_income

    ordinary_tax, breakdown = calculate_tax(ordinary_income, args.filing_status, tax_brackets)
    preferential_tax, preferential_breakdown = calculate_preferential_tax(
        ordinary_income, preferential_income,
        select_for_status(tax_data["ltcg_brackets_processed"], args.filing_status,
                          "long-term capital gain brackets")
    )
    income_tax = round(ordinary_tax + preferential_tax, 2)

    # --- Surtaxes ---
    magi = args.magi if args.magi is not None else agi
    net_investment_income = (args.net_investment_income
                             if args.net_investment_income is not None
                             else args.ltcg + args.qualified_dividends)
    niit = calculate_niit(
        magi, net_investment_income,
        get_status_value(tax_data["niit_thresholds"], args.filing_status, "NIIT threshold"),
    )

    wages = args.wages if args.wages is not None else args.gross_income
    add_medicare = calculate_additional_medicare(
        wages,
        additional_medicare_threshold(args.filing_status, tax_data["se_tax"]),
        tax_data["se_tax"]["additional_medicare_rate"],
    )

    tax_before_credits = round(income_tax + niit["tax"] + add_medicare["tax"], 2)

    # --- Credits: only non-refundable ones are floored at zero (P2.4) ---
    nonrefundable = args.nonrefundable_credits + args.credits
    refundable = args.refundable_credits

    nonrefundable_applied = min(nonrefundable, tax_before_credits)
    tax_after_nonrefundable = tax_before_credits - nonrefundable_applied
    tax_after_credits = round(tax_after_nonrefundable - refundable, 2)
    refund_from_credits = round(max(0.0, -tax_after_credits), 2)

    # AMT risk flag only (F8) — no Form 6251 computation.
    amt_risk = assess_amt_risk(
        taxable_income, args.salt_lost_to_cap, args.iso_bargain_element,
        tax_data.get("amt", {}), args.filing_status,
    )

    # Calculate rates
    effective_rate = calculate_effective_rate(max(0.0, tax_after_credits), args.gross_income)
    marginal_rate = calculate_marginal_rate(ordinary_income, args.filing_status, tax_brackets)

    result = {
        "input": {
            "gross_income": args.gross_income,
            "year": args.year,
            "filing_status": args.filing_status,
            "adjustments": args.adjustments,
            "itemized_deductions": args.deductions,
            "ltcg": args.ltcg,
            "qualified_dividends": args.qualified_dividends,
            "wages": wages,
            "nonrefundable_credits": nonrefundable,
            "refundable_credits": refundable
        },
        "calculations": {
            "adjusted_gross_income": round(agi, 2),
            "standard_deduction": standard_deduction,
            "deduction_used": deduction_used,
            "using_itemized": using_itemized,
            "taxable_income": round(taxable_income, 2),
            "ordinary_income": round(ordinary_income, 2),
            "preferential_income": round(preferential_income, 2)
        },
        "tax": {
            "ordinary_income_tax": ordinary_tax,
            "preferential_rate_tax": preferential_tax,
            "income_tax": income_tax,
            "niit": niit["tax"],
            "additional_medicare_tax": add_medicare["tax"],
            "tax_before_credits": tax_before_credits,
            "nonrefundable_credits_applied": round(nonrefundable_applied, 2),
            "refundable_credits_applied": round(refundable, 2),
            "tax_after_credits": round(max(0.0, tax_after_credits), 2),
            "refund_from_refundable_credits": refund_from_credits,
            "effective_rate_percent": effective_rate,
            "marginal_rate_percent": round(marginal_rate * 100, 2)
        },
        "niit_detail": niit,
        "additional_medicare_detail": add_medicare,
        "amt_risk": amt_risk,
        "breakdown": breakdown,
        "preferential_breakdown": preferential_breakdown
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
        if preferential_income > 0:
            print(f"  Ordinary portion:     ${ordinary_income:,.2f}")
            print(f"  Preferential portion: ${preferential_income:,.2f} (LTCG + qualified dividends)")
        print("-" * 40)
        print(f"Ordinary Income Tax:    ${ordinary_tax:,.2f}")
        if preferential_income > 0:
            print(f"Preferential Rate Tax:  ${preferential_tax:,.2f}")
        if niit["tax"] > 0:
            print(f"NIIT (3.8%):            ${niit['tax']:,.2f}")
        if add_medicare["tax"] > 0:
            print(f"Additional Medicare:    ${add_medicare['tax']:,.2f}")
        print(f"Tax Before Credits:     ${tax_before_credits:,.2f}")
        print(f"Nonrefundable Credits:  ${nonrefundable_applied:,.2f}")
        if refundable > 0:
            print(f"Refundable Credits:     ${refundable:,.2f}")
        print(f"Total Tax Liability:    ${max(0.0, tax_after_credits):,.2f}")
        if refund_from_credits > 0:
            print(f"Refund from Credits:    ${refund_from_credits:,.2f}")
        print("-" * 40)
        print(f"Effective Tax Rate:     {effective_rate}%")
        print(f"Marginal Tax Rate:      {result['tax']['marginal_rate_percent']}%")
        print("\nOrdinary Tax Bracket Breakdown:")
        for b in breakdown:
            ceiling = format(b['bracket_ceiling'], ',.0f') if isinstance(b['bracket_ceiling'], (int, float)) else b['bracket_ceiling']
            print(f"  {int(b['rate']*100):>2}% on income up to {ceiling:>10}: ${b['tax_from_bracket']:,.2f}")

        if preferential_breakdown:
            print("\nPreferential Rate Breakdown (LTCG / qualified dividends):")
            for b in preferential_breakdown:
                ceiling = format(b['bracket_ceiling'], ',.0f') if isinstance(b['bracket_ceiling'], (int, float)) else b['bracket_ceiling']
                print(f"  {int(b['rate']*100):>2}% on ${b['preferential_income_in_bracket']:>12,.2f} "
                      f"(band up to {ceiling}): ${b['tax_from_bracket']:,.2f}")

        if amt_risk["amt_risk"]:
            print("\n⚠️  AMT RISK FLAGGED (this tool does not compute Form 6251):")
            for reason in amt_risk["reasons"]:
                print(f"  - {reason}")


if __name__ == "__main__":
    main()
