#!/usr/bin/env python3
"""
Utility functions for the tax preparation plugin.
Handles centralized loading of tax constants, filing-status normalization,
and path resolution.

Design rules (see docs/audit_improvement_blueprint.md):
  * `tax_constants.json` is the single source of truth for every statutory
    figure. Scripts must never hard-code rates, caps, thresholds or due dates.
  * Unknown filing status / year / state raises a clear error. There are no
    silent fallbacks to "single" (audit finding D2).
  * A statutory figure that could not be verified against a primary source is
    stored as `null` and listed under `_sources.TODO_VERIFY`. Reading it via
    `get_constant` raises `UnverifiedConstantError` rather than returning a
    guess (ground rule 1).
"""

import argparse
import json
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UnverifiedConstantError(RuntimeError):
    """Raised when a script reads a statutory figure that has no verified value.

    The constants file stores such figures as `null` with a matching entry in
    `_sources.TODO_VERIFY`. Returning a guess would silently produce a wrong
    tax result, so reading one is a hard error.
    """


# ---------------------------------------------------------------------------
# Filing status normalization (audit finding D2)
# ---------------------------------------------------------------------------

CANONICAL_FILING_STATUSES = (
    "single",
    "married_jointly",
    "married_separately",
    "head_of_household",
    "qualifying_surviving_spouse",
)

#: Accepted aliases -> canonical filing status. Keys are compared lowercased
#: with spaces/hyphens collapsed to underscores.
FILING_STATUS_ALIASES = {
    "single": "single",
    "s": "single",
    "unmarried": "single",
    "mfj": "married_jointly",
    "married_jointly": "married_jointly",
    "married_filing_jointly": "married_jointly",
    "joint": "married_jointly",
    "married": "married_jointly",
    "mfs": "married_separately",
    "married_separately": "married_separately",
    "married_filing_separately": "married_separately",
    "separate": "married_separately",
    "hoh": "head_of_household",
    "head_of_household": "head_of_household",
    "head_of_house": "head_of_household",
    "qss": "qualifying_surviving_spouse",
    "qw": "qualifying_surviving_spouse",
    "qualifying_surviving_spouse": "qualifying_surviving_spouse",
    "qualifying_widow": "qualifying_surviving_spouse",
    "qualifying_widower": "qualifying_surviving_spouse",
    "widow": "qualifying_surviving_spouse",
}


def normalize_filing_status(status: str) -> str:
    """Map a user-supplied filing status onto its canonical form.

    Raises ValueError on anything unrecognized. Never falls back to "single" —
    a silent fallback previously made `--filing-status mfj` compute the single
    standard deduction, understating the deduction by $15,750 (finding D2).
    """
    if status is None:
        raise ValueError(
            "Filing status is required. Must be one of: "
            + ", ".join(CANONICAL_FILING_STATUSES)
        )

    key = str(status).strip().lower().replace("-", "_").replace(" ", "_")
    while "__" in key:
        key = key.replace("__", "_")

    if key in FILING_STATUS_ALIASES:
        return FILING_STATUS_ALIASES[key]

    raise ValueError(
        f"Unknown filing status {status!r}. Must be one of: "
        + ", ".join(CANONICAL_FILING_STATUSES)
        + " (aliases: single, mfj, mfs, hoh, qss)"
    )


# ---------------------------------------------------------------------------
# Paths & raw data
# ---------------------------------------------------------------------------


def get_plugin_root() -> str:
    """Get the root directory of the plugin."""
    # Since this file is in scripts/, the root is the parent directory
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_constants_path() -> str:
    """Absolute path to the centralized constants file."""
    return os.path.join(get_plugin_root(), "scripts", "tax_constants.json")


def load_raw_constants() -> Dict[str, Any]:
    """Load the whole constants document without per-year processing."""
    with open(get_constants_path(), "r") as f:
        return json.load(f)


def _year_blocks(full_data: Dict[str, Any]) -> List[str]:
    """Top-level keys that look like a tax year, sorted ascending."""
    return sorted(k for k in full_data if k.isdigit() and len(k) == 4)


def get_supported_years() -> List[str]:
    """Tax years the constants file actually carries data for.

    Cross-checks the declared `supported_years` array against the year blocks
    present so the two cannot drift apart unnoticed (finding Y2/Y5).
    """
    full_data = load_raw_constants()
    present = _year_blocks(full_data)
    declared = full_data.get("supported_years")

    if declared is None:
        return present

    if sorted(declared) != present:
        raise ValueError(
            "tax_constants.json is inconsistent: supported_years="
            f"{sorted(declared)} but year blocks present are {present}. "
            "Update both together."
        )
    return list(declared)


#: Years available for `--year`. Read once at import for argparse `choices`.
SUPPORTED_YEARS: List[str] = get_supported_years()


def make_year_arg(parser: argparse.ArgumentParser, default: str = "2025") -> None:
    """Register a `--year` argument whose choices track the constants file.

    Using this everywhere prevents the class of bug where a year is added to
    the data but a script's hard-coded `choices` list still rejects it, or
    where a script silently falls through to another year's figures (Y5).
    """
    if default not in SUPPORTED_YEARS:
        default = SUPPORTED_YEARS[-1]
    parser.add_argument(
        "--year",
        type=str,
        default=default,
        choices=SUPPORTED_YEARS,
        help=f"Tax year (supported: {', '.join(SUPPORTED_YEARS)})",
    )


# ---------------------------------------------------------------------------
# Verified-value accessor (ground rule 1)
# ---------------------------------------------------------------------------


def get_constant(
    data: Dict[str, Any],
    *path: str,
    allow_none: bool = False,
    default: Any = ...,
) -> Any:
    """Read a statutory figure from a constants mapping, failing loudly.

    Args:
        data: a constants mapping (typically the result of load_tax_constants).
        *path: successive keys to walk, e.g. ("salt", "cap").
        allow_none: pass True only where `null` is semantically meaningful
            (e.g. CA SDI `wage_cap: null` means "no wage cap"), not where it
            means "we could not verify this".
        default: returned when the path is absent. Omit to make absence an error.

    Raises:
        UnverifiedConstantError: the figure resolves to `null` and `allow_none`
            is False — i.e. it is an unverified placeholder.
        KeyError: the path does not exist and no `default` was supplied.
    """
    node: Any = data
    walked: List[str] = []
    for key in path:
        walked.append(key)
        if not isinstance(node, dict) or key not in node:
            if default is not ...:
                return default
            raise KeyError(
                f"Constant {'.'.join(path)!r} not found in tax constants "
                f"(missing at {'.'.join(walked)!r})."
            )
        node = node[key]

    if node is None and not allow_none:
        dotted = ".".join(path)
        note = _todo_verify_note(dotted)
        raise UnverifiedConstantError(
            f"Statutory figure {dotted!r} has no verified value for this tax "
            f"year. {note} Supply a value verified against a primary source "
            "(IRS Rev. Proc. / form instructions, or FTB for California) in "
            "scripts/tax_constants.json before relying on this calculation."
        )
    return node


def _todo_verify_note(dotted_path: str) -> str:
    """Human-readable pointer to the matching `_sources.TODO_VERIFY` entry."""
    try:
        todos = load_raw_constants().get("_sources", {}).get("TODO_VERIFY", {})
    except Exception:  # pragma: no cover - constants unreadable is reported elsewhere
        return ""

    for key, note in todos.items():
        if dotted_path.endswith(key) or key.endswith(dotted_path):
            return f"Recorded as needing verification: {note}."
    return "It is listed as null in scripts/tax_constants.json."


# ---------------------------------------------------------------------------
# Per-year federal constants
# ---------------------------------------------------------------------------


def _process_brackets(bracket_list: Sequence[Dict[str, Any]]):
    """Turn [{"top": x, "rate": r}] into [(x_or_inf, r)] tuples."""
    return [
        (b["top"] if b["top"] is not None else float("inf"), b["rate"])
        for b in bracket_list
    ]


def load_tax_constants(year: str = "2025") -> Dict[str, Any]:
    """Load tax constants for a specific year from the centralized JSON file."""
    full_data = load_raw_constants()
    year = str(year)

    if year not in full_data or not year.isdigit():
        supported = ", ".join(_year_blocks(full_data))
        raise ValueError(
            f"Tax data for year {year} not found in constants file. "
            f"Supported years: {supported}."
        )

    data = full_data[year]

    # Process Federal Income Tax brackets
    data["tax_brackets_processed"] = {
        status: _process_brackets(bracket_list)
        for status, bracket_list in data["tax_brackets"].items()
    }

    # Process LTCG brackets
    data["ltcg_brackets_processed"] = {
        status: _process_brackets(bracket_list)
        for status, bracket_list in data["ltcg_brackets"].items()
    }

    # Include shared constants (not specific to a year, or mapped from shared)
    data["state_tax_rates"] = full_data.get("state_tax_rates", {})
    data["filing_status_map"] = full_data.get("filing_status_map", {})
    data["states"] = full_data.get("states", {})
    data["_sources"] = full_data.get("_sources", {})
    # Statutory, non-inflation-indexed credit parameters shared by every year.
    data["credits_static"] = full_data.get("credits_static", {})

    return data


def select_for_status(mapping: Dict[str, Any], filing_status: str, label: str = "table") -> Any:
    """Pick a per-filing-status entry from a mapping, or raise.

    The single primitive that replaces every `.get(status, mapping["single"])`
    silent fallback (finding D2). Callers that hold a bare table (rather than
    the whole constants dict) use this directly.
    """
    status = normalize_filing_status(filing_status)
    if status not in mapping:
        raise ValueError(
            f"No {label} defined for filing status {status!r}. "
            f"Available: {', '.join(sorted(mapping))}."
        )
    return mapping[status]


def get_brackets(tax_data: Dict[str, Any], filing_status: str, key: str = "tax_brackets_processed"):
    """Fetch a processed bracket table for a filing status, or raise.

    Replaces the `.get(status, brackets["single"])` pattern that silently
    computed the wrong tax for valid-but-unhandled statuses (finding D2).
    """
    label = f"{key.replace('_processed', '')} (tax year {tax_data.get('tax_year')})"
    return select_for_status(tax_data[key], filing_status, label)


def get_status_value(mapping: Dict[str, Any], filing_status: str, label: str = "value") -> Any:
    """Look up a per-filing-status scalar (standard deduction, threshold, ...).

    Raises rather than defaulting, so a missing status can never be papered
    over with another status's figure (findings D2, F12).
    """
    status = normalize_filing_status(filing_status)
    value = select_for_status(mapping, status, label)
    if value is None:
        raise UnverifiedConstantError(
            f"The {label} for filing status {status!r} has no verified value. "
            f"{_todo_verify_note(label)}"
        )
    return value


# ---------------------------------------------------------------------------
# State constants
# ---------------------------------------------------------------------------


def _as_date(d) -> date:
    """Coerce a date or datetime to a plain date."""
    return d.date() if isinstance(d, datetime) else d


def add_years(d, years: int) -> date:
    """Add calendar years to a date, handling Feb 29 on non-leap years.

    Feb 29 + 1 year lands on Mar 1 of the following year, which is the
    conservative reading: the holding period is not satisfied until the day
    after the anniversary, and Feb 29 has no anniversary in a common year.
    """
    d = _as_date(d)
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        # Feb 29 -> Mar 1 in a non-leap year.
        return d.replace(year=d.year + years, month=3, day=1)


def is_long_term(acquisition_date, sale_date, years: int = 1) -> bool:
    """True when the holding period EXCEEDS `years` calendar years.

    IRS Pub 544: "To determine how long you held the investment property, begin
    counting on the date after the day you acquired the property. The day you
    disposed of the property is part of your holding period." Property held for
    exactly one year is SHORT term; you must hold for one year *and one day*.

    Measured by calendar anniversary, not a fixed day count. A fixed `> 365`
    test misclassifies any holding period that spans a leap day, because such a
    span reaches its anniversary at 366 days (finding F10).
    """
    return _as_date(sale_date) > add_years(acquisition_date, years)


def additional_medicare_threshold(filing_status: str, se_tax_data: Dict[str, Any]) -> float:
    """Additional Medicare wage threshold for a filing status.

    IRC 3101(b)(2) / IRS Topic 560: $250,000 joint, $125,000 MFS, $200,000 "in
    any other case" — which includes head of household AND qualifying surviving
    spouse (QSS uses joint *rates* but is not a joint return, so it takes the
    $200,000 threshold). Not inflation-indexed.

    Raises when a status has no threshold defined rather than defaulting to the
    single-filer $200,000 (findings D2/F12).
    """
    status = normalize_filing_status(filing_status)
    key_by_status = {
        "single": "additional_medicare_threshold_single",
        "married_jointly": "additional_medicare_threshold_married",
        "married_separately": "additional_medicare_threshold_married_separately",
        "head_of_household": "additional_medicare_threshold_head_of_household",
        "qualifying_surviving_spouse": "additional_medicare_threshold_qualifying_surviving_spouse",
    }
    key = key_by_status[status]
    if key not in se_tax_data or se_tax_data[key] is None:
        raise ValueError(
            f"No Additional Medicare threshold defined for filing status "
            f"{status!r} (expected key {key!r} in se_tax constants)."
        )
    return se_tax_data[key]


def load_state_constants(state: str, year: str) -> Dict[str, Any]:
    """Load a state's constants for a year, with clear errors when absent.

    Returns the per-year mapping for the state, augmented with "type" and
    any state-level (non-year-specific) keys.
    """
    if not state:
        raise ValueError("State is required (e.g. CA, WA, TX).")

    code = str(state).strip().upper()
    year = str(year)
    full_data = load_raw_constants()
    states = full_data.get("states", {})

    if code not in states:
        raise ValueError(
            f"No state constants for {code!r}. Configured states: "
            f"{', '.join(sorted(states)) or '(none)'}. Add a block under "
            "'states' in scripts/tax_constants.json."
        )

    state_block = dict(states[code])
    state_type = state_block.get("type")

    # States with no income tax carry no per-year bracket data.
    if state_type == "none":
        return state_block

    if year not in state_block:
        available = ", ".join(sorted(k for k in state_block if k.isdigit())) or "(none)"
        raise ValueError(
            f"No {code} tax data for year {year}. Years available for {code}: "
            f"{available}."
        )

    year_block = dict(state_block[year])
    year_block["type"] = state_type
    year_block["state"] = code
    year_block["year"] = year
    return year_block
