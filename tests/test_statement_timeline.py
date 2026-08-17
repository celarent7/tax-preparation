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


# ===========================================================================
# §8 — multi-currency merge and FX conversion
# ===========================================================================

from statement_parser import (  # noqa: E402
    BOUND_AT_LEAST,
    BOUND_UNORDERED,
    StatementParseError,
    balance_at_year_end,
    build_combined_series,
    derive_max,
    to_usd,
)

# Invented rates, chosen so the arithmetic is checkable by hand.
RATES = {"EUR": Decimal("2"), "GEL": Decimal("4"), "COP": Decimal("4000")}


# ---------------------------------------------------------------------------
# FX direction
# ---------------------------------------------------------------------------

def test_usd_is_local_divided_by_units_per_usd_never_the_reciprocal():
    """`units_per_usd` is foreign units per ONE dollar. Storing or applying the
    reciprocal silently inverts every conversion for that currency — and for a
    rate near 1.0 the result still looks plausible."""
    assert to_usd(Decimal("100"), "EUR", RATES) == Decimal("50")
    assert to_usd(Decimal("100"), "EUR", RATES) != Decimal("200")
    assert to_usd(Decimal("100"), "USD", RATES) == Decimal("100")


def test_a_missing_rate_is_refused_rather_than_treated_as_one_to_one():
    with pytest.raises(StatementParseError, match="No exchange rate supplied for GBP"):
        to_usd(Decimal("100"), "GBP", RATES)


def test_a_non_positive_rate_is_refused():
    with pytest.raises(StatementParseError, match="must be positive"):
        to_usd(Decimal("100"), "EUR", {"EUR": Decimal("0")})


# ---------------------------------------------------------------------------
# Forward-fill: a currency absent from an event is unchanged, not zero
# ---------------------------------------------------------------------------

def test_a_currency_that_did_not_move_holds_its_level():
    """Step 2 of the merge is the one that is silently wrong if omitted."""
    movements = [
        movement(date(2025, 1, 5), "100", currency="USD", source_file="usd.xlsx", order=0),
        movement(date(2025, 2, 5), "200", currency="EUR", source_file="eur.xlsx", order=0),
        movement(date(2025, 3, 5), "150", currency="USD", source_file="usd.xlsx", order=1),
    ]
    points, _, currencies = build_combined_series(movements, RATES)
    assert currencies == ["EUR", "USD"]
    # March: USD moved to 150, EUR did not move and is still 200 (= $100).
    assert points[-1].components == {"USD": Decimal("150"), "EUR": Decimal("200")}
    assert points[-1].usd == Decimal("250")


def test_a_currency_before_its_first_observation_is_unknown_not_zero(  ):
    """TBC's GEL sheet has a SINGLE data row, in November. For January through
    October that currency's level is genuinely unknown: forward-fill has no
    prior value, back-filling asserts a level the file never states, and
    zero-filling asserts an empty wallet. A full-year statement with one
    transaction does not describe a zero-balance account — it describes an
    account whose opening balance was never exported.

    Both zero-fill and back-fill pass a naive test, so this is asserted
    explicitly."""
    movements = [
        movement(date(2025, 1, 5), "100", currency="USD", source_file="usd.xlsx", order=0),
        movement(date(2025, 11, 2), "400", currency="GEL", source_file="gel.xlsx", order=0),
    ]
    points, uncertainty, _ = build_combined_series(movements, RATES)

    january = points[0]
    assert january.unknown_currencies == ["GEL"]
    assert "GEL" not in january.components          # not back-filled
    assert january.usd == Decimal("100")            # not zero-filled into the sum
    assert january.is_bounded is True

    november = points[-1]
    assert november.unknown_currencies == []
    assert november.usd == Decimal("200")           # 100 USD + 400/4 GEL

    at_least = [u for u in uncertainty if u["kind"] == BOUND_AT_LEAST]
    assert [u["currency"] for u in at_least] == ["GEL"]
    assert at_least[0]["until"] == "2025-11-02"


def test_a_peak_inside_an_unknown_prefix_is_reported_as_at_least():
    """The peak falls in January, while GEL has still never been observed. The
    figure is a LOWER bound: the unobserved currency could only have added."""
    movements = [
        movement(date(2025, 1, 5), "1000", currency="USD", source_file="usd.xlsx", order=0),
        movement(date(2025, 6, 5), "100", currency="USD", source_file="usd.xlsx", order=1),
        movement(date(2025, 11, 2), "4", currency="GEL", source_file="gel.xlsx", order=0),
    ]
    points, _, _ = build_combined_series(movements, RATES)
    result = derive_max(points, is_liability=False)
    assert result["date_of_maximum"] == date(2025, 1, 5)
    assert result["max_usd"] == Decimal("1000")
    assert result["max_usd_bound"] == BOUND_AT_LEAST
    # Only the observed currency is a component; GEL is absent, not zero.
    assert result["max_components"] == {"USD": Decimal("1000")}


def test_a_peak_after_every_currency_is_observed_is_not_bounded():
    movements = [
        movement(date(2025, 1, 5), "100", currency="USD", source_file="usd.xlsx", order=0),
        movement(date(2025, 11, 2), "4", currency="GEL", source_file="gel.xlsx", order=0),
    ]
    points, _, _ = build_combined_series(movements, RATES)
    result = derive_max(points, is_liability=False)
    assert result["date_of_maximum"] == date(2025, 11, 2)
    assert result["max_usd"] == Decimal("101")
    assert result["max_usd_bound"] is None


# ---------------------------------------------------------------------------
# The property that pins §8: max of the merged series != sum of maxima
# ---------------------------------------------------------------------------

def test_the_maximum_is_not_the_sum_of_per_currency_maxima():
    """Per-currency peaks occur on different dates; summing them invents a
    moment that never existed. The peak is a single date on a COMBINED series.

    USD peaks at 1000 in March and falls to 100 in June.
    EUR is 200 (=$100) until June, when it rises to 1000 (=$500).
    Sum of maxima would be 1000 + 500 = $1500 — an instant that never was.
    The real combined peak is March: 1000 + 100 = $1100.
    """
    movements = [
        movement(date(2025, 1, 5), "200", currency="EUR", source_file="eur.xlsx", order=0),
        movement(date(2025, 3, 5), "1000", currency="USD", source_file="usd.xlsx", order=0),
        movement(date(2025, 6, 5), "100", currency="USD", source_file="usd.xlsx", order=1),
        movement(date(2025, 6, 6), "1000", currency="EUR", source_file="eur.xlsx", order=1),
    ]
    points, _, _ = build_combined_series(movements, RATES)
    result = derive_max(points, is_liability=False)

    per_currency_maxima = Decimal("1000") + to_usd(Decimal("1000"), "EUR", RATES)
    assert per_currency_maxima == Decimal("1500")

    assert result["max_usd"] == Decimal("1100")
    assert result["max_usd"] < per_currency_maxima
    assert result["date_of_maximum"] == date(2025, 3, 5)
    assert result["max_components"] == {"USD": Decimal("1000"), "EUR": Decimal("200")}


def test_conversion_commutes_with_taking_the_maximum():
    """One rate applies to every point in the year, which is what licenses
    converting point-wise and taking the max afterwards."""
    movements = [
        movement(date(2025, 1, 5), "100", currency="EUR", source_file="eur.xlsx", order=0),
        movement(date(2025, 6, 5), "300", currency="EUR", source_file="eur.xlsx", order=1),
        movement(date(2025, 9, 5), "200", currency="EUR", source_file="eur.xlsx", order=2),
    ]
    points, _, _ = build_combined_series(movements, RATES)
    converted_then_maxed = derive_max(points, is_liability=False)["max_usd"]
    maxed_then_converted = to_usd(Decimal("300"), "EUR", RATES)
    assert converted_then_maxed == maxed_then_converted


# ---------------------------------------------------------------------------
# §8.1 — ordering within a day
# ---------------------------------------------------------------------------

def cross_stream_same_day(order_usd_first=True):
    """Two currencies both moving on one date, with no timestamp to separate
    them. USD goes 100 -> 500; EUR goes 200 (=$100) -> 100 (=$50)."""
    usd = [movement(date(2025, 1, 1), "100", currency="USD", source_file="usd.xlsx", order=0),
           movement(date(2025, 6, 5), "500", currency="USD", source_file="usd.xlsx", order=1)]
    eur = [movement(date(2025, 1, 1), "200", currency="EUR", source_file="eur.xlsx", order=0),
           movement(date(2025, 6, 5), "100", currency="EUR", source_file="eur.xlsx", order=1)]
    return (usd + eur) if order_usd_first else (eur + usd)


def test_a_peak_that_depends_on_intraday_sequence_is_a_bound_not_a_figure():
    """Two independent streams moved on one date and no timestamp separates
    them. Taking the day's maximum per currency would manufacture an instant
    that never existed; taking the day's last would discard a real intraday
    peak. Neither is acceptable as a default."""
    points, uncertainty, _ = build_combined_series(cross_stream_same_day(), RATES)
    result = derive_max(points, is_liability=False)

    # Ceiling for 5 June: USD at its highest (500) with EUR at its highest
    # (200 = $100) — reachable by some interleaving, so a true upper bound.
    assert result["max_usd"] == Decimal("600")
    assert result["max_usd_bound"] == BOUND_UNORDERED
    assert result["date_of_maximum"] == date(2025, 6, 5)
    assert any(u["kind"] == BOUND_UNORDERED for u in uncertainty)


def test_reordering_the_same_day_rows_changes_nothing():
    """The reported figure must not depend on which order the reader happened
    to see the streams in — that is what makes a bound honest rather than a
    silent pick."""
    first = derive_max(build_combined_series(cross_stream_same_day(True), RATES)[0], False)
    second = derive_max(build_combined_series(cross_stream_same_day(False), RATES)[0], False)
    assert first["max_usd"] == second["max_usd"]
    assert first["max_usd_bound"] == second["max_usd_bound"] == BOUND_UNORDERED
    assert first["date_of_maximum"] == second["date_of_maximum"]


def test_same_day_rows_within_one_file_are_not_ambiguous():
    """Within a file, row order IS the sequence — a running-balance file gives
    one balance per row. Only the interleaving ACROSS streams is unknowable."""
    movements = [
        movement(date(2025, 1, 9), "110", currency="COP", source_file="q1.xlsx", row=12, order=0),
        movement(date(2025, 1, 9), "100", currency="COP", source_file="q1.xlsx", row=13, order=1),
    ]
    points, uncertainty, _ = build_combined_series(movements, RATES)
    assert uncertainty == []
    result = derive_max(points, is_liability=False)
    assert result["max_usd_bound"] is None
    # The intraday peak is kept, not discarded by taking the day's last.
    assert result["max_usd"] == to_usd(Decimal("110"), "COP", RATES)


def test_date_of_maximum_breaks_ties_to_the_earliest_date():
    """A maximum touched on several dates is common on a flat account, and an
    unspecified tie-break makes the output non-deterministic."""
    movements = [
        movement(date(2025, 3, 5), "100", currency="USD", source_file="a.xlsx", order=0),
        movement(date(2025, 6, 5), "50", currency="USD", source_file="a.xlsx", order=1),
        movement(date(2025, 9, 5), "100", currency="USD", source_file="a.xlsx", order=2),
    ]
    points, _, _ = build_combined_series(movements, RATES)
    assert derive_max(points, is_liability=False)["date_of_maximum"] == date(2025, 3, 5)


# ---------------------------------------------------------------------------
# §8.2 / §6.2 — signed components, and liabilities
# ---------------------------------------------------------------------------

def test_components_are_signed_and_no_convention_is_applied():
    """`negative_balance_is_zero` applied per currency would floor one
    component while the merged series was summed from signed values; the two
    then disagree by the floored amount and V3 hard-errors on a roster that is
    otherwise correct. Zero-flooring is a filing decision."""
    movements = [
        movement(date(2025, 1, 5), "1000", currency="USD", source_file="usd.xlsx", order=0),
        movement(date(2025, 1, 5), "-200", currency="EUR", source_file="eur.xlsx", order=0),
    ]
    points, _, _ = build_combined_series(movements, RATES)
    result = derive_max(points, is_liability=False)
    assert result["max_components"]["EUR"] == Decimal("-200")
    # The stated maximum is the SIGNED sum, so the two reconcile.
    assert result["max_usd"] == Decimal("1000") + to_usd(Decimal("-200"), "EUR", RATES)
    assert result["max_usd"] == Decimal("900")


def test_a_liability_gets_no_bare_max_usd():
    """The maximum of a mostly-negative card series is its LEAST NEGATIVE
    point — a figure with no filing meaning until
    `credit_card_positive_balance_only` is applied, which is Claude's call."""
    movements = [
        movement(date(2025, 1, 5), "-1000", currency="USD", source_file="card.xlsx", order=0),
        movement(date(2025, 6, 5), "-100", currency="USD", source_file="card.xlsx", order=1),
    ]
    points, _, _ = build_combined_series(movements, RATES)
    result = derive_max(points, is_liability=True)
    assert result["max_usd"] is None
    assert result["max_signed_usd"] == Decimal("-100")
    assert result["max_positive_usd"] == Decimal("0")


def test_a_genuine_overpayment_shows_up_as_max_positive_usd():
    movements = [
        movement(date(2025, 1, 5), "-1000", currency="USD", source_file="card.xlsx", order=0),
        movement(date(2025, 6, 5), "250", currency="USD", source_file="card.xlsx", order=1),
    ]
    points, _, _ = build_combined_series(movements, RATES)
    result = derive_max(points, is_liability=True)
    assert result["max_signed_usd"] == Decimal("250")
    assert result["max_positive_usd"] == Decimal("250")
    assert result["max_usd"] is None


# ---------------------------------------------------------------------------
# 9.5 — the December 31 balance is a cross-check, not a filing field
# ---------------------------------------------------------------------------

def test_year_end_balance_is_forward_filled_and_says_so():
    movements = [movement(date(2025, 11, 30), "500", currency="USD",
                          source_file="nov.xlsx", order=0)]
    points, _, _ = build_combined_series(movements, RATES)
    value, caveat = balance_at_year_end(points, 2025)
    assert value == Decimal("500")
    assert "Forward-filled from 2025-11-30" in caveat


def test_an_observed_december_31_balance_carries_no_caveat():
    movements = [movement(date(2025, 12, 31), "500", currency="USD",
                          source_file="dec.xlsx", order=0)]
    points, _, _ = build_combined_series(movements, RATES)
    value, caveat = balance_at_year_end(points, 2025)
    assert (value, caveat) == (Decimal("500"), None)


def test_no_year_end_balance_when_a_currency_was_never_observed():
    movements = [
        movement(date(2025, 12, 31), "500", currency="USD", source_file="usd.xlsx", order=0),
    ]
    points, _, _ = build_combined_series(movements + [
        movement(date(2026, 1, 5), "100", currency="EUR", source_file="eur.xlsx", order=0),
    ], RATES)
    value, caveat = balance_at_year_end(points, 2025)
    assert value is None
    assert "EUR" in caveat


def test_an_empty_series_yields_nothing_rather_than_zero():
    points, uncertainty, currencies = build_combined_series([], RATES)
    assert (points, uncertainty, currencies) == ([], [], [])
    result = derive_max(points, is_liability=False)
    assert result["max_usd"] is None
    assert balance_at_year_end(points, 2025) == (None, "No observation falls on or before December 31.")


def test_streams_that_all_rise_on_one_date_are_not_ambiguous():
    """Both currencies peak together at the close whatever the order, so the
    ceiling adds nothing. Flagging it would put an `unordered_within_day` bound
    — and a `statement_partial` cap — on a maximum that is not in doubt."""
    movements = [
        movement(date(2025, 1, 1), "100", currency="USD", source_file="usd.xlsx", order=0),
        movement(date(2025, 1, 1), "200", currency="EUR", source_file="eur.xlsx", order=0),
        movement(date(2025, 6, 5), "500", currency="USD", source_file="usd.xlsx", order=1),
        movement(date(2025, 6, 5), "1000", currency="EUR", source_file="eur.xlsx", order=1),
    ]
    points, uncertainty, _ = build_combined_series(movements, RATES)
    assert uncertainty == []
    result = derive_max(points, is_liability=False)
    assert result["max_usd"] == Decimal("1000")      # 500 USD + 1000/2 EUR
    assert result["max_usd_bound"] is None


def test_streams_that_all_fall_on_one_date_are_not_ambiguous():
    """The ceiling equals the level ENTERING the day, which is an instant
    already in the series — not a new possibility the ordering opens up."""
    movements = [
        movement(date(2025, 1, 1), "500", currency="USD", source_file="usd.xlsx", order=0),
        movement(date(2025, 1, 1), "1000", currency="EUR", source_file="eur.xlsx", order=0),
        movement(date(2025, 6, 5), "100", currency="USD", source_file="usd.xlsx", order=1),
        movement(date(2025, 6, 5), "200", currency="EUR", source_file="eur.xlsx", order=1),
    ]
    points, uncertainty, _ = build_combined_series(movements, RATES)
    assert uncertainty == []
    result = derive_max(points, is_liability=False)
    assert result["max_usd"] == Decimal("1000")
    assert result["date_of_maximum"] == date(2025, 1, 1)


def test_a_multi_currency_account_can_still_reach_a_full_year():
    """Consequence of the rule above, and the reason it matters: with an
    opening observation for every currency on the same date, a multi-currency
    timeline carries no bound at all — otherwise no such account could ever
    derive `statement_full_year`, because the earliest event always belongs to
    exactly one currency."""
    movements = [
        movement(date(2025, 1, 1), "100", currency="USD", source_file="usd.xlsx", order=0),
        movement(date(2025, 1, 1), "200", currency="EUR", source_file="eur.xlsx", order=0),
    ]
    points, uncertainty, _ = build_combined_series(movements, RATES)
    assert uncertainty == []
    assert points[0].unknown_currencies == []
