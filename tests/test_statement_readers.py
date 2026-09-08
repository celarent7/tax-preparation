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

def test_order_key_is_date_then_file_then_time_order():
    """A running-balance file gives one balance per ROW, so the sequence within
    a day lives in row order. TBC's every timestamp is 00:00:00, and same-date
    rows are real: Bancolombia savings has two `9/01` rows in Q1 alone."""
    first = MovementRecord(date=date(2025, 1, 9), description="a", currency="COP",
                           source_file="q1.xlsx", source_row=12, row_order=0,
                           amount=Decimal("10"), balance_after=Decimal("110"))
    second = MovementRecord(date=date(2025, 1, 9), description="b", currency="COP",
                            source_file="q1.xlsx", source_row=13, row_order=1,
                            amount=Decimal("-10"), balance_after=Decimal("100"))
    assert first.order_key() < second.order_key()
    assert sorted([second, first], key=MovementRecord.order_key) == [first, second]


def test_row_order_is_time_order_not_the_spreadsheet_row():
    """A newest-first file has its spreadsheet rows inverted relative to time.
    Ordering on `source_row` would preserve exactly the wrong sequence, so
    `row_order` is what the sort key reads and `source_row` stays traceable."""
    newest_first = MovementRecord(date=date(2025, 1, 9), description="later",
                                  currency="COP", source_file="d.xlsx",
                                  source_row=2, row_order=1,
                                  balance_after=Decimal("100"))
    oldest_last = MovementRecord(date=date(2025, 1, 9), description="earlier",
                                 currency="COP", source_file="d.xlsx",
                                 source_row=3, row_order=0,
                                 balance_after=Decimal("110"))
    assert oldest_last.order_key() < newest_first.order_key()
    assert oldest_last.source_row > newest_first.source_row


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


# ===========================================================================
# xlsx_running_balance — the readers, against synthetic files of each shape
# ===========================================================================

from statement_fixtures import (  # noqa: E402
    FAKE_ADDRESS,
    FAKE_CLIENT,
    bancolombia_savings_workbook,
    tbc_sheet_name,
    tbc_workbook,
    write_xlsx,
)
from statement_parser import read_xlsx_running_balance  # noqa: E402
from statement_profiles import (  # noqa: E402
    BANCOLOMBIA_SAVINGS,
    TBC,
    UNWRITABLE_PROFILES,
    get_profile,
)

SAVINGS_ROWS = [
    ("31/12", "Saldo inicial", "0.00", "1,000.00"),
    ("9/01", "Consignación", "500.00", "1,500.00"),
    ("9/01", "Retiro", "-200.00", "1,300.00"),
    ("15/02", "Consignación", "2,000.00", "3,300.00"),
]


def savings_file(tmp_path, rows=None, **kwargs):
    return bancolombia_savings_workbook(
        tmp_path / "extracto_q1.xlsx", "2024/12/31", "2025/03/31",
        SAVINGS_ROWS if rows is None else rows, **kwargs)


# ---------------------------------------------------------------------------
# 9.1 — sparse columns shift values (the highest-severity hazard here)
# ---------------------------------------------------------------------------

def test_two_absent_adjacent_columns_do_not_slide_valor_into_saldo(tmp_path):
    """Both SUCURSAL and DCTO. are None on every movement row in the corpus, so
    the six-column header consistently yields four values. Compacting the row
    would put VALOR where SALDO belongs — a plausible wrong number, on the very
    first file, not on an edge case."""
    result = read_xlsx_running_balance(savings_file(tmp_path),
                                       BANCOLOMBIA_SAVINGS, currency="COP")
    assert [m.amount for m in result.movements] == [
        Decimal("0.00"), Decimal("500.00"), Decimal("-200.00"), Decimal("2000.00")]
    assert [m.balance_after for m in result.movements] == [
        Decimal("1000.00"), Decimal("1500.00"), Decimal("1300.00"), Decimal("3300.00")]
    # The two empty columns sit BETWEEN the ones that matter, so a compacted
    # row would put VALOR where SALDO belongs — here, 500.00 as the balance.
    assert result.movements[1].balance_after != result.movements[1].amount


def test_a_row_shorter_than_the_header_is_short_not_shifted(tmp_path):
    """A trailing empty cell may simply be absent from the row tuple."""
    path = write_xlsx(tmp_path / "short.xlsx", {"Extracto": [
        ["Período Desde:", "2025/01/01"],
        ["Período Hasta:", "2025/03/31"],
        ["Movimientos:"],
        ["FECHA", "DESCRIPCIÓN", "SUCURSAL", "DCTO.", "VALOR", "SALDO"],
        ["1/01", "Apertura", None, None, "100.00", "100.00"],
        ["2/01", "Sin saldo", None, None, "50.00"],
    ]})
    result = read_xlsx_running_balance(path, BANCOLOMBIA_SAVINGS, currency="COP")
    assert result.movements[1].amount == Decimal("50.00")
    # The absent balance is absent, not zero.
    assert result.movements[1].balance_after is None


# ---------------------------------------------------------------------------
# 9.9 — dual header rows
# ---------------------------------------------------------------------------

def test_english_header_row_is_bound_not_the_georgian_one(tmp_path):
    """TBC row 1 is Georgian, row 2 English, data from row 3. Binding to row 1
    yields untranslatable keys. The header is found by MATCHING the profile's
    column texts, so row 2 wins without anyone counting rows."""
    path = tbc_workbook(tmp_path / "tbc_usd.xlsx", "USD", [
        ("2025-01-05", "Incoming", "", "1000.00", "1000.00"),
        ("2025-06-10", "Outgoing", "250.00", "", "750.00"),
    ])
    result = read_xlsx_running_balance(path, TBC,
                                       period_start="2025-01-01",
                                       period_end="2025-12-31")
    assert [m.description for m in result.movements] == ["Incoming", "Outgoing"]
    assert result.movements[0].source_row == 3
    assert result.currencies == ["USD"]


def test_currency_comes_from_the_sheet_name_not_the_filename(tmp_path):
    """A filename is a naming convention someone chose; a sheet name is the
    export's own statement about what the sheet holds."""
    path = tbc_workbook(tmp_path / "account_statement_misleading_USD.xlsx", "GEL",
                        [("2025-11-02", "Sole GEL movement", "", "300.00", "300.00")])
    result = read_xlsx_running_balance(path, TBC,
                                       period_start="2025-01-01",
                                       period_end="2025-12-31")
    assert result.currencies == ["GEL"]
    assert result.movements[0].currency == "GEL"


# ---------------------------------------------------------------------------
# 9.12 — '' vs None vs 0 in a balance cell, at reader level
# ---------------------------------------------------------------------------

def test_empty_string_none_and_zero_in_a_balance_cell(tmp_path):
    """TBC writes '' into the unused side of Paid Out / Paid In and '0' as a
    balance. A row with neither side written has NO amount, not an amount of
    zero; a balance of '0' is a level, an empty balance cell is not."""
    path = tbc_workbook(tmp_path / "tbc_states.xlsx", "USD", [
        ("2025-01-05", "written zero balance", "", "0", "0"),
        ("2025-02-05", "empty balance", "", "10.00", ""),
        ("2025-03-05", "no amount at all", "", "", "10.00"),
    ])
    result = read_xlsx_running_balance(path, TBC,
                                       period_start="2025-01-01",
                                       period_end="2025-12-31")
    zero, empty, no_amount = result.movements
    assert zero.balance_after == Decimal("0")
    assert empty.balance_after is None
    assert no_amount.amount is None
    assert no_amount.balance_after == Decimal("10.00")


# ---------------------------------------------------------------------------
# 9.11 — ws.max_row is None
# ---------------------------------------------------------------------------

def test_sheet_with_no_dimension_record_still_reads(tmp_path):
    """Under read_only=True both Bancolombia shapes report no dimensions, so
    code that ranges over `max_row` silently reads nothing."""
    import openpyxl

    path = savings_file(tmp_path, strip_dimensions=True)
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        # The fixture must actually exercise the hazard: openpyxl always emits
        # a <dimension> record, so a fixture saved normally would pass this
        # test without testing anything.
        assert workbook["Extracto"].max_row is None
    finally:
        workbook.close()

    result = read_xlsx_running_balance(path, BANCOLOMBIA_SAVINGS, currency="COP")
    assert len(result.movements) == 4


# ---------------------------------------------------------------------------
# 9.6 — empty statement is not a zero balance
# ---------------------------------------------------------------------------

def test_single_cell_sheet_yields_no_data_not_zero_and_not_an_error(tmp_path):
    """Wise's `All transactions` sheet is a 1x1 cell containing `ID` — not a
    header row. Emitting $0 would assert a determined maximum the evidence
    cannot support; an empty export is equally consistent with a FAILED
    export."""
    path = write_xlsx(tmp_path / "empty.xlsx", {
        "Summary": [["Account", "GE00"]],
        tbc_sheet_name("USD"): [["ID"]],
    })
    result = read_xlsx_running_balance(path, TBC,
                                       period_start="2025-01-01",
                                       period_end="2025-12-31")
    assert result.no_data is True
    assert result.movements == []
    assert any("NO DATA" in w for w in result.warnings)


def test_rows_present_but_no_matching_header_is_an_error_not_no_data(tmp_path):
    """The two halves of hazard 9.6 must not be confused: a sheet full of real
    rows with no matching header means the PROFILE is wrong."""
    path = write_xlsx(tmp_path / "wrong.xlsx", {"Extracto": [
        ["Período Desde:", "2025/01/01"],
        ["Período Hasta:", "2025/03/31"],
        ["DATE", "MEMO", "AMOUNT", "RUNNING"],
        ["1/01", "something", "10.00", "10.00"],
    ]})
    with pytest.raises(StatementParseError, match="does not match this file"):
        read_xlsx_running_balance(path, BANCOLOMBIA_SAVINGS, currency="COP")


def test_no_matching_sheet_names_the_sheets_that_are_there(tmp_path):
    path = write_xlsx(tmp_path / "othersheet.xlsx", {"Hoja1": [["a", "b"]]})
    with pytest.raises(StatementParseError, match="no sheet matches profile"):
        read_xlsx_running_balance(path, BANCOLOMBIA_SAVINGS, currency="COP")


# ---------------------------------------------------------------------------
# 9.2 — period discovery, and two date formats in one file
# ---------------------------------------------------------------------------

def test_period_is_read_from_the_metadata_block(tmp_path):
    """The savings header states the period as `YYYY/MM/DD` while the movement
    rows are `D/MM`, and Q1 opens in the PRIOR calendar year."""
    result = read_xlsx_running_balance(savings_file(tmp_path),
                                       BANCOLOMBIA_SAVINGS, currency="COP")
    period = result.periods[0]
    assert (period.start, period.end) == (date(2024, 12, 31), date(2025, 3, 31))
    assert result.period_inferred_from_rows is False
    assert result.movements[0].date == date(2024, 12, 31)
    assert result.movements[1].date == date(2025, 1, 9)


def test_configured_period_wins_over_the_metadata_block(tmp_path):
    result = read_xlsx_running_balance(savings_file(tmp_path), BANCOLOMBIA_SAVINGS,
                                       currency="COP",
                                       period_start="2024-12-01",
                                       period_end="2025-03-31")
    assert result.periods[0].start == date(2024, 12, 1)


def test_partial_dates_with_no_period_anywhere_is_an_error(tmp_path):
    """`D/MM` cannot be dated without a period, and a year may not be guessed:
    the January card period opens 30/12 of the prior year."""
    path = write_xlsx(tmp_path / "noperiod.xlsx", {"Extracto": [
        ["Movimientos:"],
        ["FECHA", "DESCRIPCIÓN", "SUCURSAL", "DCTO.", "VALOR", "SALDO"],
        ["1/01", "Apertura", None, None, "100.00", "100.00"],
    ]})
    with pytest.raises(StatementParseError, match="cannot be guessed|A year cannot"):
        read_xlsx_running_balance(path, BANCOLOMBIA_SAVINGS, currency="COP")


def test_a_period_derived_from_its_own_rows_is_flagged(tmp_path):
    """TBC states no period the profile can read, so without one in
    sources.json the span comes from the rows — which can never show that a
    statement is missing from either end."""
    path = tbc_workbook(tmp_path / "tbc_norange.xlsx", "USD", [
        ("2025-03-05", "first", "", "100.00", "100.00"),
        ("2025-09-05", "last", "", "100.00", "200.00"),
    ])
    result = read_xlsx_running_balance(path, TBC)
    assert result.period_inferred_from_rows is True
    assert result.periods[0].start == date(2025, 3, 5)
    assert any("derived from the rows themselves" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# §8.1 — a descending-order file
# ---------------------------------------------------------------------------

def test_newest_first_file_is_resequenced_into_time_order(tmp_path):
    """Sorting a newest-first file "stably" on the spreadsheet row preserves
    exactly the wrong sequence. Detection is mechanical: compare the first and
    last parsed dates."""
    path = tbc_workbook(tmp_path / "tbc_desc.xlsx", "USD", [
        ("2025-09-05", "third", "", "100.00", "300.00"),
        ("2025-06-05", "second", "", "100.00", "200.00"),
        ("2025-03-05", "first", "", "100.00", "100.00"),
    ])
    result = read_xlsx_running_balance(path, TBC,
                                       period_start="2025-01-01",
                                       period_end="2025-12-31")
    assert [m.description for m in result.movements] == ["first", "second", "third"]
    assert [m.row_order for m in result.movements] == [0, 1, 2]
    # `source_row` still points at the original spreadsheet row.
    assert [m.source_row for m in result.movements] == [5, 4, 3]
    assert any("newest-first" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# §10.2 — the metadata block is not a movement
# ---------------------------------------------------------------------------

def test_client_name_and_address_never_reach_a_movement_record(tmp_path):
    """Bancolombia sheets carry the client name and full street address in the
    metadata block of every file. Readers take the period and the account
    identifier from it and discard the rest."""
    result = read_xlsx_running_balance(savings_file(tmp_path),
                                       BANCOLOMBIA_SAVINGS, currency="COP")
    rendered = " ".join(m.description for m in result.movements)
    assert FAKE_CLIENT not in rendered
    assert FAKE_ADDRESS not in rendered
    assert all(FAKE_CLIENT not in w and FAKE_ADDRESS not in w
               for w in result.warnings)


# ---------------------------------------------------------------------------
# §4.1 — the profile the corpus cannot specify is not written
# ---------------------------------------------------------------------------

def test_no_wise_profile_exists_and_asking_says_why():
    """Both Wise exports are a single cell. Any column map would be written
    from expectation rather than evidence, which is the one thing the profile
    module exists to prevent."""
    from statement_profiles import PROFILES

    assert "wise" not in PROFILES
    with pytest.raises(StatementParseError, match="deliberately"):
        get_profile("wise")
    assert "expectation" in UNWRITABLE_PROFILES["wise"]


def test_unknown_profile_names_the_ones_that_exist():
    with pytest.raises(StatementParseError, match="Unknown profile"):
        get_profile("banco_imaginario")


# ===========================================================================
# text_period_summary — Shape B (plan §4.2)
# ===========================================================================

from statement_fixtures import tbank_summary_text  # noqa: E402
from statement_parser import (  # noqa: E402
    EVIDENCE_PERIOD_END_SUMMARIES,
    read_text_period_summary,
)
from statement_profiles import TBANK_TEXT  # noqa: E402


def summary_file(tmp_path, name="text.txt", **kwargs):
    path = tmp_path / name
    path.write_text(tbank_summary_text(**kwargs), encoding="utf-8")
    return str(path)


DECEMBER = dict(month="December", year="2025",
                open_date="22.11.2025", open_balance="+1 000,00 ₽",
                close_date="21.12.2025", close_balance="+1 500,00 ₽",
                income="+700,00 ₽", expenses="−200,00 ₽")


def test_two_bounding_balances_become_two_balance_observations(tmp_path):
    """There is no transaction detail: two balances bound the period and
    everything between them is invisible."""
    result = read_text_period_summary(summary_file(tmp_path, **DECEMBER),
                                      TBANK_TEXT, currency="RUB")
    opening, closing = result.movements
    assert opening.balance_after == Decimal("1000.00")
    assert closing.balance_after == Decimal("1500.00")
    assert opening.date == date(2025, 11, 22)
    assert closing.date == date(2025, 12, 21)
    # No flow is asserted between them — inventing one would claim a movement
    # the file never records.
    assert opening.amount is None and closing.amount is None


def test_the_evidence_kind_can_never_support_a_full_year(tmp_path):
    result = read_text_period_summary(summary_file(tmp_path, **DECEMBER),
                                      TBANK_TEXT, currency="RUB")
    assert result.evidence_kind == EVIDENCE_PERIOD_END_SUMMARIES
    assert any("peaked between them and returned looks flat" in w
               for w in result.warnings)


def test_the_period_comes_from_the_balance_lines_not_the_filename(tmp_path):
    """`text 3.txt` is October and `text 12.txt` is January, so sorting these
    by name shuffles the year."""
    october = read_text_period_summary(
        summary_file(tmp_path, name="text 3.txt", month="October", year="2025",
                     open_date="22.09.2025", open_balance="+1 000,00 ₽",
                     close_date="21.10.2025", close_balance="+1 000,00 ₽"),
        TBANK_TEXT, currency="RUB")
    january = read_text_period_summary(
        summary_file(tmp_path, name="text 12.txt", month="January", year="2025",
                     open_date="22.12.2024", open_balance="+1 000,00 ₽",
                     close_date="21.01.2025", close_balance="+1 000,00 ₽"),
        TBANK_TEXT, currency="RUB")
    assert october.periods[0].start == date(2025, 9, 22)
    assert january.periods[0].start == date(2024, 12, 22)
    # The higher-numbered filename holds the EARLIER period.
    assert january.periods[0].start < october.periods[0].start


def test_the_totals_reconcile_against_the_balance_movement(tmp_path):
    """+700 income and −200 expenses move 1000 to 1500."""
    result = read_text_period_summary(summary_file(tmp_path, **DECEMBER),
                                      TBANK_TEXT, currency="RUB")
    assert not any("a sign was lost" in w for w in result.warnings)


def test_a_lost_unicode_minus_shows_up_as_totals_that_do_not_reconcile(tmp_path):
    """Written with an ASCII-positive Expenses line, the totals sum to +900
    while the balances move by +500. The balances still parse and the timeline
    still builds — only this arithmetic notices."""
    broken = dict(DECEMBER, expenses="+200,00 ₽")
    result = read_text_period_summary(summary_file(tmp_path, **broken),
                                      TBANK_TEXT, currency="RUB")
    assert any("a sign was lost" in w for w in result.warnings)


def test_totals_never_become_balance_points(tmp_path):
    result = read_text_period_summary(summary_file(tmp_path, **DECEMBER),
                                      TBANK_TEXT, currency="RUB")
    assert len(result.movements) == 2
    assert all(m.description.startswith("Period ") for m in result.movements)


def test_a_file_with_no_balance_line_is_no_data_not_zero(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("Statement for December 2025\n", encoding="utf-8")
    result = read_text_period_summary(str(path), TBANK_TEXT, currency="RUB")
    assert result.no_data is True
    assert result.movements == []
    assert any("NO DATA" in w for w in result.warnings)


def test_a_single_balance_line_cannot_establish_a_period(tmp_path):
    path = tmp_path / "half.txt"
    path.write_text("Statement for December 2025\n"
                    "Balance on 21.12.2025: +1 500,00 ₽\n", encoding="utf-8")
    with pytest.raises(StatementParseError, match="bounded by two"):
        read_text_period_summary(str(path), TBANK_TEXT, currency="RUB")


# ===========================================================================
# xlsx_two_sheet_card — Shape D (plan §4.4)
# ===========================================================================

from statement_fixtures import bancolombia_card_workbook  # noqa: E402
from statement_parser import read_xlsx_two_sheet_card  # noqa: E402
from statement_profiles import BANCOLOMBIA_CARD  # noqa: E402


def card_file(tmp_path, pesos=None, dolares=None, name="card_jan.xlsx"):
    return bancolombia_card_workbook(
        tmp_path / name, "30/12/2024", "30/01/2025",
        pesos=pesos if pesos is not None else [
            ("30/12", "Compra", "-100,000.00", "-100,000.00"),
            ("5/01", "Pago", "150,000.00", "50,000.00"),
        ],
        dolares=dolares if dolares is not None else [
            ("10/01", "Compra", "-50.00", "-50.00"),
        ])


def test_both_currency_sheets_of_one_card_are_read(tmp_path):
    result = read_xlsx_two_sheet_card(card_file(tmp_path), BANCOLOMBIA_CARD)
    assert sorted(result.currencies) == ["COP", "USD"]
    assert sorted(result.sheets_read) == ["DOLARES", "PESOS"]
    assert result.is_liability is True


def test_the_january_card_period_opens_in_the_prior_calendar_year(tmp_path):
    """The billing period runs ~30th -> ~30th, so `30/12` in the January file
    is December 2024 — the concrete case, not a hypothetical one."""
    result = read_xlsx_two_sheet_card(card_file(tmp_path), BANCOLOMBIA_CARD)
    assert result.periods[0].start == date(2024, 12, 30)
    cop = [m for m in result.movements if m.currency == "COP"]
    assert cop[0].date == date(2024, 12, 30)


def test_row_order_is_unique_across_the_two_sheets(tmp_path):
    """Two sheets restarting at zero would tie on the merged event axis and
    make the ordering non-deterministic."""
    result = read_xlsx_two_sheet_card(card_file(tmp_path), BANCOLOMBIA_CARD)
    orders = [m.row_order for m in result.movements]
    assert len(orders) == len(set(orders))


def test_the_sign_is_reported_per_currency_and_no_convention_applied(tmp_path):
    result = read_xlsx_two_sheet_card(card_file(tmp_path), BANCOLOMBIA_CARD)
    cop_note = next(w for w in result.warnings if "[COP]" in w)
    usd_note = next(w for w in result.warnings if "[USD]" in w)
    assert "1 of 2 positive" in cop_note      # the overpayment after the Pago
    assert "0 of 1 positive" in usd_note
    assert "does not apply" in cop_note


def test_a_card_arriving_with_one_sheet_is_named_not_read_silently(tmp_path):
    path = write_xlsx(tmp_path / "half.xlsx", {"PESOS": [
        ["Período Facturado Desde:", "30/12/2024"],
        ["Período Facturado Hasta:", "30/01/2025"],
        ["FECHA", "DESCRIPCIÓN", "VALOR", "SALDO"],
        ["5/01", "Compra", "-100.00", "-100.00"],
    ]})
    result = read_xlsx_two_sheet_card(path, BANCOLOMBIA_CARD)
    assert any("DOLARES not present" in w for w in result.warnings)
    assert any("understates" in w for w in result.warnings)


def test_a_card_profile_that_forgot_the_liability_flag_is_refused(tmp_path):
    from dataclasses import replace as dataclass_replace

    asset_shaped = dataclass_replace(BANCOLOMBIA_CARD, is_liability=False)
    with pytest.raises(StatementParseError, match="not marked is_liability"):
        read_xlsx_two_sheet_card(card_file(tmp_path), asset_shaped)


def test_a_grouping_separator_after_the_decimal_point_is_rejected():
    """Regression: `_assert_grouping` only inspected the INTEGER part.

    `1.234,56` is `1234.56` written dot-thousands/comma-decimal — Colombia's own
    typographic convention, which is precisely what `COLOMBIAN_EXPORT_NUMBERS`
    deliberately does not follow. Read with that dot-DECIMAL profile the comma
    vanished as a group mark and the value became `1.23456`: a thousand times
    too small, and the integer-part check never saw it because the integer part
    was the single digit `1`.
    """
    with pytest.raises(StatementParseError, match="grouping separator after"):
        parse_decimal("1.234,56", COLOMBIAN_EXPORT_NUMBERS)
    with pytest.raises(StatementParseError, match="grouping separator after"):
        parse_decimal("12.345,67", PLAIN_NUMBERS)

    # The legitimate shapes still parse.
    assert parse_decimal("1,234.56", COLOMBIAN_EXPORT_NUMBERS) == Decimal("1234.56")
    assert parse_decimal("1 234,56", RUSSIAN_NUMBERS) == Decimal("1234.56")


# ---------------------------------------------------------------------------
# Shape E — movement registers (PDF), transactions with NO balance
# ---------------------------------------------------------------------------


def test_the_movement_register_profile_is_registered_and_states_no_balance_line():
    """Shape E carries transactions and period totals only. If a future edit
    gives this profile a balance line, the reader would start emitting levels
    the document does not state — the exact substitution UNWRITABLE_PROFILES
    exists to refuse."""
    from statement_profiles import TBANK_MOVEMENTS, MovementRegisterProfile, get_profile

    assert get_profile("tbank_movements") is TBANK_MOVEMENTS
    assert isinstance(TBANK_MOVEMENTS, MovementRegisterProfile)
    assert not hasattr(TBANK_MOVEMENTS, "balance_line")
    assert TBANK_MOVEMENTS.newest_first is True


# Synthetic, following the `examples/statements/` convention. The contract and
# account numbers are zeros on purpose: this repository is PUBLIC, and a
# well-formed account number is identifying whether or not it is anyone's.
# The reader never reads these two lines — they are here so the fixture has
# the shape of the real document.
MOVEMENT_LINES = [
    "Movement of funds statement",
    "Contract number: 0000000000",
    "Account number: 00000000000000000000",
    "Movement of funds for the period 01.01.2025 - 31.12.2025",
    "14.07.2025  14.07.2025  +2 500.00 R    +2 500.00 R",
    "20.03.2025  20.03.2025  -250.50 R      -250.50 R",
    "Deposits: 2 500,00 R",
    "Expenses: 250,50 R",
]


def test_a_register_yields_movements_in_chronological_order_with_no_balances():
    """Rows print newest-first. Read as printed, the series runs backwards and
    every downstream cumulative figure is wrong."""
    from statement_parser import read_movement_register_lines
    from statement_profiles import TBANK_MOVEMENTS

    result = read_movement_register_lines(
        MOVEMENT_LINES, TBANK_MOVEMENTS, "reg.pdf", currency="RUB")

    assert [m.date for m in result.movements] == [date(2025, 3, 20), date(2025, 7, 14)]
    assert [m.amount for m in result.movements] == [Decimal("-250.50"), Decimal("2500.00")]
    assert all(m.balance_after is None for m in result.movements), (
        "a register states no balance; emitting one would invent evidence")
    assert result.periods == [StatementPeriod(date(2025, 1, 1), date(2025, 12, 31), "reg.pdf")]


def test_a_register_whose_parse_misses_a_row_is_refused():
    """The bank states its own Пополнения/Расходы totals. If the parsed rows do
    not sum to them, rows were dropped or misread — and a dropped row in a
    movements ledger is invisible downstream. Reconciling against the document's
    own arithmetic is the only check that catches it."""
    from statement_parser import read_movement_register_lines
    from statement_profiles import TBANK_MOVEMENTS

    broken = list(MOVEMENT_LINES)
    broken[-1] = "Expenses: 999,00 R"
    with pytest.raises(StatementParseError, match="does not reconcile"):
        read_movement_register_lines(broken, TBANK_MOVEMENTS, "reg.pdf", currency="RUB")


def test_a_register_with_no_transaction_rows_is_a_no_data_finding():
    from statement_parser import read_movement_register_lines
    from statement_profiles import TBANK_MOVEMENTS

    header_only = MOVEMENT_LINES[:4] + ["Deposits: 0,00 R", "Expenses: 0,00 R"]
    result = read_movement_register_lines(
        header_only, TBANK_MOVEMENTS, "reg.pdf", currency="RUB")
    assert result.no_data is True
    assert result.movements == []


def test_the_register_reader_is_dispatchable_and_demands_a_currency(tmp_path):
    """Like the text reader, a register states amounts without naming the
    account's currency, so it comes from sources.json. Defaulting it would
    silently label rubles as dollars."""
    from statement_parser import READERS, read_source

    assert READERS["pdf_movement_register"] == "read_pdf_movement_register"

    source = {"filer": "primary", "institution": "T-Bank", "path": "",
              "glob": "*.pdf", "reader": "pdf_movement_register",
              "profile": "tbank_movements", "account_key": "k",
              "account_key_pattern": None, "account_group": None,
              "currency": None, "period_start": None, "period_end": None,
              "notes": None}
    (tmp_path / "reg.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    with pytest.raises(StatementParseError, match="currency"):
        read_source(source, str(tmp_path))


def test_no_profile_may_claim_to_derive_balances_from_a_register():
    """The reason PDF support is safe to add is that it changes the CONTAINER,
    not the evidence rule. If a profile ever gains a way to synthesize levels
    from deltas, that separation is gone and a register starts producing
    maximum values it cannot support.

    A regression lock, not a red-green cycle: it passes the day it is written
    and exists to fail on a later edit."""
    from statement_profiles import PROFILES, MovementRegisterProfile

    for name, profile in PROFILES.items():
        if isinstance(profile, MovementRegisterProfile):
            assert not any(hasattr(profile, f) for f in
                           ("balance_line", "opening_balance", "closing_balance",
                            "derive_balance", "anchor_balance")), (
                f"{name} claims a balance source; a register has none")


def test_the_movement_is_the_account_amount_not_the_operation_amount():
    """The two columns differ on any foreign-currency transaction: the
    operation amount is what the merchant charged, the account amount is what
    left the account. Only the second is a movement of THIS account.

    Every other register fixture prints the two columns equal, which is what a
    same-currency purchase looks like — so reading the wrong column would pass
    all of them. This case is the one that can tell them apart, and it is the
    §9.4-class failure: a wrong figure, not an error.
    """
    from statement_parser import read_movement_register_lines
    from statement_profiles import TBANK_MOVEMENTS

    lines = [
        "Movement of funds for the period 01.01.2025 - 31.12.2025",
        "05.02.2025  05.02.2025  -100.00 $      -9 250.00 R",
        "Deposits: 0,00 R",
        "Expenses: 9 250,00 R",
    ]
    result = read_movement_register_lines(lines, TBANK_MOVEMENTS, "reg.pdf",
                                          currency="RUB")
    assert [m.amount for m in result.movements] == [Decimal("-9250.00")]
    # And the reconciliation agrees, because the bank totals its own account
    # column too — reading the operation column would fail this as well.
    assert not any("does not reconcile" in w for w in result.warnings)


def test_the_reconciliation_tolerance_follows_the_currencys_minor_unit():
    """A hardcoded 0.01 is the wrong tolerance for a zero-decimal currency.

    JPY has no sub-unit, so a 0.50 gap between the parsed rows and the stated
    totals cannot be a real discrepancy there — while in RUB it is exactly the
    dropped-row signal this check exists to catch. The sibling
    `_check_summary_totals` already scales its tolerance with `minor_unit`;
    this one *raises* where that one only warns, so a tolerance that is too
    tight here kills a run over a difference the currency cannot express.
    """
    from statement_parser import read_movement_register_lines
    from statement_profiles import TBANK_MOVEMENTS

    # The R marker is the document's own typography and says nothing about the
    # account's currency — that is precisely why `currency` comes from
    # sources.json rather than from the page.
    lines = [
        "Movement of funds for the period 01.01.2025 - 31.12.2025",
        "14.07.2025  14.07.2025  +2 500.50 R    +2 500.50 R",
        "Deposits: 2 500,00 R",
        "Expenses: 0,00 R",
    ]

    result = read_movement_register_lines(lines, TBANK_MOVEMENTS, "reg.pdf",
                                          currency="JPY")
    assert [m.amount for m in result.movements] == [Decimal("2500.50")]

    # The same 0.50 in a two-decimal currency still stops the run, which is
    # what makes the assertion above a statement about the tolerance and not
    # about the check having been loosened for everyone.
    with pytest.raises(StatementParseError, match="does not reconcile"):
        read_movement_register_lines(lines, TBANK_MOVEMENTS, "reg.pdf",
                                     currency="RUB")


def test_a_register_row_that_matches_but_cannot_be_read_is_an_error():
    """A row the reader recognizes as a transaction and then fails to read must
    never be skipped. Reconciliation would catch the short ledger only where
    the document states totals; here it states none, so nothing else would.

    A regression lock on `parse_full_date`/`parse_decimal` raising rather than
    returning None for garbage: the reader's `continue` branch is reachable
    only for an ABSENT value, which `txn_line` cannot match. If either parser
    is ever made lenient, that branch turns into a silent drop and this fails.
    """
    from statement_parser import read_movement_register_lines
    from statement_profiles import TBANK_MOVEMENTS

    lines = [
        "Movement of funds for the period 01.01.2025 - 31.12.2025",
        "32.13.2025  32.13.2025  -100.00 R      -100.00 R",
    ]
    with pytest.raises(StatementParseError, match="cannot read"):
        read_movement_register_lines(lines, TBANK_MOVEMENTS, "reg.pdf",
                                     currency="RUB")


def test_a_register_with_no_rows_but_non_zero_totals_is_refused():
    """Zero rows is a no-data finding ONLY when the document agrees that
    nothing happened. A register stating 2 500,00 of deposits and listing no
    transaction the reader could see has not described an empty period — it has
    described a parse that read none of it, and reporting that as `no_data`
    would turn a total extraction failure into a confident statement about the
    account."""
    from statement_parser import read_movement_register_lines
    from statement_profiles import TBANK_MOVEMENTS

    lines = MOVEMENT_LINES[:4] + ["Deposits: 2 500,00 R", "Expenses: 0,00 R"]
    with pytest.raises(StatementParseError, match="does not reconcile"):
        read_movement_register_lines(lines, TBANK_MOVEMENTS, "reg.pdf",
                                     currency="RUB")


def test_a_page_header_repeated_across_pages_does_not_inflate_the_ledger():
    """`extract_text_lines` concatenates every page, so a multi-page register
    hands the reader its column headers and its footer once per page. None of
    those repeats may become a transaction, and a totals line read twice must
    not be counted twice — an inflated ledger is a wrong number, not an error.

    No multi-page PDF fixture exists; the line list is the whole input, so
    repeating the blocks here reproduces exactly what a second page delivers.
    """
    from statement_parser import read_movement_register_lines
    from statement_profiles import TBANK_MOVEMENTS

    page_break = [
        "Movement of funds statement",
        "Contract number: 0000000000",
        "Date  Settlement date  Operation amount  Account amount",
    ]
    lines = (MOVEMENT_LINES[:5] + page_break + MOVEMENT_LINES[5:]
             + ["Deposits: 2 500,00 R", "Expenses: 250,50 R"])

    result = read_movement_register_lines(lines, TBANK_MOVEMENTS, "reg.pdf",
                                          currency="RUB")
    assert [m.amount for m in result.movements] == [Decimal("-250.50"),
                                                    Decimal("2500.00")]


def test_the_documented_register_source_entry_is_valid():
    """A reader nobody can configure never runs. The entry shape is the
    deliverable of `examples/statements/sources.register.json`, so it is pinned
    here — a `sources.json` key that drifts would otherwise be discovered by a
    filer at their desk, mid-return.

    Validated through `load_sources` rather than by sweeping keys by hand,
    because that is the function a real run calls: it is the check that would
    actually reject the file.
    """
    import os
    from statement_parser import load_sources

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "examples", "statements", "sources.register.json")
    loaded = load_sources(path)

    entry, = loaded["sources"]
    assert entry["reader"] == "pdf_movement_register"
    assert entry["profile"] == "tbank_movements"
    # The reader raises without it, and that is the mistake this example exists
    # to prevent — a register names amounts but never the account's currency.
    assert entry["currency"] == "RUB"
    assert entry["path"] == "movements"

    # The example must point at a file that is actually here, or it documents
    # a run that cannot be reproduced.
    import glob as globmod
    corpus = os.path.join(os.path.dirname(path), entry["path"], entry["glob"])
    assert globmod.glob(corpus), f"no corpus file matches {corpus}"


def test_a_register_stating_no_transactions_contributes_no_coverage():
    """An empty export states nothing and therefore covers NOTHING — the
    invariant `derive_basis` states in prose and both sibling readers already
    honour by returning before they append a period.

    This reader appended the period anyway, so a December register listing no
    transactions made `detect_gaps` report December as covered. The basis is
    still capped at `statement_partial` by the empty-file branch, but the gap
    list is what the workpaper prints, and a gap that is never printed is one
    nobody goes looking for a statement to fill.
    """
    from statement_parser import read_movement_register_lines
    from statement_profiles import TBANK_MOVEMENTS

    lines = [
        "Movement of funds for the period 01.12.2025 - 31.12.2025",
        "Deposits: 0,00 R",
        "Expenses: 0,00 R",
    ]
    result = read_movement_register_lines(lines, TBANK_MOVEMENTS, "reg.pdf",
                                          currency="RUB")
    assert result.no_data is True
    assert result.periods == [], (
        "a file that listed no transactions covers no part of the year")


def test_an_inferred_period_spans_the_earliest_and_latest_row():
    """The span was taken from the FIRST and LAST row as printed, which assumes
    the print order is the order of the movement dates. It is not: rows are
    printed in settlement order and the movement is the OPERATION date, so a
    settlement lagging across a boundary puts the two out of step.

    Both failure modes were live. The span came out short by the difference —
    a silently wrong coverage claim — and where the drift crossed the endpoints
    the span inverted and `StatementPeriod` raised "period ends before it
    starts" on a perfectly well-formed file.
    """
    from statement_parser import read_movement_register_lines
    from statement_profiles import TBANK_MOVEMENTS

    # Printed in settlement order; the operation dates are not monotonic.
    lines = [
        "28.12.2025  05.01.2026  -1.00 R      -1.00 R",
        "03.01.2026  03.01.2026  -1.00 R      -1.00 R",
        "Deposits: 0,00 R",
        "Expenses: 2,00 R",
    ]
    result = read_movement_register_lines(lines, TBANK_MOVEMENTS, "reg.pdf",
                                          currency="RUB")

    assert result.period_inferred_from_rows is True
    assert result.periods == [
        StatementPeriod(date(2025, 12, 28), date(2026, 1, 3), "reg.pdf")]


def test_a_register_missing_one_of_its_totals_says_so():
    """Reconciliation is the only check standing between a dropped row and a
    silently short ledger, and it runs per side. With `Deposits` present and
    `Expenses` absent, the expense side was never reconciled and nothing said
    so — every expense row could have been lost and the parse would look clean.

    `layout=True` extraction re-spacing or dropping one footer line is a
    realistic way to lose exactly one total, so the half-checked case needs to
    be as visible as the unchecked one.
    """
    from statement_parser import read_movement_register_lines
    from statement_profiles import TBANK_MOVEMENTS

    lines = [
        "Movement of funds for the period 01.01.2025 - 31.12.2025",
        "14.07.2025  14.07.2025  +2 500.00 R    +2 500.00 R",
        "Deposits: 2 500,00 R",
    ]
    result = read_movement_register_lines(lines, TBANK_MOVEMENTS, "reg.pdf",
                                          currency="RUB")
    assert any("not reconciled" in w.lower() for w in result.warnings), (
        f"the missing Expenses total passed unmentioned: {result.warnings}")


def test_a_register_mixing_currency_markers_in_the_account_column_says_so():
    """The marker never identifies the account's currency — that is why
    `currency` is configuration. But every row is stamped with the ONE
    configured currency and summed into one ledger, so two markers in the
    account column is in-document proof that some rows are being counted in a
    currency they are not in. It was the one available signal and it was
    discarded silently.
    """
    from statement_parser import read_movement_register_lines
    from statement_profiles import TBANK_MOVEMENTS

    lines = [
        "Movement of funds for the period 01.01.2025 - 31.12.2025",
        "14.07.2025  14.07.2025  +2 500.00 R    +2 500.00 R",
        "20.03.2025  20.03.2025  -250.50 $      -250.50 $",
        "Deposits: 2 500,00 R",
        "Expenses: 250,50 R",
    ]
    result = read_movement_register_lines(lines, TBANK_MOVEMENTS, "reg.pdf",
                                          currency="RUB")
    assert any("more than one currency marker" in w for w in result.warnings), (
        f"the mixed account column passed unmentioned: {result.warnings}")


def test_one_register_carries_two_number_formats_at_once():
    """The rows are DOT-decimal and the period totals in the SAME file are
    COMMA-decimal — checked against every live register available: every row
    dot, every total comma. One `NumberFormat` cannot read both, and which half
    it breaks on depends only on which way it is set.

    Locale rules bind per FIELD, not per profile. The principle was already
    established for dates, where a savings header states `YYYY/MM/DD` over rows
    written `D/MM`; numbers simply never got it.
    """
    from statement_parser import read_movement_register_lines
    from statement_profiles import TBANK_MOVEMENTS

    lines = [
        "Movement of funds for the period 01.01.2025 - 31.12.2025",
        "14.07.2025  14.07.2025  +2 500.00 R    +2 500.00 R",   # dot
        "20.03.2025  20.03.2025  -250.50 R      -250.50 R",     # dot
        "Deposits: 2 500,00 R",                                  # comma
        "Expenses: 250,50 R",                                    # comma
    ]
    result = read_movement_register_lines(lines, TBANK_MOVEMENTS, "reg.pdf",
                                          currency="RUB")

    assert [m.amount for m in result.movements] == [Decimal("-250.50"),
                                                    Decimal("2500.00")]
    # Reconciliation had to read the comma totals to get this far: a failure
    # to parse them raises, and a wrong read of them raises "does not
    # reconcile". Silence here is the whole assertion.
    assert not any("reconcile" in w for w in result.warnings)


def test_a_comma_decimal_row_is_refused_rather_than_read_as_a_thousands_group():
    """`-250,50` under the rows' dot-decimal format must NOT become -25050.

    This is the factor-100 misread the two-format split could have introduced,
    and it is why the row format is its own `RUSSIAN_DOT_NUMBERS` rather than
    `PLAIN_NUMBERS`: the latter accepts `,` as a thousands separator, so it
    would fall back on the grouping check instead of rejecting the separator
    outright. Both catch this case; only one says what is actually wrong.
    """
    from statement_parser import read_movement_register_lines
    from statement_profiles import TBANK_MOVEMENTS

    lines = [
        "Movement of funds for the period 01.01.2025 - 31.12.2025",
        "20.03.2025  20.03.2025  -250,50 R      -250,50 R",
        "Expenses: 250,50 R",
    ]
    with pytest.raises(StatementParseError, match="decimal separator|unexpected"):
        read_movement_register_lines(lines, TBANK_MOVEMENTS, "reg.pdf",
                                     currency="RUB")


def test_a_profile_that_states_one_number_format_uses_it_for_its_totals_too():
    """`total_numbers` defaults to None meaning "same as the rows", so a
    document that is uniform says nothing and still parses.

    The default is deliberately not a concrete format: one that silently
    differed from `numbers` would hand the next profile author Russian totals
    for an English document, with no error anywhere to show it happened.
    """
    from statement_parser import (RUSSIAN_NUMBERS, PLAIN_NUMBERS,
                                  total_number_format)
    from statement_profiles import PROFILES, TBANK_MOVEMENTS

    checked = []
    for name, profile in PROFILES.items():
        if getattr(profile, "total_lines", None) and \
                getattr(profile, "total_numbers", None) is None:
            assert total_number_format(profile) is profile.numbers, (
                f"{name}: an unset total_numbers must fall back to the rows'")
            checked.append(name)

    # The loop can go VACUOUS and stay green: `TBANK_TEXT` is the only shipped
    # profile that reaches the assertion, so the day someone sets
    # `total_numbers` on it this test would iterate nothing and pass without
    # checking anything. A green test that checks nothing is the exact failure
    # this commit exists to close.
    assert checked, (
        "no shipped profile leaves total_numbers unset, so the assertion "
        "above ran zero times — pin the default against a synthetic profile "
        "rather than letting this loop go vacuous")

    # And where the document really does differ, the two are independent.
    assert TBANK_MOVEMENTS.numbers.decimal_sep == "."
    assert total_number_format(TBANK_MOVEMENTS) is RUSSIAN_NUMBERS
    assert total_number_format(TBANK_MOVEMENTS).decimal_sep == ","


def typed_summary_file(tmp_path, *lines: str) -> str:
    """Write a period-summary file whose typography is set line by line.

    `summary_file` above builds from `tbank_summary_text`, which is uniform in
    one number format by construction — precisely what these tests need to
    vary.
    """
    path = tmp_path / "text.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)

def test_a_stated_total_that_cannot_be_read_warns_rather_than_stopping_the_run():
    """A total that is PRESENT but unreadable is the same kind of gap as an
    ABSENT one: the document stated its arithmetic and the profile could not
    follow it, leaving that side unreconciled either way.

    They used to be treated oppositely — absent was a warning, unreadable
    raised out of `parse_decimal` and killed the whole run, including every
    other filer and account in the same `sources.json`. Nothing justified the
    split: neither case is a DETECTED mismatch, which is the one thing this
    function raises on.
    """
    from statement_parser import read_movement_register_lines
    from statement_profiles import TBANK_MOVEMENTS

    # Rows dot-decimal as the profile expects; the Deposits total written in
    # the ROWS' format instead of the totals' — the exact shape of a
    # `total_numbers` that disagrees with the page.
    lines = [
        "Movement of funds for the period 01.01.2025 - 31.12.2025",
        "14.07.2025  14.07.2025  +2 500.00 R    +2 500.00 R",
        "Deposits: 2 500.00 R",
        "Expenses: 0,00 R",
    ]
    result = read_movement_register_lines(lines, TBANK_MOVEMENTS, "reg.pdf",
                                          currency="RUB")

    # The rows still parsed — the run is not lost.
    assert len(result.movements) == 1
    assert result.movements[0].amount == Decimal("2500.00")
    # And the unread side is stated twice over: once naming the raw text, so
    # the remedy (`total_numbers`) is findable, and once by the half-checked
    # warning that already existed for the absent case.
    assert any("2 500.00 R" in w and "could not be read" in w
               for w in result.warnings)
    assert any("total_numbers" in w for w in result.warnings)
    assert any("1 of 2 period totals could not be read" in w
               for w in result.warnings)


def test_an_unreadable_total_still_lets_a_real_mismatch_raise():
    """Softening the unreadable case must not soften the DETECTED one. A side
    whose total reads fine and disagrees with the rows is still fatal."""
    from statement_parser import read_movement_register_lines
    from statement_profiles import TBANK_MOVEMENTS

    lines = [
        "Movement of funds for the period 01.01.2025 - 31.12.2025",
        "14.07.2025  14.07.2025  +2 500.00 R    +2 500.00 R",
        "Deposits: 9 999,00 R",
        "Expenses: 0,00 R",
    ]
    with pytest.raises(StatementParseError, match="does not reconcile"):
        read_movement_register_lines(lines, TBANK_MOVEMENTS, "reg.pdf",
                                     currency="RUB")


def test_a_summary_totals_line_that_cannot_be_read_warns_rather_than_raising(tmp_path):
    """`_check_summary_totals` had the identical exposure, and less excuse for
    it: a total that MISMATCHES there is already only a warning, so a total
    that cannot be parsed had no business being fatal."""
    import dataclasses
    from statement_parser import RUSSIAN_DOT_NUMBERS, read_text_period_summary
    from statement_profiles import TBANK_TEXT

    # Balances dot-decimal, totals left comma-decimal: `total_numbers` is
    # unset, so the totals are read with the rows' format and cannot parse.
    profile = dataclasses.replace(TBANK_TEXT, numbers=RUSSIAN_DOT_NUMBERS)
    path = typed_summary_file(
        tmp_path,
        "Balance on 22.11.2025: +1 000.00 ₽",
        "Balance on 21.12.2025: +1 500.00 ₽",
        "Income: +700,00 ₽",
        "Expenses: −200,00 ₽",
    )
    result = read_text_period_summary(path, profile, currency="RUB")

    assert [m.balance_after for m in result.movements] == [Decimal("1000.00"),
                                                           Decimal("1500.00")]
    assert any("could not be read" in w for w in result.warnings)


def test_a_summary_profile_reads_its_totals_in_the_totals_format(tmp_path):
    """Pins the `_check_summary_totals` call site itself.

    No shipped profile sets `total_numbers` on a `TextSummaryProfile`, so that
    site was unpinned: reverting it to `profile.numbers` passed green. This
    profile is synthetic for exactly that reason — the field exists on the
    class one document away from being needed, and an unpinned change site is
    how the register gap survived a full suite once already.
    """
    import dataclasses
    from statement_parser import (RUSSIAN_DOT_NUMBERS, RUSSIAN_NUMBERS,
                                  read_text_period_summary)
    from statement_profiles import TBANK_TEXT

    profile = dataclasses.replace(TBANK_TEXT, numbers=RUSSIAN_DOT_NUMBERS,
                                  total_numbers=RUSSIAN_NUMBERS)
    path = typed_summary_file(
        tmp_path,
        "Balance on 22.11.2025: +1 000.00 ₽",
        "Balance on 21.12.2025: +1 500.00 ₽",
        "Income: +700,00 ₽",
        "Expenses: −200,00 ₽",
    )
    result = read_text_period_summary(path, profile, currency="RUB")

    # Both formats were honoured: the balances read dot-decimal, the totals
    # read comma-decimal, and 1500 - 1000 == 700 - 200 reconciles.
    assert [m.balance_after for m in result.movements] == [Decimal("1000.00"),
                                                           Decimal("1500.00")]
    assert not any("could not be read" in w for w in result.warnings)
    assert not any("the stated totals sum to" in w for w in result.warnings)


def test_the_comma_vintage_register_is_readable_by_name_from_the_config():
    """Restores the coverage the dot-decimal switch deleted.

    Every comma-decimal row fixture went away when `tbank_movements` was bound
    to the dot vintage, which left the format that shipped previously pinned
    by nothing at all. It is pinned here instead — and pinned through
    `PROFILES`, because being selectable from `sources.json` by name is the
    entire reason this is a profile rather than a branch: a filer holding such
    a file must not have to edit `scripts/` to read it.
    """
    from statement_parser import read_movement_register_lines
    from statement_profiles import PROFILES, get_profile

    assert get_profile("tbank_movements_comma") is PROFILES["tbank_movements_comma"]

    lines = [
        "Movement of funds for the period 01.01.2025 - 31.12.2025",
        "14.07.2025  14.07.2025  +2 500,00 R    +2 500,00 R",
        "20.03.2025  20.03.2025  -250,50 R      -250,50 R",
        "Deposits: 2 500,00 R",
        "Expenses: 250,50 R",
    ]
    result = read_movement_register_lines(lines, get_profile("tbank_movements_comma"),
                                          "reg.pdf", currency="RUB")

    # Read, reconciled against the register's own totals, and reversed into
    # chronological order — the space groups are thousands, not decimals.
    assert [m.amount for m in result.movements] == [Decimal("-250.50"),
                                                    Decimal("2500.00")]
    assert not any("could not be read" in w for w in result.warnings)


def test_each_register_vintage_refuses_the_other_ones_rows():
    """The two profiles are not interchangeable, and neither degrades quietly
    into the other. A vintage picked wrongly in `sources.json` fails LOUDLY —
    which is the property the dot/comma split was worth having in the first
    place, and it holds in both directions."""
    from statement_parser import read_movement_register_lines
    from statement_profiles import TBANK_MOVEMENTS, TBANK_MOVEMENTS_COMMA

    header = "Movement of funds for the period 01.01.2025 - 31.12.2025"
    dot_row = "14.07.2025  14.07.2025  +2 500.00 R    +2 500.00 R"
    comma_row = "14.07.2025  14.07.2025  +2 500,00 R    +2 500,00 R"

    # A comma row read by the dot profile is REFUSED, never taken as a
    # thousands group that would turn 2 500,00 into 250000.
    with pytest.raises(StatementParseError, match="unexpected ','|does not group"):
        read_movement_register_lines([header, comma_row], TBANK_MOVEMENTS,
                                     "reg.pdf", currency="RUB")
    with pytest.raises(StatementParseError, match="unexpected '\\.'|does not group"):
        read_movement_register_lines([header, dot_row], TBANK_MOVEMENTS_COMMA,
                                     "reg.pdf", currency="RUB")


def test_totals_that_are_all_unreadable_are_not_reported_as_totals_absent():
    """A `total_numbers` that disagrees with the page disagrees with EVERY
    total line, not one — so "all unreadable" is the ORDINARY shape of that
    misconfiguration, not an edge of it.

    The register states its arithmetic in both lines. Reporting that it
    "states no period totals" would send the operator to the document when
    the remedy is one field in the profile.
    """
    from statement_parser import read_movement_register_lines
    from statement_profiles import TBANK_MOVEMENTS

    lines = [
        "Movement of funds for the period 01.01.2025 - 31.12.2025",
        "14.07.2025  14.07.2025  +2 500.00 R    +2 500.00 R",
        "Deposits: 2 500.00 R",
        "Expenses: 0.00 R",
    ]
    result = read_movement_register_lines(lines, TBANK_MOVEMENTS, "reg.pdf",
                                          currency="RUB")

    assert not any("states no period totals" in w for w in result.warnings)
    assert any("states 2 period total(s), none of which could be read" in w
               for w in result.warnings)
    assert any("total_numbers" in w for w in result.warnings)


def test_a_register_that_really_states_no_totals_still_says_so():
    """The other side of the same discrimination: a pattern that never matched
    is the document's shape, and nothing in the profile can fix it."""
    from statement_parser import read_movement_register_lines
    from statement_profiles import TBANK_MOVEMENTS

    lines = [
        "Movement of funds for the period 01.01.2025 - 31.12.2025",
        "14.07.2025  14.07.2025  +2 500.00 R    +2 500.00 R",
    ]
    result = read_movement_register_lines(lines, TBANK_MOVEMENTS, "reg.pdf",
                                          currency="RUB")

    assert any("states no period totals" in w for w in result.warnings)
    assert not any("none of which could be read" in w for w in result.warnings)


def test_new_bases_are_ordered_between_their_neighbours():
    """Position in BASIS_STRENGTH IS strength — `weaken_basis` is a max() on it.

    An anchored replay is exact arithmetic on a CITED opening, so it ranks just
    below a directly observed full year. A non-negativity floor is a proven
    bound, so it outranks a guess but says less about a peak than two bounding
    balances do.
    """
    from statement_parser import BASIS_STRENGTH, weaken_basis

    assert BASIS_STRENGTH.index("statement_full_year") \
        < BASIS_STRENGTH.index("anchored_full_year") \
        < BASIS_STRENGTH.index("statement_partial")
    assert BASIS_STRENGTH.index("period_end_summaries_only") \
        < BASIS_STRENGTH.index("non_negativity_floor") \
        < BASIS_STRENGTH.index("estimate")

    # Capping still only ever weakens.
    assert weaken_basis("anchored_full_year", "statement_partial") == "statement_partial"
    assert weaken_basis("statement_partial", "anchored_full_year") == "statement_partial"
    assert weaken_basis("non_negativity_floor", "estimate") == "estimate"

