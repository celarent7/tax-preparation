#!/usr/bin/env python3
"""Phase 0 for FBAR: turn bank statements into balance timelines.

`fbar_reconciler.py` reconciles rosters; it does not produce them. The hardest
number on a roster — the **maximum account value during the calendar year** —
was established by reading statements by hand, one institution at a time, and
mechanical extraction done by hand is where copy errors enter.

This module is a deterministic pre-processor that feeds Phase 1, not a
replacement for it. The boundary (docs/statement_parser_plan.md §1.1):

    **The parser reports what the file says; it never decides what the filing
    claims.**

So it opens files, normalizes numbers and dates, builds balance timelines,
detects gaps and continuity breaks, and derives what the evidence *can*
support. It does not decide reportability, ownership, conventions, or the
Item 15a election — those are Claude's, at the Phase 2 user-confirmation gate.
A number the parser cannot support from the file is emitted as **absent with a
reason**, never as a default.

Two rules from `references/fbar_guide.md` constrain the arithmetic:

  * **FX conversion** uses the Treasury Reporting Rate of Exchange for the LAST
    DAY of the calendar year — not the rate on the date the account peaked.
    Because one rate applies to every point in the year, conversion commutes
    with taking the maximum, which is what licenses the multi-currency merge.
    `units_per_usd` is foreign units per ONE USD; USD is `local / units_per_usd`.
  * **Maximum value method**: periodic statements may be relied on only where
    they *fairly reflect* the maximum during the year. Deciding, mechanically,
    whether the files in hand can do that is what `max_value_basis` records.

All money is `Decimal`. Never `float`: these figures are compared for
continuity equality and rounded into a filing.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple


class StatementParseError(ValueError):
    """Raised when a source file cannot be read as the profile describes it.

    Reserved for *unreadable input*, which is the only condition that ends a
    run non-zero (plan §10). A gap, a continuity break or a missing statement
    is a warning carried on the timeline, not a failure — the same severity
    contract as the reconciler, for the same reason: a tool that refuses to run
    on legitimate interim states gets worked around.
    """


# ---------------------------------------------------------------------------
# Number normalization (plan §9.4, §9.12)
# ---------------------------------------------------------------------------

#: Characters that mean "negative" and are not the ASCII hyphen-minus. The
#: motivating case is T-Bank's `Expenses: −1 234,56 ₽`, which uses U+2212 MINUS
#: SIGN. Stripping "everything that is not a digit" turns that into a positive
#: number — a plausible wrong number rather than an error, which is the failure
#: class this module exists to prevent.
MINUS_SIGNS = ("−", "‒", "–", "—", "-")


@dataclass(frozen=True)
class NumberFormat:
    """How one profile writes numbers. Declarative, per §5.3.

    `thousands_seps` is a tuple because a single export can mix them: NFKC
    folds U+00A0 and U+202F to a plain space, but a file may still carry both
    a space and an apostrophe group separator.
    """

    decimal_sep: str = "."
    thousands_seps: Tuple[str, ...] = (",", " ")


#: Space-thousands, comma-decimal, U+2212, `₽` (T-Bank). NFKC folds U+00A0 and
#: U+202F to a plain space before the separators are consulted.
RUSSIAN_NUMBERS = NumberFormat(decimal_sep=",", thousands_seps=(" ",))

#: Comma-thousands, dot-decimal — how the Colombian exports actually render,
#: which is NOT the country's typographic convention. Bound to the observed
#: file, not to the locale.
COLOMBIAN_EXPORT_NUMBERS = NumberFormat(decimal_sep=".", thousands_seps=(",", " "))

#: Dot-decimal with comma or space grouping (TBC's bilingual sheets).
PLAIN_NUMBERS = NumberFormat(decimal_sep=".", thousands_seps=(",", " "))

_DIGITS = frozenset("0123456789")

#: Unicode categories a currency marker can be built from: symbols (`₽`, `$`,
#: `€`, `₾`) and letters (`USD`, `COP`, `руб`, Georgian script).
_CURRENCY_CATEGORIES = frozenset(("Sc", "So", "Lu", "Ll", "Lo", "Lm", "Lt"))


def _strip_currency_markers(text: str) -> str:
    """Remove a currency symbol or code from either END of a numeric string.

    Only the ends: a marker between digits is not a marker.
    """
    def trimmable(ch: str) -> bool:
        return ch.isspace() or unicodedata.category(ch) in _CURRENCY_CATEGORIES

    start, end = 0, len(text)
    while start < end and trimmable(text[start]):
        start += 1
    while end > start and trimmable(text[end - 1]):
        end -= 1
    return text[start:end]


#: Placeholder standing in for a thousands separator while grouping is checked.
_GROUP_MARK = "\x00"


def _assert_grouping(marked: str, fmt: NumberFormat, value: Any, where: str) -> None:
    """Reject a value whose grouping contradicts the profile's separators.

    This is the guard against reading a file with the wrong locale, which is
    the §9.4 hazard in its most dangerous form. `1,234,56` is `1234.56` written
    comma-decimal; read with a comma-THOUSANDS profile the separators simply
    vanish and it becomes `123456` — a hundred times too large, with no error
    anywhere. Real grouping always leaves three digits after each separator, so
    a short final group is proof the profile does not match the file.
    """
    integer_part = marked.split(fmt.decimal_sep)[0]
    groups = integer_part.split(_GROUP_MARK)
    if len(groups) == 1:
        return
    bad = (not 1 <= len(groups[0]) <= 3) or any(len(g) != 3 for g in groups[1:])
    if bad:
        raise StatementParseError(
            f"{where}: {value!r} does not group in threes around "
            f"{fmt.thousands_seps}, so this profile's separators do not match "
            f"the file. Read comma-decimal text with a comma-thousands profile "
            "and the value silently multiplies."
        )


def parse_decimal(value: Any,
                  fmt: NumberFormat = PLAIN_NUMBERS,
                  where: str = "value") -> Optional[Decimal]:
    """Parse one money cell to `Decimal`, or `None` when the cell is absent.

    Three input states, not two (plan §9.12). TBC writes `''` into the unused
    side of `Paid Out` / `Paid In` and `'0'` as a balance:

      * `None` and `''`  -> `None`  (absent; on `balance_after` that means
        "this row states no balance", which is meaningful and is NOT zero)
      * `'0'`, `0`, `'0,00'` -> `Decimal("0")`
      * anything else that is not a number -> `StatementParseError`

    Coercing `''` to `0` is the defect this signature exists to prevent, so
    both absent forms collapse to `None` and only a written zero yields zero.

    Accounting parentheses (`(1,234.56)`) are read as negative. They do not
    appear in the current corpus, but a stripped paren silently flips a sign.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise StatementParseError(f"{where}: a boolean is not a money value ({value!r}).")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # openpyxl hands back floats for numeric cells. `Decimal(str(x))` keeps
        # the value the spreadsheet displayed; `Decimal(x)` would import the
        # binary representation error.
        return Decimal(str(value))

    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not text:
        return None

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()

    for sign in MINUS_SIGNS:
        if text.startswith(sign):
            negative = True
            text = text[len(sign):].strip()
            break
    if text.startswith("+"):
        text = text[1:].strip()
    if not text:
        # A bare dash is how several exports write "nothing here". Absent, not
        # zero — the §9.12 distinction again.
        return None

    # A currency marker sits on one END of the value (`1 234,56 ₽`, `USD 100`),
    # so it is stripped from the ends only. A letter or symbol found BETWEEN
    # digits is not a currency marker — dropping it would turn `1O5` into `15`,
    # which is the plausible-wrong-number failure this module exists to avoid,
    # so the interior is validated strictly.
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    if not any(ch in _DIGITS for ch in text):
        # A cell holding `n/a`, a dash, or a label is not an absent value — it
        # is a column the profile has pointed at wrongly. Say so rather than
        # returning None and letting a missing balance look deliberate.
        raise StatementParseError(
            f"{where}: {value!r} contains no digits, so it is not a money value. "
            "Check the profile's column map against this file."
        )
    text = _strip_currency_markers(text)

    kept = []
    for ch in text:
        if ch in _DIGITS or ch == fmt.decimal_sep:
            kept.append(ch)
        elif ch in fmt.thousands_seps:
            kept.append(_GROUP_MARK)
        else:
            raise StatementParseError(
                f"{where}: cannot read {value!r} as a number — unexpected "
                f"{ch!r}. Profile expects {fmt.decimal_sep!r} as the decimal "
                f"separator and {fmt.thousands_seps} for grouping."
            )
    marked = "".join(kept)
    _assert_grouping(marked, fmt, value, where)
    cleaned = marked.replace(_GROUP_MARK, "").replace(fmt.decimal_sep, ".")

    if not cleaned or cleaned == ".":
        return None
    if cleaned.count(".") > 1:
        raise StatementParseError(
            f"{where}: {value!r} has more than one {fmt.decimal_sep!r}; the "
            "profile's decimal separator does not match this file."
        )
    try:
        parsed = Decimal(cleaned)
    except InvalidOperation as exc:  # pragma: no cover - guarded above
        raise StatementParseError(f"{where}: cannot read {value!r} as a number.") from exc
    return -parsed if negative else parsed


# ---------------------------------------------------------------------------
# Date normalization (plan §9.2, §9.3)
# ---------------------------------------------------------------------------

#: Formats tried, in order, for a fully-qualified date. `D/MM` without a year
#: is handled separately by `resolve_partial_date` — a year cannot be guessed,
#: only inferred from the statement period.
_FULL_DATE_FORMATS = (
    "%Y-%m-%d",     # ISO
    "%Y/%m/%d",     # Bancolombia savings period header
    "%d/%m/%Y",     # Bancolombia card period header
    "%d.%m.%Y",     # T-Bank summary lines
    "%d-%m-%Y",
    "%Y.%m.%d",
)

_PARTIAL_DATE = re.compile(r"^\s*(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*$")


@dataclass(frozen=True)
class StatementPeriod:
    """The span one statement file covers, as the file itself states it.

    Never derived from a filename: T-Bank's `text 3.txt` is October and
    `text 12.txt` is January (plan §9.3). Never derived from a file count
    either — coverage is discovered, not assumed (§4.5).
    """

    start: date
    end: date
    source_file: str

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise StatementParseError(
                f"{self.source_file}: statement period ends {self.end} before it "
                f"starts {self.start}."
            )

    def contains(self, when: date) -> bool:
        return self.start <= when <= self.end

    def years(self) -> Tuple[int, ...]:
        return tuple(range(self.start.year, self.end.year + 1))


def parse_full_date(value: Any, where: str = "date") -> Optional[date]:
    """Parse a date that carries its own year. Returns None for an empty cell."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not text:
        return None
    # openpyxl may hand back "2025-01-08 00:00:00" as text.
    text = text.split(" ")[0].split("T")[0]
    for fmt in _FULL_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise StatementParseError(
        f"{where}: cannot read {value!r} as a date. Recognized forms: "
        + ", ".join(_FULL_DATE_FORMATS) + "."
    )


def resolve_partial_date(value: Any, period: StatementPeriod,
                         where: str = "date") -> Optional[date]:
    """Resolve a `D/MM` cell against the period the statement states.

    Bancolombia's `FECHA` is `D/MM` and is **not zero-padded** (`1/01`, `8/01`,
    `31/01`), so a fixed-width parse fails. The year comes from the period, and
    the period can span a year boundary in both directions seen in the corpus:
    the savings Q1 statement opens **2024/12/31** and the January card
    statement opens **30/12/2024**.

    A candidate that lands inside the period wins. When none does — a posting
    dated just outside its own statement window — the nearest candidate is
    returned rather than raising, because the row is real evidence; the caller
    records the excursion as a warning.
    """
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return parse_full_date(value, where)
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not text:
        return None
    match = _PARTIAL_DATE.match(text)
    if match is None:
        return parse_full_date(value, where)

    day, month = int(match.group(1)), int(match.group(2))
    candidates: List[date] = []
    for year in period.years():
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue  # e.g. 29/02 in a non-leap candidate year
    if not candidates:
        raise StatementParseError(
            f"{where}: {value!r} is not a valid day/month in any year of the "
            f"statement period {period.start}..{period.end}."
        )

    inside = [d for d in candidates if period.contains(d)]
    if inside:
        return min(inside)

    def distance(d: date) -> int:
        if d < period.start:
            return (period.start - d).days
        return (d - period.end).days

    return min(candidates, key=lambda d: (distance(d), d))


# ---------------------------------------------------------------------------
# What a reader emits (plan §6.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MovementRecord:
    """One row of one statement, normalized. A reader emits these and nothing else.

    `amount` is `None` on a pure balance observation — a period-end summary
    states two balances and no transactions, and inventing a movement between
    them would assert a flow the file never records.

    `balance_after` is `None` when the row states no balance. That is
    meaningful, not zero (§9.12): a ledger without balances yields *changes*,
    not *levels*.

    `source_file` and `source_row` are retained by design, for validator V13
    and the five-year recordkeeping rule — a figure has to trace back to its
    cell.
    """

    date: date
    description: str
    currency: str
    source_file: str
    source_row: int
    amount: Optional[Decimal] = None
    balance_after: Optional[Decimal] = None

    def order_key(self) -> Tuple[date, str, int]:
        """Stable sort key: `(date, source_file, source_row)`, never date alone.

        A running-balance file gives one `balance_after` per ROW, so the true
        sequence within a day lives in row order. TBC carries a time component
        but every value is `00:00:00`, and same-date rows are real — Bancolombia
        savings has two `9/01` rows and two `9/02` rows in Q1 alone. Sorting on
        date collapses them: taking the day's maximum manufactures an instant
        that never existed, taking the day's last discards a real intraday peak
        (plan §8.1, §9.10).
        """
        return (self.date, self.source_file, self.source_row)


@dataclass
class ReadResult:
    """Everything one file yielded: its movements, its period, and its complaints.

    A reader that finds nothing readable returns an empty `movements` with a
    populated `warnings` — an empty export is a *no-data* finding, never a
    zero balance (§9.6).
    """

    movements: List[MovementRecord] = field(default_factory=list)
    periods: List[StatementPeriod] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    source_file: str = ""
    currencies: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# What the core emits (plan §6.2, §6.3)
# ---------------------------------------------------------------------------

#: Why a maximum is a bound rather than a determined figure (plan §8.1). Both
#: cap the derived basis at `statement_partial`.
BOUND_AT_LEAST = "at_least"                    # an unknown prefix could only have added
BOUND_UNORDERED = "unordered_within_day"       # the peak depends on intraday sequence

#: `fbar_schema.MAX_VALUE_BASES`, restated here as an ordering from strongest
#: evidence to weakest so a cap is a `min()` rather than a chain of ifs.
BASIS_STRENGTH = (
    "statement_full_year",
    "statement_partial",
    "period_end_summaries_only",
    "estimate",
    "unknown",
)


def weaken_basis(current: str, cap: str) -> str:
    """Return the weaker of two bases. Evidence can only be capped, never promoted."""
    for basis in (current, cap):
        if basis not in BASIS_STRENGTH:
            raise StatementParseError(
                f"{basis!r} is not a max_value_basis. Valid: {', '.join(BASIS_STRENGTH)}."
            )
    return max((current, cap), key=BASIS_STRENGTH.index)


@dataclass(frozen=True)
class BalancePoint:
    """One observed level of one currency, at one point on the merged axis."""

    date: date
    currency: str
    balance: Decimal
    source_file: str
    source_row: int


@dataclass
class CombinedPoint:
    """One instant on the combined-USD series.

    `components` are per-currency amounts in LOCAL units and are emitted
    **signed**, with no convention applied (plan §8.2). `v3_recompute_usd`
    converts every component and compares the sum to the roster's stated
    maximum at a $1.00 tolerance, severity `error`. Flooring one component
    here while the merged series was summed from signed values makes the two
    disagree by the floored amount and hard-errors a roster that is otherwise
    correct. Zero-flooring is a filing decision, and it must be applied to the
    component sum and the stated maximum together or to neither.
    """

    date: date
    usd: Decimal
    components: Dict[str, Decimal] = field(default_factory=dict)
    unknown_currencies: List[str] = field(default_factory=list)
    source_file: str = ""
    source_row: int = 0

    @property
    def is_bounded(self) -> bool:
        """True when some currency's level was unknown at this instant.

        Such a point contributes a BOUND, not a value: forward-fill has nothing
        to fill from before a currency's first observation, back-filling would
        assert a level the file never states, and zero-filling asserts an empty
        wallet. TBC's GEL sheet has a single data row, in November — for January
        through October that currency's level is genuinely unknown.
        """
        return bool(self.unknown_currencies)


@dataclass
class AccountTimeline:
    """The parser's answer for one account group.

    Carries the derivation, not just the answer: `date_of_maximum` and the
    per-currency components exist because the hand-made workpapers record them
    and the five-year recordkeeping rule wants to see how a figure was reached.
    """

    account_key: str
    account_group: str
    tax_year: int
    currencies: List[str] = field(default_factory=list)
    series: Dict[str, List[BalancePoint]] = field(default_factory=dict)
    combined_usd: List[CombinedPoint] = field(default_factory=list)

    max_usd: Optional[Decimal] = None
    max_usd_bound: Optional[str] = None          # BOUND_AT_LEAST / BOUND_UNORDERED
    date_of_maximum: Optional[date] = None
    max_components: Dict[str, Decimal] = field(default_factory=dict)

    # Liability accounts get no bare `max_usd` (plan §6.2, §4.4). The maximum of
    # a mostly-negative card series is its LEAST NEGATIVE point — a figure with
    # no filing meaning until `credit_card_positive_balance_only` is applied,
    # which is Claude's call. Whichever field becomes Item 15, the roster has to
    # say which one it chose.
    is_liability: bool = False
    max_signed_usd: Optional[Decimal] = None
    max_positive_usd: Optional[Decimal] = None

    balance_dec_31: Optional[Decimal] = None
    periods: List[StatementPeriod] = field(default_factory=list)
    gaps: List[Dict[str, Any]] = field(default_factory=list)
    continuity_breaks: List[Dict[str, Any]] = field(default_factory=list)
    interval_uncertainty: List[Dict[str, Any]] = field(default_factory=list)
    parser_warnings: List[str] = field(default_factory=list)

    max_value_basis: str = "unknown"
    source_files: List[str] = field(default_factory=list)
    movement_count: int = 0
