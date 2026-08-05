"""C4: CA 529 non-conformity — data and the earnings calculator.

California does not conform to several federal expansions of IRC 529 "qualified"
expenses. The earnings portion of a federally-qualified-but-CA-non-qualified
distribution is includable in CA income (Schedule CA (540) line 8z) AND subject
to a 2.5% CA additional tax (Form FTB 3805P). CA does NOT conform to K-12
expenses or 529-to-Roth IRA rollovers; CA DOES conform to apprenticeship and
student-loan-repayment expansions (R&TC 17140.3(e), effective 2021), so those
are NOT in the CA-non-qualified list.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from utils import load_state_constants, load_tax_constants, ca_529_nonqualified_withdrawal
from credit_eligibility_checker import get_credit_params
from draft_reviewer import ReviewContext, Severity, check_529
from savings_rules import RULES


def test_ca_529_block_shape():
    p = load_state_constants("CA", "2025")["nonconformity"]["plan_529"]
    assert p["state_deduction_for_contributions"] is False
    assert p["additional_tax_rate_on_nonqualified_earnings"] == 0.025
    assert any("K-12" in x for x in p["federal_qualified_but_ca_nonqualified"])


def test_ca_529_nonqualified_list_is_correct():
    """Lock in the primary-source correction: only K-12 and Roth rollovers are
    CA-non-qualified. Apprenticeship and student-loan repayment ARE conformed
    (R&TC 17140.3(e)) and must NOT appear in the non-qualified list."""
    p = load_state_constants("CA", "2025")["nonconformity"]["plan_529"]
    nq = p["federal_qualified_but_ca_nonqualified"]
    # Exactly two CA-non-qualified purposes.
    assert len(nq) == 2
    joined = " | ".join(nq).lower()
    assert "k-12" in joined
    assert "roth ira" in joined
    # The conformed expansions must NOT be mis-filed as non-qualified.
    assert "apprenticeship" not in joined
    assert "student loan" not in joined and "loan repayment" not in joined
    # And they are documented in the conformed list.
    conformed = " | ".join(p["ca_conformed_federal_expansions"]).lower()
    assert "apprenticeship" in conformed
    assert "loan repayment" in conformed


# ---------------------------------------------------------------------------
# ca_529_nonqualified_withdrawal (Task 2)
# ---------------------------------------------------------------------------

@pytest.fixture()
def plan529():
    return load_state_constants("CA", "2025")["nonconformity"]["plan_529"]


def test_earnings_taxed_and_penalized(plan529):
    # $10,000 K-12 withdrawal of which $3,000 is earnings: CA taxes the $3,000
    # as income AND adds 2.5% x $3,000 = $75.
    r = ca_529_nonqualified_withdrawal(10000, 3000, plan529)
    assert r["ca_taxable_earnings"] == 3000
    assert r["additional_tax"] == 75.0


def test_zero_earnings_zero_tax(plan529):
    r = ca_529_nonqualified_withdrawal(10000, 0, plan529)
    assert r["ca_taxable_earnings"] == 0 and r["additional_tax"] == 0


def test_earnings_cannot_exceed_withdrawal(plan529):
    with pytest.raises(ValueError):
        ca_529_nonqualified_withdrawal(5000, 6000, plan529)


def test_negative_inputs_rejected(plan529):
    with pytest.raises(ValueError):
        ca_529_nonqualified_withdrawal(-1000, 0, plan529)
    with pytest.raises(ValueError):
        ca_529_nonqualified_withdrawal(1000, -100, plan529)


# ---------------------------------------------------------------------------
# C4.3a — draft reviewer check_529
# ---------------------------------------------------------------------------

_TD_2025 = load_tax_constants("2025")


def _ctx(year="2025"):
    """A ReviewContext pinned to `year`; only year/exact_tolerance are read by
    check_529 (the rest are populated from the real 2025 constants so the
    dataclass is valid)."""
    td = _TD_2025
    return ReviewContext(
        year=year, tax_brackets=td["tax_brackets_processed"],
        ltcg_brackets=td["ltcg_brackets_processed"],
        standard_deductions=td["standard_deductions"],
        additional_deductions=td["additional_deduction_65_blind"],
        ctc_params=get_credit_params(td)["child_tax_credit"],
        salt_constants=td["salt"], capital_loss_limit=td["capital_loss_limit"],
        capital_loss_limit_mfs=td["capital_loss_limit_mfs"],
        ss_wage_base=td["se_tax"]["ss_wage_base"],
        se_tax_factor=td["se_tax"]["se_adjustment_factor"],
        se_ss_rate=td["se_tax"]["ss_rate"],
        se_medicare_rate=td["se_tax"]["medicare_rate"],
        add_medicare_threshold_single=td["se_tax"]["additional_medicare_threshold_single"],
        add_medicare_threshold_mfj=td["se_tax"]["additional_medicare_threshold_married"],
        hsa_limit_self=td["retirement_limits"]["hsa"]["single"],
        hsa_limit_family=td["retirement_limits"]["hsa"]["family"],
        charitable_cash_limit=td["credits"]["charitable_cash_limit"],
        charitable_property_limit=td["charitable_property_agi_limit"],
        educator_limit=td["educator_expense_limit"],
        student_loan_limit=td["student_loan_interest_limit"],
        year_constants=td)


_W = {"withdrawal": 10000, "earnings": 3000}


def test_check_529_omitted_addback_is_error():
    """CA draft omits the earnings add-back for a declared K-12 529 withdrawal."""
    draft = {"form_540": {}}  # no line_529_earnings_addback
    computed = {"state": "CA", "plan_529_withdrawal_ca_nonqualified": _W}
    findings = check_529(draft, computed, _ctx("2025"))
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR
    msg = findings[0].message
    assert "$3,000.00" in msg       # the earnings add-back amount
    assert "$75.00" in msg          # the 2.5% additional tax


def test_check_529_present_addback_is_ok():
    """CA draft reports the correct earnings add-back -> OK finding."""
    draft = {"form_540": {"line_529_earnings_addback": 3000}}
    computed = {"state": "CA", "plan_529_withdrawal_ca_nonqualified": _W}
    findings = check_529(draft, computed, _ctx("2025"))
    assert len(findings) == 1
    assert findings[0].severity == Severity.OK
    assert "$75.00" in findings[0].message


def test_check_529_non_ca_no_findings():
    computed = {"state": "TX", "plan_529_withdrawal_ca_nonqualified": _W}
    assert check_529({"form_540": {}}, computed, _ctx("2025")) == []


def test_check_529_no_withdrawal_no_findings():
    computed = {"state": "CA"}  # no plan_529_withdrawal_ca_nonqualified
    assert check_529({"form_540": {}}, computed, _ctx("2025")) == []


def test_check_529_year_without_plan529_skips_gracefully():
    """2023/2024 have no plan_529 sub-block (C4 is 2025-scoped); a declared
    withdrawal must not crash the reviewer — it skips with no findings."""
    computed = {"state": "CA", "plan_529_withdrawal_ca_nonqualified": _W}
    assert check_529({"form_540": {}}, computed, _ctx("2023")) == []


# ---------------------------------------------------------------------------
# C4.3b — savings-engine caveat rule (rule_529_ca_caveat)
# ---------------------------------------------------------------------------

def _profile(**over):
    p = {"filing_status": "single", "agi": 100000, "taxable_income": 80000}
    p.update(over)
    return p


def test_rule_529_ca_fires_with_dollar_exposure():
    ops = RULES["plan_529_ca"](
        _profile(state="CA", plan_529_k12_withdrawals=10000,
                 plan_529_k12_earnings=3000), _TD_2025)
    assert len(ops) == 1
    op = ops[0]
    assert op.rule_id == "plan_529_ca"
    assert op.savings_federal is None  # zero-savings caveat, not a savings claim
    cav = op.state_caveat
    assert "$3,000.00" in cav   # CA-taxable earnings
    assert "$75.00" in cav      # 2.5% additional tax
    assert "K-12" in cav


def test_rule_529_tx_does_not_fire():
    ops = RULES["plan_529_ca"](
        _profile(state="TX", plan_529_k12_withdrawals=10000,
                 plan_529_k12_earnings=3000), _TD_2025)
    assert ops == []


def test_rule_529_ca_zero_withdrawal_does_not_fire():
    ops = RULES["plan_529_ca"](
        _profile(state="CA", plan_529_k12_withdrawals=0,
                 plan_529_k12_earnings=3000), _TD_2025)
    assert ops == []


def test_rule_529_ca_no_earnings_fires_caveat_unquantified():
    """Earnings=0 still fires the caveat (the withdrawal is CA-non-qualified)
    but without a dollar quantification line."""
    ops = RULES["plan_529_ca"](
        _profile(state="CA", plan_529_k12_withdrawals=10000,
                 plan_529_k12_earnings=0), _TD_2025)
    assert len(ops) == 1
    assert ops[0].savings_federal is None
    # No dollar-exposure sentence (earnings == 0).
    assert "$75.00" not in (ops[0].state_caveat or "")
    assert "K-12" in (ops[0].state_caveat or "")