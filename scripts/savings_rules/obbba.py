#!/usr/bin/env python3
"""OBBBA rule family: M10 senior bonus, M11 tips/overtime.

The `senior_bonus_deduction` and `tips_deduction` / `overtime_deduction`
helpers are SHARED with `deduction_analyzer.py` (blueprint P5.3) so the two
tools agree to the dollar on the same inputs. Import this module from both
places rather than duplicating the phase-out math.
"""

import math
from typing import Any, Dict, List, Optional

from .engine import (
    ca_nonconformity,
    constant,
    federal_ordinary_marginal_rate,
    filing_status_of,
)
from .models import Priority, TaxOpportunity


# ---------------------------------------------------------------------------
# Shared helpers (imported by deduction_analyzer.py)
# ---------------------------------------------------------------------------

#: Statuses that use the MFJ MAGI phase-out start rather than the single one.
_JOINT_STATUSES = ("married_jointly", "qualifying_surviving_spouse")


def _phaseout_start_for_status(block: Dict[str, Any], filing_status: str) -> float:
    if filing_status in _JOINT_STATUSES:
        return block["magi_phaseout_start_mfj"]
    # MFS uses the single threshold (the OBBBA statute specifies single/MFS).
    return block["magi_phaseout_start_single"]


def senior_bonus_deduction(tax_data: Dict[str, Any], filing_status: str,
                           magi: float, qualifying_persons: int) -> float:
    """OBBBA senior bonus deduction (audit F7 / P5.3), shared helper.

        per_person = max(0, amount − phaseout_rate × max(0, magi − phaseout_start))

    Returns 0 outside the statutory window (effective_years) or when no
    qualifying person. `qualifying_persons` is the count of people aged 65+.
    """
    block = constant(tax_data, "obbba_deductions", "senior_bonus")
    year = str(tax_data.get("tax_year"))
    if year not in block["effective_years"] or qualifying_persons <= 0:
        return 0.0

    fs = filing_status_of({"filing_status": filing_status})
    start = _phaseout_start_for_status(block, fs)
    amount = block["amount"]
    rate = block["phaseout_rate"]
    excess = max(0.0, float(magi) - start)
    per_person = max(0.0, amount - rate * excess)
    return round(per_person * qualifying_persons, 2)


def senior_bonus_phaseout_start(tax_data: Dict[str, Any], filing_status: str) -> float:
    """The MAGI phase-out start for the senior bonus, by filing status.

    Public counterpart to the private `_phaseout_start_for_status`, exposed so
    `deduction_analyzer.py` can display the threshold without duplicating the
    joint-vs-single status logic.
    """
    block = constant(tax_data, "obbba_deductions", "senior_bonus")
    fs = filing_status_of({"filing_status": filing_status})
    return _phaseout_start_for_status(block, fs)


def _phaseout_reduction(magi: float, start: float, per_1000: float,
                       rounding: str) -> float:
    """OBBBA per-$1,000 MAGI phase-out reduction."""
    excess = max(0.0, float(magi) - start)
    if excess <= 0:
        return 0.0
    if rounding == "up":
        increments = math.ceil(excess / 1000.0)
    else:  # "down" — only whole $1,000 increments count
        increments = math.floor(excess / 1000.0)
    return increments * per_1000


def tips_deduction(tax_data: Dict[str, Any], filing_status: str,
                   magi: float, tips_income: float) -> float:
    """OBBBA tips deduction (2025-2028)."""
    block = constant(tax_data, "obbba_deductions", "tips")
    year = str(tax_data.get("tax_year"))
    if year not in block["effective_years"] or tips_income <= 0:
        return 0.0
    fs = filing_status_of({"filing_status": filing_status})
    start = _phaseout_start_for_status(block, fs)
    cap = block["max"]
    base = min(float(tips_income), cap)
    reduction = _phaseout_reduction(magi, start, block["phaseout_per_1000"],
                                   block["phaseout_rounding"])
    return round(max(0.0, base - reduction), 2)


def overtime_deduction(tax_data: Dict[str, Any], filing_status: str,
                       magi: float, overtime_income: float) -> float:
    """OBBBA overtime deduction (2025-2028)."""
    block = constant(tax_data, "obbba_deductions", "overtime")
    year = str(tax_data.get("tax_year"))
    if year not in block["effective_years"] or overtime_income <= 0:
        return 0.0
    fs = filing_status_of({"filing_status": filing_status})
    start = _phaseout_start_for_status(block, fs)
    cap = block["max_mfj"] if fs in _JOINT_STATUSES else block["max_single"]
    base = min(float(overtime_income), cap)
    reduction = _phaseout_reduction(magi, start, block["phaseout_per_1000"],
                                   block["phaseout_rounding"])
    return round(max(0.0, base - reduction), 2)


# ---------------------------------------------------------------------------
# M10 — Senior bonus deduction
# ---------------------------------------------------------------------------


def rule_senior_bonus(profile: Dict[str, Any],
                      tax_data: Dict[str, Any]) -> List[TaxOpportunity]:
    """M10: OBBBA senior bonus, phased out above the MAGI threshold, per person."""
    age = profile.get("age", 0)
    spouse_age = profile.get("spouse_age")
    fs = filing_status_of(profile)
    qualifying = (1 if age >= 65 else 0)
    if fs == "married_jointly" and spouse_age is not None and spouse_age >= 65:
        qualifying += 1
    if qualifying <= 0:
        return []

    # OBBBA (2025-2028) did not exist pre-2025; the constants block is absent
    # for those years. Refuse to fire rather than raise (acceptance A10: a
    # year-gated rule returns nothing outside its statutory window).
    if "obbba_deductions" not in tax_data:
        return []

    block = constant(tax_data, "obbba_deductions", "senior_bonus")
    year = str(tax_data.get("tax_year"))
    if year not in block["effective_years"]:
        return []

    deduction = senior_bonus_deduction(tax_data, fs, profile["magi"], qualifying)
    if deduction <= 0:
        # Phased fully out — still surface the rule at LOW priority so the
        # taxpayer knows why nothing is deductible.
        return [TaxOpportunity(
            category="OBBBA",
            name="Senior Bonus Deduction (fully phased out)",
            description=(f"Your MAGI (${profile['magi']:,.0f}) phases the senior "
                        "bonus out to $0."),
            potential_savings="$0 (phased out)",
            priority=Priority.LOW,
            action_required="No action — deduction is fully phased out at your income.",
            documentation_needed=[],
            rule_id="senior_bonus",
            savings_federal=0.0,
            savings_state=0.0,
            state_caveat=_ca_caveat(tax_data, profile),
            deadline="By the tax filing deadline.",
            assumptions=[f"Phase-out start ${_phaseout_start_for_status(block, fs):,.0f} for {fs}."],
            deduction_amount=0.0,
        )]

    rate = federal_ordinary_marginal_rate(tax_data, fs, profile["taxable_income"])
    savings_federal = round(deduction * rate, 2)
    return [TaxOpportunity(
        category="OBBBA",
        name="Senior Bonus Deduction",
        description=(f"Deduct ${deduction:,.0f} (${deduction / qualifying:,.0f} × "
                     f"{qualifying} qualifying person{'s' if qualifying > 1 else ''}) "
                     "under the OBBBA senior bonus."),
        potential_savings=(f"~${savings_federal:,.0f} federal"
                          + (" + $0 CA (added back)" if _ca_caveat(tax_data, profile) else "")),
        priority=Priority.HIGH,
        action_required="Claim the senior bonus above-the-line deduction on Schedule 1.",
        documentation_needed=["Form 1040 Schedule 1", "Age documentation"],
        rule_id="senior_bonus",
        savings_federal=savings_federal,
        savings_state=0.0 if _ca_caveat(tax_data, profile) else None,
        state_caveat=_ca_caveat(tax_data, profile),
        deadline="By the tax filing deadline (claimed on the return).",
        assumptions=[f"MAGI ${profile['magi']:,.0f}; phase-out start "
                    f"${_phaseout_start_for_status(block, fs):,.0f} for {fs}; "
                    f"rate {block['phaseout_rate']:.0%} per dollar over start."],
        deduction_amount=deduction,
    )]


# ---------------------------------------------------------------------------
# M11 — Tips / overtime deductions
# ---------------------------------------------------------------------------


def _ca_caveat(tax_data: Dict[str, Any], profile: Dict[str, Any]) -> Optional[str]:
    if str(profile.get("state", "")).upper() != "CA":
        return None
    nc = ca_nonconformity(tax_data)
    if not nc.get("obbba_tips_overtime_senior", True):
        return ("California conformity date is 2015-01-01, so the OBBBA tips / "
                "overtime / senior deductions are added back on Schedule CA — "
                "no CA benefit; state savings $0.")
    return None


def rule_tips_overtime(profile: Dict[str, Any],
                       tax_data: Dict[str, Any]) -> List[TaxOpportunity]:
    """M11: OBBBA tips and overtime above-the-line deductions (2025-2028)."""
    tips = float(profile.get("tips_income", 0) or 0)
    overtime = float(profile.get("overtime_premium_income", 0) or 0)
    if tips <= 0 and overtime <= 0:
        return []

    # OBBBA (2025-2028) did not exist pre-2025; refuse to fire outside the
    # statutory window rather than raise on the absent constants block (A10).
    if "obbba_deductions" not in tax_data:
        return []

    fs = filing_status_of(profile)
    magi = profile["magi"]
    rate = federal_ordinary_marginal_rate(tax_data, fs, profile["taxable_income"])
    caveat = _ca_caveat(tax_data, profile)

    ops: List[TaxOpportunity] = []
    if tips > 0:
        d = tips_deduction(tax_data, fs, magi, tips)
        if d > 0:
            ops.append(TaxOpportunity(
                category="OBBBA",
                name="Tips Deduction",
                description=f"Deduct ${d:,.0f} of tip income above the line (OBBBA).",
                potential_savings=f"~${round(d * rate, 2):,.0f} federal",
                priority=Priority.MEDIUM,
                action_required="Claim the tips deduction on Schedule 1.",
                documentation_needed=["Form 1040 Schedule 1", "Tip income records (Form 4070)"],
                rule_id="tips_deduction",
                savings_federal=round(d * rate, 2),
                savings_state=0.0 if caveat else None,
                state_caveat=caveat,
                deadline="By the tax filing deadline.",
                assumptions=[f"Capped at the statutory max; phased down above the MAGI threshold."],
                deduction_amount=d,
            ))
    if overtime > 0:
        d = overtime_deduction(tax_data, fs, magi, overtime)
        if d > 0:
            ops.append(TaxOpportunity(
                category="OBBBA",
                name="Overtime Deduction",
                description=f"Deduct ${d:,.0f} of overtime premium pay above the line (OBBBA).",
                potential_savings=f"~${round(d * rate, 2):,.0f} federal",
                priority=Priority.MEDIUM,
                action_required="Claim the overtime deduction on Schedule 1.",
                documentation_needed=["Form 1040 Schedule 1", "Overtime pay documentation"],
                rule_id="overtime_deduction",
                savings_federal=round(d * rate, 2),
                savings_state=0.0 if caveat else None,
                state_caveat=caveat,
                deadline="By the tax filing deadline.",
                assumptions=[f"Capped at the statutory max (single/MFJ); phased down above the MAGI threshold."],
                deduction_amount=d,
            ))
    return ops