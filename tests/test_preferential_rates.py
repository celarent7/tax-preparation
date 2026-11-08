#!/usr/bin/env python3
"""Tests for the preferential-rate stack, NIIT and Additional Medicare (task P2).

Covers audit finding F3: `tax_calculator.py` taxed long-term capital gain and
qualified dividends at ordinary rates, never computed NIIT or Additional
Medicare despite carrying the thresholds in its constants, and treated all
credits as non-refundable.

EVERY expected value in this file is hand-computed from the published brackets
and shown as a worksheet in the test, never read off the script's output.

2025 MFJ ordinary brackets: 10% to 23,850; 12% to 96,950; 22% to 206,700;
24% to 394,600; 32% to 501,050; 35% to 751,600; 37% above.
2025 MFJ LTCG brackets: 0% to 96,700; 15% to 600,050; 20% above.
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

from tax_calculator import (  # noqa: E402
    calculate_additional_medicare,
    calculate_niit,
    calculate_preferential_tax,
)
from utils import load_tax_constants  # noqa: E402

TAX_2025 = load_tax_constants("2025")
LTCG_MFJ = TAX_2025["ltcg_brackets_processed"]["married_jointly"]
LTCG_SINGLE = TAX_2025["ltcg_brackets_processed"]["single"]
NIIT_RATE_2025 = TAX_2025["niit_rate"]


def run_calc(*args):
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "tax_calculator.py"),
         "--output-format", "json", *args],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# The stacking worksheet
# ---------------------------------------------------------------------------

def test_preferential_income_entirely_in_the_zero_bracket():
    """MFJ, $50,000 ordinary + $30,000 LTCG.

    The preferential slice occupies [50,000, 80,000]. The 0% band runs to
    $96,700, so the whole slice is taxed at 0%.
    Expected preferential tax: $0.
    """
    tax, breakdown = calculate_preferential_tax(50000, 30000, LTCG_MFJ)
    assert tax == 0.0
    assert breakdown[0]["rate"] == 0.00
    assert breakdown[0]["preferential_income_in_bracket"] == 30000


def test_preferential_income_straddling_the_zero_and_fifteen_bands():
    """MFJ, $80,000 ordinary + $40,000 LTCG.

    Slice occupies [80,000, 120,000]; the 0% band ends at $96,700.
        0%:  96,700 - 80,000 = 16,700  -> $0
        15%: 120,000 - 96,700 = 23,300 -> 23,300 x 0.15 = $3,495
    Expected preferential tax: $3,495.
    """
    tax, breakdown = calculate_preferential_tax(80000, 40000, LTCG_MFJ)
    assert tax == 3495.00
    assert breakdown[0]["preferential_income_in_bracket"] == 16700
    assert breakdown[1]["preferential_income_in_bracket"] == 23300


def test_preferential_income_entirely_in_the_fifteen_bracket():
    """MFJ, $168,500 ordinary + $50,000 preferential.

    Slice occupies [168,500, 218,500], wholly inside the 15% band
    ($96,700 - $600,050).
    Expected: 50,000 x 0.15 = $7,500.
    """
    tax, _ = calculate_preferential_tax(168500, 50000, LTCG_MFJ)
    assert tax == 7500.00


def test_preferential_income_reaching_the_twenty_percent_band():
    """MFJ, $500,000 ordinary + $200,000 LTCG.

    Slice occupies [500,000, 700,000]; the 15% band ends at $600,050.
        15%: 600,050 - 500,000 = 100,050 -> x 0.15 = $15,007.50
        20%: 700,000 - 600,050 =  99,950 -> x 0.20 = $19,990.00
    Expected: $34,997.50.
    """
    tax, breakdown = calculate_preferential_tax(500000, 200000, LTCG_MFJ)
    assert tax == 34997.50
    assert [b["rate"] for b in breakdown] == [0.15, 0.20]


def test_zero_preferential_income_is_free():
    assert calculate_preferential_tax(100000, 0, LTCG_MFJ) == (0.0, [])


def test_ordinary_income_alone_pushes_gains_out_of_the_zero_bracket():
    """Regression for the stacking rule itself.

    The same $30,000 of LTCG is taxed at 0% for a low-ordinary-income filer
    and at 15% for a high-ordinary-income one. Taxing gains in isolation (the
    naive approach) would give the same answer for both, which is wrong.
    """
    low, _ = calculate_preferential_tax(20000, 30000, LTCG_MFJ)
    high, _ = calculate_preferential_tax(300000, 30000, LTCG_MFJ)
    assert low == 0.0
    assert high == 4500.00        # 30,000 x 0.15


# ---------------------------------------------------------------------------
# End-to-end: the full return
# ---------------------------------------------------------------------------

def test_full_return_with_qualified_dividends_and_ltcg():
    """MFJ 2025: $200,000 wages + $10,000 qualified dividends + $40,000 LTCG.

    Worksheet:
      Total income      250,000
      Standard deduction 31,500  (OBBBA MFJ)
      Taxable income    218,500
      Preferential       50,000  (10,000 QD + 40,000 LTCG)
      Ordinary          168,500

      Ordinary tax:
        10% x 23,850                    =  2,385.00
        12% x (96,950 - 23,850=73,100)  =  8,772.00
        22% x (168,500 - 96,950=71,550) = 15,741.00
                                   total = 26,898.00
      Preferential: slice [168,500, 218,500] is inside the 15% band
        15% x 50,000                    =  7,500.00

      Income tax = 26,898 + 7,500       = 34,398.00
      NIIT: MAGI 250,000 is exactly at the MFJ threshold -> $0
      Additional Medicare: wages 250,000 exactly at threshold -> $0
      TOTAL                             = 34,398.00

    Taxing the 50,000 as ordinary income instead (the old behavior) would give
    22% x 50,000 = 11,000 of tax on that slice rather than 7,500 — the plugin
    overstated tax by $3,500 on this return.
    """
    r = run_calc("--gross-income", "250000", "--filing-status", "mfj",
                 "--year", "2025", "--ltcg", "40000",
                 "--qualified-dividends", "10000")

    assert r["calculations"]["taxable_income"] == 218500
    assert r["calculations"]["ordinary_income"] == 168500
    assert r["calculations"]["preferential_income"] == 50000

    assert r["tax"]["ordinary_income_tax"] == 26898.00
    assert r["tax"]["preferential_rate_tax"] == 7500.00
    assert r["tax"]["income_tax"] == 34398.00
    assert r["tax"]["niit"] == 0
    assert r["tax"]["additional_medicare_tax"] == 0
    assert r["tax"]["tax_after_credits"] == 34398.00


def test_same_return_without_preferential_flags_is_higher():
    """F3 regression: the old behavior, reproduced by omitting the new flags.

    Without --ltcg/--qualified-dividends the whole $218,500 is ordinary:
        10% x 23,850                     =  2,385.00
        12% x 73,100                     =  8,772.00
        22% x (206,700-96,950=109,750)   = 24,145.00
        24% x (218,500-206,700=11,800)   =  2,832.00
                                    total = 38,134.00
    which is $3,736 more than the correct $34,398.
    """
    r = run_calc("--gross-income", "250000", "--filing-status", "mfj", "--year", "2025")
    assert r["tax"]["income_tax"] == 38134.00
    assert 38134.00 - 34398.00 == 3736.00


# ---------------------------------------------------------------------------
# NIIT
# ---------------------------------------------------------------------------

def test_niit_is_lesser_of_nii_and_magi_excess():
    """IRC 1411: 3.8% x min(NII, MAGI - threshold).

    MAGI 300,000, MFJ threshold 250,000 -> excess 50,000.
    NII 50,000 -> base = min(50,000, 50,000) = 50,000.
    Tax = 0.038 x 50,000 = $1,900.
    """
    niit = calculate_niit(300000, 50000, 250000, NIIT_RATE_2025)
    assert niit["taxable_base"] == 50000
    assert niit["tax"] == 1900.00


def test_niit_limited_by_magi_excess_when_nii_is_larger():
    """MAGI 260,000 -> excess 10,000; NII 50,000. Base is the SMALLER: 10,000.
    Tax = 0.038 x 10,000 = $380."""
    niit = calculate_niit(260000, 50000, 250000, NIIT_RATE_2025)
    assert niit["taxable_base"] == 10000
    assert niit["tax"] == 380.00


def test_niit_limited_by_nii_when_magi_excess_is_larger():
    """MAGI 500,000 -> excess 250,000; NII 20,000. Base = 20,000.
    Tax = 0.038 x 20,000 = $760."""
    niit = calculate_niit(500000, 20000, 250000, NIIT_RATE_2025)
    assert niit["taxable_base"] == 20000
    assert niit["tax"] == 760.00


def test_niit_does_not_apply_below_threshold():
    assert calculate_niit(200000, 50000, 250000, NIIT_RATE_2025)["tax"] == 0
    assert calculate_niit(250000, 50000, 250000, NIIT_RATE_2025)["applies"] is False


def test_niit_end_to_end():
    """MFJ: 250,000 wages + 50,000 LTCG. MAGI 300,000, NII 50,000 -> $1,900."""
    r = run_calc("--gross-income", "300000", "--filing-status", "mfj",
                 "--year", "2025", "--ltcg", "50000", "--wages", "250000")
    assert r["tax"]["niit"] == 1900.00
    assert r["niit_detail"]["threshold"] == 250000


# ---------------------------------------------------------------------------
# Additional Medicare
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wages,threshold,expected", [
    (300000, 250000, 450.00),    # 0.9% x 50,000
    (250000, 250000, 0),         # exactly at the threshold
    (200000, 250000, 0),
    (500000, 200000, 2700.00),   # single: 0.9% x 300,000
])
def test_additional_medicare(wages, threshold, expected):
    assert calculate_additional_medicare(wages, threshold)["tax"] == expected


def test_additional_medicare_end_to_end_uses_status_threshold():
    """MFJ threshold is $250,000; single is $200,000, for the same wages."""
    mfj = run_calc("--gross-income", "300000", "--filing-status", "mfj", "--year", "2025")
    single = run_calc("--gross-income", "300000", "--filing-status", "single", "--year", "2025")

    assert mfj["tax"]["additional_medicare_tax"] == 450.00      # 0.9% x 50,000
    assert single["tax"]["additional_medicare_tax"] == 900.00   # 0.9% x 100,000


# ---------------------------------------------------------------------------
# Refundable vs non-refundable credits (P2.4)
# ---------------------------------------------------------------------------

def test_nonrefundable_credits_cannot_go_below_zero():
    """A $50,000 non-refundable credit against a small liability stops at zero."""
    r = run_calc("--gross-income", "50000", "--filing-status", "single",
                 "--year", "2025", "--nonrefundable-credits", "50000")
    assert r["tax"]["tax_after_credits"] == 0
    assert r["tax"]["refund_from_refundable_credits"] == 0


def test_refundable_credits_produce_a_refund():
    """F3: refundable credits (EITC/ACTC) pay out below zero.

    Single, $30,000 gross, 2025 standard deduction 15,750 -> taxable 14,250.
        10% x 11,925           = 1,192.50
        12% x (14,250-11,925)  =   279.00
                          total = 1,471.50
    A $3,000 refundable credit leaves 1,471.50 - 3,000 = -1,528.50,
    i.e. a $1,528.50 refund. The old code floored this at zero and silently
    discarded the refund.
    """
    r = run_calc("--gross-income", "30000", "--filing-status", "single",
                 "--year", "2025", "--refundable-credits", "3000")
    assert r["tax"]["tax_before_credits"] == 1471.50
    assert r["tax"]["tax_after_credits"] == 0
    assert r["tax"]["refund_from_refundable_credits"] == 1528.50


# ---------------------------------------------------------------------------
# AMT risk flag (F8 / P2.6 — flag only, no Form 6251)
# ---------------------------------------------------------------------------

def test_amt_risk_flagged_for_iso_exercise():
    r = run_calc("--gross-income", "300000", "--filing-status", "mfj",
                 "--year", "2025", "--iso-bargain-element", "200000")
    assert r["amt_risk"]["amt_risk"] is True
    assert any("ISO" in reason for reason in r["amt_risk"]["reasons"])


def test_amt_risk_not_flagged_for_a_plain_return():
    r = run_calc("--gross-income", "120000", "--filing-status", "mfj", "--year", "2025")
    assert r["amt_risk"]["amt_risk"] is False


def test_amt_risk_is_a_flag_not_a_computation():
    """F8 is explicitly scoped to a trigger flag; no Form 6251 number is emitted."""
    r = run_calc("--gross-income", "300000", "--filing-status", "mfj",
                 "--year", "2025", "--iso-bargain-element", "200000")
    assert "does not compute Form 6251" in r["amt_risk"]["note"]
    assert "amt_tax" not in r["tax"]


def test_niit_uses_given_rate():
    """The NIIT rate is an argument read from constants, not a literal default."""
    assert calculate_niit(300000, 50000, 250000, 0.05)["tax"] == 2500.0
