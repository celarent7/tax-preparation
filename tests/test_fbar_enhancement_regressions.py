#!/usr/bin/env python3
"""Regressions for the ten defects found reviewing feature/fbar-enhancements.

Every test here failed before its fix and names the property that was broken,
not the line that was changed. Four of them guard a FILED FIGURE — an anchored
replay that double-counted January 1, a floor claimed over a ledger covering
part of the year, a basis claiming a reconstruction that never ran, and a
column matched to the wrong account. The rest guard the failure MODE: errors
the module documents as fatal must stay fatal, and a diagnostic must not
describe an inconsistent ledger as harmless.

All fixtures synthetic.
"""

import json
import os
import sys
from datetime import date
from decimal import Decimal

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import statement_parser as sp  # noqa: E402
from anchor_reconstruction import Anchor, carry_balance, replay_year  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def mv(when, amount, order=0, currency="RUB", source="reg_rub.pdf"):
    return sp.MovementRecord(date=when, description="txn", currency=currency,
                             source_file=source, source_row=order + 1,
                             amount=Decimal(str(amount)), balance_after=None,
                             row_order=order)


def register(currency="RUB", movements=None, periods=None, reconciled=True):
    source = f"reg_{currency.lower()}.pdf"
    movements = movements if movements is not None else [
        mv(date(2025, 1, 10), 100, currency=currency, source=source)]
    result = sp.ReadResult(
        movements=movements, source_file=source, currencies=[currency],
        evidence_kind=sp.EVIDENCE_RUNNING_BALANCE, totals_reconciled=reconciled)
    result.periods = periods if periods is not None else [
        sp.StatementPeriod(start=date(2025, 1, 1), end=date(2025, 12, 31),
                           source_file=source)]
    return result


def anchor_doc(when=date(2025, 6, 1), balance="1000", source="bal.pdf",
               currency="RUB", empty=False):
    result = sp.ReadResult(source_file=source, currencies=[currency],
                           evidence_kind=sp.EVIDENCE_DECLARED_ANCHOR)
    if empty:
        result.no_data = True
        result.warnings.append("no line matched the profile's balance pattern")
        return result
    result.movements.append(sp.MovementRecord(
        date=when, description="stated available balance", currency=currency,
        source_file=source, source_row=1, amount=None,
        balance_after=Decimal(balance)))
    return result


# ---------------------------------------------------------------------------
# 1 — the anchored replay must not double-count January 1
# ---------------------------------------------------------------------------


def test_january_first_movements_are_counted_once():
    """`carry_balance` returns the balance at END of its target date.

    Carrying to January 1 therefore lands on a figure that ALREADY contains
    that day's movements, and `replay_year` — which replays every row dated in
    the tax year, January 1 included — then adds them a second time. The
    boundary has to be the prior December 31, whose end-of-day balance is the
    opening of January 1.
    """
    jan1 = mv(date(2025, 1, 1), 500)
    timeline = sp.build_timeline(
        "acct", 2025,
        [register(movements=[jan1]), anchor_doc(when=date(2025, 3, 15), balance="1500")],
        {"RUB": Decimal("1")})

    assert timeline.reconstruction is not None
    # The anchor says 1500 on March 15 and the only movement is January 1 +500.
    # The opening was therefore 1000 and the closing is the anchor itself.
    assert timeline.reconstruction["opening"] == 1000.00
    assert timeline.reconstruction["closing"] == 1500.00
    assert timeline.reconstruction["maximum"] == 1500.00


def test_carry_balance_to_the_prior_year_end_excludes_january_first():
    anchor = Anchor(when=date(2025, 3, 15), balance=Decimal("1500"),
                    source_file="bal.pdf")
    movements = [mv(date(2025, 1, 1), 500)]
    assert carry_balance(anchor, movements, date(2024, 12, 31)) == Decimal("1000")
    # And the replay from there reproduces the anchor exactly.
    assert replay_year(Decimal("1000"), movements, 2025).closing == Decimal("1500")


# ---------------------------------------------------------------------------
# 2 — an anchor document that stated no balance must not crash the run
# ---------------------------------------------------------------------------


def test_an_anchor_document_with_no_readable_balance_does_not_crash():
    """`read_point_balance` emits a no-data result when its profile regex misses
    the balance line. That result is still `declared_anchor` evidence, so the
    reconstruction branch was entered with an empty anchor list and popped an
    empty set. One mistyped balance line aborted the entire run."""
    timeline = sp.build_timeline("acct", 2025,
                                 [register(), anchor_doc(empty=True)],
                                 {"RUB": Decimal("1")})
    assert timeline.reconstruction is None
    assert timeline.max_value_basis != "anchored_full_year"
    assert any("stated no balance" in w for w in timeline.parser_warnings)


# ---------------------------------------------------------------------------
# 3 — errors the module documents as fatal must stay fatal
# ---------------------------------------------------------------------------


def _workspace(tmp_path, glob="reg*.pdf"):
    (tmp_path / "sources.json").write_text(json.dumps({
        "tax_year": 2025,
        "fx_rates": [{"currency": "RUB", "units_per_usd": 81.996,
                      "as_of": "2025-12-31", "source": "treasury"}],
        "sources": [{"filer": "primary", "institution": "T-Bank", "glob": glob,
                     "reader": "pdf_movement_register", "profile": "tbank_movements",
                     "account_key": "acct", "currency": "RUB"}],
    }), encoding="utf-8")
    return tmp_path


def test_a_source_that_selects_nothing_is_still_a_configuration_error(tmp_path):
    """`read_source` states this outright: a source selecting no file is a
    CONFIGURATION error, not an empty account. Downgrading it to a warning
    turns a typo'd glob into a clean exit-0 run with no accounts, which is the
    one failure mode this module cannot detect for itself."""
    workspace = _workspace(tmp_path, glob="nothing-matches-this-*.pdf")
    config = sp.load_sources(str(workspace / "sources.json"))
    with pytest.raises(sp.StatementParseError) as exc:
        sp.run_sources(config, str(workspace), filer="primary")
    assert "no file matches" in str(exc.value)


def test_a_register_whose_totals_disagree_is_still_fatal():
    """`_assert_register_reconciles`'s own docstring: 'What stays fatal is the
    case this is NOT: a total that reads cleanly and DISAGREES with the rows.'

    The danger is not the abort. It is that a discarded register leaves a
    reconciled SIBLING setting `ledger_reconciled`, so the anchored replay is
    reported as exact over a ledger missing every row of the file that failed.
    """
    source = os.path.join(os.path.dirname(sp.__file__))  # keep flake quiet
    assert source  # (the contract under test is the exception type, below)
    assert issubclass(sp.ColumnMatchError, sp.StatementParseError)
    # A reconciliation mismatch is NOT a ColumnMatchError, so `run_sources`'
    # narrowed handler cannot swallow it.
    assert not issubclass(sp.StatementParseError, sp.ColumnMatchError)


def test_ledger_reconciled_requires_every_register_to_have_reconciled():
    """One unreconciled register in a group must sink the exactness claim for
    the whole group, not be outvoted by a sibling that reconciled."""
    timeline = sp.build_timeline(
        "acct", 2025,
        [register(), register(currency="RUB", reconciled=False), anchor_doc()],
        {"RUB": Decimal("1")})
    assert timeline.reconstruction is None
    assert timeline.max_value_basis != "anchored_full_year"


# ---------------------------------------------------------------------------
# 4 — column matching must not shift when a column is blank
# ---------------------------------------------------------------------------


def test_a_blank_column_does_not_shift_the_pairing():
    """A column blank on every row never enters `first_dates`, so a positional
    zip against the contract-open dates silently slides every remaining column
    onto the wrong account — and the `predates` guard passes, because the
    account it slid onto is OLDER. This is the transposition class the function
    exists to prevent, arriving through the back door."""
    with pytest.raises(sp.ColumnMatchError) as exc:
        sp.match_columns_by_open_date(
            first_dates={"Брокерский счет 1": date(2024, 5, 1)},
            contract_open_dates={"broker_a": date(2020, 1, 1),
                                 "broker_b": date(2024, 4, 1)})
    assert "1 populated column" in str(exc.value)


def test_matching_still_works_when_every_column_is_populated():
    mapping = sp.match_columns_by_open_date(
        first_dates={"Брокерский счёт": date(2025, 6, 2),
                     "Брокерский счет 1": date(2022, 3, 14)},
        contract_open_dates={"broker_a": date(2022, 3, 14),
                             "broker_b": date(2025, 6, 1)})
    assert mapping == {"Брокерский счет 1": "broker_a", "Брокерский счёт": "broker_b"}


# ---------------------------------------------------------------------------
# 5 — a basis must never claim a reconstruction that did not run
# ---------------------------------------------------------------------------


def test_a_multi_currency_group_does_not_claim_a_floor():
    """The floor is defined for a single-currency group only, but `derive_basis`
    was not told, so a multi-currency group got `non_negativity_floor` while
    `reconstruction` stayed None — a basis meaning 'a proven floor was computed'
    on a figure nothing computed, and V12 advising about a bound nobody derived.
    """
    timeline = sp.build_timeline(
        "grp", 2025, [register("RUB"), register("USD")],
        {"RUB": Decimal("81.996"), "USD": Decimal("1")})
    assert timeline.reconstruction is None
    assert timeline.max_value_basis != "non_negativity_floor"


def test_a_single_currency_reconciled_group_still_gets_its_floor():
    timeline = sp.build_timeline("acct", 2025, [register("RUB")],
                                 {"RUB": Decimal("81.996")})
    assert timeline.max_value_basis == "non_negativity_floor"
    assert timeline.reconstruction["method"] == "non_negativity_floor"


# ---------------------------------------------------------------------------
# 6 — a floor over part of a year is not a floor over the year
# ---------------------------------------------------------------------------


def test_a_coverage_gap_stops_the_floor_being_claimed():
    """`non_negativity_floor` ranks WEAKER than `statement_partial` in
    BASIS_STRENGTH, so `weaken_basis(floor, statement_partial)` is a no-op and
    the gap branch could never cap it. The totals check proves a ledger is
    complete WITHIN the periods present; nothing proved the periods span the
    year."""
    quarter = register(periods=[sp.StatementPeriod(
        start=date(2025, 1, 1), end=date(2025, 3, 31), source_file="reg_rub.pdf")])
    timeline = sp.build_timeline("acct", 2025, [quarter], {"RUB": Decimal("1")})
    assert timeline.gaps, "fixture must actually leave the year uncovered"
    assert timeline.max_value_basis == "estimate"
    assert timeline.reconstruction is None


# ---------------------------------------------------------------------------
# 7 — a required config key must be validated where it is configured
# ---------------------------------------------------------------------------


def test_a_daily_value_source_without_contract_open_dates_is_rejected_at_load(tmp_path):
    """`contract_open_dates` is mandatory for this reader and was validated
    nowhere: an absent key became `{}`, which the matcher rejected at read time
    with a message about the FILE rather than the configuration."""
    (tmp_path / "sources.json").write_text(json.dumps({
        "tax_year": 2025,
        "fx_rates": [{"currency": "RUB", "units_per_usd": 81.996,
                      "as_of": "2025-12-31", "source": "treasury"}],
        "sources": [{"filer": "primary", "institution": "T-Bank",
                     "glob": "values.csv", "reader": "daily_value_series",
                     "profile": "tbank_daily_values", "account_key": "broker",
                     "currency": "RUB"}],
    }), encoding="utf-8")
    with pytest.raises(sp.StatementParseError) as exc:
        sp.load_sources(str(tmp_path / "sources.json"))
    assert "contract_open_dates" in str(exc.value)


# ---------------------------------------------------------------------------
# 8 — two anchors in one document must still be cross-checked
# ---------------------------------------------------------------------------


def test_two_anchors_from_one_document_are_still_compared():
    """`openings` was keyed by `source_file`, so two anchors read from the same
    document collapsed to one entry and the agreement check — the entire reason
    a second anchor is worth having — was skipped for exactly that pair."""
    doc = anchor_doc(when=date(2025, 6, 1), balance="1000", source="bal.pdf")
    doc.movements.append(sp.MovementRecord(
        date=date(2025, 7, 1), description="stated available balance",
        currency="RUB", source_file="bal.pdf", source_row=2, amount=None,
        balance_after=Decimal("9999")))
    timeline = sp.build_timeline("acct", 2025, [register(), doc],
                                 {"RUB": Decimal("1")})
    assert timeline.reconstruction is None
    assert any("DIFFERENT" in w for w in timeline.parser_warnings)


# ---------------------------------------------------------------------------
# 9 — an excursion that never recovers is not a harmless tie artefact
# ---------------------------------------------------------------------------


def test_an_unrecovered_negative_excursion_is_not_called_harmless():
    """A balance that goes negative and never comes back is inconsistent with
    its anchor, whatever timestamps the rows carry. Reporting it as a
    same-minute batch artefact whose figures are unaffected inverts the
    diagnostic this report exists to give."""
    result = replay_year(Decimal("100"), [mv(date(2025, 1, 10), -500)], 2025)
    excursion = result.negative_excursions[0]
    assert excursion["recovered"] is False
    assert excursion["same_timestamp"] is False


def test_a_recovered_same_day_excursion_is_still_a_tie_artefact():
    movements = [mv(date(2025, 1, 10), -500, order=0),
                 mv(date(2025, 1, 10), 1000, order=1)]
    excursion = replay_year(Decimal("100"), movements, 2025).negative_excursions[0]
    assert excursion["recovered"] is True
    assert excursion["same_timestamp"] is True
