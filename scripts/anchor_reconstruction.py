#!/usr/bin/env python3
"""Carry a dated balance across a ledger. Pure arithmetic, no I/O, no policy.

This module exists because two techniques used repeatedly in live FBAR work had
no name in the pipeline: a bank-confirmed balance walked backward through a
trailing statement to land on a year boundary, and a year replayed forward from
that boundary to give an exact opening, maximum and closing.

**Why this is not "synthesizing a balance from transaction deltas".** The rule
`statement_parser` enforces is that a ledger yields CHANGES, not LEVELS — a
register alone can never say what the account held. That rule is intact here:
every function takes a level as INPUT, from a document that stated it. What is
computed is the level at a different date, which is arithmetic over a cited
observation, not an invention. The caller is responsible for the other half —
that the ledger between the two dates is COMPLETE — and `statement_parser`
proves that from the register's own stated totals before calling in (see
`_assert_register_reconciles`).

**Conservation is what makes an anchor trustworthy.** `final = opening + Σ
movements` holds under every permutation of the movements, so an anchor's
correctness is judged by whether the replayed total matches an independently
known figure — never by whether some assumed intra-day order produced a
plausible-looking path. `replay_year` reports a negative excursion rather than
resolving it, for exactly that reason: see references/fbar_guide.md §9, "A
replayed series that dips below zero".
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple


class ReconstructionError(ValueError):
    """Raised when a reconstruction's inputs cannot support the arithmetic."""


@dataclass(frozen=True)
class Anchor:
    """One level, at one date, from one document that stated it."""

    when: date
    balance: Decimal
    source_file: str


@dataclass
class Replay:
    """What a full-year replay from a known opening established."""

    opening: Decimal
    closing: Decimal
    maximum: Decimal
    date_of_maximum: Optional[date]
    minimum: Decimal
    #: Spans where the running balance went below zero. Informational: an
    #: account cannot actually go negative, so an excursion is either a
    #: same-timestamp ordering artefact (`same_timestamp: True`, harmless) or
    #: evidence the anchor or the ledger is wrong (`False`, not harmless).
    #:
    #: `recovered` says whether the balance came back above zero before the
    #: ledger ended. An UNRECOVERED excursion is never harmless — a same-minute
    #: cluster resolves within the minute — so it is reported as
    #: `same_timestamp: False` however its rows are dated.
    negative_excursions: List[Dict[str, Any]] = field(default_factory=list)


def _amounts(movements: Iterable[Any], where: str) -> List[Any]:
    ordered = sorted(movements, key=lambda m: m.order_key())
    for movement in ordered:
        if movement.amount is None:
            raise ReconstructionError(
                f"{where}: {movement.source_file}:{movement.source_row} states a "
                "balance but no amount. A balance observation is a LEVEL, not a "
                "flow; summing it would double-count the level it records. Filter "
                "balance-only rows out before reconstructing.")
    return ordered


def carry_balance(anchor: Anchor, movements: Iterable[Any], target: date) -> Decimal:
    """The balance at `target`, given the level at `anchor.when`.

    A bank's "balance as of D" is END OF DAY D, so movements dated D are
    already inside it. Walking backward to a date before D removes every
    movement in `(target, anchor.when]`; walking forward to a date after D adds
    every movement in `(anchor.when, target]`. Getting that boundary wrong is a
    one-day error that looks like a rounding difference and is not one.

    Order-invariant by construction: it is a sum.
    """
    ordered = _amounts(movements, "carry_balance")
    if target <= anchor.when:
        delta = sum((m.amount for m in ordered
                     if target < m.date <= anchor.when), Decimal("0"))
        return anchor.balance - delta
    delta = sum((m.amount for m in ordered
                 if anchor.when < m.date <= target), Decimal("0"))
    return anchor.balance + delta


def replay_year(opening: Decimal, movements: Iterable[Any],
                tax_year: int) -> Replay:
    """Walk a year forward from a known opening.

    `opening`, `closing` and the net sum are conservation results and hold
    under any ordering. `maximum` and `date_of_maximum` do depend on the order
    WITHIN a day, which is what `MovementRecord.order_key` fixes and what a
    same-timestamp cluster makes unknowable — hence the excursion report.
    """
    ordered = [m for m in _amounts(movements, "replay_year")
               if m.date.year == tax_year]
    running = opening
    maximum, date_of_maximum = opening, date(tax_year, 1, 1)
    minimum = opening
    excursions: List[Dict[str, Any]] = []
    open_excursion: Optional[Dict[str, Any]] = None

    for movement in ordered:
        running += movement.amount
        if running > maximum:
            maximum, date_of_maximum = running, movement.date
        minimum = min(minimum, running)

        if running < 0 and open_excursion is None:
            open_excursion = {"date": movement.date, "start": movement.date,
                              "end": movement.date, "low": running,
                              "same_timestamp": True, "recovered": False}
        elif running < 0:
            open_excursion["end"] = movement.date
            open_excursion["low"] = min(open_excursion["low"], running)
            if movement.date != open_excursion["start"]:
                # An excursion spanning more than one date cannot be a
                # same-minute batch-processing artefact. That distinction is
                # the whole diagnostic value of this report.
                open_excursion["same_timestamp"] = False
        elif open_excursion is not None:
            if movement.date != open_excursion["start"]:
                open_excursion["same_timestamp"] = False
            open_excursion["recovered"] = True
            excursions.append(open_excursion)
            open_excursion = None

    if open_excursion is not None:
        # The ledger ENDS below zero. Whatever timestamps the rows carry, this
        # is not a batch-processing artefact: a same-minute cluster resolves
        # within the minute, and this one never resolves at all. The account
        # cannot have closed the year owing the bank money it never repaid, so
        # the anchor or the ledger is wrong and the figures are NOT safe.
        # Leaving `same_timestamp` True here made the caller print the
        # reassuring message for the one case that warrants the alarming one.
        open_excursion["same_timestamp"] = False
        open_excursion["recovered"] = False
        excursions.append(open_excursion)

    return Replay(opening=opening, closing=running, maximum=maximum,
                  date_of_maximum=date_of_maximum, minimum=minimum,
                  negative_excursions=excursions)


def non_negativity_floor(movements: Iterable[Any],
                         tax_year: int) -> Tuple[Decimal, Decimal]:
    """The smallest opening — and resulting maximum — the ledger can be true with.

    The opening is unknown, but an asset account cannot go negative. The
    deepest point the cumulative sum reaches is therefore how much the account
    must already have held at the start, and replaying from that opening gives
    a PROVEN FLOOR on the true maximum.

    This is not an estimate and must not be recorded as one: it is a bound with
    a known direction of error (it can only understate). `max_value_basis`
    `non_negativity_floor` exists to say exactly that, and V12 warns in those
    terms rather than calling it a guess.
    """
    ordered = _amounts(movements, "non_negativity_floor")
    ordered = [m for m in ordered if m.date.year == tax_year]

    running = Decimal("0")
    trough = Decimal("0")
    for movement in ordered:
        running += movement.amount
        trough = min(trough, running)

    opening = -trough if trough < 0 else Decimal("0")
    return opening, replay_year(opening, ordered, tax_year).maximum
