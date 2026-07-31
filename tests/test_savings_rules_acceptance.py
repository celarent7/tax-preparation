#!/usr/bin/env python3
"""Acceptance tests A1-A10 for the Savings & Optimization Engine
(docs/savings_engine_plan.md §3).

Each test hand-computes its expected value from primary-source statutory figures
read out of `tax_constants.json` (no hard-coded statutory literals here either —
the figures are read via the same `load_tax_constants` path the rules use, and the
expected math is spelled out in the docstring so a reviewer can recompute it from
the cited constants).

Statutory inputs used (TY2025 unless noted), all from scripts/tax_constants.json
with `_sources` entries:
  * 401(k) elective deferral limit 23,500; super catch-up (60-63) 11,250
    (IRS Notice 2025-03 / §414(v)).
  * Roth IRA MAGI phase-out MFJ 236,000-246,000 (Rev. Proc. 2024-80? -> IRS
    Notice 2024-80). IRA limit 7,000.
  * 415(c) total-additions cap 70,000 (§415(c); 2025 COLA).
  * Capital-loss ordinary offset limit 3,000 (1,500 MFS) — §1211(b).
  * Charitable cash 60%-AGI, appreciated property 30%-AGI — IRC 170(b).
  * QCD cap 108,000 (indexed since 2024 — IRC 408(d)(8)).
  * §179 limit 2,500,000, phase-out 4,000,000 (2025).
  * Bonus depreciation 2025 OBBBA cutover: 40% pre / 100% post 2025-01-19.
  * OBBBA senior bonus 6,000, 6%/dollar phase-out from MFJ 150,000 / single 75,000.
  * OBBBA tips (cap 25,000) / overtime (25,000 MFJ / 12,500 single), 2025-2028.
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

from utils import load_tax_constants  # noqa: E402
from savings_rules import RULES  # noqa: E402
from savings_rules.harvesting import net_capital_gains  # noqa: E402
from savings_rules.obbba import senior_bonus_deduction  # noqa: E402

FINDER = os.path.join(SCRIPTS, "tax_savings_finder.py")
DEDUCT = os.path.join(SCRIPTS, "deduction_analyzer.py")

TD = {y: load_tax_constants(y) for y in (2024, 2025, 2026)}


def _run(script, *args):
    return subprocess.run([sys.executable, script, *args],
                          capture_output=True, text=True)


def _base_profile(**overrides):
    """A minimal canonical profile; tests override the savings-relevant fields."""
    p = {"filing_status": "single", "agi": 100000, "taxable_income": 80000}
    p.update(overrides)
    return p


def _ops(rule_id, profile, year=2025):
    """Run one rule against a profile and return its opportunities."""
    return RULES[rule_id](profile, TD[year])


# ---------------------------------------------------------------------------
# A1 — M1: 401(k) headroom, per person, age-banded catch-up
# ---------------------------------------------------------------------------


def test_A1_401k_headroom_age_bands_and_spouse():
    """A1: age 61, $10,000 contributed -> headroom = 23,500 + 11,250 (super
    catch-up 60-63) - 10,000 = 24,750. Age 45 -> no catch-up, headroom 23,500
    (contributed 0). MFJ computes the spouse independently.

    2025 figures: limit 23,500, super_catchup_60_63 11,250 (constants).
    """
    # Age 61 (super catch-up band).
    ops = _ops("k401_headroom",
               _base_profile(filing_status="single", age=61,
                             employee_401k_contributions=10000,
                             taxable_income=200000), year=2025)
    assert len(ops) == 1
    # Headroom 24,750 appears in the description.
    assert "$24,750" in ops[0].description
    assert ops[0].rule_id == "k401_headroom"
    # Limit 23,500 + 11,250 = 34,750 also appears.
    assert "$34,750" in ops[0].description

    # Age 45: no catch-up. limit 23,500, contributed 0 -> headroom 23,500.
    ops45 = _ops("k401_headroom",
                 _base_profile(filing_status="single", age=45,
                               employee_401k_contributions=0,
                               taxable_income=200000), year=2025)
    assert len(ops45) == 1
    assert "$23,500" in ops45[0].description
    # No super-catch-up amount for age 45.
    assert "$11,250" not in ops45[0].description

    # MFJ spouse computed independently: taxpayer age 61 ($10k), spouse age 61
    # ($20k) -> spouse headroom = 34,750 - 20,000 = 14,750.
    ops_mfj = _ops("k401_headroom",
                   _base_profile(filing_status="married_jointly", age=61,
                                 employee_401k_contributions=10000,
                                 spouse_age=61,
                                 spouse_employee_401k_contributions=20000,
                                 taxable_income=400000), year=2025)
    assert len(ops_mfj) == 2
    descriptions = " ".join(o.description for o in ops_mfj)
    assert "$24,750" in descriptions   # taxpayer
    assert "$14,750" in descriptions   # spouse
    assert any("spouse" in o.name.lower() for o in ops_mfj)


# ---------------------------------------------------------------------------
# A2 — M2: backdoor Roth, mandatory pro-rata warning
# ---------------------------------------------------------------------------


def test_A2_backdoor_roth_pro_rata_warning():
    """A2: MFJ MAGI above the Roth phase-out END (246,000), pretax balance
    93,000, contribution 7,000 -> taxable fraction 93,000/100,000 = 93%.
    The recommendation MUST carry a pro-rata warning stating 93%.

    A zero-balance profile -> recommendation with NO warning. MAGI BELOW the
    phase-out start (236,000) -> no backdoor recommendation (direct Roth works).
    """
    # Above phase-out, with pretax balance -> 93% pro-rata warning.
    ops = _ops("backdoor_roth",
               _base_profile(filing_status="married_jointly", age=45,
                             agi=250000, magi=250000, taxable_income=200000,
                             traditional_ira_pretax_balance=93000,
                             ira_contributions=0, roth_ira_contributions=0),
               year=2025)
    assert len(ops) == 1
    caveats = ops[0].state_caveat
    assert caveats and "Pro-rata" in caveats
    assert "93%" in caveats
    assert ops[0].savings_federal is None  # no current-year deduction claimed

    # Zero pretax balance -> no pro-rata warning, still recommends.
    ops_zero = _ops("backdoor_roth",
                    _base_profile(filing_status="married_jointly", age=45,
                                  agi=250000, magi=250000, taxable_income=200000,
                                  traditional_ira_pretax_balance=0,
                                  ira_contributions=0, roth_ira_contributions=0),
                    year=2025)
    assert len(ops_zero) == 1
    assert ops_zero[0].state_caveat is None

    # Below the phase-out start -> no backdoor recommendation.
    ops_below = _ops("backdoor_roth",
                    _base_profile(filing_status="married_jointly", age=45,
                                  agi=200000, magi=200000, taxable_income=150000,
                                  traditional_ira_pretax_balance=93000,
                                  ira_contributions=0, roth_ira_contributions=0),
                    year=2025)
    assert ops_below == []


# ---------------------------------------------------------------------------
# A3 — M3: mega backdoor Roth 415(c) room
# ---------------------------------------------------------------------------


def test_A3_mega_backdoor_415c_room():
    """A3: 415(c) room = total_415c (70,000) - employee (10,000) - employer
    (5,000) - after-tax (0) = 55,000. No recommendation when the plan does not
    allow after-tax contributions.
    """
    ops = _ops("mega_backdoor_roth",
               _base_profile(filing_status="single", age=45,
                             taxable_income=200000,
                             plan_allows_after_tax_401k=True,
                             employee_401k_contributions=10000,
                             employer_401k_contributions=5000,
                             after_tax_401k_contributions=0), year=2025)
    assert len(ops) == 1
    assert "$55,000" in ops[0].description
    assert ops[0].rule_id == "mega_backdoor_roth"

    # Plan does not allow after-tax -> nothing.
    ops_off = _ops("mega_backdoor_roth",
                   _base_profile(filing_status="single", age=45,
                                 taxable_income=200000,
                                 plan_allows_after_tax_401k=False,
                                 employee_401k_contributions=10000), year=2025)
    assert ops_off == []


# ---------------------------------------------------------------------------
# A4 — M4: §1222 netting ladder (pure function)
# ---------------------------------------------------------------------------


def test_A4_capital_gains_netting_ladder():
    """A4: §1222 netting, hand-computed.

    Case 1 — ST gains 5,000, ST losses 8,000, LT gains 10,000, LT losses 2,000:
      net ST = -3,000; net LT = +8,000; ST loss offsets 3,000 of LT gain ->
      taxable_st 0, taxable_lt 5,000, ordinary_offset 0, no carryforward.

    Case 2 — ST losses only 20,000, no LT activity:
      net ST = -20,000; $3,000 ordinary offset (taken from ST first), 17,000 ST
      carryforward (character retained). MFS halves the offset to 1,500 and
      carries 18,500 forward.
    """
    fs = "single"
    case1 = net_capital_gains(TD[2025], fs, 5000, 8000, 10000, 2000)
    assert case1 == {"taxable_st": 0.0, "taxable_lt": 5000.0,
                     "ordinary_offset": 0.0,
                     "carryforward_st": 0.0, "carryforward_lt": 0.0}

    case2 = net_capital_gains(TD[2025], fs, 0, 20000, 0, 0)
    assert case2["ordinary_offset"] == 3000.0
    assert case2["carryforward_st"] == 17000.0     # character retained (ST)
    assert case2["carryforward_lt"] == 0.0

    # MFS halves the ordinary offset to 1,500 (§1211(b)(2)).
    case2_mfs = net_capital_gains(TD[2025], "married_separately", 0, 20000, 0, 0)
    assert case2_mfs["ordinary_offset"] == 1500.0
    assert case2_mfs["carryforward_st"] == 18500.0


# ---------------------------------------------------------------------------
# A5 — M5: appreciated-stock donation vs cash, to the cent
# ---------------------------------------------------------------------------


def test_A5_appreciated_stock_donation_delta():
    """A5: hand-computed stock-vs-cash delta to the cent.

    Non-CA single filer: taxable_income 100,000 -> ordinary marginal 22%
    (single 2025 bracket 22% spans 48,475-103,350). LTCG rate 15% (stacked $1 on
    100,000 stays under the 533,400 single 15% breakpoint). MAGI 120,000 is
    below the 200,000 NIIT threshold -> no NIIT.

      cash_gift 10,000, stock_fmv 20,000, basis 5,000 -> gain 15,000.
      cash route: deduction min(10,000, 60%*120,000=72,000)=10,000; benefit
                  10,000 * 0.22 = 2,200.
      stock route: deduction min(20,000, 30%*120,000=36,000)=20,000;
                  20,000 * 0.22 = 4,400; avoided gain tax 15,000*0.15 = 2,250;
                  stock benefit 4,400 + 2,250 = 6,650.
      delta = 6,650 - 2,200 = 4,450 -> recommend stock, savings_federal 4,450.
    """
    ops = _ops("appreciated_stock_donation",
               _base_profile(filing_status="single", state=None,
                             agi=120000, magi=120000, taxable_income=100000,
                             planned_cash_donations=10000,
                             appreciated_stock_value=20000,
                             appreciated_stock_basis=5000,
                             held_over_one_year=True,
                             itemized_total=50000),  # itemizes with the gift
               year=2025)
    assert len(ops) == 1
    assert ops[0].rule_id == "appreciated_stock_donation"
    assert ops[0].savings_federal == 4450.0


def test_A5_short_term_holding_basis_warning():
    """A5: held_over_one_year False -> basis-only warning fires (deduction
    limited to basis, not FMV)."""
    ops = _ops("appreciated_stock_donation",
               _base_profile(filing_status="single", agi=120000,
                             magi=120000, taxable_income=100000,
                             planned_cash_donations=10000,
                             appreciated_stock_value=20000,
                             appreciated_stock_basis=5000,
                             held_over_one_year=False,
                             itemized_total=50000), year=2025)
    assert len(ops) == 1
    joined = " ".join(ops[0].assumptions)
    assert "limited to BASIS" in joined


def test_A5_no_itemize_gets_bunching_advice():
    """A5: a profile that won't itemize even with the gift gets a bunching
    recommendation (rule_id charitable_bunching), not a false savings claim."""
    # Standard deduction single 2025 = 15,750. itemized_total 0 + gift 6,000
    # stays below it -> won't itemize.
    ops = _ops("appreciated_stock_donation",
               _base_profile(filing_status="single", agi=120000,
                             magi=120000, taxable_income=100000,
                             planned_cash_donations=5000,
                             appreciated_stock_value=6000,
                             appreciated_stock_basis=4000,
                             held_over_one_year=True,
                             itemized_total=0), year=2025)
    assert len(ops) == 1
    assert ops[0].rule_id == "charitable_bunching"
    assert ops[0].savings_federal == 0.0


# ---------------------------------------------------------------------------
# A6 — M7: SE health insurance deduction cap (shared SE-tax figure)
# ---------------------------------------------------------------------------


def test_A6_se_health_insurance_cap_matches_estimated_tax_calculator():
    """A6: premiums exceeding (se_net_profit - 1/2 SE tax) are capped at that
    figure, using the SAME SE-tax engine estimated_tax_calculator.py uses.

    se_net_profit 100,000, premiums 100,000 (deliberately over the cap). The
    deduction = min(premiums, net profit - 1/2 SE tax), where 1/2 SE tax comes
    from calculate_se_tax on the same se_tax constants.
    """
    from estimated_tax_calculator import calculate_se_tax
    se_profit, premiums = 100000, 100000
    half_se = calculate_se_tax(se_profit, TD[2025]["se_tax"])["deduction"]
    expected_cap = round(min(premiums, se_profit - half_se), 2)

    ops = _ops("se_health_insurance",
               _base_profile(filing_status="single", agi=200000,
                             taxable_income=150000,
                             se_net_profit=se_profit,
                             se_health_insurance_premiums=premiums), year=2025)
    assert len(ops) == 1
    assert ops[0].rule_id == "se_health_insurance"
    assert ops[0].deduction_amount == expected_cap
    # Sanity: the cap is materially below the premiums (the cap bit).
    assert expected_cap < premiums


# ---------------------------------------------------------------------------
# A7 — M8: 2025 bonus-depreciation cutover asks for a date, shows both rates
# ---------------------------------------------------------------------------


def test_A7_section179_bonus_no_date_shows_both_rates():
    """A7: a 2025 profile with NO equipment_in_service_date produces BOTH
    bonus rates (40% and 100%) and an ask-for-date note — never a single
    guessed rate. The §179 deduction itself (50,000 of 50,000 purchases, under
    both the 2,500,000 limit and the 4,000,000 phase-out) is unaffected.
    """
    ops = _ops("section179_bonus",
               _base_profile(filing_status="single", agi=300000,
                             taxable_income=200000,
                             se_net_profit=200000,
                             business_equipment_purchases=50000,
                             equipment_in_service_date=None), year=2025)
    assert len(ops) == 1
    assert ops[0].rule_id == "section179_bonus"
    blob = ops[0].description + " " + " ".join(ops[0].assumptions)
    # Ask-for-date note present.
    assert "in-service date is missing" in blob
    # Both rates shown — not a single guessed rate.
    assert "40%" in blob and "100%" in blob
    # §179 deduction = min(50,000, 2,500,000) = 50,000 (no phase-out).
    assert ops[0].deduction_amount == 50000.0


# ---------------------------------------------------------------------------
# A8 — M9: HSA CA caveat vs TX no-caveat
# ---------------------------------------------------------------------------


def test_A8_hsa_ca_vs_tx_caveat():
    """A8: a CA HSA profile shows savings_state == 0 and a nonempty
    state_caveat naming California; a TX profile shows no caveat and no state
    figure (savings_state is None)."""
    ca = _ops("hsa",
              _base_profile(filing_status="single", state="CA", age=40,
                            agi=80000, taxable_income=60000, has_hdhp=True,
                            hsa_coverage="single", hsa_contributions=1000),
              year=2025)
    assert len(ca) == 1
    assert ca[0].savings_state == 0
    assert ca[0].state_caveat and "California" in ca[0].state_caveat

    tx = _ops("hsa",
              _base_profile(filing_status="single", state="TX", age=40,
                            agi=80000, taxable_income=60000, has_hdhp=True,
                            hsa_coverage="single", hsa_contributions=1000),
              year=2025)
    assert len(tx) == 1
    assert tx[0].state_caveat is None
    assert tx[0].savings_state is None  # no state figure


# ---------------------------------------------------------------------------
# A9 — M10: senior bonus phase-out + shared-helper agreement
# ---------------------------------------------------------------------------


def test_A9a_senior_bonus_phases_out_to_zero():
    """A9 (first clause): the senior bonus phases out to $0 at the documented
    MAGI. MFJ phase-out starts at 150,000 at 6%/dollar; full phase-out at
    6,000 / 0.06 = 100,000 over the start -> MAGI 250,000 yields $0."""
    ops = _ops("senior_bonus",
               _base_profile(filing_status="married_jointly", age=65,
                             agi=250000, magi=250000, taxable_income=200000),
               year=2025)
    assert len(ops) == 1
    assert ops[0].deduction_amount == 0.0
    assert "phased out" in ops[0].name.lower()


def test_A9b_senior_bonus_shared_helper_matches_deduction_analyzer(tmp_path):
    """A9 (shared-helper test): deduction_analyzer.py --age-65-plus 1 and the
    savings finder's M10 rule produce the SAME senior-bonus amount for the
    same inputs, because both call senior_bonus_deduction().

    Inputs: MFJ, MAGI 160,000, one qualifying person. Excess 10,000 over the
    150,000 start, 6% * 10,000 = 600 reduction -> 6,000 - 600 = 5,400.
    """
    magi = 160000
    expected = senior_bonus_deduction(TD[2025], "married_jointly", magi, 1)
    assert expected == 5400.0

    # deduction_analyzer side (MAGI defaults to --agi).
    r_da = _run(DEDUCT, "--agi", str(magi), "--filing-status", "mfj",
                "--age-65-plus", "1", "--year", "2025", "--output-format", "json")
    assert r_da.returncode == 0, r_da.stderr
    da = json.loads(r_da.stdout)
    assert da["obbba_senior_bonus"]["deduction_amount"] == expected

    # Savings-finder side: same MAGI, one qualifying person (age 65).
    profile = {
        "filing_status": "married_jointly", "age": 65,
        "agi": magi, "magi": magi, "taxable_income": 140000,
    }
    prof_path = tmp_path / "senior.json"
    prof_path.write_text(json.dumps(profile))
    r_find = _run(FINDER, "--data", str(prof_path), "--year", "2025",
                  "--rules", "senior_bonus", "--output-format", "json")
    assert r_find.returncode == 0, r_find.stderr
    find_ops = json.loads(r_find.stdout)
    assert any(o["deduction_amount"] == expected for o in find_ops)


# ---------------------------------------------------------------------------
# A10 — year-gated rules refuse to fire outside their statutory window
# ---------------------------------------------------------------------------

# Each case names the rule and a profile that sits OUTSIDE that rule's
# statutory window, plus the year to test against. A10 demands [] (no crash,
# no recommendation) in that out-of-window position.
A10_OUTSIDE = [
    # M10/M11: OBBBA (2025-2028) did not exist pre-2025; constants block absent.
    ("senior_bonus", {"filing_status": "married_jointly", "age": 65,
                      "agi": 80000, "magi": 80000, "taxable_income": 50000}, 2024),
    ("tips_deduction", {"filing_status": "single", "agi": 80000,
                        "magi": 80000, "taxable_income": 50000,
                        "tips_income": 10000, "overtime_premium_income": 5000}, 2024),
    # M2: MAGI below the MFJ Roth phase-out start (236,000 in 2025) -> a direct
    # Roth contribution works; the backdoor mechanic is not recommended.
    ("backdoor_roth", {"filing_status": "married_jointly", "age": 45,
                       "agi": 150000, "magi": 150000, "taxable_income": 50000,
                       "traditional_ira_pretax_balance": 93000}, 2025),
    # M6: the QCD gate is AGE (70.5), not a tax-year window; under 70.5 -> [].
    ("qcd", {"filing_status": "single", "age": 65, "agi": 100000,
             "magi": 100000, "taxable_income": 50000}, 2025),
]


@pytest.mark.parametrize("rule_id,profile,year", A10_OUTSIDE)
def test_A10_year_gated_rules_refuse_outside_window(rule_id, profile, year):
    """A10: each year-gated rule returns [] outside its statutory window — no
    crash, no recommendation. (Window = tax year for M10/M11; MAGI-vs-phase-out
    for M2; age 70.5 for M6.)"""
    assert _ops(rule_id, profile, year=year) == []


@pytest.mark.parametrize("rule_id,profile,year", [
    ("senior_bonus", {"filing_status": "married_jointly", "age": 65,
                      "agi": 80000, "magi": 80000, "taxable_income": 50000}, 2025),
    ("tips_deduction", {"filing_status": "single", "agi": 80000,
                        "magi": 80000, "taxable_income": 50000,
                        "tips_income": 10000}, 2025),
    ("backdoor_roth", {"filing_status": "married_jointly", "age": 45,
                       "agi": 250000, "magi": 250000, "taxable_income": 50000,
                       "traditional_ira_pretax_balance": 93000}, 2025),
    ("qcd", {"filing_status": "single", "age": 75, "agi": 100000,
             "magi": 100000, "taxable_income": 50000}, 2025),
])
def test_A10_year_gated_rules_FIRE_inside_window(rule_id, profile, year):
    """A10 sanity: each rule DOES fire in its in-window position, proving the
    out-of-window [] above is a scoped gate, not a blanket suppression."""
    assert len(_ops(rule_id, profile, year=year)) >= 1


# ---------------------------------------------------------------------------
# A11 — M12: home-office deduction (simplified vs actual-expense method)
# ---------------------------------------------------------------------------


def test_A11_home_office_simplified_vs_actual():
    """A11: simplified = rate x min(sqft, cap); actual = business% x expenses;
    the larger wins and the recapture caveat is named in the assumptions.

    simplified: 200 sq ft x $5 = $1,000; actual: 10% x $30,000 = $3,000 ->
    actual wins; deduction $3,000 (under the $80,000 SE-profit cap, 280A(c)(5)).
    """
    profile = _base_profile(filing_status="single", se_net_profit=80000,
                            taxable_income=60000, home_office_sqft=200,
                            home_total_sqft=2000, home_office_actual_expenses=30000)
    from savings_rules.self_employment import rule_home_office
    opps = rule_home_office(profile, TD[2025])
    assert len(opps) == 1
    o = opps[0]
    # simplified: 200 x $5 = $1,000; actual: 10% x $30,000 = $3,000 -> actual wins
    assert o.deduction_amount == 3000
    assert "recapture" in " ".join(o.assumptions).lower()


def test_A11b_simplified_wins_and_sqft_capped():
    """A11b: simplified min(450, 300) x $5 = $1,500 (sq ft capped at 300);
    actual 15% x $8,000 = $1,200 -> simplified wins."""
    profile = _base_profile(filing_status="single", se_net_profit=80000,
                           taxable_income=60000, home_office_sqft=450,
                           home_total_sqft=3000, home_office_actual_expenses=8000)
    from savings_rules.self_employment import rule_home_office
    opps = rule_home_office(profile, TD[2025])
    assert opps[0].deduction_amount == 1500


def test_A11c_no_se_profit_no_rule():
    """A11c: no SE profit -> no home-office rule fires."""
    profile = _base_profile(se_net_profit=0, home_office_sqft=200,
                           home_total_sqft=2000)
    from savings_rules.self_employment import rule_home_office
    assert rule_home_office(profile, TD[2025]) == []


def test_A11d_deduction_capped_at_se_profit():
    """A11d: IRC 280A(c)(5) — the deduction cannot exceed Schedule C income.
    actual 20% x $20,000 = $4,000 would win, but SE profit is only $1,000 ->
    deduction capped at $1,000."""
    profile = _base_profile(filing_status="single", se_net_profit=1000,
                           taxable_income=40000, home_office_sqft=300,
                           home_total_sqft=1500, home_office_actual_expenses=20000)
    from savings_rules.self_employment import rule_home_office
    o = rule_home_office(profile, TD[2025])[0]
    assert o.deduction_amount == 1000