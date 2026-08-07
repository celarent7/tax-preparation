#!/usr/bin/env python3
"""
QBI (IRC §199A) deduction calculator for sole proprietors / single-member LLCs
filing Schedule C, plus the qualified REIT/PTP dividend component from
1099-DIV Box 5 (section 199A dividends).

Scope and design rules (see docs/qbi_calculator_plan.md and
docs/implementation_prompt.md):
  * House style mirrors scripts/espp_calculator.py: dataclasses, a pure
    computation core, and a thin argparse CLI with --output-format json|text.
  * No statutory figure is hard-coded. Every rate, threshold, phase-in range
    and percentage comes from the per-year ``qbi`` block of
    scripts/tax_constants.json via utils.get_constant, which raises
    UnverifiedConstantError on an unverified (null) figure and KeyError on an
    absent one — the engine never guesses (ground rule 6).
  * The ½ SE tax that reduces QBI to "adjusted QBI" is computed by reusing
    estimated_tax_calculator.calculate_se_tax — it is NOT reimplemented here,
    so the figure is byte-identical to the estimated-tax path (ground rule 4).
  * K-1 pass-through QBI (S-corp / partnership) is out of scope; a profile
    carrying ``k1_qbi`` is rejected with a pointer to docs/limitations.md
    rather than silently ignored.

Out of scope (docs/limitations.md): K-1 pass-throughs, multi-business
aggregation under Reg. §1.199A-4, PTP income, DPAD/co-op patronage,
trusts/estates. The §199A math lives ONLY in this module — integrated scripts
import it rather than duplicating the computation (gate G5).
"""

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

import utils
from estimated_tax_calculator import calculate_se_tax


# ---------------------------------------------------------------------------
# SSTB field list (Reg. §1.199A-5(b))
# ---------------------------------------------------------------------------
# The trades or businesses whose QBI is phased out above the threshold. The
# tool must ASK whether the filer's business is an SSTB — it never infers it.
SSTB_FIELDS: List[str] = [
    "Health (medicine, dentistry, veterinary, optometry, etc.)",
    "Law (attorneys / paralegals)",
    "Accounting",
    "Consulting",
    "Financial services (investment management / banking / brokerage)",
    "Performing arts",
    "Athletics",
    'Any business where the principal asset is the reputation or skill of '
    'one or more of its employees/owners (the "reputation or skill" catch-all)',
]


# ---------------------------------------------------------------------------
# Filing-status → threshold/range key mapping
# ---------------------------------------------------------------------------
# §199A(e)(2): one threshold for "all other returns" (single/HOH/QSS). MFJ gets
# a joint threshold. For 2023–2025 MFS uses the single threshold; OBBBA split
# MFS to its own figure for 2026 (threshold_mfs), so the mapping is explicit
# per status — MFS never falls through a .get default to threshold_single
# (acceptance test Q10, audit finding D2).
_QBI_THRESHOLD_KEY = {
    "single": "threshold_single",
    "head_of_household": "threshold_single",
    "qualifying_surviving_spouse": "threshold_single",
    "married_jointly": "threshold_mfj",
    "married_separately": "threshold_mfs",
}
_QBI_RANGE_KEY = {
    "single": "phase_in_range_single",
    "head_of_household": "phase_in_range_single",
    "qualifying_surviving_spouse": "phase_in_range_single",
    "married_jointly": "phase_in_range_mfj",
    "married_separately": "phase_in_range_single",  # MFS uses the single range
}


def qbi_threshold(filing_status: str, year_constants: Dict[str, Any]) -> float:
    """Return the §199A taxable-income threshold for a filing status.

    Explicit per-status key access (no .get fallback): a missing threshold_mfs
    for a year that should carry one raises KeyError rather than silently
    reusing the single threshold (Q10).
    """
    status = utils.normalize_filing_status(filing_status)
    key = _QBI_THRESHOLD_KEY[status]
    return float(utils.get_constant(year_constants["qbi"], key))


def qbi_phase_in_range(filing_status: str, year_constants: Dict[str, Any]) -> float:
    """Return the §199A phase-in range for a filing status."""
    status = utils.normalize_filing_status(filing_status)
    key = _QBI_RANGE_KEY[status]
    return float(utils.get_constant(year_constants["qbi"], key))


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class QBIComponentResult:
    """The qualified-trade-or-business component of the §199A deduction."""

    adjusted_qbi: float
    w2_wages: float
    ubia: float
    is_sstb: bool
    taxable_income_before_qbi: float
    threshold: float
    phase: str  # "below_threshold" | "phase_in" | "above_range"
    applicable_pct: Optional[float]  # None below threshold; else 1-(TI-thr)/range
    tentative: float  # rate x (effective) QBI
    full_wage_limit: float  # max(wage_limit_pct*wages, wage_ubia_pct*wages + ubia_pct*ubia)
    reduction: float  # wage-limit phase-in reduction applied (0 outside the range)
    component: float  # the QBI component deduction (>= 0)
    carryforward: float  # negative QBI carried forward (0 when QBI >= 0)
    form: str  # "8995" or "8995-A"
    reason: str


@dataclass
class QBIResult:
    """The full §199A deduction for a profile."""

    year: str
    filing_status: str
    taxable_income_before_qbi: float
    threshold: float
    above_threshold: bool
    qbi_component: float
    reit_component: float
    combined: float
    overall_limit: float
    deduction: float
    form: str
    qbi_loss_carryforward: float
    reit_loss_carryforward: float
    min_deduction_applied: bool
    adjusted_qbi: float
    half_se_tax: float
    notes: List[str] = field(default_factory=list)
    component_detail: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# Pure computation functions
# ---------------------------------------------------------------------------


def compute_half_se_tax(se_net_profit: float, year_constants: Dict[str, Any]) -> float:
    """Return the deductible ½ SE tax for a Schedule C net profit.

    Reuses estimated_tax_calculator.calculate_se_tax so the figure is
    byte-identical to the estimated-tax / draft-reviewer path (ground rule 4).
    """
    if se_net_profit is None or se_net_profit <= 0:
        return 0.0
    se_tax_data = year_constants["se_tax"]
    return float(calculate_se_tax(float(se_net_profit), se_tax_data)["deduction"])


def compute_adjusted_qbi(
    se_net_profit: float = 0.0,
    *,
    half_se_tax: Optional[float] = None,
    se_health_insurance: float = 0.0,
    se_retirement: float = 0.0,
    qbi_loss_carryforward: float = 0.0,
    adjusted_qbi_override: Optional[float] = None,
    year_constants: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Reduce Schedule C net profit to adjusted QBI.

    Adjusted QBI = net SE profit − ½ SE tax − SE health insurance −
    SE retirement − prior-year QBI loss carryforward (the attributable
    above-the-line items, per §199A and Form 8995-A instructions).

    ``adjusted_qbi_override`` lets a caller supply a pre-computed adjusted QBI
    (e.g. when ½ SE tax was already taken on Schedule SE) and skips the SE
    adjustment entirely.

    Returns a dict with ``adjusted_qbi`` and ``half_se_tax`` (the figure
    actually used, for byte-identical reporting).
    """
    if adjusted_qbi_override is not None:
        return {"adjusted_qbi": float(adjusted_qbi_override), "half_se_tax": 0.0}

    if half_se_tax is None:
        half_se_tax = compute_half_se_tax(se_net_profit, year_constants or {})

    adjusted = (
        float(se_net_profit)
        - float(half_se_tax)
        - float(se_health_insurance)
        - float(se_retirement)
        - float(qbi_loss_carryforward)
    )
    return {"adjusted_qbi": adjusted, "half_se_tax": float(half_se_tax)}


def compute_qbi_component(
    qbi: float,
    w2_wages: float,
    ubia: float,
    is_sstb: bool,
    taxable_income_before_qbi: float,
    filing_status: str,
    year_constants: Dict[str, Any],
) -> QBIComponentResult:
    """Compute the qualified-trade-or-business component (Form 8995 / 8995-A).

    Implements the plan §3.1 spec, in order:
      1. Threshold test on taxable income before the QBI deduction.
      2. Below threshold → Form 8995 simplified: rate × QBI (SSTB/wages/UBIA
         irrelevant).
      3. Within the phase-in range → Form 8995-A: SSTB reduces QBI/wages/UBIA
         by applicable_pct first; then the wage/UBIA limit is phased in.
      4. Above the range → full wage/UBIA limit binds; SSTB component is $0.
      5. Negative QBI → component $0, loss carries forward (reported in
         ``carryforward``).
    """
    qbi_block = year_constants["qbi"]
    rate = float(utils.get_constant(qbi_block, "rate"))
    wage_limit_pct = float(utils.get_constant(qbi_block, "wage_limit_pct"))
    wage_ubia_pct = float(utils.get_constant(qbi_block, "wage_ubia_pct"))
    ubia_pct = float(utils.get_constant(qbi_block, "ubia_pct"))

    threshold = qbi_threshold(filing_status, year_constants)
    rng = qbi_phase_in_range(filing_status, year_constants)
    ti = float(taxable_income_before_qbi)

    # Negative QBI: no current-year deduction; the loss carries forward.
    if qbi < 0:
        return QBIComponentResult(
            adjusted_qbi=qbi,
            w2_wages=float(w2_wages),
            ubia=float(ubia),
            is_sstb=bool(is_sstb),
            taxable_income_before_qbi=ti,
            threshold=threshold,
            phase="negative",
            applicable_pct=None,
            tentative=0.0,
            full_wage_limit=0.0,
            reduction=0.0,
            component=0.0,
            carryforward=qbi,
            form="8995",
            reason="Adjusted QBI is negative; the loss carries forward to "
                   "reduce a future year's QBI (no current-year component).",
        )

    above_end = threshold + rng

    if ti <= threshold:
        # Below (or at) threshold → simplified Form 8995 path.
        tentative = rate * qbi
        component = tentative
        return QBIComponentResult(
            adjusted_qbi=qbi,
            w2_wages=float(w2_wages),
            ubia=float(ubia),
            is_sstb=bool(is_sstb),
            taxable_income_before_qbi=ti,
            threshold=threshold,
            phase="below_threshold",
            applicable_pct=None,
            tentative=round(tentative, 2),
            full_wage_limit=0.0,
            reduction=0.0,
            component=round(component, 2),
            carryforward=0.0,
            form="8995",
            reason="Taxable income at or below the §199A threshold: the "
                   "simplified Form 8995 applies — 20% of adjusted QBI, with "
                   "no wage/UBIA limit and no SSTB phase-out.",
        )

    # Above the threshold: determine the phase-in fraction p = (TI-thr)/range.
    p = (ti - threshold) / rng  # 0 < p at this point; capped at/above 1 below
    if p > 1.0:
        p = 1.0
    applicable_pct = max(0.0, 1.0 - p)

    if is_sstb:
        # SSTB: QBI, wages and UBIA are reduced by the applicable percentage
        # before the wage/UBIA limit is computed (§199A(d)(3)). If the
        # applicable percentage is zero the SSTB is fully phased out.
        if applicable_pct <= 0.0:
            return QBIComponentResult(
                adjusted_qbi=qbi,
                w2_wages=float(w2_wages),
                ubia=float(ubia),
                is_sstb=True,
                taxable_income_before_qbi=ti,
                threshold=threshold,
                phase="above_range",
                applicable_pct=0.0,
                tentative=0.0,
                full_wage_limit=0.0,
                reduction=0.0,
                component=0.0,
                carryforward=0.0,
                form="8995-A",
                reason="SSTB fully phased out: taxable income exceeds the top "
                       "of the §199A phase-in range, so the applicable "
                       "percentage is 0% and the QBI component is $0. The REIT "
                       "/ PTP component, if any, is unaffected and survives.",
            )
        eff_qbi = qbi * applicable_pct
        eff_wages = float(w2_wages) * applicable_pct
        eff_ubia = float(ubia) * applicable_pct
    else:
        eff_qbi = qbi
        eff_wages = float(w2_wages)
        eff_ubia = float(ubia)

    tentative = rate * eff_qbi
    full_wage_limit = max(
        wage_limit_pct * eff_wages,
        wage_ubia_pct * eff_wages + ubia_pct * eff_ubia,
    )

    phase = "phase_in" if ti <= above_end else "above_range"

    if phase == "above_range":
        # Full wage/UBIA limit binds (reduction factor = 1).
        if tentative > full_wage_limit:
            component = full_wage_limit
            reduction = tentative - full_wage_limit
        else:
            component = tentative
            reduction = 0.0
        reason = (
            "Taxable income above the phase-in range: the full wage/UBIA "
            "limit applies (20% of QBI capped at the greater of 50% of W-2 "
            "wages or 25% of wages plus 2.5% of UBIA)."
        )
    else:
        # Within the range: phase in the wage/UBIA reduction.
        if tentative > full_wage_limit:
            reduction = (tentative - full_wage_limit) * p
            component = tentative - reduction
        else:
            reduction = 0.0
            component = tentative
        sstb_note = (
            " The QBI, wages and UBIA were first reduced by the applicable "
            f"percentage of {applicable_pct:.4f} (SSTB phase-out)."
            if is_sstb else ""
        )
        reason = (
            "Taxable income inside the §199A phase-in range: the wage/UBIA "
            "reduction is phased in linearly." + sstb_note
        )

    return QBIComponentResult(
        adjusted_qbi=qbi,
        w2_wages=float(w2_wages),
        ubia=float(ubia),
        is_sstb=bool(is_sstb),
        taxable_income_before_qbi=ti,
        threshold=threshold,
        phase=phase,
        applicable_pct=round(applicable_pct, 6),
        tentative=round(tentative, 2),
        full_wage_limit=round(full_wage_limit, 2),
        reduction=round(reduction, 2),
        component=round(max(component, 0.0), 2),
        carryforward=0.0,
        form="8995-A",
        reason=reason,
    )


def compute_reit_component(
    reit_dividends: float,
    year_constants: Dict[str, Any],
    reit_loss_carryforward: float = 0.0,
) -> Dict[str, Any]:
    """Compute the REIT/PTP component = reit_ptp_rate × qualified dividends.

    The REIT/PTP component is NEVER subject to the W-2 wage / UBIA limit or the
    SSTB phase-out (§199A(c)(1)). Negative REIT/PTP carries forward separately
    and is not netted against the QBI component.
    """
    rate = float(utils.get_constant(year_constants["qbi"], "reit_ptp_rate"))
    div = float(reit_dividends) - float(reit_loss_carryforward)
    if div <= 0:
        return {
            "component": 0.0,
            "carryforward": -div if div < 0 else 0.0,
            "rate": rate,
            "reason": "No positive REIT/PTP dividend after applying the "
                      "prior-year loss carryforward; the REIT component is $0 "
                      "and any remaining loss carries forward separately.",
        }
    component = rate * div
    return {
        "component": round(component, 2),
        "carryforward": 0.0,
        "rate": rate,
        "reason": f"REIT/PTP component = {rate:.0%} × qualified REIT/PTP "
                  f"dividends; never wage-limited.",
    }


def compute_overall_limit(
    taxable_income_before_qbi: float,
    net_capital_gain: float,
    year_constants: Dict[str, Any],
) -> float:
    """Overall taxable-income limit = rate × (TI − net capital gain).

    ``net_capital_gain`` is the §1(h) net capital gain, which per §199A(e)(3)
    and the Form 8995 instructions INCLUDES qualified dividends (the §1(h)(11)
    amounts). The caller must pass LTCG + qualified dividends together; the
    engine does not add them itself.
    """
    rate = float(utils.get_constant(year_constants["qbi"], "rate"))
    ti = float(taxable_income_before_qbi)
    ncg = max(0.0, float(net_capital_gain))
    base = max(0.0, ti - ncg)
    return round(rate * base, 2)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _has_trade_or_business(adjusted_qbi: float, reit_dividends: float) -> bool:
    """A qualified trade/business exists when there is non-zero adjusted QBI
    (positive or negative) to report. REIT-only profiles have none."""
    return adjusted_qbi != 0.0


def _route_form(
    has_tb: bool,
    is_sstb: bool,
    ti: float,
    threshold: float,
) -> str:
    """Form 8995 (simplified) vs 8995-A.

    8995-A is required when taxable income exceeds the threshold AND there is a
    qualified trade/business (the wage/UBIA limit must be computed), or when an
    SSTB is above the threshold (phase-out must be shown). 8995 otherwise,
    including the REIT-only case at any income level (no wage limit applies).
    """
    if has_tb and ti > threshold:
        return "8995-A"
    if is_sstb and has_tb and ti > threshold:
        return "8995-A"
    return "8995"


def compute_qbi_deduction(
    profile: Dict[str, Any], year_constants: Dict[str, Any]
) -> QBIResult:
    """Compute the full §199A QBI deduction for a profile.

    Profile keys (all amounts in dollars):
      adjusted_qbi (float, optional) — pre-computed adjusted QBI; skips the
          SE adjustment. Mutually exclusive with se_net_profit in practice.
      se_net_profit (float, optional) — Schedule C net profit; ½ SE tax,
          se_health_insurance and se_retirement are subtracted to get
          adjusted QBI.
      se_health_insurance, se_retirement (float, default 0)
      se_tax_half (float, optional) — override ½ SE tax (byte-identical reuse).
      w2_wages_paid (float, default 0) — the business's W-2 wages.
      ubia (float, default 0) — unadjusted basis of qualified property.
      is_sstb (bool, default False)
      taxable_income_before_qbi (float, required)
      net_capital_gain (float, default 0) — net LTCG + qualified dividends.
      reit_dividends (float, default 0) — 1099-DIV Box 5 (section 199A).
      filing_status (str, required)
      qbi_loss_carryforward (float, default 0) — prior-year QBI loss.
      reit_loss_carryforward (float, default 0)
      k1_qbi (any, optional) — presence triggers rejection (out of scope).

    Raises:
      ValueError: k1_qbi present (K-1 pass-throughs out of scope), or a
          required input is missing.
      utils.UnverifiedConstantError / KeyError: a needed statutory figure is
          unverified or absent (ground rule 6 — never guess).
    """
    # K-1 rejection: pass-through QBI is out of scope (docs/limitations.md).
    if profile.get("k1_qbi") is not None:
        raise ValueError(
            "K-1 pass-through QBI (S-corp / partnership §199A) is out of "
            "scope for this calculator; it requires aggregation under "
            "Reg. §1.199A-4 and separate wage/UBIA allocation. See "
            "docs/limitations.md. The Schedule-C + REIT engine cannot "
            "produce a correct result for a K-1 profile and will not guess."
        )

    filing_status = profile.get("filing_status")
    if not filing_status:
        raise ValueError("filing_status is required.")
    ti = profile.get("taxable_income_before_qbi")
    if ti is None:
        raise ValueError("taxable_income_before_qbi is required.")

    year = str(year_constants.get("tax_year", ""))

    # 1. Adjusted QBI (§199A attribution of above-the-line items).
    adj = compute_adjusted_qbi(
        se_net_profit=float(profile.get("se_net_profit", 0.0) or 0.0),
        half_se_tax=(
            None if profile.get("se_tax_half") is None
            else float(profile["se_tax_half"])
        ),
        se_health_insurance=float(profile.get("se_health_insurance", 0.0) or 0.0),
        se_retirement=float(profile.get("se_retirement", 0.0) or 0.0),
        qbi_loss_carryforward=float(profile.get("qbi_loss_carryforward", 0.0) or 0.0),
        adjusted_qbi_override=(
            None if profile.get("adjusted_qbi") is None
            else float(profile["adjusted_qbi"])
        ),
        year_constants=year_constants,
    )
    adjusted_qbi = adj["adjusted_qbi"]
    half_se_tax = adj["half_se_tax"]

    # 2. QBI component.
    comp = compute_qbi_component(
        qbi=adjusted_qbi,
        w2_wages=float(profile.get("w2_wages_paid", 0.0) or 0.0),
        ubia=float(profile.get("ubia", 0.0) or 0.0),
        is_sstb=bool(profile.get("is_sstb", False)),
        taxable_income_before_qbi=float(ti),
        filing_status=filing_status,
        year_constants=year_constants,
    )

    # 3. REIT/PTP component.
    reit = compute_reit_component(
        reit_dividends=float(profile.get("reit_dividends", 0.0) or 0.0),
        year_constants=year_constants,
        reit_loss_carryforward=float(
            profile.get("reit_loss_carryforward", 0.0) or 0.0
        ),
    )

    qbi_component = comp.component
    reit_component = reit["component"]
    combined = round(qbi_component + reit_component, 2)

    # 4. Overall taxable-income limit.
    overall_limit = compute_overall_limit(
        taxable_income_before_qbi=float(ti),
        net_capital_gain=float(profile.get("net_capital_gain", 0.0) or 0.0),
        year_constants=year_constants,
    )
    deduction = min(combined, overall_limit)

    # 5. Form routing.
    threshold = comp.threshold
    has_tb = _has_trade_or_business(adjusted_qbi, float(profile.get("reit_dividends", 0.0) or 0.0))
    form = _route_form(has_tb, bool(profile.get("is_sstb", False)), float(ti), threshold)
    # The component's own form reflects whether 8995-A math was needed; the
    # return-level form follows the same rule but also stays 8995 for REIT-only.
    if not has_tb:
        form = "8995"

    notes: List[str] = [comp.reason, reit["reason"]]
    min_applied = False

    # 6. 2026+ OBBBA minimum (§199A(i)). The minimum is "the greater of the
    # otherwise-computed deduction or the min_deduction constant" for an
    # applicable taxpayer (aggregate active QBI ≥ the floor), and it OVERRIDES
    # the overall taxable-income limit. Reading the constants is unconditional
    # for years ≥ 2026: a null/absent figure raises rather than silently
    # skipping (ground rule 6 / plan §3.1.6).
    if year and int(year) >= 2026:
        qbi_block = year_constants["qbi"]
        min_deduction = float(utils.get_constant(qbi_block, "min_deduction"))
        floor = float(utils.get_constant(qbi_block, "min_deduction_qbi_floor"))
        active_qbi = max(0.0, adjusted_qbi)
        if active_qbi >= floor and deduction < min_deduction:
            deduction = min_deduction
            min_applied = True
            notes.append(
                f"OBBBA §199A(i) minimum applied: aggregate active QBI "
                f"(${active_qbi:,.2f}) ≥ the ${floor:,.0f} floor, so the "
                f"deduction is raised to the ${min_deduction:,.0f} minimum "
                f"(this overrides the overall taxable-income limit)."
            )

    deduction = round(deduction, 2)

    return QBIResult(
        year=year,
        filing_status=utils.normalize_filing_status(filing_status),
        taxable_income_before_qbi=float(ti),
        threshold=threshold,
        above_threshold=float(ti) > threshold,
        qbi_component=qbi_component,
        reit_component=reit_component,
        combined=combined,
        overall_limit=overall_limit,
        deduction=deduction,
        form=form,
        # comp.carryforward is negative (it mirrors the negative adjusted QBI);
        # qbi_loss_carryforward is reported as a positive magnitude so it
        # round-trips directly into next year's qbi_loss_carryforward input,
        # which subtracts it (see compute_adjusted_qbi).
        qbi_loss_carryforward=-comp.carryforward + 0.0,  # avoid -0.0 in output
        reit_loss_carryforward=reit["carryforward"],
        min_deduction_applied=min_applied,
        adjusted_qbi=round(adjusted_qbi, 2),
        half_se_tax=half_se_tax,
        notes=notes,
        component_detail={
            "phase": comp.phase,
            "applicable_pct": comp.applicable_pct,
            "tentative": comp.tentative,
            "full_wage_limit": comp.full_wage_limit,
            "reduction": comp.reduction,
            "w2_wages": comp.w2_wages,
            "ubia": comp.ubia,
            "is_sstb": comp.is_sstb,
        },
    )


# ---------------------------------------------------------------------------
# Text formatting
# ---------------------------------------------------------------------------


def format_text(result: QBIResult) -> str:
    lines: List[str] = []
    lines.append(f"QBI Deduction (§199A) — Tax Year {result.year}")
    lines.append(f"Filing status: {result.filing_status}")
    lines.append(f"Form: {result.form}")
    lines.append("")
    lines.append("Inputs:")
    lines.append(f"  Taxable income before QBI deduction: ${result.taxable_income_before_qbi:,.2f}")
    lines.append(f"  §199A threshold: ${result.threshold:,.0f}")
    lines.append(f"  Above threshold: {'yes' if result.above_threshold else 'no'}")
    lines.append(f"  Adjusted QBI: ${result.adjusted_qbi:,.2f}")
    if result.half_se_tax:
        lines.append(f"  ½ SE tax subtracted: ${result.half_se_tax:,.2f}")
    lines.append("")
    lines.append("Components:")
    lines.append(f"  QBI component (trade/business): ${result.qbi_component:,.2f}")
    lines.append(f"  REIT/PTP component: ${result.reit_component:,.2f}")
    lines.append(f"  Combined: ${result.combined:,.2f}")
    lines.append(f"  Overall taxable-income limit: ${result.overall_limit:,.2f}")
    lines.append("")
    lines.append(f"QBI deduction: ${result.deduction:,.2f}")
    if result.min_deduction_applied:
        lines.append("  (raised to the §199A(i) minimum — see Notes)")
    if result.qbi_loss_carryforward:
        lines.append(f"  QBI loss carryforward to next year: ${result.qbi_loss_carryforward:,.2f}")
    if result.reit_loss_carryforward:
        lines.append(f"  REIT/PTP loss carryforward: ${result.reit_loss_carryforward:,.2f}")
    lines.append("")
    lines.append("Notes:")
    for n in result.notes:
        lines.append(f"  - {n}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qbi_calculator.py",
        description="Compute the IRC §199A QBI deduction for a Schedule C "
                    "filer plus qualified REIT/PTP dividends.",
    )
    p.add_argument("--se-net-profit", type=float, default=None,
                   help="Schedule C net profit (before ½ SE tax).")
    p.add_argument("--adjusted-qbi", type=float, default=None,
                   help="Pre-computed adjusted QBI (skips the SE adjustment).")
    p.add_argument("--se-health-insurance", type=float, default=0.0)
    p.add_argument("--se-retirement", type=float, default=0.0)
    p.add_argument("--w2-wages-paid", type=float, default=0.0,
                   help="W-2 wages paid by the business (wage/UBIA limit).")
    p.add_argument("--ubia", type=float, default=0.0,
                   help="Unadjusted basis of qualified property.")
    p.add_argument("--sstb", action="store_true",
                   help="The business is a Specified Service Trade or Business.")
    p.add_argument("--sstb-help", action="store_true",
                   help="Print the SSTB field list (Reg. §1.199A-5) and exit.")
    p.add_argument("--taxable-income-before-qbi", type=float, required=False,
                   help="Taxable income before the QBI deduction (not AGI).")
    p.add_argument("--net-capital-gain", type=float, default=0.0,
                   help="Net LTCG + qualified dividends (§1(h)).")
    p.add_argument("--reit-dividends", type=float, default=0.0,
                   help="1099-DIV Box 5 section 199A dividends.")
    p.add_argument("--qbi-loss-carryforward", type=float, default=0.0)
    p.add_argument("--reit-loss-carryforward", type=float, default=0.0)
    p.add_argument("--k1-qbi", type=float, default=None,
                   help="K-1 pass-through QBI (S-corp/partnership). Out of "
                        "scope — supplying it rejects the profile with a "
                        "pointer to docs/limitations.md (exit 1).")
    p.add_argument("--filing-status", type=str, default=None)
    p.add_argument("--output-format", choices=["json", "text"], default="text")
    utils.make_year_arg(p, default="2025")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.sstb_help:
        print("Specified Service Trades or Businesses (Reg. §1.199A-5(b)):")
        print("The QBI deduction phases out for these fields above the threshold:")
        for f in SSTB_FIELDS:
            print(f"  - {f}")
        print()
        print("This tool ASKS whether your business is an SSTB (--sstb); it never infers it.")
        return 0

    if args.adjusted_qbi is None and args.se_net_profit is None:
        parser.error("Provide --se-net-profit or --adjusted-qbi.")
    if not args.filing_status:
        parser.error("--filing-status is required.")
    if args.taxable_income_before_qbi is None:
        parser.error("--taxable-income-before-qbi is required.")

    year_constants = utils.load_tax_constants(args.year)

    profile: Dict[str, Any] = {
        "se_net_profit": args.se_net_profit if args.se_net_profit is not None else 0.0,
        "adjusted_qbi": args.adjusted_qbi,
        "se_health_insurance": args.se_health_insurance,
        "se_retirement": args.se_retirement,
        "w2_wages_paid": args.w2_wages_paid,
        "ubia": args.ubia,
        "is_sstb": args.sstb,
        "taxable_income_before_qbi": args.taxable_income_before_qbi,
        "net_capital_gain": args.net_capital_gain,
        "reit_dividends": args.reit_dividends,
        "qbi_loss_carryforward": args.qbi_loss_carryforward,
        "reit_loss_carryforward": args.reit_loss_carryforward,
        "k1_qbi": args.k1_qbi,
        "filing_status": args.filing_status,
    }

    try:
        result = compute_qbi_deduction(profile, year_constants)
    except utils.UnverifiedConstantError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.output_format == "json":
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(format_text(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())