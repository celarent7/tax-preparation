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
from tax_calculator import calculate_niit
from utils import (
    additional_medicare_threshold,
    get_status_value,
    load_tax_constants,
    make_year_arg,
    normalize_filing_status,
    select_for_status,
    UnverifiedConstantError,
)
# qbi_calculator is imported lazily inside main() to avoid a circular import:
# qbi_calculator imports THIS module (for calculate_se_tax).


def get_due_dates(tax_data: Dict) -> List:
    """Quarterly due dates for the tax year, read from the constants file.

    Previously these were hard-coded with an `else` branch that returned 2025
    dates for ANY non-2024 year — a latent bug that would have silently
    mislabelled every 2026 deadline the moment 2026 was added (finding Y5).
    """
    dates = tax_data.get("estimated_tax_due_dates")
    if not dates:
        raise ValueError(
            f"No estimated_tax_due_dates defined for tax year "
            f"{tax_data.get('tax_year')} in scripts/tax_constants.json."
        )
    return [tuple(d) for d in dates]


def calculate_income_tax(taxable_income: float, filing_status: str, tax_brackets: Dict) -> float:
    """Calculate federal income tax."""
    brackets = select_for_status(tax_brackets, filing_status, "tax brackets")

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


def additional_medicare_8959(wages: float, se_net_profit: float,
                             filing_status: str, se_tax_data: Dict) -> Dict:
    """Additional Medicare Tax with the Form 8959 wage/SE coordination.

    Part I taxes wages above the threshold. Part II taxes SE income — at
    92.35% (the SE base) — above the threshold AS REDUCED by wages already
    counted in Part I. Charging 0.9% on raw (wages + SE) over the threshold,
    as the previous projection did, overstates the tax whenever SE income is
    present (the 7.65% haircut) and misallocates the threshold.

    IRC 3101(b)(2); IRS Form 8959 Parts I & II. With zero SE income this
    reproduces ``calculate_additional_medicare`` exactly (test-asserted), so
    the pure-wage path is provably unchanged.
    """
    rate = se_tax_data["additional_medicare_rate"]
    factor = se_tax_data["se_adjustment_factor"]
    threshold = additional_medicare_threshold(filing_status, se_tax_data)

    wage_part = max(0.0, wages - threshold) * rate
    se_base = max(0.0, se_net_profit) * factor
    se_threshold_after_wages = max(0.0, threshold - max(0.0, wages))
    se_part = max(0.0, se_base - se_threshold_after_wages) * rate

    return {
        "wage_part": round(wage_part, 2),
        "se_part": round(se_part, 2),
        "tax": round(wage_part + se_part, 2),
        "threshold": threshold,
        "se_threshold_after_wages": round(se_threshold_after_wages, 2),
        "se_base": round(se_base, 2),
        "rate": rate,
    }


#: AGI above which the prior-year safe harbor rises from 100% to 110%.
#: Source: IRS Form 1040-ES, https://www.irs.gov/pub/irs-pdf/f1040es.pdf —
#: "If your adjusted gross income (AGI) ... was more than $150,000 ($75,000 if
#: your filing status ... is married filing separately), substitute 110% for
#: 100%". Statutory (IRC 6654(d)(1)(C)), not inflation-indexed.
HIGH_INCOME_THRESHOLD = 150000
HIGH_INCOME_THRESHOLD_MFS = 75000


def high_income_threshold(filing_status: str) -> float:
    """Prior-year AGI threshold that triggers the 110% safe harbor.

    Married filing separately uses half the general threshold. Applying the
    $150,000 figure to an MFS filer understated the required payment and would
    have caused an underpayment penalty (finding F9).
    """
    status = normalize_filing_status(filing_status)
    if status == "married_separately":
        return HIGH_INCOME_THRESHOLD_MFS
    return HIGH_INCOME_THRESHOLD


def calculate_safe_harbor(prior_year_tax: float, prior_year_agi: float,
                          filing_status: str) -> Dict:
    """Calculate safe harbor amounts to avoid the underpayment penalty.

    Required annual payment (Form 1040-ES General Rule) is the LESSER of:
      (a) 90% of the tax shown on the current-year return, and
      (b) 100% of the tax shown on the prior-year return — substituting 110%
          when prior-year AGI exceeded the high-income threshold.
    This function computes leg (b); the caller applies the min() against leg (a).
    """
    threshold = high_income_threshold(filing_status)
    # The statute says "more than", so AGI exactly at the threshold does not
    # trigger the higher percentage.
    applies_high_income = prior_year_agi > threshold

    standard_safe_harbor = prior_year_tax
    high_income_safe_harbor = prior_year_tax * 1.10 if applies_high_income else prior_year_tax

    return {
        "standard": round(standard_safe_harbor, 2),
        "high_income": round(high_income_safe_harbor, 2),
        "threshold_used": threshold,
        "applies_high_income": applies_high_income,
        "minimum_required": round(
            high_income_safe_harbor if applies_high_income else standard_safe_harbor, 2
        )
    }


def calculate_ca_estimated_tax(projected_ca_tax: float, prior_year_ca_tax: float,
                               ca_agi: float, filing_status: str, est: Dict,
                               ca_withholding: float = 0) -> Dict:
    """FTB Form 540-ES required payment and installment schedule.

    Required annual payment is the LESSER of 90% of the current-year CA tax
    and the prior-year CA tax (110% when CA AGI exceeds the high-income
    threshold) — EXCEPT that filers with CA AGI at or above $1,000,000
    ($500,000 MFS) may not use the prior-year leg at all (R&TC 19136.3).
    Installments are NOT even quarters: 30% / 40% / 0% / 30% (FTB 540-ES).
    All figures come from the constants block; nothing is hard-coded here.
    """
    status = normalize_filing_status(filing_status)
    is_mfs = status == "married_separately"

    current_leg = round(projected_ca_tax * est["current_year_pct"], 2)
    hi_threshold = est["high_income_agi_mfs"] if is_mfs else est["high_income_agi"]
    # The statute says "more than", so AGI exactly at the threshold does not
    # trigger the 110% factor (mirrors the federal safe-harbor rule).
    prior_pct = est["high_income_prior_year_pct"] if ca_agi > hi_threshold else 1.0
    prior_leg = round(prior_year_ca_tax * prior_pct, 2)

    no_sh = est["safe_harbor_unavailable_agi_mfs"] if is_mfs else est["safe_harbor_unavailable_agi"]
    # "$1,000,000 or more" -> the prior-year leg is unavailable at >= the cap.
    safe_harbor_available = ca_agi < no_sh

    if safe_harbor_available and prior_leg < current_leg:
        required, basis = prior_leg, "prior-year safe harbor"
    else:
        required, basis = current_leg, "90% current-year"

    installments = [
        {"quarter": q, "due_date": due, "pct": pct,
         "amount": round(required * pct, 2)}
        for (q, due), pct in zip(est["due_dates"], est["installment_pct"])
    ]
    return {
        "required_annual_payment": required,
        "basis": basis,
        "current_year_leg": current_leg,
        "prior_year_leg": prior_leg,
        "prior_year_pct_applied": prior_pct,
        "safe_harbor_available": safe_harbor_available,
        "installments": installments,
        "remaining_after_withholding": round(max(0.0, required - ca_withholding), 2),
    }


def main():
    parser = argparse.ArgumentParser(description="Estimated Tax Calculator")
    parser.add_argument("--projected-income", type=float, required=True, help="Total projected gross income for the year")
    parser.add_argument("--projected-se-income", type=float, default=0, help="Projected net self-employment income")
    parser.add_argument("--prior-year-tax", type=float, required=True, help="Total tax from prior year return")
    parser.add_argument("--prior-year-agi", type=float, default=0, help="AGI from prior year return")
    parser.add_argument("--filing-status", default="single",
                        help="Filing status (single, mfj, mfs, hoh, qss or canonical form)")
    parser.add_argument("--projected-wages", type=float, default=None,
                        help="Projected W-2 wages for the Additional Medicare Tax (defaults to --projected-income)")
    parser.add_argument("--net-investment-income", type=float, default=0,
                        help="Projected net investment income, for NIIT")
    parser.add_argument("--withholding-to-date", type=float, default=0, help="Federal withholding to date")
    # --- QBI (§199A) subtraction (blueprint P3.2 / plan G3) ---
    # When projected SE income is present, the §199A QBI deduction is subtracted
    # from taxable income by DEFAULT (closing P3.2). --no-qbi disables it for a
    # control run. The engine is reused (no §199A math duplicated here).
    parser.add_argument("--no-qbi", action="store_true",
                        help="Do NOT subtract the §199A QBI deduction (control run). "
                             "By default the deduction is computed and subtracted when "
                             "projected SE income is present.")
    parser.add_argument("--se-health-insurance", type=float, default=0,
                        help="SE health insurance deduction (reduces QBI).")
    parser.add_argument("--se-retirement", type=float, default=0,
                        help="SE retirement (SEP/Solo-401k) deduction (reduces QBI).")
    parser.add_argument("--w2-wages-paid", type=float, default=0,
                        help="W-2 wages paid by the BUSINESS (the §199A wage limit).")
    parser.add_argument("--ubia", type=float, default=0,
                        help="Unadjusted basis of qualified business property.")
    parser.add_argument("--sstb", action="store_true",
                        help="Business is a Specified Service Trade or Business.")
    parser.add_argument("--reit-dividends", type=float, default=0,
                        help="1099-DIV Box 5 section 199A dividends.")
    parser.add_argument("--net-capital-gain", type=float, default=0,
                        help="Net LTCG + qualified dividends for the QBI overall limit.")
    parser.add_argument("--qbi-loss-carryforward", type=float, default=0,
                        help="Prior-year QBI loss carryforward.")
    make_year_arg(parser)
    parser.add_argument("--output-format", choices=["json", "text"], default="text")

    args = parser.parse_args()

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

    tax_brackets = tax_data["tax_brackets_processed"]
    standard_deductions = tax_data["standard_deductions"]
    se_tax_config = tax_data["se_tax"]

    # Calculate SE Tax
    se_results = calculate_se_tax(args.projected_se_income, se_tax_config)

    # Calculate AGI
    # Gross income includes SE income - adjustments (half of SE tax)
    agi = args.projected_income - se_results["deduction"]

    # Calculate Taxable Income
    std_deduction = get_status_value(
        standard_deductions, args.filing_status, "standard deduction"
    )
    taxable_income = max(0, agi - std_deduction)

    # --- QBI (§199A) deduction (blueprint P3.2 / plan G3) ---
    # When projected SE income is present, compute the §199A QBI deduction and
    # subtract it from taxable income (it is a from-AGI, non-itemized deduction,
    # taken AFTER the standard deduction). The §199A threshold test and overall
    # limit use taxable income BEFORE the QBI deduction, so the engine consumes
    # `taxable_income` as-is and the result is subtracted here. This is a single
    # fixed-point pass with the pre-QBI taxable income — the documented Form
    # 8995 approach: QBI does not depend on itself, so no iteration is needed.
    # The ½ SE tax is passed in byte-identical from se_results (already computed
    # above) rather than recomputed, guaranteeing AGI and adjusted-QBI agree.
    qbi_result = None
    qbi_deduction = 0.0
    if args.projected_se_income > 0 and not args.no_qbi:
        import qbi_calculator  # lazy: breaks the circular import (see top of file)
        qbi_profile = {
            "se_net_profit": args.projected_se_income,
            "se_tax_half": se_results["deduction"],
            "se_health_insurance": args.se_health_insurance,
            "se_retirement": args.se_retirement,
            "w2_wages_paid": args.w2_wages_paid,
            "ubia": args.ubia,
            "is_sstb": args.sstb,
            "taxable_income_before_qbi": taxable_income,
            "net_capital_gain": args.net_capital_gain,
            "reit_dividends": args.reit_dividends,
            "qbi_loss_carryforward": args.qbi_loss_carryforward,
            "filing_status": args.filing_status,
        }
        try:
            qbi_result = qbi_calculator.compute_qbi_deduction(qbi_profile, tax_data)
            qbi_deduction = qbi_result.deduction
        except UnverifiedConstantError as e:
            print(f"Error computing QBI: {e}")
            sys.exit(1)
        except ValueError as e:
            print(f"Error computing QBI: {e}")
            sys.exit(1)
        taxable_income = max(0, taxable_income - qbi_deduction)

    # Calculate Federal Income Tax (on taxable income AFTER the QBI deduction)
    income_tax = calculate_income_tax(taxable_income, args.filing_status, tax_brackets)

    # Additional Medicare + NIIT on the projection (task P3.2). Omitting these
    # understated the projected liability for exactly the high earners who owe
    # estimated tax.
    # Additional Medicare with Form 8959 wage/SE coordination (B5). When no SE
    # income is present this equals the plain wage computation (test-asserted).
    # The wage default is projected_income MINUS projected_se_income: previously
    # it defaulted to projected_income, which includes SE income and would
    # double-count it across the SE base in Part II.
    wages_for_medicare = (args.projected_wages if args.projected_wages is not None
                          else max(0.0, args.projected_income - args.projected_se_income))
    add_medicare = additional_medicare_8959(
        wages_for_medicare, args.projected_se_income, args.filing_status, se_tax_config)
    niit = calculate_niit(
        agi, args.net_investment_income,
        get_status_value(tax_data["niit_thresholds"], args.filing_status, "NIIT threshold"),
    )

    # Total estimated tax liability
    total_estimated_tax = (income_tax + se_results["total"]
                           + add_medicare["tax"] + niit["tax"])

    # Safe Harbor analysis
    safe_harbor = calculate_safe_harbor(
        args.prior_year_tax, args.prior_year_agi, args.filing_status
    )

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
            "additional_medicare_tax": add_medicare["tax"],
            "niit": niit["tax"],
            "total": round(total_estimated_tax, 2)
        },
        "additional_medicare_detail": add_medicare,
        "qbi": (qbi_result.to_dict() if qbi_result is not None else None),
        "safe_harbor": safe_harbor,
        "payment_plan": {
            "total_target_payment": round(total_target, 2),
            "withholding_to_date": args.withholding_to_date,
            "remaining_estimated_due": round(remaining_to_pay, 2),
            "quarterly_amount": round(quarterly_payment, 2),
            "due_dates": get_due_dates(tax_data)
        }
    }

    if args.output_format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"\n--- {args.year} Estimated Tax Calculation ---")
        print(f"Projected Total Liability:  ${total_estimated_tax:,.2f}")
        print(f"  (Income Tax: ${income_tax:,.2f}, SE Tax: ${se_results['total']:,.2f}"
              f", Add'l Medicare: ${add_medicare['tax']:,.2f}, NIIT: ${niit['tax']:,.2f})")
        if qbi_result is not None:
            print(f"  (QBI §199A deduction: ${qbi_deduction:,.2f} subtracted from "
                  f"taxable income — Form {qbi_result.form})")
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
