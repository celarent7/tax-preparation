#!/usr/bin/env python3
"""Tests for the canonical profile loader & legacy adapter (savings_engine_plan
§1.1, acceptance gates G3 & G4).

  G3 — the legacy nested shape runs through the adapter with a deprecation
       warning and produces identical opportunities to its canonical twin.
  G4 — a profile with an unknown key fails with an error naming the nearest
       valid key — no silent `agi = 0` zero result.
"""

import json
import os
import subprocess
import sys
import warnings

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

from savings_profile import load_profile, ProfileError  # noqa: E402

FINDER = os.path.join(SCRIPTS, "tax_savings_finder.py")


def run_finder(profile_path, *extra):
    return subprocess.run(
        [sys.executable, FINDER, "--data", profile_path, *extra],
        capture_output=True, text=True,
    )


# ---------------------------------------------------------------------------
# G4 — unknown keys are rejected, naming the nearest valid key
# ---------------------------------------------------------------------------


def test_unknown_key_is_rejected_naming_nearest(tmp_path):
    """G4: `"aggi"` must raise an error that suggests `"agi"` — the silent-$0
    failure mode documented in the remediation log must not return."""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"filing_status": "mfj", "aggi": 100000}))
    with pytest.raises(ProfileError) as exc:
        load_profile(str(bad))
    msg = str(exc.value)
    assert "aggi" in msg
    assert "'agi'" in msg  # nearest valid key is named


def test_unknown_key_with_no_close_match_still_rejects(tmp_path):
    """An unknown key with no close match still fails loudly (lists valid keys)."""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"filing_status": "single", "zzz_nonsense": 1}))
    with pytest.raises(ProfileError):
        load_profile(str(bad))


def test_missing_required_filing_status_fails(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"agi": 100000}))
    with pytest.raises(ProfileError):
        load_profile(str(bad))


def test_canonical_load_applies_defaults_and_normalizes(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"filing_status": "mfj", "agi": 100000}))
    prof = load_profile(str(p))
    assert prof["filing_status"] == "married_jointly"
    assert prof["magi"] == 100000  # defaults to agi
    assert prof["has_hdhp"] is False
    assert prof["employee_401k_contributions"] == 0


# ---------------------------------------------------------------------------
# B4.1 — home-office profile keys are canonical (M12 rule reads these)
# ---------------------------------------------------------------------------


def test_home_office_keys_are_canonical():
    from savings_profile import CANONICAL_FIELDS
    for key in ("home_office_sqft", "home_total_sqft", "home_office_actual_expenses"):
        assert key in CANONICAL_FIELDS, key
        assert CANONICAL_FIELDS[key] == 0


def test_home_office_typo_suggests_correct_key(tmp_path):
    """A typo'd home-office key is rejected with the nearest valid key named —
    no silent $0 result (the G4 failure mode the loader was built to prevent)."""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"filing_status": "single", "home_ofice_sqft": 200}))
    with pytest.raises(ProfileError) as exc:
        load_profile(str(bad))
    assert "home_office_sqft" in str(exc.value)


# ---------------------------------------------------------------------------
# G3 — legacy adapter produces identical opportunities to its canonical twin
# ---------------------------------------------------------------------------


def test_legacy_example_runs_through_adapter_with_deprecation_warning(tmp_path):
    """The committed legacy example loads through the adapter and emits a
    DeprecationWarning listing the mapped keys."""
    legacy = os.path.join(ROOT, "examples", "sample_tax_scenario_legacy.json")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load_profile(legacy)
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
    msg = str(caught[0].message)
    assert "hsa_contributions" in msg  # a mapped key is named


def test_legacy_and_canonical_twin_produce_identical_opportunities(tmp_path):
    """G3: a nested legacy profile and its flat canonical twin, holding the
    same savings-relevant values, yield identical finder output."""
    legacy = {
        "tax_year": 2025,
        "filing_status": "married_jointly",
        "income": {
            "w2_wages": 185000,
            "interest_income": 1250,
            "dividend_income": 3400,
            "qualified_dividends": 2800,
            "capital_gains_short": 0,
            "capital_gains_long": 5200,
        },
        "deductions": {"charitable_cash": 3000},
        "adjustments": {"traditional_ira": 0, "hsa_contributions": 8550},
    }
    # Canonical twin: same values the adapter maps the legacy shape onto.
    # agi folds interest + dividend_income only; qualified_dividends is a
    # subset of dividend_income (not a separate canonical field) and adds
    # nothing further.
    canonical = {
        "tax_year": 2025,
        "filing_status": "married_jointly",
        "wages": 185000,
        "agi": 1250 + 3400,
        "realized_st_gains": 0,
        "realized_lt_gains": 5200,
        "planned_cash_donations": 3000,
        "ira_contributions": 0,
        "hsa_contributions": 8550,
    }
    lp = tmp_path / "legacy.json"
    cp = tmp_path / "canonical.json"
    lp.write_text(json.dumps(legacy))
    cp.write_text(json.dumps(canonical))

    r_leg = run_finder(str(lp), "--year", "2025", "--output-format", "json")
    r_can = run_finder(str(cp), "--year", "2025", "--output-format", "json")
    assert r_leg.returncode == 0, r_leg.stderr
    assert r_can.returncode == 0, r_can.stderr

    leg_ops = json.loads(r_leg.stdout)
    can_ops = json.loads(r_can.stdout)
    # Same opportunities (names + savings fields) in the same order.
    norm = lambda ops: [(o["rule_id"], o["name"], o["savings_federal"]) for o in ops]
    assert norm(leg_ops) == norm(can_ops)


def test_canonical_example_round_trips_and_fires_expected_rules():
    """G2 (engine-wide): the rewritten canonical example parses as JSON and
    fires the M1, M2, M4 and M9 rules (M2 must carry the pro-rata caveat)."""
    example = os.path.join(ROOT, "examples", "sample_tax_scenario.json")
    result = run_finder(example, "--year", "2025", "--output-format", "json")
    assert result.returncode == 0, result.stderr
    ops = json.loads(result.stdout)
    rule_ids = {o["rule_id"] for o in ops}
    assert "k401_headroom" in rule_ids
    assert "backdoor_roth" in rule_ids
    backdoor = next(o for o in ops if o["rule_id"] == "backdoor_roth")
    assert backdoor["state_caveat"] and "Pro-rata" in backdoor["state_caveat"]
    assert "93%" in backdoor["state_caveat"]
    assert "tlh" in rule_ids
    hsa = next(o for o in ops if o["rule_id"] == "hsa")
    assert hsa["savings_state"] == 0
    assert hsa["state_caveat"] and "California" in hsa["state_caveat"]

# ---------------------------------------------------------------------------
# Legacy adapter: adjustments.* deductions subtract from agi; income.* adds
# ---------------------------------------------------------------------------

def test_legacy_student_loan_interest_subtracts_from_agi(tmp_path):
    """adjustments.student_loan_interest is an above-the-line DEDUCTION and
    must subtract from agi, not add to it. Since magi defaults to agi when
    not explicitly given, a sign error here would silently distort every
    MAGI-gated rule (Roth phase-out, HSA, backdoor-Roth trigger)."""
    legacy = {
        "filing_status": "single",
        "income": {"w2_wages": 100000, "interest_income": 5000},
        "adjustments": {"student_loan_interest": 2500},
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(legacy))
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        profile = load_profile(str(path))
    # agi = 5000 (interest) - 2500 (student loan interest deduction) = 2500
    assert profile["agi"] == 2500


def test_legacy_qualified_dividends_not_double_counted(tmp_path):
    """income.qualified_dividends is a subset of income.dividend_income
    (1099-DIV box 1b within box 1a); it must not add a second time to agi."""
    legacy = {
        "filing_status": "single",
        "income": {"dividend_income": 3400, "qualified_dividends": 2800},
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(legacy))
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        profile = load_profile(str(path))
    assert profile["agi"] == 3400
