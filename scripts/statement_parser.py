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

import csv
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from anchor_reconstruction import Anchor, carry_balance, non_negativity_floor, replay_year


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


#: Space-thousands, comma-decimal, U+2212, `₽`. NFKC folds U+00A0 and U+202F
#: to a plain space before the separators are consulted.
#:
#: For T-Bank this is the format of the PERIOD TOTALS, and of the .txt
#: summaries throughout — NOT of a PDF register's transaction rows, which are
#: dot-decimal. Picking a format for a T-Bank source means picking per FIELD:
#: see `RUSSIAN_DOT_NUMBERS` below and `total_number_format`.
RUSSIAN_NUMBERS = NumberFormat(decimal_sep=",", thousands_seps=(" ",))

#: Space-thousands, DOT-decimal — how T-Bank's PDF registers render their
#: transaction rows, while the very same document writes its period totals
#: comma-decimal. Observed across every live register checked: EVERY row dot,
#: EVERY total comma. Bound to the observed file, not to the locale, and
#: kept as its own format rather than reusing PLAIN_NUMBERS so that a comma
#: decimal is REJECTED here rather than read as a thousands group.
RUSSIAN_DOT_NUMBERS = NumberFormat(decimal_sep=".", thousands_seps=(" ",))

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
    integer_part, _, fraction = marked.partition(fmt.decimal_sep)
    if _GROUP_MARK in fraction:
        # A group separator NEVER appears after the decimal point. `1.234,56`
        # is `1234.56` written dot-thousands/comma-decimal; read with a
        # dot-DECIMAL profile the comma vanishes as a group mark and the value
        # becomes `1.23456` — a thousand times too small, and the integer-part
        # check above never sees it because the integer part is the single
        # digit `1`. Colombia's own convention is exactly this shape, which is
        # what COLOMBIAN_EXPORT_NUMBERS deliberately does NOT follow.
        raise StatementParseError(
            f"{where}: {value!r} puts a grouping separator after "
            f"{fmt.decimal_sep!r}, which no number does. This profile's "
            "decimal separator does not match the file — read dot-thousands "
            "text with a dot-decimal profile and the value silently divides."
        )
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

#: What a source file is capable of showing. A running balance can, in
#: principle, evidence a full year. Two bounding balances per period NEVER can,
#: however many periods there are: an account that peaked mid-period and
#: returned looks flat (plan §4.2, §6.3).
EVIDENCE_RUNNING_BALANCE = "running_balance"
EVIDENCE_PERIOD_END_SUMMARIES = "period_end_summaries"
EVIDENCE_DECLARED_ANCHOR = "declared_anchor"
EVIDENCE_PRICED_DAILY_SERIES = "priced_daily_series"
EVIDENCE_NONE = "none"



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
    #: Position in TIME order within its file, assigned by the reader.
    #: Distinct from `source_row`, which stays the spreadsheet row so a figure
    #: can be traced back to its cell. They differ on a descending-order file:
    #: one written newest-first has its row order inverted relative to time,
    #: and sorting it "stably" on the spreadsheet row preserves exactly the
    #: wrong sequence (plan §8.1).
    row_order: int = 0

    def order_key(self) -> Tuple[date, str, int]:
        """Stable sort key: `(date, source_file, row_order)`, never date alone.

        A running-balance file gives one `balance_after` per ROW, so the true
        sequence within a day lives in row order. TBC carries a time component
        but every value is `00:00:00`, and same-date rows are real — Bancolombia
        savings has two `9/01` rows and two `9/02` rows in Q1 alone. Sorting on
        date collapses them: taking the day's maximum manufactures an instant
        that never existed, taking the day's last discards a real intraday peak
        (plan §8.1, §9.10).
        """
        return (self.date, self.source_file, self.row_order)


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
    #: True when no period could be found and the span was derived from the
    #: rows themselves. Such a period cannot show that a statement is missing
    #: from either end, so it caps the derived basis at `statement_partial`.
    period_inferred_from_rows: bool = False
    #: True when the source describes a liability (a credit card).
    is_liability: bool = False
    #: True when the file is readable but states nothing — an empty export.
    #: NOT a zero balance: emitting `$0` would assert a determined maximum the
    #: evidence cannot support (plan §9.6).
    no_data: bool = False
    #: What KIND of evidence this file is, which is what caps the derived
    #: basis. `running_balance` can in principle support a full year;
    #: `period_end_summaries` never can, however many files there are.
    evidence_kind: str = EVIDENCE_NONE
    #: Names of the sheets actually read, so a caller can tell that a
    #: two-sheet source arrived with one sheet.
    sheets_read: List[str] = field(default_factory=list)
    #: Whether the register's parsed rows reconcile against the bank's own
    #: stated period totals. `True` when they do, `False` when the reader
    #: accepted the file with a caveat, and **None when the file is not a
    #: register at all** — a running-balance sheet has no totals to reconcile,
    #: and reading its None as a failure would block every anchored
    #: reconstruction on a group that happens to hold one.
    totals_reconciled: Optional[bool] = None


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
    "anchored_full_year",
    "statement_partial",
    "period_end_summaries_only",
    "non_negativity_floor",
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
    #: `BOUND_UNORDERED` when this point is the order-independent ceiling for a
    #: date whose intraday sequence is unknowable. `None` on a point that was
    #: actually observed.
    bound: Optional[str] = None

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
    #: Set by `run_sources` from the source entry. Names the markdown report;
    #: it is a label for the reader, not something the parser derives.
    institution: Optional[str] = None
    #: What an anchored reconstruction established, or None. Carries the
    #: DERIVATION, not just the answer — the five-year recordkeeping rule wants
    #: to see how a figure was reached, and this figure was reached by
    #: arithmetic over a cited document rather than read off a column.
    reconstruction: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Reader: xlsx_running_balance (plan §5.1, step 2)
# ---------------------------------------------------------------------------

#: How many rows to scan for a header before concluding the file does not have
#: one. Bancolombia's savings sheet carries a metadata block and a `Resumen:`
#: totals block above `Movimientos:`, so the header is not near the top.
_HEADER_SCAN_ROWS = 200


def _cell_text(value: Any) -> str:
    """Normalize a cell to comparable header text. Empty for a blank cell."""
    if value is None:
        return ""
    return " ".join(unicodedata.normalize("NFKC", str(value)).split()).strip()


def _header_key(value: Any) -> str:
    return _cell_text(value).casefold().rstrip(":.")


def read_sheet_rows(worksheet) -> List[Tuple[int, Tuple[Any, ...]]]:
    """Materialize a worksheet as `(row_number, values)`, positions preserved.

    Iterates rows directly and never consults `ws.max_row`. Under
    `read_only=True` both Bancolombia shapes report no dimensions — `max_row`
    and `max_column` are `None` because the sheets carry no dimension record —
    so code that ranges over `max_row` silently reads nothing (hazard 9.11).

    Values are returned as the sheet's own tuples. They are NEVER compacted:
    dropping the `None`s in a row would slide `VALOR` into `SALDO` (hazard 9.1).
    """
    return [(number, row)
            for number, row in enumerate(worksheet.iter_rows(values_only=True), start=1)]


def _at(row: Tuple[Any, ...], index: Optional[int]) -> Any:
    """Read a column by POSITION, tolerating a row shorter than the header.

    A trailing empty cell may simply be absent from the row tuple; that is a
    short row, not a shifted one.
    """
    if index is None or index >= len(row):
        return None
    return row[index]


def _looks_like_a_table(rows: List[Tuple[int, Tuple[Any, ...]]]) -> bool:
    """True when at least one row holds two or more non-empty cells.

    Separates the two failure modes hazard 9.6 puts together. Wise's
    `All transactions` sheet is a single cell containing `ID` — there is no
    table there, so "no header found" means *no data*. A sheet full of real
    rows with no matching header means the PROFILE is wrong, which is an error.
    """
    return any(sum(1 for cell in row if _cell_text(cell)) >= 2 for _, row in rows)


def find_header_row(rows: List[Tuple[int, Tuple[Any, ...]]],
                    profile) -> Optional[Tuple[int, Dict[str, Optional[int]]]]:
    """Locate the header by MATCHING the profile's column texts.

    Matching rather than counting is what handles hazard 9.9 for free: TBC's
    row 1 is Georgian and row 2 English, and looking for `Balance` finds row 2
    without anyone hard-coding an index. Binding to row 1 would yield
    untranslatable keys.

    Returns `(row_number, {logical: column_index})`, with `None` for a declared
    column the file does not carry. Returns `None` when no row matches.
    """
    wanted = {logical: _header_key(text) for logical, text in profile.columns.items()}
    for number, row in rows[:_HEADER_SCAN_ROWS]:
        present = {}
        for position, cell in enumerate(row):
            key = _header_key(cell)
            if not key:
                continue
            for logical, expected in wanted.items():
                if key == expected and logical not in present:
                    present[logical] = position
        if all(logical in present for logical in profile.required_columns):
            return number, {logical: present.get(logical) for logical in profile.columns}
    return None


def _find_labelled_date(rows: List[Tuple[int, Tuple[Any, ...]]],
                        labels: Tuple[str, ...],
                        source_file: str) -> Optional[date]:
    """Find a date sitting beside one of `labels` in the metadata block.

    The label and its value may share a cell (`Desde: 30/12/2024`) or occupy
    adjacent cells, so both are tried. Only the metadata block is searched —
    the client name and street address that live there are read past and never
    retained (plan §10.2).
    """
    patterns = [re.compile(pattern, re.IGNORECASE) for pattern in labels]
    for _, row in rows:
        for position, cell in enumerate(row):
            text = _cell_text(cell)
            if not text or not any(p.search(text) for p in patterns):
                continue
            remainder = re.split(r"[:=]", text, maxsplit=1)
            tail = remainder[1].strip() if len(remainder) > 1 else ""
            for candidate in [tail] + [row[i] for i in range(position + 1, len(row))]:
                if candidate in (None, ""):
                    continue
                try:
                    found = parse_full_date(candidate, f"{source_file}: period label")
                except StatementParseError:
                    continue
                if found is not None:
                    return found
    return None


def resolve_period(rows: List[Tuple[int, Tuple[Any, ...]]],
                   profile,
                   source_file: str,
                   configured_start: Optional[Any] = None,
                   configured_end: Optional[Any] = None,
                   header_row: Optional[int] = None) -> Tuple[Optional[StatementPeriod], bool]:
    """Find the span the statement covers. Returns `(period, was_inferred)`.

    Order: what `sources.json` states, then a label in the metadata block, then
    nothing. Never the filename — `text 3.txt` is October and `text 12.txt` is
    January (hazard 9.3) — and never the file count: Bancolombia's cards are
    11 and 9 monthly files, not 12 and 12 (§4.5).

    Returning `(None, ...)` means the caller must derive the span from the rows
    and cap the basis; a period that came from its own rows cannot show that a
    statement is missing from either end.
    """
    start = parse_full_date(configured_start, f"{source_file}: configured period_start")
    end = parse_full_date(configured_end, f"{source_file}: configured period_end")
    if start and end:
        return StatementPeriod(start, end, source_file), False

    metadata = [r for r in rows if header_row is None or r[0] < header_row]
    metadata = metadata[:profile.period.search_rows]
    if profile.period.start_labels and profile.period.end_labels:
        start = start or _find_labelled_date(metadata, profile.period.start_labels, source_file)
        end = end or _find_labelled_date(metadata, profile.period.end_labels, source_file)
        if start and end:
            return StatementPeriod(start, end, source_file), False
    return None, True


def read_xlsx_running_balance(path: str,
                              profile,
                              currency: Optional[str] = None,
                              period_start: Optional[Any] = None,
                              period_end: Optional[Any] = None) -> ReadResult:
    """Read a spreadsheet whose rows carry a running balance (Shape A / D).

    The good case: a per-transaction row carries a balance column, so the
    intra-year maximum is directly observable. This reader turns the file into
    `MovementRecord`s and a `StatementPeriod` and stops there — it performs no
    arithmetic and makes no judgment (plan §5.1).
    """
    import openpyxl

    from statement_profiles import currency_for_sheet

    source_file = os.path.basename(path)
    result = ReadResult(source_file=source_file, is_liability=profile.is_liability,
                        evidence_kind=EVIDENCE_RUNNING_BALANCE)

    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises a wide family on a bad file
        raise StatementParseError(f"{source_file}: cannot open as .xlsx — {exc}") from exc

    try:
        pattern = re.compile(profile.sheet_pattern)
        sheet_names = [name for name in workbook.sheetnames if pattern.match(name)]
        if not sheet_names:
            raise StatementParseError(
                f"{source_file}: no sheet matches profile {profile.name!r} "
                f"({profile.sheet_pattern!r}). Sheets present: "
                f"{', '.join(workbook.sheetnames) or '(none)'}."
            )
        for name in sheet_names:
            _read_one_sheet(workbook[name], name, profile, source_file, result,
                            currency, period_start, period_end, currency_for_sheet)
    finally:
        workbook.close()

    if not result.movements and not result.no_data:
        result.no_data = True
    return result


def _read_one_sheet(worksheet, sheet_name: str, profile, source_file: str,
                    result: ReadResult, configured_currency: Optional[str],
                    period_start: Optional[Any], period_end: Optional[Any],
                    currency_for_sheet) -> None:
    """Append one sheet's movements to `result`. See `read_xlsx_running_balance`."""
    where = f"{source_file}[{sheet_name}]"
    rows = read_sheet_rows(worksheet)

    result.sheets_read.append(sheet_name)

    header = find_header_row(rows, profile)
    if header is None:
        if not _looks_like_a_table(rows):
            # An empty export is a NO-DATA finding, never a zero balance.
            # Emitting $0 would assert a determined maximum the evidence cannot
            # support — and an empty export is equally consistent with a failed
            # one. Dormancy is Phase 1 knowledge, not something the parser may
            # infer from an empty sheet (plan §9.6).
            result.no_data = True
            result.warnings.append(
                f"{where}: no header row and no table — the sheet states nothing. "
                "Reported as NO DATA, not as a zero balance: an empty export is "
                "equally consistent with a failed export, and only Phase 1 can "
                "say the account was dormant."
            )
            return
        raise StatementParseError(
            f"{where}: rows are present but no row carries the headers profile "
            f"{profile.name!r} expects "
            f"({', '.join(profile.columns[c] for c in profile.required_columns)}). "
            "The profile does not match this file."
        )

    header_row, columns = header
    currency = currency_for_sheet(profile, sheet_name, configured_currency)
    if currency not in result.currencies:
        result.currencies.append(currency)

    period, inferred = resolve_period(rows, profile, source_file,
                                      period_start, period_end, header_row)
    if period is None and profile.date_kind == "partial":
        # `D/MM` cannot be dated without a period, and a year may not be
        # guessed: the savings Q1 statement opens in the PRIOR calendar year.
        raise StatementParseError(
            f"{where}: rows are dated `D/MM` with no year, and no statement "
            "period was found in the metadata block or supplied by sources.json. "
            "A year cannot be guessed — the January card period opens 30/12 of "
            "the prior year. Set period_start/period_end on the source entry."
        )

    parsed = _parse_data_rows(rows, header_row, columns, profile, period,
                              currency, source_file, where, result)
    if not parsed:
        result.warnings.append(f"{where}: header found but no dated rows below it.")
        return

    # A file written newest-first has its row order inverted relative to time.
    # Sorting it "stably" on the spreadsheet row preserves exactly the wrong
    # sequence, so the direction is detected before any ordering happens.
    # Detection is mechanical: compare the first and last parsed dates.
    descending = parsed[0][1].date > parsed[-1][1].date
    if descending:
        parsed = list(reversed(parsed))
        result.warnings.append(
            f"{where}: rows are newest-first; re-sequenced into time order. "
            "`source_row` still points at the original spreadsheet row."
        )

    # The counter continues across sheets so `row_order` is unique within the
    # file. A card carries `DOLARES` and `PESOS` for the SAME card, and two
    # sheets restarting at zero would tie on the merged event axis and make the
    # ordering non-deterministic.
    offset = len(result.movements)
    for order, (_, record) in enumerate(parsed):
        result.movements.append(replace(record, row_order=offset + order))

    if period is None:
        period = StatementPeriod(parsed[0][1].date, parsed[-1][1].date, source_file)
        result.period_inferred_from_rows = True
        result.warnings.append(
            f"{where}: no statement period stated, so the span "
            f"{period.start}..{period.end} was derived from the rows themselves. "
            "Such a period cannot show that a statement is missing from either "
            "end, so the derived basis is capped at 'statement_partial'."
        )
    if period not in result.periods:
        result.periods.append(period)


def _parse_data_rows(rows, header_row, columns, profile, period, currency,
                     source_file, where, result):
    """Turn the rows below the header into `(row_number, MovementRecord)`.

    Every value is read by column POSITION. A row is never compacted: on the
    savings sheet both `SUCURSAL` and `DCTO.` are `None` on every movement row,
    so a 6-column header consistently yields 4 values, and filtering the `None`s
    would slide `VALOR` into `SALDO` on the very first file (hazard 9.1).
    """
    parsed = []
    skipped_undated = 0
    outside_period = 0

    for number, row in rows:
        if number <= header_row:
            continue
        raw_date = _at(row, columns.get("date"))
        if not _cell_text(raw_date):
            skipped_undated += 1
            continue
        if profile.date_kind == "partial":
            when = resolve_partial_date(raw_date, period, f"{where} row {number} date")
        else:
            when = parse_full_date(raw_date, f"{where} row {number} date")
        if when is None:
            skipped_undated += 1
            continue
        if period is not None and not period.contains(when):
            outside_period += 1

        amount = _row_amount(row, columns, profile, where, number)
        balance = parse_decimal(_at(row, columns.get("balance")), profile.numbers,
                                f"{where} row {number} balance")
        parsed.append((number, MovementRecord(
            date=when,
            description=_cell_text(_at(row, columns.get("description"))),
            currency=currency,
            source_file=source_file,
            source_row=number,
            amount=amount,
            balance_after=balance,
        )))

    if skipped_undated:
        result.warnings.append(
            f"{where}: {skipped_undated} row(s) below the header carry no readable "
            "date and were not read (totals and spacer rows look like this)."
        )
    if outside_period:
        result.warnings.append(
            f"{where}: {outside_period} row(s) fall outside the stated statement "
            "period. Read as dated, not discarded — but check the period."
        )
    return parsed


def _row_amount(row, columns, profile, where, number) -> Optional[Decimal]:
    """The signed movement for one row. `paid_in` is positive.

    A profile declares either a single signed `amount` column or the
    `paid_out`/`paid_in` pair. TBC writes `''` into the unused side of the
    pair, which is absent and not zero — so a row with neither side written
    has no amount at all rather than an amount of zero (hazard 9.12).
    """
    if columns.get("amount") is not None:
        return parse_decimal(_at(row, columns["amount"]), profile.numbers,
                             f"{where} row {number} amount")

    paid_in = parse_decimal(_at(row, columns.get("paid_in")), profile.numbers,
                            f"{where} row {number} paid in")
    paid_out = parse_decimal(_at(row, columns.get("paid_out")), profile.numbers,
                             f"{where} row {number} paid out")
    if paid_in is None and paid_out is None:
        return None
    return (paid_in or Decimal("0")) - abs(paid_out or Decimal("0"))


# ---------------------------------------------------------------------------
# Continuity and gap detection (plan §9.5, §9.8, step 3)
# ---------------------------------------------------------------------------

#: ISO-4217 currencies with no minor unit. Everything else is assumed to have
#: two decimal places, which is what the continuity tolerance is built from.
_ZERO_DECIMAL_CURRENCIES = frozenset((
    "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW",
    "PYG", "RWF", "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF",
))


def minor_unit(currency: str) -> Decimal:
    """One unit of the currency's smallest denomination — the break tolerance.

    **Exact equality is the wrong test** for continuity (hazard 9.8). Displayed
    balances are rounded to the minor unit and interest can post between one
    statement's close and the next one's open, so a bare `!=` fires on noise
    and then gets ignored — which is how a real break slips through. Anything
    above one minor unit is a break; anything at or below it is a
    reconciliation note.
    """
    return Decimal("1") if str(currency).upper() in _ZERO_DECIMAL_CURRENCIES else Decimal("0.01")



def total_number_format(profile) -> NumberFormat:
    """The NumberFormat a profile's TOTALS lines are written in.

    Locale rules bind per FIELD, not per profile — the principle already
    applied to dates, where a savings header states `YYYY/MM/DD` over rows
    written `D/MM`. Numbers skipped it, and a live T-Bank register is the case
    that shows why: every transaction row in it is dot-decimal while every
    period total in the same document is comma-decimal.

    Defaults to `profile.numbers` when `total_numbers` is unset, so a profile
    whose document is uniform says nothing and gets the right answer. The
    default is deliberately NOT a concrete format: one that silently differed
    from `numbers` would hand the next profile author Russian totals for an
    English document with no error anywhere.
    """
    return getattr(profile, "total_numbers", None) or profile.numbers


def read_stated_total(raw: Any, profile, where: str,
                      result: ReadResult) -> Optional[Decimal]:
    """Read one stated period total, or WARN and return None.

    An ABSENT total is missing evidence rather than an error — both callers
    already treat it that way, and say so in as many words. A total that is
    PRESENT but unreadable is the same gap wearing different clothes: the
    document stated its arithmetic, the profile could not follow it, and that
    side of the ledger goes unreconciled either way. Raising here split the
    two apart for no reason anyone could state, and the blast radius was the
    whole run — `run_sources` has no per-source recovery, so one register
    whose totals disagreed with its profile produced zero output for every
    filer and every account in the file.

    What stays fatal is the case this is NOT: a total that reads cleanly and
    DISAGREES with the rows. That is a detected mismatch, it means a row was
    dropped, and `_assert_register_reconciles` still raises on it.

    The warning names the raw text because the two gaps have different
    remedies: an absent total is the document's shape and nothing can be done
    about it, while an unreadable one is almost always `total_numbers`
    disagreeing with the page — a one-line profile fix, but only for a reader
    who is told which text failed.
    """
    try:
        return parse_decimal(raw, total_number_format(profile), where)
    except StatementParseError as exc:
        result.warnings.append(
            f"{where}: the stated total {raw!r} could not be read, so that "
            "side of the ledger was NOT reconciled against the document's own "
            "arithmetic. Check the profile's `total_numbers` against the "
            f"page. ({exc})")
        return None


def clip_periods(periods: List[StatementPeriod], tax_year: int) -> List[Tuple[date, date]]:
    """Trim each period to the calendar year and drop the ones that fall outside.

    The January card statement opens **30/12 of the prior year** and the savings
    Q1 statement opens **2024/12/31**, so clipping is the normal case here, not
    a defensive one. Coverage is a question about the calendar year FBAR asks
    about; evidence from outside it is read, but it does not count as coverage.
    """
    first, last = date(tax_year, 1, 1), date(tax_year, 12, 31)
    clipped = []
    for period in periods:
        start, end = max(period.start, first), min(period.end, last)
        if start <= end:
            clipped.append((start, end))
    return sorted(clipped)


def merge_spans(spans: List[Tuple[date, date]]) -> List[Tuple[date, date]]:
    """Union overlapping and day-adjacent spans into contiguous coverage."""
    merged: List[Tuple[date, date]] = []
    for start, end in sorted(spans):
        if merged and (start - merged[-1][1]).days <= 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def detect_gaps(periods: List[StatementPeriod], tax_year: int) -> List[Dict[str, Any]]:
    """Stretches of the calendar year no statement covers.

    Neither shape below is hypothetical. Where an institution's periods run
    the **22nd to the 21st**, a full year of monthly files still ends at
    December 21 and **December 22-31 is uncovered**. A card whose December
    statement never arrived is worse — December is the month that would
    establish the year-end balance, so its absence caps the basis outright.

    Detected and reported. **Never extrapolated**: a gap is a fact about the
    evidence, and filling it would be the parser deciding what the filing
    claims.
    """
    covered = merge_spans(clip_periods(periods, tax_year))
    first, last = date(tax_year, 1, 1), date(tax_year, 12, 31)

    def gap(start: date, end: date, where: str) -> Dict[str, Any]:
        return {"start": start.isoformat(), "end": end.isoformat(),
                "days": (end - start).days + 1, "position": where}

    if not covered:
        return [gap(first, last, "whole_year")]

    gaps = []
    if covered[0][0] > first:
        gaps.append(gap(first, covered[0][0] - timedelta(days=1), "year_start"))
    for (_, prev_end), (next_start, _) in zip(covered, covered[1:]):
        gaps.append(gap(prev_end + timedelta(days=1),
                        next_start - timedelta(days=1), "between_periods"))
    if covered[-1][1] < last:
        gaps.append(gap(covered[-1][1] + timedelta(days=1), last, "year_end"))
    return gaps


def period_endpoints(movements: List[MovementRecord],
                     period: StatementPeriod,
                     currency: str) -> Optional[Dict[str, Any]]:
    """The opening and closing balance one file states for one currency.

    Movements are matched to their period by `source_file`, not by date: one
    file is one period, and periods can overlap across institutions.

    The **opening** balance is not the first row's `balance_after` — that is the
    level *after* the first movement. Where the row states its amount, the
    opening level is `balance_after - amount`; where it does not (a period-end
    summary states a balance and no flow), the stated balance IS the opening.
    """
    rows = sorted((m for m in movements
                   if m.source_file == period.source_file
                   and m.currency == currency
                   and m.balance_after is not None),
                  key=MovementRecord.order_key)
    if not rows:
        return None
    first, last = rows[0], rows[-1]
    opening = first.balance_after
    if first.amount is not None:
        opening = first.balance_after - first.amount
    return {
        "opening": opening,
        "closing": last.balance_after,
        "opening_row": first.source_row,
        "closing_row": last.source_row,
        "source_file": period.source_file,
    }


def detect_continuity_breaks(movements: List[MovementRecord],
                             periods: List[StatementPeriod]) -> List[Dict[str, Any]]:
    """Check that each period's closing balance is the next one's opening.

    `closing[n] != opening[n+1]` means a missing statement or a mis-parse. The
    protocol is written down in one of the hand workpapers — *"If January's
    Ending Balance != February's Beginning Balance, log a warning and stop"* —
    and this makes it mechanical.

    Three outcomes, because collapsing them is what makes the check useless:

      * **`warn` — break.** The periods are contiguous and the balances differ
        by more than one minor unit. A statement is missing, or a column was
        read wrongly.
      * **`info` — not comparable.** There is a gap between the periods, so the
        balance was free to move unobserved. Reported, not silenced: it is the
        gap that is the finding.
      * **`info` — reconciles.** The difference is within one minor unit, which
        is rounding or an interest posting, not evidence of a missing file.
    """
    # `period_endpoints` matches movements to a period by SOURCE FILE, so two
    # periods carrying the same file (a multi-sheet workbook whose span was
    # inferred per sheet) both resolve to the whole file's endpoints. Paired
    # against each other they compare a file with itself and report the file's
    # own opening-to-closing move as a break. Collapse them into one span
    # first, which is the unit `period_endpoints` actually measures.
    spans: Dict[str, StatementPeriod] = {}
    for period in periods:
        seen = spans.get(period.source_file)
        spans[period.source_file] = period if seen is None else StatementPeriod(
            min(seen.start, period.start), max(seen.end, period.end),
            period.source_file)

    by_currency: Dict[str, List[Tuple[StatementPeriod, Dict[str, Any]]]] = {}
    for period in spans.values():
        for currency in sorted({m.currency for m in movements
                                if m.source_file == period.source_file}):
            endpoints = period_endpoints(movements, period, currency)
            if endpoints is not None:
                by_currency.setdefault(currency, []).append((period, endpoints))

    findings = []
    for currency, entries in sorted(by_currency.items()):
        entries.sort(key=lambda item: (item[0].start, item[0].end))
        tolerance = minor_unit(currency)
        for (prev, prev_ends), (nxt, next_ends) in zip(entries, entries[1:]):
            delta = next_ends["opening"] - prev_ends["closing"]
            gap_days = (nxt.start - prev.end).days - 1
            finding = {
                "currency": currency,
                "prior_period_end": prev.end.isoformat(),
                "next_period_start": nxt.start.isoformat(),
                "prior_closing": str(prev_ends["closing"]),
                "next_opening": str(next_ends["opening"]),
                "delta": str(delta),
                "tolerance": str(tolerance),
                "gap_days": max(gap_days, 0),
                "source_files": [prev.source_file, nxt.source_file],
            }
            if abs(delta) <= tolerance:
                finding.update(severity="info", kind="reconciles", message=(
                    f"{currency}: {prev.source_file} closes at "
                    f"{prev_ends['closing']} and {nxt.source_file} opens at "
                    f"{next_ends['opening']} — within one minor unit "
                    f"({tolerance}), so rounding or an interest posting rather "
                    "than a missing statement."))
            elif gap_days > 0:
                finding.update(severity="info", kind="not_comparable", message=(
                    f"{currency}: {gap_days} uncovered day(s) sit between "
                    f"{prev.source_file} and {nxt.source_file}, so the balance "
                    f"was free to move from {prev_ends['closing']} to "
                    f"{next_ends['opening']} unobserved. The GAP is the finding, "
                    "not the difference."))
            else:
                finding.update(severity="warn", kind="break", message=(
                    f"{currency}: {prev.source_file} closes at "
                    f"{prev_ends['closing']} but the contiguous "
                    f"{nxt.source_file} opens at {next_ends['opening']} — off by "
                    f"{delta}, more than one minor unit ({tolerance}). A "
                    "statement is missing, or a column was read wrongly."))
            findings.append(finding)
    return findings


# ---------------------------------------------------------------------------
# Multi-currency merge and FX conversion (plan §3, §8, step 4)
# ---------------------------------------------------------------------------


def to_usd(amount: Decimal, currency: str, rates: Dict[str, Decimal]) -> Decimal:
    """Convert a local amount at the December 31 Treasury rate.

    `units_per_usd` is foreign currency units per ONE US dollar — the Treasury
    Reporting Rate of Exchange convention — so USD is `local / units_per_usd`,
    **never the reciprocal** (`fbar_guide.md:134-139`). Storing or applying the
    reciprocal silently inverts every conversion for that currency, and for a
    rate near 1.0 the result still looks plausible.

    A missing rate is refused rather than defaulted. Treating an unconvertible
    currency as 1:1 would produce a number, which is worse than producing none.
    """
    code = str(currency).strip().upper()
    if code == "USD":
        return amount
    rate = rates.get(code)
    if rate is None:
        raise StatementParseError(
            f"No exchange rate supplied for {code}. FBAR converts at the Treasury "
            "Reporting Rate of Exchange for the LAST DAY of the calendar year; "
            "the parser converts but never fetches, so the rate is an input. "
            f"Rates supplied: {', '.join(sorted(rates)) or '(none)'}."
        )
    rate = Decimal(str(rate))
    if rate <= 0:
        raise StatementParseError(
            f"The rate for {code} is {rate}; units_per_usd must be positive."
        )
    return amount / rate


def _combined_point(when: date,
                    levels: Dict[str, Optional[Decimal]],
                    rates: Dict[str, Decimal],
                    reference: MovementRecord,
                    bound: Optional[str] = None) -> CombinedPoint:
    """One instant of the combined series, from each currency's current level.

    Components are the LOCAL, **signed** amounts. No convention is applied —
    see `derive_max` and plan §8.2: `v3_recompute_usd` re-converts every
    component and compares the sum to the roster's stated maximum at a $1.00
    tolerance, severity `error`, so flooring a negative component here while
    the merged series was summed from signed values hard-errors a roster that
    is otherwise correct.
    """
    known = {currency: level for currency, level in levels.items() if level is not None}
    unknown = sorted(currency for currency, level in levels.items() if level is None)
    total = sum((to_usd(level, currency, rates) for currency, level in known.items()),
                Decimal("0"))
    return CombinedPoint(
        date=when, usd=total, components=dict(known), unknown_currencies=unknown,
        source_file=reference.source_file, source_row=reference.source_row,
        bound=bound,
    )


def build_combined_series(movements: List[MovementRecord],
                          rates: Dict[str, Decimal]
                          ) -> Tuple[List[CombinedPoint], List[Dict[str, Any]], List[str]]:
    """Merge every currency onto one axis and convert point-wise.

    **The maximum is not the sum of per-currency maxima.** Per-currency peaks
    occur on different dates; summing them invents a moment that never existed.
    The peak is a single date on a *combined* series.

    The algorithm is licensed by the single-year-end-rate rule: because one rate
    applies to every point in the year, conversion **commutes with taking the
    maximum**, so the merged series can be converted point-wise and the max
    taken afterwards. Had FinCEN required historical daily rates, this would
    need a rate per event date and the whole design would change.

      1. Merge all events onto one axis ordered by `(date, source_file,
         row_order)` — never by date alone (§8.1).
      2. Forward-fill each currency across events where it did not move. Before
         a currency's FIRST observation its level is `None`: not zero, not
         back-filled. A full-year statement with one transaction does not
         describe a zero-balance account; it describes an account whose opening
         balance was never exported.
      3. Convert each currency point-wise at the December 31 rate.
      4. Sum across currencies at each event. A point with an unknown currency
         is a BOUND, not a value.

    Returns `(points, interval_uncertainty, currencies)`.
    """
    observations = sorted((m for m in movements if m.balance_after is not None),
                          key=MovementRecord.order_key)
    currencies = sorted({m.currency for m in observations})
    if not observations:
        return [], [], currencies

    level: Dict[str, Optional[Decimal]] = {currency: None for currency in currencies}
    points: List[CombinedPoint] = []
    uncertainty: List[Dict[str, Any]] = []

    index = 0
    while index < len(observations):
        when = observations[index].date
        same_day = []
        while index < len(observations) and observations[index].date == when:
            same_day.append(observations[index])
            index += 1

        # A date is not a timestamp. Two streams — two currencies, or two files
        # — that both moved on one date have an unknowable interleaving: TBC
        # carries a time component but every value is 00:00:00, so it holds no
        # information at all.
        streams = {(m.source_file, m.currency) for m in same_day}
        entering = dict(level)

        if len(streams) == 1:
            for record in same_day:
                level[record.currency] = record.balance_after
                points.append(_combined_point(when, level, rates, record))
        else:
            attained: Dict[str, List[Decimal]] = {c: [] for c in currencies}
            for record in same_day:
                level[record.currency] = record.balance_after
                attained[record.currency].append(record.balance_after)

            # The order-independent ceiling for the day: each currency free to
            # sit at the highest level it touched. Reachable by some
            # interleaving, so it is a true upper bound — and it does not
            # depend on which order the reader happened to see the rows in,
            # which is what makes it honest rather than a guess.
            ceiling_levels = {}
            for currency in currencies:
                touched = [v for v in [entering[currency]] + attained[currency]
                           if v is not None]
                ceiling_levels[currency] = max(touched) if touched else None

            ceiling = _combined_point(when, ceiling_levels, rates, same_day[-1],
                                      bound=BOUND_UNORDERED)
            # The end-of-day levels definitely coexisted, whatever the order,
            # and so did the levels entering the day.
            closing = _combined_point(when, level, rates, same_day[-1])
            observed = _combined_point(when, entering, rates, same_day[-1])
            # The OBSERVED point is appended before any ceiling, and the order
            # is load-bearing: `derive_max` breaks a tie on the earliest point
            # in the series, so an observed instant beats a ceiling of equal
            # value and the maximum stays a determined figure rather than
            # becoming a bound. Reversing these two would silently flip that.
            points.append(closing)

            # The ordering only MATTERS when the ceiling beats both instants
            # that are known to have existed. Two currencies that both rise on
            # one date peak together at the close whatever the order; two that
            # both fall peaked together before it. Flagging those would put an
            # `unordered_within_day` bound — and a `statement_partial` cap — on
            # a timeline whose maximum is not in doubt at all.
            if ceiling.usd > max(closing.usd, observed.usd):
                points.append(ceiling)
                uncertainty.append({
                    "kind": BOUND_UNORDERED,
                    "date": when.isoformat(),
                    "streams": sorted(f"{f}:{c}" for f, c in streams),
                    "ceiling_usd": str(ceiling.usd),
                    "closing_usd": str(closing.usd),
                    "message": (
                        f"{when.isoformat()}: {len(streams)} independent streams "
                        "moved on one date and no timestamp separates them. One "
                        "interleaving reaches a higher combined value than the "
                        "day's close, so the figure for that date is a ceiling "
                        "over every possible order, not an observed instant."),
                })

    # A currency's unknown prefix spans from the start of the series to its
    # first observation. Forward-fill has nothing to fill from, back-filling
    # would assert a level the file never states, and zero-filling asserts an
    # empty wallet — TBC's GEL sheet has a single data row, in November.
    for currency in currencies:
        first = next((p for p in points if currency not in p.unknown_currencies), None)
        if first is None or first is points[0]:
            continue
        uncertainty.append({
            "kind": BOUND_AT_LEAST,
            "currency": currency,
            "from": points[0].date.isoformat(),
            "until": first.date.isoformat(),
            "message": (
                f"{currency} is first observed on {first.date.isoformat()}; before "
                f"that its level is unknown, not zero. Combined values over that "
                "span are lower bounds — the unobserved currency could only have "
                "added to them."),
        })
    return points, uncertainty, currencies


def derive_max(points: List[CombinedPoint], is_liability: bool
               ) -> Dict[str, Any]:
    """The maximum of the combined series, with its date, components and bound.

    Four rules, each of which is a wrong number if omitted (plan §6.2):

    **`date_of_maximum` breaks ties to the EARLIEST date.** A maximum touched
    on several dates is common on a flat account, and an unspecified tie-break
    makes the output non-deterministic — which silently defeats both the golden
    diff and any year-over-year comparison. Ties on the same date then break to
    the earliest point in the series, which is why `build_combined_series`
    appends an observed point before any ceiling for that date: an observed
    instant of equal value must win, or a determined figure would become a
    bound. A same-code comparison of two runs cannot catch a deterministic but
    wrong tie-break, so the rule is stated rather than inferred from a test.

    **Components are signed and no convention is applied.** `negative_balance_is_zero`
    applied per currency would floor one component while the merged series that
    produced the maximum was summed from signed values; the two then disagree by
    the floored amount and V3 hard-errors. Zero-flooring is a filing decision:
    it must be applied to the component sum and the stated maximum together, or
    to neither.

    **A liability gets no bare `max_usd`.** The maximum of a mostly-negative
    card series is its *least negative* point — a figure with no filing meaning
    until `credit_card_positive_balance_only` is applied, which is Claude's
    call. Such a timeline reports `max_signed_usd` and `max_positive_usd`
    (the largest genuine overpayment) as separate fields, and the roster has to
    say which one it chose.

    **A bounded peak is never a determined figure.** It is reported with its
    direction, which caps the derived basis at `statement_partial`.
    """
    if not points:
        return {"max_usd": None, "max_signed_usd": None, "max_positive_usd": None,
                "date_of_maximum": None, "max_components": {}, "max_usd_bound": None}

    # Ties break to the earliest date, then to the earliest point in the series.
    peak = min(enumerate(points), key=lambda item: (-item[1].usd, item[1].date, item[0]))[1]

    bound = peak.bound
    if bound is None and peak.unknown_currencies:
        bound = BOUND_AT_LEAST

    result: Dict[str, Any] = {
        "date_of_maximum": peak.date,
        "max_components": dict(peak.components),
        "max_usd_bound": bound,
        "max_signed_usd": peak.usd,
        "max_positive_usd": max(peak.usd, Decimal("0")),
    }
    result["max_usd"] = None if is_liability else peak.usd
    return result


def balance_at_year_end(points: List[CombinedPoint], tax_year: int
                        ) -> Tuple[Optional[Decimal], Optional[str]]:
    """The forward-filled combined level at December 31, and a caveat if any.

    Emitted because it is a useful cross-check and the workpapers record it —
    **not because a filing depends on it.** Form 114 Item 15 asks for the
    maximum during the year; there is no year-end balance item, and the
    schema's `year_end_balance_usd` is consumed by no validator.
    """
    year_end = date(tax_year, 12, 31)
    # BOUNDED points are excluded. A `BOUND_UNORDERED` ceiling is appended
    # AFTER the day's close, so on a year-end date where two streams moved the
    # last point in the list is the order-independent ceiling — a level no
    # instant ever held. Taking it would report a December 31 balance higher
    # than any the files state, with no caveat at all.
    eligible = [p for p in points if p.date <= year_end and p.bound is None]
    if not eligible:
        return None, "No observation falls on or before December 31."
    last = eligible[-1]
    if last.unknown_currencies:
        return None, (
            f"At the last observation ({last.date.isoformat()}) "
            f"{', '.join(last.unknown_currencies)} had never been observed, so no "
            "combined year-end level can be stated.")
    if last.date < year_end:
        return last.usd, (
            f"Forward-filled from {last.date.isoformat()}: no statement covers "
            f"{last.date.isoformat()} to {year_end.isoformat()}, so this is the "
            "last observed level, not an observed December 31 balance.")
    return last.usd, None


# ---------------------------------------------------------------------------
# Reader: text_period_summary (plan §4.2, step 5)
# ---------------------------------------------------------------------------


def read_text_period_summary(path: str,
                             profile,
                             currency: str) -> ReadResult:
    """Read a period-end summary: two bounding balances and four totals.

    **There is no transaction detail.** Two balances bound each period and
    everything between them is invisible — an account that peaked mid-period
    and returned looks flat. This is the shape that must NEVER yield a
    determined maximum, so the result is stamped
    `EVIDENCE_PERIOD_END_SUMMARIES` and the basis derivation caps it at
    `period_end_summaries_only` however many files there are.

    The period comes from the two balance lines, which carry full dates.
    **Never from the filename**: `text 3.txt` is October and `text 12.txt` is
    January, so sorting these by name shuffles the year (hazard 9.3).

    The four totals are read but never become movements. They are used for one
    thing: checking that `closing - opening` equals their signed sum. That
    catches the U+2212 hazard directly — drop the Unicode minus on the
    `Expenses` line and the arithmetic stops balancing, where otherwise an
    expense would silently read as income (hazard 9.4).
    """
    source_file = os.path.basename(path)
    result = ReadResult(source_file=source_file, currencies=[currency.strip().upper()],
                        evidence_kind=EVIDENCE_PERIOD_END_SUMMARIES)
    currency = currency.strip().upper()

    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            lines = handle.read().splitlines()
    except OSError as exc:
        raise StatementParseError(f"{source_file}: cannot read — {exc}") from exc
    except UnicodeDecodeError as exc:
        raise StatementParseError(
            f"{source_file}: is not UTF-8 text — {exc}. The summary files carry "
            "U+2212 and the ruble sign, so the encoding is load-bearing."
        ) from exc

    balance_pattern = re.compile(profile.balance_line)
    balances = []
    for number, line in enumerate(lines, start=1):
        match = balance_pattern.match(unicodedata.normalize("NFKC", line))
        if match is None:
            continue
        when = parse_full_date(match.group("date"), f"{source_file} line {number}")
        amount = parse_decimal(match.group("balance"), profile.numbers,
                               f"{source_file} line {number} balance")
        if when is None or amount is None:
            continue
        balances.append((number, when, amount))

    if len(balances) < 2:
        if not balances:
            result.no_data = True
            result.warnings.append(
                f"{source_file}: no balance line matched, so the file states "
                "nothing this reader can use. Reported as NO DATA, not as a "
                "zero balance.")
            return result
        raise StatementParseError(
            f"{source_file}: found only one balance line. A period summary is "
            "bounded by two — an opening and a closing — and one alone cannot "
            "establish a period."
        )
    if len(balances) > 2:
        result.warnings.append(
            f"{source_file}: {len(balances)} balance lines found; the earliest "
            "is read as the period opening and the latest as its closing.")

    balances.sort(key=lambda item: (item[1], item[0]))
    (open_row, open_date, open_balance) = balances[0]
    (close_row, close_date, close_balance) = balances[-1]

    result.periods.append(StatementPeriod(open_date, close_date, source_file))
    result.movements.extend([
        MovementRecord(date=open_date, description="Period opening balance",
                       currency=currency, source_file=source_file,
                       source_row=open_row, row_order=0,
                       amount=None, balance_after=open_balance),
        MovementRecord(date=close_date, description="Period closing balance",
                       currency=currency, source_file=source_file,
                       source_row=close_row, row_order=1,
                       amount=None, balance_after=close_balance),
    ])
    result.warnings.append(
        f"{source_file}: period-end summary only — two bounding balances "
        f"({open_date.isoformat()} to {close_date.isoformat()}) and no "
        "transaction detail. An account that peaked between them and returned "
        "looks flat here.")

    _check_summary_totals(lines, profile, result, source_file,
                          open_balance, close_balance, currency)
    return result


def _check_summary_totals(lines, profile, result: ReadResult, source_file: str,
                          opening: Decimal, closing: Decimal, currency: str) -> None:
    """Verify `closing - opening` against the signed sum of the stated totals.

    The totals are NOT movements and never become balance points. They are read
    for this check alone, and the check earns its place by catching a specific
    silent failure: `Expenses` is written with U+2212, and a normalizer that
    dropped it would turn an expense into income. The balances would still
    parse, the timeline would still build, and only this arithmetic would
    notice.
    """
    totals: Dict[str, Decimal] = {}
    for pattern in profile.total_lines:
        compiled = re.compile(pattern)
        for number, line in enumerate(lines, start=1):
            match = compiled.match(unicodedata.normalize("NFKC", line))
            if match is None:
                continue
            amount = read_stated_total(match.group("amount"), profile,
                                       f"{source_file} line {number}", result)
            if amount is not None:
                totals[match.group("label")] = amount
    if not totals:
        return

    stated = sum(totals.values(), Decimal("0"))
    observed = closing - opening
    tolerance = minor_unit(currency)
    if abs(stated - observed) > tolerance:
        result.warnings.append(
            f"{source_file}: the stated totals sum to {stated} but the balances "
            f"move by {observed} ({closing} - {opening}), a difference of "
            f"{observed - stated}. Either a total was misread or a sign was "
            "lost — the Expenses line carries U+2212, not an ASCII hyphen. "
            f"Totals read: {', '.join(f'{k}={v}' for k, v in sorted(totals.items()))}.")


# ---------------------------------------------------------------------------
# Reader: pdf_movement_register (shape E) — movements, and NO balance
# ---------------------------------------------------------------------------


def read_movement_register_lines(lines: List[str], profile, source_file: str,
                                 currency: str) -> ReadResult:
    """Parse a movement register's text lines into movements. NO balances.

    A register states changes, never levels. Every movement therefore carries
    `balance_after=None`, and `derive_basis` caps the run at `estimate` through
    its `has_balances` branch — which is the honest answer, not a limitation to
    be worked around. Reconstructing levels from the deltas would assert a
    maximum the document does not support (§9.6, UNWRITABLE_PROFILES).

    The result is stamped `EVIDENCE_RUNNING_BALANCE` because `_EVIDENCE_CEILING`
    grades evidence by GRANULARITY, not by whether balances are present: this
    file is transaction-level, unlike a period-end summary whose two bounding
    balances can never see a mid-period peak. The absence of balances is a
    separate axis and `has_balances` is what applies it. Stamping
    `EVIDENCE_NONE` here would cap the run at `unknown` and lose the true fact
    that every transaction in the period is known.

    **Known limit of that argument, and it is worse than "no cap".** Two
    mechanisms have to hold for the reasoning above to be safe, and neither is
    per-file:

      * `has_balances` is computed once over the WHOLE account group
        (`build_timeline`), so a balance-bearing sibling satisfies it and the
        `estimate` cap never applies to this file's months;
      * this result's PERIOD counts as coverage in `detect_gaps` even though it
        contributes no `BalancePoint` — `build_combined_series` filters on
        `balance_after is not None` — so the last remaining cap is removed too.

    Measured: an `xlsx_running_balance` sibling covering Jan–Jun plus a
    register covering Jul–Dec yields `max_value_basis: statement_full_year`
    with `gaps: []`, and the reason string asserts FinCEN's periodic-statement
    safe harbour — over six months in which no level is known at all. That is
    the strongest available claim resting on the weakest available evidence,
    and `derive_basis` calls this "the single most important output".

    A register read ALONE is unaffected: nothing else supplies balances, so
    `has_balances` is False and the run caps at `estimate` as described. The
    empty-register path into this is closed below (a file listing no
    transactions contributes no period); the with-rows path is NOT. Closing it
    needs a per-result balance flag or a fourth evidence kind — a change to
    basis derivation, wider than this reader, so it is recorded here rather
    than half-done.

    Takes lines rather than a path so that every rule below is testable with
    literal text; `read_pdf_movement_register` is the one-line file wrapper.

    The parse is checked against the document's OWN stated totals. A register
    has no running balance to fall out of step, so a dropped or misread row is
    otherwise undetectable: the ledger simply comes out short and nothing
    contradicts it. The totals are the only internal evidence available, so
    failing to reconcile stops the run.
    """
    currency = currency.strip().upper()
    result = ReadResult(source_file=source_file, currencies=[currency],
                        evidence_kind=EVIDENCE_RUNNING_BALANCE)

    txn = re.compile(profile.txn_line)
    period_re = re.compile(profile.period_line)
    normalized = [unicodedata.normalize("NFKC", line) for line in lines]

    period = None
    for line in normalized:
        match = period_re.search(line)
        if match:
            start = parse_full_date(match.group("start"), f"{source_file} period start")
            end = parse_full_date(match.group("end"), f"{source_file} period end")
            if start and end:
                period = StatementPeriod(start, end, source_file)
            break

    rows = []
    markers = set()
    for number, line in enumerate(normalized, start=1):
        match = txn.match(line)
        if match is None:
            continue
        when = parse_full_date(match.group("date_op"), f"{source_file} line {number}")
        # The ACCOUNT amount is the movement. `amount_op` is what was charged
        # in the merchant's currency; it is matched so the two columns cannot
        # be confused for one another, and then deliberately discarded.
        amount = parse_decimal(match.group("amount_acct"), profile.numbers,
                               f"{source_file} line {number} account amount")
        if when is None or amount is None:
            continue
        markers.add(match.group("currency_acct"))
        rows.append((number, when, amount))

    if len(markers) > 1:
        # The marker does NOT identify the account's currency — that is why
        # `currency` is configuration. It does prove INCONSISTENCY: every row
        # is stamped with the one configured currency and summed into one
        # ledger, so more than one marker in the account column means some of
        # those rows are being counted in a currency they are not in.
        # A warning, not an error: `R` and `₽` can both be rubles, drifting
        # across pages of the same extraction, and refusing that would be a
        # false alarm on a correct file.
        result.warnings.append(
            f"{source_file}: the account column carries more than one currency "
            f"marker ({', '.join(sorted(markers))}), but every row is being read "
            f"as {currency}. If those are genuinely different currencies the "
            "ledger is summing across them; if they are one currency typeset "
            "two ways, this is noise.")

    if profile.newest_first:
        rows.reverse()

    for order, (number, when, amount) in enumerate(rows):
        result.movements.append(MovementRecord(
            when, "", currency, source_file, number,
            amount=amount, balance_after=None, row_order=order))

    _assert_register_reconciles(normalized, profile, rows, source_file, result,
                                currency)
    if result.totals_reconciled is not False:
        result.totals_reconciled = True

    if not rows:
        result.no_data = True
        result.warnings.append(
            f"{source_file}: states a period but lists no transactions. This is "
            "a no-data finding about the period, NOT a zero balance (§9.6).")
    if period is not None:
        # A file that listed no transactions covers NOTHING, so it contributes
        # no period even where it prints one — the invariant `derive_basis`
        # states and both sibling readers already keep by returning before
        # their own append. Appending it here let `detect_gaps` see the month
        # as covered, and a gap that never prints is one nobody fills.
        if rows:
            result.periods.append(period)
    else:
        result.period_inferred_from_rows = True
        if rows:
            # MIN and MAX, not first and last. Rows are printed in SETTLEMENT
            # order while the movement is the OPERATION date, so a settlement
            # lagging over a boundary puts the two out of step: taking the ends
            # as printed gave a span short by the drift, and where the drift
            # crossed the endpoints it inverted and StatementPeriod raised
            # "ends before it starts" on a well-formed file.
            dates = [when for _, when, _ in rows]
            result.periods.append(
                StatementPeriod(min(dates), max(dates), source_file))
            result.warnings.append(
                f"{source_file}: no period line found, so the span was inferred "
                "from the rows and cannot show a statement missing from either end.")
    result.warnings.append(
        f"{source_file}: states no balance of any kind, so this file supports "
        "movements only and no maximum value can be derived from it.")
    return result


def _assert_register_reconciles(lines: List[str], profile, rows, source_file: str,
                                result: ReadResult, currency: str) -> None:
    """Check the parsed rows against the register's own stated totals.

    The counterpart of `_check_summary_totals`, and stricter on purpose. There
    a mismatch is a warning, because two stated balances still bound the period
    whatever the totals say. Here nothing else bounds anything: if a row was
    dropped the ledger is simply short, every figure derived from it is wrong,
    and no other check in the pipeline can notice. So this one raises.

    `currency` is taken for the tolerance alone, and it is taken because this
    function raises: `minor_unit` is the same tolerance its sibling uses, and a
    hardcoded 0.01 would stop a JPY run over half a unit the currency has no
    way to express. Rounding noise is not a dropped row.
    """
    stated: Dict[str, Decimal] = {}
    # Tracked PER PATTERN, not per label: a pattern alternates over the English
    # and Russian wording of one total, and a Russian register legitimately
    # matches only the Cyrillic side. What matters is whether each SIDE of the
    # ledger was found, not which language stated it.
    unread = 0
    # Counted apart from `unread`, which cannot tell the two gaps apart: a
    # pattern that NEVER MATCHED and one that matched and could not be parsed
    # both leave a side unreconciled, but only the second means the document
    # stated its arithmetic and the PROFILE failed to follow it.
    unreadable = 0
    for pattern in profile.total_lines:
        compiled = re.compile(pattern)
        found = False
        matched = False
        for line in lines:
            match = compiled.match(line)
            if match is None:
                continue
            matched = True
            label = match.group("label").strip().lower()
            if label in stated:
                found = True
                continue
            # NOT `found = True` on an unreadable total: letting it fall
            # through is what makes `unread` count it, so the half-checked
            # warning below covers this case as well as the absent one.
            value = read_stated_total(match.group("amount"), profile,
                                      f"{source_file} {label} total", result)
            if value is not None:
                stated[label] = abs(value)
                found = True
        if not found:
            unread += 1
            if matched:
                unreadable += 1

    if not stated:
        result.totals_reconciled = False
        if unreadable:
            # NOT "states no period totals". A `total_numbers` that disagrees
            # with the page disagrees with EVERY total line, not one, so this
            # is the ordinary shape of that misconfiguration rather than an
            # edge of it — and telling the operator the register states
            # nothing would point them at the document when the remedy is in
            # the profile.
            result.warnings.append(
                f"{source_file}: states {unreadable} period total(s), none of "
                "which could be read, so the parse could not be reconciled "
                "against the document's own arithmetic. The arithmetic is "
                "there and the profile could not follow it — check "
                "`total_numbers`, not the file.")
        else:
            result.warnings.append(
                f"{source_file}: states no period totals, so the parse could not be "
                "reconciled against the document's own arithmetic.")
        return
    if unread:
        result.totals_reconciled = False
        # A HALF-checked ledger looked exactly like a fully checked one. With
        # `Deposits` present and `Expenses` absent every expense row could have
        # been dropped and this function would still have returned clean, so
        # the half case has to be as visible as the unchecked case. A warning
        # rather than an error: an absent total is missing evidence, which is
        # what the `not stated` branch above already treats it as.
        result.warnings.append(
            f"{source_file}: {unread} of {len(profile.total_lines)} period "
            "totals could not be read, so that side of the ledger was NOT "
            "reconciled against the document's own arithmetic. A dropped row on "
            "an unreconciled side leaves the ledger short with nothing to "
            "contradict it.")

    parsed_in = sum((a for _, _, a in rows if a > 0), Decimal("0"))
    parsed_out = -sum((a for _, _, a in rows if a < 0), Decimal("0"))
    tolerance = minor_unit(currency)
    for labels, parsed, what in (
            (("deposits", "пополнения"), parsed_in, "deposits"),
            (("expenses", "расходы"), parsed_out, "expenses")):
        for label in labels:
            if label not in stated:
                continue
            if abs(stated[label] - parsed) > tolerance:
                raise StatementParseError(
                    f"{source_file}: parsed {what} {parsed} does not reconcile "
                    f"with the document's stated {stated[label]}. Rows were "
                    "dropped or misread. A register has no running balance to "
                    "fall out of step, so this check is the only thing standing "
                    "between a short ledger and a silently wrong figure.")
            break


def read_pdf_movement_register(path: str, profile, currency: str) -> ReadResult:
    """Read a movement-register PDF. Container handling only; see above."""
    from statement_pdf import extract_text_lines

    return read_movement_register_lines(
        extract_text_lines(path), profile, os.path.basename(path), currency)


# ---------------------------------------------------------------------------
# Reader: xlsx_two_sheet_card (plan §4.4, step 6)
# ---------------------------------------------------------------------------


def read_xlsx_two_sheet_card(path: str,
                             profile,
                             period_start: Optional[Any] = None,
                             period_end: Optional[Any] = None) -> ReadResult:
    """Read a credit-card statement carrying one sheet per currency.

    Extraction is the running-balance reader's job — a card statement is the
    same shape with two sheets. What this adds is the two things that are
    specific to a LIABILITY source and would otherwise be forgotten:

      * **The sign is reported, and reported per currency.** Credit-card
        balances are liabilities; only an overpayment (positive) balance is an
        asset. That is the existing `credit_card_positive_balance_only`
        convention, and applying it is Claude's call — so the reader states
        what the signs were and stops there (hazard 9.7).
      * **A missing sheet is named.** Two sheets describe one card. A file that
        arrives with one has half the card's activity, and silently reading the
        half would understate every figure derived from it.

    Refuses a profile that is not marked `is_liability`: without the flag the
    timeline would emit a bare `max_usd`, and the maximum of a mostly-negative
    series is its least-negative point — a number with no filing meaning.
    """
    if not getattr(profile, "is_liability", False):
        raise StatementParseError(
            f"Profile {profile.name!r} is being read as a credit card but is not "
            "marked is_liability. Without that flag the timeline emits a bare "
            "max_usd, and the maximum of a mostly-negative series is its "
            "least-negative point — a figure with no filing meaning until "
            "`credit_card_positive_balance_only` is applied."
        )

    result = read_xlsx_running_balance(path, profile,
                                       period_start=period_start,
                                       period_end=period_end)

    declared = {name.upper() for name in getattr(profile, "sheet_currencies", {})}
    missing = sorted(declared - {name.upper() for name in result.sheets_read})
    if declared and missing:
        result.warnings.append(
            f"{result.source_file}: {', '.join(missing)} not present — this "
            f"source declares {len(declared)} sheets for one card and only "
            f"{len(result.sheets_read)} was read. Half a card's activity is "
            "missing, so every figure derived from it understates.")

    for currency in sorted(result.currencies):
        balances = [m.balance_after for m in result.movements
                    if m.currency == currency and m.balance_after is not None]
        if not balances:
            continue
        positive = [b for b in balances if b > 0]
        result.warnings.append(
            f"{result.source_file} [{currency}]: liability balances, "
            f"{len(positive)} of {len(balances)} positive. Only an overpayment "
            "balance is an asset — whether that makes the account reportable is "
            "the `credit_card_positive_balance_only` convention, which the "
            "parser records but does not apply.")
    return result


def read_point_balance(path: str, profile, currency: str) -> ReadResult:
    """One bank-confirmed balance, at one date. No period, no transactions.

    Emitted as a MovementRecord with `amount=None` and a stated
    `balance_after` — the same "pure balance observation" shape a period-end
    summary already produces. Nothing is being inferred: the document states
    this level, and the reader reports what the document says.

    `periods` is empty, and that is the point. A point balance has no span, so
    it contributes no COVERAGE: `detect_gaps` must not see it as a month of
    observed year, or a single letter would close a gap it says nothing about.
    """
    result = ReadResult(source_file=os.path.basename(path),
                        currencies=[currency.upper()],
                        evidence_kind=EVIDENCE_DECLARED_ANCHOR)
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()

    balance_re = re.compile(profile.balance_line)
    hits = [(n, m) for n, line in enumerate(lines, start=1)
            if (m := balance_re.search(line))]

    if len(hits) > 1:
        raise StatementParseError(
            f"{result.source_file}: found {len(hits)} dated balances, expected "
            "exactly one. A document stating two bounding balances is a PERIOD "
            "SUMMARY, and reading only the first would silently discard the "
            "other. Read it with the text_period_summary reader instead.")

    if not hits:
        result.no_data = True
        result.warnings.append(
            f"{result.source_file}: no line matched the profile's balance "
            "pattern, so the document states no balance this reader can use. "
            "That is a no-data finding, not a zero balance.")
        return result

    row, match = hits[0]
    when = parse_full_date(match.group("date"), where=f"{result.source_file}:{row}")
    balance = parse_decimal(match.group("balance"), profile.numbers,
                            where=f"{result.source_file}:{row}")
    result.movements.append(MovementRecord(
        date=when, description="stated available balance", currency=currency.upper(),
        source_file=result.source_file, source_row=row,
        amount=None, balance_after=balance, row_order=0))

    identifier_re = re.compile(profile.identifier_line)
    if not any(identifier_re.search(line) for line in lines):
        result.warnings.append(
            f"{result.source_file}: states a balance but names no card or "
            "account. Do not attribute it — see the UNIDENTIFIED convention in "
            "references/fbar_guide.md §9.")
    return result


def _read_table(path: str, profile) -> List[List[Any]]:
    """Read a table from a .csv or .xlsx file as a list of rows."""
    if path.lower().endswith(".csv") or getattr(profile, "sheet_pattern", None) is None:
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                return [list(r) for r in csv.reader(f)]
        except UnicodeDecodeError:
            with open(path, "r", encoding="cp1251") as f:
                return [list(r) for r in csv.reader(f)]
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = None
    if getattr(profile, "sheet_pattern", None):
        for name in wb.sheetnames:
            if re.search(profile.sheet_pattern, name):
                ws = wb[name]
                break
    if ws is None:
        ws = wb.active
    rows = [list(r[1]) for r in read_sheet_rows(ws)]
    wb.close()
    return rows


def match_columns_by_open_date(first_dates: Dict[str, date],
                               contract_open_dates: Dict[str, date]) -> Dict[str, str]:
    """Match unlabelled export columns to accounts by when each one starts.

    A multi-account export labels its columns by nickname or not at all. The
    reliable signal is each column's FIRST NON-BLANK DATE against each
    account's CONTRACT-OPEN DATE, because an account cannot hold value before
    it existed — the match is forced, not guessed.

    Magnitude and column order both fail on the case that matters. A
    transposition — two accounts' columns swapped in the workbook — preserves
    every magnitude and every position; only the start dates disagree. One such
    swap stood for over a week in a live workspace and was caught this way.

    Raises rather than guessing when the signal runs out. Two accounts opened
    on the same date make this method silent, and falling back to magnitude
    because the better method ran out is how the transposition survived in the
    first place.
    """
    opens = sorted(contract_open_dates.items(), key=lambda kv: kv[1])
    for index in range(len(opens) - 1):
        if opens[index][1] == opens[index + 1][1]:
            raise StatementParseError(
                f"{opens[index][0]!r} and {opens[index + 1][0]!r} were opened on the "
                f"same date ({opens[index][1]}), so first-non-blank-date cannot tell "
                "their columns apart. Obtain the account numbers rather than falling "
                "back to magnitude or column order — a transposition preserves both.")

    columns = sorted(first_dates.items(), key=lambda kv: kv[1])
    if len(columns) > len(opens):
        raise StatementParseError(
            f"The export has {len(columns)} account column(s) and only {len(opens)} "
            "contract-open date(s) were supplied. An unmatched column is an account "
            "that may be on no roster — see validator V15.")

    mapping = {}
    for (label, first_date), (key, opened) in zip(columns, opens):
        if first_date < opened:
            raise StatementParseError(
                f"Column {label!r} has a value on {first_date}, which predates when {key!r} was "
                f"opened on {opened}. An account cannot hold value before it existed, "
                "so this pairing is wrong and the contract-open dates or the column "
                "set need checking before anything is read from this file.")
        mapping[label] = key
    return mapping


def read_daily_value_series(path: str, profile, currency: str = "RUB",
                            contract_open_dates: Optional[Dict[str, date]] = None
                            ) -> List[Tuple[str, ReadResult]]:
    """A wide daily value table: one row per date, one column per account.

    Returns ONE (account_key, ReadResult) PAIR PER COLUMN — the only reader
    here that yields several accounts from one file, because a daily-value
    export covers every brokerage account the filer holds. Merging them into
    one result would produce a single fictional account holding the sum.

    Values are already priced (cash + securities at that day's close), which is
    what makes this gradeable as `statement_full_year`-capable evidence: it is
    the closest thing to a bank's own daily balance column a brokerage
    produces. Gap and continuity analysis then caps it honestly where days are
    missing — nothing here claims coverage the file does not have.
    """
    rows = _read_table(path, profile)          # CSV, or the sheet_pattern tab
    if not rows:
        raise StatementParseError(f"{os.path.basename(path)}: file contains no rows.")
    header, data_rows = rows[0], rows[1:]

    try:
        date_index = header.index(profile.date_column)
    except ValueError:
        date_index = -1
        for idx, col in enumerate(header):
            if str(col or "").strip() == profile.date_column:
                date_index = idx
                break
        if date_index == -1:
            raise StatementParseError(
                f"{os.path.basename(path)}: date column {profile.date_column!r} not found in header: {header!r}"
            )

    column_pattern = re.compile(profile.account_column_pattern)
    account_columns = {label: index for index, label in enumerate(header)
                       if index != date_index and column_pattern.search(str(label or ""))}
    if not account_columns:
        raise StatementParseError(
            f"{os.path.basename(path)}: no header cell matched "
            f"{profile.account_column_pattern!r}, so no column in this file can be "
            "read as an account. Reading it positionally would assign values to "
            "whichever account happened to sort first.")

    # (label, row_number, date, value) for every NON-BLANK cell. A blank is an
    # absent observation, never a zero: before an account opened there is
    # nothing to observe, and 0.00 would assert a determined balance the export
    # never states — the §9.6 empty-export rule in another costume.
    observed: Dict[str, List[Tuple[int, date, Decimal]]] = {l: [] for l in account_columns}
    for row_number, row in enumerate(data_rows, start=2):
        if date_index >= len(row) or row[date_index] is None or str(row[date_index]).strip() == "":
            continue
        when = parse_full_date(row[date_index], where=f"{path}:{row_number}")
        if when is None:
            continue
        for label, index in account_columns.items():
            cell = row[index] if index < len(row) else None
            if cell is None or str(cell).strip() == "":
                continue
            observed[label].append((row_number, when, parse_decimal(
                cell, profile.numbers, where=f"{path}:{row_number}:{label}")))

    first_dates = {label: entries[0][1]
                   for label, entries in observed.items() if entries}
    mapping = match_columns_by_open_date(first_dates, contract_open_dates or {})

    out: List[Tuple[str, ReadResult]] = []
    for label, entries in observed.items():
        key = mapping.get(label, label)
        result = ReadResult(source_file=os.path.basename(path),
                            currencies=[currency.upper()],
                            evidence_kind=EVIDENCE_PRICED_DAILY_SERIES)
        if not entries:
            result.no_data = True
            result.warnings.append(
                f"{result.source_file}: column {label!r} is blank on every row. An "
                "empty column states nothing and covers no part of the year.")
            out.append((key, result))
            continue
        for order, (row_number, when, value) in enumerate(entries):
            result.movements.append(MovementRecord(
                date=when, description="stated portfolio value",
                currency=currency.upper(), source_file=result.source_file,
                source_row=row_number, amount=None, balance_after=value,
                row_order=order))
        # The period is the column's OWN first and last observation, so a
        # column that starts in June contributes June..December of coverage and
        # `detect_gaps` reports January..May honestly rather than the file's
        # overall span papering over it.
        result.periods.append(StatementPeriod(start=entries[0][1], end=entries[-1][1],
                                              source_file=result.source_file))
        out.append((key, result))
    return out


# ---------------------------------------------------------------------------
# Basis derivation and timeline assembly (plan §6.2, §6.3, step 7)
# ---------------------------------------------------------------------------

#: The strongest basis each kind of evidence could ever support. A cap, not a
#: verdict: gaps, breaks and bounded intervals weaken it further.
_EVIDENCE_CEILING = {
    EVIDENCE_RUNNING_BALANCE: "statement_full_year",
    EVIDENCE_PRICED_DAILY_SERIES: "statement_full_year",
    EVIDENCE_PERIOD_END_SUMMARIES: "period_end_summaries_only",
    EVIDENCE_DECLARED_ANCHOR: "estimate",
    EVIDENCE_NONE: "unknown",
}


def derive_basis(read_results: List[ReadResult],
                 gaps: List[Dict[str, Any]],
                 continuity_breaks: List[Dict[str, Any]],
                 interval_uncertainty: List[Dict[str, Any]],
                 has_balances: bool,
                 anchor_available: bool = False,
                 ledger_reconciled: bool = False) -> Tuple[str, List[str]]:
    """Derive `max_value_basis` from what the files actually contain.

    **This is the single most important output.** `fbar_schema.MAX_VALUE_BASES`
    already defines the vocabulary; the point of deriving rather than asserting
    it is validator V12. If a human types `statement_full_year` into a roster by
    hand, V12 is decorative — it warns only when someone has already admitted
    the evidence is weak. Derived from gap and continuity analysis, **V12 fires
    honestly**, and the existing validator becomes worth having.

    Evidence is only ever capped, never promoted (`weaken_basis`):

    | Evidence found | Basis |
    |---|---|
    | Running balance, no gaps, continuity holds | `statement_full_year` |
    | Running balance, period gap or break | `statement_partial` |
    | Two bounding balances per period only | `period_end_summaries_only` |
    | Movements with no balances anywhere | `estimate` |
    | No files, or nothing readable in them | `unknown` |

    Returns `(basis, reasons)` — the reasons are what the workpaper prints, and
    the five-year recordkeeping rule wants the derivation, not just the answer.
    """
    reasons: List[str] = []
    contributing = [r for r in read_results if r.movements]
    if not contributing:
        return "unknown", [
            "No file yielded a readable balance, so the evidence cannot support "
            "any maximum. This is the Item 15a decision, and it is Claude's."]

    basis = "statement_full_year"
    for result in contributing:
        if result.evidence_kind == EVIDENCE_DECLARED_ANCHOR and anchor_available and ledger_reconciled:
            ceiling = "anchored_full_year"
        else:
            ceiling = _EVIDENCE_CEILING.get(result.evidence_kind, "unknown")
        if BASIS_STRENGTH.index(ceiling) > BASIS_STRENGTH.index(basis):
            reasons.append(
                f"{result.source_file} is {result.evidence_kind!r} evidence, "
                f"which can support at best {ceiling!r}.")
        basis = weaken_basis(basis, ceiling)

    empty = sorted({r.source_file for r in read_results if r.no_data})
    if empty:
        # An empty export states nothing and therefore covers nothing. It
        # contributes no period, so where a SIBLING file's period spans it the
        # gap check sees full coverage and the basis stays `statement_full_year`
        # — a safe-harbour claim resting partly on a file that said nothing.
        # That is hazard 9.6 arriving through the back door.
        basis = weaken_basis(basis, "statement_partial")
        reasons.append(
            f"{len(empty)} source file(s) stated nothing — {', '.join(empty)}. "
            "An empty export is NOT a zero balance and covers no part of the "
            "year, so the evidence cannot support a full-year claim even where "
            "another file's period spans the same days.")

    if not has_balances:
        # A ledger yields CHANGES, not levels — and that rule is intact here.
        # What changes is WHICH cap applies, because a cited dated level plus a
        # ledger the bank's own totals prove complete is not the same evidence
        # as a bare register. Nothing is promoted: `weaken_basis` still only
        # caps, and gaps, breaks and inferred periods below cap further.
        if anchor_available and ledger_reconciled:
            basis = weaken_basis(basis, "anchored_full_year")
            reasons.append(
                "No row states a balance, but a dated anchor balance was read from "
                "its own document and every register reconciles against the bank's "
                "stated period totals. The opening is carried from the anchor and the "
                "year replayed exactly — the records DO cover the year, via a citation "
                "rather than a balance column.")
        elif ledger_reconciled:
            basis = weaken_basis(basis, "non_negativity_floor")
            reasons.append(
                "No row states a balance and no anchor was supplied, but every "
                "register reconciles against the bank's stated totals, so the ledger "
                "is complete. The account cannot go negative, so the smallest opening "
                "consistent with it yields a PROVEN FLOOR on the maximum — a bound "
                "that can only understate, not a guess.")
        else:
            basis = weaken_basis(basis, "estimate")
            reasons.append(
                "No row anywhere states a balance, so only movements are known and "
                "no level can be established without an anchor balance.")

    if gaps:
        basis = weaken_basis(basis, "statement_partial")
        total = sum(g["days"] for g in gaps)
        reasons.append(
            f"{len(gaps)} coverage gap(s) totalling {total} day(s) — "
            + "; ".join(f"{g['start']}..{g['end']} ({g['position']})" for g in gaps)
            + ". The account was free to move unobserved in those days.")

    breaks = [f for f in continuity_breaks if f.get("kind") == "break"]
    if breaks:
        basis = weaken_basis(basis, "statement_partial")
        reasons.append(
            f"{len(breaks)} continuity break(s): a period's closing balance is "
            "not the next one's opening, so a statement is missing or a column "
            "was read wrongly.")

    if interval_uncertainty:
        basis = weaken_basis(basis, "statement_partial")
        kinds = sorted({u["kind"] for u in interval_uncertainty})
        reasons.append(
            f"The combined series carries interval uncertainty ({', '.join(kinds)}), "
            "so the maximum is a bound rather than a determined figure.")

    if any(r.period_inferred_from_rows for r in contributing):
        basis = weaken_basis(basis, "statement_partial")
        reasons.append(
            "At least one statement's period was derived from its own rows. Such "
            "a period cannot show that a statement is missing from either end.")

    if basis == "statement_full_year":
        reasons.append(
            "Running balances cover the calendar year with no gap, no continuity "
            "break and no bounded interval — FinCEN's periodic-statement safe "
            "harbour is available on this evidence.")
    return basis, reasons


def build_timeline(account_key: str,
                   tax_year: int,
                   read_results: List[ReadResult],
                   fx_rates: Dict[str, Decimal],
                   account_group: Optional[str] = None) -> AccountTimeline:
    """Assemble one account group's timeline from everything its files yielded.

    **Grouping is configuration, not inference.** Which files belong to one
    account was decided and written down in `sources.json`; the parser never
    decides that two files are one account. Where the decision has not been
    made, the group is left unset and the files become separate timelines with
    the ambiguity flagged, rather than merged on a guess (plan §5.2, §8.2).
    """
    movements: List[MovementRecord] = []
    periods: List[StatementPeriod] = []
    warnings: List[str] = []
    source_files: List[str] = []
    for result in read_results:
        movements.extend(result.movements)
        periods.extend(result.periods)
        warnings.extend(result.warnings)
        if result.source_file and result.source_file not in source_files:
            source_files.append(result.source_file)

    points, uncertainty, currencies = build_combined_series(movements, fx_rates)
    currencies = sorted(set(currencies) | {c for r in read_results for c in r.currencies}
                        | {m.currency for m in movements if m.currency})
    gaps = detect_gaps(periods, tax_year)
    breaks = detect_continuity_breaks(movements, periods)
    is_liability = any(r.is_liability for r in read_results)
    maximum = derive_max(points, is_liability)
    year_end, year_end_caveat = balance_at_year_end(points, tax_year)
    if year_end_caveat:
        warnings.append(f"December 31 balance: {year_end_caveat}")

    has_balances = any(m.balance_after is not None for r in read_results
                       if r.evidence_kind != EVIDENCE_DECLARED_ANCHOR
                       for m in r.movements)

    # --- Anchored reconstruction (docs/fbar_enhancements_plan.md, F1/F2) -----
    #
    # Two preconditions, and neither is negotiable. A cited dated LEVEL, from a
    # document that stated it; and a ledger the bank's own arithmetic proves is
    # COMPLETE. Without the first there is nothing to carry; without the second
    # the replay is exact over the wrong set of rows, which is worse than
    # inexact because it does not look wrong.
    anchor_results = [r for r in read_results
                      if r.evidence_kind == EVIDENCE_DECLARED_ANCHOR]
    register_flags = [r.totals_reconciled for r in read_results
                      if r.totals_reconciled is not None]
    ledger_reconciled = bool(register_flags) and all(register_flags)

    reconstruction = None
    anchor_available = False
    flow_movements = [m for m in movements if m.amount is not None]

    if anchor_results and len(currencies) > 1:
        # `carry_balance` sums Decimals with no currency awareness, and this
        # group merges several currencies onto a USD axis. An anchor stated in
        # one currency has no defined meaning against that sum, so the honest
        # answer is to decline rather than to convert silently at a rate the
        # anchor's own document never mentioned.
        warnings.append(
            f"{len(anchor_results)} anchor balance(s) were read, but this account "
            f"group holds {len(currencies)} currencies ({', '.join(currencies)}). An "
            "anchored reconstruction is defined for a SINGLE-CURRENCY group only; "
            "the anchor is recorded and not applied.")
    elif anchor_results and not ledger_reconciled:
        warnings.append(
            "An anchor balance was read, but no register in this group reconciles "
            "against the bank's own stated period totals. Exactness rests on the "
            "ledger being complete, and nothing here proves it is — so the anchor "
            "is recorded and not applied.")
    elif anchor_results:
        anchors = [
            Anchor(when=m.date, balance=m.balance_after, source_file=m.source_file)
            for r in anchor_results for m in r.movements
            if m.balance_after is not None
        ]
        boundary = date(tax_year, 1, 1)
        openings = {a.source_file: carry_balance(a, flow_movements, boundary)
                    for a in anchors}

        # More than one anchor is a GIFT, not a complication: each is carried
        # independently and they must agree. A disagreement means the ledger
        # between them is incomplete — exactly the condition the exactness
        # claim rests on — so it fails loudly here rather than producing a
        # confident wrong opening.
        distinct = set(openings.values())
        if len(distinct) > 1:
            detail = "; ".join(f"{f} -> {v}" for f, v in sorted(openings.items()))
            warnings.append(
                f"{len(anchors)} anchors carry to DIFFERENT January 1 openings "
                f"({detail}). They cannot all be right, and the ledger between them "
                "must be incomplete. No reconstruction is applied.")
        else:
            opening = distinct.pop()
            replay = replay_year(opening, flow_movements, tax_year)
            anchor_available = True
            reconstruction = {
                "method": "anchored_full_year",
                "anchors": [{"as_of": a.when.isoformat(),
                             "balance": money(a.balance),
                             "source_file": a.source_file} for a in anchors],
                "opening": money(replay.opening),
                "closing": money(replay.closing),
                "maximum": money(replay.maximum),
                "date_of_maximum": (replay.date_of_maximum.isoformat()
                                    if replay.date_of_maximum else None),
                "minimum": money(replay.minimum),
                # Informational, NEVER an error (fbar_guide.md §9). The opening,
                # maximum and closing are conservation results and hold under
                # every ordering; only the trajectory is affected, and the
                # trajectory is not a filed figure.
                "negative_excursions": [
                    {"start": e["start"].isoformat(), "end": e["end"].isoformat(),
                     "low": money(e["low"]), "same_timestamp": e["same_timestamp"]}
                    for e in replay.negative_excursions],
            }
            for excursion in replay.negative_excursions:
                warnings.append(
                    f"The replayed balance goes below zero {excursion['start']}.."
                    f"{excursion['end']} (low {excursion['low']}). "
                    + ("Every row in the excursion shares one date, so this is a "
                       "same-timestamp batch-processing artefact: the opening, "
                       "maximum and closing are conservation results and are "
                       "unaffected. Do not reorder the rows to remove it."
                       if excursion["same_timestamp"] else
                       "The excursion spans more than one date, so it is NOT a "
                       "same-timestamp artefact — the anchor or the ledger is "
                       "wrong. Resolve this before using any figure here."))

    elif ledger_reconciled and not has_balances and len(currencies) == 1:
        # No anchor, but the ledger is complete: the account's own
        # non-negativity gives a PROVEN floor. Same single-currency restriction,
        # for the same reason.
        opening, maximum_floor = non_negativity_floor(flow_movements, tax_year)
        reconstruction = {
            "method": "non_negativity_floor",
            "opening": money(opening),
            "maximum": money(maximum_floor),
        }

    basis, reasons = derive_basis(read_results, gaps, breaks, uncertainty,
                                  has_balances, anchor_available=anchor_available,
                                  ledger_reconciled=ledger_reconciled)

    series: Dict[str, List[BalancePoint]] = {}
    for record in sorted(movements, key=MovementRecord.order_key):
        if record.balance_after is None:
            continue
        series.setdefault(record.currency, []).append(BalancePoint(
            date=record.date, currency=record.currency,
            balance=record.balance_after,
            source_file=record.source_file, source_row=record.source_row))

    return AccountTimeline(
        account_key=account_key,
        account_group=account_group or account_key,
        tax_year=tax_year,
        currencies=currencies,
        series=series,
        combined_usd=points,
        max_usd=maximum["max_usd"],
        max_usd_bound=maximum["max_usd_bound"],
        date_of_maximum=maximum["date_of_maximum"],
        max_components=maximum["max_components"],
        is_liability=is_liability,
        max_signed_usd=maximum["max_signed_usd"],
        max_positive_usd=maximum["max_positive_usd"],
        balance_dec_31=year_end,
        periods=periods,
        gaps=gaps,
        continuity_breaks=breaks,
        interval_uncertainty=uncertainty,
        parser_warnings=warnings + reasons,
        max_value_basis=basis,
        source_files=source_files,
        movement_count=len(movements),
        reconstruction=reconstruction,
    )


# ---------------------------------------------------------------------------
# Serialization — where Decimal stops (plan §6.2)
# ---------------------------------------------------------------------------

#: Where every emitted figure is rounded. All internal arithmetic is `Decimal`,
#: and the continuity checks compare exactly — but the emitted JSON carries
#: plain JSON numbers, which the roster and `v3_recompute_usd` consume as
#: `float` at a $1.00 tolerance. **The exact-equality reasoning justifies the
#: parser's own checks and nothing downstream.** `fbar_schema._number` rejects
#: anything that is not an int or a float, so emitting Decimal-as-string here
#: would make the timeline unusable as roster input.
JSON_MONEY_PLACES = Decimal("0.01")


def money(value: Optional[Decimal]) -> Optional[float]:
    """Round one figure to cents and hand it to JSON as a float."""
    if value is None:
        return None
    return float(value.quantize(JSON_MONEY_PLACES, rounding=ROUND_HALF_UP))


def timeline_to_json(timeline: AccountTimeline) -> Dict[str, Any]:
    """Serialize a timeline to plain JSON types — the Phase 1 input.

    `max_value_components` is shaped exactly as the roster schema takes it,
    `[{"currency", "amount"}, ...]` in LOCAL signed units, so Claude can carry
    it across without re-deriving anything — and so `v3_recompute_usd` can
    re-convert it and reconcile against the stated maximum.
    """
    return {
        "account_key": timeline.account_key,
        "account_group": timeline.account_group,
        "tax_year": timeline.tax_year,
        "currencies": list(timeline.currencies),
        "is_liability": timeline.is_liability,
        "max_value_basis": timeline.max_value_basis,
        "max_usd": money(timeline.max_usd),
        "max_usd_bound": timeline.max_usd_bound,
        "max_signed_usd": money(timeline.max_signed_usd),
        "max_positive_usd": money(timeline.max_positive_usd),
        "date_of_maximum": (timeline.date_of_maximum.isoformat()
                            if timeline.date_of_maximum else None),
        "max_value_components": [
            {"currency": currency, "amount": money(amount)}
            for currency, amount in sorted(timeline.max_components.items())
        ],
        "year_end_balance_usd": money(timeline.balance_dec_31),
        "periods": [{"start": p.start.isoformat(), "end": p.end.isoformat(),
                     "source_file": p.source_file} for p in timeline.periods],
        "gaps": timeline.gaps,
        "continuity_breaks": timeline.continuity_breaks,
        "interval_uncertainty": timeline.interval_uncertainty,
        "parser_warnings": timeline.parser_warnings,
        "source_documents": list(timeline.source_files),
        "movement_count": timeline.movement_count,
        "reconstruction": timeline.reconstruction,
        "combined_usd_series": [
            {"date": point.date.isoformat(), "usd": money(point.usd),
             "components": {c: money(a) for c, a in sorted(point.components.items())},
             "unknown_currencies": point.unknown_currencies,
             "bound": point.bound}
            for point in timeline.combined_usd
        ],
    }


# ---------------------------------------------------------------------------
# Source configuration (plan §5.2, step 8)
# ---------------------------------------------------------------------------

#: Every recognized key of a `sources[]` entry, with its default. Unknown keys
#: are REJECTED with the nearest valid one named, following
#: `fbar_schema._reject_unknown`: a typo here silently drops a whole
#: institution from the run, and a missing account is the one failure this
#: module cannot detect for itself.
SOURCE_FIELDS: Dict[str, Any] = {
    "filer": None,              # REQUIRED — one workspace holds both filers
    "institution": None,        # REQUIRED — names the markdown report
    "path": "",                 # directory, relative to the workspace
    "glob": None,               # REQUIRED — which files in it
    "reader": None,             # REQUIRED — see READERS
    "profile": None,            # REQUIRED — see statement_profiles.PROFILES
    "account_key": None,        # literal key, or use account_key_pattern
    "account_key_pattern": None,  # regex over the FILENAME with a `key` group
    "account_group": None,      # what the multi-currency merge iterates over
    "currency": None,           # literal ISO-4217, when the profile takes config
    "period_start": None,       # when the file states no period the profile reads
    "period_end": None,
    "contract_open_dates": None,
    "notes": None,
}

_SOURCE_REQUIRED = ("filer", "institution", "glob", "reader", "profile")

#: Reader name -> callable. Selected by EXPLICIT CONFIG, never by sniffing the
#: file's contents: a misidentified institution is a silent wrong number.
READERS = {
    "xlsx_running_balance": "read_xlsx_running_balance",
    "xlsx_two_sheet_card": "read_xlsx_two_sheet_card",
    "text_period_summary": "read_text_period_summary",
    "pdf_movement_register": "read_pdf_movement_register",
    "point_balance": "read_point_balance",
    "daily_value_series": "read_daily_value_series",
}


def _reject_unknown_keys(raw: Dict[str, Any], valid, where: str) -> None:
    from difflib import get_close_matches

    unknown = [k for k in raw if k not in valid and not k.startswith("_")]
    if not unknown:
        return
    hints = []
    for key in sorted(unknown):
        match = get_close_matches(key, list(valid), n=1, cutoff=0.6)
        hints.append(f"\n  {key!r}:" + (f" Did you mean {match[0]!r}?" if match else ""))
    raise StatementParseError(
        f"Unknown key(s) in {where}, refusing to proceed silently:{''.join(hints)}\n"
        f"Valid keys: {', '.join(sorted(valid))}."
    )


def load_sources(path: str) -> Dict[str, Any]:
    """Load and validate `sources.json` — the taxpayer-specific file map.

    This lives at the **workspace root, beside the data it maps**, not in the
    distributed plugin: it names real directories and real account keys, and
    the plugin repository is a public distribution artifact.

    Its central job is `account_group`. One `account_key` per directory does not
    work — TBC is three files, one per currency, all under a single IBAN, which
    the hand workpaper treats as ONE FBAR entry — and a file-level key alone
    cannot say the three belong together. **Grouping is configuration, not
    inference**: the parser never decides that two files are one account. Where
    the decision has not been made, leave the group unset and the files become
    separate timelines with the ambiguity flagged.
    """
    import json

    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError as exc:
        raise StatementParseError(f"sources file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise StatementParseError(f"{path} is not valid JSON: {exc}") from exc

    valid_top = ("tax_year", "fx_rates", "sources")
    _reject_unknown_keys(raw, valid_top, f"{path} (top level)")
    if raw.get("tax_year") is None:
        raise StatementParseError(f"{path}: tax_year is required.")

    rates: Dict[str, Decimal] = {}
    for index, entry in enumerate(raw.get("fx_rates") or []):
        _reject_unknown_keys(entry, ("currency", "units_per_usd", "as_of", "source",
                                     "source_url"), f"{path} fx_rates[{index}]")
        currency = str(entry.get("currency", "")).strip().upper()
        if not currency:
            raise StatementParseError(f"{path} fx_rates[{index}].currency is required.")
        units = entry.get("units_per_usd")
        if units is None:
            raise StatementParseError(
                f"{path} fx_rates[{index}].units_per_usd is required — foreign "
                "units per ONE US dollar, the Treasury convention.")
        rates[currency] = Decimal(str(units))

    sources = []
    for index, entry in enumerate(raw.get("sources") or []):
        where = f"{path} sources[{index}]"
        _reject_unknown_keys(entry, SOURCE_FIELDS, where)
        source = {key: entry.get(key, default) for key, default in SOURCE_FIELDS.items()}
        for key in _SOURCE_REQUIRED:
            if not source[key]:
                raise StatementParseError(f"{where}.{key} is required.")
        if source["reader"] not in READERS:
            raise StatementParseError(
                f"{where}.reader is {source['reader']!r}; known readers: "
                f"{', '.join(sorted(READERS))}.")
        if (source["reader"] != "daily_value_series"
                and not source["account_key"]
                and not source["account_key_pattern"]):
            raise StatementParseError(
                f"{where} sets neither account_key nor account_key_pattern. One "
                "directory can hold several accounts — the card files are two "
                "cards distinguished only by a filename suffix — so the key has "
                "to be stated or derived, never assumed from the directory.")
        sources.append(source)

    if not sources:
        raise StatementParseError(f"{path} declares no sources.")
    return {"tax_year": int(raw["tax_year"]), "fx_rates": rates,
            "sources": sources, "_origin": path}


def account_key_for(source: Dict[str, Any], filename: str) -> str:
    """The account a file's movements belong to, per its source entry."""
    if source.get("account_key"):
        return str(source["account_key"])
    pattern = re.compile(source["account_key_pattern"])
    match = pattern.search(filename)
    if match is None or not match.groupdict().get("key"):
        raise StatementParseError(
            f"{filename}: account_key_pattern "
            f"{source['account_key_pattern']!r} did not match with a `key` group. "
            "Rather than guess which account this file belongs to, the run stops.")
    return match.group("key")


def _parse_open_dates(source: Dict[str, Any]) -> Dict[str, date]:
    """Parse `contract_open_dates` config {account_key: 'YYYY-MM-DD'} into {account_key: date}."""
    raw = source.get("contract_open_dates") or {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, date] = {}
    for key, val in raw.items():
        parsed = parse_full_date(val, where=f"contract_open_dates[{key!r}]")
        if parsed is not None:
            out[str(key)] = parsed
    return out


def read_source(source: Dict[str, Any], workspace: str) -> List[Tuple[str, ReadResult]]:
    """Read every file a source entry selects. Returns `(account_key, result)`."""
    import glob as globbing

    from statement_profiles import get_profile

    profile = get_profile(source["profile"])
    reader_name = READERS[source["reader"]]
    reader = globals()[reader_name]
    directory = os.path.join(workspace, source["path"] or "")
    matches = sorted(globbing.glob(os.path.join(directory, source["glob"])))
    if not matches:
        raise StatementParseError(
            f"{source['institution']}: no file matches {source['glob']!r} under "
            f"{directory}. A source that selects nothing is a configuration "
            "error, not an empty account — an account with no evidence is "
            "recorded by leaving it off this file, not by pointing at nothing.")

    out = []
    for match in matches:
        if source["reader"] in ("text_period_summary", "pdf_movement_register", "point_balance", "daily_value_series"):
            if not source["currency"]:
                raise StatementParseError(
                    f"{os.path.basename(match)}: the {source['reader']} reader "
                    "takes its currency from sources.json and none is set. A "
                    "register states amounts without naming the account's "
                    "currency, so defaulting it would label rubles as dollars.")
        if source["reader"] == "daily_value_series":
            # The ONLY reader that returns several accounts from one file: a
            # daily-value export covers every brokerage account the filer
            # holds. Keyed by the matcher, not by `account_key_for`, because
            # the file's own columns decide which accounts it describes.
            out.extend(reader(match, profile, currency=source["currency"],
                              contract_open_dates=_parse_open_dates(source)))
            continue
        key = account_key_for(source, os.path.basename(match))
        if source["reader"] in ("text_period_summary", "pdf_movement_register", "point_balance"):
            result = reader(match, profile, currency=source["currency"])
        else:
            kwargs = {"period_start": source["period_start"],
                      "period_end": source["period_end"]}
            if source["reader"] == "xlsx_running_balance":
                kwargs["currency"] = source["currency"]
            result = reader(match, profile, **kwargs)
        out.append((key, result))
    return out


def run_sources(config: Dict[str, Any], workspace: str,
                filer: Optional[str] = None) -> Dict[str, List[AccountTimeline]]:
    """Read every configured source and build one timeline per account group.

    Grouped by filer, because separately-owned accounts force separate FBARs
    and mixing two filers' accounts would fabricate a filing neither of them
    makes — the same rule `fbar_reconciler` enforces on rosters.
    """
    tax_year = config["tax_year"]
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for source in config["sources"]:
        if filer and source["filer"] != filer:
            continue
        for key, result in read_source(source, workspace):
            group = source["account_group"] or key
            bucket = grouped.setdefault((source["filer"], group), {
                "account_key": key, "group": group, "results": [],
                "institution": source["institution"], "keys": set()})
            bucket["results"].append(result)
            bucket["keys"].add(key)

    timelines: Dict[str, List[AccountTimeline]] = {}
    for (filer_slug, group), bucket in sorted(grouped.items()):
        timeline = build_timeline(bucket["account_key"], tax_year, bucket["results"],
                                  config["fx_rates"], account_group=group)
        timeline.institution = bucket["institution"]
        if len(bucket["keys"]) > 1:
            timeline.parser_warnings.append(
                f"account_group {group!r} merges files whose account_keys differ "
                f"({', '.join(sorted(bucket['keys']))}). That grouping was a "
                "recorded decision, not something the parser inferred — check it "
                "is still the one the roster means to make.")
        timelines.setdefault(filer_slug, []).append(timeline)
    return timelines


# ---------------------------------------------------------------------------
# Outputs (plan §10)
# ---------------------------------------------------------------------------

_BOUND_LABEL = {
    BOUND_AT_LEAST: "at least (a currency was unobserved for part of the year)",
    BOUND_UNORDERED: "ceiling (the peak depends on an unknowable intraday order)",
}


def _fmt(value: Optional[Decimal], suffix: str = "") -> str:
    return "—" if value is None else f"{money(value):,.2f}{suffix}"


def render_parse_report(institution: str, tax_year: int,
                        timelines: List[AccountTimeline], filer: str) -> str:
    """The human-readable workpaper for one institution.

    Carries the frontmatter the hand-made BCS workpaper already specifies —
    `institution`, `taxpayer`, `tax_year`, `accounts_analyzed`,
    `parser_warnings`. That protocol was written as a specification for work not
    yet done rather than as a record of work done by hand, which is exactly why
    adopting it here is right: the format is being introduced, not matched.
    """
    warning_count = sum(len(t.parser_warnings) for t in timelines)
    lines = [
        "---",
        f"institution: {institution}",
        f"taxpayer: {filer}",
        f"tax_year: {tax_year}",
        f"accounts_analyzed: {len(timelines)}",
        f"parser_warnings: {warning_count}",
        "---",
        "",
        f"# Statement parse — {institution}, {tax_year}",
        "",
        "Phase 0 output. **This reports what the files say; it does not decide "
        "what the filing claims.** Reportability, ownership, the applicable "
        "conventions and the Item 15a election are Phase 1 decisions, and every "
        "figure below still goes through the Phase 2 user-confirmation gate.",
        "",
        "| Account | Currencies | Maximum | On | Basis | Dec 31 | Gaps | Breaks |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for timeline in timelines:
        headline = (_fmt(timeline.max_signed_usd) + " (signed, liability)"
                    if timeline.is_liability else _fmt(timeline.max_usd))
        if timeline.max_usd_bound:
            headline += f" [{timeline.max_usd_bound}]"
        breaks = len([f for f in timeline.continuity_breaks if f["kind"] == "break"])
        lines.append(
            f"| `{timeline.account_key}` | {', '.join(timeline.currencies) or '—'} "
            f"| {headline} | {timeline.date_of_maximum or '—'} "
            f"| `{timeline.max_value_basis}` | {_fmt(timeline.balance_dec_31)} "
            f"| {len(timeline.gaps)} | {breaks} |")

    for timeline in timelines:
        lines += ["", f"## `{timeline.account_key}`", ""]
        if timeline.is_liability:
            lines += [
                "> **Liability account.** The maximum of a mostly-negative series "
                "is its least-negative point, which has no filing meaning until "
                "`credit_card_positive_balance_only` is applied — a decision this "
                "tool does not make. Largest genuine overpayment: "
                f"**{_fmt(timeline.max_positive_usd)}**.", ""]
        if timeline.max_usd_bound:
            lines += [
                f"> **The maximum is a bound, not a determined figure**: "
                f"{_BOUND_LABEL.get(timeline.max_usd_bound, timeline.max_usd_bound)}.",
                ""]

        if timeline.max_components:
            lines += ["Components at the peak — **signed, no convention applied**:", ""]
            lines += [f"- {currency} {money(amount):,.2f}"
                      for currency, amount in sorted(timeline.max_components.items())]
            lines.append("")

        lines += ["Periods read:", ""]
        lines += [f"- {p.start} .. {p.end} (`{p.source_file}`)" for p in timeline.periods] \
            or ["- none"]

        if timeline.gaps:
            lines += ["", "Coverage gaps — **not extrapolated**:", ""]
            lines += [f"- {g['start']} .. {g['end']} ({g['days']} days, {g['position']})"
                      for g in timeline.gaps]
        if timeline.continuity_breaks:
            lines += ["", "Continuity:", ""]
            lines += [f"- **{f['severity']}** {f['message']}"
                      for f in timeline.continuity_breaks]
        if timeline.parser_warnings:
            lines += ["", "Parser warnings:", ""]
            lines += [f"- {w}" for w in timeline.parser_warnings]
    lines.append("")
    return "\n".join(lines)


def write_outputs(output_dir: str, filer: str, tax_year: int,
                  timelines: List[AccountTimeline]) -> List[str]:
    """Write the timelines JSON and one markdown report per institution.

    **Both artifacts are workspace-only.** They are written beside the
    statements they describe and never into the plugin repository: the
    descriptions are verbatim and embed counterparty names, and the source
    paths are retained by design. Add `account_timelines_*.json` and
    `statement_parse_*.md` to the workspace `.gitignore`.
    """
    import json

    os.makedirs(output_dir, exist_ok=True)
    written = []

    payload = {
        "tax_year": tax_year,
        "filer": filer,
        "generated_by": "statement_parser.py (FBAR Phase 0)",
        "accounts": [timeline_to_json(t) for t in timelines],
    }
    json_path = os.path.join(output_dir, f"account_timelines_{filer}_{tax_year}.json")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    written.append(json_path)

    by_institution: Dict[str, List[AccountTimeline]] = {}
    for timeline in timelines:
        by_institution.setdefault(timeline.institution or filer, []).append(timeline)
    for institution, group in sorted(by_institution.items()):
        report = render_parse_report(institution, tax_year, group, filer)
        report_path = os.path.join(
            output_dir, f"statement_parse_{institution}_{tax_year}.md")
        with open(report_path, "w", encoding="utf-8") as handle:
            handle.write(report)
        written.append(report_path)
    return written


# ---------------------------------------------------------------------------
# CLI (plan §13)
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    """Parse configured statements into balance timelines.

    Exit code is **non-zero only on unreadable input**. A gap, a break or a
    missing statement is a warning, not a failure — the same severity contract
    as `fbar_reconciler.py`, for the same reason: a tool that refuses to run on
    legitimate interim states gets worked around.
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="FBAR Phase 0: parse bank statements into balance timelines.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse", help="Parse the sources into timelines")
    p_parse.add_argument("--sources", required=True,
                         help="sources.json, at the workspace root beside the data")
    p_parse.add_argument("--workspace", default=None,
                         help="Directory the source paths are relative to. "
                              "Defaults to the sources file's directory.")
    p_parse.add_argument("--filer", default=None,
                         help="Restrict the run to one filer slug. Omitted, "
                              "every filer in the sources file is processed.")
    p_parse.add_argument("--output-dir", default=None,
                         help="Write the timelines JSON and the per-institution "
                              "markdown reports here")
    p_parse.add_argument("--output-format", choices=["text", "json"], default="text")

    args = parser.parse_args(argv)

    try:
        config = load_sources(args.sources)
        workspace = args.workspace or os.path.dirname(os.path.abspath(args.sources))
        by_filer = run_sources(config, workspace, args.filer)
    except StatementParseError as exc:
        if args.output_format == "json":
            print(json.dumps({"isError": True, "error": str(exc)}, indent=2))
        else:
            print(f"🔴 ERROR: {exc}")
        return 1

    if args.filer and args.filer not in by_filer:
        print(f"🔴 ERROR: no source in {args.sources} names filer "
              f"{args.filer!r}. Present: {', '.join(sorted(by_filer)) or '(none)'}.")
        return 1

    written = []
    if args.output_dir:
        for filer_slug, timelines in sorted(by_filer.items()):
            written += write_outputs(args.output_dir, filer_slug,
                                     config["tax_year"], timelines)

    payload = {
        "tax_year": config["tax_year"],
        "filers": {slug: [timeline_to_json(t) for t in timelines]
                   for slug, timelines in sorted(by_filer.items())},
        "outputs": written,
    }
    if args.output_format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_parse_summary(by_filer, written)
    return 0


def _print_parse_summary(by_filer: Dict[str, List[AccountTimeline]],
                         written: List[str]) -> None:
    for filer_slug, timelines in sorted(by_filer.items()):
        print(f"\n=== {filer_slug} — {len(timelines)} account group(s) ===")
        for timeline in timelines:
            headline = (f"{_fmt(timeline.max_signed_usd)} signed (liability)"
                        if timeline.is_liability else _fmt(timeline.max_usd, " USD"))
            bound = f"  [{timeline.max_usd_bound}]" if timeline.max_usd_bound else ""
            print(f"  {timeline.account_key:<28} {headline:>22}{bound}")
            print(f"    basis {timeline.max_value_basis!r} on "
                  f"{timeline.movement_count} movement(s) from "
                  f"{len(timeline.source_files)} file(s); "
                  f"{len(timeline.gaps)} gap(s), "
                  f"{len([f for f in timeline.continuity_breaks if f['kind'] == 'break'])} break(s)")
    if written:
        print("\nWrote:\n" + "\n".join(f"  {path}" for path in written))
    print("\nPhase 0 reports what the files say. Reportability, ownership, "
          "conventions and\nthe Item 15a election are Phase 1 — and every figure "
          "goes through the Phase 2\nuser-confirmation gate before it reaches a "
          "roster.")


if __name__ == "__main__":
    sys.exit(main())
