"""Tests for the deprecated scripts/rsu/calculate_rsu_tax.py (plan CC Task 9).

The module used to load one year's figures into module globals and carried the
NIIT (3.8%) and Additional Medicare (0.9%) rates as literals. It now takes the
tax year as an argument and reads every figure from tax_constants.json.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "rsu"))

from calculate_rsu_tax import (  # noqa: E402
    calculate_full_rsu_tax,
    calculate_niit,
    load_tables,
)


def test_rsu_tax_differs_by_year():
    r25 = calculate_full_rsu_tax("single", 300000, 100000, year="2025")
    r26 = calculate_full_rsu_tax("single", 300000, 100000, year="2026")
    tax25 = r25["federal_taxes"]["ordinary_income_tax"]["amount"]
    tax26 = r26["federal_taxes"]["ordinary_income_tax"]["amount"]
    assert tax25 != tax26


def test_rsu_niit_uses_table_rate():
    tables = {**load_tables("2025"), "niit_rate": 0.05}
    assert calculate_niit(300000, 50000, "single", tables)["niit"] == 2500.0


def test_rsu_unknown_status_raises():
    with pytest.raises(ValueError):
        calculate_niit(300000, 50000, "bogus", load_tables("2025"))
