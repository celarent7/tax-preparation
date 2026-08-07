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


# ---------------------------------------------------------------------------
# F11 — the $25,000 annual limit check is wired into the CLI (not just
# unit-tested via check_annual_limit in isolation).
# ---------------------------------------------------------------------------

import json
import subprocess

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "espp_calculator.py")


def _run_espp_cli(*args):
    return subprocess.run(
        [sys.executable, SCRIPT, *args, "--output-format", "json"],
        capture_output=True, text=True,
    )


def test_cli_basis_warns_when_offering_value_exceeds_25000():
    """A $30,000-at-offering purchase (300 shares x $100 FMV at offering)
    must surface an IRC 423(b)(8) warning through the actual `basis`
    subcommand, not just through the unit-tested check_annual_limit
    function."""
    result = _run_espp_cli(
        "basis",
        "--offering-date", "2024-01-01", "--purchase-date", "2024-06-30",
        "--fmv-at-offering", "100", "--fmv-at-purchase", "110",
        "--purchase-price", "85", "--sale-date", "2025-08-01",
        "--sale-price", "130", "--shares", "300",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    check = payload["annual_limit_check"]
    assert check["within_limit"] is False
    assert check["excess"] == 5000.0
    assert "423(b)(8)" in check["warning"]


def test_cli_basis_no_warning_within_limit():
    """A purchase valued at or under $25,000 at the offering date produces no
    warning."""
    result = _run_espp_cli(
        "basis",
        "--offering-date", "2024-01-01", "--purchase-date", "2024-06-30",
        "--fmv-at-offering", "100", "--fmv-at-purchase", "110",
        "--purchase-price", "85", "--sale-date", "2025-08-01",
        "--sale-price", "130", "--shares", "200",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    check = payload["annual_limit_check"]
    assert check["within_limit"] is True
    assert check["warning"] is None


def test_cli_lots_aggregates_annual_limit_by_offering_year(tmp_path):
    """The `lots` subcommand must aggregate the offering-date FMV total
    across ALL purchases sharing the same offering-date calendar year (the
    limit accrues per calendar year, not per purchase) and flag the excess."""
    lots = [
        {"offering_date": "2024-01-01", "purchase_date": "2024-06-30",
         "shares": 200, "purchase_price": 85, "fmv_at_offering": 100,
         "fmv_at_purchase": 110},
        {"offering_date": "2024-01-01", "purchase_date": "2024-12-31",
         "shares": 100, "purchase_price": 90, "fmv_at_offering": 105,
         "fmv_at_purchase": 115},
    ]
    path = tmp_path / "lots.json"
    path.write_text(json.dumps(lots))
    result = _run_espp_cli("lots", "--file", str(path))
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert len(payload) == 2
    # Combined offering-date value: 200*100 + 100*105 = 30,500 -> exceeds
    # $25,000 by $5,500 for BOTH lots (same offering year).
    for lot in payload:
        check = lot["annual_limit_check"]
        assert check["within_limit"] is False
        assert check["offering_date_value_used"] == 30500.0
        assert check["excess"] == 5500.0
