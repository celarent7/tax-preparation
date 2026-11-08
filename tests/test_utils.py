#!/usr/bin/env python3
"""Tests for scripts/utils.py — the shared constants/normalization layer (task P1).

Covers audit findings:
  D2 — filing-status alias normalization, no silent fallback to "single"
  Y2/Y5 — year support derived from the data, not hard-coded per script
  Ground rule 1 — reading an unverified (null) statutory figure must raise
"""

import argparse
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from utils import (  # noqa: E402
    CANONICAL_FILING_STATUSES,
    SUPPORTED_YEARS,
    UnverifiedConstantError,
    get_brackets,
    get_constant,
    get_status_value,
    get_supported_years,
    load_state_constants,
    load_tax_constants,
    make_year_arg,
    normalize_filing_status,
)


# ---------------------------------------------------------------------------
# D2 — filing status normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("alias,expected", [
    ("single", "single"),
    ("SINGLE", "single"),
    ("mfj", "married_jointly"),
    ("MFJ", "married_jointly"),
    ("married_filing_jointly", "married_jointly"),
    ("married-filing-jointly", "married_jointly"),
    ("married filing jointly", "married_jointly"),
    ("joint", "married_jointly"),
    ("mfs", "married_separately"),
    ("married_filing_separately", "married_separately"),
    ("hoh", "head_of_household"),
    ("head of household", "head_of_household"),
    ("qss", "qualifying_surviving_spouse"),
    ("qualifying_widow", "qualifying_surviving_spouse"),
])
def test_normalize_filing_status_aliases(alias, expected):
    """D2: every documented alias maps to its canonical filing status."""
    assert normalize_filing_status(alias) == expected


@pytest.mark.parametrize("bad", ["", "  ", "marreid", "mfjj", "trust", "single_ish", None])
def test_normalize_filing_status_rejects_unknown(bad):
    """D2: an unrecognized status raises — it must never fall back to 'single'.

    The original bug: `.get(status, brackets["single"])` meant a typo or an
    unhandled-but-valid status silently produced single-filer tax.
    """
    with pytest.raises(ValueError):
        normalize_filing_status(bad)


def test_all_canonical_statuses_normalize_to_themselves():
    """D2: canonical forms are accepted verbatim (idempotent)."""
    for status in CANONICAL_FILING_STATUSES:
        assert normalize_filing_status(status) == status


def test_get_brackets_rejects_unknown_status_instead_of_defaulting():
    """D2: bracket lookup raises for an unknown status rather than using single."""
    data = load_tax_constants("2025")
    with pytest.raises(ValueError):
        get_brackets(data, "not_a_status")


def test_get_brackets_mfj_differs_from_single():
    """D2 regression: mfj must resolve to the MFJ table, not the single table."""
    data = load_tax_constants("2025")
    assert get_brackets(data, "mfj") == get_brackets(data, "married_jointly")
    assert get_brackets(data, "mfj") != get_brackets(data, "single")


def test_get_status_value_raises_on_unknown_status():
    """D2/F12: per-status scalars must not silently default to another status."""
    data = load_tax_constants("2025")
    with pytest.raises(ValueError):
        get_status_value(data["standard_deductions"], "bogus", "standard deduction")


def test_get_status_value_returns_correct_status_amount():
    """D2: mfj alias resolves to the MFJ standard deduction, not single's."""
    data = load_tax_constants("2025")
    sd = data["standard_deductions"]
    assert get_status_value(sd, "mfj", "standard deduction") == sd["married_jointly"]
    assert get_status_value(sd, "mfj", "standard deduction") != sd["single"]


# ---------------------------------------------------------------------------
# Y2/Y5 — year handling
# ---------------------------------------------------------------------------

def test_supported_years_matches_year_blocks():
    """Y2: declared supported_years and actual year blocks stay in sync."""
    years = get_supported_years()
    assert years, "constants file must declare at least one tax year"
    assert years == sorted(years)
    for year in years:
        data = load_tax_constants(year)
        assert str(data["tax_year"]) == year


def test_load_tax_constants_rejects_unsupported_year():
    """Y5: an unsupported year raises instead of quietly using another year."""
    with pytest.raises(ValueError):
        load_tax_constants("1999")


def test_make_year_arg_choices_track_constants():
    """Y2/Y5: --year choices are derived from the data, never hard-coded."""
    parser = argparse.ArgumentParser()
    make_year_arg(parser)
    args = parser.parse_args([])
    assert args.year in SUPPORTED_YEARS

    for year in SUPPORTED_YEARS:
        assert parser.parse_args(["--year", year]).year == year

    with pytest.raises(SystemExit):
        parser.parse_args(["--year", "1999"])


# ---------------------------------------------------------------------------
# Ground rule 1 — unverified constants must raise, never return a guess
# ---------------------------------------------------------------------------

def test_get_constant_reads_nested_value():
    data = load_tax_constants("2025")
    assert get_constant(data, "se_tax", "se_adjustment_factor") == 0.9235


def test_get_constant_raises_on_null_value():
    """Ground rule 1: a null (unverified) figure must raise, not return None."""
    data = {"salt": {"cap": None}}
    with pytest.raises(UnverifiedConstantError) as exc:
        get_constant(data, "salt", "cap")
    assert "salt.cap" in str(exc.value)


def test_get_constant_allows_semantic_null_when_requested():
    """A null that means something (CA SDI 'no wage cap') is opt-in only."""
    data = {"sdi": {"wage_cap": None}}
    assert get_constant(data, "sdi", "wage_cap", allow_none=True) is None


def test_get_constant_missing_path_raises_without_default():
    data = load_tax_constants("2025")
    with pytest.raises(KeyError):
        get_constant(data, "nope", "missing")
    assert get_constant(data, "nope", "missing", default=42) == 42


# ---------------------------------------------------------------------------
# State constants
# ---------------------------------------------------------------------------

def test_load_state_constants_rejects_unknown_state():
    with pytest.raises(ValueError):
        load_state_constants("ZZ", "2025")


def test_load_state_constants_requires_state():
    with pytest.raises(ValueError):
        load_state_constants("", "2025")


# ---------------------------------------------------------------------------
# One configured default tax year (plan CC Task 1)
# ---------------------------------------------------------------------------

import utils as _utils  # noqa: E402


def test_default_tax_year_comes_from_constants():
    raw = _utils.load_raw_constants()
    assert _utils.DEFAULT_TAX_YEAR == raw["default_tax_year"]
    assert _utils.DEFAULT_TAX_YEAR in SUPPORTED_YEARS


def test_default_tax_year_outside_supported_years_raises(monkeypatch):
    monkeypatch.setattr(
        _utils,
        "load_raw_constants",
        lambda: {"supported_years": ["2025"], "2025": {}, "default_tax_year": "2031"},
    )
    with pytest.raises(ValueError, match="2031"):
        _utils.get_default_tax_year()


def test_make_year_arg_rejects_unsupported_default():
    with pytest.raises(ValueError):
        make_year_arg(argparse.ArgumentParser(), default="1999")


def test_load_tax_constants_without_year_uses_default():
    assert load_tax_constants()["tax_year"] == int(_utils.DEFAULT_TAX_YEAR)
