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
from utils import load_state_constants


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