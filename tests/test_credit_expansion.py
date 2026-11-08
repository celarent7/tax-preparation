"""B2: LLC, FTC no-1116 election, and clean-energy credit calculators."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from credit_eligibility_checker import (
    calculate_llc,
    calculate_ftc_simple,
    calculate_savers_credit,
    check_all_credits,
    check_energy_credits,
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


def _savers_params(tax_data):
    return get_credit_params(tax_data)["savers_credit"]


def test_savers_credit_hoh_bands_derive_from_mfj_not_single(data_2025):
    """HoH Saver's Credit AGI bands are 75% of the MFJ bands per IRC 25B(b),
    not 75% of the single bands. HoH $30,000 AGI is below the HoH 50%
    threshold (mfj $47,500 x 0.75 = $35,625), so a $2,000 contribution earns
    the full 50% credit ($1,000). Deriving from single x 0.75 would instead
    put $30,000 in the 10% bracket (single $23,750 x 0.75 = $17,812.50 for
    50%, $25,500 x 0.75 = $19,125 for 20%), yielding only $200 -- a 5x
    understatement.
    """
    r = calculate_savers_credit({
        "filing_status": "head_of_household",
        "agi": 30000,
        "retirement_contributions": 2000,
        "age": 40,
    }, _savers_params(data_2025))
    assert r.eligible
    assert r.amount == 1000.0


def test_savers_credit_single_and_mfj_bands_unchanged(data_2025):
    """Regression: single and MFJ Saver's Credit bands are unaffected by the
    HoH fix (single/MFS/QSS use the single bands unscaled; MFJ uses the MFJ
    bands unscaled)."""
    r_single = calculate_savers_credit({
        "filing_status": "single", "agi": 20000,
        "retirement_contributions": 2000, "age": 40,
    }, _savers_params(data_2025))
    assert r_single.amount == 1000.0  # single 50% band: $23,750

    r_mfj = calculate_savers_credit({
        "filing_status": "married_jointly", "agi": 45000,
        "retirement_contributions": 2000, "age": 40,
    }, _savers_params(data_2025))
    assert r_mfj.amount == 1000.0  # mfj 50% band: $47,500



def test_savers_credit_2025_bands_match_notice_2024_80(data_2025):
    """Notice 2024-80: joint 50% band ends at $47,500 and 20% at $51,000.
    The old figures (49,000 / 53,000) put a $48,000 joint filer at 50%."""
    p = _savers_params(data_2025)
    base = {"filing_status": "married_jointly", "retirement_contributions": 2000, "age": 40}
    assert calculate_savers_credit({**base, "agi": 48000}, p).amount == 400.0   # 20%
    assert calculate_savers_credit({**base, "agi": 80000}, p).eligible is False  # over $79,000


@pytest.mark.parametrize("year,creditable", [
    ("2023", True), ("2024", True), ("2025", True), ("2026", True), ("2027", False),
])
def test_savers_retirement_contributions_creditable_by_year(year, creditable):
    """P.L. 119-21 sec. 70116: 25B(d)(1)(B) counts retirement contributions only
    for taxable years beginning before January 1, 2027."""
    p = _savers_params(load_tax_constants(year))
    assert p["retirement_contributions_creditable"] is creditable


def test_savers_credit_not_available_for_retirement_contributions_2027():
    """2027: retirement contributions earn no Saver's Credit (they count toward
    the Saver's Match), and the unpublished 2027 bands must not raise."""
    r = calculate_savers_credit({
        "filing_status": "married_jointly", "agi": 40000,
        "retirement_contributions": 2000, "age": 40,
    }, _savers_params(load_tax_constants("2027")))
    assert r.eligible is False
    assert r.amount == 0
    note = " ".join(r.notes)
    assert "Saver's Match" in note
    assert "6433" in note
    assert "ABLE" in note


def test_check_all_credits_2027_does_not_raise():
    """Review finding F1: every 2027 credit check used to fail on the null
    Saver's Credit bands, whatever the filer's situation."""
    out = check_all_credits({"filing_status": "mfj", "agi": 300000,
                             "earned_income": 300000, "children_under_17": 1},
                            load_tax_constants("2027"))
    savers = [c for c in out["credits"] if c["name"] == "Saver's Credit"]
    assert savers and savers[0]["eligible"] is False


def test_savers_credit_no_contributions_skips_unverified_bands():
    """With nothing to credit, null bands must not raise."""
    params = {"max_contribution": 2000, "retirement_contributions_creditable": True,
              "bands": {k: {"50_percent": None, "20_percent": None, "10_percent": None}
                        for k in ("mfj", "single")}}
    r = calculate_savers_credit({"filing_status": "single", "agi": 20000,
                                 "retirement_contributions": 0}, params)
    assert r.eligible is False

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


def test_30d_eligible_within_magi_and_msrp(data_2025):
    rs = check_energy_credits({"filing_status": "mfj", "magi": 250000,
                               "ev_purchase_price": 52000, "ev_msrp": 52000,
                               "ev_purchase_date": "2025-06-01"}, data_2025)
    r = next(x for x in rs if "New Clean Vehicle" in x.name)
    assert r.eligible and r.amount == 7500


def test_30d_lesser_of_current_or_prior_magi(data_2025):
    # Current MAGI over the limit, prior year under -> still eligible (IRC 30D(f)(10)(B)).
    rs = check_energy_credits({"filing_status": "mfj", "magi": 400000,
                               "magi_prior_year": 250000,
                               "ev_purchase_price": 52000, "ev_msrp": 52000,
                               "ev_purchase_date": "2025-06-01"}, data_2025)
    r = next(x for x in rs if "New Clean Vehicle" in x.name)
    assert r.eligible


def test_30d_purchase_after_termination_ineligible(data_2025):
    rs = check_energy_credits({"filing_status": "mfj", "magi": 100000,
                               "ev_purchase_price": 52000, "ev_msrp": 52000,
                               "ev_purchase_date": "2025-10-15"}, data_2025)
    r = next(x for x in rs if "New Clean Vehicle" in x.name)
    assert not r.eligible
    assert any("2025-09-30" in n for n in r.notes)


def test_25e_used_vehicle_30pct_capped(data_2025):
    rs = check_energy_credits({"filing_status": "single", "magi": 60000,
                               "ev_purchase_price": 12000, "ev_is_used": True,
                               "ev_purchase_date": "2025-03-01"}, data_2025)
    r = next(x for x in rs if "Used" in x.name)
    assert r.eligible and r.amount == 3600  # 30% x 12,000, under the $4,000 cap


def test_25c_annual_and_heat_pump_limits(data_2025):
    rs = check_energy_credits({"filing_status": "single", "magi": 90000,
                               "home_improvement_costs": 6000,
                               "heat_pump_costs": 8000}, data_2025)
    r = next(x for x in rs if "Home Improvement" in x.name)
    # 30% x 6000 = 1800 -> capped 1200; heat pump 30% x 8000 = 2400 -> capped 2000
    assert r.eligible and r.amount == 3200


def test_energy_credits_2026_not_available():
    rs = check_energy_credits({"filing_status": "single", "magi": 90000,
                               "solar_costs": 20000}, load_tax_constants("2026"))
    assert rs == [] or all(not r.eligible for r in rs)

# ---------------------------------------------------------------------------
# Child and Dependent Care Credit — per-year tiers, OBBBA 2026+ (plan CC Task 2)
# ---------------------------------------------------------------------------

from credit_eligibility_checker import (  # noqa: E402
    calculate_child_dependent_care,
    cdcc_applicable_percentage,
)


def _cdcc_params(year):
    return get_credit_params(load_tax_constants(year))["child_dependent_care"]


def _cdcc_data(year_status="married_jointly", agi=100000, persons=2,
               expenses=6000, fsa=0):
    return {"filing_status": year_status, "agi": agi,
            "qualifying_dependents_care": persons, "childcare_expenses": expenses,
            "earned_income": 100000, "spouse_earned_income": 100000,
            "dependent_care_fsa": fsa}


def test_cdcc_percentage_pre_obbba_rounds_fraction_up():
    """IRC 21(a)(2) pre-2026: 1 point per $2,000 'or fraction thereof' over $15,000."""
    p = _cdcc_params("2025")
    assert cdcc_applicable_percentage(15000, "single", p) == 35
    assert cdcc_applicable_percentage(15001, "single", p) == 34
    assert cdcc_applicable_percentage(43000, "single", p) == 21
    assert cdcc_applicable_percentage(43001, "single", p) == 20


def test_cdcc_percentage_2026_two_tiers():
    p = _cdcc_params("2026")
    expected = {15000: 50, 15001: 49, 45000: 35, 75000: 35, 100000: 22, 105000: 20}
    for agi, pct in expected.items():
        assert cdcc_applicable_percentage(agi, "single", p) == pct, agi


def test_cdcc_percentage_2026_joint_second_tier():
    p = _cdcc_params("2026")
    for agi, pct in {150000: 35, 200000: 22, 210000: 20}.items():
        assert cdcc_applicable_percentage(agi, "married_jointly", p) == pct, agi


def test_cdcc_qss_and_hoh_use_non_joint_second_tier():
    """Only a joint return gets the $150,000 / $4,000 second tier."""
    p = _cdcc_params("2026")
    assert cdcc_applicable_percentage(100000, "qualifying_surviving_spouse", p) == 22
    assert cdcc_applicable_percentage(100000, "head_of_household", p) == 22


def test_cdcc_mfs_not_eligible():
    r = calculate_child_dependent_care(
        _cdcc_data("married_separately", agi=50000), _cdcc_params("2025"))
    assert r.eligible is False
    assert r.amount == 0
    note = " ".join(r.notes)
    assert "21(e)(2)" in note
    assert "21(e)(4)" in note
    assert "last 6 months" in note


def test_cdcc_dollar_limit_reduced_by_excluded_benefits():
    """IRC 21(c) last sentence / Form 2441 lines 27-29: excluded benefits
    reduce the $3,000/$6,000 limit, not only the expenses."""
    r = calculate_child_dependent_care(
        _cdcc_data(expenses=10000, fsa=5000), _cdcc_params("2025"))
    assert r.amount == 200.0


def test_cdcc_2026_fsa_at_7500_eliminates_two_person_credit():
    r = calculate_child_dependent_care(
        _cdcc_data(expenses=10000, fsa=7500), _cdcc_params("2026"))
    assert r.amount == 0
    assert r.eligible is False


def test_cdcc_2026_credit_amount():
    r = calculate_child_dependent_care(_cdcc_data(expenses=6000), _cdcc_params("2026"))
    assert r.amount == 2100.0
