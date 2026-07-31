"""B2: LLC, FTC no-1116 election, and clean-energy credit calculators."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from credit_eligibility_checker import (
    calculate_llc,
    calculate_ftc_simple,
    get_credit_params,
)
from utils import load_tax_constants


@pytest.fixture(scope="module")
def data_2025():
    return load_tax_constants("2025")


def _llc_params(tax_data):
    return get_credit_params(tax_data)["lifetime_learning"]


def test_llc_20_pct_of_expenses_capped(data_2025):
    r = calculate_llc({"filing_status": "single", "agi": 50000,
                       "education_expenses_llc": 12000}, _llc_params(data_2025))
    assert r.eligible
    assert r.amount == 2000            # 20% x $10,000 cap
    assert r.refundable_amount == 0    # LLC is never refundable


def test_llc_phaseout_band(data_2025):
    p = _llc_params(data_2025)
    # Mid-band MAGI: credit reduced but nonzero
    r = calculate_llc({"filing_status": "single", "agi": 85000,
                       "education_expenses_llc": 10000}, p)
    assert r.eligible and 0 < r.amount < 2000 and r.phaseout_applied


def test_llc_over_band_ineligible(data_2025):
    r = calculate_llc({"filing_status": "single", "agi": 200000,
                       "education_expenses_llc": 10000}, _llc_params(data_2025))
    assert not r.eligible and r.amount == 0


def test_llc_mfs_ineligible(data_2025):
    r = calculate_llc({"filing_status": "mfs", "agi": 40000,
                       "education_expenses_llc": 5000}, _llc_params(data_2025))
    assert not r.eligible


def test_llc_mutual_exclusion_note_with_aotc(data_2025):
    r = calculate_llc({"filing_status": "single", "agi": 50000,
                       "education_expenses_llc": 5000,
                       "aotc_claimed_for_same_student": True},
                      _llc_params(data_2025))
    assert not r.eligible
    assert any("same student" in n.lower() for n in r.notes)


def _ftc_params(tax_data):
    return tax_data["credits_static"]["ftc_no_1116_limit"]


def test_ftc_under_limit_full_credit(data_2025):
    r = calculate_ftc_simple({"filing_status": "single", "foreign_tax_paid": 210},
                             _ftc_params(data_2025))
    assert r.eligible and r.amount == 210 and r.refundable_amount == 0


def test_ftc_mfj_limit_600(data_2025):
    r = calculate_ftc_simple({"filing_status": "mfj", "foreign_tax_paid": 480},
                             _ftc_params(data_2025))
    assert r.eligible and r.amount == 480


def test_ftc_over_limit_flags_form_1116(data_2025):
    r = calculate_ftc_simple({"filing_status": "single", "foreign_tax_paid": 900},
                             _ftc_params(data_2025))
    assert not r.eligible and r.amount == 0
    assert any("1116" in n for n in r.notes)


def test_energy_credits_present_2023_2025_absent_2026():
    for year in ("2023", "2024", "2025"):
        assert "energy_credits" in load_tax_constants(year), year
    assert "energy_credits" not in load_tax_constants("2026")


def test_energy_termination_dates_only_in_2025():
    for year in ("2023", "2024"):
        ec = load_tax_constants(year)["energy_credits"]
        for block in ec.values():
            assert "terminated_after" not in block, (year, block)
    ec25 = load_tax_constants("2025")["energy_credits"]
    assert ec25["new_clean_vehicle_30d"]["terminated_after"] == "2025-09-30"
    assert ec25["home_improvement_25c"]["terminated_after"] == "2025-12-31"