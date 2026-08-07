import sys
import os
import pytest

# Add scripts directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "scripts"))

from tax_calculator import calculate_tax, calculate_standard_deduction, calculate_marginal_rate
from utils import load_tax_constants

@pytest.fixture
def tax_data_2025():
    return load_tax_constants("2025")

@pytest.fixture
def tax_data_2024():
    return load_tax_constants("2024")

def test_standard_deduction_2025(tax_data_2025):
    """Y3: 2025 standard deductions are the OBBBA amounts, not the pre-OBBBA ones.

    This test previously asserted $15,000 / $30,000 — the pre-OBBBA figures —
    and so encoded audit finding Y3 rather than catching it. OBBBA amended the
    basic standard deduction for tax years beginning after 2024-12-31 to
    $15,750 single/MFS, $31,500 MFJ/QSS, $23,625 HoH.

    Source: IRS Publication 501 (2025), https://www.irs.gov/publications/p501;
    IRS "New and enhanced deductions for individuals" (retrieved 2026-07-29).
    """
    sd = tax_data_2025["standard_deductions"]
    add = tax_data_2025["additional_deduction_65_blind"]

    # Single
    assert calculate_standard_deduction("single", sd, add) == 15750
    # MFJ
    assert calculate_standard_deduction("married_jointly", sd, add) == 31500
    # MFS and HoH
    assert calculate_standard_deduction("married_separately", sd, add) == 15750
    assert calculate_standard_deduction("head_of_household", sd, add) == 23625
    # Single 65+: 15750 + 2000
    assert calculate_standard_deduction("single", sd, add, age_65_plus=1) == 17750
    # MFJ both 65+: 31500 + 2 x 1600
    assert calculate_standard_deduction("married_jointly", sd, add, age_65_plus=2) == 31500 + (1600 * 2)

def test_standard_deduction_2024(tax_data_2024):
    sd = tax_data_2024["standard_deductions"]
    add = tax_data_2024["additional_deduction_65_blind"]
    
    # Single
    assert calculate_standard_deduction("single", sd, add) == 14600
    # MFJ
    assert calculate_standard_deduction("married_jointly", sd, add) == 29200

def test_tax_calculation_simple_2025(tax_data_2025):
    brackets = tax_data_2025["tax_brackets_processed"]
    
    # Single, $10,000 taxable income (all in 10% bracket)
    tax, breakdown = calculate_tax(10000, "single", brackets)
    assert tax == 1000.00
    assert len(breakdown) == 1
    assert breakdown[0]["rate"] == 0.10
    
    # Single, $50,000 taxable income
    # 10% on 11,925 = 1,192.50
    # 12% on (48,475 - 11,925 = 36,550) = 4,386.00
    # 22% on (50,000 - 48,475 = 1,525) = 335.50
    # Total = 5,914.00
    tax, breakdown = calculate_tax(50000, "single", brackets)
    assert tax == 5914.00
    assert len(breakdown) == 3

def test_marginal_rate_2025(tax_data_2025):
    brackets = tax_data_2025["tax_brackets_processed"]
    
    # Single, $10,000 (10%)
    assert calculate_marginal_rate(10000, "single", brackets) == 0.10
    # Single, $50,000 (22% - since 48,475 is the 12% cutoff)
    assert calculate_marginal_rate(50000, "single", brackets) == 0.22
    # Single, very high income (37%)
    assert calculate_marginal_rate(1000000, "single", brackets) == 0.37

def test_tax_calculation_mfj_2025(tax_data_2025):
    brackets = tax_data_2025["tax_brackets_processed"]
    
    # MFJ, $100,000 taxable
    # 10% on 23,850 = 2,385.00
    # 12% on (96,950 - 23,850 = 73,100) = 8,772.00
    # 22% on (100,000 - 96,950 = 3,050) = 671.00
    # Total = 11,828.00
    tax, breakdown = calculate_tax(100000, "married_jointly", brackets)
    assert tax == 11828.00
    assert len(breakdown) == 3


# ---------------------------------------------------------------------------
# Nonrefundable credits offset income tax (line 18) only, never NIIT or
# Additional Medicare Tax (added afterward at line 23).
# ---------------------------------------------------------------------------

import json
import subprocess

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "tax_calculator.py")


def test_nonrefundable_credits_do_not_offset_niit_or_additional_medicare():
    """Single 2025: wages/MAGI $300,000, net investment income $50,000 ->
    NIIT $1,900 (3.8% x min(50,000, 300,000-200,000)); Additional Medicare
    $900 (0.9% x (300,000-200,000)). Nonrefundable credits of $100,000 (far
    more than income tax alone) must cap at income_tax exactly, not at
    income_tax + niit + additional_medicare -- Form 1040's structure never
    lets nonrefundable credits reduce the surtaxes added at line 23.
    """
    result = subprocess.run(
        [sys.executable, SCRIPT,
         "--gross-income", "300000", "--filing-status", "single", "--year", "2025",
         "--wages", "300000", "--magi", "300000", "--net-investment-income", "50000",
         "--nonrefundable-credits", "100000", "--output-format", "json"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    tax = json.loads(result.stdout)["tax"]

    assert tax["niit"] == 1900.0
    assert tax["additional_medicare_tax"] == 900.0
    # The cap: applied credits equal income_tax alone, NOT
    # income_tax + niit + additional_medicare (tax_before_credits).
    assert tax["nonrefundable_credits_applied"] == tax["income_tax"]
    assert tax["nonrefundable_credits_applied"] != tax["tax_before_credits"]
    # The surtaxes survive the (fully-consumed) nonrefundable credit.
    assert tax["tax_after_credits"] == round(tax["niit"] + tax["additional_medicare_tax"], 2)
    assert tax["tax_after_credits"] == 2800.0
