#!/usr/bin/env python3
"""Data-integrity tests for scripts/tax_constants.json.

Every expected value here is transcribed from a primary source cited in the
test, not from another part of the codebase.

Covers audit findings:
  F14 (discovered during remediation, not in the original audit matrix) — the
       2024 married-filing-separately long-term capital gain 15% ceiling was
       $259,450, which is half of the *single* ceiling ($518,900). The correct
       figure is $291,850 (half of the MFJ ceiling, per Rev. Proc. 2023-34).
  Y2  — declared supported_years stays in sync with the year blocks present.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from utils import SUPPORTED_YEARS, load_raw_constants, load_tax_constants  # noqa: E402


# ---------------------------------------------------------------------------
# F14 — long-term capital gain thresholds
# ---------------------------------------------------------------------------

# Rev. Proc. 2023-34 section .03 "Maximum Capital Gains Rate", taxable years
# beginning in 2024. https://www.irs.gov/pub/irs-drop/rp-23-34.pdf
LTCG_2024 = {
    "married_jointly":     (94050, 583750),
    "married_separately":  (47025, 291850),
    "head_of_household":   (63000, 551350),
    "single":              (47025, 518900),
}

# IRS Topic no. 409, "Capital gains and losses", taxable years beginning in
# 2025. https://www.irs.gov/taxtopics/tc409
LTCG_2025 = {
    "married_jointly":     (96700, 600050),
    "married_separately":  (48350, 300000),
    "head_of_household":   (64750, 566700),
    "single":              (48350, 533400),
}


@pytest.mark.parametrize("year,expected", [("2024", LTCG_2024), ("2025", LTCG_2025)])
def test_ltcg_brackets_match_primary_source(year, expected):
    """F14: LTCG 0%/15% ceilings match the IRS figures for each filing status."""
    brackets = load_tax_constants(year)["ltcg_brackets"]
    for status, (zero_top, fifteen_top) in expected.items():
        actual = brackets[status]
        assert actual[0]["top"] == zero_top, f"{year} {status} 0% ceiling"
        assert actual[0]["rate"] == 0.00
        assert actual[1]["top"] == fifteen_top, f"{year} {status} 15% ceiling"
        assert actual[1]["rate"] == 0.15
        assert actual[2]["top"] is None, f"{year} {status} top bracket is open-ended"
        assert actual[2]["rate"] == 0.20


def test_mfs_ltcg_ceiling_is_half_of_mfj_not_half_of_single():
    """F14 regression: the specific arithmetic slip that produced $259,450.

    MFS thresholds track the MFJ ones (roughly half), not the single ones.
    Half of single's 2024 ceiling is $259,450 — the wrong value that was there.
    """
    for year in ("2024", "2025"):
        brackets = load_tax_constants(year)["ltcg_brackets"]
        mfs_top = brackets["married_separately"][1]["top"]
        mfj_top = brackets["married_jointly"][1]["top"]
        single_top = brackets["single"][1]["top"]

        assert abs(mfs_top - mfj_top / 2) <= 100, (
            f"{year}: MFS 15% ceiling {mfs_top} should be ~half of MFJ {mfj_top}"
        )
        assert mfs_top != single_top / 2, (
            f"{year}: MFS ceiling equals half the SINGLE ceiling — the F14 bug"
        )


# ---------------------------------------------------------------------------
# Structural invariants across every supported year
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("year", SUPPORTED_YEARS)
def test_every_year_has_required_blocks(year):
    """Each supported year carries the blocks the engines depend on."""
    data = load_tax_constants(year)
    for key in ("tax_brackets", "standard_deductions", "additional_deduction_65_blind",
                "salt", "ltcg_brackets", "niit_thresholds", "se_tax",
                "retirement_limits", "credits", "niit_rate"):
        assert key in data, f"{year} missing {key!r}"


@pytest.mark.parametrize("year", SUPPORTED_YEARS)
def test_brackets_are_ascending_and_open_ended(year):
    """Bracket tables must be sorted and end with an unbounded top bracket."""
    data = load_tax_constants(year)
    for table_name in ("tax_brackets", "ltcg_brackets"):
        for status, rows in data[table_name].items():
            tops = [r["top"] for r in rows]
            assert tops[-1] is None, f"{year} {table_name} {status} must be open-ended"
            finite = [t for t in tops if t is not None]
            assert finite == sorted(finite), f"{year} {table_name} {status} not ascending"
            rates = [r["rate"] for r in rows]
            assert rates == sorted(rates), f"{year} {table_name} {status} rates not ascending"


@pytest.mark.parametrize("year", SUPPORTED_YEARS)
@pytest.mark.parametrize("table", ["tax_brackets", "ltcg_brackets", "niit_thresholds",
                                   "standard_deductions", "additional_deduction_65_blind"])
def test_all_filing_statuses_present_in_every_status_table(year, table):
    """Every canonical filing status appears in every per-status table.

    Regression guard: `ltcg_brackets` and `niit_thresholds` originally omitted
    `qualifying_surviving_spouse`. Before the D2 fix that silently fell back to
    the single-filer figures (wrong); after it, a QSS filer with capital gains
    raised ValueError instead. Asserting over ALL per-status tables — not just
    tax_brackets — is what catches this class of gap.
    """
    from utils import CANONICAL_FILING_STATUSES

    mapping = load_tax_constants(year)[table]
    for status in CANONICAL_FILING_STATUSES:
        assert status in mapping, f"{year} {table} missing {status!r}"


@pytest.mark.parametrize("year", SUPPORTED_YEARS)
def test_qss_thresholds_follow_the_right_statute(year):
    """QSS is treated differently by the LTCG, NIIT and Additional Medicare rules.

    * LTCG — Rev. Proc. tables read "Married Individuals Filing Joint Returns
      AND SURVIVING SPOUSE", so QSS takes the MFJ thresholds.
    * NIIT — IRC 1411(b)(1) lists "qualifying widow(er) with a child" at
      $250,000, i.e. the MFJ figure.
    * Additional Medicare — IRC 3101(b)(2) says $250,000 only for "a joint
      return"; QSS falls into "any other case" at $200,000.

    Getting the last one wrong by analogy to the first two is an easy mistake,
    so it is asserted explicitly.
    """
    data = load_tax_constants(year)

    assert data["ltcg_brackets"]["qualifying_surviving_spouse"] == \
        data["ltcg_brackets"]["married_jointly"]
    assert data["niit_thresholds"]["qualifying_surviving_spouse"] == \
        data["niit_thresholds"]["married_jointly"] == 250000
    assert data["se_tax"]["additional_medicare_threshold_qualifying_surviving_spouse"] == 200000
    assert data["se_tax"]["additional_medicare_threshold_married"] == 250000


def test_sources_map_exists_and_entries_are_well_formed():
    """Ground rule 1: every recorded source carries a URL, date and key list."""
    sources = load_raw_constants()["_sources"]
    entries = {k: v for k, v in sources.items()
               if not k.startswith("_") and k != "TODO_VERIFY"}
    assert entries, "at least one primary source must be recorded"

    for name, entry in entries.items():
        assert entry.get("url", "").startswith("http"), f"{name} needs a URL"
        assert entry.get("retrieved"), f"{name} needs a retrieval date"
        assert entry.get("backs"), f"{name} must list the keys it backs"


def test_no_unverified_constant_lacks_a_todo_entry():
    """Any null statutory figure must be declared in _sources.TODO_VERIFY.

    Guards the ground-rule-1 contract: a null is an *acknowledged* gap, never a
    silent one. Bracket `top: null` means "open-ended" and is excluded.
    """
    raw = load_raw_constants()
    todos = raw["_sources"].get("TODO_VERIFY", {})
    undeclared = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                # `top: null` is the open-ended top bracket, not a missing figure.
                if key == "top" and value is None:
                    continue
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")
        elif node is None:
            leaf = path.split(".")[-1]
            if not any(k in path or path.endswith(k) for k in todos) and leaf not in todos:
                undeclared.append(path)

    for year in SUPPORTED_YEARS:
        walk(raw[year], year)

    assert not undeclared, (
        "null statutory figures with no _sources.TODO_VERIFY entry: "
        + ", ".join(sorted(undeclared))
    )
