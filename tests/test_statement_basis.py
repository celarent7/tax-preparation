#!/usr/bin/env python3
"""`max_value_basis` derivation, and the round trip into a roster.

This is the file that justifies the whole tool. `fbar_schema.MAX_VALUE_BASES`
already defines the vocabulary and `fbar_reconciler.v12_max_value_basis`
already warns on a weak one — but if a human types `statement_full_year` into a
roster by hand, V12 warns only when someone has already admitted the evidence
is weak, which makes it decorative. Derived from gap and continuity analysis,
**V12 fires honestly**.

The round trip is the other half: a timeline that cannot be carried into a
roster the reconciler accepts is a timeline nobody can use.
"""

import os
import sys
from datetime import date
from decimal import Decimal

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import fbar_reconciler as reconciler  # noqa: E402
from fbar_schema import MAX_VALUE_BASES, load_fbar_constants, validate_roster  # noqa: E402
from statement_parser import (  # noqa: E402
    BOUND_UNORDERED,
    EVIDENCE_NONE,
    EVIDENCE_PERIOD_END_SUMMARIES,
    EVIDENCE_RUNNING_BALANCE,
    MovementRecord,
    ReadResult,
    StatementPeriod,
    build_timeline,
    derive_basis,
    timeline_to_json,
)

TAX_YEAR = 2025
# Invented rates, chosen so the arithmetic is checkable by hand.
RATES = {"EUR": Decimal("2"), "GEL": Decimal("4")}


def movement(day, balance, *, amount=None, currency="USD", source_file="f.xlsx",
             row=1, order=0):
    return MovementRecord(
        date=day, description="", currency=currency, source_file=source_file,
        source_row=row, row_order=order,
        amount=amount if amount is None else Decimal(amount),
        balance_after=balance if balance is None else Decimal(balance),
    )


def full_year_source(source_file="year.xlsx", currency="USD", rows=None):
    """A running-balance file covering the whole calendar year with no gap."""
    rows = rows or [(date(2025, 1, 1), "1000"), (date(2025, 12, 31), "1200")]
    return ReadResult(
        source_file=source_file,
        currencies=[currency],
        evidence_kind=EVIDENCE_RUNNING_BALANCE,
        periods=[StatementPeriod(date(2025, 1, 1), date(2025, 12, 31), source_file)],
        movements=[movement(day, balance, currency=currency,
                            source_file=source_file, row=index + 2, order=index)
                   for index, (day, balance) in enumerate(rows)],
    )


# ---------------------------------------------------------------------------
# §6.3 — one test per row of the table
# ---------------------------------------------------------------------------

def test_running_balance_with_no_gaps_or_breaks_is_the_safe_harbour():
    basis, reasons = derive_basis([full_year_source()], [], [], [], has_balances=True)
    assert basis == "statement_full_year"
    assert any("safe harbour" in reason for reason in reasons)


def test_a_coverage_gap_caps_at_statement_partial():
    gaps = [{"start": "2025-12-22", "end": "2025-12-31", "days": 10,
             "position": "year_end"}]
    basis, reasons = derive_basis([full_year_source()], gaps, [], [], has_balances=True)
    assert basis == "statement_partial"
    assert any("free to move unobserved" in reason for reason in reasons)


def test_a_continuity_break_caps_at_statement_partial():
    breaks = [{"kind": "break", "severity": "warn"}]
    basis, _ = derive_basis([full_year_source()], [], breaks, [], has_balances=True)
    assert basis == "statement_partial"


def test_an_info_level_continuity_note_does_not_cap_anything():
    """A difference within one minor unit is rounding, not a missing file."""
    notes = [{"kind": "reconciles", "severity": "info"}]
    basis, _ = derive_basis([full_year_source()], [], notes, [], has_balances=True)
    assert basis == "statement_full_year"


def test_period_end_summaries_can_never_reach_a_full_year():
    """Two bounding balances per period leave everything between them
    invisible. A full year of such files is still a year of blind periods."""
    summaries = [
        ReadResult(source_file=f"text{month}.txt",
                   evidence_kind=EVIDENCE_PERIOD_END_SUMMARIES,
                   periods=[StatementPeriod(date(2025, month, 1),
                                            date(2025, month, 28),
                                            f"text{month}.txt")],
                   movements=[movement(date(2025, month, 1), "1000",
                                       source_file=f"text{month}.txt")])
        for month in range(1, 13)
    ]
    basis, reasons = derive_basis(summaries, [], [], [], has_balances=True)
    assert basis == "period_end_summaries_only"
    assert any("at best 'period_end_summaries_only'" in r for r in reasons)


def test_mixing_a_running_balance_with_summaries_takes_the_weaker():
    """The summarized months' intra-period peaks are still invisible, so the
    year's maximum is not fairly reflected however good the other file is."""
    mixed = [full_year_source(),
             ReadResult(source_file="text.txt",
                        evidence_kind=EVIDENCE_PERIOD_END_SUMMARIES,
                        movements=[movement(date(2025, 6, 1), "10")])]
    basis, _ = derive_basis(mixed, [], [], [], has_balances=True)
    assert basis == "period_end_summaries_only"


def test_movements_with_no_balances_anywhere_are_an_estimate():
    """A ledger yields CHANGES, not levels, and a reconstructed timeline is
    never promoted: completeness of a ledger is unverifiable from the ledger."""
    ledger = ReadResult(source_file="movements.xlsx",
                        evidence_kind=EVIDENCE_RUNNING_BALANCE,
                        movements=[movement(date(2025, 6, 1), None, amount="100")])
    basis, reasons = derive_basis([ledger], [], [], [], has_balances=False)
    assert basis == "estimate"
    assert any("anchor balance" in reason for reason in reasons)


def test_no_files_at_all_is_unknown_and_says_it_is_claudes_call():
    """An institution with no statement files at all."""
    basis, reasons = derive_basis([], [], [], [], has_balances=False)
    assert basis == "unknown"
    assert any("Item 15a" in reason for reason in reasons)


def test_an_empty_export_contributes_nothing_rather_than_a_zero():
    """Wise: the sheet is one cell. `unknown`, never `$0`."""
    empty = ReadResult(source_file="wise.xlsx", no_data=True,
                       evidence_kind=EVIDENCE_NONE)
    basis, _ = derive_basis([empty], [], [], [], has_balances=False)
    assert basis == "unknown"


def test_a_bounded_interval_cannot_derive_a_full_year():
    """§8.1: a timeline with any interval_uncertainty reports a bound rather
    than a determined figure, and a bound is not the safe harbour."""
    uncertainty = [{"kind": BOUND_UNORDERED, "date": "2025-06-05"}]
    basis, reasons = derive_basis([full_year_source()], [], [], uncertainty,
                                  has_balances=True)
    assert basis == "statement_partial"
    assert any("bound rather than a determined figure" in r for r in reasons)


def test_a_period_derived_from_its_own_rows_cannot_derive_a_full_year():
    source = full_year_source()
    source.period_inferred_from_rows = True
    basis, reasons = derive_basis([source], [], [], [], has_balances=True)
    assert basis == "statement_partial"
    assert any("missing from either end" in reason for reason in reasons)


def test_every_derived_basis_is_in_the_schema_vocabulary():
    for sources, gaps, breaks, uncertainty, balances in [
        ([full_year_source()], [], [], [], True),
        ([full_year_source()], [{"days": 1, "start": "a", "end": "b",
                                 "position": "year_end"}], [], [], True),
        ([], [], [], [], False),
    ]:
        basis, _ = derive_basis(sources, gaps, breaks, uncertainty, balances)
        assert basis in MAX_VALUE_BASES


# ---------------------------------------------------------------------------
# Timeline assembly end to end
# ---------------------------------------------------------------------------

def test_a_clean_full_year_timeline_derives_the_safe_harbour():
    timeline = build_timeline("acct-1", TAX_YEAR, [full_year_source()], RATES)
    assert timeline.max_value_basis == "statement_full_year"
    assert timeline.max_usd == Decimal("1200")
    assert timeline.date_of_maximum == date(2025, 12, 31)
    assert timeline.gaps == []
    assert timeline.max_usd_bound is None


def test_the_december_gap_shows_up_in_the_basis_without_anyone_asserting_it():
    """The point of the whole tool: nobody typed `statement_partial`. The
    parser found ten uncovered days at the end of the year and derived it."""
    source = full_year_source()
    source.periods = [StatementPeriod(date(2025, 1, 1), date(2025, 12, 21), "year.xlsx")]
    timeline = build_timeline("acct-1", TAX_YEAR, [source], RATES)
    assert timeline.max_value_basis == "statement_partial"
    assert [g["position"] for g in timeline.gaps] == ["year_end"]


def test_a_timeline_with_no_sources_states_no_figure(  ):
    timeline = build_timeline("bcs-1", TAX_YEAR, [], RATES)
    assert timeline.max_usd is None
    assert timeline.max_value_basis == "unknown"
    assert timeline.balance_dec_31 is None
    assert timeline.movement_count == 0


# ---------------------------------------------------------------------------
# §6.2 — Decimal stops at the JSON boundary
# ---------------------------------------------------------------------------

def test_emitted_money_is_json_numbers_not_decimal_strings():
    """`fbar_schema._number` rejects anything that is not an int or a float, so
    emitting Decimal-as-string makes the timeline unusable as roster input."""
    payload = timeline_to_json(build_timeline("acct-1", TAX_YEAR,
                                              [full_year_source()], RATES))
    assert isinstance(payload["max_usd"], float)
    assert payload["max_usd"] == 1200.0
    for component in payload["max_value_components"]:
        assert isinstance(component["amount"], float)


def test_emitted_payload_is_json_serializable():
    import json

    payload = timeline_to_json(build_timeline("acct-1", TAX_YEAR,
                                              [full_year_source()], RATES))
    assert json.loads(json.dumps(payload))["account_key"] == "acct-1"


# ---------------------------------------------------------------------------
# §11 round trip — parser output becomes a roster the reconciler accepts
# ---------------------------------------------------------------------------

def roster_from_timelines(timelines, *, rates=None):
    """Build the roster a Phase 1 agent would write from these timelines.

    Only the parser-derived fields are taken from the timeline. Reportability,
    ownership, the account type and the Item 15a election are Claude's, and
    they are supplied here the way Claude would supply them.
    """
    payloads = [timeline_to_json(t) for t in timelines]
    return {
        "schema_version": "1.0",
        "tax_year": TAX_YEAR,
        "filer": {"slug": "primary"},
        "fx_rates": [
            {"currency": currency, "units_per_usd": float(units),
             "as_of": f"{TAX_YEAR}-12-31", "source": "treasury"}
            for currency, units in sorted((rates or RATES).items())
        ],
        "accounts": [{
            "account_key": payload["account_key"],
            "account_number": f"****{index:04d}",
            "account_type": "bank",
            "institution": {"name": "Example Bank", "country": "Georgia"},
            "currencies": payload["currencies"],
            "max_value_usd": payload["max_usd"],
            "max_value_components": payload["max_value_components"] or None,
            "max_value_date": payload["date_of_maximum"],
            "max_value_basis": payload["max_value_basis"],
            "year_end_balance_usd": payload["year_end_balance_usd"],
        } for index, payload in enumerate(payloads, start=1)],
    }


def validate_roster_findings(roster_dict, workspace=None):
    roster = validate_roster(roster_dict, origin="<round-trip>")
    fbar = load_fbar_constants(str(TAX_YEAR))
    return roster, reconciler.validate(roster, None, fbar, workspace, None)


def test_a_parser_built_roster_validates_with_no_error_severity_findings():
    timeline = build_timeline("acct-1", TAX_YEAR, [full_year_source()], RATES)
    _, validation = validate_roster_findings(roster_from_timelines([timeline]))
    errors = [f for f in validation["findings"] if f.severity in ("blocking", "error")]
    assert errors == [], [f.message for f in errors]


def test_v12_reports_exactly_the_derived_basis():
    """V12 warns on a reportable account whose basis is weaker than a full year
    and which has not elected Item 15a. With the basis DERIVED, that warning is
    a statement about the evidence rather than about someone's typing."""
    weak = full_year_source()
    weak.periods = [StatementPeriod(date(2025, 1, 1), date(2025, 12, 21), "year.xlsx")]
    timeline = build_timeline("acct-weak", TAX_YEAR, [weak], RATES)
    _, validation = validate_roster_findings(roster_from_timelines([timeline]))

    v12 = [f for f in validation["findings"] if f.id == "V12"]
    assert len(v12) == 1
    assert v12[0].account_key == "acct-weak"
    assert v12[0].detail["max_value_basis"] == timeline.max_value_basis == "statement_partial"


def test_a_clean_full_year_timeline_draws_no_v12_warning():
    timeline = build_timeline("acct-1", TAX_YEAR, [full_year_source()], RATES)
    _, validation = validate_roster_findings(roster_from_timelines([timeline]))
    assert [f for f in validation["findings"] if f.id == "V12"] == []


def multi_currency_timeline():
    """USD peaks in March; EUR peaks in June. The combined peak is March.

    USD:  1000 from 5 Mar, 100 from 5 Jun
    EUR:  -200 (= -$100) from 5 Jan, 1000 (= $500) from 6 Jun
    Combined peak: 5 Mar = 1000 + (-100) = $900, components signed.
    """
    usd = ReadResult(
        source_file="usd.xlsx", currencies=["USD"],
        evidence_kind=EVIDENCE_RUNNING_BALANCE,
        periods=[StatementPeriod(date(2025, 1, 1), date(2025, 12, 31), "usd.xlsx")],
        movements=[movement(date(2025, 3, 5), "1000", currency="USD",
                            source_file="usd.xlsx", row=2, order=0),
                   movement(date(2025, 6, 5), "100", currency="USD",
                            source_file="usd.xlsx", row=3, order=1)])
    eur = ReadResult(
        source_file="eur.xlsx", currencies=["EUR"],
        evidence_kind=EVIDENCE_RUNNING_BALANCE,
        periods=[StatementPeriod(date(2025, 1, 1), date(2025, 12, 31), "eur.xlsx")],
        movements=[movement(date(2025, 1, 5), "-200", currency="EUR",
                            source_file="eur.xlsx", row=2, order=0),
                   movement(date(2025, 6, 6), "1000", currency="EUR",
                            source_file="eur.xlsx", row=3, order=1)])
    return build_timeline("tbc-group", TAX_YEAR, [usd, eur], RATES,
                          account_group="tbc-group")


def test_a_negative_component_survives_v3_because_nothing_was_floored():
    """The case §8.2 exists for. `negative_balance_is_zero` applied per currency
    would floor the EUR component to 0 while `max_value_usd` stayed the signed
    sum; V3 re-converts every component, sums them, and compares at a $1.00
    tolerance with severity `error` — a non-zero exit on a roster that is
    otherwise correct.

    This test fails if a convention is ever applied inside the parser.
    """
    timeline = multi_currency_timeline()
    assert timeline.max_components["EUR"] == Decimal("-200")
    assert timeline.max_usd == Decimal("900")

    roster, validation = validate_roster_findings(roster_from_timelines([timeline]))
    errors = [f for f in validation["findings"] if f.severity in ("blocking", "error")]
    assert errors == [], [f.message for f in errors]

    v3 = [f for f in validation["findings"] if f.id == "V3"]
    assert v3 == []

    # And the components really do reconcile to the stated maximum.
    account = roster["accounts"][0]
    recomputed = sum(
        component["amount"] if component["currency"] == "USD"
        else component["amount"] / float(RATES[component["currency"]])
        for component in account["max_value_components"])
    assert abs(account["max_value_usd"] - recomputed) < 1.0


def test_flooring_a_component_by_hand_is_what_v3_catches():
    """The negative test for the one above: if a convention HAD been applied to
    the components alone, V3 hard-errors. That is what makes the rule
    load-bearing rather than stylistic."""
    roster_dict = roster_from_timelines([multi_currency_timeline()])
    for component in roster_dict["accounts"][0]["max_value_components"]:
        component["amount"] = max(component["amount"], 0.0)

    _, validation = validate_roster_findings(roster_dict)
    v3 = [f for f in validation["findings"] if f.id == "V3"]
    assert len(v3) == 1
    assert v3[0].severity == "error"


def test_the_emitted_source_documents_are_what_v13_looks_for(tmp_path):
    """A workpaper citing files that are not in the workspace does not satisfy
    the five-year retention rule — the parser cites exactly the files it read,
    so V13 is checking a real claim."""
    timeline = build_timeline("acct-1", TAX_YEAR, [full_year_source()], RATES)
    roster_dict = roster_from_timelines([timeline])
    roster_dict["accounts"][0]["source_documents"] = timeline.source_files

    _, missing = validate_roster_findings(roster_dict, workspace=str(tmp_path))
    assert [f.id for f in missing["findings"] if f.id == "V13"] == ["V13"]

    (tmp_path / "year.xlsx").write_text("", encoding="utf-8")
    _, present = validate_roster_findings(roster_dict, workspace=str(tmp_path))
    assert [f for f in present["findings"] if f.id == "V13"] == []


def test_an_empty_export_caps_the_basis_even_when_a_sibling_period_spans_it():
    """Regression: an empty export contributes no movements and no period, so
    where a SIBLING file's period covers the same days the gap check sees full
    coverage and the basis stayed `statement_full_year` — a safe-harbour claim
    resting partly on a file that stated nothing.

    That is hazard 9.6 arriving through the back door: the reader correctly
    refuses to call the empty file a zero balance, and the basis then treats it
    as if it were not there at all.
    """
    covering = ReadResult(
        source_file="usd.xlsx",
        evidence_kind=EVIDENCE_RUNNING_BALANCE,
        movements=[
            MovementRecord(date(2025, 1, 1), "open", "USD", "usd.xlsx", 2,
                           balance_after=Decimal("100")),
            MovementRecord(date(2025, 12, 31), "close", "USD", "usd.xlsx", 3,
                           balance_after=Decimal("200"), row_order=1),
        ],
        periods=[StatementPeriod(date(2025, 1, 1), date(2025, 12, 31), "usd.xlsx")],
    )
    empty = ReadResult(source_file="gel.xlsx", evidence_kind=EVIDENCE_RUNNING_BALANCE,
                       no_data=True)

    timeline = build_timeline("k", TAX_YEAR, [covering, empty], {})
    assert timeline.gaps == []
    assert timeline.max_value_basis == "statement_partial"
    assert any("gel.xlsx" in w and "stated nothing" in w
               for w in timeline.parser_warnings)


def test_a_register_only_account_derives_the_estimate_basis():
    """A ledger yields CHANGES, not levels. `derive_basis` already caps a
    balance-free read at `estimate`; this pins that a register reaches it, so
    no later change can quietly let movements support a stronger claim."""
    movements = [
        MovementRecord(date(2025, 3, 20), "", "RUB", "reg.pdf", 5,
                       amount=Decimal("-250.50"), balance_after=None, row_order=0),
        MovementRecord(date(2025, 7, 14), "", "RUB", "reg.pdf", 6,
                       amount=Decimal("2500.00"), balance_after=None, row_order=1),
    ]
    register = ReadResult(
        source_file="reg.pdf", evidence_kind=EVIDENCE_RUNNING_BALANCE,
        movements=movements,
        periods=[StatementPeriod(date(2025, 1, 1), date(2025, 12, 31), "reg.pdf")])

    timeline = build_timeline("k", TAX_YEAR, [register], {})
    assert timeline.max_value_basis == "estimate"
    assert any("no level can be established" in w or "anchor balance" in w
               for w in timeline.parser_warnings)


def _register_result(currency="RUB"):
    """A movement register: transaction-level evidence, no balance anywhere.

    `totals_reconciled=True` because the bank's own period totals matched the
    parsed rows — the completeness proof every anchored reconstruction rests on.

    It also carries a FULL-YEAR period. Reconciled totals prove a ledger
    complete only within the periods present, so `build_timeline` declines both
    reconstructions outright when coverage has a gap. Without a period here the
    coverage gate fires first and the multi-currency and multi-anchor branches
    below it are never reached — the tests would pass on the wrong reason.
    """
    from statement_parser import (EVIDENCE_RUNNING_BALANCE, MovementRecord,
                                  ReadResult, StatementPeriod)
    source = f"reg_{currency.lower()}.pdf"
    result = ReadResult(
        movements=[MovementRecord(date=date(2025, 1, 10), description="txn",
                                  currency=currency, source_file=source,
                                  source_row=1, amount=Decimal("100"),
                                  balance_after=None)],
        source_file=source, currencies=[currency],
        evidence_kind=EVIDENCE_RUNNING_BALANCE, totals_reconciled=True)
    result.periods = [StatementPeriod(start=date(2025, 1, 1),
                                      end=date(2025, 12, 31), source_file=source)]
    return result


def _anchor_result(when=date(2025, 6, 1), balance=Decimal("1000"),
                   source_file="bal.pdf", currency="RUB"):
    """One stated level at one date, as `read_point_balance` emits it.

    No period: a point balance has no span and must contribute no coverage.
    `totals_reconciled` stays None — it is not a register, and reading that
    None as a failure would block every reconstruction it exists to enable.
    """
    from statement_parser import EVIDENCE_DECLARED_ANCHOR, MovementRecord, ReadResult
    return ReadResult(
        movements=[MovementRecord(date=when, description="stated available balance",
                                  currency=currency, source_file=source_file,
                                  source_row=1, amount=None, balance_after=balance)],
        source_file=source_file, currencies=[currency],
        evidence_kind=EVIDENCE_DECLARED_ANCHOR)


def test_a_register_with_a_verified_anchor_is_anchored_not_an_estimate():
    """A complete, reconciled ledger plus a cited dated level is exact.

    Recording this as `estimate` was the ad hoc workaround this replaces: it
    told the filer to obtain year-covering records they already had.
    """
    from statement_parser import derive_basis
    basis, reasons = derive_basis(
        [_register_result()], gaps=[], continuity_breaks=[],
        interval_uncertainty=[], has_balances=False,
        anchor_available=True, ledger_reconciled=True)
    assert basis == "anchored_full_year"
    assert any("anchor" in r for r in reasons)


def test_a_register_with_no_anchor_but_full_reconciled_coverage_is_a_floor():
    from statement_parser import derive_basis
    basis, reasons = derive_basis(
        [_register_result()], gaps=[], continuity_breaks=[],
        interval_uncertainty=[], has_balances=False,
        anchor_available=False, ledger_reconciled=True, floor_available=True)
    assert basis == "non_negativity_floor"
    assert any("cannot go negative" in r for r in reasons)


def test_an_unreconciled_ledger_stays_an_estimate_even_with_an_anchor():
    """Exactness rests on the ledger being COMPLETE. The bank's own totals are
    what prove that; without them the anchor buys nothing."""
    from statement_parser import derive_basis
    basis, _ = derive_basis(
        [_register_result()], gaps=[], continuity_breaks=[],
        interval_uncertainty=[], has_balances=False,
        anchor_available=True, ledger_reconciled=False)
    assert basis == "estimate"


def test_an_anchored_account_with_a_coverage_gap_is_still_partial():
    """Capping composes. An anchor does not close a hole in the year."""
    from statement_parser import derive_basis
    basis, _ = derive_basis(
        [_register_result()],
        gaps=[{"start": "2025-03-01", "end": "2025-03-31", "days": 30,
               "position": "middle"}],
        continuity_breaks=[], interval_uncertainty=[], has_balances=False,
        anchor_available=True, ledger_reconciled=True)
    assert basis == "statement_partial"


def test_the_defaults_preserve_existing_behaviour():
    """Every existing caller passes neither flag and must be unaffected."""
    from statement_parser import derive_basis
    basis, _ = derive_basis([_register_result()], gaps=[], continuity_breaks=[],
                            interval_uncertainty=[], has_balances=False)
    assert basis == "estimate"


def test_an_anchor_is_declined_on_a_multi_currency_group():
    """`carry_balance` sums Decimals with no currency awareness, and this group
    merges currencies onto a USD axis. Converting the anchor at a rate its own
    document never mentioned would be the silent wrong number this module
    exists to prevent."""
    from statement_parser import build_timeline

    timeline = build_timeline(
        "grp", 2025,
        [_register_result(), _register_result(currency="USD"), _anchor_result()],
        fx_rates={"RUB": Decimal("81.996"), "USD": Decimal("1")})
    assert timeline.reconstruction is None
    assert timeline.max_value_basis != "anchored_full_year"
    assert any("SINGLE-CURRENCY" in w for w in timeline.parser_warnings)


def test_two_anchors_that_disagree_produce_no_reconstruction():
    """Each anchor is carried independently and they must agree. A
    disagreement means the ledger between them is incomplete — which is exactly
    the precondition the exactness claim rests on."""
    from statement_parser import build_timeline

    timeline = build_timeline(
        "grp", 2025,
        [_register_result(),
         _anchor_result(when=date(2025, 6, 1), balance=Decimal("1000"),
                        source_file="bal_a.pdf"),
         _anchor_result(when=date(2025, 6, 1), balance=Decimal("9999"),
                        source_file="bal_b.pdf")],
        fx_rates={"RUB": Decimal("81.996")})
    assert timeline.reconstruction is None
    assert any("DIFFERENT January 1 openings" in w for w in timeline.parser_warnings)

