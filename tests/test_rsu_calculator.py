import sys
import os
import pytest

# Add scripts directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "scripts"))

from rsu_calculator import calculate_withholding_estimate, calculate_lot_sale, VestingLot
from utils import load_tax_constants

@pytest.fixture
def tax_data_2025():
    return load_tax_constants("2025")

def test_rsu_withholding_shortfall_2025(tax_data_2025):
    # Scenario: $150k base, $20k RSU vesting (100 shares @ $200)
    
    result = calculate_withholding_estimate(
        vesting_income=20000,
        ytd_wages=150000,
        filing_status="single",
        state_rate=0.00,
        tax_data=tax_data_2025
    )
    
    # Typical federal withholding @ 22% = $4,400
    assert result["withholding"]["federal"] == 4400.00
    
    # Taxable income = $150k + $20k - $15k = $155k
    # Marginal bracket for $155k in 2025 is 24%
    assert result["estimated_actual_tax"]["marginal_bracket"] == 0.24
    
    # Estimated federal tax on the $20k @ 24% = $4,800
    assert result["estimated_actual_tax"]["federal"] == 4800.00
    
    # Shortfall = $4,800 - $4,400 = $400
    assert result["shortfall"] == 400.00

def test_rsu_additional_medicare_trigger(tax_data_2025):
    # Scenario: Single filer crosses $200k threshold
    # $190k base + $20k RSU = $210k. $10k is subject to extra 0.9%
    
    result = calculate_withholding_estimate(
        vesting_income=20000,
        ytd_wages=190000,
        filing_status="single",
        state_rate=0.05,
        tax_data=tax_data_2025
    )
    
    # Additional medicare = $10,000 * 0.009 = $90
    assert result["estimated_actual_tax"]["additional_medicare"] == 90.00

def test_rsu_lot_sale_long_term():
    lot = VestingLot(
        vesting_date="2023-01-01",
        shares_vested=100,
        fmv_at_vesting=150.00,
        shares_withheld=30
    )
    
    # Sold 2 years later @ $200
    sale_result = calculate_lot_sale(
        lot=lot,
        shares_to_sell=50,
        sale_price=200.00,
        sale_date="2025-01-02",
        reported_basis_1099b=0.00 # Common broker error
    )
    
    assert sale_result.holding_period == "long_term"
    # Proceeds = 50 * 200 = 10000
    assert sale_result.proceeds == 10000.00
    # Correct Basis = 50 * 150 = 7500
    assert sale_result.correct_basis == 7500.00
    # Gain = 2500
    assert sale_result.gain_loss == 2500.00
    # Adjustment needed for 8949
    assert sale_result.basis_adjustment_needed is True
    assert sale_result.adjustment_amount == 7500.00
    assert sale_result.form_8949_code == "B"
