#!/usr/bin/env python3
"""Reader-level tests for the Phase 0 statement parser.

Every case here was observed in the live TY2025 FBAR corpus and is catalogued
in docs/statement_parser_plan.md §9. They share one property that makes them
worth pinning: each produces a **plausible wrong number** rather than an error,
which is the failure class the parser exists to prevent.

Fixtures are synthetic — invented shapes, never a live statement with the
numbers edited, which is how PII survives into a public repository (§10.2).
"""

import os
import sys
from datetime import date
from decimal import Decimal

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from statement_parser import (  # noqa: E402
    COLOMBIAN_EXPORT_NUMBERS,
    PLAIN_NUMBERS,
    RUSSIAN_NUMBERS,
    MovementRecord,
    StatementParseError,
    StatementPeriod,
    parse_decimal,
    parse_full_date,
    resolve_partial_date,
    weaken_basis,
)


# ---------------------------------------------------------------------------
# 9.4 — locale-specific numbers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,fmt,expected", [
    # T-Bank: space thousands, comma decimal, ruble sign, explicit plus.
    ("+1 234,56 ₽", RUSSIAN_NUMBERS, Decimal("1234.56")),
    ("1 234,56 ₽", RUSSIAN_NUMBERS, Decimal("1234.56")),
    ("1 234,56 ₽", RUSSIAN_NUMBERS, Decimal("1234.56")),
    # Colombian export: comma thousands, dot decimal, leading-dot cents.
    ("1,234,567.89", COLOMBIAN_EXPORT_NUMBERS, Decimal("1234567.89")),
    (".00", COLOMBIAN_EXPORT_NUMBERS, Decimal("0.00")),
    # A currency code sits at an end, not between digits.
    ("USD 100.50", PLAIN_NUMBERS, Decimal("100.50")),
])
def test_locale_numbers_parse(raw, fmt, expected):
    assert parse_decimal(raw, fmt) == expected


def test_unicode_minus_is_negative():
    """9.4: T-Bank writes `Expenses: −N ₽` with U+2212, not an ASCII hyphen.

    Stripping "everything that is not a digit" turns this into a positive
    number — an expense that reads as income.
    """
    assert "−" != "-"
    assert parse_decimal("−1 234,56 ₽", RUSSIAN_NUMBERS) == Decimal("-1234.56")
    assert parse_decimal("-1 234,56 ₽", RUSSIAN_NUMBERS) == Decimal("-1234.56")


def test_accounting_parentheses_are_negative():
    assert parse_decimal("(1,234.56)", COLOMBIAN_EXPORT_NUMBERS) == Decimal("-1234.56")


def test_wrong_locale_is_an_error_not_a_guess():
    """A comma-decimal value read with a dot-decimal profile must not silently
    become a thousand times too large."""
    with pytest.raises(StatementParseError):
        parse_decimal("1,234,56", COLOMBIAN_EXPORT_NUMBERS)


def test_letter_between_digits_is_rejected():
    """Stripping letters wholesale turns `1O5` into `15`."""
    with pytest.raises(StatementParseError):
        parse_decimal("1O5", PLAIN_NUMBERS)


def test_non_numeric_cell_names_the_profile_rather_than_reading_as_absent():
    """A label in a money column means the column map is wrong, not that the
    balance is missing."""
    with pytest.raises(StatementParseError, match="no digits"):
        parse_decimal("n/a", PLAIN_NUMBERS)


# ---------------------------------------------------------------------------
# 9.12 — empty string is not None is not zero
# ---------------------------------------------------------------------------

def test_three_input_states_are_distinguished():
    """TBC writes `''` into the unused side of Paid Out / Paid In and `'0'` as
    a balance. Coercing `''` to `0` asserts an empty account."""
    assert parse_decimal(None, PLAIN_NUMBERS) is None
    assert parse_decimal("", PLAIN_NUMBERS) is None
    assert parse_decimal("   ", PLAIN_NUMBERS) is None
    assert parse_decimal("0", PLAIN_NUMBERS) == Decimal("0")
    assert parse_decimal(0, PLAIN_NUMBERS) == Decimal("0")
    assert parse_decimal("0,00", RUSSIAN_NUMBERS) == Decimal("0.00")
    # A written zero is a level; an absent cell is not.
    assert parse_decimal("0", PLAIN_NUMBERS) is not None


def test_bare_dash_is_absent_not_zero():
    assert parse_decimal("-", PLAIN_NUMBERS) is None
    assert parse_decimal("−", PLAIN_NUMBERS) is None


def test_floats_keep_the_displayed_value():
    """openpyxl hands back floats; `Decimal(float)` imports binary error."""
    assert parse_decimal(1234.56, PLAIN_NUMBERS) == Decimal("1234.56")


def test_booleans_are_not_money():
    with pytest.raises(StatementParseError):
        parse_decimal(True, PLAIN_NUMBERS)


# ---------------------------------------------------------------------------
# 9.2 — dates without years, and periods that cross a year boundary
# ---------------------------------------------------------------------------

def test_non_padded_day_month_resolves_within_the_period():
    """Bancolombia's FECHA is `D/MM` and is not zero-padded, so a fixed-width
    parse fails on `1/01` and `8/01`."""
    period = StatementPeriod(date(2025, 1, 1), date(2025, 3, 31), "q1.xlsx")
    assert resolve_partial_date("1/01", period) == date(2025, 1, 1)
    assert resolve_partial_date("8/01", period) == date(2025, 1, 8)
    assert resolve_partial_date("31/01", period) == date(2025, 1, 31)


def test_savings_q1_period_opens_in_the_prior_calendar_year():
    """The savings Q1 statement opens 2024/12/31 — real in the corpus, not a
    defensive hypothetical."""
    period = StatementPeriod(date(2024, 12, 31), date(2025, 3, 31), "q1.xlsx")
    assert resolve_partial_date("31/12", period) == date(2024, 12, 31)
    assert resolve_partial_date("1/01", period) == date(2025, 1, 1)


def test_card_january_period_opens_30_december_of_the_prior_year():
    """The card billing period runs ~30th -> ~30th, so January opens in 2024."""
    period = StatementPeriod(date(2024, 12, 30), date(2025, 1, 30), "jan.xlsx")
    assert resolve_partial_date("30/12", period) == date(2024, 12, 30)
    assert resolve_partial_date("2/01", period) == date(2025, 1, 2)


def test_impossible_day_month_in_every_candidate_year_raises():
    period = StatementPeriod(date(2025, 1, 1), date(2025, 12, 31), "y.xlsx")
    with pytest.raises(StatementParseError):
        resolve_partial_date("29/02", period)


def test_two_date_formats_in_one_file():
    """9.2: the savings header states the period as `YYYY/MM/DD` while the
    movement rows are `D/MM`. Locale rules bind per FIELD, not per profile."""
    period_start = parse_full_date("2024/12/31")
    period_end = parse_full_date("2025/03/31")
    period = StatementPeriod(period_start, period_end, "q1.xlsx")
    assert resolve_partial_date("9/01", period) == date(2025, 1, 9)


@pytest.mark.parametrize("raw,expected", [
    ("2025-12-31", date(2025, 12, 31)),
    ("2025/01/08", date(2025, 1, 8)),
    ("30/12/2024", date(2024, 12, 30)),
    ("22.11.2025", date(2025, 11, 22)),          # T-Bank summary line
    ("2025-01-08 00:00:00", date(2025, 1, 8)),   # openpyxl text datetime
])
def test_full_dates(raw, expected):
    assert parse_full_date(raw) == expected


def test_unreadable_date_raises_rather_than_defaulting():
    with pytest.raises(StatementParseError):
        parse_full_date("last Tuesday")


def test_period_end_before_start_is_rejected():
    with pytest.raises(StatementParseError):
        StatementPeriod(date(2025, 3, 31), date(2025, 1, 1), "backwards.xlsx")


# ---------------------------------------------------------------------------
# 9.10 / §8.1 — row order is the only intraday clock
# ---------------------------------------------------------------------------

def test_order_key_is_date_then_file_then_row():
    """A running-balance file gives one balance per ROW, so the sequence within
    a day lives in row order. TBC's every timestamp is 00:00:00, and same-date
    rows are real: Bancolombia savings has two `9/01` rows in Q1 alone."""
    first = MovementRecord(date=date(2025, 1, 9), description="a", currency="COP",
                           source_file="q1.xlsx", source_row=12,
                           amount=Decimal("10"), balance_after=Decimal("110"))
    second = MovementRecord(date=date(2025, 1, 9), description="b", currency="COP",
                            source_file="q1.xlsx", source_row=13,
                            amount=Decimal("-10"), balance_after=Decimal("100"))
    assert first.order_key() < second.order_key()
    assert sorted([second, first], key=MovementRecord.order_key) == [first, second]


# ---------------------------------------------------------------------------
# §6.3 — evidence can be capped, never promoted
# ---------------------------------------------------------------------------

def test_weaken_basis_only_ever_weakens():
    assert weaken_basis("statement_full_year", "statement_partial") == "statement_partial"
    assert weaken_basis("statement_partial", "statement_full_year") == "statement_partial"
    assert weaken_basis("period_end_summaries_only", "estimate") == "estimate"
    assert weaken_basis("unknown", "statement_full_year") == "unknown"


def test_weaken_basis_rejects_a_basis_the_schema_does_not_define():
    with pytest.raises(StatementParseError):
        weaken_basis("statement_full_year", "pretty_sure")


def test_parser_bases_are_exactly_the_schema_vocabulary():
    """The parser populates `fbar_schema.MAX_VALUE_BASES`; it must not invent
    a value the roster loader would reject."""
    from fbar_schema import MAX_VALUE_BASES
    from statement_parser import BASIS_STRENGTH

    assert set(BASIS_STRENGTH) == set(MAX_VALUE_BASES)
