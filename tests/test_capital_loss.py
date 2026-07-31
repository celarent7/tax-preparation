"""B1: net-capital-loss offset ($3,000 / $1,500 MFS) and carryforward."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from tax_calculator import apply_capital_loss
from utils import load_tax_constants


@pytest.fixture(scope="module")
def limits_2025():
    data = load_tax_constants("2025")
    return data["capital_loss_limit"], data["capital_loss_limit_mfs"]


def test_mfj_8k_loss_offsets_3k_carries_5k(limits_2025):
    limit, limit_mfs = limits_2025
    r = apply_capital_loss(8000, "married_jointly", limit, limit_mfs)
    assert r["allowed_offset"] == 3000
    assert r["carryforward"] == 5000
    assert r["limit_used"] == limit


def test_mfs_2k_loss_offsets_1500_carries_500(limits_2025):
    limit, limit_mfs = limits_2025
    r = apply_capital_loss(2000, "married_separately", limit, limit_mfs)
    assert r["allowed_offset"] == 1500
    assert r["carryforward"] == 500
    assert r["limit_used"] == limit_mfs


def test_loss_below_limit_fully_absorbed(limits_2025):
    limit, limit_mfs = limits_2025
    r = apply_capital_loss(1200, "single", limit, limit_mfs)
    assert r["allowed_offset"] == 1200
    assert r["carryforward"] == 0


def test_zero_loss_is_noop(limits_2025):
    limit, limit_mfs = limits_2025
    r = apply_capital_loss(0, "single", limit, limit_mfs)
    assert r["allowed_offset"] == 0
    assert r["carryforward"] == 0


def test_unknown_status_raises(limits_2025):
    limit, limit_mfs = limits_2025
    with pytest.raises(ValueError):
        apply_capital_loss(5000, "widowed_maybe", limit, limit_mfs)


# --- CLI integration (B1.2) ---
import json
import subprocess

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "tax_calculator.py")


def run_calc(*args):
    return subprocess.run(
        [sys.executable, SCRIPT, *args, "--output-format", "json"],
        capture_output=True, text=True)


def test_cli_capital_loss_reduces_agi_and_reports_carryforward():
    p = run_calc("--gross-income", "100000", "--filing-status", "mfj",
                 "--year", "2025", "--net-capital-loss", "8000")
    assert p.returncode == 0, p.stdout + p.stderr
    out = json.loads(p.stdout)
    assert out["calculations"]["adjusted_gross_income"] == 97000.0
    assert out["capital_loss"]["allowed_offset"] == 3000
    assert out["capital_loss"]["carryforward"] == 5000


def test_cli_capital_loss_mutually_exclusive_with_ltcg():
    p = run_calc("--gross-income", "100000", "--year", "2025",
                 "--net-capital-loss", "5000", "--ltcg", "10000")
    assert p.returncode == 1
    assert "mutually exclusive" in p.stdout


def test_cli_negative_capital_loss_rejected():
    p = run_calc("--gross-income", "100000", "--year", "2025",
                 "--net-capital-loss", "-5000")
    assert p.returncode == 1


def test_cli_niit_clamped_in_loss_year():
    # High MAGI, net loss: NIIT base must be 0, never negative.
    p = run_calc("--gross-income", "400000", "--filing-status", "single",
                 "--year", "2025", "--net-capital-loss", "8000")
    out = json.loads(p.stdout)
    assert out["tax"]["niit"] == 0