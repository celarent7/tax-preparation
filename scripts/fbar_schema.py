#!/usr/bin/env python3
"""Roster schema for FinCEN Form 114 (FBAR) — constants, enums, loader, normalizer.

One roster file per filer per year (`fbar_roster_<filer_slug>_<year>.json`)
holds every foreign financial account that filer had during the calendar year.
`fbar_reconciler.py` consumes two of them — current and prior — and guarantees
that every prior-year account resolves to an explicit disposition.

Design rules (docs/fbar_module_plan.md; docs/implementation_prompt.md):
  * Statutory figures come from the shared `fbar` block in
    `scripts/tax_constants.json` via `utils.get_constant`. Nothing here
    hard-codes the $10,000 threshold, the deadline, or a penalty amount.
  * Unknown keys are REJECTED with the nearest valid key named, following
    `savings_profile.load_profile`. A typo in a roster field must never
    silently become "field absent" — on this schema an absent
    `max_value_usd` reads as "not yet determined", which is a legitimate
    state, so a silent typo would look like a real filing position.
  * The account-number normalizer is a MATCHING aid only. Item 18 of the form
    takes the number exactly as the institution states it; `normalize_account_number`
    output must never be displayed or filed (see `assert_display_safe`).
"""

import json
import os
import sys
import unicodedata
from datetime import date, datetime
from difflib import get_close_matches
from typing import Any, Dict, List, Optional, Tuple

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import get_constant, load_tax_constants  # noqa: E402

SCHEMA_VERSION = "1.0"


class RosterError(ValueError):
    """Raised when a roster file fails schema validation."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

FILING_CAPACITIES = ("individual", "partnership", "corporation", "fiduciary")

#: Where an exchange rate came from. Only "treasury" satisfies the FinCEN
#: instruction (validator V1); the other members exist so a non-conforming rate
#: is recorded honestly instead of being mislabelled. FBAR module plan D4 is a
#: case where the same numeric rate was labelled "xe.com" in one workpaper and
#: "U.S. Treasury Reporting Rate" in another.
FX_SOURCES = ("treasury", "xe", "other")

ACCOUNT_TYPES = ("bank", "securities", "other")

#: "signature_authority" is accepted by the schema but NOT reconciled in v1 —
#: it belongs to Part IV, which is out of scope (plan §2). The reconciler
#: reports such accounts rather than pretending to have handled them.
OWNERSHIPS = ("separate", "joint", "signature_authority")

ACCOUNT_STATUSES = ("open", "closed", "dormant")

#: How the reported maximum was established. Drives V12: a reportable account
#: whose basis is weaker than a full year of statements, and which has NOT
#: elected the Item 15a "amount unknown" checkbox, is asserting a maximum it
#: cannot support.
MAX_VALUE_BASES = (
    "statement_full_year",       # statements covering the whole year (FinCEN's safe harbour)
    "statement_partial",         # statements for part of the year only
    "period_end_summaries_only",  # period-end balances; intra-period peaks invisible
    "estimate",
    "unknown",
)

IDENTITY_CHANGE_TYPES = ("split", "merge", "renumber")

#: What the workspace actually holds as prior-year evidence. "form_114_filed"
#: means a BSA E-Filing acknowledgment copy; "form_114_review_copy_pdf" means a
#: preparer review copy, which is what the current cycle actually has. The
#: distinction is recorded rather than smoothed over — they are not the same
#: evidence, and the reconciliation report says which one it read.
PRIOR_YEAR_SOURCES = (
    "form_114_filed",
    "form_114_review_copy_pdf",
    "roster_json",
    "workpaper",
    "none",
)


# ---------------------------------------------------------------------------
# Filer-adopted conventions (plan §3.1)
# ---------------------------------------------------------------------------

#: Treatments the workspace applies that could NOT be confirmed against FinCEN
#: guidance. They are load-bearing, so the module carries them as conventions
#: with a recorded rationale and prints them in the report, rather than as
#: settled law. A year-over-year CHANGE in an account's applied conventions is
#: validator V9.
CONVENTIONS: Dict[str, Dict[str, str]] = {
    "multi_currency_single_account": {
        "statement": (
            "Currency sub-wallets sharing one account number are reported as a "
            "single account; the currencies are summed at each point in time to "
            "find the combined maximum."
        ),
        "status": "unconfirmed",
        "note": (
            "FinCEN guidance addresses aggregation FOR THE THRESHOLD, not "
            "per-entry granularity for sub-accounts. Plausible and internally "
            "consistent, but unconfirmed — and the opposite treatment has been "
            "applied to the same institution family in an adjacent year, which "
            "is exactly the reporting-model change §7.3 requires a written "
            "justification for."
        ),
    },
    "credit_card_positive_balance_only": {
        "statement": (
            "A credit card account is reportable only if it carried a positive "
            "balance (the bank owes the holder); a negative or zero balance is "
            "not reportable."
        ),
        "status": "unconfirmed",
        "note": (
            "Directionally consistent with a credit card not being a financial "
            "account absent a credit balance owed to the holder, but "
            "unconfirmed as phrased."
        ),
    },
    "negative_balance_is_zero": {
        "statement": "A negative balance is treated as $0 rather than a negative maximum.",
        "status": "conservative",
        "note": "Reasonable and conservative; recorded as a convention, not a rule.",
    },
}


# ---------------------------------------------------------------------------
# Account-number normalization (plan §7.1)
# ---------------------------------------------------------------------------

#: Confusable folding for the Cyrillic and Greek capitals that are visually
#: identical to a Latin capital. Naive punctuation stripping is not enough:
#: the motivating defect (plan D5) is one brokerage account rendered with a
#: trailing Latin "M" on the filed form and a Cyrillic "м" in the workpaper,
#: which a byte-equality match reports as one closed plus one new account.
#:
#: Folding runs AFTER upper-casing, so only the capital forms need entries —
#: `str.upper()` maps Cyrillic "м" to "М" and Greek "μ" to "Μ" first. A table
#: of lowercase pairs alone would fail the very case it exists for.
#:
#: Letter-to-letter only. Cyrillic "З"/Greek "Ο" style letter-to-DIGIT folds
#: (З→3) are deliberately excluded: they would collapse genuinely distinct
#: identifiers, and a false merge is worse here than a false split — a false
#: split surfaces as a reviewable finding, a false merge hides an account.
CONFUSABLE_FOLD: Dict[str, str] = {
    # Cyrillic capitals -> Latin
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
    "О": "O", "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X",
    "І": "I", "Ј": "J", "Ѕ": "S", "Ԛ": "Q", "Ԝ": "W",
    # Greek capitals -> Latin
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I",
    "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T",
    "Υ": "Y", "Χ": "X",
}

#: Punctuation institutions add or drop freely when rendering an account
#: number. Stripped for matching only.
SEPARATORS = frozenset(" \t\r\n/-.#_\\,()[]:")


def normalize_account_number(value: Optional[str]) -> str:
    """Fold an account number to a matching key. NEVER use the result for display.

    Steps, in this order:
      1. Unicode NFKC (collapses fullwidth/compatibility forms; NFKC does NOT
         fold Cyrillic to Latin, which is why step 3 exists).
      2. Drop format characters (zero-width space/joiner and friends), which
         survive NFKC and would otherwise defeat equality.
      3. Upper-case, then apply `CONFUSABLE_FOLD`.
      4. Strip `SEPARATORS`.

    `12345678M` and `123456/78-м` both normalize to `12345678M` — the D5 shape.
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = text.upper()
    text = "".join(CONFUSABLE_FOLD.get(ch, ch) for ch in text)
    return "".join(ch for ch in text if ch not in SEPARATORS)


def assert_display_safe(displayed: str, account: Dict[str, Any]) -> None:
    """Guard that a value bound for Item 18 is the institution's rendering.

    Item 18 of Form 114 takes the account number as the institution states it.
    The normalizer exists purely to join records across years; emitting its
    output onto a worksheet would put a fabricated identifier on a filing.
    """
    if displayed != account.get("account_number"):
        raise RosterError(
            "Refusing to emit a normalized account number for display. Item 18 "
            "takes the number as the institution states it; "
            f"account_key={account.get('account_key')!r} would have been shown "
            f"as {displayed!r} instead of {account.get('account_number')!r}."
        )


# ---------------------------------------------------------------------------
# Field tables
# ---------------------------------------------------------------------------

#: Every recognized account key, with its default. `None` means "not supplied",
#: which is meaningful on this schema (an absent `max_value_usd` is an
#: undetermined maximum, not zero).
ACCOUNT_FIELDS: Dict[str, Any] = {
    "account_key": None,                 # REQUIRED — the stable cross-year join key
    "aliases": [],                       # every identifier this account has been filed/recorded under
    "institution": {},
    "account_number": None,              # REQUIRED — as the institution states it (Item 18)
    "account_number_normalized": None,   # derived from account_number when absent
    "account_type": None,                # REQUIRED — Item 16 (BANK / SECURITIES / OTHER)
    "account_type_other_desc": None,     # the free text beside Item 16's OTHER checkbox
    "ownership": "separate",
    "joint_owners": [],
    "currencies": [],
    "max_value_local": None,
    "max_value_currency": None,
    "max_value_components": None,        # multi-currency peaks: [{"currency","amount"}, …]
    "max_value_date": None,
    "max_value_usd": None,               # Item 15
    "max_value_basis": "unknown",
    "max_value_unknown": False,          # Item 15a checkbox
    "year_end_balance_usd": None,
    "reportable": True,
    "not_reportable_reason": None,
    "account_status": "open",
    "closed_date": None,
    "opened_date": None,
    "conventions_applied": [],
    "source_documents": [],
    "notes": None,
}

_ACCOUNT_REQUIRED = ("account_key", "account_number", "account_type")

INSTITUTION_FIELDS = ("name", "country", "address", "swift_bic")
ADDRESS_FIELDS = ("street", "city", "state_province", "postal_code")

FILER_FIELDS: Dict[str, Any] = {
    "slug": None,                        # REQUIRED — stable, non-identifying
    "name": None,                        # free text; stays in the local workspace
    "filing_capacity": "individual",
    "files_separately_from_spouse": True,
    # Form 114a has TWO independent uses. Conflating them hard-errors V10 on
    # the correct, normal pattern (separate FBARs plus a third-party preparer).
    "form_114a_spousal": False,          # spouse authorized to file on the other's behalf
    "form_114a_preparer": False,         # third-party preparer authorized (Form 114 box 44a)
}

FX_RATE_FIELDS: Dict[str, Any] = {
    "currency": None,                    # REQUIRED — ISO-4217
    "units_per_usd": None,               # REQUIRED — foreign units per ONE USD (Treasury's convention)
    "as_of": None,                       # REQUIRED — must be Dec 31 of the tax year (V2)
    "source": None,                      # REQUIRED — FX_SOURCES
    "source_url": None,
}

IDENTITY_CHANGE_FIELDS: Dict[str, Any] = {
    "type": None,                        # REQUIRED — IDENTITY_CHANGE_TYPES
    "prior_account_keys": [],
    "current_account_keys": [],
    "reason": None,
    "reporting_model_changed": False,
    "justification": None,               # REQUIRED when reporting_model_changed
}

PRIOR_YEAR_FIELDS: Dict[str, Any] = {
    "tax_year": None,
    "roster_file": None,
    "source": "none",
}

ROSTER_FIELDS: Dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "tax_year": None,                    # REQUIRED
    "filer": None,                       # REQUIRED
    "fx_rates": [],
    "accounts": [],
    "identity_changes": [],
    "prior_year": None,
}


# ---------------------------------------------------------------------------
# Validation primitives
# ---------------------------------------------------------------------------


def _suggest(unknown: str, valid) -> str:
    matches = get_close_matches(unknown, list(valid), n=1, cutoff=0.6)
    return f" Did you mean {matches[0]!r}?" if matches else ""


def _reject_unknown(raw: Dict[str, Any], valid, where: str) -> None:
    """Reject unrecognized keys, naming the nearest valid one (repo convention)."""
    unknown = [k for k in raw if k not in valid and not k.startswith("_")]
    if not unknown:
        return
    hints = "".join(f"\n  {k!r}:{_suggest(k, valid)}" for k in sorted(unknown))
    raise RosterError(
        f"Unknown key(s) in {where}, refusing to proceed silently:{hints}\n"
        f"Valid keys: {', '.join(sorted(valid))}."
    )


def _require_enum(value: Any, allowed: Tuple[str, ...], where: str) -> str:
    if value not in allowed:
        raise RosterError(
            f"{where} is {value!r}; must be one of: {', '.join(allowed)}."
        )
    return value


def parse_date(value: Any, where: str) -> Optional[date]:
    """Parse an ISO `YYYY-MM-DD` date, or None. Raises on anything else."""
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError as exc:
        raise RosterError(f"{where} must be an ISO date (YYYY-MM-DD), got {value!r}.") from exc


def _number(value: Any, where: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RosterError(f"{where} must be a number, got {value!r}.")
    return float(value)


# ---------------------------------------------------------------------------
# Section validators
# ---------------------------------------------------------------------------


def _validate_filer(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise RosterError("roster.filer must be an object.")
    _reject_unknown(raw, FILER_FIELDS, "roster.filer")
    filer = {k: raw.get(k, v) for k, v in FILER_FIELDS.items()}
    if not filer["slug"]:
        raise RosterError(
            "roster.filer.slug is required. Use a stable, non-identifying slug "
            "(e.g. 'primary', 'spouse') — it is the roster's join identity "
            "across years and appears in output filenames."
        )
    _require_enum(filer["filing_capacity"], FILING_CAPACITIES, "roster.filer.filing_capacity")
    for flag in ("files_separately_from_spouse", "form_114a_spousal", "form_114a_preparer"):
        if not isinstance(filer[flag], bool):
            raise RosterError(f"roster.filer.{flag} must be true or false.")
    return filer


def _validate_fx_rate(raw: Any, index: int) -> Dict[str, Any]:
    where = f"roster.fx_rates[{index}]"
    if not isinstance(raw, dict):
        raise RosterError(f"{where} must be an object.")
    _reject_unknown(raw, FX_RATE_FIELDS, where)
    rate = {k: raw.get(k, v) for k, v in FX_RATE_FIELDS.items()}

    if not rate["currency"]:
        raise RosterError(f"{where}.currency is required (ISO-4217, e.g. 'EUR').")
    rate["currency"] = str(rate["currency"]).strip().upper()

    units = _number(rate["units_per_usd"], f"{where}.units_per_usd")
    if units is None or units <= 0:
        raise RosterError(
            f"{where}.units_per_usd is required and must be positive. It is "
            "foreign currency units per ONE US dollar — the Treasury Reporting "
            "Rate of Exchange convention. Storing the reciprocal here silently "
            "inverts every conversion for this currency."
        )
    rate["units_per_usd"] = units
    rate["as_of"] = parse_date(rate["as_of"], f"{where}.as_of")
    if rate["as_of"] is None:
        raise RosterError(f"{where}.as_of is required (the rate date, YYYY-MM-DD).")
    _require_enum(rate["source"], FX_SOURCES, f"{where}.source")
    return rate


def _validate_institution(raw: Any, where: str) -> Dict[str, Any]:
    if raw in (None, {}):
        return {"name": None, "country": None, "address": {}, "swift_bic": None}
    if not isinstance(raw, dict):
        raise RosterError(f"{where} must be an object.")
    _reject_unknown(raw, INSTITUTION_FIELDS, where)
    inst = {k: raw.get(k) for k in INSTITUTION_FIELDS}
    address = inst.get("address") or {}
    if not isinstance(address, dict):
        raise RosterError(f"{where}.address must be an object.")
    _reject_unknown(address, ADDRESS_FIELDS, f"{where}.address")
    inst["address"] = {k: address.get(k) for k in ADDRESS_FIELDS}
    return inst


def _validate_account(raw: Any, index: int) -> Dict[str, Any]:
    where = f"roster.accounts[{index}]"
    if not isinstance(raw, dict):
        raise RosterError(f"{where} must be an object.")
    _reject_unknown(raw, ACCOUNT_FIELDS, where)

    acct: Dict[str, Any] = {}
    for key, default in ACCOUNT_FIELDS.items():
        value = raw.get(key, default)
        # Never share a mutable default between records.
        acct[key] = list(default) if isinstance(default, list) and value is default else value

    for key in _ACCOUNT_REQUIRED:
        if acct[key] in (None, ""):
            raise RosterError(f"{where}.{key} is required.")

    acct["account_key"] = str(acct["account_key"]).strip()
    acct["account_number"] = str(acct["account_number"])

    _require_enum(acct["account_type"], ACCOUNT_TYPES, f"{where}.account_type")
    if acct["account_type"] == "other" and not acct["account_type_other_desc"]:
        raise RosterError(
            f"{where}.account_type is 'other', so account_type_other_desc is "
            "required — Item 16 of Form 114 carries a free-text description "
            "beside the OTHER checkbox."
        )
    _require_enum(acct["ownership"], OWNERSHIPS, f"{where}.ownership")
    _require_enum(acct["account_status"], ACCOUNT_STATUSES, f"{where}.account_status")
    _require_enum(acct["max_value_basis"], MAX_VALUE_BASES, f"{where}.max_value_basis")

    for flag in ("max_value_unknown", "reportable"):
        if not isinstance(acct[flag], bool):
            raise RosterError(f"{where}.{flag} must be true or false.")

    for list_field in ("aliases", "joint_owners", "currencies",
                       "conventions_applied", "source_documents"):
        if acct[list_field] is None:
            acct[list_field] = []
        if not isinstance(acct[list_field], list):
            raise RosterError(f"{where}.{list_field} must be a list.")
    acct["aliases"] = [str(a) for a in acct["aliases"]]
    acct["currencies"] = [str(c).strip().upper() for c in acct["currencies"]]

    acct["institution"] = _validate_institution(acct["institution"], f"{where}.institution")

    for money_field in ("max_value_local", "max_value_usd", "year_end_balance_usd"):
        acct[money_field] = _number(acct[money_field], f"{where}.{money_field}")

    if acct["max_value_currency"]:
        acct["max_value_currency"] = str(acct["max_value_currency"]).strip().upper()

    acct["max_value_components"] = _validate_components(
        acct["max_value_components"], f"{where}.max_value_components")
    if acct["max_value_components"] and acct["max_value_local"] is not None:
        raise RosterError(
            f"{where} sets both max_value_local and max_value_components. Pick "
            "one: components describe a multi-currency account whose reported "
            "maximum is the USD sum of its per-currency amounts at the "
            "combined-peak instant, so a single local amount is ambiguous."
        )

    for date_field in ("max_value_date", "closed_date", "opened_date"):
        acct[date_field] = parse_date(acct[date_field], f"{where}.{date_field}")

    if acct["account_status"] == "closed" and acct["closed_date"] is None:
        raise RosterError(
            f"{where}.account_status is 'closed' but closed_date is missing. "
            "The date decides whether the account is CLOSED during the year or "
            "CLOSED_PRIOR_YEAR, and the two carry different filing duties."
        )
    if acct["max_value_unknown"] and acct["max_value_usd"] is not None:
        raise RosterError(
            f"{where} sets max_value_unknown (Item 15a) AND a max_value_usd of "
            f"{acct['max_value_usd']}. Item 15a means the maximum could not be "
            "determined; supplying a figure contradicts the checkbox."
        )

    if acct["account_number_normalized"] in (None, ""):
        acct["account_number_normalized"] = normalize_account_number(acct["account_number"])
    else:
        acct["account_number_normalized"] = str(acct["account_number_normalized"])

    acct["normalized_aliases"] = sorted({
        normalize_account_number(a) for a in acct["aliases"] if str(a).strip()
    } | {acct["account_number_normalized"]})

    return acct


def _validate_components(raw: Any, where: str) -> Optional[List[Dict[str, Any]]]:
    if raw in (None, []):
        return None
    if not isinstance(raw, list):
        raise RosterError(f"{where} must be a list of {{currency, amount}} objects.")
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RosterError(f"{where}[{i}] must be an object.")
        _reject_unknown(item, ("currency", "amount"), f"{where}[{i}]")
        currency = item.get("currency")
        if not currency:
            raise RosterError(f"{where}[{i}].currency is required.")
        amount = _number(item.get("amount"), f"{where}[{i}].amount")
        if amount is None:
            raise RosterError(f"{where}[{i}].amount is required.")
        out.append({"currency": str(currency).strip().upper(), "amount": amount})
    return out


def _validate_identity_change(raw: Any, index: int) -> Dict[str, Any]:
    where = f"roster.identity_changes[{index}]"
    if not isinstance(raw, dict):
        raise RosterError(f"{where} must be an object.")
    _reject_unknown(raw, IDENTITY_CHANGE_FIELDS, where)
    change = {k: raw.get(k, v) for k, v in IDENTITY_CHANGE_FIELDS.items()}
    _require_enum(change["type"], IDENTITY_CHANGE_TYPES, f"{where}.type")
    for list_field in ("prior_account_keys", "current_account_keys"):
        if not isinstance(change[list_field], list) or not change[list_field]:
            raise RosterError(f"{where}.{list_field} must be a non-empty list of account_keys.")
        change[list_field] = [str(k) for k in change[list_field]]
    if not isinstance(change["reporting_model_changed"], bool):
        raise RosterError(f"{where}.reporting_model_changed must be true or false.")
    return change


def _validate_prior_year(raw: Any) -> Optional[Dict[str, Any]]:
    if raw in (None, {}):
        return None
    if not isinstance(raw, dict):
        raise RosterError("roster.prior_year must be an object.")
    _reject_unknown(raw, PRIOR_YEAR_FIELDS, "roster.prior_year")
    prior = {k: raw.get(k, v) for k, v in PRIOR_YEAR_FIELDS.items()}
    if prior["tax_year"] is not None:
        prior["tax_year"] = int(prior["tax_year"])
    _require_enum(prior["source"], PRIOR_YEAR_SOURCES, "roster.prior_year.source")
    return prior


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------


def validate_roster(raw: Any, origin: str = "<roster>") -> Dict[str, Any]:
    """Validate and normalize a roster mapping. Returns the canonical form.

    Raises RosterError with the offending path on any structural problem.
    Statutory checks (FX provenance, threshold arithmetic, 25-account rule)
    are NOT done here — they are the reconciler's validators, which report
    findings by severity instead of refusing to load the file.
    """
    if not isinstance(raw, dict):
        raise RosterError(f"{origin}: roster must be a JSON object, got {type(raw).__name__}.")
    _reject_unknown(raw, ROSTER_FIELDS, f"{origin} (top level)")

    roster = {k: raw.get(k, v) for k, v in ROSTER_FIELDS.items()}

    version = str(roster["schema_version"])
    if version.split(".")[0] != SCHEMA_VERSION.split(".")[0]:
        raise RosterError(
            f"{origin}: schema_version {version!r} is not compatible with this "
            f"module's {SCHEMA_VERSION!r} (major versions must match)."
        )
    roster["schema_version"] = version

    if roster["tax_year"] is None:
        raise RosterError(f"{origin}: tax_year is required.")
    roster["tax_year"] = int(roster["tax_year"])

    roster["filer"] = _validate_filer(roster["filer"])

    for name, validator in (("fx_rates", _validate_fx_rate),
                            ("accounts", _validate_account),
                            ("identity_changes", _validate_identity_change)):
        items = roster[name] or []
        if not isinstance(items, list):
            raise RosterError(f"{origin}: roster.{name} must be a list.")
        roster[name] = [validator(item, i) for i, item in enumerate(items)]

    roster["prior_year"] = _validate_prior_year(roster["prior_year"])

    seen: Dict[str, int] = {}
    for i, acct in enumerate(roster["accounts"]):
        key = acct["account_key"]
        if key in seen:
            raise RosterError(
                f"{origin}: duplicate account_key {key!r} at accounts[{seen[key]}] "
                f"and accounts[{i}]. The key is the cross-year join identity and "
                "must be unique within a roster."
            )
        seen[key] = i

    known_keys = set(seen)
    for i, change in enumerate(roster["identity_changes"]):
        missing = [k for k in change["current_account_keys"] if k not in known_keys]
        if missing:
            raise RosterError(
                f"{origin}: identity_changes[{i}] names current_account_keys "
                f"{missing} that are not in this roster's accounts."
            )

    roster["_origin"] = origin
    return roster


def load_roster(path: str) -> Dict[str, Any]:
    """Load and validate a roster JSON file."""
    try:
        with open(path, "r") as f:
            raw = json.load(f)
    except FileNotFoundError as exc:
        raise RosterError(f"Roster file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RosterError(f"{path} is not valid JSON: {exc}") from exc
    return validate_roster(raw, origin=path)


def roster_to_json(roster: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize a validated roster back to plain JSON types.

    The round trip is the point: this year's emitted roster is next year's
    `--prior` input, which is what makes the reconciliation self-sustaining
    after the first cycle.

    `identity_changes` are deliberately NOT carried into the emitted roster.
    They describe the transition *into* this year — a split's
    `prior_account_keys` name accounts that, by the time this roster is a
    baseline, no longer exist on it. Carrying them forward would make next
    year's run fail on a link it cannot verify, which is the opposite of
    self-sustaining. The declarations, their reasons and their justifications
    are retained in the reconciliation report and findings JSON written
    alongside, which is what the five-year recordkeeping rule needs; the link
    itself survives in each account's `aliases[]`.
    """
    def plain(value: Any) -> Any:
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, dict):
            return {k: plain(v) for k, v in value.items() if not k.startswith("_")}
        if isinstance(value, list):
            return [plain(v) for v in value]
        return value

    out = {k: plain(roster[k]) for k in ROSTER_FIELDS if k in roster}
    out["identity_changes"] = []
    for acct in out["accounts"]:
        acct.pop("normalized_aliases", None)
    return out


# ---------------------------------------------------------------------------
# Statutory lookups (never hard-coded — always via tax_constants.json)
# ---------------------------------------------------------------------------


def load_fbar_constants(year: Optional[str] = None) -> Dict[str, Any]:
    """The shared `fbar` block from tax_constants.json."""
    return get_constant(load_tax_constants(year), "fbar")


def fbar_threshold(fbar: Dict[str, Any]) -> float:
    """The aggregate reporting threshold in USD."""
    return float(get_constant(fbar, "threshold_usd"))


def threshold_tripped(aggregate_usd: float, fbar: Dict[str, Any]) -> bool:
    """True when the aggregate maximum EXCEEDS the threshold.

    The statute says "exceeded $10,000 at any time", so an aggregate of exactly
    $10,000 does NOT require a filing. `threshold_test` carries that comparison
    so the boundary can never drift into `>=` unnoticed.
    """
    test = get_constant(fbar, "threshold_test")
    threshold = fbar_threshold(fbar)
    if test == "exceeds":
        return aggregate_usd > threshold
    if test == "meets_or_exceeds":
        return aggregate_usd >= threshold
    raise RosterError(f"Unknown fbar.threshold_test {test!r} in tax_constants.json.")


def fbar_deadlines(tax_year: int, fbar: Dict[str, Any]) -> Dict[str, date]:
    """Original and automatically-extended due dates for a tax year.

    Derived from the month/day rule in the constants rather than stored per
    year, so any supported tax year answers without a data edit.
    """
    deadline = get_constant(fbar, "deadline")
    filing_year = int(tax_year) + int(get_constant(deadline, "offset_years"))
    return {
        "due": date(filing_year, get_constant(deadline, "due_month"),
                    get_constant(deadline, "due_day")),
        "extended": date(filing_year, get_constant(deadline, "extended_month"),
                         get_constant(deadline, "extended_day")),
        "extension_is_automatic": bool(get_constant(deadline, "extension_is_automatic")),
    }


def record_retention_until(tax_year: int, fbar: Dict[str, Any]) -> date:
    """Last date the supporting records must still be retained.

    Five years from the FBAR DUE DATE — not from the filing date and not from
    the end of the tax year.
    """
    years = int(get_constant(fbar, "recordkeeping_years"))
    due = fbar_deadlines(tax_year, fbar)["due"]
    return due.replace(year=due.year + years)


def fbar_penalty_maximum(fbar: Dict[str, Any],
                         assessed_on: Optional[date] = None) -> Dict[str, Any]:
    """Inflation-adjusted penalty maxima for the column in force on a date.

    31 CFR 1010.821 indexes these by the date the penalty is ASSESSED, not by
    the tax year reported on — a TY2025 FBAR assessed in 2027 takes the 2027
    column. The table is cumulative: each annual adjustment adds a column, so
    the applicable one is the latest whose effective date is on or before
    `assessed_on` (default: today).

    Returns the amounts plus the column used, so output can state which one.
    """
    table = get_constant(fbar, "penalties_by_assessment_date")
    on = assessed_on or date.today()
    effective = sorted(
        (parse_date(k, "fbar.penalties_by_assessment_date key"), k) for k in table
    )
    applicable = [(d, k) for d, k in effective if d <= on]
    if not applicable:
        earliest, key = effective[0]
        raise RosterError(
            f"No penalty column in tax_constants.json applies to an assessment "
            f"on {on.isoformat()}; the earliest recorded column takes effect "
            f"{earliest.isoformat()} ({key})."
        )
    used_date, used_key = applicable[-1]
    column = table[used_key]
    return {
        "effective_date": used_date,
        "column": used_key,
        "non_willful_max": get_constant(column, "non_willful_max"),
        "willful_max": get_constant(column, "willful_max"),
        "willful_balance_percent": get_constant(column, "willful_balance_percent"),
        "unit": get_constant(fbar, "penalty_unit"),
        "is_latest_column": used_key == effective[-1][1],
    }
