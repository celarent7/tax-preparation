#!/usr/bin/env python3
"""
Canonical taxpayer profile for the Savings & Optimization Engine.

The savings finder consumes ONE flat, well-defined profile (see
`references/tax_planning.md` for the schema). This module loads, validates
and normalizes it, and adapts the legacy nested shape used by the rest of the
plugin (`income.w2_wages`, `adjustments.hsa_contributions`, ...) onto the
canonical one.

Design rules (see docs/savings_engine_plan.md §1.1 and docs/audit_improvement_blueprint.md):
  * Unknown keys are REJECTED with an error naming the nearest valid key —
    not silently ignored. A typo like `"aggi"` previously produced `agi = 0`
    and a silent $0 result (the failure mode documented in the remediation
    log). That class of bug must not return.
  * Filing status is normalized via `utils.normalize_filing_status` (audit D2).
  * The legacy nested shape is accepted through an EXPLICIT adapter that emits
    a deprecation warning listing every mapped key, so callers see the
    migration path.
"""

import json
import warnings
from difflib import get_close_matches
from typing import Any, Dict, List, Optional

from utils import normalize_filing_status

# ---------------------------------------------------------------------------
# Canonical schema
# ---------------------------------------------------------------------------

#: Every recognized top-level key, with its default. A default of `None` means
#: the field is optional and its absence is meaningful (e.g. `taxable_income`
#: is derived when omitted). `required` fields have no default and must be
#: supplied (or derived from the legacy shape).
CANONICAL_FIELDS: Dict[str, Any] = {
    "tax_year": None,            # optional; the CLI --year wins when both given
    "filing_status": None,       # REQUIRED (validated by the finder too)
    "state": None,               # optional; enables state-aware caveats
    "age": 40,
    "spouse_age": None,          # optional; presence implies MFJ spouse calc
    "agi": 0,
    "magi": None,                # optional; defaults to agi
    "wages": 0,
    "se_net_profit": 0,
    "taxable_income": None,      # optional; derived if absent
    # Retirement
    "employee_401k_contributions": 0,
    "spouse_employee_401k_contributions": 0,
    "plan_allows_after_tax_401k": False,
    "plan_allows_in_service_rollover": False,
    "employer_401k_contributions": 0,
    "after_tax_401k_contributions": 0,
    "ira_contributions": 0,
    "roth_ira_contributions": 0,
    "traditional_ira_pretax_balance": 0,
    "covered_by_workplace_plan": False,
    "spouse_covered_by_workplace_plan": False,
    # HSA
    "has_hdhp": False,
    "hsa_coverage": "single",
    "hsa_contributions": 0,
    # Investments
    "unrealized_st_losses": 0,
    "unrealized_lt_losses": 0,
    "realized_st_gains": 0,
    "realized_st_losses": 0,
    "realized_lt_gains": 0,
    "realized_lt_losses": 0,
    "capital_loss_carryforward": 0,
    "appreciated_stock_value": 0,
    "appreciated_stock_basis": 0,
    "held_over_one_year": True,
    # Charitable
    "planned_cash_donations": 0,
    "itemized_total": 0,
    # Self-employment
    "se_health_insurance_premiums": 0,
    "business_equipment_purchases": 0,
    "equipment_in_service_date": None,
    # Misc / OBBBA
    "tips_income": 0,
    "overtime_premium_income": 0,
}

_REQUIRED: List[str] = ["filing_status"]

#: Fields that, when present on an MFJ return, signal a spouse should be
#: computed independently (the retirement rules run per-person).
_SPOUSE_PRESENCE_KEYS = ("spouse_age", "spouse_employee_401k_contributions",
                         "spouse_covered_by_workplace_plan")


class ProfileError(ValueError):
    """Raised when a profile fails schema validation."""


# ---------------------------------------------------------------------------
# Nearest-key suggestion (kills the silent `agi = 0` failure mode)
# ---------------------------------------------------------------------------


def _suggest_unknown(unknown: str) -> str:
    """Return a human hint for an unknown key, e.g. `"aggi"` -> `"'agi'"`."""
    matches = get_close_matches(unknown, list(CANONICAL_FIELDS), n=1, cutoff=0.6)
    if matches:
        return f" Did you mean {matches[0]!r}?"
    return ""


def _reject_unknown_keys(raw: Dict[str, Any]) -> None:
    unknown = [k for k in raw if k not in CANONICAL_FIELDS]
    if not unknown:
        return
    hints = "".join(f"\n  {k!r}:{_suggest_unknown(k)}" for k in sorted(unknown))
    raise ProfileError(
        "Unknown profile key(s), refusing to proceed silently (a typo here "
        f"previously produced a silent $0 result):{hints}\n"
        f"Valid keys: {', '.join(sorted(CANONICAL_FIELDS))}."
    )


# ---------------------------------------------------------------------------
# Legacy nested-shape adapter
# ---------------------------------------------------------------------------

#: The legacy shape groups fields under `income`, `deductions`, `adjustments`,
#: `credits`, `withholding`. This table maps each legacy path to its canonical
#: key. Only keys that carry savings-relevant information are mapped; the rest
#: are recorded in the deprecation warning so nothing is silently dropped.
LEGACY_MAP: Dict[str, str] = {
    "tax_year": "tax_year",
    "filing_status": "filing_status",
    "age": "age",
    "state": "state",
    # income
    "income.w2_wages": "wages",
    "income.interest_income": "agi",          # legacy had no flat agi; approximate
    "income.dividend_income": "agi",
    "income.qualified_dividends": "agi",
    "income.capital_gains_short": "realized_st_gains",
    "income.capital_gains_long": "realized_lt_gains",
    # deductions (charitable bunching path)
    "deductions.charitable_cash": "planned_cash_donations",
    # adjustments
    "adjustments.traditional_ira": "ira_contributions",
    "adjustments.hsa_contributions": "hsa_contributions",
    "adjustments.student_loan_interest": "agi",
    # credits / withholding carry no savings-relevant scalar
}


def _is_legacy(raw: Dict[str, Any]) -> bool:
    return any(k in raw for k in ("income", "deductions", "adjustments", "credits", "withholding"))


def _flatten_legacy(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map the legacy nested shape onto the canonical flat schema.

    Emits a DeprecationWarning listing every key that was mapped (and every
    legacy key that was NOT mapped, so the caller sees what was dropped).
    """
    mapped: List[str] = []
    dropped: List[str] = []
    out: Dict[str, Any] = {}

    def walk(prefix: str, node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(f"{prefix}.{k}" if prefix else k, v)
        else:
            canon = LEGACY_MAP.get(prefix)
            if canon is None:
                dropped.append(prefix)
                return
            mapped.append(f"{prefix} -> {canon}")
            # income.* interest/dividend contributions fold into agi additively
            value = node if isinstance(node, (int, float)) else 0
            if canon == "agi":
                out[canon] = out.get(canon, 0) + value
            elif canon in ("realized_st_gains", "realized_lt_gains"):
                out[canon] = value
            else:
                out[canon] = node

    for top, val in raw.items():
        if isinstance(val, dict):
            walk(top, val)
        else:
            # top-level scalar (tax_year, filing_status, age, state)
            canon = LEGACY_MAP.get(top, top if top in CANONICAL_FIELDS else None)
            if canon is None:
                dropped.append(top)
                continue
            if canon != top:
                mapped.append(f"{top} -> {canon}")
            out[canon] = val

    warnings.warn(
        "The nested profile shape (income/adjustments/...) is deprecated; use the "
        "flat canonical schema in references/tax_planning.md. Mapped: "
        + "; ".join(mapped)
        + (f". Dropped (no canonical equivalent): {', '.join(dropped)}" if dropped else "")
        + ".",
        DeprecationWarning,
        stacklevel=3,
    )
    return out


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------


def load_profile(path: str) -> Dict[str, Any]:
    """Load and validate a taxpayer profile from a JSON file.

    Accepts either the canonical flat shape or the legacy nested shape (via the
    adapter). Returns a fully-defaulted, filing-status-normalized profile dict.

    Raises ProfileError on unknown keys (naming the nearest valid key) or a
    missing required field. Never silently falls back to defaults for a typo.
    """
    with open(path, "r") as f:
        raw = json.load(f)

    if not isinstance(raw, dict):
        raise ProfileError(f"Profile must be a JSON object, got {type(raw).__name__}.")

    if _is_legacy(raw):
        raw = _flatten_legacy(raw)

    _reject_unknown_keys(raw)

    for key in _REQUIRED:
        if raw.get(key) in (None, ""):
            raise ProfileError(
                f"Required profile field {key!r} is missing. "
                "Must be one of: single, mfj, mfs, hoh, qss (or aliases)."
            )

    # Apply defaults, then normalize filing status (audit D2).
    profile: Dict[str, Any] = {}
    for key, default in CANONICAL_FIELDS.items():
        profile[key] = raw.get(key, default)

    profile["filing_status"] = normalize_filing_status(profile["filing_status"])

    # magi defaults to agi when not supplied
    if profile["magi"] is None:
        profile["magi"] = profile["agi"]

    # taxable_income derived from agi when not supplied (best-effort; the
    # finder also accepts an explicit figure for marginal-rate math)
    if profile["taxable_income"] is None:
        profile["taxable_income"] = max(0.0, profile["agi"])

    # Coerce numeric fields defensively (the adapter may have left None in agi)
    for key in ("agi", "magi", "taxable_income"):
        if profile[key] is None:
            profile[key] = 0

    return profile


def has_spouse(profile: Dict[str, Any]) -> bool:
    """True when an MFJ profile carries any spouse-specific field."""
    if profile.get("filing_status") != "married_jointly":
        return False
    return any(profile.get(k) not in (None, 0, False) for k in _SPOUSE_PRESENCE_KEYS)