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
    sd = tax_data_2025["standard_deductions"]
    add = tax_data_2025["additional_deduction_65_blind"]
    
    # Single
    assert calculate_standard_deduction("single", sd, add) == 15000
    # MFJ
    assert calculate_standard_deduction("married_jointly", sd, add) == 30000
    # Single 65+
    assert calculate_standard_deduction("single", sd, add, age_65_plus=1) == 17000
    # MFJ both 65+
    assert calculate_standard_deduction("married_jointly", sd, add, age_65_plus=2) == 30000 + (1600 * 2)

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
