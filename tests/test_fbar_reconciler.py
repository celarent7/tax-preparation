#!/usr/bin/env python3
"""The FBAR disposition state machine (docs/fbar_module_plan.md §7.2-§7.3).

The feature's core requirement: **every prior-year account resolves to exactly
one disposition.** An account that vanishes between filings without an answer
is a blocking finding.

Fixtures are built inline and are entirely synthetic — no real account numbers,
institutions or amounts.
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import fbar_reconciler as fr  # noqa: E402
from fbar_schema import RosterError, load_roster, roster_to_json, validate_roster  # noqa: E402

EXAMPLES = os.path.join(ROOT, "examples", "fbar")


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def account(key, number="10000001", **overrides):
    base = {
        "account_key": key,
        "account_number": number,
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


def roster(year_or_accounts, accounts=None, **overrides):
    if isinstance(year_or_accounts, list):
        accounts = year_or_accounts
        year = overrides.pop("year", 2025)
    else:
        year = year_or_accounts
    base = {
        "schema_version": "1.0",
        "tax_year": year,
        "filer": {"slug": "test", "filing_capacity": "individual"},
        "fx_rates": [{"currency": "USD", "units_per_usd": 1.0,
                      "as_of": f"{year}-12-31", "source": "treasury"}],
        "accounts": accounts,
    }
    base.update(overrides)
    return validate_roster(base, origin=f"<test {year}>")


def dispositions(current, prior):
    result = fr.reconcile(current, prior)
    prior_map = {r["account_key"]: r["disposition"] for r in result["prior_rows"]}
    current_map = {r["account_key"]: r["disposition"] for r in result["current_rows"]}
    return prior_map, current_map, result


def finding_ids(result):
    return {f.id for f in result["findings"]}


# ---------------------------------------------------------------------------
# Prior-year dispositions
# ---------------------------------------------------------------------------


def test_disposition_carried_forward():
    prior = roster(2024, [account("a")])
    current = roster(2025, [account("a")])
    prior_map, _, result = dispositions(current, prior)
    assert prior_map == {"a": fr.CARRIED_FORWARD}
    assert not fr.stops(result["findings"])


def test_disposition_closed_midyear():
    prior = roster(2024, [account("a")])
    current = roster(2025, [account("a", account_status="closed", closed_date="2025-06-30")])
    prior_map, _, result = dispositions(current, prior)
    assert prior_map == {"a": fr.CLOSED}
    assert not fr.stops(result["findings"])


def test_disposition_closed_prior_year_is_correctly_absent():
    """An account already closed before the year began may simply be gone."""
    prior = roster(2024, [account("a", account_status="closed", closed_date="2024-11-30")])
    current = roster(2025, [account("b", number="20000002", opened_date="2025-01-05")])
    prior_map, _, result = dispositions(current, prior)
    assert prior_map == {"a": fr.CLOSED_PRIOR_YEAR}
    assert not fr.stops(result["findings"])


def test_disposition_dormant_zero():
    prior = roster(2024, [account("card")])
    current = roster(2025, [account(
        "card", reportable=False, max_value_usd=0.0, max_value_local=0.0,
        not_reportable_reason="Zero balance throughout; the holder owed the issuer.")])
    prior_map, _, result = dispositions(current, prior)
    assert prior_map == {"card": fr.DORMANT_ZERO}
    assert not fr.stops(result["findings"])


def test_dormant_zero_without_a_reason_is_an_error():
    prior = roster(2024, [account("card")])
    current = roster(2025, [account("card", reportable=False, max_value_usd=0.0,
                                    max_value_local=0.0)])
    _, _, result = dispositions(current, prior)
    assert "D-NOREASON" in finding_ids(result)
    assert fr.stops(result["findings"])


def test_disposition_renumbered_warns_not_blocks():
    prior = roster(2024, [account("a", number="10000001")])
    current = roster(2025, [account("a", number="99999999", aliases=["10000001"])])
    prior_map, _, result = dispositions(current, prior)
    assert prior_map == {"a": fr.RENUMBERED}
    assert not fr.stops(result["findings"])
    assert "D-RENUMBERED" in finding_ids(result)


def test_renumbered_without_the_prior_id_in_aliases_warns():
    prior = roster(2024, [account("a", number="10000001")])
    current = roster(2025, [account("a", number="99999999")])
    _, _, result = dispositions(current, prior)
    assert "D-ALIAS-MISSING" in finding_ids(result)
    assert not fr.stops(result["findings"])


def test_disposition_unresolved_blocks():
    """The core requirement: a prior account with no answer stops the run."""
    prior = roster(2024, [account("gone", number="10000001")])
    current = roster(2025, [account("b", number="20000002", opened_date="2025-02-01")])
    prior_map, _, result = dispositions(current, prior)
    assert prior_map == {"gone": fr.UNRESOLVED}
    assert fr.stops(result["findings"])
    assert "D-UNRESOLVED" in finding_ids(result)


def test_carried_forward_without_a_maximum_or_15a_is_an_error():
    prior = roster(2024, [account("a")])
    current = roster(2025, [account("a", max_value_usd=None, max_value_local=None,
                                    max_value_currency=None)])
    _, _, result = dispositions(current, prior)
    assert "D-NOMAX" in finding_ids(result)


def test_carried_forward_with_15a_elected_does_not_error():
    prior = roster(2024, [account("a")])
    current = roster(2025, [account("a", max_value_usd=None, max_value_local=None,
                                    max_value_currency=None, max_value_unknown=True,
                                    max_value_basis="unknown")])
    _, _, result = dispositions(current, prior)
    assert "D-NOMAX" not in finding_ids(result)


# ---------------------------------------------------------------------------
# Current-year dispositions
# ---------------------------------------------------------------------------


def test_new_opened_is_clean():
    prior = roster(2024, [account("a")])
    current = roster(2025, [account("a"),
                            account("b", number="20000002", opened_date="2025-04-01")])
    _, current_map, result = dispositions(current, prior)
    assert current_map == {"b": fr.NEW_OPENED}
    assert not fr.stops(result["findings"])


def test_new_previously_unreported_warns():
    """D2 shape: two cards appeared with no record of whether they were omitted."""
    prior = roster(2024, [account("a")])
    current = roster(2025, [
        account("a"),
        account("card", number="40000004",
                notes="Opened 2019; absent from the 2024 filing. Rostered now."),
    ])
    _, current_map, result = dispositions(current, prior)
    assert current_map == {"card": fr.NEW_PREVIOUSLY_UNREPORTED}
    assert not fr.stops(result["findings"])
    assert "D-NEW-UNREPORTED" in finding_ids(result)


def test_new_unexplained_blocks():
    prior = roster(2024, [account("a")])
    current = roster(2025, [account("a"), account("mystery", number="50000005")])
    _, current_map, result = dispositions(current, prior)
    assert current_map == {"mystery": fr.NEW_UNEXPLAINED}
    assert fr.stops(result["findings"])


def test_opened_date_wins_over_notes():
    prior = roster(2024, [account("a")])
    current = roster(2025, [account("a"),
                            account("b", number="20000002", opened_date="2025-04-01",
                                    notes="some note")])
    _, current_map, _ = dispositions(current, prior)
    assert current_map == {"b": fr.NEW_OPENED}


# ---------------------------------------------------------------------------
# Identity changes (§7.3)
# ---------------------------------------------------------------------------


SPLIT_CHANGE = {
    "type": "split",
    "prior_account_keys": ["wallet"],
    "current_account_keys": ["wallet-eur", "wallet-usd"],
    "reason": "Institution now issues a per-currency balance ID.",
    "reporting_model_changed": True,
}


def _split_pair(justification):
    prior = roster(2024, [account("wallet", number="PROFILE-1")])
    change = dict(SPLIT_CHANGE)
    if justification is not None:
        change["justification"] = justification
    current = roster(2025, [
        account("wallet-eur", number="EUR-1", aliases=["PROFILE-1"]),
        account("wallet-usd", number="USD-1", aliases=["PROFILE-1"]),
    ], identity_changes=[change])
    return current, prior


def test_split_requires_justification():
    """D1 shape: one account in 2024, two in 2025, no written reason -> blocked."""
    current, prior = _split_pair(justification=None)
    prior_map, _, result = dispositions(current, prior)
    assert prior_map == {"wallet": fr.SPLIT}
    assert "IC-JUSTIFICATION" in finding_ids(result)
    assert fr.stops(result["findings"])


def test_split_with_justification_only_warns():
    current, prior = _split_pair(
        justification="Statements are now issued per balance ID, so a profile-level "
                      "maximum can no longer be tied to a statement.")
    prior_map, _, result = dispositions(current, prior)
    assert prior_map == {"wallet": fr.SPLIT}
    assert "IC-JUSTIFICATION" not in finding_ids(result)
    assert not fr.stops(result["findings"])


def test_split_targets_are_not_reported_as_new_accounts():
    current, prior = _split_pair(justification="recorded")
    _, current_map, _ = dispositions(current, prior)
    assert current_map == {}


def test_merge_disposition():
    prior = roster(2024, [account("a", number="A1"), account("b", number="B1")])
    current = roster(2025, [account("merged", number="M1", aliases=["A1", "B1"])],
                     identity_changes=[{
                         "type": "merge",
                         "prior_account_keys": ["a", "b"],
                         "current_account_keys": ["merged"],
                         "reason": "Institution consolidated the two into one.",
                         "reporting_model_changed": False,
                     }])
    prior_map, current_map, result = dispositions(current, prior)
    assert prior_map == {"a": fr.MERGED, "b": fr.MERGED}
    assert current_map == {}
    assert not fr.stops(result["findings"])


def test_identity_change_targets_missing_the_prior_alias_warn():
    prior = roster(2024, [account("wallet", number="PROFILE-1")])
    current = roster(2025, [account("wallet-eur", number="EUR-1")],
                     identity_changes=[{
                         "type": "renumber",
                         "prior_account_keys": ["wallet"],
                         "current_account_keys": ["wallet-eur"],
                         "reason": "renumbered",
                         "reporting_model_changed": False,
                     }])
    _, _, result = dispositions(current, prior)
    assert "IC-ALIAS-MISSING" in finding_ids(result)
    assert not fr.stops(result["findings"])


def test_identity_change_naming_an_unknown_prior_key_errors():
    prior = roster(2024, [account("a")])
    current = roster(2025, [account("a"), account("b", number="B1",
                                                  opened_date="2025-01-01")],
                     identity_changes=[{
                         "type": "renumber",
                         "prior_account_keys": ["never-existed"],
                         "current_account_keys": ["b"],
                         "reporting_model_changed": False,
                     }])
    _, _, result = dispositions(current, prior)
    assert "IC-UNKNOWN-PRIOR" in finding_ids(result)


def test_identity_change_naming_an_unknown_current_key_is_rejected_at_load():
    with pytest.raises(RosterError, match="not in this roster's accounts"):
        roster(2025, [account("a")], identity_changes=[{
            "type": "renumber",
            "prior_account_keys": ["x"],
            "current_account_keys": ["does-not-exist"],
            "reporting_model_changed": False,
        }])


# ---------------------------------------------------------------------------
# Matching ladder (§4.1)
# ---------------------------------------------------------------------------


def test_homoglyph_renumber_matches_instead_of_closed_plus_new():
    """D5 end to end: normalization keeps one account from becoming two."""
    prior = roster(2024, [account("brokerage", number="12345678M")])
    current = roster(2025, [account("brokerage-renamed", number="123456/78-м")])
    prior_map, current_map, result = dispositions(current, prior)
    assert prior_map == {"brokerage": fr.CARRIED_FORWARD}
    assert current_map == {}
    assert not fr.stops(result["findings"])


def test_match_by_alias_when_the_key_changed():
    prior = roster(2024, [account("old-key", number="A1")])
    current = roster(2025, [account("new-key", number="B1", aliases=["A1"])])
    prior_map, current_map, result = dispositions(current, prior)
    assert prior_map == {"old-key": fr.RENUMBERED}
    assert current_map == {}
    assert result["matching"]["matches"]["old-key"]["method"] == fr.MATCH_ALIAS


def test_fuzzy_candidate_suggests_and_never_auto_accepts():
    """Step 4 of the ladder must not silently link two accounts."""
    prior = roster(2024, [account("old", number="1111-5678")])
    current = roster(2025, [account("new", number="2222-5678", opened_date="2025-01-01")])
    prior_map, current_map, result = dispositions(current, prior)
    assert prior_map == {"old": fr.UNRESOLVED}       # not auto-matched
    assert current_map == {"new": fr.NEW_OPENED}
    assert "D-FUZZY" in finding_ids(result)
    assert result["matching"]["suggestions"][0]["candidate_current_keys"] == ["new"]


def test_fuzzy_suggestion_needs_the_same_institution():
    prior = roster(2024, [account("old", number="1111-5678")])
    current = roster(2025, [account(
        "new", number="2222-5678", opened_date="2025-01-01",
        institution={"name": "Different Bank", "country": "GE",
                     "address": {"street": "s", "city": "c", "postal_code": "p"}})])
    _, _, result = dispositions(current, prior)
    assert result["matching"]["suggestions"] == []


def test_one_prior_account_never_consumes_two_current_ones():
    prior = roster(2024, [account("a", number="A1")])
    current = roster(2025, [account("a", number="A1"),
                            account("a2", number="A1", opened_date="2025-01-01")])
    prior_map, current_map, _ = dispositions(current, prior)
    assert prior_map == {"a": fr.CARRIED_FORWARD}
    assert current_map == {"a2": fr.NEW_OPENED}


def test_two_prior_accounts_never_resolve_to_one_current_account():
    """The ladder runs rung-by-rung across ALL prior accounts, not per account.

    'a' lists 'b''s number in aliases[], so a per-account ladder let 'a' take
    'b' on the alias rung before 'b' claimed it on the stronger exact-key rung.
    Both prior accounts then reported a carry-forward onto one current record
    and 'a' silently vanished with a disposition it never earned — the exact
    failure this module exists to prevent.
    """
    prior = roster(2024, [account("a", number="A1"), account("b", number="B2")])
    current = roster(2025, [account("b", number="B2", aliases=["A1"])])
    prior_map, _, result = dispositions(current, prior)
    assert prior_map["b"] == fr.CARRIED_FORWARD
    assert prior_map["a"] == fr.UNRESOLVED
    assert fr.stops(result["findings"])
    claimed = [k for m in result["matching"]["matches"].values() for k in m["current_keys"]]
    assert len(claimed) == len(set(claimed)), "a current account was claimed twice"


# ---------------------------------------------------------------------------
# Round trip (§9.3) — this year's roster is next year's prior input
# ---------------------------------------------------------------------------


def test_emitted_roster_drops_identity_changes():
    """They describe the transition INTO this year, not out of it.

    Retained in the reconciliation report and findings JSON; the account-level
    link survives in aliases[].
    """
    original = load_roster(os.path.join(EXAMPLES, "fbar_roster_example_2025.json"))
    assert original["identity_changes"], "fixture should declare a split"
    assert roster_to_json(original)["identity_changes"] == []
    assert "PROFILE-9001" in roster_to_json(original)["accounts"][1]["aliases"]


def test_roundtrip_roster():
    original = load_roster(os.path.join(EXAMPLES, "fbar_roster_example_2025.json"))
    reloaded = validate_roster(json.loads(json.dumps(roster_to_json(original), default=str)),
                               origin="<roundtrip>")
    assert [a["account_key"] for a in reloaded["accounts"]] == \
           [a["account_key"] for a in original["accounts"]]
    assert reloaded["tax_year"] == original["tax_year"]
    assert reloaded["accounts"][0]["max_value_usd"] == original["accounts"][0]["max_value_usd"]
    assert reloaded["accounts"][0]["account_number"] == original["accounts"][0]["account_number"]


def test_emitted_roster_reconciles_as_the_next_years_prior(tmp_path):
    current = load_roster(os.path.join(EXAMPLES, "fbar_roster_example_2025.json"))
    emitted = tmp_path / "emitted.json"
    emitted.write_text(json.dumps(roster_to_json(current), default=str))
    reloaded = load_roster(str(emitted))

    next_year = json.loads(json.dumps(roster_to_json(current), default=str))
    next_year["tax_year"] = 2026
    for rate in next_year["fx_rates"]:
        rate["as_of"] = "2026-12-31"
    next_roster = validate_roster(next_year, origin="<2026>")

    prior_map, _, result = dispositions(next_roster, reloaded)
    assert set(prior_map.values()) <= {fr.CARRIED_FORWARD, fr.CLOSED_PRIOR_YEAR,
                                       fr.DORMANT_ZERO}
    # "Self-sustaining" has to mean the second cycle actually clears. An
    # identity_change carried into the emitted roster would name prior keys
    # that no longer exist and fail the very run it was meant to enable.
    assert not fr.stops(result["findings"]), \
        [f"{f.id}: {f.message}" for f in result["findings"] if f.severity in fr.STOPPING_SEVERITIES]


# ---------------------------------------------------------------------------
# CLI contract (§7.2 exit contract, §10)
# ---------------------------------------------------------------------------


def run_cli(*args):
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "fbar_reconciler.py"), *args],
        capture_output=True, text=True)
    return proc


def test_cli_reconcile_on_the_example_pair_clears():
    proc = run_cli("reconcile",
                   "--current", os.path.join(EXAMPLES, "fbar_roster_example_2025.json"),
                   "--prior", os.path.join(EXAMPLES, "fbar_roster_example_2024.json"),
                   "--filing-date", "2026-06-01",
                   "--output-format", "json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert "isError" not in payload
    assert payload["counts"]["blocking"] == 0
    assert payload["counts"]["error"] == 0
    assert payload["counts"]["warn"] >= 5


def test_cli_emits_iserror_and_exits_nonzero_when_blocked(tmp_path):
    prior = {"schema_version": "1.0", "tax_year": 2024,
             "filer": {"slug": "test"},
             "fx_rates": [{"currency": "USD", "units_per_usd": 1.0,
                           "as_of": "2024-12-31", "source": "treasury"}],
             "accounts": [account("gone")]}
    current = {"schema_version": "1.0", "tax_year": 2025,
               "filer": {"slug": "test"},
               "fx_rates": [{"currency": "USD", "units_per_usd": 1.0,
                             "as_of": "2025-12-31", "source": "treasury"}],
               "accounts": [account("mystery", number="90000009")]}
    p = tmp_path / "prior.json"
    c = tmp_path / "current.json"
    p.write_text(json.dumps(prior))
    c.write_text(json.dumps(current))

    proc = run_cli("reconcile", "--current", str(c), "--prior", str(p),
                   "--output-format", "json")
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["isError"] is True
    ids = {f["id"] for f in payload["findings"]}
    assert {"D-UNRESOLVED", "D-NEW-UNEXPLAINED"} <= ids


def test_cli_writes_all_four_outputs(tmp_path):
    out = tmp_path / "out"
    proc = run_cli("reconcile",
                   "--current", os.path.join(EXAMPLES, "fbar_roster_example_2025.json"),
                   "--prior", os.path.join(EXAMPLES, "fbar_roster_example_2024.json"),
                   "--output-dir", str(out), "--output-format", "json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    written = {os.path.basename(p) for p in json.loads(proc.stdout)["outputs"]}
    assert written == {
        "fbar_reconciliation_example_2025.md",
        "fbar_worksheet_example_2025.md",
        "fbar_reconciliation_example_2025.json",
        "fbar_roster_example_2025.json",
    }


def test_cli_rejects_a_filer_slug_mismatch(tmp_path):
    prior = json.load(open(os.path.join(EXAMPLES, "fbar_roster_example_2024.json")))
    prior["filer"]["slug"] = "spouse"
    p = tmp_path / "prior.json"
    p.write_text(json.dumps(prior))
    proc = run_cli("reconcile",
                   "--current", os.path.join(EXAMPLES, "fbar_roster_example_2025.json"),
                   "--prior", str(p), "--output-format", "json")
    assert proc.returncode == 1
    assert "Filer slug mismatch" in json.loads(proc.stdout)["error"]


def test_cli_validate_without_a_prior_year_does_not_invent_dispositions():
    proc = run_cli("validate",
                   "--roster", os.path.join(EXAMPLES, "fbar_roster_example_2025.json"),
                   "--output-format", "json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["prior_tax_year"] is None
    assert all(row["disposition"] is None for row in payload["new_accounts"])


def test_cli_worksheet_shows_the_institutions_rendering_at_item_18():
    proc = run_cli("worksheet",
                   "--roster", os.path.join(EXAMPLES, "fbar_roster_example_2025.json"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "| 18 Account Number or Other Designation | 123456/78-м |" in proc.stdout
    assert "12345678M" not in proc.stdout


def test_cli_refuses_an_output_dir_that_would_overwrite_the_input_roster(tmp_path):
    """The emitted roster reuses the input's own filename and carries no
    identity_changes — writing it back over the input would erase the declared
    splits, merges and renumbers in the middle of a run that reports success.
    """
    for year in (2024, 2025):
        name = f"fbar_roster_example_{year}.json"
        (tmp_path / name).write_text(
            open(os.path.join(EXAMPLES, name)).read())

    proc = run_cli("reconcile",
                   "--current", str(tmp_path / "fbar_roster_example_2025.json"),
                   "--prior", str(tmp_path / "fbar_roster_example_2024.json"),
                   "--output-dir", str(tmp_path), "--output-format", "json")
    assert proc.returncode == 1
    assert "over its own input" in json.loads(proc.stdout)["error"]
    survived = json.load(open(tmp_path / "fbar_roster_example_2025.json"))
    assert survived["identity_changes"], "the input roster was overwritten anyway"


def test_cli_reports_a_bad_roster_as_iserror():
    proc = run_cli("validate", "--roster", os.path.join(EXAMPLES, "does_not_exist.json"),
                   "--output-format", "json")
    assert proc.returncode == 1
    assert json.loads(proc.stdout)["isError"] is True


def test_a_renumbered_ledger_matches_on_the_contract_number():
    """A bank migrates a product and issues a new ledger number; the contract
    number survives. Without it this is one closed plus one new account."""
    import fbar_reconciler as fr
    prior = roster([account("old", account_number="40817810000000123474",
                            contract_number="5000000001")], year=2024)
    current = roster([account("new", account_number="40817810900000112233",
                              contract_number="5000000001")])
    matches = fr.match_accounts(current, prior)["matches"]
    assert matches["old"]["current_keys"] == ["new"]
    assert matches["old"]["method"] == fr.MATCH_CONTRACT_NUMBER


def test_a_contract_number_never_matches_across_institutions():
    """Contract numbers are institution-scoped; two banks may reuse a string."""
    import fbar_reconciler as fr
    prior = roster([account("old", number="11111111", contract_number="1234567890",
                            institution={"name": "Bank One", "country": "GE",
                                         "address": {"street": "s", "city": "c",
                                                     "postal_code": "p"}})], year=2024)
    current = roster([account("new", number="22222222", contract_number="1234567890",
                              institution={"name": "Bank Two", "country": "GE",
                                           "address": {"street": "s", "city": "c",
                                                       "postal_code": "p"}})])
    matches = fr.match_accounts(current, prior)["matches"]
    assert matches.get("old", {}).get("current_keys") != ["new"]

