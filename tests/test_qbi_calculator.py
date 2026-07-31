"""Acceptance tests for the QBI (§199A) calculator — docs/qbi_calculator_plan.md Q1–Q11.

Every expected value here is hand-computed from the statute / form logic and
the constants in scripts/tax_constants.json. The engine reads those constants
via utils.get_constant; no figure is hard-coded in the engine, and the ½ SE
tax is reused byte-identically from estimated_tax_calculator.calculate_se_tax.
"""

import copy
import json
import os
import subprocess
import sys

import pytest

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

import utils  # noqa: E402
import qbi_calculator as qbi  # noqa: E402
from estimated_tax_calculator import calculate_se_tax  # noqa: E402


# Shared year-constant fixtures (deep-copied so monkeypatches stay local).
_YC = {}
for _y in ("2025", "2026"):
    _YC[_y] = utils.load_tax_constants(_y)


def yc(year):
    return copy.deepcopy(_YC[str(year)])


# ---------------------------------------------------------------------------
# Q1 — Below threshold, simplified path (Form 8995)
# ---------------------------------------------------------------------------
def test_q1_below_threshold_simplified():
    """MFJ 2025, adjusted QBI $80,000, TI $150,000, no wages/UBIA.

    Threshold (MFJ 2025) = $394,600 (below it → Form 8995): component =
    20% × 80,000 = $16,000; SSTB status is irrelevant below the threshold
    (run with and without --sstb must give the same result).
    """
    base = dict(adjusted_qbi=80000, taxable_income_before_qbi=150000,
                filing_status="mfj")
    r_non = qbi.compute_qbi_deduction(base, yc(2025))
    r_sstb = qbi.compute_qbi_deduction(dict(base, is_sstb=True), yc(2025))
    assert r_non.deduction == 16000.0
    assert r_non.form == "8995"
    assert r_non.deduction == r_sstb.deduction  # SSTB flag has no effect below threshold
    assert r_non.qbi_component == 16000.0


# ---------------------------------------------------------------------------
# Q2 — Adjusted-QBI derivation (½ SE tax reused byte-identically)
# ---------------------------------------------------------------------------
def test_q2_adjusted_qbi_derivation():
    """Schedule C $100,000, SE health $10,000, ½ SE tax computed internally.

    ½ SE tax is reused from calculate_se_tax — byte-identical to the
    estimated-tax path — so it is $7,064.77 (the plan's hand-rounding of
    $7,064.78 differs by one cent; the reuse requirement governs, ground
    rule 4). Adjusted QBI = 100,000 − 7,064.77 − 10,000 = $82,935.23.
    """
    half = calculate_se_tax(100000.0, yc(2025)["se_tax"])["deduction"]
    assert half == 7064.77  # byte-identical to estimated_tax_calculator
    profile = dict(se_net_profit=100000, se_health_insurance=10000,
                   taxable_income_before_qbi=150000, filing_status="mfj")
    r = qbi.compute_qbi_deduction(profile, yc(2025))
    assert r.half_se_tax == half == 7064.77
    assert r.adjusted_qbi == 82935.23  # 100000 − 7064.77 − 10000
    # Below threshold (MFJ 394600): component = 20% × adjusted QBI.
    assert r.qbi_component == round(0.20 * 82935.23, 2)


# ---------------------------------------------------------------------------
# Q3 — SSTB fully phased out
# ---------------------------------------------------------------------------
def test_q3_sstb_fully_phased_out():
    """Single 2025, TI = threshold + range + $1, SSTB.

    Threshold 197,300, range 50,000 → top of range 247,300; TI = 247,301 is
    above it. SSTB applicable percentage = 0 → QBI component $0. The REIT
    component (if any) survives and is never wage-limited.
    """
    ti = 197300 + 50000 + 1  # 247301
    profile = dict(adjusted_qbi=100000, w2_wages_paid=40000, is_sstb=True,
                   taxable_income_before_qbi=ti, reit_dividends=1000,
                   filing_status="single")
    r = qbi.compute_qbi_deduction(profile, yc(2025))
    assert r.qbi_component == 0.0
    assert r.reit_component == 200.0  # 20% × 1000, survives
    assert r.deduction == 200.0
    assert r.form == "8995-A"
    assert any("phased out" in n.lower() or "$0" in n for n in r.notes)


# ---------------------------------------------------------------------------
# Q4 — SSTB inside the phase-in range
# ---------------------------------------------------------------------------
def test_q4_sstb_inside_phase_in():
    """Single 2025, TI = threshold + 50% of range, SSTB, QBI $100k, wages $40k.

    applicable_pct = 1 − (TI−thr)/range = 1 − 0.5 = 0.50, applied to QBI and
    wages FIRST: eff_QBI = 50,000, eff_wages = 20,000. tentative = 20% × 50,000
    = 10,000; full_limit = max(50% × 20,000, 25% × 20,000) = 10,000. tentative
    does not exceed the limit → reduction 0 → component = $10,000.
    """
    ti = 197300 + 25000  # midpoint of the range
    profile = dict(adjusted_qbi=100000, w2_wages_paid=40000, is_sstb=True,
                   taxable_income_before_qbi=ti, filing_status="single")
    r = qbi.compute_qbi_deduction(profile, yc(2025))
    assert r.component_detail["applicable_pct"] == 0.5
    assert r.qbi_component == 10000.0
    assert r.form == "8995-A"


# ---------------------------------------------------------------------------
# Q5 — Wage limit binds above the range
# ---------------------------------------------------------------------------
def test_q5_wage_limit_binds_above_range():
    """MFJ 2025, TI $600,000 (above the $494,600 top of the MFJ range), non-SSTB.

    UBIA $0: tentative 60,000 vs full_limit max(25,000, 12,500) = 25,000 →
    component 25,000. UBIA $1,000,000: full_limit max(25,000, 12,500 + 25,000)
    = 37,500 → component 37,500.
    """
    common = dict(adjusted_qbi=300000, w2_wages_paid=50000,
                  taxable_income_before_qbi=600000, filing_status="mfj")
    r0 = qbi.compute_qbi_deduction(dict(common, ubia=0), yc(2025))
    assert r0.qbi_component == 25000.0
    r1 = qbi.compute_qbi_deduction(dict(common, ubia=1000000), yc(2025))
    assert r1.qbi_component == 37500.0
    assert r0.form == "8995-A"


# ---------------------------------------------------------------------------
# Q6 — Overall taxable-income limit (qualified dividends count in NCG)
# ---------------------------------------------------------------------------
def test_q6_overall_taxable_income_limit():
    """QBI component $20,000, TI $90,000, net capital gain $60,000.

    Overall limit = 20% × (90,000 − 60,000) = $6,000, so the deduction is
    min(20,000, 6,000) = $6,000. net_capital_gain is the §1(h) amount which
    per §199A(e)(3) INCLUDES qualified dividends — the caller passes LTCG +
    qualified dividends together (documented in the engine).
    """
    # adjusted_qbi 100,000 below threshold → component 20,000.
    profile = dict(adjusted_qbi=100000, taxable_income_before_qbi=90000,
                   net_capital_gain=60000, filing_status="single")
    r = qbi.compute_qbi_deduction(profile, yc(2025))
    assert r.qbi_component == 20000.0
    assert r.overall_limit == 6000.0
    assert r.deduction == 6000.0


# ---------------------------------------------------------------------------
# Q7 — REIT component (the live 1099-DIV Box 5 case)
# ---------------------------------------------------------------------------
def test_q7_reit_component():
    """$40.26 of section 199A dividends, no SE income.

    REIT component = 20% × 40.26 = $8.05; never wage-limited; Form 8995
    (REIT-only, no trade/business, below threshold).
    """
    profile = dict(se_net_profit=0, reit_dividends=40.26,
                   taxable_income_before_qbi=50000, filing_status="single")
    r = qbi.compute_qbi_deduction(profile, yc(2025))
    assert r.reit_component == 8.05
    assert r.qbi_component == 0.0
    assert r.deduction == 8.05
    assert r.form == "8995"


# ---------------------------------------------------------------------------
# Q8 — Negative QBI carryforward
# ---------------------------------------------------------------------------
def test_q8_negative_qbi_carryforward():
    """Adjusted QBI −$30,000, REIT $1,000.

    QBI component $0 (negative), REIT component $200 (not netted), QBI loss
    carryforward −$30,000 reported. Feeding the carryforward into a
    second-year run reduces that year's QBI by $30,000 (i.e. its component
    by $6,000 below threshold).
    """
    y1 = dict(adjusted_qbi=-30000, reit_dividends=1000,
              taxable_income_before_qbi=50000, filing_status="single")
    r1 = qbi.compute_qbi_deduction(y1, yc(2025))
    assert r1.qbi_component == 0.0
    assert r1.reit_component == 200.0
    assert r1.qbi_loss_carryforward == -30000.0
    assert r1.deduction == 200.0

    # Second year: same Schedule C $50,000, consume the $30,000 carryforward.
    base2 = dict(se_net_profit=50000, taxable_income_before_qbi=50000,
                 filing_status="single")
    r2_no_cf = qbi.compute_qbi_deduction(base2, yc(2025))
    r2_cf = qbi.compute_qbi_deduction(dict(base2, qbi_loss_carryforward=30000),
                                     yc(2025))
    # Carryforward reduced adjusted QBI by exactly 30,000 (half-SE-tax is
    # identical for the same net profit), and the component by 6,000.
    assert r2_cf.adjusted_qbi == round(r2_no_cf.adjusted_qbi - 30000, 2)
    assert r2_cf.qbi_component == round(r2_no_cf.qbi_component - 6000, 2)
    assert r2_cf.qbi_component == 3293.52
    assert r2_cf.qbi_loss_carryforward == 0.0


# ---------------------------------------------------------------------------
# Q9 — Year gating (2025 vs 2026) + 2026 $400 minimum + fail-loudly on null
# ---------------------------------------------------------------------------
def test_q9_year_gating_2025_vs_2026():
    """Identical single filer, adjusted QBI $200,000, wages $50,000, TI $260,000.

    2025 range $50,000 → top of range $247,300; TI $260,000 is ABOVE it → full
    wage limit → component = min(40,000, 25,000) = $25,000.
    2026 range $75,000 → top of range $276,750; TI $260,000 is INSIDE it →
    reduction = (40,000 − 25,000) × (58,250/75,000) = $11,650 → component
    = $28,350. Different results driven by the OBBBA-widened 2026 range.
    """
    prof = dict(adjusted_qbi=200000, w2_wages_paid=50000,
                taxable_income_before_qbi=260000, filing_status="single")
    r25 = qbi.compute_qbi_deduction(prof, yc(2025))
    r26 = qbi.compute_qbi_deduction(prof, yc(2026))
    assert r25.qbi_component == 25000.0
    assert r26.qbi_component == 28350.0
    assert r25.qbi_component != r26.qbi_component


def test_q9_2026_400_minimum_applies():
    """2026 $400 minimum fires only when constants are non-null and conditions met.

    Single 2026, adjusted QBI $1,500 (≥ the $1,000 floor), TI $50,000 (below
    threshold). Normal component = 20% × 1,500 = $300 < $400 → §199A(i)
    raises the deduction to $400, overriding the overall taxable-income limit.
    """
    prof = dict(adjusted_qbi=1500, taxable_income_before_qbi=50000,
                filing_status="single")
    r = qbi.compute_qbi_deduction(prof, yc(2026))
    assert r.qbi_component == 300.0
    assert r.deduction == 400.0
    assert r.min_deduction_applied is True


def test_q9_2026_minimum_does_not_fire_below_floor():
    """REIT-only 2026 (active QBI 0 < $1,000 floor): the minimum does NOT apply."""
    prof = dict(se_net_profit=0, reit_dividends=100,
                taxable_income_before_qbi=50000, filing_status="single")
    r = qbi.compute_qbi_deduction(prof, yc(2026))
    assert r.deduction == 20.0  # 20% × 100, no $400 floor (not an applicable taxpayer)
    assert r.min_deduction_applied is False


def test_q9_2026_null_minimum_raises_rather_than_guessing():
    """If the 2026 minimum constant is null (unverified), the engine raises.

    Monkeypatch min_deduction → None in memory; the engine reads it via
    get_constant (no default) and raises UnverifiedConstantError rather than
    silently skipping the minimum (ground rule 6 / plan §3.1.6).
    """
    c = yc(2026)
    c["qbi"]["min_deduction"] = None
    prof = dict(adjusted_qbi=1500, taxable_income_before_qbi=50000,
                filing_status="single")
    with pytest.raises(utils.UnverifiedConstantError):
        qbi.compute_qbi_deduction(prof, c)


# ---------------------------------------------------------------------------
# Q10 — MFS threshold mapping (explicit, no .get fallback)
# ---------------------------------------------------------------------------
def test_q10_mfs_2026_threshold_differs_from_single():
    """OBBBA split MFS from single for 2026: MFS $201,775 ≠ single $201,750."""
    c = yc(2026)
    assert qbi.qbi_threshold("mfs", c) == 201775.0
    assert qbi.qbi_threshold("single", c) == 201750.0
    assert qbi.qbi_threshold("mfs", c) != qbi.qbi_threshold("single", c)


def test_q10_mfs_2025_equals_single():
    """2023–2025: MFS uses the single threshold (no separate MFS figure)."""
    c = yc(2025)
    assert qbi.qbi_threshold("mfs", c) == qbi.qbi_threshold("single", c) == 197300.0


def test_q10_missing_mfs_threshold_raises_no_get_fallback():
    """Deleting threshold_mfs must raise, not silently fall back to single.

    This is the Q10 regression guard against the D2 .get-fallback pattern: a
    year that should carry a distinct MFS threshold but is missing it must
    fail loudly rather than reuse the single figure.
    """
    c = yc(2026)
    del c["qbi"]["threshold_mfs"]
    with pytest.raises(KeyError):
        qbi.qbi_threshold("mfs", c)


# ---------------------------------------------------------------------------
# Q11 — K-1 pass-through rejection (out of scope)
# ---------------------------------------------------------------------------
def test_q11_k1_rejected_with_limitations_pointer():
    """A profile carrying k1_qbi is rejected with a message citing limitations.md."""
    prof = dict(adjusted_qbi=50000, k1_qbi=20000,
                taxable_income_before_qbi=100000, filing_status="single")
    with pytest.raises(ValueError, match="limitations.md"):
        qbi.compute_qbi_deduction(prof, yc(2025))


def test_q11_k1_cli_exits_nonzero():
    """The CLI rejects a --k1-qbi profile with a nonzero exit code."""
    env = dict(os.environ, PYTHONPATH=os.path.abspath(SCRIPTS_DIR))
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "qbi_calculator.py"),
         "--se-net-profit", "50000", "--filing-status", "single",
         "--taxable-income-before-qbi", "100000", "--k1-qbi", "20000",
         "--year", "2025"],
        capture_output=True, text=True, env=env, cwd=os.path.abspath(SCRIPTS_DIR),
    )
    assert proc.returncode != 0
    assert "limitations.md" in proc.stderr


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------
def test_cli_json_output():
    env = dict(os.environ, PYTHONPATH=os.path.abspath(SCRIPTS_DIR))
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "qbi_calculator.py"),
         "--adjusted-qbi", "80000", "--taxable-income-before-qbi", "150000",
         "--filing-status", "mfj", "--year", "2025", "--output-format", "json"],
        capture_output=True, text=True, env=env, cwd=os.path.abspath(SCRIPTS_DIR),
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["deduction"] == 16000.0
    assert data["form"] == "8995"


def test_cli_sstb_help():
    env = dict(os.environ, PYTHONPATH=os.path.abspath(SCRIPTS_DIR))
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "qbi_calculator.py"),
         "--sstb-help"],
        capture_output=True, text=True, env=env, cwd=os.path.abspath(SCRIPTS_DIR),
    )
    assert proc.returncode == 0
    assert "§1.199A-5" in proc.stdout
    assert "reputation or skill" in proc.stdout