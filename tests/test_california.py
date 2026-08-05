#!/usr/bin/env python3
"""California engine tests — findings C1–C7 and the PHASE 3 ACCEPTANCE GATE.

The audit scored California compliance 12/100: there was effectively no CA
engine. `state_tax_rates.CA = 0.133` treated the top marginal rate INCLUDING
the Mental Health Services Tax as a flat rate.

Primary sources (all retrieved 2026-07-30):
  FTB, 2025 California Tax Rate Schedules,
    https://www.ftb.ca.gov/forms/2025/2025-540-tax-rate-schedules.pdf
  FTB, 2025 Form 540 + booklet,
    https://www.ftb.ca.gov/forms/2025/2025-540.pdf
  EDD, 2025 California Employer's Guide (DE 44),
    https://edd.ca.gov/siteassets/files/pdf_pub_ctr/de44.pdf
  FTB, 2025 Instructions for Form 540-ES,
    https://www.ftb.ca.gov/forms/2025/2025-540-es-instructions.html
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

from tax_calculator import calculate_california_tax  # noqa: E402
from utils import load_state_constants  # noqa: E402

CA_2025 = load_state_constants("CA", "2025")


def ca(**kw):
    params = dict(federal_agi=200000, filing_status="married_jointly", state_data=CA_2025)
    params.update(kw)
    return calculate_california_tax(**params)


# ---------------------------------------------------------------------------
# C1 — THE PHASE 3 GATE: progressive brackets, not a flat 13.3%
# ---------------------------------------------------------------------------

def test_gate_c1_200k_mfj_taxable_income_is_low_ten_thousands():
    """PHASE 3 GATE (C1): 2025 CA tax on $200,000 MFJ TAXABLE income.

    FTB Schedule Y: $200,000 falls in the 145,448–742,958 band at 9.30%.
        tax = 6,403.94 + 9.30% x (200,000 - 145,448 = 54,552)
            = 6,403.94 + 5,073.34
            = 11,477.28

    The old flat 13.3% rate produced 0.133 x 200,000 = $26,600 — a 132%
    overstatement that poisoned every RSU withholding and estimated-payment
    recommendation for CA filers.
    """
    # Feed taxable income directly by zeroing the deduction.
    result = calculate_california_tax(
        federal_agi=200000 + CA_2025["standard_deduction"]["married_jointly"],
        filing_status="married_jointly", state_data=CA_2025,
    )
    assert result["ca_taxable_income"] == 200000

    tax = result["ca_tax_before_credits"]
    assert tax == pytest.approx(11477.28, abs=0.01)

    # The falsifiable claim from the gate: low-$10Ks, NOT ~$26,600.
    assert 10000 <= tax < 13000, f"expected low-$10K range, got {tax}"
    assert abs(tax - 26600) > 10000, "still behaving like the old flat 13.3% rate"


def test_top_bracket_is_12_3_percent_not_13_3():
    """C1: 13.3% is the top marginal rate INCLUDING the 1% BHST, not a bracket."""
    for status in ("single", "married_jointly", "head_of_household"):
        assert CA_2025["brackets"][status][-1]["rate"] == 0.123


def test_mfj_bracket_boundaries_are_exactly_double_single():
    """R&TC 17041: the MFJ table is the single table doubled.

    A structural invariant that catches a transcription slip anywhere in either
    of the two nine-row tables at once.
    """
    single = [b["top"] for b in CA_2025["brackets"]["single"][:-1]]
    joint = [b["top"] for b in CA_2025["brackets"]["married_jointly"][:-1]]
    assert joint == [2 * s for s in single]


def test_bracket_cumulative_totals_match_ftb_published_bases():
    """Transcription check against the FTB schedule's own cumulative amounts.

    The Schedule Y rows publish the tax at each boundary. Summing our brackets
    must reproduce them exactly:
        22,158 -> 221.58    52,528 -> 828.98    82,904 -> 2,044.02
        115,084 -> 3,974.82  145,448 -> 6,403.94
    """
    published = {22158: 221.58, 52528: 828.98, 82904: 2044.02,
                 115084: 3974.82, 145448: 6403.94}
    brackets = CA_2025["brackets"]["married_jointly"]

    for boundary, expected in published.items():
        total, prev = 0.0, 0
        for b in brackets:
            top = b["top"] if b["top"] is not None else float("inf")
            amount = min(boundary, top) - prev
            if amount <= 0:
                break
            total += amount * b["rate"]
            prev = top
        assert total == pytest.approx(expected, abs=0.01), f"at boundary {boundary}"


# ---------------------------------------------------------------------------
# PHASE 3 GATE — the worked example in references/california_tax_guide.md
# ---------------------------------------------------------------------------

def test_gate_worked_example_matches_the_guide():
    """PHASE 3 GATE: the guide's worked MFJ W-2 + RSU + HSA example.

    Filer: MFJ, California resident.
      W-2 Box 1 wages (incl. $50,000 of RSU vesting income)   250,000
      HSA family contribution via payroll (excluded from Box 1)  8,550

    Federal AGI                                               250,000
    CA AGI = 250,000 + 8,550 HSA add-back                     258,550
      (California does not allow the HSA deduction — finding C2)
    CA standard deduction, MFJ                                 11,412
    CA taxable income                                         247,138

    CA tax, FTB Schedule Y, bracket by bracket:
         1.0% x  22,158                    =    221.58
         2.0% x  30,370                    =    607.40
         4.0% x  30,376                    =  1,215.04
         6.0% x  32,180                    =  1,930.80
         8.0% x  30,364                    =  2,429.12   (cum 6,403.94)
         9.3% x 101,690                    =  9,457.17
      CA tax before credits                = 15,861.11
      Cross-check against the FTB schedule row:
         6,403.94 + 9.30% x (247,138 - 145,448) = 15,861.11 ✓

    Less exemption credit (2 personal x $153)  =   306.00
      (federal AGI 250,000 < 252,203, so no phase-out)
    CA TAX                                     = 15,555.11

    Behavioral Health Services Tax: taxable income < $1,000,000 -> $0.
    """
    r = ca(federal_agi=250000, hsa_contributions=8550)

    assert r["ca_agi"] == 258550.00
    assert r["ca_standard_deduction"] == 11412
    assert r["ca_taxable_income"] == 247138.00
    assert r["ca_tax_before_credits"] == pytest.approx(15861.11, abs=0.01)
    assert r["exemption_credit"] == 306
    assert r["behavioral_health_tax"] == 0
    assert r["ca_tax"] == pytest.approx(15555.11, abs=0.01)


def test_gate_worked_example_end_to_end_via_cli():
    """PHASE 3 GATE: the same example through the CLI, end to end."""
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "tax_calculator.py"),
         "--gross-income", "250000", "--filing-status", "mfj", "--year", "2025",
         "--state", "CA", "--hsa-contributions", "8550", "--output-format", "json"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)["state"]

    assert state["ca_agi"] == 258550.00
    assert state["ca_taxable_income"] == 247138.00
    assert state["ca_tax"] == pytest.approx(15555.11, abs=0.01)


# ---------------------------------------------------------------------------
# C2 — HSA non-conformity
# ---------------------------------------------------------------------------

def test_hsa_contributions_added_back_for_ca():
    """C2: California taxes HSA contributions; federal excludes them."""
    without = ca(hsa_contributions=0)
    with_hsa = ca(hsa_contributions=8550)

    assert with_hsa["ca_agi"] - without["ca_agi"] == 8550
    assert with_hsa["ca_tax"] > without["ca_tax"]

    adj = next(a for a in with_hsa["adjustments"] if "HSA" in a["item"])
    assert adj["direction"] == "add-back"
    assert "does not allow" in adj["reason"]


def test_hsa_earnings_flagged_as_ca_taxable():
    """C2: interest/dividends inside an HSA are CA-taxable each year."""
    assert CA_2025["nonconformity"]["hsa_earnings_taxable"] is True
    assert CA_2025["nonconformity"]["hsa_deductible"] is False


# ---------------------------------------------------------------------------
# C3 — QBI, OBBBA and capital-gains non-conformity
# ---------------------------------------------------------------------------

def test_qbi_deduction_added_back_for_ca():
    """C3: California has no IRC 199A deduction."""
    r = ca(qbi_deduction=20000)
    assert r["ca_agi"] == 220000
    assert any("business income" in a["item"] for a in r["adjustments"])


def test_gate_g2_compute_qbi_one_flow_federal_and_ca():
    """G2: --compute-qbi computes §199A once, reduces federal taxable income
    (NOT AGI), and feeds the SAME figure into the CA add-back automatically.

    MFJ 2025, gross $300,000. Standard deduction $31,500 → taxable income
    before QBI = $268,500. Schedule C $100,000 with $10,000 SE health → adjusted
    QBI $82,935.23; below the MFJ threshold → Form 8995 → component 20% =
    $16,587.05. Federal taxable income becomes $268,500 − $16,587.05; AGI stays
    $300,000. CA adds back exactly $16,587.05 → CA AGI $316,587.05. One flow,
    no double entry.
    """
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "tax_calculator.py"),
         "--gross-income", "300000", "--filing-status", "mfj", "--year", "2025",
         "--compute-qbi", "--se-net-profit", "100000",
         "--se-health-insurance", "10000", "--state", "CA",
         "--output-format", "json"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    d = json.loads(result.stdout)
    qbi = d["qbi"]["deduction"]
    assert qbi == 16587.05
    assert d["qbi"]["form"] == "8995"
    # QBI reduces taxable income, NOT AGI.
    assert d["calculations"]["adjusted_gross_income"] == 300000.0
    assert d["calculations"]["taxable_income"] == round(268500 - 16587.05, 2)
    # The same figure is added back for CA automatically (one flow).
    assert d["state"]["ca_agi"] == round(300000 + 16587.05, 2)
    qbi_adj = next(a for a in d["state"]["adjustments"]
                   if "business income" in a["item"])
    assert qbi_adj["amount"] == 16587.05
    assert qbi_adj["direction"] == "add-back"


def test_gate_g2_compute_qbi_mutually_exclusive_with_manual():
    """Supplying both --compute-qbi and --qbi-deduction is rejected (nonzero exit)."""
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "tax_calculator.py"),
         "--gross-income", "300000", "--filing-status", "mfj", "--year", "2025",
         "--compute-qbi", "--se-net-profit", "100000", "--qbi-deduction", "5000",
         "--output-format", "json"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "mutually exclusive" in (result.stdout + result.stderr)


def test_obbba_deductions_added_back_for_ca():
    """C3: CA conformity date is 2015-01-01, so OBBBA deductions do not exist."""
    r = ca(obbba_deductions=12000)
    assert r["ca_agi"] == 212000
    adj = next(a for a in r["adjustments"] if "OBBBA" in a["item"])
    assert "2015-01-01" in adj["reason"]


def test_ca_does_not_give_capital_gains_a_preferential_rate():
    """C3: CA taxes capital gains as ordinary income — no separate bracket."""
    assert CA_2025["nonconformity"]["capital_gains_preferential"] is False
    assert any("ORDINARY income" in n for n in ca()["notes"])


def test_ca_mortgage_limit_is_more_generous_than_federal():
    """C3: CA still allows $1M of acquisition debt (federal is $750K)."""
    assert CA_2025["nonconformity"]["mortgage_debt_limit"] == 1000000
    assert CA_2025["nonconformity"]["heloc_limit"] == 100000


# ---------------------------------------------------------------------------
# C4 — standard deduction, exemption credits, renter's credit, SALT add-back
# ---------------------------------------------------------------------------

def test_ca_standard_deduction_is_a_fraction_of_federal():
    """C4: $5,706 / $11,412 vs the federal $15,750 / $31,500.

    Because CA's standard deduction is so much smaller, itemizing for CA can be
    correct even when the federal return takes the standard deduction — a
    separate decision the analyzer never made.
    """
    assert CA_2025["standard_deduction"]["single"] == 5706
    assert CA_2025["standard_deduction"]["married_jointly"] == 11412


def test_state_income_tax_excluded_from_ca_itemized():
    """C4: state income tax is not deductible on the California return."""
    r = ca(itemized_deductions=40000, state_income_tax_in_itemized=15000)
    assert r["ca_itemized_deductions"] == 25000
    assert r["using_itemized"] is True
    assert r["ca_deduction_used"] == 25000


def test_ca_itemized_below_standard_falls_back_to_standard():
    r = ca(itemized_deductions=12000, state_income_tax_in_itemized=9000)
    assert r["ca_itemized_deductions"] == 3000
    assert r["using_itemized"] is False
    assert r["ca_deduction_used"] == 11412


def test_exemption_credits_personal_and_dependent():
    """C4: $153 per personal/blind/senior exemption, $475 per dependent."""
    r = ca(dependents=2)
    assert r["exemption_credit"] == 2 * 153 + 2 * 475      # $1,256


def test_exemption_credit_phaseout_flagged_above_threshold():
    """C4: the credit phases out above federal AGI $252,203 — flag, not guess."""
    low = ca(federal_agi=200000)
    high = ca(federal_agi=300000)

    assert low["exemption_credit_phased_out"] is False
    assert high["exemption_credit_phased_out"] is True
    assert "UPPER BOUND" in high["exemption_credit_note"]


def test_renters_credit_applies_below_the_agi_limit():
    """C4: $120 MFJ when CA AGI is at or below $107,988."""
    eligible = ca(federal_agi=90000, is_renter=True)
    too_rich = ca(federal_agi=200000, is_renter=True)
    not_renter = ca(federal_agi=90000, is_renter=False)

    assert eligible["renters_credit"] == 120
    assert too_rich["renters_credit"] == 0
    assert not_renter["renters_credit"] == 0


# ---------------------------------------------------------------------------
# Behavioral Health Services Tax (formerly Mental Health Services Tax)
# ---------------------------------------------------------------------------

def test_bhst_does_not_apply_below_one_million():
    """It is a separate 1% ABOVE $1M, not part of the bracket table."""
    assert ca(federal_agi=500000)["behavioral_health_tax"] == 0


def test_bhst_applies_only_to_the_excess_over_one_million():
    """CA AGI 1,511,412 -> taxable 1,500,000 -> 1% x 500,000 = $5,000."""
    r = ca(federal_agi=1500000 + 11412)
    assert r["ca_taxable_income"] == 1500000
    assert r["behavioral_health_tax"] == 5000.00


def test_bhst_renamed_for_2025():
    """Prop 1 / SB 326 renamed MHST to the Behavioral Health Services Tax."""
    bhst = CA_2025["behavioral_health_tax"]
    assert bhst["name"] == "Behavioral Health Services Tax"
    assert bhst["former_name"] == "Mental Health Services Tax"
    assert bhst["rate"] == 0.01 and bhst["threshold"] == 1000000


# ---------------------------------------------------------------------------
# C5 / C6 — SDI and supplemental withholding
# ---------------------------------------------------------------------------

def test_sdi_rate_has_no_wage_cap():
    """C5: SB 951 removed the SDI taxable wage limit effective 2024-01-01."""
    sdi = CA_2025["sdi"]
    assert sdi["rate"] == 0.012
    assert sdi["wage_cap"] is None
    assert sdi["excess_credit_multiple_employers"] is True


def test_supplemental_withholding_rate_for_rsus():
    """C6: CA withholds 10.23% on bonuses and stock options, 6.6% on other.

    Source: EDD DE 44 — "Bonuses and stock options ... 10.23 percent (.1023)";
    "Other types (such as overtime pay, commissions, sales awards, severance,
    and vacation pay) ... 6.6 percent (.066)". The RSU calculator previously
    defaulted state withholding to a flat 5%.
    """
    sw = CA_2025["supplemental_withholding"]
    assert sw["rsu_bonus_stock_options"] == 0.1023
    assert sw["other"] == 0.066


# ---------------------------------------------------------------------------
# C7 — CA estimated tax differs from federal
# ---------------------------------------------------------------------------

def test_ca_installment_schedule_is_30_40_0_30():
    """C7: CA front-loads installments; the third quarter is zero."""
    assert CA_2025["estimated_tax"]["installment_pct"] == [0.30, 0.40, 0.0, 0.30]
    assert sum(CA_2025["estimated_tax"]["installment_pct"]) == pytest.approx(1.0)


def test_ca_safe_harbor_unavailable_at_one_million_agi():
    """C7: at CA AGI >= $1M the prior-year safe harbor is unavailable."""
    et = CA_2025["estimated_tax"]
    assert et["safe_harbor_unavailable_agi"] == 1000000
    assert et["safe_harbor_unavailable_agi_mfs"] == 500000
    assert et["high_income_agi"] == 150000
    assert et["high_income_agi_mfs"] == 75000


# ---------------------------------------------------------------------------
# C1 — the misleading flat-rate table is gone
# ---------------------------------------------------------------------------

def test_flat_state_rate_table_no_longer_advertises_ca():
    """C1: `state_tax_rates.CA = 0.133` is removed, not merely deprecated.

    Same reasoning as finding F1's salt_cap: a readable wrong value invites
    reintroduction of the bug.
    """
    from utils import load_raw_constants

    raw = load_raw_constants()
    assert "state_tax_rates" not in raw
    table = raw["state_top_marginal_rates"]
    assert table["estimate_only"] is True
    assert "CA" not in table["rates"]
    assert "NOT flat effective rates" in table["_note"]


def test_unsupported_state_year_raises():
    """CA 2023-2025 are populated; 2026 is not yet published by the FTB and
    must raise rather than fall back to an adjacent year's indexed figures."""
    with pytest.raises(ValueError):
        load_state_constants("CA", "2026")


def test_no_income_tax_states_report_zero():
    for code in ("WA", "TX", "FL", "NV"):
        assert load_state_constants(code, "2025")["type"] == "none"


# ---------------------------------------------------------------------------
# C4 — the CA itemize-vs-standard decision in deduction_analyzer
# ---------------------------------------------------------------------------

def run_analyzer(*extra):
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "deduction_analyzer.py"),
         "--agi", "300000", "--filing-status", "mfj", "--year", "2025",
         "--state", "CA", "--output-format", "json", *extra],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_ca_itemize_decision_is_made_separately():
    """C4: the analyzer never made the CA itemize-vs-standard decision.

    CA's standard deduction ($11,412 MFJ) is roughly a third of the federal
    $31,500, so a filer can land on opposite sides of the two decisions.
    """
    payload = run_analyzer("--property-tax", "9000", "--charitable-cash", "6000")
    ca_section = payload["california"]

    assert ca_section["ca_standard_deduction"] == 11412
    # federal itemized 15,000 < 31,500 standard -> take federal standard
    assert payload["comparison"]["use_itemized"] is False
    # but CA itemized 15,000 > 11,412 -> itemize for CA
    assert ca_section["use_itemized_for_ca"] is True
    assert ca_section["differs_from_federal_decision"] is True


def test_ca_excludes_state_income_tax_from_itemized():
    """C4: state income tax is not deductible on the California return."""
    payload = run_analyzer("--property-tax", "14000", "--state-income-tax", "22000")
    ca_section = payload["california"]

    # Federal SALT allows property + income tax up to the cap; CA takes the
    # property tax only.
    assert ca_section["state_income_tax_excluded"] == 22000
    assert ca_section["ca_itemized_total"] == 14000


def test_ca_mortgage_limit_delta_is_quantified():
    """C3/C4: CA allows $1M of acquisition debt where federal allows $750K.

    Interest $40,000 on $950,000 of post-2017 debt:
      federal: 750,000/950,000 x 40,000 = $31,578.95
      CA:      950,000 <= 1,000,000, so the full $40,000
      delta:   $8,421.05 of extra California deduction
    """
    payload = run_analyzer("--mortgage-interest", "40000",
                           "--acquisition-debt", "950000",
                           "--loan-origination-date", "2020-01-01")
    ca_section = payload["california"]

    assert ca_section["ca_mortgage_debt_limit"] == 1000000
    assert ca_section["mortgage_deduction_delta_vs_federal"] == pytest.approx(8421.05, abs=0.05)
