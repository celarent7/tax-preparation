#!/usr/bin/env python3
"""Account-number normalization for FBAR year-over-year matching.

Covers docs/fbar_module_plan.md §7.1 and defect D5: one brokerage account was
recorded on the filed form and in the workpaper with two renderings of the same
identifier — a trailing Latin "M" against a Cyrillic "м", plus different
punctuation. Byte equality calls that one closed account plus one new account,
which is precisely the false reconciliation this module exists to prevent.

The synthetic pair `12345678M` / `123456/78-м` preserves the Latin-M /
Cyrillic-м property of the real pair without carrying a real account number.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from fbar_schema import (  # noqa: E402
    RosterError,
    assert_display_safe,
    normalize_account_number,
)


# ---------------------------------------------------------------------------
# D5 — the motivating case
# ---------------------------------------------------------------------------


def test_normalize_homoglyph():
    """D5: Latin M and Cyrillic м, with differing punctuation, are one account."""
    assert normalize_account_number("12345678M") == "12345678M"
    assert normalize_account_number("123456/78-м") == "12345678M"
    assert normalize_account_number("12345678M") == normalize_account_number("123456/78-м")


def test_lowercase_cyrillic_folds():
    """The fold table is uppercase-only, so casing must be applied FIRST.

    A table of only uppercase pairs applied before upper-casing would leave
    the lowercase Cyrillic "м" untouched and fail D5 outright.
    """
    assert normalize_account_number("м") == "M"
    assert normalize_account_number("а1") == "A1"


@pytest.mark.parametrize("cyrillic,latin", [
    ("А", "A"), ("В", "B"), ("Е", "E"), ("К", "K"), ("М", "M"), ("Н", "H"),
    ("О", "O"), ("Р", "P"), ("С", "C"), ("Т", "T"), ("У", "Y"), ("Х", "X"),
])
def test_cyrillic_capitals_fold_to_latin(cyrillic, latin):
    assert normalize_account_number(cyrillic) == latin


@pytest.mark.parametrize("greek,latin", [
    ("Α", "A"), ("Β", "B"), ("Ε", "E"), ("Η", "H"), ("Κ", "K"), ("Μ", "M"),
    ("Ν", "N"), ("Ο", "O"), ("Ρ", "P"), ("Τ", "T"), ("Υ", "Y"), ("Χ", "X"),
])
def test_greek_capitals_fold_to_latin(greek, latin):
    assert normalize_account_number(greek) == latin


# ---------------------------------------------------------------------------
# Separators, width, invisible characters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [
    "1234 5678", "1234-5678", "1234.5678", "1234/5678",
    "1234#5678", "1234_5678", "(1234)5678", "1234,5678", "1234:5678",
])
def test_separators_are_stripped(raw):
    assert normalize_account_number(raw) == "12345678"


def test_fullwidth_digits_normalize_via_nfkc():
    assert normalize_account_number("１２３４") == "1234"


def test_zero_width_characters_are_removed():
    """Zero-width joiners survive NFKC and would otherwise defeat equality."""
    assert normalize_account_number("1234​5678") == "12345678"
    assert normalize_account_number("1234 5678") == "12345678"


def test_none_and_empty():
    assert normalize_account_number(None) == ""
    assert normalize_account_number("") == ""
    assert normalize_account_number("  -- ") == ""


def test_order_of_remaining_characters_is_preserved():
    assert normalize_account_number("ge-00/ex 12ab") == "GE00EX12AB"


# ---------------------------------------------------------------------------
# Deliberate non-folds
# ---------------------------------------------------------------------------


def test_letter_to_digit_confusables_are_not_folded():
    """Cyrillic З looks like 3 but is not folded — a false MERGE hides an account.

    A false split surfaces as a reviewable UNRESOLVED/NEW finding. A false
    merge makes two distinct accounts look like one and silently drops a
    reportable account off the filing, so the fold table is letter-to-letter
    only and this asymmetry is deliberate.
    """
    assert normalize_account_number("З") != normalize_account_number("3")


def test_distinct_numbers_stay_distinct():
    assert normalize_account_number("12345678") != normalize_account_number("87654321")


# ---------------------------------------------------------------------------
# The normalizer must never reach a form
# ---------------------------------------------------------------------------


def test_normalize_never_mutates_display():
    """Item 18 takes the number as the institution states it — round-trip intact."""
    account = {"account_key": "k", "account_number": "123456/78-м"}
    assert_display_safe(account["account_number"], account)


def test_display_guard_rejects_the_normalized_form():
    account = {"account_key": "k", "account_number": "123456/78-м"}
    normalized = normalize_account_number(account["account_number"])
    with pytest.raises(RosterError, match="as the institution states it"):
        assert_display_safe(normalized, account)


def test_contract_number_is_normalized_for_matching_only():
    from fbar_schema import validate_roster
    r = validate_roster({
        "schema_version": "1.0", "tax_year": 2025, "filer": {"slug": "t"},
        "accounts": [{"account_key": "a", "account_number": "40817 810 0000 0012 3474",
                      "account_type": "bank", "contract_number": "5000-000-001"}],
    }, origin="<test>")
    acct = r["accounts"][0]
    assert acct["contract_number"] == "5000-000-001"          # as the institution states it
    assert acct["contract_number_normalized"] == "5000000001"  # matching key only

