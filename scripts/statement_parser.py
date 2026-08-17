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

import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

sys.path.append(os.path.dirname(os.path.abspath(__file__)))


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
    result = ReadResult(source_file=source_file, is_liability=profile.is_liability)

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

    Two of these are live in the corpus, not hypothetical. T-Bank's periods run
    the **22nd to the 21st**, so twelve monthly files end at December 21 and
    **December 22-31 is uncovered**. Bancolombia's cards are worse: one lacks
    December, the other October through December — and December is the month
    that would establish the year-end balance.

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
    by_currency: Dict[str, List[Tuple[StatementPeriod, Dict[str, Any]]]] = {}
    for period in periods:
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
