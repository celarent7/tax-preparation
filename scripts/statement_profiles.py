#!/usr/bin/env python3
"""Declarative column maps for the Phase 0 statement parser.

Profiles are **data, not classes** (plan §5.3). Five per-bank parser classes
would each re-implement — or quietly omit — continuity checking, multi-currency
merging and basis derivation; the institution-specific part is genuinely small:
*where the columns are*. Adding an institution should mean adding a profile.

Every profile here was written from a file that was actually read. Where the
available exports cannot specify a layout — a sheet whose only cell is `ID`
is not a header row — **no profile is written**. A column map written from
expectation rather than evidence is the one thing this module exists to
prevent, because it fails as a plausible wrong number rather than as an error.
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from statement_parser import (  # noqa: E402
    COLOMBIAN_EXPORT_NUMBERS,
    PLAIN_NUMBERS,
    RUSSIAN_NUMBERS,
    NumberFormat,
    StatementParseError,
)

# How a reader learns which currency a sheet holds. Never from the filename
# (plan §5.2) — a filename is a naming convention, a sheet name is the export's
# own statement about its contents.
CURRENCY_FROM_SHEET_SUFFIX = "sheet_name_suffix"   # `<IBAN>-USD` -> USD
CURRENCY_FROM_SHEET_NAME = "sheet_name"            # `DOLARES` -> USD, via `sheet_currencies`
CURRENCY_FROM_CONFIG = "config"                    # the sources.json entry says so


@dataclass(frozen=True)
class PeriodSpec:
    """How to find the span a statement covers, tried in this order.

    1. The `sources.json` entry states it outright (`period_start`/`period_end`).
    2. A label in the metadata block above the data (`start_labels`/`end_labels`).
    3. Nothing — the reader derives the span from the rows it parsed, records a
       warning, and the basis is capped at `statement_partial`.

    Step 3 is deliberately weak. A period derived from its own rows can never
    show that a statement is missing from either end, so a timeline built on it
    must not claim to cover the year. This is the §4.5 rule in code: cadence and
    coverage are discovered from the parsed period, never from the filename and
    never from the file count.
    """

    start_labels: Tuple[str, ...] = ()
    end_labels: Tuple[str, ...] = ()
    #: How many rows from the TOP OF THE SHEET to scan for those labels, up to
    #: the data header. A metadata block sits at the top of the file; it is the
    #: HEADER that moves down, because the totals block above it is
    #: variable-length (`_HEADER_SCAN_ROWS` is 200 for that reason). So the
    #: window is anchored at the top, not measured back from the header.
    search_rows: int = 40


@dataclass(frozen=True)
class SheetProfile:
    """Where the columns are in one shape of sheet.

    `columns` maps a LOGICAL name the core understands to the header text the
    file prints. The header row is located by matching those texts, which
    handles hazard 9.9 for free: TBC's row 1 is Georgian and row 2 English, and
    matching `Balance` finds row 2 without anyone having to count rows.
    """

    name: str
    #: Regex matched against a sheet name to decide whether to read it.
    sheet_pattern: str
    columns: Dict[str, str]
    #: Logical columns that MUST be present for the header row to be accepted.
    required_columns: Tuple[str, ...]
    numbers: NumberFormat = PLAIN_NUMBERS
    #: `full` — the date cell carries its own year. `partial` — `D/MM`, resolved
    #: against the statement period (hazard 9.2).
    date_kind: str = "full"
    currency_from: str = CURRENCY_FROM_CONFIG
    #: Sheet-name -> ISO-4217, for `CURRENCY_FROM_SHEET_NAME`.
    sheet_currencies: Dict[str, str] = field(default_factory=dict)
    period: PeriodSpec = PeriodSpec()
    #: True when the balances describe a LIABILITY. Only an overpayment
    #: (positive) balance is an asset, which is the existing
    #: `credit_card_positive_balance_only` convention — so a liability timeline
    #: emits a signed series and a separate `max_positive_usd` rather than a
    #: bare `max_usd`, and the parser applies no convention (plan §4.4, §6.2).
    is_liability: bool = False
    #: Sheets to skip outright. A metadata or summary sheet is read for the
    #: period and for nothing else: the client name and street address in it
    #: must never reach a MovementRecord (plan §10.2).
    notes: str = ""


# ---------------------------------------------------------------------------
# Shape A — full transaction detail with a running balance
# ---------------------------------------------------------------------------

#: TBC (Georgia). Three files, one per currency, each two sheets: `Summary`
#: (label/value) and `<IBAN>-<CCY>` transactions. Bilingual Georgian/English
#: with DUAL HEADER ROWS — Georgian row 1, English row 2, data from row 3
#: (hazard 9.9). All three files share one IBAN prefix and the hand workpaper
#: treats them as a single account with three currency sub-wallets, which is
#: what `account_group` in sources.json records (§5.2).
#:
#: The `Summary` sheet's labels were not legible when this profile was written,
#: so the period comes from `sources.json`. Guessing a label here would be the
#: Wise mistake in a different costume.
TBC = SheetProfile(
    name="tbc",
    sheet_pattern=r"^(?P<account>.+)-(?P<currency>[A-Za-z]{3})$",
    columns={
        "date": "Date",
        "description": "Description",
        "paid_out": "Paid Out",
        "paid_in": "Paid In",
        "balance": "Balance",
    },
    required_columns=("date", "balance"),
    numbers=PLAIN_NUMBERS,
    date_kind="full",
    currency_from=CURRENCY_FROM_SHEET_SUFFIX,
    period=PeriodSpec(),
    notes=(
        "Low transaction volume, and a currency that may appear on a single "
        "row. This shape exercises the multi-currency merge, not the "
        "extraction."
    ),
)

#: Bancolombia savings. One sheet `Extracto`: a metadata block, `Resumen:`
#: totals, then `Movimientos:` with the header below. The only high-volume
#: running-balance source in the corpus.
#:
#: Two hazards live in this one sheet. `SUCURSAL` and `DCTO.` are empty on
#: every movement row, so a 6-column header yields 4 values — filtering `None`
#: slides `VALOR` into `SALDO` (hazard 9.1). And `FECHA` is `D/MM`, not
#: zero-padded, while the period in the header is `YYYY/MM/DD`: two date
#: formats in one file, so locale rules bind per FIELD (hazard 9.2).
BANCOLOMBIA_SAVINGS = SheetProfile(
    name="bancolombia_savings",
    sheet_pattern=r"^Extracto$",
    columns={
        "date": "FECHA",
        "description": "DESCRIPCIÓN",
        "branch": "SUCURSAL",
        "document": "DCTO.",
        "amount": "VALOR",
        "balance": "SALDO",
    },
    required_columns=("date", "amount", "balance"),
    numbers=COLOMBIAN_EXPORT_NUMBERS,
    date_kind="partial",
    currency_from=CURRENCY_FROM_CONFIG,
    period=PeriodSpec(
        start_labels=(r"per[ií]odo.*desde", r"fecha\s+inicial", r"desde"),
        end_labels=(r"per[ií]odo.*hasta", r"fecha\s+final", r"hasta"),
    ),
    notes=(
        "Cadence is QUARTERLY, and the first quarter OPENS ON THE PRIOR "
        "YEAR'S LAST DAY (e.g. 2024/12/31 for a 2025 Q1 file) — a period "
        "boundary that reaches outside the tax year."
    ),
)


# ---------------------------------------------------------------------------
# Shape B — period-end summaries only
# ---------------------------------------------------------------------------

#: T-Bank. Very small `.txt` files, each stating two bounding balances and
#: four totals. THERE IS NO TRANSACTION DETAIL: an account that peaked
#: mid-period and returned looks flat, which is why this shape must never yield
#: a determined maximum, however many files arrive (plan §6.3).
#:
#: Not a `SheetProfile` — there is no sheet. The reader is driven by the label
#: patterns below, and the numbers carry a space thousands separator, a comma
#: decimal, U+2212 for minus and `₽` (hazard 9.4).
@dataclass(frozen=True)
class TextSummaryProfile:
    name: str
    #: Regex with a `date` and a `balance` group, matched per line.
    balance_line: str
    numbers: NumberFormat = RUSSIAN_NUMBERS
    #: Regexes for the totals lines. Read so a warning can say what was
    #: DISCARDED — they are not movements and never become balance points.
    total_lines: Tuple[str, ...] = ()
    notes: str = ""


TBANK_TEXT = TextSummaryProfile(
    name="tbank_text",
    balance_line=r"^\s*Balance\s+on\s+(?P<date>\d{1,2}\.\d{1,2}\.\d{4})\s*:\s*(?P<balance>.+?)\s*$",
    numbers=RUSSIAN_NUMBERS,
    total_lines=(
        r"^\s*(?P<label>Income|Expenses|Interest|Cashback)\s*:\s*(?P<amount>.+?)\s*$",
    ),
    notes=(
        "Periods run the 22nd to the 21st, so a full set of monthly files "
        "still ends at December 21 and leaves December 22-31 uncovered "
        "(hazard 9.5). The period comes from the two balance lines — NEVER "
        "from the filename: these files are numbered in issue order, not "
        "calendar order, so `text 3.txt` need not be March (hazard 9.3)."
    ),
)


# ---------------------------------------------------------------------------
# Shape E — movement registers, transactions with NO balance of any kind
# ---------------------------------------------------------------------------

#: T-Bank's «О движении средств». A transaction list plus two period totals.
#: There is NO opening, closing or running balance anywhere in the document.
#:
#: This profile deliberately has no balance field. A register cannot yield a
#: level, only changes, and `derive_basis` caps a balance-free read at
#: `estimate` on exactly that ground. Deriving balances from the deltas would
#: manufacture the evidence the document declines to give.
@dataclass(frozen=True)
class MovementRegisterProfile:
    name: str
    #: Regex per transaction row. Named groups: `date_op`, `date_settle`,
    #: `amount_op`, `currency_op`, `amount_acct`, `currency_acct`. The ACCOUNT
    #: amount is the movement; the operation amount is what was charged in the
    #: merchant's currency and is read only to be discarded explicitly.
    txn_line: str
    #: Regex with `start` and `end` groups for the period the file covers.
    period_line: str
    #: Regex with a `value` group. The contract number identifies the account.
    contract_line: str
    #: Regex with a `value` group. May legitimately be absent — a credit
    #: contract prints "not opened" where a deposit account prints a number.
    account_line: str
    #: Regexes with `label` and `amount` groups. Read so the parse can be
    #: reconciled against the bank's own arithmetic, never as movements.
    total_lines: Tuple[str, ...] = ()
    numbers: NumberFormat = RUSSIAN_NUMBERS
    #: True when rows are printed newest-first and must be reversed.
    newest_first: bool = True
    notes: str = ""


TBANK_MOVEMENTS = MovementRegisterProfile(
    name="tbank_movements",
    txn_line=(
        r"^\s*(?P<date_op>\d{2}\.\d{2}\.\d{4})\s+"
        r"(?P<date_settle>\d{2}\.\d{2}\.\d{4})\s+"
        r"(?P<amount_op>[+−-][\d\s\u00a0\u202f]*\d[.,]\d{2})\s*(?P<currency_op>[₽$€R])\s+"
        r"(?P<amount_acct>[+−-][\d\s\u00a0\u202f]*\d[.,]\d{2})\s*(?P<currency_acct>[₽$€R])"
    ),
    period_line=(
        r"(?:Movement of funds for the period|Движение "
        r"средств за период с)\s+"
        r"(?P<start>\d{2}\.\d{2}\.\d{4})\s*(?:-|по)\s*(?P<end>\d{2}\.\d{2}\.\d{4})"
    ),
    contract_line=r"(?:Contract number|Номер договора):\s*(?P<value>\S+)",
    account_line=r"(?:Account number|Номер лицевого счета):\s*(?P<value>.+?)\s*$",
    total_lines=(
        r"^\s*(?P<label>Deposits|Пополнения):\s*(?P<amount>.+?)\s*$",
        r"^\s*(?P<label>Expenses|Расходы):\s*(?P<amount>.+?)\s*$",
    ),
    numbers=RUSSIAN_NUMBERS,
    newest_first=True,
    notes=(
        "States NO balance. Rows print newest-first, so a chronological read "
        "must reverse them. Amounts carry U+00A0 and U+202F inside digit "
        "groups far more often than the .xlsx exports do, because PDF text "
        "extraction preserves typographic spaces — RUSSIAN_NUMBERS and "
        "_assert_grouping between them are what stop that becoming a silent "
        "factor-of-1000 error (hazard 9.4)."
    ),
)


# ---------------------------------------------------------------------------
# Shape D — credit cards, dual-currency sheets
# ---------------------------------------------------------------------------

#: Bancolombia Mastercard. One file per card per billing period, each with TWO
#: sheets — `DOLARES` and `PESOS` — for the same card, plus a metadata block
#: carrying `Cupo Total`, the billing period and the masked card number.
#:
#: Where the December file is absent this profile CANNOT produce a
#: `statement_full_year` account however well it is written: December is the
#: month that establishes the year-end balance. That is the correct behaviour,
#: not a defect — the basis follows the coverage that actually arrived.
#:
#: The billing period runs ~30th to ~30th, so the January file OPENS IN THE
#: PRIOR CALENDAR YEAR (`30/12/2024 -> 30/01/2025`).
BANCOLOMBIA_CARD = SheetProfile(
    name="bancolombia_card",
    sheet_pattern=r"^(DOLARES|PESOS)$",
    columns={
        "date": "FECHA",
        "description": "DESCRIPCIÓN",
        "amount": "VALOR",
        "balance": "SALDO",
    },
    required_columns=("date", "amount"),
    numbers=COLOMBIAN_EXPORT_NUMBERS,
    date_kind="partial",
    currency_from=CURRENCY_FROM_SHEET_NAME,
    sheet_currencies={"DOLARES": "USD", "PESOS": "COP"},
    period=PeriodSpec(
        start_labels=(r"per[ií]odo\s+facturado\s+desde", r"desde"),
        end_labels=(r"per[ií]odo\s+facturado\s+hasta", r"hasta"),
    ),
    is_liability=True,
    notes=(
        "Credit-card balances are LIABILITIES. The parser reports the signed "
        "balance and flags the sign; whether an overpayment makes the account "
        "reportable is the `credit_card_positive_balance_only` convention, and "
        "that is Claude's call, not the parser's."
    ),
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PROFILES: Dict[str, object] = {
    TBC.name: TBC,
    BANCOLOMBIA_SAVINGS.name: BANCOLOMBIA_SAVINGS,
    BANCOLOMBIA_CARD.name: BANCOLOMBIA_CARD,
    TBANK_TEXT.name: TBANK_TEXT,
    TBANK_MOVEMENTS.name: TBANK_MOVEMENTS,
}

#: Institutions in the corpus for which NO profile may be written, with the
#: reason. Kept as data so the omission is visible rather than looking like an
#: oversight, and so a reader that is asked for one fails with the reason.
UNWRITABLE_PROFILES: Dict[str, str] = {
    "wise": (
        "The available exports' `All transactions` sheet is a single cell "
        "containing `ID` — not a header row — and none of `Running Balance`, "
        "`Exchange Rate` or `Total Fees` is present. Any column map would be "
        "written from expectation rather than evidence. Whatever settles the "
        "roster figure for such an account, it is not these files describing "
        "a layout they do not describe. Write this profile only when a "
        "non-empty export exists."
    ),
    "bcs": (
        "No statement files, in any format, to write a column map from. There "
        "is nothing to profile; the correct output is an explicit absent with "
        "a reason, feeding max_value_basis 'unknown' and the Item 15a "
        "decision to Claude."
    ),
}


def get_profile(name: str):
    """Look up a profile by name, refusing the ones the corpus cannot specify."""
    if name in PROFILES:
        return PROFILES[name]
    if name in UNWRITABLE_PROFILES:
        raise StatementParseError(
            f"No {name!r} profile exists, deliberately: {UNWRITABLE_PROFILES[name]}"
        )
    raise StatementParseError(
        f"Unknown profile {name!r}. Known: {', '.join(sorted(PROFILES))}."
    )


def currency_for_sheet(profile: SheetProfile, sheet_name: str,
                       configured: Optional[str] = None) -> str:
    """Resolve a sheet's currency the way its profile says to.

    Never from the filename. A filename is a naming convention someone chose;
    a sheet name is the export's own statement about what the sheet holds.
    """
    import re

    if profile.currency_from == CURRENCY_FROM_CONFIG:
        if not configured:
            raise StatementParseError(
                f"Profile {profile.name!r} takes its currency from sources.json, "
                f"but the source entry for sheet {sheet_name!r} sets none."
            )
        return configured.strip().upper()

    if profile.currency_from == CURRENCY_FROM_SHEET_NAME:
        currency = profile.sheet_currencies.get(sheet_name.strip().upper())
        if not currency:
            raise StatementParseError(
                f"Profile {profile.name!r} maps sheet names to currencies but "
                f"has no entry for {sheet_name!r}. Known: "
                f"{', '.join(sorted(profile.sheet_currencies))}."
            )
        return currency

    if profile.currency_from == CURRENCY_FROM_SHEET_SUFFIX:
        match = re.match(profile.sheet_pattern, sheet_name)
        if match is None or not match.groupdict().get("currency"):
            raise StatementParseError(
                f"Profile {profile.name!r} takes the currency from the sheet-name "
                f"suffix, but {sheet_name!r} does not match "
                f"{profile.sheet_pattern!r} with a `currency` group."
            )
        return match.group("currency").upper()

    raise StatementParseError(
        f"Profile {profile.name!r} has an unknown currency_from "
        f"{profile.currency_from!r}."
    )
