"""D1+D2: Form 8949 column (g) sign and code-B gating for RSU/ESPP.

D1 — column (g) is the *signed* adjustment to gain/loss:
    column (g) = reported_basis − correct_basis
    (negative when the broker under-reported basis, the typical RSU
    double-taxation case; positive when the broker over-reported).

D2 — Form 8949 code "B" is gated to the case where the broker actually
    *reported a wrong basis*. When the 1099-B reports NO basis (noncovered
    shares, Box B/E), the correct basis is entered directly in column (e)
    and NO adjustment code is used. The previous implementation wrongly
    treated `reported_basis_1099b == 0` as "needs code B".
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from rsu_calculator import VestingLot, calculate_lot_sale


def make_lot(**kw):
    """Build a VestingLot with the real constructor signature.

    `cost_basis_per_share` and `shares_remaining` are derived in
    `__post_init__` (not constructor params), so they are omitted here.
    100 shares vested at $50 FMV -> $5,000 correct cost basis.
    """
    defaults = dict(vesting_date="2024-03-15", shares_vested=100,
                    fmv_at_vesting=50.0)
    defaults.update(kw)
    return VestingLot(**defaults)


# ---------------------------------------------------------------------------
# D1: signed column (g)
# ---------------------------------------------------------------------------

def test_broker_underreported_basis_negative_column_g():
    # Correct basis 100 x $50 = $5,000; broker reported $1,000 (a WRONG
    # nonzero basis). Column (g) = reported - correct = 1,000 - 5,000 = -4,000.
    r = calculate_lot_sale(make_lot(), 100, 60.0, "2025-06-01",
                           reported_basis_1099b=1000.0)
    assert r.form_8949_code == "B"
    assert r.column_g_adjustment == -4000.0
    assert r.basis_missing_on_1099b is False
    # Legacy field (correct - reported) unchanged in the wrong-basis case.
    assert r.adjustment_amount == 4000.0


def test_broker_overreported_basis_positive_column_g():
    # Broker reported $6,000 (over the $5,000 correct basis).
    # Column (g) = 6,000 - 5,000 = +1,000 (a gain-increasing adjustment).
    r = calculate_lot_sale(make_lot(), 100, 60.0, "2025-06-01",
                           reported_basis_1099b=6000.0)
    assert r.form_8949_code == "B"
    assert r.column_g_adjustment == 1000.0
    assert r.basis_missing_on_1099b is False


def test_matching_basis_no_code():
    # Broker reported the correct basis exactly -> no adjustment at all.
    r = calculate_lot_sale(make_lot(), 100, 60.0, "2025-06-01",
                           reported_basis_1099b=5000.0)
    assert r.form_8949_code == ""
    assert r.column_g_adjustment == 0.0
    assert r.basis_adjustment_needed is False
    assert r.basis_missing_on_1099b is False


# ---------------------------------------------------------------------------
# D2: no code when basis is unreported (noncovered shares)
# ---------------------------------------------------------------------------

def test_missing_basis_no_code_no_adjustment():
    """D2: 1099-B reported NO basis (noncovered, Box B/E). Correct basis
    goes straight into column (e); no code, no column (g) entry."""
    r = calculate_lot_sale(make_lot(), 100, 60.0, "2025-06-01",
                           reported_basis_1099b=0)
    assert r.form_8949_code == ""
    assert r.column_g_adjustment == 0.0
    assert r.basis_missing_on_1099b is True
    assert r.correct_basis == 5000.0     # still surfaced for column (e)
    assert r.basis_adjustment_needed is False
    assert r.adjustment_amount == 0.0


def test_none_basis_behaves_like_missing():
    """A falsy/missing reported basis (None or 0) must behave identically:
    no code, basis_missing flagged, correct basis surfaced."""
    r_none = calculate_lot_sale(make_lot(), 100, 60.0, "2025-06-01",
                                reported_basis_1099b=0)
    # The default param is 0, so an explicit 0 is the "missing" path.
    assert r_none.basis_missing_on_1099b is True
    assert r_none.form_8949_code == ""


# ---------------------------------------------------------------------------
# Task 2: ESPP mirror (added in D12.2)
# ---------------------------------------------------------------------------