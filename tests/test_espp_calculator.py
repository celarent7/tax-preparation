import sys
import os
import pytest
from datetime import date

# Add scripts directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "scripts"))

from espp_calculator import (
    determine_disposition, 
    calculate_tax_breakdown, 
    ESPPPurchase, 
    ESPPSale
)

def test_disqualifying_disposition_determination():
    offering = date(2024, 1, 1)
    purchase = date(2024, 6, 30)
    
    # Sold too early from purchase (less than 1 year)
    sale_1 = date(2025, 1, 1)
    result_1 = determine_disposition(offering, purchase, sale_1)
    assert result_1.disposition_type == "disqualifying"
    
    # Sold too early from offering (less than 2 years)
    sale_2 = date(2025, 7, 1)
    result_2 = determine_disposition(offering, purchase, sale_2)
    assert result_2.disposition_type == "disqualifying"

def test_qualifying_disposition_determination():
    offering = date(2023, 1, 1)
    purchase = date(2023, 6, 30)
    
    # Meets both: > 2 yrs from offering AND > 1 yr from purchase
    sale = date(2025, 7, 1)
    result = determine_disposition(offering, purchase, sale)
    assert result.disposition_type == "qualifying"

def test_disqualifying_tax_breakdown():
    # Scenario: $100 offering, $120 purchase FMV, $85 purchase price, $150 sale
    purchase = ESPPPurchase(
        offering_date=date(2024, 1, 1),
        purchase_date=date(2024, 6, 30),
        shares=100,
        purchase_price=85.00,
        fmv_at_offering=100.00,
        fmv_at_purchase=120.00
    )
    sale = ESPPSale(
        sale_date=date(2025, 1, 1), # Disqualifying
        shares_sold=100,
        sale_price=150.00,
        purchase=purchase
    )
    
    breakdown = calculate_tax_breakdown(sale)
    
    assert breakdown.disposition_type == "disqualifying"
    # Ordinary income = spread on purchase date: ($120 - $85) * 100 = $3500
    assert breakdown.ordinary_income == 3500.00
    # Adjusted basis = purchase price + ordinary = $8500 + $3500 = $12000 (or FMV at purchase)
    assert breakdown.adjusted_basis == 12000.00
    # Capital gain = $15000 proceeds - $12000 basis = $3000
    assert breakdown.capital_gain_loss == 3000.00
    assert breakdown.capital_gain_type == "short-term"

def test_qualifying_tax_breakdown():
    # Scenario: Same prices but sold late enough
    purchase = ESPPPurchase(
        offering_date=date(2023, 1, 1),
        purchase_date=date(2023, 6, 30),
        shares=100,
        purchase_price=85.00,
        fmv_at_offering=100.00,
        fmv_at_purchase=120.00
    )
    sale = ESPPSale(
        sale_date=date(2025, 7, 1), # Qualifying
        shares_sold=100,
        sale_price=150.00,
        purchase=purchase
    )
    
    breakdown = calculate_tax_breakdown(sale)
    
    assert breakdown.disposition_type == "qualifying"
    # Ordinary income = lesser of (150-85=65) or (100*0.15=15) -> $15 per share
    assert breakdown.ordinary_income == 1500.00
    # Adjusted basis = $85 + $15 = $100 per share -> $10000 total
    assert breakdown.adjusted_basis == 10000.00
    # Capital gain = $15000 proceeds - $10000 basis = $5000
    assert breakdown.capital_gain_loss == 5000.00
    assert breakdown.capital_gain_type == "long-term"
