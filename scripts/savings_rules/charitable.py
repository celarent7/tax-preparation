#!/usr/bin/env python3
"""Charitable rule family: M5 appreciated-stock donation / DAF, M6 QCD.

Marginal rates reuse the shared engine helpers. AGI limits and the QCD cap
come from constants (verified primary-source figures).
"""

from typing import Any, Dict, List

from .engine import (
    ca_marginal_rate,
    ca_nonconformity,
    constant,
    federal_ltcg_marginal_rate,
    federal_ordinary_marginal_rate,
    filing_status_of,
    niit_on_gain,
)
from .models import Priority, TaxOpportunity


def _marginal_rate(tax_data, profile):
    return federal_ordinary_marginal_rate(
        tax_data, filing_status_of(profile), profile["taxable_income"])


# ---------------------------------------------------------------------------
# M5 — Appreciated-stock donation / DAF
# ---------------------------------------------------------------------------


def rule_appreciated_stock_donation(profile: Dict[str, Any],
                                    tax_data: Dict[str, Any]) -> List[TaxOpportunity]:
    """M5: donate appreciated long-term stock instead of cash.

    Compares the cash route (60%-AGI deduction) to the stock route (30%-AGI
    deduction of FMV PLUS the avoided LTCG/NIIT/CA tax on the embedded gain),
    recommends the larger combined benefit, and surfaces bunching when the
    taxpayer won't itemize even with the gift.
    """
    cash_gift = float(profile.get("planned_cash_donations", 0) or 0)
    stock_fmv = float(profile.get("appreciated_stock_value", 0) or 0)
    stock_basis = float(profile.get("appreciated_stock_basis", 0) or 0)
    held_long = profile.get("held_over_one_year", True)

    if cash_gift <= 0:
        return []
    if stock_fmv <= stock_basis:
        return []

    fs = filing_status_of(profile)
    agi = profile["agi"]
    marginal_rate = _marginal_rate(tax_data, profile)
    cash_limit_rate = constant(tax_data, "credits", "charitable_cash_limit")
    property_limit_rate = constant(tax_data, "charitable_property_agi_limit")

    # --- Will the taxpayer itemize even with this gift? ---
    from tax_calculator import calculate_standard_deduction
    std = calculate_standard_deduction(
        fs, tax_data["standard_deductions"], tax_data["additional_deduction_65_blind"])
    itemized_total = float(profile.get("itemized_total", 0) or 0)
    wont_itemize = (itemized_total + max(cash_gift, stock_fmv)) <= std

    gain = stock_fmv - stock_basis
    ltcg_rate = federal_ltcg_marginal_rate(tax_data, fs, profile["taxable_income"])
    avoided_niit = niit_on_gain(tax_data, fs, profile["magi"], gain)

    # CA avoided gain (CA taxes the gain as ordinary income at the marginal CA rate).
    avoided_ca = 0.0
    state_caveat = None
    if str(profile.get("state", "")).upper() == "CA":
        nc = ca_nonconformity(tax_data)
        if not nc.get("capital_gains_preferential", True):
            ca_rate = ca_marginal_rate(tax_data, str(tax_data.get("tax_year", "2025")),
                                       fs, agi) or 0.0
            avoided_ca = gain * ca_rate

    # Cash route: deduction capped at 60% AGI.
    cash_deduction = min(cash_gift, cash_limit_rate * agi)
    cash_benefit = cash_deduction * marginal_rate

    # Stock route: FMV deduction capped at 30% AGI, PLUS avoided gain tax.
    if not held_long:
        # Short-term holding: stock donation is limited to BASIS (not FMV), and
        # there is no avoided gain. The cash route then dominates in most cases.
        stock_deduction = min(stock_basis, property_limit_rate * agi)
        avoided_gain_tax = 0.0
        basis_warning = ("Held one year or less: the stock-donation deduction is "
                         "limited to BASIS ($%s), not fair market value." % f"{stock_basis:,.0f}")
    else:
        stock_deduction = min(stock_fmv, property_limit_rate * agi)
        avoided_gain_tax = gain * ltcg_rate + avoided_niit + avoided_ca
        basis_warning = None

    stock_benefit = stock_deduction * marginal_rate + avoided_gain_tax
    delta = stock_benefit - cash_benefit

    assumptions = [
        f"Cash route: deduction ${cash_deduction:,.0f} (60%-AGI cap), benefit ${cash_benefit:,.0f}.",
        f"Stock route: FMV deduction ${stock_deduction:,.0f} (30%-AGI cap) × {marginal_rate:.1%} "
        f"+ avoided gain tax ${avoided_gain_tax:,.0f} = ${stock_benefit:,.0f}.",
        f"Marginal rate {marginal_rate:.1%}; LTCG rate {ltcg_rate:.0%}; avoided NIIT ${avoided_niit:,.0f}.",
    ]
    if basis_warning:
        assumptions.append(basis_warning)

    if wont_itemize:
        # The gift yields no current deduction; surface bunching instead.
        return [TaxOpportunity(
            category="Charitable",
            name="Charitable Bunching (won't itemize)",
            description=(f"Your itemized deductions (${itemized_total:,.0f}) plus the "
                         f"planned gift stay at or below the ${std:,.0f} standard "
                         "deduction, so the gift gives no current-year deduction."),
            potential_savings="No current-year deduction; bunch multi-year gifts into one year.",
            priority=Priority.MEDIUM,
            action_required=("Bunch two or more years of donations into a single year "
                             "to exceed the standard deduction; use a DAF to time grants."),
            documentation_needed=["Donation receipts", "DAF agreement if used"],
            rule_id="charitable_bunching",
            savings_federal=0.0,
            savings_state=0.0,
            state_caveat=state_caveat,
            deadline="By Dec 31 to count in the current tax year.",
            assumptions=assumptions,
        )]

    recommend_stock = delta > 0
    name = "Donate Appreciated Stock (instead of cash)" if recommend_stock else "Donate Cash (stock route not better)"
    priority = Priority.HIGH if recommend_stock else Priority.LOW
    savings_federal = round(abs(delta), 2) if recommend_stock else 0.0

    return [TaxOpportunity(
        category="Charitable",
        name=name,
        description=(f"Donating ${stock_fmv:,.0f} of appreciated stock "
                     f"({'long-term' if held_long else 'short-term'}) "
                     f"{'beats' if recommend_stock else 'does not beat'} donating "
                     f"${cash_gift:,.0f} cash by ~${abs(delta):,.0f}."),
        potential_savings=(f"~${abs(delta):,.0f} extra benefit vs cash"
                          + (f" + ${avoided_ca:,.0f} CA avoided-gain" if avoided_ca else "")),
        priority=priority,
        action_required=("Transfer the appreciated shares directly to the charity (or a "
                         "DAF if bunching); do not sell then donate cash." if recommend_stock
                        else "Donate cash unless the stock's embedded gain is large."),
        documentation_needed=["Brokerage transfer confirmation", "Charity acknowledgment letter"],
        rule_id="appreciated_stock_donation",
        savings_federal=savings_federal,
        savings_state=round(avoided_ca, 2) if avoided_ca else None,
        state_caveat=state_caveat,
        deadline="By Dec 31 to count in the current tax year.",
        assumptions=assumptions,
    )]


# ---------------------------------------------------------------------------
# M6 — Qualified Charitable Distribution (age 70½+)
# ---------------------------------------------------------------------------


def rule_qcd(profile: Dict[str, Any], tax_data: Dict[str, Any]) -> List[TaxOpportunity]:
    """M6: direct IRA-to-charity transfer for those 70½+, excluding the
    distribution from AGI and satisfying the RMD.
    """
    age = profile.get("age", 0)
    if age < 70.5:
        return []

    qcd_limit = constant(tax_data, "qcd_limit")
    fs = filing_status_of(profile)
    marginal_rate = federal_ordinary_marginal_rate(tax_data, fs, profile["taxable_income"])

    savings_federal = round(qcd_limit * marginal_rate, 2)

    return [TaxOpportunity(
        category="Charitable",
        name="Qualified Charitable Distribution (QCD)",
        description=(f"At age {age} you can direct up to ${qcd_limit:,.0f} from your IRA "
                     "to charity; the distribution is excluded from AGI and counts "
                     "toward your RMD."),
        potential_savings=(f"~${savings_federal:,.0f} by reducing AGI (vs an itemized "
                          "deduction that may be worthless under the standard deduction)."),
        priority=Priority.HIGH,
        action_required=("Instruct your IRA custodian to make a direct trustee-to-"
                         "charity transfer of up to ${:,.0f}.".format(qcd_limit)),
        documentation_needed=["Custodian QCD confirmation", "Charity acknowledgment"],
        rule_id="qcd",
        savings_federal=savings_federal,
        savings_state=None,
        state_caveat=None,
        deadline="By Dec 31 (must be received by the charity in the tax year).",
        assumptions=["Beats an itemized donation for standard-deduction takers because "
                     "the QCD reduces AGI directly rather than being itemized.",
                    f"Per-person annual cap ${qcd_limit:,.0f} (indexed since 2024)."],
    )]