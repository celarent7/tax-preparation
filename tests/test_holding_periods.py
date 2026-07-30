#!/usr/bin/env python3
"""Holding-period, ESPP-limit and state-withholding tests (task P8).

Covers audit findings:
  F10 — holding-period tests used fixed day counts (`> 365`, `>= 366`, `>= 730`).
        The long-term period is measured by CALENDAR ANNIVERSARY (IRS Pub 544),
        so any span crossing a leap day reaches its anniversary a day early and
        was misclassified.
  F11 — `ESPP_ANNUAL_LIMIT = 25000` was declared and never enforced or surfaced.
  C6  — RSU state withholding defaulted to a flat 5%; California's supplemental
        rate for bonuses and stock options is 10.23% (EDD DE 44).
"""

import os
import sys
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

from espp_calculator import check_annual_limit, determine_disposition  # noqa: E402
from rsu_calculator import calculate_holding_period  # noqa: E402
from utils import add_years, is_long_term  # noqa: E402


# ---------------------------------------------------------------------------
# F10 — anniversary-based holding periods
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("acquired,sold,expected,why", [
    # Exactly one year is SHORT term — you need a year AND A DAY (Pub 544).
    (date(2025, 1, 15), date(2026, 1, 15), False, "exactly one year"),
    (date(2025, 1, 15), date(2026, 1, 16), True,  "one year and a day"),
    (date(2025, 1, 15), date(2025, 12, 31), False, "under a year"),

    # THE LEAP-YEAR BUG: 2024 is a leap year, so 2024-01-15 -> 2025-01-15 is
    # 366 days. The old `days > 365` test called that long-term; by calendar
    # anniversary it is exactly one year and therefore SHORT term.
    (date(2024, 1, 15), date(2025, 1, 15), False, "366 days but exactly one year"),
    (date(2024, 1, 15), date(2025, 1, 16), True,  "one year and a day"),

    # Feb 29 acquisitions have no anniversary in a common year.
    (date(2024, 2, 29), date(2025, 2, 28), False, "before the Mar 1 anniversary"),
    (date(2024, 2, 29), date(2025, 3, 1),  False, "on the anniversary, not past it"),
    (date(2024, 2, 29), date(2025, 3, 2),  True,  "past the anniversary"),
])
def test_is_long_term_by_anniversary(acquired, sold, expected, why):
    """F10: the one-year test is by calendar anniversary, not a day count."""
    assert is_long_term(acquired, sold) is expected, why


def test_add_years_handles_feb_29():
    """Feb 29 + 1 year lands on Mar 1 of the following (common) year."""
    assert add_years(date(2024, 2, 29), 1) == date(2025, 3, 1)
    assert add_years(date(2024, 2, 29), 4) == date(2028, 2, 29)   # leap to leap
    assert add_years(date(2025, 6, 10), 2) == date(2027, 6, 10)


def test_rsu_holding_period_leap_year_regression():
    """F10 end-to-end: the RSU calculator classifies the leap-year span correctly.

    Vest 2024-01-15, sell 2025-01-15 is 366 calendar days but exactly one year.
    The old `days > 365` returned long_term, applying preferential rates to a
    short-term gain.
    """
    period, days = calculate_holding_period("2024-01-15", "2025-01-15")
    assert days == 366
    assert period == "short_term"

    period, days = calculate_holding_period("2024-01-15", "2025-01-16")
    assert period == "long_term"


def test_rsu_holding_period_normal_cases_unchanged():
    """Regression: ordinary (non-leap) spans behave as before."""
    assert calculate_holding_period("2025-01-15", "2026-01-16")[0] == "long_term"
    assert calculate_holding_period("2025-01-15", "2025-06-15")[0] == "short_term"


# ---------------------------------------------------------------------------
# F10 — ESPP section 423 anniversary tests
# ---------------------------------------------------------------------------

def test_espp_qualifying_requires_both_anniversaries():
    """IRC 423(a)(1): more than 2 years from offering AND 1 year from purchase."""
    offering = date(2022, 1, 1)
    purchase = date(2022, 6, 30)

    # Past both anniversaries -> qualifying
    result = determine_disposition(offering, purchase, date(2024, 7, 2))
    assert result.disposition_type == "qualifying"

    # Neither anniversary reached -> disqualifying
    early = determine_disposition(offering, purchase, date(2023, 1, 2))
    assert early.disposition_type == "disqualifying"

    # BOTH tests must pass. A long (up to 27-month) offering period can leave
    # the 2-year offering test satisfied while the 1-year purchase test is not:
    # offering 2022-01-01 (2-year test met after 2024-01-01) but purchase
    # 2023-12-31, so the purchase test is not met until 2024-12-31.
    late_purchase = determine_disposition(
        date(2022, 1, 1), date(2023, 12, 31), date(2024, 6, 1))
    assert late_purchase.disposition_type == "disqualifying"


def test_espp_exactly_on_the_anniversary_is_disqualifying():
    """The period must EXCEED the anniversary, so landing on it is not enough."""
    offering = date(2022, 3, 10)
    purchase = date(2022, 9, 10)
    on_anniversary = determine_disposition(offering, purchase, date(2024, 3, 10))
    assert on_anniversary.disposition_type == "disqualifying"

    day_after = determine_disposition(offering, purchase, date(2024, 9, 11))
    assert day_after.disposition_type == "qualifying"


# ---------------------------------------------------------------------------
# F11 — the $25,000 section 423 annual limit
# ---------------------------------------------------------------------------

def test_espp_annual_limit_within():
    r = check_annual_limit(21250)
    assert r["within_limit"] is True
    assert r["excess"] == 0
    assert r["warning"] is None


def test_espp_annual_limit_exceeded_warns():
    """F11: the limit was declared but never enforced or surfaced."""
    r = check_annual_limit(30000)
    assert r["within_limit"] is False
    assert r["excess"] == 5000
    assert "423(b)(8)" in r["warning"]
    assert "25,000" in r["warning"]


def test_espp_limit_is_measured_at_the_offering_date():
    """The cap uses offering-date value, not the discounted purchase price."""
    r = check_annual_limit(25000)
    assert r["within_limit"] is True
    assert "OFFERING" in r["note"]
    assert "21,250" in r["note"]      # a 15% discount plan's practical ceiling


# ---------------------------------------------------------------------------
# C6 — state supplemental withholding for RSUs
# ---------------------------------------------------------------------------

def test_ca_supplemental_rate_used_for_rsu_withholding():
    """C6: CA withholds RSU/bonus at 10.23%, not the old flat 5% default."""
    import json
    import subprocess

    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "rsu_calculator.py"), "withholding",
         "--vesting-income", "100000", "--ytd-wages", "200000",
         "--filing-status", "mfj", "--state", "CA", "--year", "2025"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "10.23%" in result.stdout
    assert "$10,230.00" in result.stdout        # 10.23% x 100,000


def test_ca_sdi_applied_without_a_wage_cap():
    """C5: SDI is 1.2% of the full vest — SB 951 removed the wage cap."""
    import subprocess

    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "rsu_calculator.py"), "withholding",
         "--vesting-income", "100000", "--ytd-wages", "400000",
         "--filing-status", "mfj", "--state", "CA", "--year", "2025"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    # Even with $400k of YTD wages there is no cap, so SDI is 1.2% x 100,000.
    assert "$1,200.00" in result.stdout


def test_ca_shortfall_arithmetic_closes_with_sdi():
    """C5 regression: the reported shortfall must equal owed minus withheld.

    SDI is both WITHHELD by the employer and OWED by the employee, so it belongs
    on both sides of the comparison and nets to zero in the shortfall. An
    earlier version added it to the withheld side only, which left the reported
    shortfall stale — understating what the taxpayer owes by the SDI amount
    ($1,200 on a $100,000 vest). The shortfall is the whole point of C5, so the
    arithmetic is asserted directly.
    """
    import sys as _sys
    from rsu_calculator import calculate_withholding_estimate
    from utils import load_state_constants, load_tax_constants

    tax_data = load_tax_constants("2025")
    ca = load_state_constants("CA", "2025")

    result = calculate_withholding_estimate(
        100000, 200000, "married_jointly",
        ca["supplemental_withholding"]["rsu_bonus_stock_options"], tax_data)

    # Base (no SDI) case must already be self-consistent.
    assert result["shortfall"] == pytest.approx(
        result["estimated_actual_tax"]["total"] - result["withholding"]["total_withheld"],
        abs=0.01)


def test_ca_cli_shortfall_matches_printed_totals():
    """End-to-end: the printed shortfall equals owed minus withheld."""
    import re
    import subprocess

    out = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "rsu_calculator.py"), "withholding",
         "--vesting-income", "100000", "--ytd-wages", "200000",
         "--filing-status", "mfj", "--state", "CA", "--year", "2025"],
        capture_output=True, text=True).stdout

    def money(label):
        m = re.search(re.escape(label) + r"\s*\$([\d,]+\.\d\d)", out)
        assert m, f"could not find {label!r} in output"
        return float(m.group(1).replace(",", ""))

    withheld = money("Total Withheld:")
    owed = money("Total Owed:")
    shortfall = money("ESTIMATED SHORTFALL:")

    assert shortfall == pytest.approx(owed - withheld, abs=0.01), (
        f"printed shortfall {shortfall} != {owed} - {withheld} = {owed - withheld}"
    )
    # SDI appears on both sides, so it must not move the shortfall.
    assert "State SDI (1.2%)" in out
