#!/usr/bin/env python3
"""Shared rate helpers for the savings rules.

Every marginal-rate figure here is computed by REUSING the existing engines in
`utils` + `tax_calculator` — no bracket-walking code is duplicated here (that
duplication is how audit finding F13 happened). The rule modules import from
this module rather than touching brackets directly.

  * Federal ordinary marginal rate -> `tax_calculator.calculate_marginal_rate`
  * Federal LTCG marginal rate    -> `tax_calculator.calculate_preferential_tax`
    (the qualified-dividends/capital-gain stacking worksheet), evaluated on a
    $1 preferential slice stacked on top of ordinary income.
  * NIIT on a slice of investment income -> `tax_calculator.calculate_niit`
  * CA marginal rate -> `calculate_marginal_rate` fed the CA bracket table
    (loaded via `utils.load_state_constants`).
"""

import os
import sys
from typing import Any, Dict, Optional

# Make `scripts/` importable when this package is imported directly (tests do
# this); the finder already adds it to sys.path at runtime.
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from tax_calculator import (  # noqa: E402
    calculate_marginal_rate,
    calculate_niit,
    calculate_preferential_tax,
)
from utils import (  # noqa: E402
    UnverifiedConstantError,
    get_constant,
    get_status_value,
    load_state_constants,
    normalize_filing_status,
    select_for_status,
)


def federal_ordinary_marginal_rate(tax_data: Dict[str, Any],
                                  filing_status: str,
                                  taxable_income: float) -> float:
    """Federal marginal rate on the next ordinary dollar.

    Reuses `calculate_marginal_rate` against the processed federal bracket
    table — the same path `tax_calculator.py` uses.
    """
    return calculate_marginal_rate(
        max(0.0, float(taxable_income)), filing_status,
        tax_data["tax_brackets_processed"],
    )


def federal_ltcg_marginal_rate(tax_data: Dict[str, Any],
                               filing_status: str,
                               ordinary_income: float) -> float:
    """Federal marginal LTCG rate (0/15/20%) on the next long-term dollar.

    Determined by stacking $1 of preferential income on top of ordinary income
    and reading the tax it produces — exactly the qualified-dividends/capital-
    gain stacking worksheet (`calculate_preferential_tax`), so the rate band is
    set by TOTAL taxable income as the worksheet requires.
    """
    ltcg_brackets = select_for_status(
        tax_data["ltcg_brackets_processed"], filing_status, "LTCG brackets"
    )
    tax, _ = calculate_preferential_tax(max(0.0, ordinary_income), 1.0, ltcg_brackets)
    # tax is the dollar tax on a $1 slice, i.e. the marginal rate itself. Round
    # to 4 decimals to absorb float noise WITHOUT collapsing $0.15 to $0.
    return round(tax, 4)


def niit_on_gain(tax_data: Dict[str, Any], filing_status: str,
                 magi: float, gain: float) -> float:
    """NIIT (IRC 1411) that applies to a slice of investment income.

    Reuses `calculate_niit` against the per-status threshold. Returns 0 when
    MAGI is below the threshold (so donating appreciated stock to AVOID a gain
    yields $0 avoided NIIT for sub-threshold filers).
    """
    threshold = get_status_value(tax_data["niit_thresholds"], filing_status,
                                 "NIIT threshold")
    return calculate_niit(float(magi), max(0.0, float(gain)), threshold)["tax"]


def ca_marginal_rate(tax_data: Dict[str, Any], year: str, filing_status: str,
                     ca_taxable_income: float) -> Optional[float]:
    """California marginal rate on the next dollar of CA taxable income.

    CA brackets are per-status in the constants; we feed them to
    `calculate_marginal_rate` (the same walker used federally) so no bracket
    code is duplicated. Returns None when CA is not available for the year.
    """
    try:
        ca = load_state_constants("CA", year)
    except ValueError:
        return None
    brackets_raw = select_for_status(ca["brackets"], filing_status, "California brackets")
    processed = [(b["top"] if b["top"] is not None else float("inf"), b["rate"])
                 for b in brackets_raw]
    return calculate_marginal_rate(max(0.0, float(ca_taxable_income)), filing_status,
                                   {filing_status: processed})


def ca_nonconformity(tax_data: Dict[str, Any]) -> Dict[str, Any]:
    """The CA nonconformity block for the active year, or {} if CA absent.

    `tax_year` in the constants is an int (2025); the per-year state keys are
    strings ("2025"). Coerce to str so the lookup hits.
    """
    year = str(tax_data.get("tax_year", "2025"))
    return tax_data.get("states", {}).get("CA", {}).get(year, {}).get("nonconformity", {})


def constant(tax_data: Dict[str, Any], *path: str, **kwargs) -> Any:
    """`utils.get_constant` bound to the active year's data — fail loudly on
    an unverified (null) figure rather than returning a guess (ground rule 1).
    """
    return get_constant(tax_data, *path, **kwargs)


def filing_status_of(profile: Dict[str, Any]) -> str:
    return normalize_filing_status(profile.get("filing_status", "single"))