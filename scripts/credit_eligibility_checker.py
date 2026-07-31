#!/usr/bin/env python3
"""
Credit Eligibility Checker - Determine eligibility for major tax credits
and calculate potential credit amounts.
"""

import argparse
import json
import math
import os
import sys
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

# Add the scripts directory to the path to import utils
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import (
    UnverifiedConstantError,
    load_tax_constants,
    make_year_arg,
    normalize_filing_status,
)


@dataclass
class CreditResult:
    name: str
    eligible: bool
    amount: float
    refundable_amount: float
    phaseout_applied: bool
    notes: List[str]
    requirements_met: Dict[str, bool]
    documentation_needed: List[str]


def get_credit_params(tax_data: Dict) -> Dict:
    """Assemble credit parameters for a tax year from the constants file.

    Replaces the module-level CREDIT_PARAMS dict, which held 2024 figures with
    no --year switch and applied them silently to any year (finding Y1), and
    which duplicated the EITC table that also lived in tax_constants.json.
    """
    return {
        "child_tax_credit": tax_data["ctc"],
        "eitc": tax_data["credits"]["eitc"],
        "aotc": tax_data["credits_static"]["aotc"],
        "lifetime_learning": tax_data["credits_static"]["lifetime_learning"],
        "child_dependent_care": tax_data["credits_static"]["child_dependent_care"],
        "savers_credit": {
            "max_contribution": tax_data["credits_static"]["savers_credit_max_contribution"],
            "bands": tax_data["credits"]["savers_credit"],
        },
    }


def calculate_child_tax_credit(data: Dict, params: Dict) -> CreditResult:
    """Calculate Child Tax Credit eligibility and amount."""

    qualifying_children = data.get("children_under_17", 0)
    agi = data.get("agi", 0)
    filing_status = normalize_filing_status(data["filing_status"])
    earned_income = data.get("earned_income", 0)

    requirements = {
        "has_qualifying_children": qualifying_children > 0,
        "children_have_ssn": data.get("children_have_ssn", True),
        "income_below_limit": True  # CTC phases out slowly, rarely fully eliminated
    }

    notes = []
    docs = ["Children's Social Security cards", "Proof of relationship", "Proof of residency"]

    if not requirements["has_qualifying_children"]:
        return CreditResult(
            name="Child Tax Credit",
            eligible=False,
            amount=0,
            refundable_amount=0,
            phaseout_applied=False,
            notes=["No qualifying children under age 17"],
            requirements_met=requirements,
            documentation_needed=docs
        )

    # Calculate base credit
    base_credit = qualifying_children * params["per_child"]

    # Apply phaseout
    phaseout_start = params["phaseout_start_mfj"] if filing_status == "married_jointly" else params["phaseout_start_single"]
    phaseout_applied = False

    if agi > phaseout_start:
        excess = agi - phaseout_start
        # IRC 24(b): $50 for each $1,000 "or fraction thereof" of excess, i.e.
        # the excess rounds UP to the next $1,000. Schedule 8812 line 10 states
        # it explicitly: "if the result is $425, enter $1,000; if the result is
        # $1,025, enter $2,000". The old `excess // 1000` floored instead,
        # understating the phase-out by up to $50 (finding F4).
        thousands = math.ceil(excess / 1000)
        reduction = thousands * params["phaseout_per_1000"]
        base_credit = max(0, base_credit - reduction)
        phaseout_applied = True
        notes.append(f"Credit reduced by ${reduction:,.0f} due to income phaseout")

    # Calculate refundable portion (Additional Child Tax Credit)
    if earned_income > 2500:
        potential_refund = (earned_income - 2500) * 0.15
        refundable = min(potential_refund, params["refundable_max"] * qualifying_children, base_credit)
    else:
        refundable = 0

    notes.append(f"Credit: ${params['per_child']:,} per qualifying child under 17")

    return CreditResult(
        name="Child Tax Credit",
        eligible=base_credit > 0,
        amount=base_credit,
        refundable_amount=refundable,
        phaseout_applied=phaseout_applied,
        notes=notes,
        requirements_met=requirements,
        documentation_needed=docs
    )


def calculate_eitc(data: Dict, eitc_table: Dict) -> CreditResult:
    """Calculate Earned Income Tax Credit eligibility and amount.

    IRC 32(b)(1) sets a DIFFERENT phase-in and phase-out percentage for each
    qualifying-child count:

        children   phase-in   phase-out
        0            7.65%      7.65%
        1           34%        15.98%
        2           40%        21.06%
        3+          45%        21.06%

    The old code applied 34% and 15.98% to every child count (findings F5/F6),
    which overstated a zero-child filer's credit by roughly 4.4x. All figures
    now come from the per-year `eitc` block in tax_constants.json.
    """
    qualifying_children = min(data.get("qualifying_children_eitc", 0), 3)
    earned_income = data.get("earned_income", 0)
    agi = data.get("agi", 0)
    investment_income = data.get("investment_income", 0)
    filing_status = normalize_filing_status(data["filing_status"])
    age = data.get("age", 30)

    params = eitc_table[str(qualifying_children)]
    investment_limit = eitc_table["investment_income_limit"]

    requirements = {
        "has_earned_income": earned_income > 0,
        "investment_income_under_limit": investment_income <= investment_limit,
        "income_under_limit": True,
        "valid_ssn": data.get("has_valid_ssn", True),
        "not_mfs": filing_status != "married_separately",
        "age_requirement": qualifying_children > 0 or (25 <= age <= 64)
    }

    notes = []
    docs = ["Proof of earned income (W-2, Schedule C)", "Children's SSNs if applicable", "Proof of residency for children"]

    # Check disqualifying factors
    if not requirements["has_earned_income"]:
        return CreditResult("Earned Income Tax Credit", False, 0, 0, False,
                           ["No earned income"], requirements, docs)

    if not requirements["investment_income_under_limit"]:
        return CreditResult("Earned Income Tax Credit", False, 0, 0, False,
                           [f"Investment income ${investment_income:,.0f} exceeds ${investment_limit:,} limit"],
                           requirements, docs)

    if not requirements["not_mfs"]:
        return CreditResult("Earned Income Tax Credit", False, 0, 0, False,
                           ["Cannot claim EITC when filing Married Filing Separately"],
                           requirements, docs)

    if not requirements["age_requirement"]:
        return CreditResult("Earned Income Tax Credit", False, 0, 0, False,
                           ["Without qualifying children, must be age 25-64"],
                           requirements, docs)

    # Determine income limit (the completed-phaseout amount for this status)
    is_mfj = filing_status == "married_jointly"
    suffix = "mfj" if is_mfj else "other"
    income_limit = params[f"completed_phaseout_{suffix}"]

    income_for_calc = max(earned_income, agi)

    if income_for_calc > income_limit:
        requirements["income_under_limit"] = False
        return CreditResult("Earned Income Tax Credit", False, 0, 0, False,
                           [f"Income ${income_for_calc:,.0f} exceeds limit ${income_limit:,}"],
                           requirements, docs)

    phaseout_start = params[f"threshold_phaseout_{suffix}"]

    if income_for_calc <= params["earned_income_amount"]:
        # Phase-in: credit accrues at the per-child-count rate, capped at max.
        credit = min(earned_income * params["phase_in_rate"], params["max_credit"])
    elif income_for_calc <= phaseout_start:
        # Plateau between the earned income amount and the phase-out threshold.
        credit = params["max_credit"]
    else:
        # Phase-out at the per-child-count rate.
        excess = income_for_calc - phaseout_start
        reduction = excess * params["phase_out_rate"]
        credit = max(0, params["max_credit"] - reduction)

    credit = round(credit, 2)
    notes.append(f"Max credit for {qualifying_children} children: ${params['max_credit']:,}")
    notes.append(f"Phase-in {params['phase_in_rate']:.2%}, phase-out {params['phase_out_rate']:.2%}")
    notes.append("EITC is fully refundable")

    return CreditResult(
        name="Earned Income Tax Credit",
        eligible=credit > 0,
        amount=credit,
        refundable_amount=credit,  # EITC is fully refundable
        phaseout_applied=income_for_calc > phaseout_start,
        notes=notes,
        requirements_met=requirements,
        documentation_needed=docs
    )


def calculate_child_dependent_care(data: Dict, params: Dict) -> CreditResult:
    """Calculate Child and Dependent Care Credit."""

    qualifying_persons = data.get("qualifying_dependents_care", 0)
    expenses = data.get("childcare_expenses", 0)
    agi = data.get("agi", 0)
    earned_income = data.get("earned_income", 0)
    spouse_earned_income = data.get("spouse_earned_income", earned_income)
    dependent_care_fsa = data.get("dependent_care_fsa", 0)

    requirements = {
        "has_qualifying_person": qualifying_persons > 0,
        "has_expenses": expenses > 0,
        "has_earned_income": earned_income > 0,
        "expenses_for_work": True  # Assumed
    }

    notes = []
    docs = ["Provider name, address, SSN/EIN", "Amount paid to each provider", "Dependent care FSA records (if any)"]

    if not requirements["has_qualifying_person"]:
        return CreditResult("Child and Dependent Care Credit", False, 0, 0, False,
                           ["No qualifying person (child under 13 or disabled dependent)"],
                           requirements, docs)

    if not requirements["has_expenses"]:
        return CreditResult("Child and Dependent Care Credit", False, 0, 0, False,
                           ["No qualifying childcare expenses"], requirements, docs)

    # Expense limit
    expense_limit = params["expense_limit_two_plus"] if qualifying_persons >= 2 else params["expense_limit_one"]

    # Reduce by dependent care FSA
    net_expenses = max(0, expenses - dependent_care_fsa)

    # Limit by earned income (lower of taxpayer or spouse)
    lower_earned = min(earned_income, spouse_earned_income)
    eligible_expenses = min(net_expenses, expense_limit, lower_earned)

    # Determine credit percentage based on AGI
    if agi <= params["agi_for_max"]:
        percentage = params["max_percentage"]
    elif agi >= params["agi_for_min"]:
        percentage = params["min_percentage"]
    else:
        # Gradual reduction
        reduction = ((agi - params["agi_for_max"]) // 2000)
        percentage = max(params["min_percentage"], params["max_percentage"] - reduction)

    credit = eligible_expenses * (percentage / 100)

    notes.append(f"Credit rate: {percentage}% of eligible expenses")
    notes.append(f"Expense limit: ${expense_limit:,}")
    if dependent_care_fsa > 0:
        notes.append(f"Reduced by ${dependent_care_fsa:,} FSA contributions")

    return CreditResult(
        name="Child and Dependent Care Credit",
        eligible=credit > 0,
        amount=credit,
        refundable_amount=0,  # Non-refundable
        phaseout_applied=agi > params["agi_for_max"],
        notes=notes,
        requirements_met=requirements,
        documentation_needed=docs
    )


def calculate_aotc(data: Dict, params: Dict) -> CreditResult:
    """Calculate American Opportunity Tax Credit."""

    expenses = data.get("education_expenses", 0)
    agi = data.get("agi", 0)
    filing_status = normalize_filing_status(data["filing_status"])
    years_claimed = data.get("aotc_years_claimed", 0)
    is_student = data.get("is_eligible_student", False)

    requirements = {
        "is_eligible_student": is_student,
        "first_four_years": years_claimed < 4,
        "has_expenses": expenses > 0,
        "at_least_half_time": data.get("at_least_half_time", True),
        "no_felony_drug_conviction": data.get("no_felony_drug", True)
    }

    notes = []
    docs = ["Form 1098-T", "Receipts for books and supplies", "Enrollment verification"]

    if not requirements["is_eligible_student"]:
        return CreditResult("American Opportunity Tax Credit", False, 0, 0, False,
                           ["No eligible student"], requirements, docs)

    if not requirements["first_four_years"]:
        return CreditResult("American Opportunity Tax Credit", False, 0, 0, False,
                           ["AOTC already claimed for 4 years"], requirements, docs)

    # Calculate credit
    credit = min(expenses, params["expense_100_percent"]) * 1.0
    if expenses > params["expense_100_percent"]:
        credit += min(expenses - params["expense_100_percent"], params["expense_25_percent"]) * 0.25

    credit = min(credit, params["max_credit"])

    # Apply phaseout
    is_mfj = filing_status == "married_jointly"
    phaseout_start = params["phaseout_start_mfj"] if is_mfj else params["phaseout_start_single"]
    phaseout_end = params["phaseout_end_mfj"] if is_mfj else params["phaseout_end_single"]

    phaseout_applied = False
    if agi > phaseout_start:
        if agi >= phaseout_end:
            credit = 0
            notes.append("Credit fully phased out due to income")
        else:
            reduction_ratio = (agi - phaseout_start) / (phaseout_end - phaseout_start)
            credit = credit * (1 - reduction_ratio)
            phaseout_applied = True
            notes.append(f"Credit reduced due to income phaseout")

    refundable = credit * params["refundable_percent"] / 100

    notes.append(f"Max credit: ${params['max_credit']:,} per student")
    notes.append(f"40% refundable (up to $1,000)")

    return CreditResult(
        name="American Opportunity Tax Credit",
        eligible=credit > 0,
        amount=credit,
        refundable_amount=refundable,
        phaseout_applied=phaseout_applied,
        notes=notes,
        requirements_met=requirements,
        documentation_needed=docs
    )


def calculate_llc(data: Dict, params: Dict) -> CreditResult:
    """Lifetime Learning Credit (IRC 25A(c)): 20% of up to $10,000 of expenses.

    Nonrefundable; per-RETURN (not per-student); shares the AOTC MAGI band;
    cannot be claimed for the same student as the AOTC in the same year;
    MFS is statutorily ineligible.
    """
    status = normalize_filing_status(data["filing_status"])
    magi = float(data.get("magi", data.get("agi", 0)))
    expenses = float(data.get("education_expenses_llc", 0))
    notes: List[str] = []
    req = {"has_expenses": expenses > 0, "not_mfs": status != "married_separately",
           "not_aotc_same_student": not data.get("aotc_claimed_for_same_student", False)}

    if status == "married_separately":
        notes.append("Married filing separately cannot claim education credits (IRC 25A(g)(6)).")
        return CreditResult("Lifetime Learning Credit", False, 0, 0, False, notes, req, [])
    if data.get("aotc_claimed_for_same_student"):
        notes.append("AOTC already claimed for the same student this year — "
                     "the LLC cannot be claimed for that student (IRC 25A(c)(2)(A)).")
        return CreditResult("Lifetime Learning Credit", False, 0, 0, False, notes, req, [])
    if expenses <= 0:
        return CreditResult("Lifetime Learning Credit", False, 0, 0, False,
                            ["No qualified education expenses provided."], req, [])

    is_joint = status in ("married_jointly", "qualifying_surviving_spouse")
    lo = params["phaseout_start_mfj" if is_joint else "phaseout_start_single"]
    hi = params["phaseout_end_mfj" if is_joint else "phaseout_end_single"]
    base = params["credit_rate"] * min(expenses, params["expense_limit"])

    phaseout = False
    if magi >= hi:
        return CreditResult("Lifetime Learning Credit", False, 0, 0, True,
                            [f"MAGI ${magi:,.0f} is at or above the ${hi:,.0f} phase-out end."],
                            req, [])
    if magi > lo:
        base *= (hi - magi) / (hi - lo)
        phaseout = True
        notes.append(f"Credit reduced by the MAGI phase-out (${lo:,.0f}–${hi:,.0f}).")

    notes.append("Nonrefundable; per return, unlimited number of years; "
                 "no half-time-enrollment or first-4-years requirement (unlike AOTC).")
    return CreditResult("Lifetime Learning Credit", True, round(base, 2), 0.0,
                        phaseout, notes, req, ["Form 8863", "Form 1098-T"])


def calculate_savers_credit(data: Dict, params: Dict) -> CreditResult:
    """Calculate Retirement Savings Contribution Credit."""

    contributions = data.get("retirement_contributions", 0)
    agi = data.get("agi", 0)
    filing_status = normalize_filing_status(data["filing_status"])
    age = data.get("age", 30)
    is_student = data.get("is_full_time_student", False)

    # Constants carry mfj/single AGI bands; HoH bands are 75% of the MFJ ones
    # per IRC 25B(b). Only mfj/single are stored, so HoH derives from MFJ.
    status_key = "mfj" if filing_status == "married_jointly" else "single"
    bands = params["bands"][status_key]
    if any(v is None for v in bands.values()):
        raise UnverifiedConstantError(
            "Saver's Credit AGI bands have no verified value for this tax year. "
            "See _sources.TODO_VERIFY in scripts/tax_constants.json."
        )
    scale = 0.75 if filing_status == "head_of_household" else 1.0
    rates = [
        (bands["50_percent"] * scale, 0.50),
        (bands["20_percent"] * scale, 0.20),
        (bands["10_percent"] * scale, 0.10),
    ]

    requirements = {
        "has_contributions": contributions > 0,
        "age_18_plus": age >= 18,
        "not_full_time_student": not is_student,
        "not_dependent": not data.get("is_dependent", False),
        "income_under_limit": agi <= rates[-1][0]
    }

    notes = []
    docs = ["Form 5498 (IRA)", "W-2 Box 12 (401k)", "Retirement account statements"]

    if not requirements["has_contributions"]:
        return CreditResult("Saver's Credit", False, 0, 0, False,
                           ["No retirement contributions"], requirements, docs)

    if not requirements["income_under_limit"]:
        return CreditResult("Saver's Credit", False, 0, 0, False,
                           [f"AGI ${agi:,.0f} exceeds limit ${rates[-1][0]:,}"], requirements, docs)

    if not requirements["age_18_plus"] or not requirements["not_full_time_student"]:
        return CreditResult("Saver's Credit", False, 0, 0, False,
                           ["Must be 18+, not full-time student, not a dependent"], requirements, docs)

    # Determine rate
    credit_rate = 0
    for threshold, rate in rates:
        if agi <= threshold:
            credit_rate = rate
            break

    eligible_contributions = min(contributions, params["max_contribution"])
    credit = eligible_contributions * credit_rate

    notes.append(f"Credit rate: {credit_rate*100:.0f}% of contributions")
    notes.append(f"Max eligible contributions: ${params['max_contribution']:,}")

    return CreditResult(
        name="Saver's Credit",
        eligible=credit > 0,
        amount=credit,
        refundable_amount=0,
        phaseout_applied=False,
        notes=notes,
        requirements_met=requirements,
        documentation_needed=docs
    )


def calculate_ftc_simple(data: Dict, params: Dict) -> CreditResult:
    """Foreign Tax Credit under the IRC 904(j) no-Form-1116 election.

    Claimable directly on Schedule 3 when ALL foreign tax is passive-category
    (1099-DIV Box 7 / 1099-INT Box 6) and totals <= $300 ($600 MFJ). Above the
    limit the credit requires Form 1116, which is out of this tool's scope —
    flag, do not compute.
    """
    status = normalize_filing_status(data["filing_status"])
    paid = float(data.get("foreign_tax_paid", 0))
    limit = params["limit_mfj"] if status == "married_jointly" else params["limit"]
    req = {"has_foreign_tax": paid > 0, "within_election_limit": 0 < paid <= limit}

    if paid <= 0:
        return CreditResult("Foreign Tax Credit (no-1116 election)", False, 0, 0,
                            False, ["No foreign tax paid."], req, [])
    if paid > limit:
        return CreditResult(
            "Foreign Tax Credit (no-1116 election)", False, 0, 0, False,
            [f"Foreign tax ${paid:,.0f} exceeds the ${limit:,.0f} election limit — "
             "Form 1116 is required and is outside this tool's scope "
             "(see docs/limitations.md). The credit is still real; compute it "
             "with a professional or full-form software."],
            req, ["Form 1116"])
    return CreditResult(
        "Foreign Tax Credit (no-1116 election)", True, round(paid, 2), 0.0, False,
        ["Claim directly on Schedule 3 line 1 — commonly missed on international "
         "index-fund 1099-DIVs (Box 7)."],
        req, ["1099-DIV (Box 7)", "1099-INT (Box 6)"])


def check_all_credits(data: Dict, tax_data: Dict) -> Dict:
    """Check eligibility for all major credits for a given tax year."""
    params = get_credit_params(tax_data)
    results = []

    # Normalize once so every credit sees a canonical status (finding D2).
    if not data.get("filing_status"):
        raise ValueError("Tax data must specify 'filing_status'.")
    data = dict(data)
    data["filing_status"] = normalize_filing_status(data["filing_status"])

    # Run all credit calculations
    results.append(calculate_child_tax_credit(data, params["child_tax_credit"]))
    results.append(calculate_eitc(data, params["eitc"]))
    results.append(calculate_child_dependent_care(data, params["child_dependent_care"]))
    results.append(calculate_aotc(data, params["aotc"]))
    results.append(calculate_llc(data, params["lifetime_learning"]))
    results.append(calculate_savers_credit(data, params["savers_credit"]))
    results.append(calculate_ftc_simple(data, tax_data["credits_static"]["ftc_no_1116_limit"]))

    # Calculate totals
    total_credits = sum(r.amount for r in results if r.eligible)
    total_refundable = sum(r.refundable_amount for r in results if r.eligible)

    return {
        "summary": {
            "total_potential_credits": round(total_credits, 2),
            "total_refundable": round(total_refundable, 2),
            "credits_eligible": len([r for r in results if r.eligible]),
            "credits_checked": len(results)
        },
        "credits": [
            {
                "name": r.name,
                "eligible": r.eligible,
                "amount": round(r.amount, 2),
                "refundable_amount": round(r.refundable_amount, 2),
                "phaseout_applied": r.phaseout_applied,
                "notes": r.notes,
                "requirements_met": r.requirements_met,
                "documentation_needed": r.documentation_needed
            }
            for r in results
        ]
    }


def main():
    parser = argparse.ArgumentParser(description="Check tax credit eligibility")
    parser.add_argument("--data-file", type=str, help="JSON file with tax data")
    make_year_arg(parser)
    parser.add_argument("--output-format", choices=["json", "text"], default="text")

    # Common inline parameters
    parser.add_argument("--agi", type=float, default=50000)
    parser.add_argument("--earned-income", type=float, default=None)
    parser.add_argument("--filing-status", default="single",
                        help="Filing status (single, mfj, mfs, hoh, qss or canonical form)")
    parser.add_argument("--children-under-17", type=int, default=0)
    parser.add_argument("--qualifying-children-eitc", type=int, default=0)
    parser.add_argument("--childcare-expenses", type=float, default=0)
    parser.add_argument("--education-expenses", type=float, default=0)
    parser.add_argument("--retirement-contributions", type=float, default=0)
    parser.add_argument("--age", type=int, default=35)

    args = parser.parse_args()

    if args.data_file:
        with open(args.data_file) as f:
            data = json.load(f)
    else:
        data = {
            "agi": args.agi,
            "earned_income": args.earned_income or args.agi,
            "filing_status": args.filing_status,
            "children_under_17": args.children_under_17,
            "qualifying_children_eitc": args.qualifying_children_eitc or args.children_under_17,
            "childcare_expenses": args.childcare_expenses,
            "qualifying_dependents_care": 1 if args.childcare_expenses > 0 else 0,
            "education_expenses": args.education_expenses,
            "is_eligible_student": args.education_expenses > 0,
            "retirement_contributions": args.retirement_contributions,
            "age": args.age
        }

    try:
        tax_data = load_tax_constants(args.year)
        results = check_all_credits(data, tax_data)
    except (ValueError, UnverifiedConstantError) as e:
        print(f"Error: {e}")
        sys.exit(1)

    if args.output_format == "json":
        print(json.dumps(results, indent=2))
    else:
        print("\n" + "="*70)
        print(f"TAX CREDIT ELIGIBILITY CHECK — TAX YEAR {args.year}")
        print("="*70)

        summary = results["summary"]
        print(f"\nSUMMARY:")
        print(f"  Total Potential Credits: ${summary['total_potential_credits']:,.2f}")
        print(f"  Total Refundable: ${summary['total_refundable']:,.2f}")
        print(f"  Eligible Credits: {summary['credits_eligible']} of {summary['credits_checked']}")

        for credit in results["credits"]:
            print(f"\n{'-'*70}")
            status = "ELIGIBLE" if credit["eligible"] else "NOT ELIGIBLE"
            print(f"{credit['name']}: {status}")

            if credit["eligible"]:
                print(f"  Credit Amount: ${credit['amount']:,.2f}")
                if credit["refundable_amount"] > 0:
                    print(f"  Refundable Portion: ${credit['refundable_amount']:,.2f}")
                if credit["phaseout_applied"]:
                    print(f"  Note: Income phaseout applied")

            print(f"\n  Requirements:")
            for req, met in credit["requirements_met"].items():
                symbol = "[x]" if met else "[ ]"
                print(f"    {symbol} {req.replace('_', ' ').title()}")

            if credit["notes"]:
                print(f"\n  Notes:")
                for note in credit["notes"]:
                    print(f"    - {note}")

            if credit["eligible"]:
                print(f"\n  Documentation Needed:")
                for doc in credit["documentation_needed"]:
                    print(f"    - {doc}")

        print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    main()
