#!/usr/bin/env python3
"""FinCEN 114 (FBAR) year-over-year reconciliation, validation and worksheets.

The module exists for one reason (docs/fbar_module_plan.md §1): the
year-over-year account reconciliation was being done by memory and
inconsistently — one filer's roster carried a prior-year column, the other's
did not, so nothing guaranteed that every account on last year's filing was
carried forward, closed out, or consciously dropped.

The minimum bar this enforces: **for every prior-year FBAR record there is a
clear answer.** An account that vanishes between years without a disposition is
a blocking finding, not a silent omission.

What it does NOT do (plan §2, and deliberately):
  * No BSA E-Filing XML or PDF. The worksheet is a keying aid; transmission
    goes through the taxpayer or their preparer.
  * No statement parsing. Maxima arrive as normalized JSON from Phase 1,
    consistent with every other tool in this plugin.
  * No Form 8938/FATCA, no Part IV (signature authority only), no Part V
    (consolidated). The schema reserves the fields; v1 reconciles Part II
    (separately owned) and Part III (jointly owned).

Severity contract:
  blocking / error -> exit 1, payload carries `"isError": true`
  warn / info / ok -> exit 0
Warnings never block. That is load-bearing: the Item 15a "amount unknown"
election and a non-Treasury rate awaiting replacement are legitimate interim
states, and a tool that refuses to run on them would just be worked around.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import get_constant  # noqa: E402
from fbar_schema import (  # noqa: E402
    CONVENTIONS,
    RosterError,
    assert_display_safe,
    fbar_deadlines,
    fbar_penalty_maximum,
    fbar_threshold,
    load_fbar_constants,
    load_roster,
    normalize_account_number,
    record_retention_until,
    roster_to_json,
    threshold_tripped,
)

# Severity ordering, most severe first. `blocking` and `error` both stop the
# run; they are kept distinct because they answer different questions —
# `blocking` means the reconciliation has an unanswered question about an
# account, `error` means a stated figure or flag is internally wrong.
SEVERITIES = ("blocking", "error", "warn", "info", "ok")
_SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}
STOPPING_SEVERITIES = ("blocking", "error")

SEVERITY_MARK = {
    "blocking": "🛑 BLOCKING",
    "error": "🔴 ERROR",
    "warn": "🟡 WARN",
    "info": "🔵 INFO",
    "ok": "✅ OK",
}

# --- Prior-year dispositions (plan §7.2) ----------------------------------
CARRIED_FORWARD = "CARRIED_FORWARD"
CLOSED = "CLOSED"
CLOSED_PRIOR_YEAR = "CLOSED_PRIOR_YEAR"
DORMANT_ZERO = "DORMANT_ZERO"
RENUMBERED = "RENUMBERED"
SPLIT = "SPLIT"
MERGED = "MERGED"
UNRESOLVED = "UNRESOLVED"

# --- Current-year dispositions --------------------------------------------
NEW_OPENED = "NEW_OPENED"
NEW_PREVIOUSLY_UNREPORTED = "NEW_PREVIOUSLY_UNREPORTED"
NEW_UNEXPLAINED = "NEW_UNEXPLAINED"

DISPOSITION_SEVERITY = {
    CARRIED_FORWARD: "ok",
    CLOSED: "ok",
    CLOSED_PRIOR_YEAR: "ok",
    DORMANT_ZERO: "ok",
    RENUMBERED: "warn",
    SPLIT: "warn",
    MERGED: "warn",
    UNRESOLVED: "blocking",
    NEW_OPENED: "ok",
    NEW_PREVIOUSLY_UNREPORTED: "warn",
    NEW_UNEXPLAINED: "blocking",
}

#: How each prior-year account was matched to a current-year one. Recorded so a
#: reviewer can see whether a match rested on a declared identity change, a
#: stable key, or bare digit equality.
MATCH_EXACT_KEY = "account_key"
MATCH_ALIAS = "alias"
MATCH_NORMALIZED_NUMBER = "normalized_account_number"
MATCH_IDENTITY_CHANGE = "identity_change"
MATCH_FUZZY_SUGGESTION = "fuzzy_suggestion"

#: Trailing-digit run length for the fuzzy candidate rule (plan §4.1 step 4).
FUZZY_TAIL_DIGITS = 4


@dataclass
class Finding:
    """One reviewable statement about the reconciliation."""
    id: str                       # "V3", "D-UNRESOLVED", "IC1" …
    severity: str
    message: str
    account_key: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.severity not in _SEVERITY_RANK:
            raise ValueError(f"Unknown severity {self.severity!r}.")

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self.severity]


def sort_findings(findings: List[Finding]) -> List[Finding]:
    """Most severe first, then by id, then by account — a stable report order."""
    return sorted(findings, key=lambda f: (f.rank, f.id, f.account_key or ""))


def stops(findings: List[Finding]) -> bool:
    return any(f.severity in STOPPING_SEVERITIES for f in findings)


# ---------------------------------------------------------------------------
# Matching (plan §4.1)
# ---------------------------------------------------------------------------


def _trailing_digits(normalized: str, n: int = FUZZY_TAIL_DIGITS) -> str:
    """The last `n` digits of a normalized identifier, or "" if too short."""
    digits = "".join(ch for ch in normalized if ch.isdigit())
    return digits[-n:] if len(digits) >= n else ""


def _institution_key(account: Dict[str, Any]) -> str:
    name = (account.get("institution") or {}).get("name") or ""
    return normalize_account_number(name)


def _declared_links(current: Dict[str, Any]) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """Prior->current and current->prior links declared in `identity_changes`."""
    prior_to_current: Dict[str, List[str]] = {}
    current_to_prior: Dict[str, List[str]] = {}
    for change in current.get("identity_changes", []):
        for prior_key in change["prior_account_keys"]:
            prior_to_current.setdefault(prior_key, []).extend(change["current_account_keys"])
        for current_key in change["current_account_keys"]:
            current_to_prior.setdefault(current_key, []).extend(change["prior_account_keys"])
    return prior_to_current, current_to_prior


def match_accounts(current: Dict[str, Any],
                   prior: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Resolve prior-year accounts onto current-year accounts.

    The ladder, in order, exactly as plan §4.1 specifies:
      1. declared `identity_changes`
      2. exact `account_key`
      3. normalized account number present in the other record's aliases
      4. normalized account-number equality
      5. same institution + same trailing 4+ digits -> SUGGEST ONLY
      6. no match

    Each rung is a full pass over the prior accounts before the next one runs.
    Applying the ladder per account instead would let a weaker rung on an
    earlier prior account consume a current account that a later prior account
    matches on a stronger one — two prior accounts resolving to a single
    current record, one of them collecting a carry-forward it never earned.
    That is precisely the silent disappearance this module exists to prevent,
    so a current account is claimed exactly once and any prior account left
    over surfaces as UNRESOLVED.

    The final step never auto-accepts. A fuzzy match that silently linked two
    accounts would defeat the whole point: the module exists to make unmatched
    accounts visible, not to make them disappear.
    """
    prior_accounts = list(prior["accounts"]) if prior else []
    current_accounts = list(current["accounts"])
    declared_p2c, _ = _declared_links(current)

    by_key = {a["account_key"]: a for a in current_accounts}
    matched_current_keys: set = set()
    matches: Dict[str, Dict[str, Any]] = {}
    suggestions: List[Dict[str, Any]] = []

    # Rung 1 — the filer's own written declaration wins over anything inferred.
    for pa in prior_accounts:
        pkey = pa["account_key"]
        declared = [k for k in declared_p2c.get(pkey, []) if k in by_key]
        if declared:
            matches[pkey] = {"current_keys": sorted(set(declared)),
                             "method": MATCH_IDENTITY_CHANGE}
            matched_current_keys.update(declared)

    # Rung 2 — exact account_key, unless a declaration already claimed it.
    for pa in prior_accounts:
        pkey = pa["account_key"]
        if pkey in matches or pkey in matched_current_keys or pkey not in by_key:
            continue
        matches[pkey] = {"current_keys": [pkey], "method": MATCH_EXACT_KEY}
        matched_current_keys.add(pkey)

    # Rungs 3-4 — alias membership, then normalized-number equality.
    for pa in prior_accounts:
        pkey = pa["account_key"]
        if pkey in matches:
            continue
        p_norms = set(pa["normalized_aliases"])
        alias_hit = next(
            (ca for ca in current_accounts
             if ca["account_key"] not in matched_current_keys
             and (ca["account_number_normalized"] in p_norms
                  or pa["account_number_normalized"] in set(ca["normalized_aliases"]))),
            None,
        )
        if alias_hit is None:
            continue
        method = (MATCH_NORMALIZED_NUMBER
                  if alias_hit["account_number_normalized"] == pa["account_number_normalized"]
                  else MATCH_ALIAS)
        matches[pkey] = {"current_keys": [alias_hit["account_key"]], "method": method}
        matched_current_keys.add(alias_hit["account_key"])

    # Rung 5 — suggestion only, never applied.
    for pa in prior_accounts:
        pkey = pa["account_key"]
        if pkey in matches:
            continue
        tail = _trailing_digits(pa["account_number_normalized"])
        if not tail:
            continue
        candidates = [
            ca["account_key"] for ca in current_accounts
            if ca["account_key"] not in matched_current_keys
            and _institution_key(ca) == _institution_key(pa)
            and _trailing_digits(ca["account_number_normalized"]) == tail
        ]
        if candidates:
            suggestions.append({
                "prior_account_key": pkey,
                "candidate_current_keys": sorted(candidates),
                "basis": f"same institution and same trailing {FUZZY_TAIL_DIGITS} digits",
            })

    unmatched_current = [a["account_key"] for a in current_accounts
                         if a["account_key"] not in matched_current_keys]

    return {
        "matches": matches,
        "suggestions": suggestions,
        "unmatched_prior": [a["account_key"] for a in prior_accounts if a["account_key"] not in matches],
        "unmatched_current": unmatched_current,
    }


# ---------------------------------------------------------------------------
# Disposition state machine (plan §7.2)
# ---------------------------------------------------------------------------


def _in_tax_year(d: Optional[date], tax_year: int) -> bool:
    return d is not None and d.year == tax_year


def _before_tax_year(d: Optional[date], tax_year: int) -> bool:
    return d is not None and d < date(tax_year, 1, 1)


def _identity_change_for(current: Dict[str, Any], prior_key: str) -> Optional[Dict[str, Any]]:
    for change in current.get("identity_changes", []):
        if prior_key in change["prior_account_keys"]:
            return change
    return None


def _prior_disposition(pa: Dict[str, Any],
                       match: Optional[Dict[str, Any]],
                       current: Dict[str, Any],
                       by_key: Dict[str, Any],
                       tax_year: int) -> Tuple[str, List[Finding]]:
    """One prior-year account -> exactly one disposition, plus its findings.

    Precedence, when several could apply: a declared identity change wins
    (it is the filer's own written statement about the account), then closure
    (the strongest fact about an account's fate), then a changed identifier,
    then non-reportability, then plain carry-forward.
    """
    pkey = pa["account_key"]
    findings: List[Finding] = []

    if match is None:
        # Correctly absent only when the PRIOR record itself already recorded
        # the account as closed before this tax year began.
        if pa["account_status"] == "closed" and _before_tax_year(pa["closed_date"], tax_year):
            return CLOSED_PRIOR_YEAR, findings
        findings.append(Finding(
            id="D-UNRESOLVED", severity="blocking", account_key=pkey,
            message=(
                f"Prior-year account {pkey!r} ({pa['institution'].get('name') or 'institution not recorded'}, "
                f"number {pa['account_number']!r}) has no current-year antecedent and no "
                "disposition. Every account on the prior filing must resolve to one of: "
                "carried forward, closed, closed in an earlier year, dormant/zero, "
                "renumbered, split, or merged. Add the account to the current roster, "
                "or record account_status/closed_date on the prior roster."),
            detail={"prior_account_number": pa["account_number"]},
        ))
        return UNRESOLVED, findings

    change = _identity_change_for(current, pkey)
    if change is not None and match["method"] == MATCH_IDENTITY_CHANGE:
        disposition = {"split": SPLIT, "merge": MERGED, "renumber": RENUMBERED}[change["type"]]
        findings.extend(_identity_change_findings(change, pa, disposition, by_key))
        return disposition, findings

    ca = by_key[match["current_keys"][0]]

    if ca["account_status"] == "closed":
        if _before_tax_year(ca["closed_date"], tax_year):
            return CLOSED_PRIOR_YEAR, findings
        if not _in_tax_year(ca["closed_date"], tax_year):
            findings.append(Finding(
                id="D-CLOSE-DATE", severity="warn", account_key=pkey,
                message=(
                    f"{pkey!r} is marked closed with closed_date "
                    f"{ca['closed_date']}, which falls after tax year {tax_year}. "
                    "It was still open during the year, so it remains reportable "
                    "for this filing."),
            ))
            return CARRIED_FORWARD, findings
        return CLOSED, findings

    if ca["account_number_normalized"] != pa["account_number_normalized"]:
        if pa["account_number_normalized"] not in set(ca["normalized_aliases"]):
            findings.append(Finding(
                id="D-ALIAS-MISSING", severity="warn", account_key=pkey,
                message=(
                    f"{pkey!r} is filed this year as {ca['account_number']!r} but was "
                    f"{pa['account_number']!r} last year, and the prior identifier is not "
                    "in the current record's aliases[]. Add it — aliases[] is the audit "
                    "trail that lets next year's reconciliation match on either rendering."),
            ))
        findings.append(Finding(
            id="D-RENUMBERED", severity="warn", account_key=pkey,
            message=(
                f"{pkey!r} changed identifier: {pa['account_number']!r} -> "
                f"{ca['account_number']!r}. Same account, new number — confirm with the "
                "institution before filing, since Item 18 must match its rendering."),
            detail={"prior": pa["account_number"], "current": ca["account_number"]},
        ))
        return RENUMBERED, findings

    if not ca["reportable"]:
        if not ca["not_reportable_reason"]:
            findings.append(Finding(
                id="D-NOREASON", severity="error", account_key=pkey,
                message=(
                    f"{pkey!r} is marked reportable: false with no not_reportable_reason. "
                    "Dropping an account that WAS on the prior filing needs a written "
                    "reason retained with the five-year records."),
            ))
        return DORMANT_ZERO, findings

    if ca["max_value_usd"] is None and not ca["max_value_unknown"]:
        findings.append(Finding(
            id="D-NOMAX", severity="error", account_key=pkey,
            message=(
                f"{pkey!r} carries forward but has neither a determined max_value_usd "
                "nor the Item 15a 'amount unknown' election. One of the two is required: "
                "supply the maximum, or set max_value_unknown to elect Item 15a "
                "(a legitimate filing position for a filer with fewer than 25 accounts)."),
        ))
    return CARRIED_FORWARD, findings


def _identity_change_findings(change: Dict[str, Any], pa: Dict[str, Any],
                              disposition: str,
                              by_key: Dict[str, Any]) -> List[Finding]:
    """§7.3 — a reporting-model change must carry a written justification."""
    pkey = pa["account_key"]
    findings = [Finding(
        id=f"D-{disposition}", severity="warn", account_key=pkey,
        message=(
            f"{change['type'].upper()}: prior {change['prior_account_keys']} -> current "
            f"{change['current_account_keys']}. Reason: "
            f"{change.get('reason') or '(none recorded)'}"),
        detail={"identity_change": {k: v for k, v in change.items()}},
    )]

    # The declaration links the accounts; aliases[] is what carries the link
    # forward, so next year's reconciliation can still match on either
    # rendering without re-reading this year's identity_changes.
    prior_norm = pa["account_number_normalized"]
    missing_alias = [k for k in change["current_account_keys"]
                     if k in by_key and prior_norm not in set(by_key[k]["normalized_aliases"])]
    if missing_alias:
        findings.append(Finding(
            id="IC-ALIAS-MISSING", severity="warn", account_key=pkey,
            message=(
                f"Current account(s) {missing_alias} were declared as the "
                f"{change['type']} target of {pkey!r}, but none list its prior identifier "
                f"{pa['account_number']!r} in aliases[]. Add it, so the link survives "
                "into next year's roster."),
        ))

    if change["reporting_model_changed"] and not (change.get("justification") or "").strip():
        findings.append(Finding(
            id="IC-JUSTIFICATION", severity="blocking", account_key=pkey,
            message=(
                f"identity_change of type {change['type']!r} sets reporting_model_changed "
                "but records no justification. Filing one account in one year and "
                f"{len(change['current_account_keys'])} in the next for the same underlying "
                "relationship changes the reporting model, and the governing convention is "
                "unconfirmed against FinCEN guidance (see references/fbar_guide.md, "
                "'Filer-adopted conventions'). Write the reasoning down now and retain it "
                "with the five-year records."),
        ))
    return findings


def _current_disposition(ca: Dict[str, Any], tax_year: int) -> Tuple[str, List[Finding]]:
    """A current-year account with no prior-year antecedent."""
    ckey = ca["account_key"]
    findings: List[Finding] = []

    if _in_tax_year(ca["opened_date"], tax_year):
        return NEW_OPENED, findings

    if (ca["notes"] or "").strip():
        findings.append(Finding(
            id="D-NEW-UNREPORTED", severity="warn", account_key=ckey,
            message=(
                f"{ckey!r} ({ca['institution'].get('name') or 'institution not recorded'}) "
                f"is on the {tax_year} roster but has no prior-year antecedent and no "
                "opened_date within the year — so it existed before and was absent from "
                "the prior filing. Recorded explanation: "
                f"{ca['notes'].strip()!r}. If this is a genuine prior-year omission rather "
                "than a newly-rostered account, consider the Delinquent FBAR Submission "
                "Procedures (see references/fbar_guide.md)."),
        ))
        return NEW_PREVIOUSLY_UNREPORTED, findings

    findings.append(Finding(
        id="D-NEW-UNEXPLAINED", severity="blocking", account_key=ckey,
        message=(
            f"{ckey!r} ({ca['institution'].get('name') or 'institution not recorded'}, "
            f"number {ca['account_number']!r}) appears on the {tax_year} roster with no "
            "prior-year antecedent, no opened_date within the tax year, and no notes. "
            "Record which it is: opened during the year (set opened_date), or existed "
            "earlier and was not on the prior filing (explain in notes)."),
    ))
    return NEW_UNEXPLAINED, findings


def reconcile(current: Dict[str, Any],
              prior: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Run the full disposition pass. Returns dispositions plus findings."""
    tax_year = current["tax_year"]
    by_key = {a["account_key"]: a for a in current["accounts"]}
    matching = match_accounts(current, prior)
    findings: List[Finding] = []

    prior_rows: List[Dict[str, Any]] = []
    for pa in (prior["accounts"] if prior else []):
        pkey = pa["account_key"]
        match = matching["matches"].get(pkey)
        disposition, row_findings = _prior_disposition(pa, match, current, by_key, tax_year)
        findings.extend(row_findings)
        prior_rows.append({
            "account_key": pkey,
            "institution": pa["institution"].get("name"),
            "account_number": pa["account_number"],
            "prior_max_value_usd": pa["max_value_usd"],
            "prior_max_value_unknown": pa["max_value_unknown"],
            "disposition": disposition,
            "severity": DISPOSITION_SEVERITY[disposition],
            "matched_current_keys": match["current_keys"] if match else [],
            "match_method": match["method"] if match else None,
        })

    matched_current = {k for m in matching["matches"].values() for k in m["current_keys"]}
    current_rows: List[Dict[str, Any]] = []
    for ca in current["accounts"]:
        ckey = ca["account_key"]
        if ckey in matched_current:
            continue
        if prior is None:
            # First cycle: there is no prior filing to be absent from, so no
            # account can be "new" relative to one. Reported, not judged.
            current_rows.append({"account_key": ckey, "disposition": None,
                                 "severity": "info",
                                 "institution": ca["institution"].get("name"),
                                 "account_number": ca["account_number"],
                                 "max_value_usd": ca["max_value_usd"]})
            continue
        disposition, row_findings = _current_disposition(ca, tax_year)
        findings.extend(row_findings)
        current_rows.append({
            "account_key": ckey,
            "institution": ca["institution"].get("name"),
            "account_number": ca["account_number"],
            "max_value_usd": ca["max_value_usd"],
            "disposition": disposition,
            "severity": DISPOSITION_SEVERITY[disposition],
        })

    for suggestion in matching["suggestions"]:
        findings.append(Finding(
            id="D-FUZZY", severity="warn", account_key=suggestion["prior_account_key"],
            message=(
                f"Possible match for prior account {suggestion['prior_account_key']!r}: "
                f"{suggestion['candidate_current_keys']} ({suggestion['basis']}). "
                "NOT applied automatically — confirm the relationship, then record it by "
                "adding the prior identifier to the current record's aliases[] or by "
                "declaring an identity_change."),
            detail=suggestion,
        ))

    # Identity changes that name a prior key the prior roster does not have.
    known_prior = {a["account_key"] for a in (prior["accounts"] if prior else [])}
    if prior is not None:
        for i, change in enumerate(current.get("identity_changes", [])):
            unknown = [k for k in change["prior_account_keys"] if k not in known_prior]
            if unknown:
                findings.append(Finding(
                    id="IC-UNKNOWN-PRIOR", severity="error",
                    message=(
                        f"identity_changes[{i}] names prior_account_keys {unknown} that do "
                        "not exist on the prior-year roster. The link cannot be verified."),
                ))

    return {
        "tax_year": tax_year,
        "prior_rows": prior_rows,
        "current_rows": current_rows,
        "matching": matching,
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Validators (plan §8)
# ---------------------------------------------------------------------------


def _rate_for(roster: Dict[str, Any], currency: str) -> Optional[Dict[str, Any]]:
    for rate in roster["fx_rates"]:
        if rate["currency"] == currency:
            return rate
    return None


def _to_usd(amount: float, currency: str, roster: Dict[str, Any]) -> Optional[float]:
    """Convert to USD. `units_per_usd` is foreign units per ONE dollar.

    Divide, always, in this direction only. Never try the reciprocal to make a
    figure agree: the motivating defect (plan D4) is a rate recorded once as
    itself and once as its own reciprocal under conflicting provenance labels.
    A validator that quietly accepts either direction would erase exactly the
    finding it exists to produce.
    """
    if currency == "USD":
        return amount
    rate = _rate_for(roster, currency)
    if rate is None:
        return None
    return amount / rate["units_per_usd"]


def v1_fx_source(roster: Dict[str, Any], fbar: Dict[str, Any]) -> List[Finding]:
    url = get_constant(fbar, "fx_rate_source", "url")
    required = get_constant(fbar, "fx_rate_source", "required_source")
    out = []
    for rate in roster["fx_rates"]:
        if rate["source"] != required:
            out.append(Finding(
                id="V1", severity="warn",
                message=(
                    f"{rate['currency']} uses a {rate['source']!r} rate "
                    f"({rate['units_per_usd']} per USD as of {rate['as_of']}). FinCEN "
                    "requires the Treasury Reporting Rate of Exchange for the last day of "
                    f"the calendar year. Replace it from {url} and set source to "
                    f"{required!r}, or record why the Treasury rate is unavailable."),
                detail={"currency": rate["currency"], "source": rate["source"]},
            ))
    return out


def v2_fx_as_of(roster: Dict[str, Any]) -> List[Finding]:
    expected = date(roster["tax_year"], 12, 31)
    out = []
    for rate in roster["fx_rates"]:
        if rate["as_of"] != expected:
            out.append(Finding(
                id="V2", severity="error",
                message=(
                    f"{rate['currency']} rate is dated {rate['as_of']}, but FBAR "
                    f"conversion uses the rate for the LAST DAY of the calendar year "
                    f"({expected}) — not the date the account peaked."),
                detail={"currency": rate["currency"], "as_of": rate["as_of"].isoformat()},
            ))
    return out


def v3_recompute_usd(roster: Dict[str, Any], tolerance: float = 1.0) -> List[Finding]:
    out = []
    for acct in roster["accounts"]:
        if acct["max_value_usd"] is None:
            continue
        key = acct["account_key"]

        if acct["max_value_components"]:
            total = 0.0
            missing = []
            for component in acct["max_value_components"]:
                converted = _to_usd(component["amount"], component["currency"], roster)
                if converted is None:
                    missing.append(component["currency"])
                else:
                    total += converted
            if missing:
                out.append(Finding(
                    id="V3", severity="error", account_key=key,
                    message=(f"{key!r} has max_value_components in {sorted(set(missing))} "
                             "with no matching fx_rates entry, so the reported USD maximum "
                             "cannot be recomputed."),
                ))
                continue
            expected = total
            basis = ("USD sum of per-currency components at the combined-peak instant: "
                     + " + ".join(f"{c['amount']:,.2f} {c['currency']}"
                                  for c in acct["max_value_components"]))
        elif acct["max_value_local"] is not None and acct["max_value_currency"]:
            converted = _to_usd(acct["max_value_local"], acct["max_value_currency"], roster)
            if converted is None:
                out.append(Finding(
                    id="V3", severity="error", account_key=key,
                    message=(f"{key!r} reports a maximum in {acct['max_value_currency']} "
                             "but the roster carries no fx_rates entry for it."),
                ))
                continue
            expected = converted
            basis = (f"{acct['max_value_local']:,.2f} {acct['max_value_currency']} "
                     f"/ {(_rate_for(roster, acct['max_value_currency']) or {}).get('units_per_usd', 1)} "
                     "units per USD")
        else:
            continue

        delta = acct["max_value_usd"] - expected
        if abs(delta) > tolerance:
            out.append(Finding(
                id="V3", severity="error", account_key=key,
                message=(
                    f"{key!r} reports max_value_usd ${acct['max_value_usd']:,.2f} but "
                    f"recomputes to ${expected:,.2f} (off by ${delta:,.2f}, tolerance "
                    f"${tolerance:,.2f}). Basis: {basis}."),
                detail={"reported": acct["max_value_usd"], "recomputed": round(expected, 2)},
            ))
    return out


def aggregate_maximum(roster: Dict[str, Any]) -> Dict[str, Any]:
    """Sum of every account's maximum, with the undetermined ones counted apart.

    Every foreign financial account counts toward the threshold, including
    accounts that turn out to be trivial — the test is on the aggregate.
    """
    known = 0.0
    unknown_keys: List[str] = []
    for acct in roster["accounts"]:
        # An Item 15a election and a maximum nobody has determined yet are the
        # same thing for this arithmetic: neither is a zero, and neither can
        # join the sum.
        if acct["max_value_usd"] is not None:
            known += acct["max_value_usd"]
        else:
            unknown_keys.append(acct["account_key"])
    return {"known_total_usd": round(known, 2),
            "accounts_with_undetermined_max": unknown_keys}


def v4_threshold(roster: Dict[str, Any], fbar: Dict[str, Any]) -> Tuple[List[Finding], Dict[str, Any]]:
    agg = aggregate_maximum(roster)
    threshold = fbar_threshold(fbar)
    tripped = threshold_tripped(agg["known_total_usd"], fbar)
    arithmetic = (f"${agg['known_total_usd']:,.2f} aggregate maximum vs "
                  f"${threshold:,.2f} threshold -> "
                  f"{'EXCEEDED, filing required' if tripped else 'not exceeded'}")
    findings = [Finding(id="V4", severity="info", message=arithmetic,
                        detail={"aggregate_usd": agg["known_total_usd"],
                                "threshold_usd": threshold, "tripped": tripped})]
    if agg["accounts_with_undetermined_max"] and not tripped:
        findings.append(Finding(
            id="V4", severity="warn",
            message=(
                "The aggregate does not exceed the threshold, but "
                f"{len(agg['accounts_with_undetermined_max'])} account(s) have no "
                f"determined maximum ({', '.join(agg['accounts_with_undetermined_max'])}). "
                "The 'no filing required' conclusion is not supported until those are "
                "resolved — an undetermined maximum is not a zero."),
        ))
    result = dict(agg)
    result["threshold_usd"] = threshold
    result["tripped"] = tripped
    result["arithmetic"] = arithmetic
    return findings, result


def v5_all_reportable(roster: Dict[str, Any], tripped: bool) -> List[Finding]:
    if not tripped:
        return []
    out = []
    for acct in roster["accounts"]:
        if not acct["reportable"] and not (acct["not_reportable_reason"] or "").strip():
            out.append(Finding(
                id="V5", severity="error", account_key=acct["account_key"],
                message=(
                    f"The threshold is exceeded, so EVERY foreign financial account is "
                    f"reported — including trivial ones. {acct['account_key']!r} is marked "
                    "reportable: false with no not_reportable_reason. Either report it or "
                    "record why it is not a reportable foreign financial account."),
            ))
    return out


def v6_account_count(roster: Dict[str, Any], fbar: Dict[str, Any]) -> List[Finding]:
    limit = int(get_constant(fbar, "aggregate_max_account_count_detail_limit"))
    count = len([a for a in roster["accounts"] if a["reportable"]])
    if count < limit:
        return []
    return [Finding(
        id="V6", severity="info",
        message=(
            f"{count} reportable accounts (limit {limit}). Take the Item 14a/14b path: "
            "report the NUMBER of accounts and leave the per-account Parts II-V blank. "
            "The detail records must still be retained and produced on request — this "
            "roster is that record. Note the Item 15a 'amount unknown' election is "
            f"available only to filers with FEWER than {limit} accounts."),
        detail={"reportable_account_count": count, "limit": limit},
    )]


def v7_address_complete(roster: Dict[str, Any]) -> List[Finding]:
    """Items 19-23: mailing address, city, state, foreign postal code, country.

    State is not required — many countries have no state/province level, and
    Item 21 is captioned as such. Name (Item 17), street, city, postal code and
    country are.
    """
    out = []
    for acct in roster["accounts"]:
        if not acct["reportable"]:
            continue
        inst = acct["institution"]
        address = inst.get("address") or {}
        missing = []
        if not inst.get("name"):
            missing.append("institution.name (Item 17)")
        if not address.get("street"):
            missing.append("address.street (Item 19)")
        if not address.get("city"):
            missing.append("address.city (Item 20)")
        if not address.get("postal_code"):
            missing.append("address.postal_code (Item 22)")
        if not inst.get("country"):
            missing.append("institution.country (Item 23)")
        if missing:
            out.append(Finding(
                id="V7", severity="warn", account_key=acct["account_key"],
                message=(f"{acct['account_key']!r} is reportable but its institution "
                         f"address is incomplete; missing {', '.join(missing)}."),
                detail={"missing": missing},
            ))
    return out


def prior_to_current(current: Dict[str, Any],
                     prior: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Each prior account_key -> its single current-year successor, where there is one.

    Year-over-year validators must follow the same matching ladder the
    reconciliation does. Looking the prior key up in the current roster
    directly makes every renumbered, merged or split account invisible to them
    — and those are exactly the accounts whose figures moved, so the checks
    would go quiet on the cases they exist for.

    Accounts with more than one successor (a declared split) are omitted:
    "the same account" has no single answer there, and the split itself is
    already reported as its own finding.
    """
    if prior is None:
        return {}
    by_key = {a["account_key"]: a for a in current["accounts"]}
    resolved: Dict[str, Dict[str, Any]] = {}
    for pkey, match in match_accounts(current, prior)["matches"].items():
        keys = [k for k in match["current_keys"] if k in by_key]
        if len(keys) == 1:
            resolved[pkey] = by_key[keys[0]]
    return resolved


def v8_amount_unknown_carryforward(current: Dict[str, Any],
                                   prior: Optional[Dict[str, Any]],
                                   resolved: Optional[Dict[str, Dict[str, Any]]] = None
                                   ) -> List[Finding]:
    """Item 15a is a legitimate filing position, so this NEVER blocks.

    What it does is stop an unresolved "amount unknown" from riding along for
    years unnoticed: each one either gets a determined maximum this year, or is
    re-elected with a note saying why the maximum still cannot be determined.
    """
    if prior is None:
        return []
    if resolved is None:
        resolved = prior_to_current(current, prior)
    out = []
    for pa in prior["accounts"]:
        if not pa["max_value_unknown"]:
            continue
        ca = resolved.get(pa["account_key"])
        if ca is None:
            continue
        if ca["max_value_usd"] is not None:
            continue
        if ca["max_value_unknown"] and (ca["notes"] or "").strip():
            continue
        out.append(Finding(
            id="V8", severity="warn", account_key=pa["account_key"],
            message=(
                f"{pa['account_key']!r} used the Item 15a 'amount unknown' checkbox on the "
                f"{prior['tax_year']} filing and still has no determined maximum. Item 15a "
                "is a valid position, not a defect — but re-electing it needs a "
                "contemporaneous note saying why the maximum still cannot be determined "
                "(statements not received, institution closed, and so on)."),
        ))
    return out


def v9_convention_drift(current: Dict[str, Any],
                        prior: Optional[Dict[str, Any]],
                        resolved: Optional[Dict[str, Dict[str, Any]]] = None
                        ) -> List[Finding]:
    if prior is None:
        return []
    if resolved is None:
        resolved = prior_to_current(current, prior)
    out = []
    for pa in prior["accounts"]:
        ca = resolved.get(pa["account_key"])
        if ca is None:
            continue
        now, before = set(ca["conventions_applied"]), set(pa["conventions_applied"])
        if now != before:
            renamed = ("" if ca["account_key"] == pa["account_key"]
                       else f" (filed last year as {pa['account_key']!r})")
            out.append(Finding(
                id="V9", severity="warn", account_key=ca["account_key"],
                message=(
                    f"{ca['account_key']!r}{renamed} changed applied conventions: added "
                    f"{sorted(now - before) or '[]'}, dropped {sorted(before - now) or '[]'}. "
                    "These are filer-adopted treatments unconfirmed against FinCEN "
                    "guidance, so changing one between years changes the reported figure "
                    "on an unconfirmed basis. Record why in notes."),
                detail={"added": sorted(now - before), "dropped": sorted(before - now)},
            ))
    return out


def v10_spousal_114a(roster: Dict[str, Any]) -> List[Finding]:
    """Separately-owned accounts force separate FBARs.

    Scoped to the SPOUSAL authorization. `form_114a_preparer` is a different
    use of the same form — a third-party preparer filing on the taxpayer's
    behalf (Form 114 box 44a) — and is entirely compatible with separate
    filings. Conflating the two hard-errors on the correct, normal pattern.
    """
    filer = roster["filer"]
    separate = [a["account_key"] for a in roster["accounts"] if a["ownership"] == "separate"]
    if separate and filer["form_114a_spousal"]:
        return [Finding(
            id="V10", severity="error",
            message=(
                f"filer.form_114a_spousal is true, but {len(separate)} account(s) are "
                f"separately owned ({', '.join(separate[:5])}"
                f"{'…' if len(separate) > 5 else ''}). A couple may file one FBAR on a "
                "spousal Form 114a only when ALL accounts are jointly owned. Any "
                "separately-owned account forces separate FBARs. (This check does not "
                "look at form_114a_preparer — a third-party preparer authorization is "
                "unrelated and compatible with separate filings.)"),
            detail={"separately_owned": separate},
        )]
    return []


def v11_filing_date(roster: Dict[str, Any], fbar: Dict[str, Any],
                    filing_date: Optional[date]) -> List[Finding]:
    deadlines = fbar_deadlines(roster["tax_year"], fbar)
    on = filing_date or date.today()
    base = (f"TY{roster['tax_year']} FBAR is due {deadlines['due'].isoformat()} with an "
            f"automatic extension to {deadlines['extended'].isoformat()} (no request "
            "required). Records must be retained until "
            f"{record_retention_until(roster['tax_year'], fbar).isoformat()} — five years "
            "from the due date.")
    if on > deadlines["extended"]:
        # The penalty table is keyed by ASSESSMENT date and only runs forward
        # from its earliest column. A filing date before that column (an old
        # tax year filed late, but still years ago) has no applicable figure —
        # report the delinquency without a penalty scale rather than aborting
        # the whole run over a parenthetical.
        try:
            penalty = fbar_penalty_maximum(fbar, on)
            scale = (
                " For scale, the non-willful maximum for penalties assessed on or after "
                f"{penalty['effective_date']} is ${penalty['non_willful_max']:,} "
                f"{penalty['unit'].replace('_', ' ')} "
                "(Bittner v. United States, 2023 — per report, not per account).")
        except RosterError:
            scale = (" No penalty column in tax_constants.json reaches back to that "
                     "date, so no maximum is quoted here.")
        return [Finding(
            id="V11", severity="warn",
            message=(
                f"{base} As of {on.isoformat()} the extended deadline has passed. A "
                "delinquent FBAR filed before the IRS asks for it may qualify for the "
                f"Delinquent FBAR Submission Procedures.{scale}"),
            detail={"due": deadlines["due"].isoformat(),
                    "extended": deadlines["extended"].isoformat()},
        )]
    return [Finding(id="V11", severity="info", message=base,
                    detail={"due": deadlines["due"].isoformat(),
                            "extended": deadlines["extended"].isoformat()})]


def v12_max_value_basis(roster: Dict[str, Any]) -> List[Finding]:
    """A stated maximum should rest on statements that could show the maximum.

    Period-end summaries cannot: an account can peak and fall back between two
    statement dates and show nothing. Asserting a determined maximum on that
    basis, without electing Item 15a, claims more certainty than the records
    support.
    """
    out = []
    for acct in roster["accounts"]:
        if not acct["reportable"] or acct["max_value_unknown"]:
            continue
        if acct["max_value_basis"] == "statement_full_year":
            continue
        out.append(Finding(
            id="V12", severity="warn", account_key=acct["account_key"],
            message=(
                f"{acct['account_key']!r} reports a determined maximum on a "
                f"{acct['max_value_basis']!r} basis, without electing Item 15a. FinCEN "
                "permits reliance on periodic statements only where they FAIRLY REFLECT "
                "the maximum during the year. Either obtain records that cover the year, "
                "or elect Item 15a and note why."),
            detail={"max_value_basis": acct["max_value_basis"]},
        ))
    return out


def v13_source_documents(roster: Dict[str, Any], workspace: Optional[str],
                         fbar: Dict[str, Any]) -> List[Finding]:
    """Every cited support file must actually be in the workspace.

    The five-year recordkeeping rule is not satisfied by a workpaper that
    points at a file which is not there.
    """
    if workspace is None:
        return []
    years = int(get_constant(fbar, "recordkeeping_years"))
    out = []
    for acct in roster["accounts"]:
        missing = [p for p in acct["source_documents"]
                   if not os.path.exists(os.path.join(workspace, p))]
        if missing:
            out.append(Finding(
                id="V13", severity="warn", account_key=acct["account_key"],
                message=(
                    f"{acct['account_key']!r} cites source documents that are not present "
                    f"under {workspace}: {', '.join(missing)}. Supporting records must be "
                    f"retained for {years} years from the FBAR due date; a citation "
                    "pointing at a missing file does not satisfy that."),
                detail={"missing": missing, "workspace": workspace},
            ))
    return out


def validate(roster: Dict[str, Any],
             prior: Optional[Dict[str, Any]] = None,
             fbar: Optional[Dict[str, Any]] = None,
             workspace: Optional[str] = None,
             filing_date: Optional[date] = None) -> Dict[str, Any]:
    """Run V1-V13. Returns findings plus the threshold arithmetic."""
    fbar = fbar or load_fbar_constants(str(roster["tax_year"]))
    findings: List[Finding] = []
    findings += v1_fx_source(roster, fbar)
    findings += v2_fx_as_of(roster)
    findings += v3_recompute_usd(roster)
    v4_findings, threshold_result = v4_threshold(roster, fbar)
    findings += v4_findings
    findings += v5_all_reportable(roster, threshold_result["tripped"])
    findings += v6_account_count(roster, fbar)
    findings += v7_address_complete(roster)
    resolved = prior_to_current(roster, prior)
    findings += v8_amount_unknown_carryforward(roster, prior, resolved)
    findings += v9_convention_drift(roster, prior, resolved)
    findings += v10_spousal_114a(roster)
    findings += v11_filing_date(roster, fbar, filing_date)
    findings += v12_max_value_basis(roster)
    findings += v13_source_documents(roster, workspace, fbar)
    findings += _out_of_scope_notices(roster)
    return {"findings": findings, "threshold": threshold_result}


def _out_of_scope_notices(roster: Dict[str, Any]) -> List[Finding]:
    """Say plainly what v1 does not reconcile, rather than appearing to have."""
    keys = [a["account_key"] for a in roster["accounts"]
            if a["ownership"] == "signature_authority"]
    if not keys:
        return []
    return [Finding(
        id="SCOPE", severity="warn",
        message=(
            f"{len(keys)} account(s) are signature-authority-only ({', '.join(keys)}). "
            "Those belong to Part IV, which this module does NOT reconcile or emit a "
            "worksheet for. They are carried in the roster and counted toward the "
            "threshold; complete Part IV by hand."),
        detail={"account_keys": keys},
    )]


# ---------------------------------------------------------------------------
# Output (plan §9)
# ---------------------------------------------------------------------------

_DISCLAIMER_TAIL = (
    "FinCEN 114 is transmitted through BSA E-Filing by the taxpayer or their "
    "preparer. Confirm every figure against final bank records before "
    "transmission. This tool does not provide tax or legal advice."
)

REPORT_DISCLAIMER = (
    "> These are **findings for review**, not a filed form, and clearing them is "
    f"not a certification that the filing is correct. {_DISCLAIMER_TAIL}"
)

WORKSHEET_DISCLAIMER = (
    "> This worksheet is a **keying aid**, not a filed form — the values below are "
    f"to be keyed into BSA E-Filing, not submitted as they stand. {_DISCLAIMER_TAIL}"
)


def _conventions_section(roster: Dict[str, Any]) -> List[str]:
    applied = sorted({c for a in roster["accounts"] for c in a["conventions_applied"]})
    if not applied:
        return ["## Conventions applied", "", "None recorded on this roster.", ""]
    lines = ["## Conventions applied", "",
             "Filer-adopted treatments, not settled law. Each is applied because the "
             "workspace has consistently applied it; those marked *unconfirmed* could "
             "not be verified against FinCEN guidance.", ""]
    for name in applied:
        meta = CONVENTIONS.get(name)
        if meta is None:
            lines.append(f"- **{name}** — ⚠️ no registered description. Record the "
                         "rationale in `references/fbar_guide.md` before filing.")
        else:
            lines.append(f"- **{name}** ({meta['status']}) — {meta['statement']}")
            lines.append(f"  - {meta['note']}")
    lines.append("")
    return lines


def _findings_section(findings: List[Finding]) -> List[str]:
    lines = ["## Findings", ""]
    ordered = sort_findings(findings)
    if not ordered:
        lines += ["No findings.", ""]
        return lines
    for severity in SEVERITIES:
        bucket = [f for f in ordered if f.severity == severity]
        if not bucket:
            continue
        lines.append(f"### {SEVERITY_MARK[severity]} ({len(bucket)})")
        lines.append("")
        for finding in bucket:
            # Most messages already name the account; repeating it as a chip
            # just makes the line harder to read.
            named = finding.account_key and repr(finding.account_key) in finding.message
            scope = f" `{finding.account_key}`" if finding.account_key and not named else ""
            lines.append(f"- **[{finding.id}]**{scope} {finding.message}")
        lines.append("")
    return lines


def render_reconciliation(current: Dict[str, Any], prior: Optional[Dict[str, Any]],
                          result: Dict[str, Any], validation: Dict[str, Any]) -> str:
    filer = current["filer"]
    lines = [
        f"# FBAR reconciliation — {filer['slug']}, tax year {current['tax_year']}",
        "",
        REPORT_DISCLAIMER,
        "",
    ]

    if prior is None:
        lines += ["**No prior-year roster supplied.** This run validates the current "
                  "roster only; no account has been reconciled against a prior filing. "
                  "Supply `--prior` from the next cycle onward.", ""]
    else:
        source = (current.get("prior_year") or {}).get("source", "none")
        lines += [f"Prior year: **{prior['tax_year']}** "
                  f"({prior['_origin']}), evidence recorded as `{source}`.", ""]
        lines += ["## Prior-year accounts — disposition", "",
                  "| Account key | Institution | Prior number | Prior max (USD) | Disposition | Matched by |",
                  "|---|---|---|---|---|---|"]
        for row in result["prior_rows"]:
            prior_max = ("*unknown (15a)*" if row["prior_max_value_unknown"]
                         else (f"${row['prior_max_value_usd']:,.2f}"
                               if row["prior_max_value_usd"] is not None else "—"))
            lines.append(
                f"| `{row['account_key']}` | {row['institution'] or '—'} | "
                f"{row['account_number']} | {prior_max} | "
                f"{SEVERITY_MARK[row['severity']].split()[0]} {row['disposition']} | "
                f"{row['match_method'] or '—'} |")
        lines.append("")

    heading = ("## Current-year accounts" if prior is None
               else "## Current-year accounts with no prior-year antecedent")
    lines += [heading, ""]
    if not result["current_rows"]:
        # With no prior roster nothing was traced, so the "everything traces"
        # line would assert a reconciliation that never ran.
        lines += ["None on this roster." if prior is None
                  else "None — every current-year account traces to a prior-year record.",
                  ""]
    else:
        lines += ["| Account key | Institution | Number | Max (USD) | Disposition |",
                  "|---|---|---|---|---|"]
        for row in result["current_rows"]:
            max_usd = (f"${row['max_value_usd']:,.2f}"
                       if row.get("max_value_usd") is not None else "—")
            mark = SEVERITY_MARK[row["severity"]].split()[0]
            lines.append(f"| `{row['account_key']}` | {row['institution'] or '—'} | "
                         f"{row['account_number']} | {max_usd} | "
                         f"{mark} {row['disposition'] or 'first cycle — not reconciled'} |")
        lines.append("")

    threshold = validation["threshold"]
    lines += ["## Threshold arithmetic", "", f"{threshold['arithmetic']}", ""]
    if threshold["accounts_with_undetermined_max"]:
        lines += ["Accounts with no determined maximum (excluded from the sum above): "
                  + ", ".join(f"`{k}`" for k in threshold["accounts_with_undetermined_max"]),
                  ""]

    lines += _conventions_section(current)
    lines += _findings_section(result["findings"] + validation["findings"])
    return "\n".join(lines)


def _item_16_label(acct: Dict[str, Any]) -> str:
    label = {"bank": "BANK", "securities": "SECURITIES", "other": "OTHER"}[acct["account_type"]]
    if acct["account_type"] == "other":
        label += f" — {acct['account_type_other_desc']}"
    return label


def render_worksheet(roster: Dict[str, Any], validation: Dict[str, Any]) -> str:
    """One block per reportable account, labelled with live Form 114 item numbers.

    Part II items 15, 15a, 16, 17, 18, 19-23 verified against the FinCEN 114
    layout: 15 Maximum Account Value / 15a Max Acct Value Unknown / 16 Type of
    Account / 17 Financial Institution's Name / 18 Account Number or Other
    Designation / 19 Mailing Address / 20 City / 21 State / 22 Foreign Postal
    Code / 23 Country. Part III repeats 15-23 and adds 24 Number of Joint Owners.
    """
    filer = roster["filer"]
    threshold = validation["threshold"]
    lines = [
        f"# FinCEN 114 worksheet — {filer['slug']}, tax year {roster['tax_year']}",
        "",
        WORKSHEET_DISCLAIMER,
        "",
        "## Part I — filer",
        "",
        f"- Filing capacity: {filer['filing_capacity']}",
        f"- Files separately from spouse: {filer['files_separately_from_spouse']}",
        f"- Form 114a (spousal authorization): {filer['form_114a_spousal']}",
        f"- Form 114a (third-party preparer, box 44a): {filer['form_114a_preparer']}",
        f"- Aggregate maximum across all accounts: **${threshold['known_total_usd']:,.2f}** "
        f"vs ${threshold['threshold_usd']:,.2f} threshold — "
        f"{'filing required' if threshold['tripped'] else 'threshold not exceeded'}",
        "",
    ]

    if not threshold["tripped"]:
        lines += ["> The aggregate maximum does not exceed the threshold. The blocks below "
                  "are produced for review; no FBAR is required on these figures alone.", ""]

    separate = [a for a in roster["accounts"]
                if a["reportable"] and a["ownership"] == "separate"]
    joint = [a for a in roster["accounts"]
             if a["reportable"] and a["ownership"] == "joint"]

    for title, accounts in (("Part II — accounts owned separately", separate),
                            ("Part III — jointly owned accounts", joint)):
        lines += [f"## {title}", ""]
        if not accounts:
            lines += ["None.", ""]
            continue
        for acct in accounts:
            lines += _worksheet_block(acct, roster)
    return "\n".join(lines)


def _worksheet_block(acct: Dict[str, Any], roster: Dict[str, Any]) -> List[str]:
    inst = acct["institution"]
    address = inst.get("address") or {}

    # Item 18 must carry the institution's own rendering. The guard makes that
    # a hard failure rather than a thing a future edit can quietly break by
    # reaching for the (normalized, matching-only) field one line over.
    item_18 = acct["account_number"]
    assert_display_safe(item_18, acct)

    if acct["max_value_unknown"]:
        item_15 = "*(leave blank — see 15a)*"
        item_15a = "**CHECKED** — maximum could not be determined"
    else:
        item_15 = (f"${acct['max_value_usd']:,.0f}"
                   if acct["max_value_usd"] is not None
                   else "⚠️ NOT DETERMINED — supply a maximum or check 15a")
        item_15a = "unchecked"

    if acct["max_value_components"]:
        foreign = ", ".join(f"{c['amount']:,.2f} {c['currency']}"
                            for c in acct["max_value_components"])
    elif acct["max_value_local"] is not None:
        foreign = f"{acct['max_value_local']:,.2f} {acct['max_value_currency'] or ''}".strip()
    else:
        foreign = "—"

    lines = [
        f"### `{acct['account_key']}` — {inst.get('name') or 'institution not recorded'}",
        "",
        "| Item | Value |",
        "|---|---|",
        f"| 15 Maximum Account Value (US $) | {item_15} |",
        f"| 15 Foreign Currency | {foreign} |",
        f"| 15a Max Acct Value Unknown | {item_15a} |",
        f"| 16 Type of Account | {_item_16_label(acct)} |",
        f"| 17 Financial Institution's Name | {inst.get('name') or '⚠️ missing'} |",
        f"| 18 Account Number or Other Designation | {item_18} |",
        f"| 19 Mailing Address | {address.get('street') or '⚠️ missing'} |",
        f"| 20 City | {address.get('city') or '⚠️ missing'} |",
        f"| 21 State | {address.get('state_province') or '—'} |",
        f"| 22 Foreign Postal Code | {address.get('postal_code') or '⚠️ missing'} |",
        f"| 23 Country | {inst.get('country') or '⚠️ missing'} |",
    ]
    if acct["ownership"] == "joint":
        lines.append(f"| 24 Number of Joint Owners | {len(acct['joint_owners']) or '⚠️ missing'} |")
    lines.append("")

    peak = (f"Peak date: {acct['max_value_date'].isoformat()}. "
            if acct["max_value_date"] else "")
    lines.append(f"{peak}Basis: `{acct['max_value_basis']}`. "
                 f"Conventions: {', '.join(acct['conventions_applied']) or 'none'}. "
                 f"Support: {', '.join(acct['source_documents']) or '⚠️ none cited'}.")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Payload & CLI
# ---------------------------------------------------------------------------


def build_payload(current: Dict[str, Any], prior: Optional[Dict[str, Any]],
                  result: Optional[Dict[str, Any]], validation: Dict[str, Any]) -> Dict[str, Any]:
    findings = (result["findings"] if result else []) + validation["findings"]
    ordered = sort_findings(findings)
    payload: Dict[str, Any] = {
        "tax_year": current["tax_year"],
        "filer_slug": current["filer"]["slug"],
        "prior_tax_year": prior["tax_year"] if prior else None,
        "threshold": validation["threshold"],
        "findings": [asdict(f) for f in ordered],
        "counts": {s: len([f for f in ordered if f.severity == s]) for s in SEVERITIES},
    }
    if result is not None:
        payload["prior_dispositions"] = result["prior_rows"]
        payload["new_accounts"] = result["current_rows"]
        payload["match_suggestions"] = result["matching"]["suggestions"]
    if stops(ordered):
        payload["isError"] = True
        payload["error"] = (
            "Reconciliation did not clear. Every blocking finding is an account whose "
            "fate is unanswered, or an internally inconsistent figure; resolve them in "
            "the roster and re-run.")
    return payload


def _print_text(payload: Dict[str, Any], report: Optional[str]) -> None:
    if report:
        print(report)
    else:
        for finding in payload["findings"]:
            named = (finding["account_key"]
                     and repr(finding["account_key"]) in finding["message"])
            scope = f" [{finding['account_key']}]" if finding["account_key"] and not named else ""
            print(f"{SEVERITY_MARK[finding['severity']]} ({finding['id']}){scope} "
                  f"{finding['message']}")
    if payload.get("isError"):
        print(f"\n{SEVERITY_MARK['blocking']}: {payload['error']}")


def emitted_roster_path(output_dir: str, current: Dict[str, Any]) -> str:
    """Where `--output-dir` will write next year's `--prior` roster."""
    return os.path.join(output_dir,
                        f"fbar_roster_{current['filer']['slug']}_{current['tax_year']}.json")


def assert_not_overwriting_input(output_dir: str, current: Dict[str, Any],
                                 input_paths: List[Optional[str]]) -> None:
    """Refuse an --output-dir that would write over a roster we just read.

    The emitted roster uses the same `fbar_roster_<slug>_<year>.json` name the
    input does, so pointing --output-dir at the roster's own directory
    overwrites it — and the emitted form deliberately carries no
    `identity_changes`. That would destroy the filer's written record of every
    split, merge and renumber (and the justification the tool blocks on) in the
    course of a run that reports success. Compare realpaths, not `samefile`:
    on a first run the target does not exist yet.
    """
    target = os.path.realpath(emitted_roster_path(output_dir, current))
    for path in input_paths:
        if path and os.path.realpath(path) == target:
            raise RosterError(
                f"--output-dir would write the emitted roster over its own input "
                f"({path}). The emitted roster carries no identity_changes by design, "
                "so this would erase the declared splits, merges and renumbers along "
                "with their justifications. Choose a different --output-dir.")


def _write_outputs(output_dir: str, current: Dict[str, Any], report: str,
                   worksheet: str, payload: Dict[str, Any]) -> List[str]:
    os.makedirs(output_dir, exist_ok=True)
    slug, year = current["filer"]["slug"], current["tax_year"]
    written = []
    for name, content in (
        (f"fbar_reconciliation_{slug}_{year}.md", report),
        (f"fbar_worksheet_{slug}_{year}.md", worksheet),
        (f"fbar_reconciliation_{slug}_{year}.json", json.dumps(payload, indent=2, default=str)),
        (f"fbar_roster_{slug}_{year}.json",
         json.dumps(roster_to_json(current), indent=2, default=str)),
    ):
        path = os.path.join(output_dir, name)
        with open(path, "w") as f:
            f.write(content if content.endswith("\n") else content + "\n")
        written.append(path)
    return written


def _fail(message: str, output_format: str) -> int:
    """Report a run-ending problem in the requested format and exit non-zero."""
    print(json.dumps({"isError": True, "error": message}, indent=2)
          if output_format == "json" else f"{SEVERITY_MARK['error']}: {message}")
    return 1


def _parse_cli_date(value: Optional[str], label: str) -> Optional[date]:
    if not value:
        return None
    from fbar_schema import parse_date
    return parse_date(value, label)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="FinCEN 114 (FBAR) reconciliation, validation and worksheets.")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--workspace", default=None,
                       help="Directory that source_documents paths are relative to "
                            "(enables V13). Defaults to the roster file's directory.")
        p.add_argument("--filing-date", default=None,
                       help="Date the FBAR is/was transmitted, YYYY-MM-DD (V11). "
                            "Defaults to today.")
        p.add_argument("--output-format", choices=["text", "json"], default="text")

    p_rec = sub.add_parser("reconcile", help="Reconcile a year against the prior year")
    p_rec.add_argument("--current", required=True, help="Current-year roster JSON")
    p_rec.add_argument("--prior", required=True, help="Prior-year roster JSON")
    p_rec.add_argument("--output-dir", default=None,
                       help="Write report, worksheet, findings JSON and the "
                            "normalized roster here")
    common(p_rec)

    p_val = sub.add_parser("validate", help="Validate one roster (first cycle)")
    p_val.add_argument("--roster", required=True)
    p_val.add_argument("--prior", default=None, help="Optional prior-year roster")
    common(p_val)

    p_ws = sub.add_parser("worksheet", help="Emit the keying worksheet only")
    p_ws.add_argument("--roster", required=True)
    common(p_ws)

    args = parser.parse_args(argv)

    try:
        current_path = getattr(args, "current", None) or args.roster
        current = load_roster(current_path)
        prior = load_roster(args.prior) if getattr(args, "prior", None) else None
        fbar = load_fbar_constants(str(current["tax_year"]))
        workspace = args.workspace or os.path.dirname(os.path.abspath(current_path))
        filing_date = _parse_cli_date(args.filing_date, "--filing-date")
        if getattr(args, "output_dir", None):
            assert_not_overwriting_input(
                args.output_dir, current,
                [current_path, getattr(args, "prior", None)])
    except (RosterError, ValueError, KeyError) as exc:
        return _fail(str(exc), args.output_format)

    if prior is not None and prior["filer"]["slug"] != current["filer"]["slug"]:
        return _fail(
            f"Filer slug mismatch: current roster is "
            f"{current['filer']['slug']!r}, prior is {prior['filer']['slug']!r}. "
            "Rosters reconcile per filer — separately-owned accounts force "
            "separate FBARs, so mixing two filers' accounts would fabricate a "
            "filing neither of them makes.", args.output_format)

    # Everything below reads the constants and the roster, so the same failure
    # modes the loader guards against (a missing constant, an unsupported
    # penalty column) can surface here too. Report them the same way rather
    # than as a traceback.
    try:
        validation = validate(current, prior, fbar, workspace, filing_date)

        if args.command == "worksheet":
            worksheet = render_worksheet(current, validation)
            if args.output_format == "json":
                print(json.dumps({"worksheet_markdown": worksheet,
                                  "threshold": validation["threshold"]},
                                 indent=2, default=str))
            else:
                print(worksheet)
            return 0

        # `reconcile` handles a missing prior year itself: with no prior filing
        # to be absent from, current accounts are reported rather than judged.
        result = reconcile(current, prior)
        payload = build_payload(current, prior, result, validation)
        report = render_reconciliation(current, prior, result, validation)

        if getattr(args, "output_dir", None):
            worksheet = render_worksheet(current, validation)
            written = _write_outputs(args.output_dir, current, report, worksheet, payload)
            payload["outputs"] = written
    except (RosterError, ValueError, KeyError) as exc:
        return _fail(str(exc), args.output_format)

    if args.output_format == "json":
        print(json.dumps(payload, indent=2, default=str))
    else:
        _print_text(payload, report)
        if payload.get("outputs"):
            print("\nWrote:\n" + "\n".join(f"  {p}" for p in payload["outputs"]))

    return 1 if payload.get("isError") else 0


if __name__ == "__main__":
    sys.exit(main())
