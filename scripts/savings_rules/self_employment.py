#!/usr/bin/env python3
"""Self-employment rule family: M7 SE health insurance, M8 §179 / bonus depreciation.

Reuses `calculate_se_tax` (estimated_tax_calculator) for the SE-tax deduction,
and `tax_calculator` marginal-rate helpers — no duplicated engines.
"""

from typing import Any, Dict, List, Optional, Tuple

from .engine import (
    ca_nonconformity,
    constant,
    federal_ordinary_marginal_rate,
    filing_status_of,
)
from .models import Priority, TaxOpportunity


# ---------------------------------------------------------------------------
# M7 — Self-employed health insurance deduction
# ---------------------------------------------------------------------------


def rule_se_health_insurance(profile: Dict[str, Any],
                              tax_data: Dict[str, Any]) -> List[TaxOpportunity]:
    """M7: above-the-line SE health insurance deduction.

    Deduction = min(premiums, net SE profit − ½ SE tax), reusing the existing
    `calculate_se_tax` so the figure matches `estimated_tax_calculator.py` for
    the same profile (acceptance test A6).
    """
    se_profit = float(profile.get("se_net_profit", 0) or 0)
    premiums = float(profile.get("se_health_insurance_premiums", 0) or 0)
    if se_profit <= 0 or premiums <= 0:
        return []

    # Reuse the SAME SE-tax engine estimated_tax_calculator uses.
    from estimated_tax_calculator import calculate_se_tax
    se = calculate_se_tax(se_profit, tax_data["se_tax"])
    half_se_tax = se["deduction"]

    cap = se_profit - half_se_tax
    deduction = min(premiums, max(0.0, cap))
    if deduction <= 0:
        return []

    fs = filing_status_of(profile)
    rate = federal_ordinary_marginal_rate(tax_data, fs, profile["taxable_income"])
    savings_federal = round(deduction * rate, 2)

    return [TaxOpportunity(
        category="Self-Employment",
        name="SE Health Insurance Deduction",
        description=(f"Deduct ${deduction:,.0f} of health insurance premiums above the "
                    f"line (capped at net SE profit ${se_profit:,.0f} − ½ SE tax "
                    f"${half_se_tax:,.0f} = ${cap:,.0f})."),
        potential_savings=f"~${savings_federal:,.0f} federal",
        priority=Priority.MEDIUM,
        action_required=("Claim the self-employed health insurance deduction on "
                         "Schedule 1 (line 17); it does NOT reduce SE tax itself."),
        documentation_needed=["Form 1040 Schedule 1", "Premium statements"],
        rule_id="se_health_insurance",
        savings_federal=savings_federal,
        savings_state=None,
        state_caveat=None,
        deadline="By the tax filing deadline (claimed on the return).",
        assumptions=[f"Marginal rate {rate:.1%} on the profile's taxable income.",
                    "The deduction is above-the-line and does not reduce SE tax.",
                    "Premiums above the cap are not deductible here."],
        deduction_amount=deduction,
    )]


# ---------------------------------------------------------------------------
# M8 — §179 / bonus depreciation
# ---------------------------------------------------------------------------


def _bonus_rate(tax_data: Dict[str, Any], in_service_date: Optional[str]
                ) -> Tuple[Optional[float], Optional[float], Optional[str], List[str]]:
    """Resolve the bonus-depreciation rate for the year.

    For 2025 the OBBBA created a cutover (acquired after Jan 19, 2025 → 100%);
    other years carry a single rate. Returns (rate, alt_rate, note, asks) where
    `rate` is the chosen/applicable rate, `alt_rate` is the other rate when a
    date is needed to decide, `note` explains the cutover, and `asks` lists
    any ask-for-date prompts.
    """
    block = tax_data.get("bonus_depreciation_rate")
    asks: List[str] = []
    if block is None:
        return None, None, None, asks

    # Two-rate cutover shape (2025 OBBBA): {rate_pre_cutoff, rate_post_cutoff,
    # cutoff_acquired_after}. Single-rate shape: {rate: ...}.
    if isinstance(block, dict) and "rate_pre_cutoff" in block:
        pre = block["rate_pre_cutoff"]
        post = block["rate_post_cutoff"]
        cutoff = block.get("cutoff_acquired_after")
        if in_service_date is None:
            asks.append(
                "Equipment in-service date is missing. 2025 bonus depreciation "
                "depends on the ACQUISITION date (OBBBA cutover): property acquired "
                f"AFTER {cutoff} qualifies for {post:.0%}; acquired on/before "
                f"{cutoff} for {pre:.0%}. Provide equipment_in_service_date to "
                "select a single rate."
            )
            return pre, post, (f"OBBBA cutover at {cutoff}: {pre:.0%} before, "
                              f"{post:.0%} after."), asks
        # acquired_after: strictly after the cutoff date
        acquired_after = in_service_date > str(cutoff)
        chosen = post if acquired_after else pre
        return chosen, None, (f"Acquired {'after' if acquired_after else 'on/before'} "
                              f"{cutoff} → {chosen:.0%} bonus."), asks

    rate = block.get("rate") if isinstance(block, dict) else block
    return rate, None, None, asks


def rule_section179_bonus(profile: Dict[str, Any],
                          tax_data: Dict[str, Any]) -> List[TaxOpportunity]:
    """M8: §179 expensing and bonus depreciation for equipment purchases."""
    se_profit = float(profile.get("se_net_profit", 0) or 0)
    purchases = float(profile.get("business_equipment_purchases", 0) or 0)
    if se_profit <= 0 or purchases <= 0:
        return []

    fs = filing_status_of(profile)
    rate = federal_ordinary_marginal_rate(tax_data, fs, profile["taxable_income"])

    s179 = constant(tax_data, "section_179")
    s179_limit = s179["limit"]
    s179_phaseout = s179["phaseout_start"]

    # IRC §179(b)(4)(B): MFS spouses must split the §179 DOLLAR LIMITATION
    # (paragraph (1)) 50/50 absent a different election. This profile only
    # sees one spouse's purchases, so the phase-out threshold (paragraph (2))
    # is left as the full unsplit figure here rather than guessed at — §179
    # (b)(4)(A) treats the couple as one taxpayer for the subsection as a
    # whole, which implies the phase-out is measured against COMBINED
    # household purchases, not a threshold apportioned per spouse. Verify
    # against the statute if combined-spouse purchase tracking is added.
    mfs_note = None
    if fs == "married_separately":
        s179_limit = s179_limit / 2
        mfs_note = ("Married filing separately: §179 dollar limitation split "
                    "50/50 between spouses per IRC §179(b)(4)(B) (assumes no "
                    "alternate apportionment election).")

    # §179 deduction: up to the limit, reduced dollar-for-dollar by purchases
    # above the phase-out threshold.
    s179_deduction = min(purchases, s179_limit)
    phaseout_note = None
    if purchases > s179_phaseout:
        reduction = purchases - s179_phaseout
        s179_deduction = max(0.0, s179_deduction - reduction)
        phaseout_note = (
            f"§179 phase-out: purchases ${purchases:,.0f} exceed the "
            f"${s179_phaseout:,.0f} threshold, reducing the limit by "
            f"${reduction:,.0f} to an effective ${s179_deduction:,.0f}."
        )

    bonus_rate, alt_rate, bonus_note, asks = _bonus_rate(
        tax_data, profile.get("equipment_in_service_date"))

    # CA caveat: CA does not conform to bonus depreciation.
    state_caveat = None
    savings_state = None
    if str(profile.get("state", "")).upper() == "CA":
        nc = ca_nonconformity(tax_data)
        if not nc.get("bonus_depreciation", True):
            state_caveat = ("California does not conform to bonus depreciation: "
                            "no CA benefit from the bonus rate; state savings $0.")
            savings_state = 0.0

    savings_federal = round(s179_deduction * rate, 2)
    assumptions = [f"§179 limit ${s179_limit:,.0f}; phase-out starts at ${s179_phaseout:,.0f}.",
                  f"Marginal rate {rate:.1%} on the profile's taxable income."]
    if mfs_note:
        assumptions.append(mfs_note)
    if phaseout_note:
        assumptions.append(phaseout_note)
    if bonus_note:
        assumptions.append(f"Bonus depreciation: {bonus_note}")
    if bonus_rate is not None:
        assumptions.append(f"Bonus rate {bonus_rate:.0%} available for {purchases:,.0f} of "
                          "purchases (§179 and bonus are alternatives; consult a CPA on "
                          "stacking rules).")
    assumptions.extend(asks)

    rate_display = (f"{bonus_rate:.0%}" if bonus_rate is not None and alt_rate is None
                    else (f"{alt_rate:.0%} or {bonus_rate:.0%} (date-dependent)"
                          if alt_rate is not None else "see note"))

    return [TaxOpportunity(
        category="Self-Employment",
        name="Section 179 / Bonus Depreciation",
        description=(f"Expense ${s179_deduction:,.0f} of ${purchases:,.0f} in equipment "
                    f"under §179 (limit ${s179_limit:,.0f}); bonus depreciation at "
                    f"{rate_display} is also available."),
        potential_savings=(f"~${savings_federal:,.0f} federal (§179)"
                          + (" + $0 CA (bonus nonconformity)" if savings_state == 0.0 else "")),
        priority=Priority.MEDIUM,
        action_required=("Elect §179 on Form 4562 for the expensed amount; apply bonus "
                         "depreciation to the remainder per the year's rate."),
        documentation_needed=["Form 4562", "Purchase invoices", "In-service date records"],
        rule_id="section179_bonus",
        savings_federal=savings_federal,
        savings_state=savings_state,
        state_caveat=state_caveat,
        deadline="Asset must be placed in service by Dec 31 of the tax year.",
        assumptions=assumptions,
        deduction_amount=s179_deduction,
    )]


# ---------------------------------------------------------------------------
# M12 — Home-office deduction (simplified vs actual-expense method)
# ---------------------------------------------------------------------------


def rule_home_office(profile: Dict[str, Any],
                     tax_data: Dict[str, Any]) -> List[TaxOpportunity]:
    """M12: compare the simplified method (rate x capped sq ft, Rev. Proc.
    2013-13) against the actual-expense method (business-use % x actual home
    expenses, Form 8829) and recommend the larger. Both are limited to
    Schedule C net income (IRC 280A(c)(5)); the simplified method has no
    depreciation recapture and no expense carryover, the actual method has both.
    """
    se_profit = float(profile.get("se_net_profit", 0) or 0)
    sqft = float(profile.get("home_office_sqft", 0) or 0)
    total_sqft = float(profile.get("home_total_sqft", 0) or 0)
    actual_expenses = float(profile.get("home_office_actual_expenses", 0) or 0)
    if se_profit <= 0 or sqft <= 0:
        return []

    rate = constant(tax_data, "home_office_simplified_rate")
    max_sqft = constant(tax_data, "home_office_max_sqft")
    simplified = min(sqft, max_sqft) * rate

    business_pct = (sqft / total_sqft) if total_sqft > 0 else 0.0
    actual = business_pct * actual_expenses

    method = "actual-expense (Form 8829)" if actual > simplified else "simplified"
    deduction = min(max(simplified, actual), se_profit)  # 280A(c)(5) income limit

    fs = filing_status_of(profile)
    marginal = federal_ordinary_marginal_rate(tax_data, fs, profile["taxable_income"])
    # Home-office is a Schedule C deduction: it reduces income AND SE tax.
    from estimated_tax_calculator import calculate_se_tax
    se_before = calculate_se_tax(se_profit, tax_data["se_tax"])["total"]
    se_after = calculate_se_tax(max(0.0, se_profit - deduction), tax_data["se_tax"])["total"]
    savings_federal = round(deduction * marginal + (se_before - se_after), 2)

    assumptions = [
        f"Simplified: {min(sqft, max_sqft):,.0f} sq ft x ${rate:.2f} = ${simplified:,.0f} "
        f"(cap {max_sqft:,.0f} sq ft); actual: {business_pct:.1%} x "
        f"${actual_expenses:,.0f} = ${actual:,.0f}.",
        "Both methods are limited to Schedule C net income (IRC 280A(c)(5)).",
        "Actual-expense method includes depreciation, which is RECAPTURED as "
        "ordinary income on a later home sale; the simplified method is not.",
        "Space must be used regularly and EXCLUSIVELY for the business.",
    ]
    return [TaxOpportunity(
        category="Self-Employment",
        name="Home Office Deduction",
        description=(f"Deduct ${deduction:,.0f} for the home office using the "
                    f"{method} method."),
        potential_savings=f"~${savings_federal:,.0f} federal (income + SE tax)",
        priority=Priority.MEDIUM,
        action_required=("Simplified: enter on Schedule C line 30 directly. "
                         "Actual: file Form 8829."),
        documentation_needed=["Square-footage measurement", "Form 8829 (actual method)",
                              "Home expense records (actual method)"],
        rule_id="home_office",
        savings_federal=savings_federal,
        savings_state=None,
        state_caveat=None,
        deadline="By the tax filing deadline (claimed on Schedule C).",
        assumptions=assumptions,
        deduction_amount=round(deduction, 2),
    )]