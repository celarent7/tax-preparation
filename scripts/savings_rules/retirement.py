#!/usr/bin/env python3
"""Retirement rule family: M1 401(k) headroom, M2 backdoor Roth, M3 mega backdoor.

See docs/savings_engine_plan.md §2 (M1-M3). Marginal-rate math reuses the shared
engine helpers (no bracket walking here).
"""

from typing import Any, Dict, List

from .engine import (
    ca_marginal_rate,
    ca_nonconformity,
    constant,
    federal_ordinary_marginal_rate,
    filing_status_of,
)
from .models import Priority, TaxOpportunity
from utils import ca_529_nonqualified_withdrawal


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _ira_limit_for_age(tax_data: Dict[str, Any], age: int) -> float:
    limit = constant(tax_data, "retirement_limits", "ira", "limit")
    if age >= 50:
        limit += constant(tax_data, "retirement_limits", "ira", "catchup_50_plus")
    return limit


def _k401_limit_for_age(tax_data: Dict[str, Any], age: int) -> float:
    """Elective deferral limit for the person's age (M1).

    Ages 60-63 get the SECURE 2.0 super catch-up; 50+ get the standard catch-up;
    otherwise the base limit. All figures come from constants, never literals.
    """
    limit = constant(tax_data, "retirement_limits", "401k", "limit")
    if 60 <= age <= 63:
        # SECURE 2.0 super catch-up (ages 60-63) only exists from 2025. For years
        # where the provision is null/absent (it wasn't in effect), treat as 0.
        super_catchup = constant(tax_data, "retirement_limits", "401k",
                                 "super_catchup_60_63", allow_none=True, default=0)
        limit += (super_catchup or 0)
    elif age >= 50:
        limit += constant(tax_data, "retirement_limits", "401k", "catchup_50_plus")
    return limit


def _state_rate_if_ca(tax_data: Dict[str, Any], profile: Dict[str, Any],
                      taxable_income: float) -> float:
    """CA marginal rate when the profile is CA; 0 otherwise.

    CA *conforms* to 401(k) deferrals, so there is no caveat here — only a
    state savings figure.
    """
    if str(profile.get("state", "")).upper() != "CA":
        return 0.0
    # CA taxable income proxy: AGI less the CA standard deduction. The bracket
    # marginal rate is what matters for a per-dollar savings estimate.
    ca_taxable = max(0.0, profile["agi"])  # standard deduction folded into the bracket floor
    rate = ca_marginal_rate(tax_data, str(tax_data["tax_year"]),
                            filing_status_of(profile), ca_taxable)
    return rate or 0.0


# ---------------------------------------------------------------------------
# M1 — 401(k) headroom & super catch-up
# ---------------------------------------------------------------------------


def _m1_for_person(tax_data, profile, age, contributed, label) -> List[TaxOpportunity]:
    if contributed is None:
        contributed = 0
    limit = _k401_limit_for_age(tax_data, age)
    headroom = limit - float(contributed)
    if headroom <= 0:
        return []

    fed_rate = federal_ordinary_marginal_rate(
        tax_data, filing_status_of(profile), profile["taxable_income"])
    state_rate = _state_rate_if_ca(tax_data, profile, profile["taxable_income"])
    savings_federal = round(headroom * fed_rate, 2)
    savings_state = round(headroom * state_rate, 2) if state_rate else 0.0

    notes = []
    # SECURE 2.0 Roth catch-up mandate (effective 2026+): high-earning catch-up
    # contributors must make catch-up contributions as Roth (no current-year
    # deduction on the catch-up slice). Informational, not a savings claim.
    if age >= 50 and str(tax_data["tax_year"]) >= "2026" and profile.get("wages", 0) > 0:
        notes.append(
            "SECURE 2.0 Roth catch-up mandate: for 2026+, if your prior-year FICA wages "
            "exceeded the SECURE 2.0 threshold (check with your plan administrator), your "
            "catch-up contribution must be made as Roth — that slice gives no current-year "
            "deduction. The headroom figure above still applies to the pre-tax base limit."
        )

    who = "" if not label else f" ({label})"
    return [TaxOpportunity(
        category="Retirement",
        name=f"401(k) Contribution Headroom{who}",
        description=(f"You can defer ${headroom:,.0f} more into your 401(k) at age {age} "
                     f"(limit ${limit:,.0f} for that age, contributed ${float(contributed):,.0f})."),
        potential_savings=(f"~${savings_federal:,.0f} federal"
                          + (f" + ${savings_state:,.0f} CA" if savings_state else "")),
        priority=Priority.HIGH,
        action_required=(f"Increase 401(k) elective deferrals by ${headroom:,.0f} "
                         f"before the plan year ends."),
        documentation_needed=["Pay stub / plan contribution summary", "Plan document for catch-up rules"],
        rule_id="k401_headroom",
        savings_federal=savings_federal,
        savings_state=savings_state if savings_state else None,
        state_caveat=None,  # CA conforms to 401(k) deferrals — no caveat
        deadline="By the plan year-end (Dec 31, unless the plan allows last-day rules).",
        assumptions=(["Marginal rate computed on the profile's taxable income.",
                      "Employer match not counted against the elective-deferral limit."]
                     + notes),
    )]


def rule_401k_headroom(profile: Dict[str, Any], tax_data: Dict[str, Any]) -> List[TaxOpportunity]:
    """M1: 401(k) elective-deferral headroom, computed per person (MFJ spouse)."""
    ops: List[TaxOpportunity] = []
    age = profile.get("age", 40)
    ops.extend(_m1_for_person(tax_data, profile, age,
                              profile.get("employee_401k_contributions", 0), ""))
    if (filing_status_of(profile) == "married_jointly"
            and profile.get("spouse_age") is not None):
        ops.extend(_m1_for_person(tax_data, profile, profile["spouse_age"],
                                 profile.get("spouse_employee_401k_contributions", 0),
                                 "spouse"))
    return ops


# ---------------------------------------------------------------------------
# M2 — Backdoor Roth IRA (with pro-rata warning)
# ---------------------------------------------------------------------------


def rule_backdoor_roth(profile: Dict[str, Any], tax_data: Dict[str, Any]) -> List[TaxOpportunity]:
    """M2: nondeductible Trad IRA contribution + Form 8606 + Roth conversion.

    Only fires when the taxpayer is above the Roth MAGI phase-out START (below
    the start, a direct Roth contribution is better and the backdoor isn't
    needed). The pro-rata warning is mandatory when a pre-tax Trad IRA balance
    exists.
    """
    fs = filing_status_of(profile)
    phaseout = constant(tax_data, "ira", "roth_magi_phaseout", fs)
    start = phaseout["start"]

    magi = profile["magi"]
    if magi < start:
        # Below the phase-out: a direct Roth contribution works; the backdoor
        # mechanic is unnecessary, so we do not recommend it.
        return []

    ira_limit = _ira_limit_for_age(tax_data, profile.get("age", 40))
    already = (profile.get("ira_contributions", 0)
               + profile.get("roth_ira_contributions", 0))
    if already >= ira_limit:
        return []

    pretax = profile.get("traditional_ira_pretax_balance", 0) or 0
    contribution = ira_limit - already

    assumptions = [
        "A backdoor Roth is tax-free growth, not a current-year deduction; "
        "no federal savings dollar is claimed.",
        "Step 1: make a nondeductible contribution to a Traditional IRA.",
        "Step 2: file Form 8606 to track the nondeductible basis.",
        "Step 3: convert the Trad IRA to a Roth IRA (ideally the full amount).",
    ]
    caveat = None
    priority = Priority.HIGH

    if pretax > 0:
        # Pro-rata rule (IRC 408(d)(2)/(8)): the taxable fraction of ANY
        # conversion is driven by the ratio of pre-tax balance to total IRA
        # balance. Mandatory warning; may not recommend without it.
        denom = pretax + contribution
        taxable_frac = pretax / denom if denom else 0.0
        caveat = (
            f"Pro-rata rule applies: you have a pre-tax Trad IRA balance of "
            f"${pretax:,.0f}. A ${contribution:,.0f} conversion would be "
            f"{taxable_frac:.0%} taxable ({pretax:.0f}/{denom:.0f}). "
            f"Mitigation: roll the pre-tax balance into a 401(k) first, then "
            f"convert the after-tax remainder tax-free."
        )
        priority = Priority.HIGH  # keep HIGH but the warning dominates the message

    return [TaxOpportunity(
        category="Retirement",
        name="Backdoor Roth IRA",
        description=(f"Your MAGI (${magi:,.0f}) is at or above the Roth phase-out "
                     f"start (${start:,.0f} for {fs}); a direct Roth contribution is "
                     "limited, but a backdoor Roth can still fund Roth space."),
        potential_savings="Tax-free growth (no current-year deduction claimed).",
        priority=priority,
        action_required=(f"Contribute ${contribution:,.0f} nondeductible to a Trad IRA, "
                         "file Form 8606, then convert to Roth."),
        documentation_needed=["Form 8606 (nondeductible basis)", "Roth conversion confirmation"],
        rule_id="backdoor_roth",
        savings_federal=None,
        savings_state=None,
        state_caveat=caveat,
        deadline="Contribute by the tax filing deadline; convert promptly to limit pro-rata exposure.",
        assumptions=assumptions,
    )]


# ---------------------------------------------------------------------------
# M3 — Mega Backdoor Roth
# ---------------------------------------------------------------------------


def rule_mega_backdoor_roth(profile: Dict[str, Any], tax_data: Dict[str, Any]) -> List[TaxOpportunity]:
    """M3: after-tax 401(k) contributions up to the 415(c) total-additions cap."""
    if not profile.get("plan_allows_after_tax_401k", False):
        return []

    total_415c = constant(tax_data, "retirement_limits", "401k", "total_415c")
    used = (float(profile.get("employee_401k_contributions", 0) or 0)
           + float(profile.get("employer_401k_contributions", 0) or 0)
           + float(profile.get("after_tax_401k_contributions", 0) or 0))
    room = total_415c - used
    if room <= 0:
        return []

    in_service = profile.get("plan_allows_in_service_rollover", False)
    priority = Priority.MEDIUM
    caveat = None
    if not in_service:
        priority = Priority.LOW
        caveat = ("Your plan does not allow in-service rollover, so after-tax dollars "
                  "are stranded in the 401(k) until separation from service; the Roth "
                  "conversion (and its tax-free growth) is deferred until then.")

    return [TaxOpportunity(
        category="Retirement",
        name="Mega Backdoor Roth",
        description=(f"Your plan allows after-tax 401(k) contributions. You have "
                     f"${room:,.0f} of room left under the §415(c) total-additions cap "
                     f"(${total_415c:,.0f})."),
        potential_savings="Tax-free growth on after-tax contributions rolled to Roth.",
        priority=priority,
        action_required=(f"Contribute up to ${room:,.0f} after-tax, then roll to Roth "
                         "(in-service if the plan allows, else at separation)."),
        documentation_needed=["Plan document (after-tax + in-service rollover provisions)",
                              "Roth conversion records"],
        rule_id="mega_backdoor_roth",
        savings_federal=None,
        savings_state=None,
        state_caveat=caveat,
        deadline="By plan year-end (contributions); rollover per plan terms.",
        assumptions=["The 415(c) room is total cap minus all employee + employer + after-tax additions.",
                    "Employer true-up / non-elective contributions counted in the total."],
    )]


# ---------------------------------------------------------------------------
# M9 — HSA (upgraded: federal/state split + mandatory CA caveat)
# ---------------------------------------------------------------------------


def rule_hsa(profile: Dict[str, Any], tax_data: Dict[str, Any]) -> List[TaxOpportunity]:
    """M9: HSA contribution headroom, split into federal and state savings.

    CA does NOT conform: HSA contributions are added back on Schedule CA and
    HSA earnings are CA-taxable, so state savings are $0 with a mandatory
    caveat (constants: hsa_deductible=false, hsa_earnings_taxable=true).
    """
    if not profile.get("has_hdhp", False):
        return []

    hsa = constant(tax_data, "retirement_limits", "hsa")
    coverage = profile.get("hsa_coverage", "single")
    limit = hsa["family"] if coverage == "family" else hsa["single"]
    age = profile.get("age", 40)
    if age >= 55:
        limit += hsa["catchup_55_plus"]

    contributed = float(profile.get("hsa_contributions", 0) or 0)
    headroom = limit - contributed
    if headroom <= 0:
        return []

    fed_rate = federal_ordinary_marginal_rate(
        tax_data, filing_status_of(profile), profile["taxable_income"])
    savings_federal = round(headroom * fed_rate, 2)

    state_caveat = None
    savings_state = None
    if str(profile.get("state", "")).upper() == "CA":
        nc = ca_nonconformity(tax_data)
        deductible = nc.get("hsa_deductible", False)
        earnings_taxable = nc.get("hsa_earnings_taxable", False)
        if not deductible:
            savings_state = 0.0
            state_caveat = (
                "California does not conform: HSA contributions are added back on "
                "Schedule CA (540) — they are not deductible for CA. HSA earnings "
                f"are{' also' if earnings_taxable else ''} CA-taxable annually. "
                "Federal savings above still apply; state savings are $0."
            )

    return [TaxOpportunity(
        category="Retirement",
        name="HSA Contribution",
        description=(f"You can contribute ${headroom:,.0f} more to your HSA "
                     f"({coverage} coverage, limit ${limit:,.0f})."),
        potential_savings=(f"~${savings_federal:,.0f} federal"
                          + (f" + ${savings_state:,.0f} CA" if savings_state else "")),
        priority=Priority.HIGH,
        action_required=(f"Contribute ${headroom:,.0f} to your HSA before the tax "
                         "filing deadline."),
        documentation_needed=["HSA contribution receipts", "HDHP coverage documentation"],
        rule_id="hsa",
        savings_federal=savings_federal,
        savings_state=savings_state,
        state_caveat=state_caveat,
        deadline="By the tax filing deadline (April 15 of the following year).",
        assumptions=["Triple-tax-advantaged: deductible in, tax-free growth, tax-free medical out.",
                    "Marginal rate computed on the profile's taxable income."],
    )]


# ---------------------------------------------------------------------------
# C4 — 529 K-12 CA non-conformity caveat (zero-savings; quantifies exposure)
# ---------------------------------------------------------------------------


def rule_529_ca_caveat(profile: Dict[str, Any],
                       tax_data: Dict[str, Any]) -> List[TaxOpportunity]:
    """CA caveat for federally-qualified K-12 529 withdrawals.

    K-12 tuition paid from a 529 is federally qualified (TCJA) but NON-qualified
    for California: the earnings portion is includable in CA income (Schedule CA
    (540) line 8z) and carries a 2.5% CA additional tax (Form FTB 3805P). CA also
    offers NO state deduction for ScholarShare contributions. This rule emits a
    zero-savings opportunity (savings_federal=None) that surfaces the cost and
    recommends paying K-12 tuition from taxable funds instead.

    Fires only when the profile is CA and ``plan_529_k12_withdrawals`` > 0. The
    2.5% rate and the non-qualified classification come from the year's
    ``plan_529`` constants block; when ``plan_529_k12_earnings`` is supplied the
    exposure is quantified via ``utils.ca_529_nonqualified_withdrawal``. Years
    without a ``plan_529`` sub-block (2023/2024 — 529 coverage is 2025-only per
    plan C4) still get the caveat but without a dollar quantification, since the
    2.5% rate must not be hard-coded (ground rule 5).
    """
    if str(profile.get("state", "")).upper() != "CA":
        return []
    withdrawal = float(profile.get("plan_529_k12_withdrawals", 0) or 0)
    if withdrawal <= 0:
        return []
    earnings = float(profile.get("plan_529_k12_earnings", 0) or 0)

    plan_529 = ca_nonconformity(tax_data).get("plan_529")
    caveat = (
        "California does NOT conform to the federal K-12 expansion of IRC 529 "
        "'qualified' expenses: the EARNINGS portion of a K-12 529 withdrawal is "
        "includable in CA income (Schedule CA (540) line 8z) and subject to a CA "
        "additional tax (Form FTB 3805P). California also offers NO state income "
        "tax deduction for ScholarShare 529 contributions. Consider paying K-12 "
        "tuition from taxable funds instead."
    )
    if plan_529 is not None and earnings > 0:
        r = ca_529_nonqualified_withdrawal(withdrawal, earnings, plan_529)
        caveat += (f" At ${withdrawal:,.0f} withdrawn with ${earnings:,.0f} of "
                   f"earnings, the CA exposure is ${r['ca_taxable_earnings']:,.2f} "
                   f"of added income plus ${r['additional_tax']:,.2f} of "
                   f"additional tax ({r['rate']:.1%} x earnings).")

    return [TaxOpportunity(
        category="Retirement",
        name="529 K-12 CA non-conformity",
        description=(f"You reported ${withdrawal:,.0f} of K-12 529 withdrawals. "
                     "These are federally qualified but CA-NON-qualified — the "
                     "earnings are CA-taxable."),
        potential_savings="$0 (a cost to avoid, not a savings)",
        priority=Priority.MEDIUM,
        action_required=("Pay K-12 tuition from taxable funds rather than a 529 "
                         "distribution. If already withdrawn, add the earnings "
                         "back on Schedule CA (540) line 8z and file Form 3805P."),
        documentation_needed=["529 distribution statement (basis vs earnings)",
                             "Schedule CA (540) line 8z entry"],
        rule_id="plan_529_ca",
        savings_federal=None,
        savings_state=None,
        state_caveat=caveat,
        assumptions=["529 K-12 non-conformity is CA-wide (R&TC 17140.3); the "
                     "additional-tax rate and non-qualified list come from the "
                     "year's plan_529 constants block."],
    )]