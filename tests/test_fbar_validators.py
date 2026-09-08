#!/usr/bin/env python3
"""FBAR validators V1-V13 (docs/fbar_module_plan.md §8) and the schema loader.

Two properties matter beyond "does it fire":

  * The blocking set is small and deliberate. Item 15a ("amount unknown"),
    a non-Treasury rate awaiting replacement, and an incomplete address are
    LEGITIMATE interim states — they warn and never block. A tool that
    refused to run on them would simply be worked around.
  * V10 is scoped to the SPOUSAL Form 114a. The same form has a second,
    unrelated use (third-party preparer, Form 114 box 44a); conflating them
    hard-errors on the correct, normal filing pattern.

All fixtures are synthetic.
"""

import os
import sys
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import fbar_reconciler as fr  # noqa: E402
from fbar_schema import (  # noqa: E402
    RosterError,
    fbar_deadlines,
    fbar_penalty_maximum,
    load_fbar_constants,
    load_roster,
    record_retention_until,
    threshold_tripped,
    validate_roster,
)

FBAR = load_fbar_constants("2025")
EXAMPLES = os.path.join(ROOT, "examples", "fbar")


def account(key, **overrides):
    base = {
        "account_key": key,
        "account_number": f"NUM-{key}",
        "account_type": "bank",
        "ownership": "separate",
        "institution": {
            "name": "Test Bank",
            "country": "GE",
            "address": {"street": "1 Test Street", "city": "Tbilisi", "postal_code": "0100"},
        },
        "currencies": ["USD"],
        "max_value_local": 5000.0,
        "max_value_currency": "USD",
        "max_value_usd": 5000.0,
        "max_value_basis": "statement_full_year",
        "reportable": True,
        "account_status": "open",
    }
    base.update(overrides)
    return base


def roster(accounts, year=2025, fx=None, **overrides):
    base = {
        "schema_version": "1.0",
        "tax_year": year,
        "filer": {"slug": "test"},
        "fx_rates": fx if fx is not None else [
            {"currency": "USD", "units_per_usd": 1.0, "as_of": f"{year}-12-31",
             "source": "treasury"}],
        "accounts": accounts,
    }
    base.update(overrides)
    return validate_roster(base, origin="<test>")


def run(current, prior=None, workspace=None, filing_date=date(2026, 6, 1)):
    return fr.validate(current, prior, FBAR, workspace, filing_date)


def ids(result):
    return [f.id for f in result["findings"]]


def by_id(result, wanted):
    return [f for f in result["findings"] if f.id == wanted]


# ---------------------------------------------------------------------------
# V1 / V2 — FX provenance and date
# ---------------------------------------------------------------------------


def test_fx_non_treasury_warns():
    """D4 shape: an xe.com mid-market rate standing in for the Treasury rate."""
    result = run(roster([account("a", max_value_currency="EUR", max_value_local=4261.0,
                                 max_value_usd=5000.0, currencies=["EUR"])],
                        fx=[{"currency": "EUR", "units_per_usd": 0.8522,
                             "as_of": "2025-12-31", "source": "xe"}]))
    warnings = by_id(result, "V1")
    assert len(warnings) == 1
    assert warnings[0].severity == "warn"
    assert "fiscaldata.treasury.gov" in warnings[0].message
    assert not fr.stops(result["findings"])


def test_fx_treasury_source_is_silent():
    assert by_id(run(roster([account("a")])), "V1") == []


def test_fx_wrong_as_of_date_errors():
    result = run(roster([account("a")],
                        fx=[{"currency": "USD", "units_per_usd": 1.0,
                             "as_of": "2025-06-30", "source": "treasury"}]))
    assert [f.severity for f in by_id(result, "V2")] == ["error"]
    assert fr.stops(result["findings"])


# ---------------------------------------------------------------------------
# V3 — USD recomputation
# ---------------------------------------------------------------------------


def test_fx_recompute_matches():
    result = run(roster([account("a", currencies=["GEL"], max_value_currency="GEL",
                                 max_value_local=27000.0, max_value_usd=10000.0)],
                        fx=[{"currency": "GEL", "units_per_usd": 2.7,
                             "as_of": "2025-12-31", "source": "treasury"}]))
    assert by_id(result, "V3") == []


def test_fx_recompute_mismatch_errors():
    result = run(roster([account("a", currencies=["GEL"], max_value_currency="GEL",
                                 max_value_local=27000.0, max_value_usd=9000.0)],
                        fx=[{"currency": "GEL", "units_per_usd": 2.7,
                             "as_of": "2025-12-31", "source": "treasury"}]))
    assert [f.severity for f in by_id(result, "V3")] == ["error"]
    assert fr.stops(result["findings"])


def test_reciprocal_rate_is_not_silently_accepted():
    """D4's other half: the same rate recorded as its own reciprocal.

    27,000 GEL at 2.7 units per USD is $10,000. If the rate is stored as
    0.370 (USD per GEL) the recomputation must FAIL rather than flip the
    division to make the stated figure agree — that flip would erase the
    provenance defect the module exists to surface.
    """
    result = run(roster([account("a", currencies=["GEL"], max_value_currency="GEL",
                                 max_value_local=27000.0, max_value_usd=10000.0)],
                        fx=[{"currency": "GEL", "units_per_usd": 0.3703,
                             "as_of": "2025-12-31", "source": "treasury"}]))
    assert [f.severity for f in by_id(result, "V3")] == ["error"]


def test_multicurrency_component_sum():
    """The TBC/BCS shape: the reported max is the USD sum of the components."""
    fx = [{"currency": "GEL", "units_per_usd": 2.7, "as_of": "2025-12-31",
           "source": "treasury"},
          {"currency": "EUR", "units_per_usd": 0.8522, "as_of": "2025-12-31",
           "source": "treasury"}]
    components = [{"currency": "GEL", "amount": 2700.0},
                  {"currency": "EUR", "amount": 852.2},
                  {"currency": "USD", "amount": 1000.0}]
    ok = run(roster([account("m", max_value_local=None, max_value_currency=None,
                             max_value_components=components, max_value_usd=3000.0,
                             currencies=["GEL", "EUR", "USD"])], fx=fx))
    assert by_id(ok, "V3") == []

    off = run(roster([account("m", max_value_local=None, max_value_currency=None,
                              max_value_components=components, max_value_usd=3002.0,
                              currencies=["GEL", "EUR", "USD"])], fx=fx))
    assert [f.severity for f in by_id(off, "V3")] == ["error"]


def test_components_without_a_rate_error():
    result = run(roster([account("m", max_value_local=None, max_value_currency=None,
                                 max_value_components=[{"currency": "RUB", "amount": 100.0}],
                                 max_value_usd=1.0, currencies=["RUB"])]))
    assert [f.severity for f in by_id(result, "V3")] == ["error"]


def test_components_and_local_amount_together_are_rejected_at_load():
    with pytest.raises(RosterError, match="Pick one"):
        roster([account("m", max_value_components=[{"currency": "USD", "amount": 1.0}])])


# ---------------------------------------------------------------------------
# V4 — threshold arithmetic
# ---------------------------------------------------------------------------


def test_threshold_arithmetic():
    result = run(roster([account("a", max_value_usd=6000.0, max_value_local=6000.0),
                         account("b", max_value_usd=5000.0, max_value_local=5000.0)]))
    assert result["threshold"]["known_total_usd"] == 11000.0
    assert result["threshold"]["tripped"] is True


def test_threshold_boundary_exactly_ten_thousand_does_not_trip():
    """The statute says 'exceeded $10,000' — exactly $10,000 requires no filing."""
    result = run(roster([account("a", max_value_usd=10000.0, max_value_local=10000.0)]))
    assert result["threshold"]["tripped"] is False
    assert threshold_tripped(10000.0, FBAR) is False
    assert threshold_tripped(10000.01, FBAR) is True


def test_undetermined_maximum_undercuts_a_no_filing_conclusion():
    result = run(roster([
        account("a", max_value_usd=100.0, max_value_local=100.0),
        account("b", max_value_usd=None, max_value_local=None, max_value_currency=None,
                max_value_unknown=True, max_value_basis="unknown"),
    ]))
    assert result["threshold"]["tripped"] is False
    warnings = [f for f in by_id(result, "V4") if f.severity == "warn"]
    assert len(warnings) == 1
    assert "not supported" in warnings[0].message
    assert not fr.stops(result["findings"])


# ---------------------------------------------------------------------------
# V5 / V6
# ---------------------------------------------------------------------------


def test_unexplained_non_reportable_account_errors_once_tripped():
    result = run(roster([account("a", max_value_usd=20000.0, max_value_local=20000.0),
                         account("b", reportable=False, max_value_usd=0.0,
                                 max_value_local=0.0)]))
    assert [f.severity for f in by_id(result, "V5")] == ["error"]


def test_non_reportable_with_a_recorded_reason_is_accepted():
    result = run(roster([
        account("a", max_value_usd=20000.0, max_value_local=20000.0),
        account("b", reportable=False, max_value_usd=0.0, max_value_local=0.0,
                not_reportable_reason="Credit card, never carried a positive balance."),
    ]))
    assert by_id(result, "V5") == []


def test_25_account_rule():
    accounts = [account(f"a{i}", max_value_usd=100.0, max_value_local=100.0)
                for i in range(25)]
    result = run(roster(accounts))
    finding = by_id(result, "V6")
    assert len(finding) == 1 and finding[0].severity == "info"
    assert "14a" in finding[0].message
    assert finding[0].detail["reportable_account_count"] == 25


def test_24_accounts_do_not_trigger_the_25_rule():
    accounts = [account(f"a{i}", max_value_usd=100.0, max_value_local=100.0)
                for i in range(24)]
    assert by_id(run(roster(accounts)), "V6") == []


# ---------------------------------------------------------------------------
# V7 — institution address
# ---------------------------------------------------------------------------


def test_incomplete_address_warns_never_blocks():
    result = run(roster([account("a", institution={"name": "Test Bank", "country": "GE",
                                                   "address": {"city": "Tbilisi"}})]))
    finding = by_id(result, "V7")
    assert len(finding) == 1 and finding[0].severity == "warn"
    assert "address.street (Item 19)" in finding[0].message
    assert "address.postal_code (Item 22)" in finding[0].message
    assert not fr.stops(result["findings"])


def test_state_province_is_not_required():
    """Item 21 is captioned 'State' — many countries have no such level."""
    assert by_id(run(roster([account("a")])), "V7") == []


def test_non_reportable_accounts_are_not_address_checked():
    result = run(roster([account("a", reportable=False, max_value_usd=0.0,
                                 max_value_local=0.0,
                                 not_reportable_reason="zero throughout",
                                 institution={"name": None, "address": {}})]))
    assert by_id(result, "V7") == []


# ---------------------------------------------------------------------------
# V8 — Item 15a carry-forward (D3)
# ---------------------------------------------------------------------------


def _unknown_account(key, **overrides):
    fields = {"max_value_usd": None, "max_value_local": None,
              "max_value_currency": None, "max_value_unknown": True,
              "max_value_basis": "unknown"}
    fields.update(overrides)
    return account(key, **fields)


def test_amount_unknown_warns_not_blocks():
    """D3: Item 15a is a legitimate filing position, so V8 must never block."""
    prior = roster([_unknown_account("a")], year=2024)
    current = roster([_unknown_account("a")])
    result = run(current, prior)
    finding = by_id(result, "V8")
    assert len(finding) == 1 and finding[0].severity == "warn"
    assert not fr.stops(result["findings"])


def test_amount_unknown_resolved_this_year_is_silent():
    prior = roster([_unknown_account("a")], year=2024)
    current = roster([account("a")])
    assert by_id(run(current, prior), "V8") == []


def test_amount_unknown_re_elected_with_a_note_is_silent():
    prior = roster([_unknown_account("a")], year=2024)
    current = roster([_unknown_account("a", notes="Institution stopped issuing statements.")])
    assert by_id(run(current, prior), "V8") == []


def test_v8_is_silent_without_a_prior_year():
    assert by_id(run(roster([_unknown_account("a")])), "V8") == []


def test_15a_with_a_stated_maximum_is_rejected_at_load():
    with pytest.raises(RosterError, match="contradicts the checkbox"):
        roster([account("a", max_value_unknown=True)])


# ---------------------------------------------------------------------------
# V9 — convention drift
# ---------------------------------------------------------------------------


def test_convention_change_between_years_warns():
    prior = roster([account("a", conventions_applied=["negative_balance_is_zero"])], year=2024)
    current = roster([account("a", conventions_applied=["multi_currency_single_account"])])
    finding = by_id(run(current, prior), "V9")
    assert len(finding) == 1 and finding[0].severity == "warn"
    assert finding[0].detail == {"added": ["multi_currency_single_account"],
                                 "dropped": ["negative_balance_is_zero"]}


def test_stable_conventions_are_silent():
    prior = roster([account("a", conventions_applied=["negative_balance_is_zero"])], year=2024)
    current = roster([account("a", conventions_applied=["negative_balance_is_zero"])])
    assert by_id(run(current, prior), "V9") == []


def test_convention_drift_is_seen_across_a_renumbering():
    """V8/V9 follow the matching ladder, not a bare account_key lookup.

    A renumbered account keeps its identity through aliases[] but takes a new
    key. Resolving the prior key straight into the current roster made every
    renumbered, merged or split account invisible to these two validators —
    and those are precisely the accounts whose figures and treatments moved.
    """
    prior = roster([account("old-key", account_number="NUM-old-key",
                            conventions_applied=["negative_balance_is_zero"])], year=2024)
    current = roster([account("new-key", account_number="NUM-new-key",
                              aliases=["NUM-old-key"],
                              conventions_applied=["multi_currency_single_account"])])
    finding = by_id(run(current, prior), "V9")
    assert len(finding) == 1 and finding[0].severity == "warn"
    assert "'old-key'" in finding[0].message


def test_amount_unknown_carryforward_is_seen_across_a_renumbering():
    prior = roster([account("old-key", account_number="NUM-old-key",
                            max_value_unknown=True, max_value_usd=None,
                            max_value_local=None, max_value_currency=None)], year=2024)
    current = roster([account("new-key", account_number="NUM-new-key",
                              aliases=["NUM-old-key"],
                              max_value_unknown=True, max_value_usd=None,
                              max_value_local=None, max_value_currency=None)])
    finding = by_id(run(current, prior), "V8")
    assert len(finding) == 1 and finding[0].severity == "warn"


# ---------------------------------------------------------------------------
# V10 — Form 114a, spousal vs preparer
# ---------------------------------------------------------------------------


def test_separate_filing_required():
    result = run(roster([account("a", ownership="separate")],
                        filer={"slug": "test", "form_114a_spousal": True}))
    finding = by_id(result, "V10")
    assert len(finding) == 1 and finding[0].severity == "error"
    assert fr.stops(result["findings"])


def test_preparer_114a_does_not_trigger_v10():
    """The real filing pattern: separate FBARs plus a third-party preparer."""
    result = run(roster([account("a", ownership="separate")],
                        filer={"slug": "test", "form_114a_spousal": False,
                               "form_114a_preparer": True}))
    assert by_id(result, "V10") == []
    assert not fr.stops(result["findings"])


def test_spousal_114a_with_only_joint_accounts_is_accepted():
    result = run(roster([account("a", ownership="joint",
                                 joint_owners=[{"name": "Spouse"}])],
                        filer={"slug": "test", "form_114a_spousal": True}))
    assert by_id(result, "V10") == []


# ---------------------------------------------------------------------------
# V11 — deadlines, retention, penalties
# ---------------------------------------------------------------------------


def test_deadlines_are_derived_for_any_year():
    assert fbar_deadlines(2025, FBAR)["due"] == date(2026, 4, 15)
    assert fbar_deadlines(2025, FBAR)["extended"] == date(2026, 10, 15)
    assert fbar_deadlines(2023, FBAR)["extended"] == date(2024, 10, 15)


def test_retention_runs_five_years_from_the_due_date():
    assert record_retention_until(2025, FBAR) == date(2031, 4, 15)


def test_filing_before_the_extended_deadline_is_informational():
    finding = by_id(run(roster([account("a")]), filing_date=date(2026, 6, 1)), "V11")
    assert [f.severity for f in finding] == ["info"]


def test_filing_after_the_extended_deadline_warns_and_names_the_relief_path():
    finding = by_id(run(roster([account("a")]), filing_date=date(2026, 11, 1)), "V11")
    assert [f.severity for f in finding] == ["warn"]
    assert "Delinquent FBAR Submission Procedures" in finding[0].message


def test_late_filing_before_the_earliest_penalty_column_still_warns():
    """The penalty table only runs forward from its earliest column.

    An old tax year filed late, but before that column takes effect, has no
    applicable maximum to quote. V11 must still report the delinquency — it
    used to raise straight out of `validate`, aborting the whole run over a
    parenthetical scale figure.
    """
    finding = by_id(run(roster([account("a")], year=2023,
                               fx=[{"currency": "USD", "units_per_usd": 1.0,
                                    "as_of": "2023-12-31", "source": "treasury"}]),
                        filing_date=date(2024, 11, 1)), "V11")
    assert [f.severity for f in finding] == ["warn"]
    assert "Delinquent FBAR Submission Procedures" in finding[0].message
    assert "no maximum is quoted here" in finding[0].message


def test_penalty_column_is_selected_by_assessment_date_not_tax_year():
    """31 CFR 1010.821 indexes by the year the penalty is ASSESSED."""
    penalty = fbar_penalty_maximum(FBAR, date(2026, 8, 15))
    assert penalty["column"] == "2025-01-17"
    assert penalty["non_willful_max"] == 16536
    assert penalty["willful_max"] == 165353
    assert penalty["unit"] == "per_report"      # Bittner v. United States (2023)

    with pytest.raises(RosterError, match="No penalty column"):
        fbar_penalty_maximum(FBAR, date(2020, 1, 1))


# ---------------------------------------------------------------------------
# V12 — evidence behind a stated maximum
# ---------------------------------------------------------------------------


def test_incomplete_coverage_warns():
    result = run(roster([account("a", max_value_basis="period_end_summaries_only")]))
    finding = by_id(result, "V12")
    assert len(finding) == 1 and finding[0].severity == "warn"
    assert not fr.stops(result["findings"])


def test_full_year_statements_are_silent():
    assert by_id(run(roster([account("a")])), "V12") == []


def test_electing_15a_answers_v12():
    assert by_id(run(roster([_unknown_account("a", max_value_basis="estimate")])), "V12") == []


# ---------------------------------------------------------------------------
# V13 — supporting records
# ---------------------------------------------------------------------------


def test_missing_source_document_warns(tmp_path):
    """D7 shape: workpapers citing files that are not in the workspace."""
    (tmp_path / "present.md").write_text("x")
    result = run(roster([account("a", source_documents=["present.md", "absent.pdf"])]),
                 workspace=str(tmp_path))
    finding = by_id(result, "V13")
    assert len(finding) == 1 and finding[0].severity == "warn"
    assert finding[0].detail["missing"] == ["absent.pdf"]
    assert not fr.stops(result["findings"])


def test_present_source_documents_are_silent(tmp_path):
    (tmp_path / "present.md").write_text("x")
    assert by_id(run(roster([account("a", source_documents=["present.md"])]),
                     workspace=str(tmp_path)), "V13") == []


def test_v13_is_skipped_without_a_workspace():
    assert by_id(run(roster([account("a", source_documents=["absent.pdf"])])), "V13") == []


# ---------------------------------------------------------------------------
# Out-of-scope notice
# ---------------------------------------------------------------------------


def test_signature_authority_accounts_are_reported_as_out_of_scope():
    finding = by_id(run(roster([account("a", ownership="signature_authority")])), "SCOPE")
    assert len(finding) == 1 and finding[0].severity == "warn"
    assert "Part IV" in finding[0].message


# ---------------------------------------------------------------------------
# Schema loader — unknown keys must never be silently absorbed
# ---------------------------------------------------------------------------


def test_unknown_account_key_is_rejected_with_a_suggestion():
    with pytest.raises(RosterError, match="max_value_usd"):
        roster([dict(account("a"), max_value_us=5000.0)])


def test_unknown_top_level_key_is_rejected():
    with pytest.raises(RosterError, match="Unknown key"):
        roster([account("a")], fx_rate=[])


def test_duplicate_account_keys_are_rejected():
    with pytest.raises(RosterError, match="duplicate account_key"):
        roster([account("a"), account("a")])


def test_closed_without_a_date_is_rejected():
    with pytest.raises(RosterError, match="closed_date is missing"):
        roster([account("a", account_status="closed")])


def test_negative_or_zero_exchange_rate_is_rejected():
    with pytest.raises(RosterError, match="units_per_usd"):
        roster([account("a")], fx=[{"currency": "EUR", "units_per_usd": 0,
                                    "as_of": "2025-12-31", "source": "treasury"}])


def test_unknown_fx_source_is_rejected():
    with pytest.raises(RosterError, match="source"):
        roster([account("a")], fx=[{"currency": "EUR", "units_per_usd": 0.9,
                                    "as_of": "2025-12-31", "source": "bloomberg"}])


def test_account_type_other_requires_a_description():
    with pytest.raises(RosterError, match="account_type_other_desc"):
        roster([account("a", account_type="other")])


# ---------------------------------------------------------------------------
# The example pair behaves as the plan's F4 gate specifies
# ---------------------------------------------------------------------------


def test_example_pair_surfaces_the_defect_shapes_as_warnings_only():
    current = load_roster(os.path.join(EXAMPLES, "fbar_roster_example_2025.json"))
    prior = load_roster(os.path.join(EXAMPLES, "fbar_roster_example_2024.json"))
    validation = fr.validate(current, prior, FBAR, EXAMPLES, date(2026, 6, 1))
    reconciliation = fr.reconcile(current, prior)
    findings = validation["findings"] + reconciliation["findings"]

    fired = {f.id for f in findings}
    assert {"V1", "V8", "V12", "V13"} <= fired      # D4, D3, coverage, D7
    assert "D-SPLIT" in fired                        # D1
    assert "D-NEW-UNREPORTED" in fired               # D2
    assert not fr.stops(findings), \
        "the example cycle must run clean: every defect shape is a warning"


def test_anchored_basis_requires_an_anchor():
    """The basis asserts an opening came from somewhere. Say where.

    Without this, `anchored_full_year` is a word that silences V12 — which is
    the exact hole derive_basis was built to close.
    """
    with pytest.raises(RosterError) as exc:
        roster([account("a", max_value_basis="anchored_full_year")])
    assert "max_value_anchor" in str(exc.value)


def test_anchor_requires_every_field():
    for missing in ("as_of", "amount", "currency", "source_document", "source_kind"):
        anchor = {"as_of": "2025-01-01", "amount": 1000.0, "currency": "RUB",
                  "source_document": "prior/form_114_2024.pdf",
                  "source_kind": "prior_year_filing"}
        del anchor[missing]
        with pytest.raises(RosterError) as exc:
            roster([account("a", max_value_basis="anchored_full_year",
                            max_value_anchor=anchor)])
        assert missing in str(exc.value)


def test_anchor_source_kind_is_an_enum():
    with pytest.raises(RosterError) as exc:
        roster([account("a", max_value_basis="anchored_full_year",
                        max_value_anchor={"as_of": "2025-01-01", "amount": 1000.0,
                                          "currency": "RUB",
                                          "source_document": "x.pdf",
                                          "source_kind": "vibes"})])
    assert "vibes" in str(exc.value)


def test_an_anchor_on_a_non_anchored_basis_is_kept_not_rejected():
    """A recorded anchor is evidence even where the basis is weaker.

    An account may have an anchor and still be capped at `statement_partial`
    by a coverage gap. Dropping the anchor there would discard the citation
    that explains a figure.
    """
    r = roster([account("a", max_value_basis="statement_partial",
                        max_value_anchor={"as_of": "2025-01-01", "amount": 1000.0,
                                          "currency": "RUB",
                                          "source_document": "x.pdf",
                                          "source_kind": "declaration"})])
    assert r["accounts"][0]["max_value_anchor"]["currency"] == "RUB"


def test_anchor_as_of_must_be_a_date():
    with pytest.raises(RosterError):
        roster([account("a", max_value_basis="anchored_full_year",
                        max_value_anchor={"as_of": "January 1", "amount": 1.0,
                                          "currency": "RUB", "source_document": "x.pdf",
                                          "source_kind": "declaration"})])


def test_v13_covers_the_anchor_document(tmp_path):
    """An anchor citing a file that is not there is the same defect as any
    other dangling citation — and a worse one, since V12 trusts it."""
    r = roster([account("a", max_value_basis="anchored_full_year",
                        max_value_anchor={"as_of": "2025-01-01", "amount": 1.0,
                                          "currency": "RUB",
                                          "source_document": "nowhere.pdf",
                                          "source_kind": "declaration"})])
    ids = [f.id for f in run(r, workspace=str(tmp_path))["findings"]]
    assert "V13" in ids


def _anchor(**overrides):
    base = {"as_of": "2025-01-01", "amount": 1000.0, "currency": "RUB",
            "source_document": "anchor.pdf", "source_kind": "prior_year_filing"}
    base.update(overrides)
    return base


def test_v12_is_quiet_on_a_verified_anchored_replay(tmp_path):
    """The records DO cover the year — via a cited opening, not a balance column.

    Telling this filer to obtain year-covering records or elect Item 15a is
    advice for a problem they do not have.
    """
    (tmp_path / "anchor.pdf").write_bytes(b"%PDF-synthetic")
    r = roster([account("a", max_value_basis="anchored_full_year",
                        max_value_anchor=_anchor())])
    v12 = [f for f in run(r, workspace=str(tmp_path))["findings"] if f.id == "V12"]
    assert v12 == []


def test_v12_still_warns_when_the_anchor_document_is_missing(tmp_path):
    """An unverifiable anchor is an assertion. V12 must not take its word."""
    r = roster([account("a", max_value_basis="anchored_full_year",
                        max_value_anchor=_anchor(source_document="gone.pdf"))])
    v12 = [f for f in run(r, workspace=str(tmp_path))["findings"] if f.id == "V12"]
    assert len(v12) == 1
    assert "cannot be verified" in v12[0].message
    assert v12[0].detail["anchor_verified"] is False


def test_v12_warns_on_an_anchored_basis_with_no_workspace_to_check():
    """No workspace means no verification, so the claim stays unproven."""
    r = roster([account("a", max_value_basis="anchored_full_year",
                        max_value_anchor=_anchor())])
    v12 = [f for f in run(r)["findings"] if f.id == "V12"]
    assert len(v12) == 1


def test_v12_uses_bound_language_for_a_floor():
    """A proven floor is not a guess, and the message must not call it one."""
    r = roster([account("a", max_value_basis="non_negativity_floor")])
    v12 = [f for f in run(r)["findings"] if f.id == "V12"]
    assert len(v12) == 1
    assert "proven floor" in v12[0].message
    assert "may understate" in v12[0].message
    assert "estimate" not in v12[0].message


def test_v12_language_for_a_real_estimate_is_unchanged():
    r = roster([account("a", max_value_basis="estimate")])
    v12 = [f for f in run(r)["findings"] if f.id == "V12"]
    assert "obtain records that cover the year" in v12[0].message


# ---------------------------------------------------------------------------
# V14 — unsourced figure tracking prior year (Task 2.5)
# ---------------------------------------------------------------------------


def test_v14_flags_an_uncited_figure_that_tracks_last_year():
    """The signature of a carried-forward placeholder: no source, same number."""
    prior = roster([account("a", max_value_usd=12000.0, max_value_local=12000.0)],
                   year=2024)
    current = roster([account("a", max_value_usd=12050.0, max_value_local=12050.0,
                              source_documents=[])])
    v14 = [f for f in run(current, prior)["findings"] if f.id == "V14"]
    assert len(v14) == 1
    assert "no current-year source" in v14[0].message
    assert v14[0].detail["delta_pct"] == pytest.approx(0.42, abs=0.01)


def test_v14_is_quiet_when_a_source_is_cited():
    prior = roster([account("a", max_value_usd=12000.0, max_value_local=12000.0)],
                   year=2024)
    current = roster([account("a", max_value_usd=12050.0, max_value_local=12050.0,
                              source_documents=["statements/2025_q4.xlsx"])])
    assert [f for f in run(current, prior)["findings"] if f.id == "V14"] == []


def test_v14_is_quiet_when_the_figure_moved():
    """A figure that moved 60% was not copied forward, cited or not."""
    prior = roster([account("a", max_value_usd=12000.0, max_value_local=12000.0)],
                   year=2024)
    current = roster([account("a", max_value_usd=30000.0, max_value_local=30000.0,
                              source_documents=[])])
    assert [f for f in run(current, prior)["findings"] if f.id == "V14"] == []


def test_v14_is_quiet_on_an_admitted_estimate():
    """`estimate` and `unknown` already say the evidence is weak. V14 exists for
    figures presented WITHOUT a caveat."""
    prior = roster([account("a", max_value_usd=12000.0, max_value_local=12000.0)],
                   year=2024)
    current = roster([account("a", max_value_usd=12050.0, max_value_local=12050.0,
                              max_value_basis="estimate", source_documents=[])])
    assert [f for f in run(current, prior)["findings"] if f.id == "V14"] == []


def test_v14_accepts_a_verified_anchor_as_a_citation(tmp_path):
    """An anchor IS a cited source; it just lives in its own field."""
    (tmp_path / "anchor.pdf").write_bytes(b"%PDF-synthetic")
    prior = roster([account("a", max_value_usd=12000.0, max_value_local=12000.0)],
                   year=2024)
    current = roster([account("a", max_value_usd=12050.0, max_value_local=12050.0,
                              max_value_basis="anchored_full_year",
                              source_documents=[],
                              max_value_anchor={"as_of": "2025-01-01", "amount": 1.0,
                                                "currency": "USD",
                                                "source_document": "anchor.pdf",
                                                "source_kind": "prior_year_filing"})])
    findings = run(current, prior, workspace=str(tmp_path))["findings"]
    assert [f for f in findings if f.id == "V14"] == []


def test_v14_follows_a_renumbering():
    """The accounts whose figures were copied forward are often the ones that
    got a new number, so V14 must use the same matching ladder as everything else."""
    prior = roster([account("old", account_number="1111", contract_number="C-1",
                            max_value_usd=12000.0, max_value_local=12000.0)], year=2024)
    current = roster([account("new", account_number="2222", contract_number="C-1",
                              max_value_usd=12050.0, max_value_local=12050.0,
                              source_documents=[])])
    v14 = [f for f in run(current, prior)["findings"] if f.id == "V14"]
    assert len(v14) == 1
    assert v14[0].account_key == "new"


def test_v14_needs_a_prior_year():
    current = roster([account("a", source_documents=[])])
    assert [f for f in run(current)["findings"] if f.id == "V14"] == []




