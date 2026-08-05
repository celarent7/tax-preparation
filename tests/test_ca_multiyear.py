"""C3: every published CA year must carry the full 2025-shaped schema and
internally consistent brackets.

These tests are parametrized over the CA years actually present in
tax_constants.json, so each new year added under states.CA is covered
automatically — there is no per-year opt-in.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from utils import load_state_constants

CONSTANTS = os.path.join(os.path.dirname(__file__), "..", "scripts",
                         "tax_constants.json")

CA_YEAR_KEYS = {"behavioral_health_tax", "brackets", "estimated_tax",
                "exemption_credit", "nonconformity", "renters_credit",
                "sdi", "standard_deduction", "supplemental_withholding"}

STATUSES = {"single", "married_jointly", "married_separately",
            "head_of_household", "qualifying_surviving_spouse"}


def ca_years():
    with open(CONSTANTS) as f:
        ca = json.load(f)["states"]["CA"]
    return [y for y in ca if y != "type"]


@pytest.mark.parametrize("year", ca_years())
def test_ca_year_has_full_schema(year):
    data = load_state_constants("CA", year)
    assert CA_YEAR_KEYS <= set(data.keys()), (year, CA_YEAR_KEYS - set(data))


@pytest.mark.parametrize("year", ca_years())
def test_ca_brackets_monotonic_and_terminal(year):
    data = load_state_constants("CA", year)
    for status, brackets in data["brackets"].items():
        assert status in STATUSES
        tops = [b["top"] for b in brackets]
        assert tops[-1] is None, f"{year}/{status}: last bracket must be open-ended"
        finite = tops[:-1]
        assert finite == sorted(finite), f"{year}/{status}: tops not increasing"
        rates = [b["rate"] for b in brackets]
        assert rates == sorted(rates), f"{year}/{status}: rates not increasing"
        assert rates[-1] == 0.123, f"{year}/{status}: top CA rate must be 12.3%"


@pytest.mark.parametrize("year", ca_years())
def test_ca_mfj_brackets_double_single(year):
    """CA MFJ bracket tops are exactly 2x single — a strong transcription check."""
    data = load_state_constants("CA", year)
    single = [b["top"] for b in data["brackets"]["single"]][:-1]
    mfj = [b["top"] for b in data["brackets"]["married_jointly"]][:-1]
    assert mfj == [round(2 * t, 2) for t in single]