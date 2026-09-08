#!/usr/bin/env python3
"""The anchored-reconstruction algebra (docs/fbar_enhancements_plan.md, F1/F2/F6).

The properties that matter are conservation properties. `carry_balance` and the
opening/closing it produces are INVARIANT under any reordering of movements
within a day, which is the reason a same-timestamp cluster cannot invalidate an
anchor. The maximum is not invariant, which is why a negative excursion inside
such a cluster is reported and not silently smoothed.

All fixtures synthetic.
"""

import os
import sys
from datetime import date
from decimal import Decimal

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from anchor_reconstruction import (  # noqa: E402
    Anchor,
    carry_balance,
    non_negativity_floor,
    replay_year,
)
from statement_parser import MovementRecord  # noqa: E402


def mv(day, amount, order=0, month=1):
    return MovementRecord(
        date=date(2025, month, day), description="txn", currency="RUB",
        source_file="reg.pdf", source_row=order + 1,
        amount=Decimal(str(amount)), balance_after=None, row_order=order)


def test_carry_balance_walks_backward_to_an_earlier_date():
    """Balance on Mar 15 minus the movements Jan 1..Mar 15 is the Jan 1 opening."""
    anchor = Anchor(when=date(2025, 3, 15), balance=Decimal("1000"), source_file="bal.pdf")
    movements = [mv(10, 500, month=1), mv(20, -200, month=2)]
    assert carry_balance(anchor, movements, date(2025, 1, 1)) == Decimal("700")


def test_carry_balance_walks_forward_to_a_later_date():
    anchor = Anchor(when=date(2025, 1, 1), balance=Decimal("700"), source_file="bal.pdf")
    movements = [mv(10, 500, month=1), mv(20, -200, month=2)]
    assert carry_balance(anchor, movements, date(2025, 12, 31)) == Decimal("1000")


def test_carry_balance_is_invariant_under_reordering():
    """Conservation is the whole basis for trusting an anchor. Sum is a sum."""
    anchor = Anchor(when=date(2025, 3, 15), balance=Decimal("1000"), source_file="bal.pdf")
    forward = [mv(10, 500, order=0, month=1), mv(10, -200, order=1, month=1)]
    reversed_ = [mv(10, -200, order=0, month=1), mv(10, 500, order=1, month=1)]
    assert carry_balance(anchor, forward, date(2025, 1, 1)) == \
        carry_balance(anchor, reversed_, date(2025, 1, 1))


def test_carry_balance_excludes_the_anchor_date_movements_boundary():
    """A movement ON the anchor date is already reflected in the stated balance.

    A bank's 'balance as of D' is end-of-day D. Walking backward to a date
    before D must therefore remove D's own movements; walking forward to a date
    after D must not re-add them.
    """
    anchor = Anchor(when=date(2025, 3, 15), balance=Decimal("1000"), source_file="bal.pdf")
    movements = [mv(15, 100, month=3)]
    assert carry_balance(anchor, movements, date(2025, 3, 14)) == Decimal("900")
    assert carry_balance(anchor, movements, date(2025, 3, 15)) == Decimal("1000")


def test_replay_year_reports_opening_closing_and_maximum():
    result = replay_year(Decimal("100"), [mv(10, 400), mv(20, -300)], 2025)
    assert result.opening == Decimal("100")
    assert result.maximum == Decimal("500")
    assert result.date_of_maximum == date(2025, 1, 10)
    assert result.closing == Decimal("200")


def test_replay_year_reports_a_same_timestamp_negative_excursion():
    """Four same-day rows dip the running balance negative in this order.

    Opening, closing and — here — the maximum are unaffected. The excursion is
    reported so a reviewer sees it, and is NOT resolved by reordering.
    """
    movements = [mv(10, -500, order=0), mv(10, -400, order=1),
                 mv(10, 1000, order=2), mv(10, 200, order=3)]
    result = replay_year(Decimal("100"), movements, 2025)
    assert result.closing == Decimal("400")
    assert len(result.negative_excursions) == 1
    excursion = result.negative_excursions[0]
    assert excursion["same_timestamp"] is True
    assert excursion["date"] == date(2025, 1, 10)


def test_replay_year_marks_a_multi_day_excursion_as_not_a_tie_artefact():
    """An excursion spanning two dates cannot be a same-minute batch artefact,
    and means the anchor or the ledger is actually wrong."""
    movements = [mv(10, -500, order=0), mv(11, 1000, order=0)]
    result = replay_year(Decimal("100"), movements, 2025)
    assert result.negative_excursions[0]["same_timestamp"] is False


def test_replay_year_closing_is_invariant_under_reordering():
    forward = [mv(10, -500, order=0), mv(10, 1000, order=1)]
    reversed_ = [mv(10, 1000, order=0), mv(10, -500, order=1)]
    assert replay_year(Decimal("100"), forward, 2025).closing == \
        replay_year(Decimal("100"), reversed_, 2025).closing


def test_non_negativity_floor_is_the_smallest_consistent_opening():
    """The deepest cumulative drawdown is how much the account must have held.

    Movements -500 then +1000: the running sum bottoms at -500, so the opening
    was at least 500 and the maximum at least 500 + 500 = 1000.
    """
    opening, maximum = non_negativity_floor([mv(10, -500), mv(20, 1000)], 2025)
    assert opening == Decimal("500")
    assert maximum == Decimal("1000")


def test_non_negativity_floor_on_a_never_negative_ledger_is_zero():
    """A ledger that only ever adds proves nothing about the opening."""
    opening, maximum = non_negativity_floor([mv(10, 100), mv(20, 200)], 2025)
    assert opening == Decimal("0")
    assert maximum == Decimal("300")


def test_carry_balance_refuses_a_movement_with_no_amount():
    """A balance observation is not a flow; summing it would double-count."""
    from anchor_reconstruction import ReconstructionError
    anchor = Anchor(when=date(2025, 3, 15), balance=Decimal("1000"), source_file="b.pdf")
    balance_row = MovementRecord(date=date(2025, 1, 5), description="opening",
                                 currency="RUB", source_file="reg.pdf", source_row=1,
                                 amount=None, balance_after=Decimal("5"))
    with pytest.raises(ReconstructionError):
        carry_balance(anchor, [balance_row], date(2025, 1, 1))
