"""C2: CA 540-ES installment schedule (30/40/0/30, $1M rule, 110% rule).

The required annual payment is the lesser of 90% of the current-year CA tax
and the prior-year CA tax (110% when CA AGI exceeds the high-income threshold)
— EXCEPT that filers with CA AGI at or above $1,000,000 ($500,000 MFS) may not
use the prior-year leg at all (R&TC 19136.3 / FTB 540-ES instructions).
Installments are NOT even quarters: 30% / 40% / 0% / 30%.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from estimated_tax_calculator import calculate_ca_estimated_tax
from utils import load_state_constants


@pytest.fixture(scope="module")
def est():
    return load_state_constants("CA", "2025")["estimated_tax"]


def test_installments_split_30_40_0_30(est):
    r = calculate_ca_estimated_tax(20000, 30000, 200000, "single", est)
    # current-year leg 90% x 20,000 = 18,000; prior-year leg 110% x 30,000
    # (AGI > 150k) = 33,000 -> lesser is 18,000
    assert r["required_annual_payment"] == 18000
    assert r["basis"] == "90% current-year"
    amounts = [i["amount"] for i in r["installments"]]
    assert amounts == [5400.0, 7200.0, 0.0, 5400.0]
    assert sum(amounts) == r["required_annual_payment"]
    assert [i["due_date"] for i in r["installments"]] == [
        d for _, d in est["due_dates"]]


def test_prior_year_leg_wins_when_lower(est):
    r = calculate_ca_estimated_tax(50000, 30000, 120000, "single", est)
    # AGI <= 150k: prior-year leg 100% x 30,000 = 30,000 < 45,000
    assert r["required_annual_payment"] == 30000
    assert r["basis"] == "prior-year safe harbor"
    assert r["prior_year_leg"] == 30000


def test_high_income_110pct(est):
    r = calculate_ca_estimated_tax(50000, 30000, 200000, "single", est)
    assert r["prior_year_leg"] == 33000  # 110%


def test_mfs_high_income_threshold_is_half(est):
    r = calculate_ca_estimated_tax(50000, 30000, 100000, "mfs", est)
    assert r["prior_year_leg"] == 33000  # 100k > 75k MFS threshold


def test_1m_agi_kills_prior_year_safe_harbor(est):
    r = calculate_ca_estimated_tax(150000, 30000, 1200000, "single", est)
    # CA R&TC 19136.3: AGI >= $1M -> must pay 90% of CURRENT year, period.
    assert not r["safe_harbor_available"]
    assert r["required_annual_payment"] == 135000
    assert r["basis"] == "90% current-year"


def test_withholding_reduces_remaining(est):
    r = calculate_ca_estimated_tax(20000, 30000, 200000, "single", est,
                                   ca_withholding=10000)
    assert r["remaining_after_withholding"] == 8000