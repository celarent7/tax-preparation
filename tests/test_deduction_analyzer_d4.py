#!/usr/bin/env python3
"""Tests for deduction_analyzer refinements (audit finding D4).

D4: mortgage interest had no pre-12/16/2017 $1M grandfathering and no
average-balance method; charitable had no 30%-limit-organization handling; and
the medical threshold was hard-coded inline as 0.075 instead of being read from
the constants file.

Sources:
  IRC 163(h)(3)(F) / IRS Pub 936 — debt incurred on or before 2017-12-15 keeps
    the $1,000,000 acquisition-debt limit ($500,000 MFS); later debt is limited
    to $750,000 ($375,000 MFS). Pub 936 also allows the average-balance method.
  IRC 170(b) — 60% AGI ceiling for cash to public charities, 30% for
    appreciated capital-gain property; 30%/20% for 30%-limit organizations.
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

from deduction_analyzer import (  # noqa: E402
    analyze_charitable,
    analyze_medical_deduction,
    analyze_mortgage_interest,
    mortgage_debt_limit_for,
)
from utils import load_tax_constants  # noqa: E402


# ---------------------------------------------------------------------------
# Mortgage grandfathering
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,date,expected_limit,grandfathered", [
    ("single",             "2015-06-01", 1000000, True),
    ("married_jointly",    "2017-12-15", 1000000, True),   # on the cutoff date
    ("married_jointly",    "2017-12-16",  750000, False),  # one day later
    ("single",             "2020-01-01",  750000, False),
    ("married_separately", "2015-06-01",  500000, True),
    ("married_separately", "2020-01-01",  375000, False),
    ("single",             None,          750000, False),  # unknown -> post-TCJA
])
def test_debt_limit_grandfathering(status, date, expected_limit, grandfathered):
    """D4: pre-TCJA debt keeps the $1M limit; the cutoff is inclusive."""
    limit, is_grandfathered = mortgage_debt_limit_for(status, date, 750000, 375000)
    assert limit == expected_limit
    assert is_grandfathered is grandfathered


def test_grandfathered_mortgage_deducts_more_interest():
    """A $900,000 pre-2017 mortgage is fully deductible; a new one is not.

    Post-TCJA: limit 750,000 / balance 900,000 = 0.8333 ratio.
      36,000 x 0.8333... = $30,000.00
    Grandfathered: 900,000 <= 1,000,000, so all $36,000 is deductible.
    The old code always applied the $750,000 limit, costing this filer $6,000
    of deduction.
    """
    new = analyze_mortgage_interest(36000, 900000, "married_jointly",
                                    origination_date="2020-01-01")
    old = analyze_mortgage_interest(36000, 900000, "married_jointly",
                                    origination_date="2016-01-01")

    assert new["deductible_amount"] == 30000.00
    assert new["limited_by_debt_cap"] is True
    assert old["deductible_amount"] == 36000.00
    assert old["limited_by_debt_cap"] is False
    assert old["grandfathered_pre_tcja"] is True


def test_average_balance_method_is_more_favorable():
    """D4: Pub 936 allows applying the limit against the average balance.

    Original balance 900,000, average balance 800,000, interest 36,000.
      by original balance: 750,000/900,000 x 36,000 = $30,000.00
      by average balance:  750,000/800,000 x 36,000 = $33,750.00
    """
    by_original = analyze_mortgage_interest(36000, 900000, "single",
                                            origination_date="2020-01-01")
    by_average = analyze_mortgage_interest(36000, 900000, "single",
                                           origination_date="2020-01-01",
                                           average_balance=800000)

    assert by_original["deductible_amount"] == 30000.00
    assert by_average["deductible_amount"] == 33750.00
    assert by_average["balance_used_for_limit"] == 800000


# ---------------------------------------------------------------------------
# Charitable 30%-limit organizations
# ---------------------------------------------------------------------------

def test_public_charity_limits_unchanged():
    """Regression: the existing 60%/30% public-charity behavior is preserved.

    AGI 100,000 -> cash ceiling 60,000, property ceiling 30,000.
    """
    r = analyze_charitable(70000, 40000, 100000)
    assert r["cash_limit"] == 60000
    assert r["property_limit"] == 30000
    assert r["deductible_amount"] == 90000        # 60,000 + 30,000
    assert r["carryover_potential"] == 20000      # 10,000 + 10,000


def test_thirty_percent_organization_limits():
    """D4: gifts to private foundations use 30% (cash) and 20% (property).

    AGI 100,000, $40,000 cash and $30,000 appreciated property to a private
    foundation:
      cash:     min(40,000, 30% x 100,000 = 30,000) = 30,000 (10,000 carryover)
      property: min(30,000, 20% x 100,000 = 20,000) = 20,000 (10,000 carryover)
    """
    r = analyze_charitable(0, 0, 100000,
                           cash_to_30_percent_orgs=40000,
                           property_to_30_percent_orgs=30000)
    assert r["cash_30_percent_org_limit"] == 30000
    assert r["property_30_percent_org_limit"] == 20000
    assert r["deductible_amount"] == 50000
    assert r["carryover_potential"] == 20000


def test_charitable_rates_come_from_constants():
    """Rule 5: the AGI ceilings are read from the constants, not hard-coded."""
    data = load_tax_constants("2025")
    r = analyze_charitable(100000, 0, 100000,
                           cash_limit_rate=data["credits"]["charitable_cash_limit"])
    assert r["cash_limit"] == 100000 * data["credits"]["charitable_cash_limit"]


# ---------------------------------------------------------------------------
# Medical threshold from constants
# ---------------------------------------------------------------------------

def test_medical_threshold_read_from_constants():
    """D4: the 7.5% floor was inline; it now comes from the constants file."""
    rate = load_tax_constants("2025")["credits"]["medical_threshold"]
    r = analyze_medical_deduction(20000, 100000, rate)

    assert r["threshold_rate"] == rate
    assert r["agi_threshold"] == 7500.00          # 7.5% x 100,000
    assert r["deductible_amount"] == 12500.00     # 20,000 - 7,500


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def test_cli_exposes_origination_date():
    def run(*extra):
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "deduction_analyzer.py"),
             "--agi", "300000", "--filing-status", "mfj", "--year", "2025",
             "--mortgage-interest", "36000", "--acquisition-debt", "900000",
             "--output-format", "json", *extra],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)["itemized_breakdown"]["mortgage"]

    assert run()["deductible_amount"] == 30000.00
    assert run("--loan-origination-date", "2016-01-01")["deductible_amount"] == 36000.00
