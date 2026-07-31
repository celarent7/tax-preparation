#!/usr/bin/env python3
"""
Deduction Analyzer - Compare standard vs itemized deductions and identify optimization opportunities.
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

# Add the scripts directory to the path to import utils
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import (
    get_constant,
    load_state_constants,
    get_status_value,
    load_tax_constants,
    make_year_arg,
    normalize_filing_status,
)


def analyze_medical_deduction(expenses: float, agi: float, threshold_rate: float) -> Dict:
    """Calculate deductible medical expenses above the AGI floor.

    The floor rate is read from constants rather than the inline 0.075 the
    script used to hard-code (finding D4).
    """
    threshold = agi * threshold_rate
    deductible = max(0, expenses - threshold)

    return {
        "total_expenses": expenses,
        "threshold_rate": threshold_rate,
        "agi_threshold": round(threshold, 2),
        # Retained under its old name so existing consumers keep working.
        "agi_threshold_75": round(threshold, 2),
        "deductible_amount": round(deductible, 2),
        "non_deductible": round(min(expenses, threshold), 2)
    }


def resolve_salt_cap(salt_constants: Dict, filing_status: str, magi: float = 0) -> float:
    """Resolve the SALT cap for a filing status and MAGI (audit finding F1).

    Post-OBBBA (2025-2029) the cap is $40,000 ($20,000 MFS), reduced by 30% of
    the amount by which MAGI exceeds $500,000 ($250,000 MFS), but never below
    $10,000 ($5,000 MFS). Pre-OBBBA years carry `phasedown: false` and a flat
    $10,000/$5,000 cap.

    Source: IRS, Instructions for Schedule A (Form 1040) (2025),
    https://www.irs.gov/instructions/i1040sca
    """
    status = normalize_filing_status(filing_status)
    is_mfs = status == "married_separately"

    cap = get_constant(salt_constants, "cap_mfs" if is_mfs else "cap")

    if not salt_constants.get("phasedown", False):
        return cap

    start = get_constant(
        salt_constants,
        "phasedown_magi_start_mfs" if is_mfs else "phasedown_magi_start",
    )
    rate = get_constant(salt_constants, "phasedown_rate")
    floor = get_constant(salt_constants, "floor_mfs" if is_mfs else "floor")

    if magi is None or magi <= start:
        return cap

    reduced = cap - rate * (magi - start)
    return round(max(floor, reduced), 2)


def analyze_salt_deduction(
    property_tax: float,
    state_income_tax: float,
    state_sales_tax: float,
    filing_status: str,
    salt_constants: Dict,
    magi: float = 0
) -> Dict:
    """Analyze state and local tax deduction with the year-correct cap."""
    cap_before_phasedown = get_constant(
        salt_constants,
        "cap_mfs" if normalize_filing_status(filing_status) == "married_separately" else "cap",
    )
    cap = resolve_salt_cap(salt_constants, filing_status, magi)

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
        "cap_before_phasedown": cap_before_phasedown,
        "phasedown_applied": cap < cap_before_phasedown,
        "magi_used": magi,
        "deductible_amount": round(deductible, 2),
        "lost_to_cap": round(lost_to_cap, 2)
    }


#: Debt incurred on or before this date keeps the pre-TCJA $1,000,000 limit
#: ($500,000 MFS). IRC 163(h)(3)(F); see IRS Pub 936 "Grandfathered debt" and
#: "Home acquisition debt limit".
GRANDFATHER_CUTOFF = "2017-12-15"
GRANDFATHERED_LIMIT = 1000000
GRANDFATHERED_LIMIT_MFS = 500000


def mortgage_debt_limit_for(filing_status: str, origination_date: Optional[str],
                            mortgage_debt_limit: float,
                            mortgage_debt_limit_mfs: float) -> Tuple[float, bool]:
    """Resolve the acquisition-debt limit, honoring pre-TCJA grandfathering.

    Debt incurred on or before December 15, 2017 keeps the $1,000,000 limit
    ($500,000 MFS); later debt is capped at $750,000 ($375,000 MFS). The script
    previously applied $750,000 unconditionally, understating the deduction for
    anyone with an older mortgage (finding D4).
    """
    is_mfs = normalize_filing_status(filing_status) == "married_separately"

    if origination_date and str(origination_date) <= GRANDFATHER_CUTOFF:
        return (GRANDFATHERED_LIMIT_MFS if is_mfs else GRANDFATHERED_LIMIT), True
    return (mortgage_debt_limit_mfs if is_mfs else mortgage_debt_limit), False


def analyze_mortgage_interest(
    interest_paid: float,
    acquisition_debt: float,
    filing_status: str,
    mortgage_debt_limit: float = 750000,
    mortgage_debt_limit_mfs: float = 375000,
    origination_date: Optional[str] = None,
    average_balance: Optional[float] = None
) -> Dict:
    """Analyze mortgage interest deduction.

    `average_balance` implements the average-balance method of Pub 936: when the
    principal is paid down over the year, the limit is applied against the
    average balance rather than the original balance, which is more favorable.
    """
    debt_limit, grandfathered = mortgage_debt_limit_for(
        filing_status, origination_date, mortgage_debt_limit, mortgage_debt_limit_mfs
    )

    # Pub 936 applies the limit against the average balance when one is given.
    balance_used = average_balance if average_balance is not None else acquisition_debt

    if balance_used <= debt_limit:
        deductible = interest_paid
        limited = False
    else:
        # Pro-rate the interest
        ratio = debt_limit / balance_used
        deductible = interest_paid * ratio
        limited = True

    return {
        "interest_paid": interest_paid,
        "acquisition_debt": acquisition_debt,
        "average_balance": average_balance,
        "balance_used_for_limit": balance_used,
        "debt_limit": debt_limit,
        "grandfathered_pre_tcja": grandfathered,
        "origination_date": origination_date,
        "deductible_amount": round(deductible, 2),
        "limited_by_debt_cap": limited
    }


def analyze_charitable(
    cash_donations: float,
    appreciated_property: float,
    agi: float,
    cash_limit_rate: float = 0.60,
    property_limit_rate: float = 0.30,
    cash_to_30_percent_orgs: float = 0,
    property_to_30_percent_orgs: float = 0
) -> Dict:
    """Calculate deductible charitable contributions with 30%-limit orgs.

    IRC 170(b) sets different AGI ceilings by both the type of gift and the type
    of donee. Public charities ("50% limit organizations") take cash up to 60%
    of AGI and appreciated capital-gain property up to 30%. Gifts to "30% limit
    organizations" (private non-operating foundations, veterans' organizations,
    fraternal societies) are capped at 30% of AGI for cash and 20% for
    appreciated property. The script previously modeled only the public-charity
    case (finding D4).
    """
    cash_limit = agi * cash_limit_rate
    property_limit = agi * property_limit_rate
    cash_30_limit = agi * 0.30
    property_20_limit = agi * 0.20

    deductible_cash = min(cash_donations, cash_limit)
    deductible_property = min(appreciated_property, property_limit)
    deductible_cash_30 = min(cash_to_30_percent_orgs, cash_30_limit)
    deductible_property_20 = min(property_to_30_percent_orgs, property_20_limit)

    total = deductible_cash + deductible_property + deductible_cash_30 + deductible_property_20
    carryover = (max(0, cash_donations - cash_limit)
                 + max(0, appreciated_property - property_limit)
                 + max(0, cash_to_30_percent_orgs - cash_30_limit)
                 + max(0, property_to_30_percent_orgs - property_20_limit))

    return {
        "cash_donations": cash_donations,
        "appreciated_property": appreciated_property,
        "cash_to_30_percent_orgs": cash_to_30_percent_orgs,
        "property_to_30_percent_orgs": property_to_30_percent_orgs,
        "cash_limit": round(cash_limit, 2),
        "property_limit": round(property_limit, 2),
        "cash_30_percent_org_limit": round(cash_30_limit, 2),
        "property_30_percent_org_limit": round(property_20_limit, 2),
        "deductible_amount": round(total, 2),
        "carryover_potential": round(carryover, 2),
        "carryover_note": "Excess contributions carry forward up to 5 years (IRC 170(d))."
    }


def student_loan_interest_deduction(interest_paid: float, magi: float,
                                    filing_status: str, tax_data: Dict) -> Dict:
    """Above-the-line student-loan interest deduction with the IRC 221 phase-out.

    MFS is statutorily ineligible (IRC 221(e)(2)). The phase-out prorates the
    allowable amount linearly across the MAGI band; single/HOH/QSS share one
    band, MFJ has its own. Constants only — no inline figures.
    """
    status = normalize_filing_status(filing_status)
    limit = tax_data["student_loan_interest_limit"]
    if status == "married_separately":
        return {"eligible": False, "deduction": 0.0, "limit": limit,
                "phaseout_applied": False, "fully_phased_out": False,
                "note": "Married filing separately cannot claim the student-loan "
                        "interest deduction (IRC 221(e)(2))."}

    band = tax_data["student_loan_interest_phaseout"]
    is_mfj = status in ("married_jointly", "qualifying_surviving_spouse")
    lo = get_constant(band, "magi_start_mfj" if is_mfj else "magi_start")
    hi = get_constant(band, "magi_end_mfj" if is_mfj else "magi_end")

    base = min(max(0.0, interest_paid), limit)
    if magi >= hi:
        return {"eligible": True, "deduction": 0.0, "limit": limit,
                "phaseout_applied": True, "fully_phased_out": True,
                "note": f"MAGI ${magi:,.0f} is at or above the ${hi:,.0f} "
                        "phase-out end — deduction fully phased out."}
    if magi > lo:
        base = round(base * (hi - magi) / (hi - lo), 2)
        return {"eligible": True, "deduction": base, "limit": limit,
                "phaseout_applied": True, "fully_phased_out": False,
                "note": f"Deduction prorated across the ${lo:,.0f}–${hi:,.0f} "
                        "MAGI band (Schedule 1 line 21 worksheet)."}
    return {"eligible": True, "deduction": base, "limit": limit,
            "phaseout_applied": False, "fully_phased_out": False,
            "note": "Full deduction — MAGI below the phase-out band."}


def main():
    parser = argparse.ArgumentParser(description="Deduction Analyzer")
    parser.add_argument("--agi", type=float, required=True, help="Adjusted Gross Income")
    parser.add_argument("--magi", type=float, default=None,
                        help="Modified AGI for the SALT cap phase-down "
                             "(2025+; defaults to --agi when omitted)")
    make_year_arg(parser)
    parser.add_argument("--filing-status", default="single",
                        help="Filing status (single, mfj, mfs, hoh, qss or canonical form)")
    parser.add_argument("--property-tax", type=float, default=0)
    parser.add_argument("--state-income-tax", type=float, default=0)
    parser.add_argument("--state-sales-tax", type=float, default=0)
    parser.add_argument("--mortgage-interest", type=float, default=0)
    parser.add_argument("--acquisition-debt", type=float, default=0)
    parser.add_argument("--charitable-cash", type=float, default=0)
    parser.add_argument("--charitable-property", type=float, default=0)
    parser.add_argument("--medical-expenses", type=float, default=0)
    parser.add_argument("--loan-origination-date", type=str, default=None,
                        help="Mortgage origination date YYYY-MM-DD; on or before "
                             "2017-12-15 keeps the pre-TCJA $1M limit")
    parser.add_argument("--average-mortgage-balance", type=float, default=None,
                        help="Average balance for the Pub 936 average-balance method")
    parser.add_argument("--charitable-cash-30-org", type=float, default=0,
                        help="Cash gifts to 30%%-limit organizations (private foundations)")
    parser.add_argument("--charitable-property-30-org", type=float, default=0,
                        help="Appreciated property gifts to 30%%-limit organizations")
    parser.add_argument("--student-loan-interest", type=float, default=0,
                        help="Qualified student-loan interest paid (above-the-line "
                             "Schedule 1 deduction, IRC 221; phases out by MAGI)")
    parser.add_argument("--age-65-plus", type=int, default=0)
    parser.add_argument("--blind", type=int, default=0)
    parser.add_argument("--state", type=str, default=None,
                        help="State of residence, e.g. CA, for a state itemize-vs-standard comparison")
    parser.add_argument("--output-format", choices=["json", "text"], default="text")

    args = parser.parse_args()

    # Input Validation
    financial_inputs = [
        args.agi, args.property_tax, args.state_income_tax, args.state_sales_tax,
        args.mortgage_interest, args.acquisition_debt, args.charitable_cash,
        args.charitable_property, args.medical_expenses, args.student_loan_interest
    ]
    if any(val < 0 for val in financial_inputs):
        print("Error: All financial input values must be non-negative.")
        sys.exit(1)
    if not (0 <= args.age_65_plus <= 2) or not (0 <= args.blind <= 2):
        print("Error: Age 65+ and blind counts must be between 0 and 2.")
        sys.exit(1)

    # Normalize filing status before any lookup (finding D2)
    try:
        args.filing_status = normalize_filing_status(args.filing_status)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Load Tax Data for specified year
    try:
        tax_data = load_tax_constants(args.year)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    standard_deductions = tax_data["standard_deductions"]
    additional_deductions = tax_data["additional_deduction_65_blind"]
    salt_constants = tax_data["salt"]
    mortgage_debt_limit = tax_data["mortgage_debt_limit"]
    mortgage_debt_limit_mfs = tax_data["mortgage_debt_limit_mfs"]

    # MAGI drives the SALT phase-down; absent an explicit figure, AGI is the
    # best available proxy (they differ only by a few uncommon add-backs).
    magi = args.magi if args.magi is not None else args.agi

    # Analyze categories
    medical = analyze_medical_deduction(
        args.medical_expenses, args.agi, tax_data["credits"]["medical_threshold"]
    )
    salt = analyze_salt_deduction(
        args.property_tax, args.state_income_tax, args.state_sales_tax,
        args.filing_status, salt_constants, magi
    )
    mortgage = analyze_mortgage_interest(
        args.mortgage_interest, args.acquisition_debt, args.filing_status,
        mortgage_debt_limit, mortgage_debt_limit_mfs,
        origination_date=args.loan_origination_date,
        average_balance=args.average_mortgage_balance,
    )
    charitable = analyze_charitable(
        args.charitable_cash, args.charitable_property, args.agi,
        cash_limit_rate=tax_data["credits"]["charitable_cash_limit"],
        property_limit_rate=tax_data["charitable_property_agi_limit"],
        cash_to_30_percent_orgs=args.charitable_cash_30_org,
        property_to_30_percent_orgs=args.charitable_property_30_org,
    )

    total_itemized = medical["deductible_amount"] + salt["deductible_amount"] + \
                     mortgage["deductible_amount"] + charitable["deductible_amount"]

    # Calculate standard deduction
    base_std = get_status_value(standard_deductions, args.filing_status, "standard deduction")
    add_amt = get_status_value(
        additional_deductions, args.filing_status, "additional 65/blind deduction"
    )
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

    # --- OBBBA senior bonus (above-the-line, Schedule 1) — shared helper with
    # the savings finder's M10 rule (blueprint P5.3, acceptance A9). This is
    # NOT part of the standard-vs-itemized choice; it is a separate
    # above-the-line deduction, surfaced only when the year carries the
    # `obbba_deductions` block and at least one person is 65+. ---
    if args.age_65_plus > 0 and "obbba_deductions" in tax_data:
        from savings_rules.obbba import (
            senior_bonus_deduction,
            senior_bonus_phaseout_start,
        )
        senior_bonus = senior_bonus_deduction(
            tax_data, args.filing_status, magi, args.age_65_plus)
        phaseout_start = senior_bonus_phaseout_start(tax_data, args.filing_status)
        block = tax_data["obbba_deductions"]["senior_bonus"]
        result["obbba_senior_bonus"] = {
            "deduction_amount": senior_bonus,
            "qualifying_persons": args.age_65_plus,
            "magi": magi,
            "phaseout_start": phaseout_start,
            "phaseout_rate": block["phaseout_rate"],
            "fully_phased_out": senior_bonus == 0.0,
            "note": ("Above-the-line deduction on Schedule 1 — not part of the "
                     "standard-vs-itemized decision. Phases out at "
                     f"{block['phaseout_rate']:.0%} per dollar of MAGI over "
                     f"${phaseout_start:,.0f}."),
        }

    # --- Student-loan interest (above-the-line, Schedule 1, IRC 221) — like
    # the OBBBA senior bonus this is NOT part of the standard-vs-itemized
    # choice; it is a separate above-the-line deduction, surfaced only when
    # the user reports interest paid. ---
    if args.student_loan_interest > 0:
        result["student_loan_interest"] = student_loan_interest_deduction(
            args.student_loan_interest, magi, args.filing_status, tax_data)

    # --- State comparison: CA itemize-vs-standard is a SEPARATE decision (C4) ---
    if args.state and args.state.upper() == "CA":
        try:
            ca = load_state_constants("CA", args.year)
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)

        ca_standard = get_status_value(ca["standard_deduction"], args.filing_status,
                                       "California standard deduction")
        # CA itemized starts from the federal itemized total, then:
        #   - removes state income tax entirely (not deductible for CA)
        #   - adds back SALT lost to the federal cap that CA would allow
        #     (property tax remains deductible for CA)
        #   + allows mortgage interest on up to $1,000,000 of debt
        ca_mortgage = analyze_mortgage_interest(
            args.mortgage_interest, args.acquisition_debt, args.filing_status,
            ca["nonconformity"]["mortgage_debt_limit"],
            ca["nonconformity"]["mortgage_debt_limit"] / 2,
            origination_date=args.loan_origination_date,
            average_balance=args.average_mortgage_balance,
        )
        mortgage_delta = round(ca_mortgage["deductible_amount"] - mortgage["deductible_amount"], 2)

        ca_itemized = round(
            medical["deductible_amount"]
            + salt["property_tax"]                      # property tax only, uncapped for CA
            + ca_mortgage["deductible_amount"]
            + charitable["deductible_amount"], 2)

        ca_use_itemized = ca_itemized > ca_standard
        result["california"] = {
            "ca_standard_deduction": ca_standard,
            "ca_itemized_total": ca_itemized,
            "state_income_tax_excluded": salt["state_income_tax"],
            "mortgage_deduction_delta_vs_federal": mortgage_delta,
            "ca_mortgage_debt_limit": ca["nonconformity"]["mortgage_debt_limit"],
            "use_itemized_for_ca": ca_use_itemized,
            "recommendation": ("Itemize for California" if ca_use_itemized
                               else "Take the California standard deduction"),
            "differs_from_federal_decision": ca_use_itemized != use_itemized,
            "notes": [
                "California's standard deduction is far smaller than the federal one, "
                "so the itemize decision must be made separately for CA.",
                "State income tax is NOT deductible on the California return.",
                "California allows mortgage interest on up to $1,000,000 of "
                "acquisition debt (federal is $750,000), which can make itemizing "
                "worthwhile for CA even when the federal return takes the standard "
                "deduction.",
            ],
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
        print(f"  Medical (above {medical['threshold_rate']:.1%}):  ${medical['deductible_amount']:,.2f}")
        print(f"  SALT (cap ${salt['salt_cap']:,.0f}):{'':<4} ${salt['deductible_amount']:,.2f}")
        if salt["phasedown_applied"]:
            print(f"    (Cap phased down from ${salt['cap_before_phasedown']:,.0f} "
                  f"at MAGI ${salt['magi_used']:,.0f})")
        if salt['lost_to_cap'] > 0:
            print(f"    (Lost to SALT cap: ${salt['lost_to_cap']:,.2f})")
        print(f"  Mortgage Interest:     ${mortgage['deductible_amount']:,.2f}")
        print(f"  Charitable:            ${charitable['deductible_amount']:,.2f}")

        if "obbba_senior_bonus" in result:
            sb = result["obbba_senior_bonus"]
            print("\n--- OBBBA Senior Bonus (above-the-line, Schedule 1) ---")
            if sb["fully_phased_out"]:
                print(f"  $0 — fully phased out at MAGI ${sb['magi']:,.0f} "
                      f"(phase-out starts at ${sb['phaseout_start']:,.0f}).")
            else:
                print(f"  Deduction: ${sb['deduction_amount']:,.0f} "
                      f"({sb['qualifying_persons']} qualifying person(s), MAGI ${sb['magi']:,.0f}; "
                      f"phases out at {sb['phaseout_rate']:.0%}/$ over ${sb['phaseout_start']:,.0f}).")
            print("  Note: separate from the standard-vs-itemized decision above.")

        if "student_loan_interest" in result:
            sli = result["student_loan_interest"]
            print("\n--- Student-Loan Interest (above-the-line, Schedule 1) ---")
            if not sli["eligible"]:
                print(f"  $0 — {sli['note']}")
            elif sli["fully_phased_out"]:
                print(f"  $0 — fully phased out ({sli['note']})")
            elif sli["phaseout_applied"]:
                print(f"  Deduction: ${sli['deduction']:,.2f} of up to ${sli['limit']:,.0f} "
                      f"interest paid — {sli['note']}")
            else:
                print(f"  Deduction: ${sli['deduction']:,.2f} of up to ${sli['limit']:,.0f} "
                      f"interest paid — {sli['note']}")
            print("  Note: separate from the standard-vs-itemized decision above.")

        if "california" in result:
            c = result["california"]
            print("\n--- California (separate decision) ---")
            print(f"  CA Standard Deduction: ${c['ca_standard_deduction']:,.2f}")
            print(f"  CA Itemized Total:     ${c['ca_itemized_total']:,.2f}")
            if c["mortgage_deduction_delta_vs_federal"]:
                print(f"  Extra CA mortgage interest ($1M limit): "
                      f"${c['mortgage_deduction_delta_vs_federal']:,.2f}")
            print(f"  RESULT: {c['recommendation']}")
            if c["differs_from_federal_decision"]:
                print("  ⚠️  This DIFFERS from the federal decision above.")


if __name__ == "__main__":
    main()
