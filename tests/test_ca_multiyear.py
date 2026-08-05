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


# ---------------------------------------------------------------------------
# Anchor values — literal figures typed directly from the FTB/EDD primary
# documents at transcription time (NOT re-read from the JSON). Each anchor is
# an independent transcription of a printed source figure.
# ---------------------------------------------------------------------------

def test_ca_2024_anchor_values():
    d = load_state_constants("CA", "2024")
    # 2024 Form 540 Booklet — standard deduction (authoritative booklet figures,
    # not the 540-ES worksheet's prior-year proxy).
    assert d["standard_deduction"]["single"] == 5540
    assert d["standard_deduction"]["married_jointly"] == 11080
    # 2024 Form 540 Booklet — exemption credits and phase-out.
    assert d["exemption_credit"]["personal"] == 149
    assert d["exemption_credit"]["dependent"] == 461
    assert d["exemption_credit"]["phaseout_federal_agi_start"] == 244857
    # 2024 540 Booklet — renter's credit AGI limits.
    assert d["renters_credit"]["agi_limit_single"] == 52421
    assert d["renters_credit"]["agi_limit_joint"] == 104842
    # 2024 Tax Rate Schedules — first single bracket floor and top rate.
    assert d["brackets"]["single"][0]["top"] == 10756
    assert d["brackets"]["single"][-1]["rate"] == 0.123
    # EDD DE 44 2024 — first uncapped SDI year (SB 951).
    assert d["sdi"]["rate"] == 0.011
    assert d["sdi"]["wage_cap"] is None
    # 2024 540-ES — installment due dates (Q2/Q3 shifted for weekends/holidays).
    assert d["estimated_tax"]["due_dates"][1] == ["Q2", "2024-06-17"]
    assert d["estimated_tax"]["due_dates"][2] == ["Q3", "2024-09-16"]
    # OBBBA did not exist federally in 2024 — flag is vacuous-true (add-back
    # structurally suppressed; see _sources.ftb_2024_form_540 note).
    assert d["nonconformity"]["obbba_tips_overtime_senior"] is True


def test_ca_2023_anchor_values():
    d = load_state_constants("CA", "2023")
    # 2023 Form 540 Booklet — standard deduction.
    assert d["standard_deduction"]["single"] == 5363
    assert d["standard_deduction"]["married_jointly"] == 10726
    # 2023 Form 540 Booklet — exemption credits and phase-out.
    assert d["exemption_credit"]["personal"] == 144
    assert d["exemption_credit"]["dependent"] == 446
    assert d["exemption_credit"]["phaseout_federal_agi_start"] == 237035
    # 2023 Form 540 Booklet — renter's credit AGI limits.
    assert d["renters_credit"]["agi_limit_single"] == 50746
    assert d["renters_credit"]["agi_limit_joint"] == 101492
    # 2023 Tax Rate Schedules — first single bracket floor and top rate.
    assert d["brackets"]["single"][0]["top"] == 10412
    assert d["brackets"]["single"][-1]["rate"] == 0.123
    # EDD DE 44 2023 — last CAPPED SDI year (cap removed by SB 951 in 2024).
    assert d["sdi"]["rate"] == 0.009
    assert d["sdi"]["wage_cap"] == 153164
    # 2023 540-ES — installment due dates (Q1 shifted for Emancipation Day).
    assert d["estimated_tax"]["due_dates"][0] == ["Q1", "2023-04-18"]
    assert d["estimated_tax"]["due_dates"][3] == ["Q4", "2024-01-16"]
    # OBBBA did not exist federally in 2023 — flag is vacuous-true.
    assert d["nonconformity"]["obbba_tips_overtime_senior"] is True
    # The capped wage cap means the excess-SDI credit is live for 2023.
    assert d["sdi"]["wage_cap"] is not None