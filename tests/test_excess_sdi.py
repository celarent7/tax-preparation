"""C1: CA excess-SDI credit (Form 540 line 74)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from utils import excess_sdi_credit, load_state_constants

CAPPED_CFG = {"rate": 0.009, "wage_cap": 153164,
              "excess_credit_multiple_employers": True}  # 2023-shape fixture


def test_two_employers_over_cap_yields_credit():
    # Each withheld 0.9% on $100k => $900 each; max required = 0.9% x 153,164
    r = excess_sdi_credit([900.0, 900.0], CAPPED_CFG)
    assert r["possible"]
    assert r["max_required"] == pytest.approx(1378.48, abs=0.01)
    assert r["credit"] == pytest.approx(1800.0 - 1378.48, abs=0.01)


def test_single_employer_never_credits_even_over_cap():
    # One employer over-withholding is recovered FROM THE EMPLOYER, not on the
    # return (FTB 540 instructions) — the credit needs 2+ employers.
    r = excess_sdi_credit([2000.0], CAPPED_CFG)
    assert not r["possible"] and r["credit"] == 0
    assert "employer" in r["note"].lower()


def test_two_employers_under_cap_no_credit():
    r = excess_sdi_credit([400.0, 500.0], CAPPED_CFG)
    assert not r["possible"] and r["credit"] == 0


def test_uncapped_year_no_credit_structurally():
    cfg_2025 = load_state_constants("CA", "2025")["sdi"]
    assert cfg_2025["wage_cap"] is None  # semantic null (SB 951)
    r = excess_sdi_credit([3000.0, 3000.0], cfg_2025)
    assert not r["possible"] and r["credit"] == 0
    assert "no wage cap" in r["note"].lower()