#!/usr/bin/env python3
"""Validates the multi-year constants expansion (blueprint 3.1 steps 1 & 3).

Covers audit findings:
  Y2 — coverage was only 2024-2025; no 2023 (amended returns) or 2026 (planning).
  F6 — the EITC block's phaseout values matched neither year's Rev. Proc.
  F7 — the OBBBA individual deductions were absent everywhere.
  F8 — no AMT data at all (trigger-flag support).

Several rates are verified here by *arithmetic identity* against separately
published figures rather than by quoting them. That is a stronger check than a
transcription: if either the rate or the published amount were wrong, the
identity would not hold to the dollar.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from utils import SUPPORTED_YEARS, get_constant, load_tax_constants  # noqa: E402


def test_supported_years_cover_2023_through_2026():
    """Y2: amended-return years and the planning year are both present."""
    assert SUPPORTED_YEARS == ["2023", "2024", "2025", "2026"]


# ---------------------------------------------------------------------------
# F6 — EITC
# ---------------------------------------------------------------------------

CHILD_COUNTS = ("0", "1", "2", "3")


@pytest.mark.parametrize("year", SUPPORTED_YEARS)
def test_eitc_max_credit_equals_earned_amount_times_phase_in_rate(year):
    """F6: max_credit = earned_income_amount x phase_in_rate, to the dollar.

    IRC 32(b)(1) phase-in percentages are 7.65 / 34 / 40 / 45. The Rev. Proc.
    publishes the earned income amount and the max credit but not the rate, so
    this identity confirms the stored rate against two published figures.
    Example (2025, two children): $17,880 x 0.40 = $7,152. ✓
    """
    eitc = load_tax_constants(year)["credits"]["eitc"]
    for n in CHILD_COUNTS:
        row = eitc[n]
        computed = row["earned_income_amount"] * row["phase_in_rate"]
        assert abs(computed - row["max_credit"]) <= 1, (
            f"{year} {n} children: {row['earned_income_amount']} x "
            f"{row['phase_in_rate']} = {computed}, published max credit "
            f"{row['max_credit']}"
        )


@pytest.mark.parametrize("year", SUPPORTED_YEARS)
@pytest.mark.parametrize("suffix", ["mfj", "other"])
def test_eitc_completed_phaseout_matches_phase_out_rate(year, suffix):
    """F6: completed_phaseout = threshold + max_credit / phase_out_rate.

    Confirms the IRC 32(b)(1) phase-out percentages (7.65 / 15.98 / 21.06 /
    21.06) against the published threshold and completed phaseout amounts.
    Example (2023, one child, MFJ):
        28,120 + (11,750 x 0.34)/0.1598 = 28,120 + 25,000 = 53,120 ✓

    Tolerance is $15 rather than exact: IRC 32(j) rounds each published amount
    (earned income amount, threshold, completed phaseout) to the nearest $10
    independently, so recomputing one from the others cannot reproduce it to
    the dollar. Observed residuals across 2023-2026 are under $7.

    The tolerance is still far tighter than any rate error. Applying the
    one-child 15.98% rate to a zero-child filer, for instance, would land
    ~$44,000 away — which is exactly the F5 defect this guards against.
    """
    eitc = load_tax_constants(year)["credits"]["eitc"]
    for n in CHILD_COUNTS:
        row = eitc[n]
        threshold = row[f"threshold_phaseout_{suffix}"]
        completed = row[f"completed_phaseout_{suffix}"]
        unrounded_credit = row["earned_income_amount"] * row["phase_in_rate"]
        computed = threshold + unrounded_credit / row["phase_out_rate"]
        assert abs(computed - completed) <= 15, (
            f"{year} {n} children ({suffix}): {threshold} + "
            f"({row['earned_income_amount']} x {row['phase_in_rate']})/"
            f"{row['phase_out_rate']} = {computed:.1f}, "
            f"published completed phaseout {completed}"
        )


def test_eitc_phase_in_rates_differ_by_child_count():
    """F5: a single 34% rate for every child count overstated the 0-child credit.

    The 0-child phase-in rate is 7.65%, not 34% — using 34% overstates that
    filer's credit by roughly 4.4x, which is what the audit found.
    """
    eitc = load_tax_constants("2025")["credits"]["eitc"]
    assert eitc["0"]["phase_in_rate"] == 0.0765
    assert eitc["1"]["phase_in_rate"] == 0.34
    assert eitc["2"]["phase_in_rate"] == 0.40
    assert eitc["3"]["phase_in_rate"] == 0.45

    assert eitc["0"]["phase_out_rate"] == 0.0765
    assert eitc["1"]["phase_out_rate"] == 0.1598
    assert eitc["2"]["phase_out_rate"] == 0.2106
    assert eitc["3"]["phase_out_rate"] == 0.2106


@pytest.mark.parametrize("year,limit", [
    ("2023", 11000), ("2024", 11600), ("2025", 11950), ("2026", 12200),
])
def test_eitc_investment_income_limit_per_year(year, limit):
    """F6: the investment-income limit is year-specific (was 2024-only)."""
    eitc = load_tax_constants(year)["credits"]["eitc"]
    assert eitc["investment_income_limit"] == limit


# ---------------------------------------------------------------------------
# F8 — AMT
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("year,exemption_mfj,exemption_single,start_mfj,rate", [
    # Rev. Proc. 2022-38 sec .11 / 2024-40 sec .11 / 2025-32 sec .10
    ("2023", 126500, 81300, 1156300, 0.25),
    ("2024", 133300, 85700, 1218700, 0.25),
    ("2025", 137000, 88100, 1252700, 0.25),
    # OBBBA resets the 2026 phase-out to $1M/$500K with a 50% rate.
    ("2026", 140200, 90100, 1000000, 0.50),
])
def test_amt_exemptions_and_phaseout(year, exemption_mfj, exemption_single, start_mfj, rate):
    """F8: AMT exemption/phase-out data is present and year-correct."""
    amt = load_tax_constants(year)["amt"]
    assert amt["exemption_mfj"] == exemption_mfj
    assert amt["exemption_single"] == exemption_single
    assert amt["phaseout_start_mfj"] == start_mfj
    assert amt["phaseout_rate"] == rate


@pytest.mark.parametrize("year,complete_phaseout_mfj", [
    ("2023", 1662300),
    ("2024", 1751900),
    ("2025", 1800700),
    ("2026", 1280400),
])
def test_amt_phaseout_rate_reproduces_published_complete_phaseout(year, complete_phaseout_mfj):
    """F8: threshold + exemption/rate must equal the published complete phaseout.

    This verifies the stored phase-out RATE without quoting it from memory:
      2025 MFJ: 1,252,700 + 137,000/0.25 = 1,800,700 ✓
      2026 MFJ: 1,000,000 + 140,200/0.50 = 1,280,400 ✓ (OBBBA 50% rate)
    A wrong rate would miss the published figure by hundreds of thousands.
    """
    amt = load_tax_constants(year)["amt"]
    computed = amt["phaseout_start_mfj"] + amt["exemption_mfj"] / amt["phaseout_rate"]
    assert computed == pytest.approx(complete_phaseout_mfj, abs=1)


def test_amt_2026_phaseout_is_the_obbba_reset():
    """F8: 2026 must not inherit the 2025 phase-out thresholds."""
    amt_2025 = load_tax_constants("2025")["amt"]
    amt_2026 = load_tax_constants("2026")["amt"]
    assert amt_2026["phaseout_start_mfj"] == 1000000 < amt_2025["phaseout_start_mfj"]
    assert amt_2026["phaseout_start_single"] == 500000 < amt_2025["phaseout_start_single"]
    assert amt_2026["phaseout_rate"] == 0.50


# ---------------------------------------------------------------------------
# F7 — OBBBA deductions
# ---------------------------------------------------------------------------

def test_obbba_deductions_present_for_2025():
    """F7: senior / tips / overtime / car-loan deductions exist with limits.

    All figures from Schedule 1-A (Form 1040) 2025.
    """
    o = load_tax_constants("2025")["obbba_deductions"]

    assert o["senior_bonus"]["amount"] == 6000
    assert o["senior_bonus"]["magi_phaseout_start_single"] == 75000
    assert o["senior_bonus"]["magi_phaseout_start_mfj"] == 150000
    assert o["senior_bonus"]["phaseout_rate"] == 0.06
    assert o["senior_bonus"]["effective_years"] == ["2025", "2026", "2027", "2028"]

    assert o["tips"]["max"] == 25000
    assert o["tips"]["magi_phaseout_start_single"] == 150000
    assert o["tips"]["magi_phaseout_start_mfj"] == 300000

    assert o["overtime"]["max_single"] == 12500
    assert o["overtime"]["max_mfj"] == 25000

    assert o["car_loan_interest"]["max"] == 10000
    assert o["car_loan_interest"]["magi_phaseout_start_single"] == 100000


def test_obbba_phaseout_rounding_direction_differs_by_deduction():
    """F7: Schedule 1-A rounds tips/overtime DOWN but car-loan interest UP.

    Lines 11/19 say "decrease the result to the next lower whole number";
    line 28 says "increase the result to the next higher whole number". Getting
    this backwards changes the deduction by one $100/$200 step.
    """
    o = load_tax_constants("2025")["obbba_deductions"]
    assert o["tips"]["phaseout_rounding"] == "down"
    assert o["overtime"]["phaseout_rounding"] == "down"
    assert o["car_loan_interest"]["phaseout_rounding"] == "up"
    assert o["tips"]["phaseout_per_1000"] == 100
    assert o["car_loan_interest"]["phaseout_per_1000"] == 200


# ---------------------------------------------------------------------------
# Y5 — due dates live in data, not code
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("year", SUPPORTED_YEARS)
def test_estimated_tax_due_dates_present_and_year_correct(year):
    """Y5: each year carries its own four due dates; none fall back to 2025."""
    dates = load_tax_constants(year)["estimated_tax_due_dates"]
    assert len(dates) == 4
    quarters = [d[0] for d in dates]
    assert quarters == ["Q1", "Q2", "Q3", "Q4"]
    # Q1-Q3 fall in the tax year; Q4 falls in January of the next year.
    for q, date_str, _ in dates[:3]:
        assert date_str.startswith(year), f"{year} {q} due date is {date_str}"
    assert dates[3][1].startswith(str(int(year) + 1))


# ---------------------------------------------------------------------------
# Ground rule 1 — unverified 2026 figures raise instead of guessing
# ---------------------------------------------------------------------------

def test_2026_salt_cap_resolved_from_statute():
    """E1 (plan 2026-07-30-E1): the 2026 SALT cap is now resolved from the
    enrolled statute text — IRC 164(b)(7)(A)(ii): "in the case of any taxable
    year beginning in calendar year 2026, $40,400". Previously null because
    Rev. Proc. 2025-32 does not cover SALT and secondary sources conflicted;
    the statute (Cornell LII current-law text) settles it. See
    _sources.obbba_salt_2026 for the verbatim quote.

    Reading it no longer raises; it returns the statute-confirmed figure.
    """
    salt_2026 = load_tax_constants("2026")["salt"]
    assert get_constant(salt_2026, "cap") == 40400


def test_2026_ss_wage_base_is_verified():
    """The SSA wage base for 2026 is $184,500, per the SSA COLA announcement.

    Originally left null/TODO_VERIFY because Rev. Proc. 2025-32 does not cover
    it (it's an SSA figure, not an IRS one). Direct SSA.gov fetch was blocked
    (403), so this is corroborated by multiple independent secondary reports
    of the same SSA press release rather than a direct primary quote —
    documented as such in _sources.ssa_2026_wage_base.
    """
    se = load_tax_constants("2026")["se_tax"]
    assert get_constant(se, "ss_wage_base") == 184500


def test_2026_salt_mfs_and_phasedown_conflicts_resolved_by_statute():
    """E1 (plan 2026-07-30-E1): the two points secondary sources conflicted on
    are settled by the enrolled statute text (IRC 164(b)(6)/(b)(7), per
    _sources.obbba_salt_2026):

      * MFS cap ESCALATES — (b)(6)(B) "half the applicable limitation amount"
        -> 2026 MFS cap = $20,200, NOT the frozen $20,000 one source claimed.
      * 2026 phase-down start is $505,000 — (b)(7)(B)(ii)(II) "in the case of
        any taxable year beginning in calendar year 2026, $505,000" (101% of
        the 2025 $500,000 threshold), NOT the $500,500 another source gave.
      * MFS phase-down start is half the threshold -> $252,500.
    """
    salt_2026 = load_tax_constants("2026")["salt"]
    assert get_constant(salt_2026, "cap_mfs") == 20200
    assert get_constant(salt_2026, "phasedown_magi_start") == 505000
    assert get_constant(salt_2026, "phasedown_magi_start_mfs") == 252500


def test_verified_2026_figures_are_readable():
    """Data that WAS verified is usable — nulls are targeted, not blanket."""
    data = load_tax_constants("2026")
    assert get_constant(data, "standard_deductions", "married_jointly") == 32200
    assert get_constant(data, "ctc", "per_child") == 2200
    assert get_constant(data, "amt", "exemption_mfj") == 140200
    assert get_constant(data, "se_tax", "ss_wage_base") == 184500
    assert get_constant(data, "mileage_rate") == 0.725
    assert get_constant(data, "retirement_limits", "401k", "limit") == 24500


# ---------------------------------------------------------------------------
# 2023/2026 retirement limits, mileage rates, and Saver's Credit bands
# (added after the initial Phase 2 pass, on explicit request)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("year,limit,catchup,total_415c", [
    ("2023", 22500, 7500, 66000),
    ("2024", 23000, 7500, None),   # 2024 total_415c was not separately verified
    ("2025", 23500, 7500, 70000),
    ("2026", 24500, 8000, 72000),
])
def test_401k_limits_by_year(year, limit, catchup, total_415c):
    """401(k)/403(b)/457/TSP elective deferral limit and catch-up, per year.

    Sources: Notice 2022-55 (2023), Notice 2024-80 (2025 super catch-up/415(c)),
    Notice 2025-67 (2026).
    """
    r = load_tax_constants(year)["retirement_limits"]["401k"]
    assert r["limit"] == limit
    assert r["catchup_50_plus"] == catchup
    if total_415c is not None:
        assert r["total_415c"] == total_415c


def test_super_catchup_60_63_did_not_exist_before_2025():
    """SECURE 2.0's age-60-63 catch-up (IRC 414(v)(2)(E)) applies only to tax
    years beginning after 2024. For 2023 the null is a definitive 'not
    applicable', not a research gap — distinct from the 2026 SALT cap case,
    which genuinely could not be resolved.
    """
    from utils import UnverifiedConstantError

    r2023 = load_tax_constants("2023")["retirement_limits"]["401k"]
    assert r2023["super_catchup_60_63"] is None
    with pytest.raises(UnverifiedConstantError):
        get_constant(r2023, "super_catchup_60_63")


@pytest.mark.parametrize("year,value", [("2025", 11250), ("2026", 11250)])
def test_super_catchup_60_63_verified_for_2025_and_2026(year, value):
    """Confirmed directly from the statutory text of Notice 2024-80/2025-67:
    the age-60-63 catch-up is $11,250 for both years (it does not increase
    from 2025 to 2026, unlike the base and 50+ catch-up amounts)."""
    r = load_tax_constants(year)["retirement_limits"]["401k"]
    assert r["super_catchup_60_63"] == value


@pytest.mark.parametrize("year,single,family", [
    ("2023", 3850, 7750),
    ("2024", 4150, 8300),
    ("2025", 4300, 8550),
    ("2026", 4400, 8750),
])
def test_hsa_limits_by_year(year, single, family):
    """HSA self-only/family contribution limits, Rev. Proc. 2022-24 (2023) and
    Rev. Proc. 2025-19 (2026)."""
    r = load_tax_constants(year)["retirement_limits"]["hsa"]
    assert r["single"] == single
    assert r["family"] == family


@pytest.mark.parametrize("year,limit,catchup", [
    ("2023", 6500, 1000),
    ("2026", 7500, 1100),
])
def test_ira_limits_by_year(year, limit, catchup):
    r = load_tax_constants(year)["retirement_limits"]["ira"]
    assert r["limit"] == limit
    assert r["catchup_50_plus"] == catchup


@pytest.mark.parametrize("year,rate", [("2023", 0.655), ("2026", 0.725)])
def test_mileage_rate_by_year(year, rate):
    """Notice 2023-03 (2023) and Notice 2026-10 (2026) business mileage rates.

    The 2026 figure is a flat rate for the whole year — a secondary-source
    summary claimed a mid-year revision to 76 cents effective 2026-07-01, but
    the primary notice text contains no such change, so it was not used.
    """
    assert load_tax_constants(year)["mileage_rate"] == rate


def test_2026_educator_expense_limit():
    """Rev. Proc. 2025-32 sec .12: $350 for 2026 (up from $300)."""
    assert load_tax_constants("2026")["educator_expense_limit"] == 350


@pytest.mark.parametrize("year,mfj,single", [
    ("2023", (43500, 47500, 73000), (21750, 23750, 36500)),
    ("2026", (48500, 52500, 80500), (24250, 26250, 40250)),
])
def test_savers_credit_bands_by_year(year, mfj, single):
    """Saver's Credit (IRC 25B) AGI bands, Notice 2022-55 / Notice 2025-67."""
    sc = load_tax_constants(year)["credits"]["savers_credit"]
    assert (sc["mfj"]["50_percent"], sc["mfj"]["20_percent"], sc["mfj"]["10_percent"]) == mfj
    assert (sc["single"]["50_percent"], sc["single"]["20_percent"], sc["single"]["10_percent"]) == single


# ---------------------------------------------------------------------------
# QBI (§199A) — thresholds, phase-in ranges, statutory factors, 2026 minimum
# (QBI engagement; sources: Rev. Proc. 2022-38 sec .27 / 2023-34 sec .27 /
#  2024-40 sec .27 / 2025-32 sec .26; 26 USC 199A; Form 8995-A instructions)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("year,t_single,t_mfs,t_mfj,r_single,r_mfj", [
    # Rev. Proc. 2022-38 sec .27 (2023); 2023-34 sec .27 (2024);
    # 2024-40 sec .27 (2025); 2025-32 sec .26 (2026, post-OBBBA).
    ("2023", 182100, 182100, 364200, 50000, 100000),
    ("2024", 191950, 191950, 383900, 50000, 100000),
    ("2025", 197300, 197300, 394600, 50000, 100000),
    # OBBBA (P.L. 119-21 §70105) widened the 2026 phase-in ranges to
    # 75,000 / 150,000 and split MFS off from single for 2026.
    ("2026", 201750, 201775, 403500, 75000, 150000),
])
def test_qbi_thresholds_and_ranges_by_year(year, t_single, t_mfs, t_mfj,
                                           r_single, r_mfj):
    """§199A(e)(2) thresholds and §199A(b)(3)(B) phase-in ranges per year,
    transcribed from each year's IRS Rev. Proc. table (MFJ / MFS / All Other).

    Rev. Proc. 2025-32 sec .26 lists 2026 MFS at $201,775 and 'All Other Returns'
    (single/HoH/QSS) at $201,750 — MFS no longer equals single from 2026 on.
    """
    qbi = load_tax_constants(year)["qbi"]
    assert qbi["threshold_single"] == t_single
    assert qbi["threshold_mfs"] == t_mfs
    assert qbi["threshold_mfj"] == t_mfj
    assert qbi["phase_in_range_single"] == r_single
    assert qbi["phase_in_range_mfj"] == r_mfj


def test_qbi_2026_mfs_threshold_differs_from_single():
    """Regression guard for the transposed-digit error found during verification.

    The constants file originally carried 2026 threshold_single = 201775, which
    is the MFS figure, not the single figure ($201,750). OBBBA split MFS from
    single for 2026; for 2023-2025 MFS equalled single (verified per Rev. Proc.).
    """
    q2026 = load_tax_constants("2026")["qbi"]
    assert q2026["threshold_single"] == 201750
    assert q2026["threshold_mfs"] == 201775
    assert q2026["threshold_mfs"] != q2026["threshold_single"]
    for y in ("2023", "2024", "2025"):
        q = load_tax_constants(y)["qbi"]
        assert q["threshold_mfs"] == q["threshold_single"], (
            f"{y}: pre-OBBBA, MFS must equal single")


@pytest.mark.parametrize("year", SUPPORTED_YEARS)
def test_qbi_statutory_factors_constant_across_years(year):
    """The §199A rate and W-2 wage/UBIA factors are statutory, not indexed, so
    identical for every supported year. 26 USC 199A(a)/(b)(1)/(b)(2)(B);
    Form 8995-A Part II lines 5-10 (50% wages; 25% wages + 2.5% UBIA)."""
    qbi = load_tax_constants(year)["qbi"]
    assert qbi["rate"] == 0.20
    assert qbi["reit_ptp_rate"] == 0.20
    assert qbi["wage_limit_pct"] == 0.50
    assert qbi["wage_ubia_pct"] == 0.25
    assert qbi["ubia_pct"] == 0.025


def test_qbi_2026_minimum_deduction_verified_and_pre2026_absent():
    """26 USC 199A(i) + Rev. Proc. 2025-32 sec .12: a $400 minimum deduction
    with a $1,000 active-QBI floor, effective for tax years beginning after
    12/31/2025. The $400 is the GREATER of the normal deduction or $400 and
    overrides the §199A(a)(2) overall taxable-income limit. For 2023-2025 the
    keys are absent (pre-OBBBA), so reading one without a default raises
    KeyError — a definitive 'not applicable', never a silent null."""
    q2026 = load_tax_constants("2026")["qbi"]
    assert get_constant(q2026, "min_deduction") == 400
    assert get_constant(q2026, "min_deduction_qbi_floor") == 1000
    for y in ("2023", "2024", "2025"):
        q = load_tax_constants(y)["qbi"]
        with pytest.raises(KeyError):
            get_constant(q, "min_deduction")
