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
    get_constant,
    load_tax_constants,
    make_year_arg,
    normalize_filing_status,
    select_for_status,
    additional_medicare_threshold,
    load_state_constants,
    UnverifiedConstantError,
)
# qbi_calculator is imported lazily inside main() to avoid a circular import:
# qbi_calculator -> estimated_tax_calculator -> tax_calculator.


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


def apply_capital_loss(net_capital_loss: float, filing_status: str,
                       capital_loss_limit: float,
                       capital_loss_limit_mfs: float) -> Dict:
    """Apply the IRC 1211(b) net-capital-loss offset against ordinary income.

    A net capital loss offsets ordinary income only up to $3,000 ($1,500 MFS);
    the remainder carries forward indefinitely (IRC 1212(b)). Limits are read
    from the year constants — never hard-coded here.
    """
    status = normalize_filing_status(filing_status)
    limit = (capital_loss_limit_mfs if status == "married_separately"
             else capital_loss_limit)
    loss = max(0.0, float(net_capital_loss))
    allowed = min(loss, limit)
    return {
        "net_capital_loss": round(loss, 2),
        "allowed_offset": round(allowed, 2),
        "carryforward": round(loss - allowed, 2),
        "limit_used": limit,
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


def amt_quick_test(taxable_income: float, salt_deducted: float,
                   iso_bargain_element: float, regular_tax: float,
                   filing_status: str, amt_constants: Dict) -> Dict:
    """Quantitative AMT SCREEN — not a Form 6251 computation (plan B6).

    AMTI is approximated as taxable income + SALT actually deducted + ISO
    bargain element. This deliberately ignores other preference items and the
    preferential-rate carve-out for LTCG/qualified dividends (which Form 6251
    computes at capital-gain rates); the screen therefore OVERSTATES TMT for
    filers with large preferential income — a positive result means "run Form
    6251", never "you owe this much AMT".

    All statutory figures flow from the year's `amt` block via `get_constant`
    (rates, breakpoint, exemption, phase-out start and rate). A null/TODO
    breakpoint raises `UnverifiedConstantError` rather than letting the screen
    guess a threshold (ground rule 1 / 6).
    """
    status = normalize_filing_status(filing_status)
    if status in ("married_jointly", "qualifying_surviving_spouse"):
        exemption = get_constant(amt_constants, "exemption_mfj")
        phaseout_start = get_constant(amt_constants, "phaseout_start_mfj")
        breakpoint_ = get_constant(amt_constants, "rate_28_threshold")
    elif status == "married_separately":
        exemption = get_constant(amt_constants, "exemption_mfs")
        phaseout_start = get_constant(amt_constants, "phaseout_start_mfs")
        breakpoint_ = get_constant(amt_constants, "rate_28_threshold_mfs")
    else:  # single, head_of_household — "unmarried individuals" per § 55(d)
        exemption = get_constant(amt_constants, "exemption_single")
        phaseout_start = get_constant(amt_constants, "phaseout_start_single")
        breakpoint_ = get_constant(amt_constants, "rate_28_threshold")

    rates = get_constant(amt_constants, "rates")
    rate_low = rates[0]["rate"]
    rate_high = rates[1]["rate"]
    phaseout_rate = get_constant(amt_constants, "phaseout_rate")

    amti = taxable_income + max(0.0, salt_deducted) + max(0.0, iso_bargain_element)
    exemption_left = max(
        0.0, exemption - phaseout_rate * max(0.0, amti - phaseout_start)
    )
    base = max(0.0, amti - exemption_left)
    tmt = (base * rate_low if base <= breakpoint_
           else breakpoint_ * rate_low + (base - breakpoint_) * rate_high)
    excess = round(tmt - regular_tax, 2)

    return {
        "amti_approx": round(amti, 2),
        "exemption_after_phaseout": round(exemption_left, 2),
        "amt_base": round(base, 2),
        "tentative_minimum_tax": round(tmt, 2),
        "regular_tax": round(regular_tax, 2),
        "tmt_exceeds_regular_by": max(0.0, excess),
        "form_6251_required": excess > 0,
        "ignored_items": [
            "LTCG/qualified-dividend preferential rates inside AMT (overstates TMT)",
            "Depreciation adjustments, private-activity-bond interest, NOL differences",
            "State-tax refunds excluded from AMTI (understates the gap the other way)",
            "AMT foreign tax credit and prior-year minimum tax credit (Form 8801)",
        ],
        "note": ("QUICK SCREEN ONLY — approximates Form 6251. A positive result "
                 "means Form 6251 must actually be prepared; a negative result "
                 "with large preferential income is reliable, since this screen "
                 "overstates TMT."),
    }


def calculate_california_tax(federal_agi: float, filing_status: str, state_data: Dict,
                             hsa_contributions: float = 0,
                             qbi_deduction: float = 0,
                             obbba_deductions: float = 0,
                             itemized_deductions: float = 0,
                             state_income_tax_in_itemized: float = 0,
                             dependents: int = 0,
                             age_65_plus: int = 0, blind: int = 0,
                             is_renter: bool = False) -> Dict:
    """Compute California tax from federal AGI (findings C1-C4, C7; task P2.5).

    California has its own progressive brackets and does NOT conform to several
    federal rules, so federal AGI must be adjusted on Schedule CA (540):

      + HSA contributions        CA does not allow the deduction (C2)
      + QBI deduction            CA has no section 199A (C3)
      - OBBBA-only deductions    CA's conformity date is 2015-01-01, so the
                                 tips/overtime/senior deductions do not exist
                                 for CA and must be added back (C3)

    California also taxes capital gains and qualified dividends as ORDINARY
    income — there is no preferential rate — so the whole CA taxable income
    runs through one bracket table.

    The Behavioral Health Services Tax (1% over $1,000,000 of taxable income,
    renamed from the Mental Health Services Tax for 2025) is a SEPARATE
    additive tax, not a bracket. The top bracket is 12.3%; the "13.3%" the
    plugin previously applied as a flat rate is the combined marginal rate
    above $1M and overstated CA tax by ~132% at $200,000 of MFJ income (C1).
    """
    status = normalize_filing_status(filing_status)
    nonconformity = state_data["nonconformity"]

    # --- Schedule CA (540) adjustments: federal AGI -> CA AGI ---
    adjustments = []
    ca_agi = federal_agi

    if not nonconformity["hsa_deductible"] and hsa_contributions:
        ca_agi += hsa_contributions
        adjustments.append({
            "item": "HSA contributions", "amount": round(hsa_contributions, 2),
            "direction": "add-back",
            "reason": "California does not allow a deduction for HSA contributions "
                      "(Schedule CA 540 Part I). HSA earnings are also CA-taxable.",
        })

    if not nonconformity["qbi_deductible"] and qbi_deduction:
        ca_agi += qbi_deduction
        adjustments.append({
            "item": "Qualified business income deduction", "amount": round(qbi_deduction, 2),
            "direction": "add-back",
            "reason": "California has no IRC 199A deduction.",
        })

    if not nonconformity["obbba_tips_overtime_senior"] and obbba_deductions:
        ca_agi += obbba_deductions
        adjustments.append({
            "item": "OBBBA deductions (tips / overtime / senior / car-loan interest)",
            "amount": round(obbba_deductions, 2), "direction": "add-back",
            "reason": f"California conformity date is {nonconformity['conformity_date']}, "
                      "so post-2015 federal deductions do not apply for CA.",
        })

    # --- CA deduction: standard vs itemized (a SEPARATE decision from federal) ---
    ca_standard = get_status_value(state_data["standard_deduction"], status,
                                   "California standard deduction")
    # CA itemized deductions exclude state income tax entirely.
    ca_itemized = max(0.0, itemized_deductions - state_income_tax_in_itemized)
    use_itemized = ca_itemized > ca_standard
    ca_deduction = max(ca_standard, ca_itemized)

    ca_taxable_income = max(0.0, ca_agi - ca_deduction)

    # --- Tax from the CA bracket table (all income at ordinary rates) ---
    brackets = [
        (b["top"] if b["top"] is not None else float("inf"), b["rate"])
        for b in select_for_status(state_data["brackets"], status, "California brackets")
    ]
    ca_tax, ca_breakdown = calculate_tax(ca_taxable_income, status, {status: brackets})

    # --- Behavioral Health Services Tax: separate 1% above $1M ---
    bhst_cfg = state_data["behavioral_health_tax"]
    bhst_base = max(0.0, ca_taxable_income - bhst_cfg["threshold"])
    bhst = round(bhst_base * bhst_cfg["rate"], 2)

    # --- Exemption credits (nonrefundable) ---
    ex = state_data["exemption_credit"]
    personal_count = 2 if status in ("married_jointly", "qualifying_surviving_spouse") else 1
    exemption_credit = (personal_count * ex["personal"]
                        + age_65_plus * ex["senior"]
                        + blind * ex["blind"]
                        + dependents * ex["dependent"])
    exemption_phased_out = federal_agi > ex["phaseout_federal_agi_start"]
    if exemption_phased_out:
        # The FTB worksheet reduces the credit as AGI rises; flag rather than
        # approximate, so the user is told to run the worksheet.
        exemption_note = (f"Federal AGI ${federal_agi:,.0f} exceeds "
                          f"${ex['phaseout_federal_agi_start']:,.0f}; the exemption "
                          "credit is reduced — see the Form 540 line 32 worksheet. "
                          "The full credit is shown here and is an UPPER BOUND.")
    else:
        exemption_note = "Full exemption credit applies."

    # --- Renter's credit (nonrefundable) ---
    rc = state_data["renters_credit"]
    is_joint = status in ("married_jointly", "head_of_household", "qualifying_surviving_spouse")
    rc_limit = rc["agi_limit_joint"] if is_joint else rc["agi_limit_single"]
    rc_amount = rc["amount_joint"] if is_joint else rc["amount_single"]
    renters_credit = rc_amount if (is_renter and ca_agi <= rc_limit) else 0

    credits = exemption_credit + renters_credit
    tax_after_credits = max(0.0, ca_tax + bhst - credits)

    return {
        "state": "CA",
        "federal_agi": round(federal_agi, 2),
        "ca_agi": round(ca_agi, 2),
        "adjustments": adjustments,
        "ca_standard_deduction": ca_standard,
        "ca_itemized_deductions": round(ca_itemized, 2),
        "ca_deduction_used": round(ca_deduction, 2),
        "using_itemized": use_itemized,
        "ca_taxable_income": round(ca_taxable_income, 2),
        "ca_tax_before_credits": ca_tax,
        "behavioral_health_tax": bhst,
        "exemption_credit": exemption_credit,
        "exemption_credit_note": exemption_note,
        "exemption_credit_phased_out": exemption_phased_out,
        "renters_credit": renters_credit,
        "total_credits": credits,
        "ca_tax": round(tax_after_credits, 2),
        "breakdown": ca_breakdown,
        "notes": [
            "California taxes long-term capital gains and qualified dividends as "
            "ORDINARY income — there is no preferential rate.",
            "State income tax is not deductible on the California return.",
        ],
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
    parser.add_argument("--net-capital-loss", type=float, default=0,
                        help="Net capital LOSS for the year as a positive number "
                             "(Schedule D line 16 negative). Applies the ordinary-income "
                             "offset up to the annual limit; the rest carries forward. "
                             "Mutually exclusive with --ltcg / --qualified-dividends.")
    parser.add_argument("--wages", type=float, default=None,
                        help="W-2 wages, for the Additional Medicare Tax (defaults to gross income)")
    parser.add_argument("--net-investment-income", type=float, default=None,
                        help="Net investment income for NIIT (defaults to LTCG + qualified dividends)")
    parser.add_argument("--magi", type=float, default=None,
                        help="Modified AGI for NIIT (defaults to AGI)")

    # AMT trigger inputs (flag only — no Form 6251 computation)
    parser.add_argument("--iso-bargain-element", type=float, default=0,
                        help="ISO exercise spread, for the AMT risk flag and the quick screen")
    parser.add_argument("--salt-lost-to-cap", type=float, default=0,
                        help="SALT disallowed by the cap, for the AMT risk flag")
    parser.add_argument("--salt-deducted", type=float, default=0,
                        help="SALT amount inside your itemized deduction — added back for "
                             "the AMT quick screen (the add-back is the SALT actually "
                             "deducted, i.e. min(salt_paid, cap))")
    parser.add_argument("--amt-quick-test", action="store_true",
                        help="Run the quantitative AMT screen (approximate TMT vs regular "
                             "tax); the amt_risk flag always runs regardless")

    # State layer (task P2.5)
    parser.add_argument("--state", type=str, default=None,
                        help="State of residence for a state-tax section (e.g. CA)")
    parser.add_argument("--hsa-contributions", type=float, default=0,
                        help="HSA contributions (added back for CA — CA does not conform)")
    parser.add_argument("--qbi-deduction", type=float, default=0,
                        help="Federal QBI deduction (added back for CA)")
    # --- QBI engine integration (plan G2): compute §199A instead of passing it in ---
    # The manual --qbi-deduction passthrough above stays for pre-computed values;
    # --compute-qbi calls scripts/qbi_calculator.py and feeds the result into the
    # federal taxable-income reduction AND the CA add-back automatically (one flow,
    # no double entry). Mutually exclusive with --qbi-deduction.
    parser.add_argument("--compute-qbi", action="store_true",
                        help="Compute the §199A QBI deduction via the engine "
                             "(mutually exclusive with --qbi-deduction).")
    parser.add_argument("--se-net-profit", type=float, default=0,
                        help="[--compute-qbi] Schedule C net profit before ½ SE tax.")
    parser.add_argument("--se-health-insurance", type=float, default=0,
                        help="[--compute-qbi] SE health insurance deduction.")
    parser.add_argument("--se-retirement", type=float, default=0,
                        help="[--compute-qbi] SE retirement (SEP/Solo-401k) deduction.")
    parser.add_argument("--w2-wages-paid", type=float, default=0,
                        help="[--compute-qbi] W-2 wages paid by the business.")
    parser.add_argument("--ubia", type=float, default=0,
                        help="[--compute-qbi] Unadjusted basis of qualified property.")
    parser.add_argument("--sstb", action="store_true",
                        help="[--compute-qbi] Business is a Specified Service Trade or Business.")
    parser.add_argument("--reit-dividends", type=float, default=0,
                        help="[--compute-qbi] 1099-DIV Box 5 section 199A dividends.")
    parser.add_argument("--qbi-loss-carryforward", type=float, default=0,
                        help="[--compute-qbi] Prior-year QBI loss carryforward.")
    parser.add_argument("--obbba-deductions", type=float, default=0,
                        help="Federal tips/overtime/senior/car-loan deductions (added back for CA)")
    parser.add_argument("--state-income-tax-paid", type=float, default=0,
                        help="State income tax inside itemized deductions (not deductible for CA)")
    parser.add_argument("--dependents", type=int, default=0,
                        help="Dependents, for state exemption credits")
    parser.add_argument("--renter", action="store_true",
                        help="Claim the CA nonrefundable renter's credit if eligible")

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
    if args.compute_qbi and args.qbi_deduction:
        print("Error: --compute-qbi and --qbi-deduction are mutually exclusive "
              "(compute the deduction, or supply it pre-computed — not both).")
        sys.exit(1)
    if args.compute_qbi and args.se_net_profit <= 0 and args.reit_dividends <= 0:
        print("Error: --compute-qbi requires --se-net-profit (Schedule C) and/or "
              "--reit-dividends (1099-DIV Box 5) to compute a §199A deduction.")
        sys.exit(1)
    if args.net_capital_loss < 0:
        print("Error: --net-capital-loss must be a non-negative number (enter the loss as a positive value).")
        sys.exit(1)
    if args.net_capital_loss > 0 and (args.ltcg > 0 or args.qualified_dividends > 0):
        print("Error: --net-capital-loss and --ltcg/--qualified-dividends are mutually "
              "exclusive — a year has either a net gain or a net loss after Schedule D netting.")
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
    capital_loss = None
    agi = args.gross_income - args.adjustments
    if args.net_capital_loss > 0:
        capital_loss = apply_capital_loss(
            args.net_capital_loss, args.filing_status,
            tax_data["capital_loss_limit"], tax_data["capital_loss_limit_mfs"])
        agi -= capital_loss["allowed_offset"]

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

    # --- QBI (§199A) deduction (plan G2) ---
    # QBI is a from-AGI, non-itemized deduction: it reduces taxable income but
    # NOT AGI, and it is taken AFTER the standard/itemized deduction. The
    # §199A threshold test and overall limit use taxable income BEFORE the QBI
    # deduction, so the engine consumes `taxable_income` as-is and the result
    # is subtracted here. The same computed figure is fed into the CA add-back
    # path below — one flow, no double entry.
    qbi_result = None
    qbi_deduction = 0.0
    if args.compute_qbi:
        import qbi_calculator  # lazy: breaks the circular import (see top of file)
        qbi_profile = {
            "se_net_profit": args.se_net_profit,
            "se_health_insurance": args.se_health_insurance,
            "se_retirement": args.se_retirement,
            "w2_wages_paid": args.w2_wages_paid,
            "ubia": args.ubia,
            "is_sstb": args.sstb,
            "taxable_income_before_qbi": taxable_income,
            # net capital gain for the overall limit = §1(h) LTCG + qualified
            # dividends, which the engine treats as inclusive of qualified
            # dividends per §199A(e)(3).
            "net_capital_gain": args.ltcg + args.qualified_dividends,
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
    elif args.qbi_deduction:
        # Manual passthrough: a pre-computed QBI deduction reduces taxable
        # income the same way (and is added back for CA via the path below).
        qbi_deduction = args.qbi_deduction
        taxable_income = max(0, taxable_income - qbi_deduction)

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

    # Nonrefundable credits offset only regular income tax (Form 1040 line 18);
    # NIIT and Additional Medicare Tax are added afterward at line 23 and
    # cannot be reduced by nonrefundable credits.
    nonrefundable_applied = min(nonrefundable, income_tax)
    tax_after_nonrefundable = tax_before_credits - nonrefundable_applied
    tax_after_credits = round(tax_after_nonrefundable - refundable, 2)
    refund_from_credits = round(max(0.0, -tax_after_credits), 2)

    # AMT risk flag only (F8) — no Form 6251 computation.
    amt_risk = assess_amt_risk(
        taxable_income, args.salt_lost_to_cap, args.iso_bargain_element,
        tax_data.get("amt", {}), args.filing_status,
    )

    # Optional quantitative AMT screen (plan B6) — runs only when requested.
    # The flag above still fires with or without this option. The screen needs
    # the SALT actually deducted (the AMT add-back), supplied via --salt-deducted
    # since the calculator does not otherwise track it.
    amt_screen = None
    if args.amt_quick_test:
        try:
            amt_screen = amt_quick_test(
                taxable_income, args.salt_deducted, args.iso_bargain_element,
                income_tax, args.filing_status, tax_data["amt"],
            )
        except UnverifiedConstantError as e:
            print(f"Error: {e}")
            sys.exit(1)

    # Calculate rates
    effective_rate = calculate_effective_rate(max(0.0, tax_after_credits), args.gross_income)
    marginal_rate = calculate_marginal_rate(ordinary_income, args.filing_status, tax_brackets)

    state_result = None
    if args.state:
        try:
            state_data = load_state_constants(args.state, args.year)
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)
        if state_data.get("type") == "progressive" and args.state.upper() == "CA":
            state_result = calculate_california_tax(
                federal_agi=agi, filing_status=args.filing_status, state_data=state_data,
                hsa_contributions=args.hsa_contributions,
                qbi_deduction=qbi_deduction,
                obbba_deductions=args.obbba_deductions,
                itemized_deductions=args.deductions,
                state_income_tax_in_itemized=args.state_income_tax_paid,
                dependents=args.dependents,
                age_65_plus=args.age_65_plus, blind=args.blind,
                is_renter=args.renter,
            )
        elif state_data.get("type") == "none":
            state_result = {"state": args.state.upper(), "type": "none",
                            "ca_tax": 0, "state_tax": 0,
                            "notes": [f"{args.state.upper()} has no personal income tax."]}

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
    if state_result is not None:
        result["state"] = state_result
    if qbi_result is not None:
        result["qbi"] = qbi_result.to_dict()
    if capital_loss is not None:
        result["capital_loss"] = capital_loss
    if amt_screen is not None:
        result["amt_screen"] = amt_screen

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
        if qbi_result is not None:
            print(f"  QBI deduction:        ${qbi_deduction:,.2f} "
                  f"(Form {qbi_result.form}; reduces taxable income, not AGI)")
        if capital_loss is not None:
            print(f"  Capital loss offset:  −${capital_loss['allowed_offset']:,.2f} "
                  f"(carryforward to next year: ${capital_loss['carryforward']:,.2f})")
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

        if state_result and state_result.get("state") == "CA":
            st = state_result
            print(f"\n--- California (Form 540) ---")
            print(f"Federal AGI:            ${st['federal_agi']:,.2f}")
            for adj in st["adjustments"]:
                print(f"  + {adj['item']}: ${adj['amount']:,.2f} ({adj['direction']})")
            print(f"CA AGI:                 ${st['ca_agi']:,.2f}")
            print(f"CA Deduction:           ${st['ca_deduction_used']:,.2f} "
                  f"({'Itemized' if st['using_itemized'] else 'Standard'})")
            print(f"CA Taxable Income:      ${st['ca_taxable_income']:,.2f}")
            print(f"CA Tax Before Credits:  ${st['ca_tax_before_credits']:,.2f}")
            if st["behavioral_health_tax"]:
                print(f"Behavioral Health Tax:  ${st['behavioral_health_tax']:,.2f}")
            print(f"Exemption Credit:       ${st['exemption_credit']:,.2f}")
            if st["renters_credit"]:
                print(f"Renter's Credit:        ${st['renters_credit']:,.2f}")
            print(f"CA TAX:                 ${st['ca_tax']:,.2f}")
            for n in st["notes"]:
                print(f"  note: {n}")

        if amt_risk["amt_risk"]:
            print("\n⚠️  AMT RISK FLAGGED (this tool does not compute Form 6251):")
            for reason in amt_risk["reasons"]:
                print(f"  - {reason}")

        if amt_screen is not None:
            print("\n--- AMT QUICK SCREEN (approximation — not Form 6251) ---")
            print(f"AMTI (approx):          ${amt_screen['amti_approx']:,.2f}  "
                  f"(taxable income + SALT deducted + ISO bargain element)")
            print(f"Exemption after phase-out: ${amt_screen['exemption_after_phaseout']:,.2f}")
            print(f"AMT taxable base:      ${amt_screen['amt_base']:,.2f}")
            print(f"Tentative Minimum Tax:  ${amt_screen['tentative_minimum_tax']:,.2f}  "
                  f"(26%/28% split at the year's breakpoint)")
            print(f"Regular Tax:           ${amt_screen['regular_tax']:,.2f}")
            if amt_screen["form_6251_required"]:
                print(f"TMT exceeds regular by:  ${amt_screen['tmt_exceeds_regular_by']:,.2f}  "
                      f"=> Form 6251 REQUIRED")
            else:
                print("TMT does not exceed regular tax => Form 6251 not triggered by this screen")
            print("  Items this screen IGNORES (so TMT may be overstated):")
            for item in amt_screen["ignored_items"]:
                print(f"    - {item}")
            print(f"  note: {amt_screen['note']}")


if __name__ == "__main__":
    main()
