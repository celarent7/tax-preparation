#!/usr/bin/env python3
"""Core tests for the Phase 0 statement parser: coverage, continuity, merge, FX.

These pin the arithmetic the readers feed. Everything here is currency- and
institution-neutral: the shapes came from the corpus (docs/statement_parser_plan.md
§4), but nothing below depends on a real file.
"""

import os
import sys
from datetime import date
from decimal import Decimal

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from statement_parser import (  # noqa: E402
    MovementRecord,
    StatementPeriod,
    clip_periods,
    detect_continuity_breaks,
    detect_gaps,
    merge_spans,
    minor_unit,
    period_endpoints,
)


def movement(day, balance, *, amount=None, currency="USD", source_file="f.xlsx",
             row=1, order=0, description=""):
    return MovementRecord(
        date=day, description=description, currency=currency,
        source_file=source_file, source_row=row, row_order=order,
        amount=amount if amount is None else Decimal(amount),
        balance_after=balance if balance is None else Decimal(balance),
    )


def period(start, end, source_file):
    return StatementPeriod(start, end, source_file)


# ---------------------------------------------------------------------------
# 9.5 — period gaps, including the two that are live in the corpus
# ---------------------------------------------------------------------------

def test_full_year_coverage_has_no_gaps():
    periods = [period(date(2025, 1, 1), date(2025, 12, 31), "year.xlsx")]
    assert detect_gaps(periods, 2025) == []


def test_monthly_periods_running_22nd_to_21st_leave_december_uncovered():
    """T-Bank's periods run the 22nd to the 21st, so twelve files end at
    December 21 and December 22-31 is uncovered. The gap is at the END of the
    year, which is exactly where it matters most."""
    periods = [period(date(2024, 12, 22), date(2025, 1, 21), "01.txt")]
    for month in range(1, 12):
        periods.append(period(date(2025, month, 22),
                              date(2025, month + 1, 21), f"{month + 1:02d}.txt"))
    gaps = detect_gaps(periods, 2025)
    assert len(gaps) == 1
    assert gaps[0]["position"] == "year_end"
    assert (gaps[0]["start"], gaps[0]["end"]) == ("2025-12-22", "2025-12-31")
    assert gaps[0]["days"] == 10


def test_a_missing_december_statement_is_a_year_end_gap():
    """One card lacks December, the other October through December. December is
    the month that would establish the year-end balance."""
    import calendar

    def billing_day(year, month, day=30):
        """The billing cycle closes on the 30th, or the last day of a short
        month — February is why a `date(2025, 2, 30)` cannot be assumed."""
        return date(year, month, min(day, calendar.monthrange(year, month)[1]))

    periods = [period(date(2024, 12, 30), billing_day(2025, 1), "jan.xlsx")]
    for month in range(1, 11):
        periods.append(period(billing_day(2025, month),
                              billing_day(2025, month + 1), f"{month + 1:02d}.xlsx"))
    gaps = detect_gaps(periods, 2025)
    assert [g["position"] for g in gaps] == ["year_end"]
    assert gaps[0]["start"] == "2025-12-01"


def test_a_missing_middle_statement_is_a_gap_between_periods():
    periods = [
        period(date(2025, 1, 1), date(2025, 3, 31), "q1.xlsx"),
        # Q2 missing
        period(date(2025, 7, 1), date(2025, 12, 31), "h2.xlsx"),
    ]
    gaps = detect_gaps(periods, 2025)
    assert [g["position"] for g in gaps] == ["between_periods"]
    assert (gaps[0]["start"], gaps[0]["end"]) == ("2025-04-01", "2025-06-30")


def test_no_statements_at_all_is_the_whole_year():
    """BCS: one workpaper, zero statement files."""
    gaps = detect_gaps([], 2025)
    assert [g["position"] for g in gaps] == ["whole_year"]
    assert gaps[0]["days"] == 365


def test_evidence_from_outside_the_year_is_not_coverage():
    """The savings Q1 statement opens 2024/12/31 and the January card period
    opens 30/12/2024. Those days are read, but they are not 2025 coverage."""
    spans = clip_periods([period(date(2024, 12, 30), date(2025, 1, 30), "jan.xlsx"),
                          period(date(2024, 1, 1), date(2024, 12, 31), "prior.xlsx")],
                         2025)
    assert spans == [(date(2025, 1, 1), date(2025, 1, 30))]


def test_adjacent_and_overlapping_spans_merge():
    merged = merge_spans([
        (date(2025, 1, 1), date(2025, 1, 31)),
        (date(2025, 2, 1), date(2025, 2, 28)),   # day-adjacent
        (date(2025, 2, 15), date(2025, 3, 31)),  # overlapping
        (date(2025, 6, 1), date(2025, 6, 30)),   # disjoint
    ])
    assert merged == [(date(2025, 1, 1), date(2025, 3, 31)),
                      (date(2025, 6, 1), date(2025, 6, 30))]


def test_gaps_are_never_extrapolated_away():
    """A gap is a fact about the evidence. Filling it would be the parser
    deciding what the filing claims."""
    periods = [period(date(2025, 1, 1), date(2025, 1, 31), "jan.xlsx"),
               period(date(2025, 12, 1), date(2025, 12, 31), "dec.xlsx")]
    gaps = detect_gaps(periods, 2025)
    assert sum(g["days"] for g in gaps) == 365 - 31 - 31


# ---------------------------------------------------------------------------
# 9.8 — continuity breaks, and why exact equality is the wrong test
# ---------------------------------------------------------------------------

JAN = period(date(2025, 1, 1), date(2025, 1, 31), "jan.xlsx")
FEB = period(date(2025, 2, 1), date(2025, 2, 28), "feb.xlsx")


def two_period_movements(jan_close, feb_open_after, feb_amount):
    """January closes at `jan_close`; February's first row lands at
    `feb_open_after` having moved `feb_amount`, so it OPENS at the difference."""
    return [
        movement(date(2025, 1, 5), "100.00", amount="100.00",
                 source_file="jan.xlsx", row=2, order=0),
        movement(date(2025, 1, 20), jan_close, amount="10.00",
                 source_file="jan.xlsx", row=3, order=1),
        movement(date(2025, 2, 3), feb_open_after, amount=feb_amount,
                 source_file="feb.xlsx", row=2, order=0),
    ]


def test_opening_balance_is_before_the_first_movement_not_after_it():
    """The first row's `balance_after` is the level AFTER the first movement.
    Treating it as the opening balance manufactures a break on every file."""
    ends = period_endpoints(two_period_movements("110.00", "160.00", "50.00"),
                            FEB, "USD")
    assert ends["opening"] == Decimal("110.00")
    assert ends["closing"] == Decimal("160.00")


def test_contiguous_periods_that_disagree_are_a_break():
    """`closing[n] != opening[n+1]` means a missing statement or a mis-parse."""
    findings = detect_continuity_breaks(
        two_period_movements("110.00", "200.00", "50.00"), [JAN, FEB])
    assert [f["kind"] for f in findings] == ["break"]
    assert findings[0]["severity"] == "warn"
    assert findings[0]["delta"] == "40.00"


def test_a_difference_within_one_minor_unit_reconciles_rather_than_firing():
    """Displayed balances are rounded to 2dp and interest can post between a
    closing and the next opening. A bare `!=` fires on noise and then gets
    ignored — which is how a real break slips through."""
    findings = detect_continuity_breaks(
        two_period_movements("110.00", "160.01", "50.00"), [JAN, FEB])
    assert [f["kind"] for f in findings] == ["reconciles"]
    assert findings[0]["severity"] == "info"


def test_exactly_one_minor_unit_is_still_within_tolerance():
    findings = detect_continuity_breaks(
        two_period_movements("110.00", "160.01", "50.00"), [JAN, FEB])
    assert Decimal(findings[0]["delta"]) == minor_unit("USD")
    assert findings[0]["kind"] == "reconciles"


def test_more_than_one_minor_unit_is_a_break():
    findings = detect_continuity_breaks(
        two_period_movements("110.00", "160.02", "50.00"), [JAN, FEB])
    assert findings[0]["kind"] == "break"


def test_a_zero_decimal_currency_gets_a_whole_unit_of_tolerance():
    assert minor_unit("JPY") == Decimal("1")
    assert minor_unit("jpy") == Decimal("1")
    assert minor_unit("COP") == Decimal("0.01")


def test_balances_across_a_gap_are_not_comparable():
    """The gap is the finding, not the difference: across uncovered days the
    balance was free to move unobserved."""
    march = period(date(2025, 3, 1), date(2025, 3, 31), "mar.xlsx")
    movements = [
        movement(date(2025, 1, 20), "110.00", amount="10.00",
                 source_file="jan.xlsx", row=3, order=0),
        movement(date(2025, 3, 3), "900.00", amount="50.00",
                 source_file="mar.xlsx", row=2, order=0),
    ]
    findings = detect_continuity_breaks(movements, [JAN, march])
    assert [f["kind"] for f in findings] == ["not_comparable"]
    assert findings[0]["severity"] == "info"
    assert findings[0]["gap_days"] == 28


def test_continuity_is_checked_per_currency():
    """A card carries DOLARES and PESOS for the same card. Comparing a USD
    closing against a COP opening would be nonsense."""
    movements = [
        movement(date(2025, 1, 20), "110.00", amount="10.00", currency="USD",
                 source_file="jan.xlsx", row=3, order=0),
        movement(date(2025, 1, 21), "500000.00", amount="1000.00", currency="COP",
                 source_file="jan.xlsx", row=4, order=1),
        movement(date(2025, 2, 3), "160.00", amount="50.00", currency="USD",
                 source_file="feb.xlsx", row=2, order=0),
        movement(date(2025, 2, 4), "502000.00", amount="2000.00", currency="COP",
                 source_file="feb.xlsx", row=3, order=1),
    ]
    findings = detect_continuity_breaks(movements, [JAN, FEB])
    assert sorted(f["currency"] for f in findings) == ["COP", "USD"]
    assert all(f["kind"] == "reconciles" for f in findings)


def test_a_period_whose_rows_state_no_balance_is_skipped_not_guessed():
    """A ledger without balances yields changes, not levels. It has no
    endpoints to compare, and inventing one would assert a level."""
    movements = [
        movement(date(2025, 1, 20), None, amount="10.00",
                 source_file="jan.xlsx", row=3, order=0),
        movement(date(2025, 2, 3), "160.00", amount="50.00",
                 source_file="feb.xlsx", row=2, order=0),
    ]
    assert period_endpoints(movements, JAN, "USD") is None
    assert detect_continuity_breaks(movements, [JAN, FEB]) == []


def test_a_single_period_has_nothing_to_compare_against():
    findings = detect_continuity_breaks(
        [movement(date(2025, 1, 5), "100.00", amount="100.00",
                  source_file="jan.xlsx", row=2, order=0)], [JAN])
    assert findings == []


def test_findings_name_both_files_so_a_break_can_be_traced():
    findings = detect_continuity_breaks(
        two_period_movements("110.00", "200.00", "50.00"), [JAN, FEB])
    assert findings[0]["source_files"] == ["jan.xlsx", "feb.xlsx"]
    assert "jan.xlsx" in findings[0]["message"]
