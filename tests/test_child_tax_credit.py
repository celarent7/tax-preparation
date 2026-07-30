#!/usr/bin/env python3
"""Tests for the Child Tax Credit computation.

Covers audit finding F4:
  1. The credit was hard-coded at $2,000/child. OBBBA sets it to $2,200 for any
     taxable year beginning in 2025.
  2. The phase-out used `excess // 1000` (floor). IRC 24(b) and the Schedule 8812
     worksheet round the excess UP to the next $1,000, so the old code
     understated the phase-out by up to $50 per return.

Primary sources (retrieved 2026-07-29):
  * IRS, "Child Tax Credit", https://www.irs.gov/credits-deductions/individuals/child-tax-credit
    — $2,200 per qualifying child for 2025; up to $1,700 refundable via ACTC.
  * IRS, Instructions for Schedule 8812 (Form 1040) (2025),
    https://www.irs.gov/instructions/i1040s8 — modified AGI thresholds:
    "Married filing jointly - $400,000 / All other filing statuses - $200,000".
  * IRS, Schedule 8812 (Form 1040) 2025, https://www.irs.gov/pub/irs-pdf/f1040s8.pdf
    line 10: "If more than zero and not a multiple of $1,000, enter the next
    multiple of $1,000. For example, if the result is $425, enter $1,000; if the
    result is $1,025, enter $2,000, etc." Line 11 multiplies by 5% (= $50 per $1,000).

Every expected value below is hand-computed from those rules.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from credit_eligibility_checker import (  # noqa: E402
    calculate_child_tax_credit,
    get_credit_params,
)
from utils import load_tax_constants  # noqa: E402

CTC_PARAMS_2025 = get_credit_params(load_tax_constants("2025"))["child_tax_credit"]


def ctc(year="2025", **overrides):
    data = {
        "children_under_17": 1,
        "agi": 100000,
        "filing_status": "single",
        "earned_income": 100000,
    }
    data.update(overrides)
    params = (CTC_PARAMS_2025 if year == "2025"
              else get_credit_params(load_tax_constants(year))["child_tax_credit"])
    return calculate_child_tax_credit(data, params)


# ---------------------------------------------------------------------------
# F4.1 — the credit amount
# ---------------------------------------------------------------------------

def test_credit_is_2200_per_child_for_2025():
    """F4: OBBBA raised the per-child credit from $2,000 to $2,200."""
    assert ctc(children_under_17=1).amount == 2200
    assert ctc(children_under_17=3).amount == 6600      # 3 x 2200


def test_no_children_is_not_eligible():
    result = ctc(children_under_17=0)
    assert result.eligible is False
    assert result.amount == 0


# ---------------------------------------------------------------------------
# F4.2 — phase-out rounds the excess UP
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,agi,children,expected,why", [
    # At/below the threshold: no reduction.
    ("single",          200000, 1, 2200, "exactly at the $200,000 threshold"),
    ("married_jointly", 400000, 2, 4400, "exactly at the $400,000 threshold"),

    # Excess that is already a whole multiple of $1,000: floor and ceil agree.
    ("married_jointly", 410000, 2, 3900, "excess 10,000 -> 10 x $50 = $500 off 4,400"),

    # Excess with a remainder: the ceiling rule is what the old code got wrong.
    # $425 of excess still costs a full $50 (Schedule 8812 line 10 example).
    ("single",          200425, 1, 2150, "excess 425 -> rounds to 1,000 -> $50 off 2,200"),
    ("single",          200500, 1, 2150, "excess 500 -> rounds to 1,000 -> $50 off"),
    ("single",          201025, 1, 2100, "excess 1,025 -> rounds to 2,000 -> $100 off"),
    ("married_jointly", 410425, 2, 3850, "excess 10,425 -> rounds to 11,000 -> $550 off 4,400"),

    # Fully phased out: reduction cannot drive the credit below zero.
    ("single",          300000, 1, 0,    "excess 100,000 -> $5,000 reduction > 2,200"),
])
def test_phaseout_rounds_excess_up_to_next_thousand(status, agi, children, expected, why):
    """F4: reduction = ceil(excess / 1000) x $50, floored at a zero credit."""
    result = ctc(filing_status=status, agi=agi, children_under_17=children,
                 earned_income=agi)
    assert result.amount == expected, why


def test_old_floor_behavior_is_gone():
    """F4 regression: `excess // 1000` understated the phase-out by up to $50.

    With $200,999 of MAGI (single, 1 child) the floor method computed
    999 // 1000 = 0 -> a full $2,200 credit. The correct answer is $2,150.
    """
    assert ctc(filing_status="single", agi=200999, children_under_17=1).amount == 2150


def test_phaseout_flag_and_note_reported():
    result = ctc(filing_status="single", agi=250000, children_under_17=1)
    assert result.phaseout_applied is True
    assert any("phaseout" in n.lower() or "reduced" in n.lower() for n in result.notes)


# ---------------------------------------------------------------------------
# Refundable portion (ACTC)
# ---------------------------------------------------------------------------

def test_refundable_portion_capped_at_1700_per_child():
    """ACTC is limited to $1,700 per qualifying child for 2025."""
    result = ctc(children_under_17=2, agi=40000, earned_income=40000)
    assert result.refundable_amount <= 1700 * 2


def test_refundable_portion_uses_15_percent_over_2500():
    """ACTC = 15% x (earned income - $2,500), capped by the per-child limit.

    Hand-computed: earned income $20,000 -> 0.15 x (20,000 - 2,500) = $2,625.
    With one child the cap is $1,700, so the refundable amount is $1,700.
    """
    result = ctc(children_under_17=1, agi=20000, earned_income=20000)
    assert result.refundable_amount == 1700

    # With low earnings the 15% formula binds instead of the cap:
    # 0.15 x (10,000 - 2,500) = $1,125
    low = ctc(children_under_17=1, agi=10000, earned_income=10000)
    assert low.refundable_amount == pytest.approx(1125)


def test_no_refundable_portion_below_2500_earned_income():
    result = ctc(children_under_17=1, agi=2000, earned_income=2000)
    assert result.refundable_amount == 0
