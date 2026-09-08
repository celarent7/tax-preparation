# FBAR (FinCEN Form 114) — Domain Reference

Foreign bank account reporting: who files, what counts, how the maximum value is
determined, and — the part this plugin actually automates — how each year's
accounts reconcile against the prior year's filing.

FBAR is **not** part of the income tax return. It is filed separately through
FinCEN's BSA E-Filing system. This plugin produces **worksheets and findings**,
never a transmittable form; see §9.

> **Scope.** `scripts/fbar_reconciler.py` covers Part II (separately owned) and
> Part III (jointly owned) accounts. Part IV (signature authority, no financial
> interest) and Part V (consolidated) are out of scope — the schema records such
> accounts and the reconciler says so rather than appearing to have handled them.
> Form 8938 (FATCA) is a different regime; see §8.

---

## 1. The rules

<!-- FBAR_RULES -->

| Rule | Statement | Source |
|---|---|---|
| Who files | Any U.S. person — citizen, resident, corporation, partnership, LLC, trust or estate — with a financial interest in, or signature authority over, foreign financial accounts. | [IRS FBAR page](https://www.irs.gov/businesses/small-businesses-self-employed/report-of-foreign-bank-and-financial-accounts-fbar) |
| Threshold | The aggregate value of all foreign financial accounts **exceeded $10,000 at any time** during the calendar year. Measured on each account's **maximum** value, not its year-end balance. Exactly $10,000 does not trip it. Once tripped, **every** account is reported, including trivial ones. | IRS FBAR page |
| Taxability is irrelevant | Whether the account produced taxable income has no effect on whether it is a foreign financial account for FBAR purposes. | IRS FBAR page |
| Deadline | Due **April 15** following the calendar year, with an **automatic extension to October 15** — no request required. | IRS FBAR page |
| Maximum value method | **Periodic account statements may be relied on**, provided they fairly reflect the maximum account value during the calendar year. | [FinCEN, Reporting Maximum Account Value](https://www.fincen.gov/reporting-maximum-account-value) |
| FX conversion | Convert to USD using the **Treasury Reporting Rate of Exchange for the last day of the calendar year** — *not* the rate on the date the account peaked, and not a commercial mid-market rate. | [FinCEN](https://www.fincen.gov/reporting-maximum-account-value) |
| Amount unknown | Item 15a carries an *amount unknown* checkbox. A filer with **fewer than 25 accounts** who cannot determine whether the maximum exceeded $10,000 completes every other field for the account and checks 15a. **This is a legitimate filing position, not a defect.** | [IRM 4.26.16](https://www.irs.gov/irm/part4/irm_04-026-016), [FinCEN](https://www.fincen.gov/reporting-maximum-account-value) |
| 25 or more accounts | Items 14a/14b: report the **number** of accounts and skip the per-account parts — **but retain the detail records** and produce them on request. | IRM 4.26.16 |
| Spouses | Separate FBARs are required **unless all accounts are jointly owned** and both spouses sign **FinCEN Form 114a**. Any separately-owned account forces separate filings. | IRS FBAR page |
| Recordkeeping | Retain the name on the account, account number, institution name and address, account type, and maximum value for **five years from the FBAR due date**. | IRS FBAR page |
| Non-willful penalty | Statutory $10,000, inflation-adjusted to **$16,536** for penalties assessed on or after January 17, 2025. *Bittner v. United States*, 598 U.S. 85 (2023) fixed this as **per report, not per account**. | [31 CFR 1010.821](https://www.law.cornell.edu/cfr/text/31/1010.821) |
| Willful penalty | The **greater of** the inflation-adjusted statutory figure — **$165,353** for penalties assessed on or after January 17, 2025 — **or 50%** of the account balance at the time of the violation. | [31 CFR 1010.821](https://www.law.cornell.edu/cfr/text/31/1010.821) |

<!-- /FBAR_RULES -->

Every figure above lives in the shared `fbar` block of
`scripts/tax_constants.json` and is asserted against this table by
`tests/test_reference_consistency.py`. Do not restate a number here that is not
in the constants.

### Two things the penalty figures are easy to get wrong

- **They index by assessment date, not tax year.** 31 CFR 1010.821 is adjusted
  annually and the table is cumulative: a TY2025 FBAR assessed in 2027 takes the
  2027 column, not the 2025 one. `fbar_penalty_maximum()` selects the latest
  column effective on or before the assessment date and reports which one it
  used.
- **"~$100,000" is the statutory base, not the willful maximum.** The operative
  figure is the adjusted one.

### Delinquent filings

A filer who was required to file and did not, is not under civil examination or
criminal investigation, and has not been contacted by the IRS about the missing
report, should look at the **Delinquent FBAR Submission Procedures** on the
[IRS FBAR page](https://www.irs.gov/businesses/small-businesses-self-employed/report-of-foreign-bank-and-financial-accounts-fbar).
The reconciler points at this whenever an account lands on
`NEW_PREVIOUSLY_UNREPORTED` or a filing runs past the extended deadline.
Reference only — this plugin implements no delinquent-filing workflow.

---

## 2. Why this module exists

The rules above are the easy part. The failure mode that motivated the module is
procedural: **the year-over-year reconciliation was being done by memory and
inconsistently.** One filer's roster carried a prior-year column beside the
current one; the other's had no prior-year column at all, so nothing guaranteed
that every account on the previous filing had been carried forward, closed out,
or consciously dropped.

Nothing about that is visible on the form. It only shows up as an account that
quietly stops being reported.

The minimum bar this module enforces is one sentence:

> **For every prior-year FBAR record there is a clear answer.**

An account that disappears between filings without a disposition is a blocking
finding, not a silent omission.

---

## 3. The roster

One JSON file per filer per year: `fbar_roster_<filer_slug>_<year>.json`.
Full field list and validation rules in `scripts/fbar_schema.py`; a worked
synthetic pair is in `examples/fbar/`.

The three fields that carry the design:

**`account_key`** — a stable, human-assigned slug that is the join key across
years. `account_number` is *not* a usable join key: institutions renumber, split
multi-currency wallets into per-currency IDs, merge them back, and workpapers
transcribe them inconsistently. It is required on every record; the schema does
not derive one, because a derived key churns when ordering changes and a key
that churns is not a join key. Assign it once when the account is first rostered
— `<institution-slug>-<type>-<ordinal>` is a serviceable pattern — and never
regenerate it.

**`aliases[]`** — every identifier the account has ever been filed or recorded
under. This is the audit trail that lets next year's run match on either
rendering without re-reading this year's `identity_changes`.

**`conventions_applied[]`** — which of the filer-adopted treatments in §5 were
used to produce this account's figures. A year-over-year *change* here is a
finding (V9), because these treatments are unconfirmed and changing one silently
changes the reported number.

**`contract_number`** — a second identifier the institution issues *alongside*
the account number, where it issues one. T-Bank prints «Номер договора» and
«Номер лицевого счёта» on every bank statement; they are different numbers for
the same account, not a renumbering. Record both. The contract number is
usually the more stable cross-year join key, because a ledger number is reissued
on product migration and a contract number is not. Brokerage and IIS products
use one number for both roles — leave `contract_number` absent there, so
"absent" keeps meaning "the institution issues only one" rather than "nobody
filled it in".

### Multi-currency accounts

An account whose reported maximum is the sum of several currency balances at the
combined-peak instant uses `max_value_components` instead of
`max_value_local`/`max_value_currency`:

```jsonc
"max_value_components": [
  {"currency": "GEL", "amount": 2700.0},
  {"currency": "EUR", "amount": 852.2},
  {"currency": "USD", "amount": 1000.0}
]
```

V3 recomputes each component separately and sums the USD results. Setting both
`max_value_components` and `max_value_local` is rejected at load — a single
local amount is ambiguous on a multi-currency account.

### Exchange rates

`fx_rates[].units_per_usd` is **foreign currency units per one US dollar** —
Treasury's convention. USD is always `local ÷ units_per_usd`. The conversion is
never attempted in the other direction to make a stated figure agree: the defect
this guards against is a rate recorded once as itself and once as its own
reciprocal, under conflicting provenance labels. A validator that quietly
accepted either direction would erase exactly the finding it exists to produce.

#### Verify the rate, do not inherit it

**Fetch the Treasury rate live at the start of every cycle, before using any
rate already recorded in the workspace — including one already labelled
`treasury`.**

V1 checks a rate's *provenance label*, which is a different thing from its
value. A figure hand-copied from a commercial FX site, or a correct Treasury
rate for the wrong year, passes V1 without complaint. A stale rate then
propagates: it is copied into a workpaper, the workpaper is copied into the
next document, and every USD figure downstream is wrong by the drift. Nothing
in the pipeline can detect this, because every document agrees with every
other document. Only the source disagrees.

```bash
curl -s 'https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/rates_of_exchange?filter=record_date:eq:2025-12-31,country_currency_desc:in:(Russia-Ruble,Georgia-Lari)&fields=country_currency_desc,exchange_rate,record_date'
```

The FBAR rate is the Treasury Reporting Rate of Exchange for the **last day of
the calendar year** — `record_date` December 31 of the tax year, not the
quarter-end nearest it and not the rate on the date of the maximum. Record the
fetched value in `fx_rates[].units_per_usd` with `source: "treasury"` and the
query URL in `source_url`, so the next cycle can re-run the same query rather
than trusting this one.

If a rate already in the workspace disagrees with the live query, the live
query wins and every figure derived from the old rate is recomputed. Do not
reconcile the difference by adjusting a maximum.

### External account counts

`external_account_counts[]` records account counts stated by documents *outside*
the roster — a portfolio summary tab, a bank's own overview list. Each entry
records `institution`, optional `account_type`, `count`, and `source_document`.
V15 checks that the document does not list more accounts at that institution than
the roster carries. A surplus on the document's side means an account exists that
nobody has rostered.

---

## 4. Account-number normalization

Matching account numbers across years cannot be byte equality. The motivating
case is one brokerage account rendered with a trailing Latin `M` on the filed
form and a Cyrillic `м` in the workpaper, with different punctuation — which a
naive match reports as *one closed account plus one new account*.

`normalize_account_number()` applies, in order: Unicode NFKC → drop format
(zero-width) characters → upper-case → fold Cyrillic/Greek capitals that are
homoglyphs of Latin capitals → strip separators. So `12345678M` and
`123456/78-м` both normalize to `12345678M`.

Two deliberate limits:

- **Letter-to-letter folds only.** Cyrillic `З` looks like `3` but is not
  folded. A false *split* surfaces as a reviewable finding; a false *merge*
  makes two accounts look like one and drops a reportable account off the
  filing. The asymmetry is intentional.
- **Never for display.** Item 18 takes the account number as the institution
  states it. `assert_display_safe()` makes emitting a normalized number onto a
  worksheet a hard failure rather than something a future edit can quietly
  break.

### Matching order, and one thing it does not handle

Matching resolves in this order:

1. **Declared identity changes** (`identity_changes[]`).
2. **Exact `account_key`**, unless already claimed by a declaration.
3. **Alias membership** — the normalized number appears in the other record's `aliases[]`.
4. **Contract number**, normalized, within the same institution. Scoped by
   institution because two banks may issue the same digits, and a false merge
   hides an account while a false split only produces a reviewable finding.
5. **Normalized account-number equality**.
6. **Same institution and trailing digits** — same institution and the same trailing four digits, which **suggests and never auto-accepts**.
7. **No match**.

Step six is a suggestion because a fuzzy match that silently linked two
accounts would defeat the point: the module exists to make unmatched accounts
visible, not to make them disappear.

Matching is greedy in prior-roster order, and each current account can be
claimed only once. If two prior records share an alias, the one processed first
takes the current account and the second lands on `UNRESOLVED` — which reads as
if the first carried forward cleanly and the second vanished. That is a
reviewable finding rather than a silent error, but if you see an `UNRESOLVED`
you did not expect, check whether a sibling record is holding the alias it
needed. Keep `aliases[]` unambiguous: an identifier should appear on exactly one
account, except where an `identity_change` deliberately links several to one
predecessor.

---

## 5. Filer-adopted conventions (not settled law)

These treatments are load-bearing for the current filings but **could not be
confirmed against FinCEN guidance**. The module carries them as conventions with
a recorded rationale, prints them in the reconciliation report, and flags a
year-over-year change (V9). Flagging them is a feature, not a caveat.

<!-- FBAR_CONVENTIONS -->

| Convention id | Statement | Status |
|---|---|---|
| `multi_currency_single_account` | Currency sub-wallets sharing one account number are reported as a single account; the currencies are summed at each point in time to find the combined maximum. | **unconfirmed** — FinCEN guidance addresses aggregation *for the threshold*, not per-entry granularity for sub-accounts. Plausible and internally consistent, but the opposite treatment has been applied to the same institution family in an adjacent year. |
| `credit_card_positive_balance_only` | A credit card account is reportable only if it carried a positive balance (the bank owes the holder); negative or zero is not reportable. | **unconfirmed** as phrased. Directionally consistent with a credit card not being a financial account absent a credit balance owed to the holder. |
| `negative_balance_is_zero` | A negative balance is treated as $0 rather than a negative maximum. | **conservative** — reasonable; recorded as a convention, not a rule. |

<!-- /FBAR_CONVENTIONS -->

The registry lives in `scripts/fbar_schema.py::CONVENTIONS`. A roster naming a
convention that is not registered gets a "record the rationale" warning in the
report rather than silent acceptance.

**Keep the conventions file with the year it governs.** A conventions document
that physically sits in the prior year's folder while describing the current
cycle will eventually be silently inherited or silently lost.

### Deciding which side of the line an account falls on

`credit_card_positive_balance_only` says a credit account is reportable only if
it ever carried a positive balance in the filer's favour. On a statement with a
balance column that is a lookup. **Every movement register has no balance
column at all**, so the question has to be answered from transactions. These
four checks answer it; run all four, and treat a disagreement as a reason to
request a balance-bearing document rather than to pick a side.

1. **Net direction over the year.** Replay the register from a neutral zero
   baseline and look at the direction, not the level. A balance growing more
   negative is debt being drawn; one trending toward zero is debt being repaid.
   Neither establishes a level — the baseline is fictional — but the direction
   is real and is order-invariant.
2. **Interest and fees charged TO the filer.** Line items like `Loan interest`
   / `Проценты по кредиту` are a debtor signature. An asset account pays
   interest *to* the holder; it never charges it. One such line is strong
   evidence the account was in debit for the period it covers.
3. **The account's age.** An account opened years before the tax year carries
   forward whatever balance it already had. A small *relative* positive
   excursion above an assumed-zero start is an artefact of the assumed start,
   not evidence of a genuine credit balance. Weight check 1 by this: on an
   account opened during the tax year, the zero baseline is real and the
   replay means much more.
4. **A parallel foreign declaration, where one exists.** Where the filer also
   files a foreign-country asset declaration, read that regime's stated
   methodology. Russia's Bank-of-Russia 5798-У declaration, for one, excludes
   credit cards explicitly, for the same underlying reason FBAR would. A second
   regime reaching the same exclusion on its own reasoning is corroboration —
   it is not authority for the FBAR position, and the note must not cite it as
   though it were.

Checks 1 and 2 are mechanical and `fbar_reconciler.assess_credit_account()`
reports them from a parsed register. Checks 3 and 4 are
judgment and stay with Phase 1. Record the
outcome in the account's `notes` with the checks that drove it, because the
convention is unconfirmed and next year's reviewer needs the reasoning, not
just the answer.

---

## 6. Dispositions

Every prior-year account resolves to exactly one of these. Precedence when
several could apply: a declared identity change wins (it is the filer's own
written statement about the account), then closure, then a changed identifier,
then non-reportability, then plain carry-forward.

| Disposition | Meaning | Evidence required | Severity |
|---|---|---|---|
| `CARRIED_FORWARD` | Matched, open, current-year maximum determined | `max_value_usd` **or** `max_value_unknown` | ok |
| `CLOSED` | Closed during the year | `account_status: closed` + `closed_date` in the year | ok |
| `CLOSED_PRIOR_YEAR` | Closed before the year began; correctly absent | `closed_date` before Jan 1 of the tax year | ok |
| `DORMANT_ZERO` | Open, zero or negative throughout → not reportable | `reportable: false` + `not_reportable_reason` | ok |
| `RENUMBERED` | Same account, new identifier | Current record lists the prior ID in `aliases[]` | warn |
| `SPLIT` | One prior account → N current accounts | `identity_change` record; each current record lists the prior ID | warn |
| `MERGED` | N prior accounts → one current account | `identity_change` record; current record lists all prior IDs | warn |
| `UNRESOLVED` | No match, no disposition | — | **BLOCKING** |

Current-year accounts with no prior-year antecedent:

| Disposition | Meaning | Evidence required | Severity |
|---|---|---|---|
| `NEW_OPENED` | Opened during the year | `opened_date` within the tax year | ok |
| `NEW_PREVIOUSLY_UNREPORTED` | Existed earlier, absent from the prior filing | Explicit `notes` justification | warn — possible prior-year omission; consider the Delinquent FBAR Submission Procedures |
| `NEW_UNEXPLAINED` | Neither | — | **BLOCKING** |

### Reporting-model changes

Filing one account in one year and two the next, for the same underlying
relationship, is a **reporting-model change**. It needs an `identity_change`
record with `reporting_model_changed: true` and a written `justification`.
Missing justification is blocking — the governing convention (§5) is
unconfirmed, so the reasoning has to be written down contemporaneously and
retained with the five-year records.

```jsonc
{
  "type": "split",
  "prior_account_keys": ["wallet-profile"],
  "current_account_keys": ["wallet-eur", "wallet-usd"],
  "reason": "Institution now issues a per-currency balance ID.",
  "reporting_model_changed": true,
  "justification": "Statements and the year-end summary are now issued per balance ID, so a profile-level maximum can no longer be tied to a statement."
}
```

---

## 7. Validators

| ID | Check | Severity |
|---|---|---|
| V1 | Every FX rate is sourced `treasury`; anything else warns with the fiscaldata URL | warn |
| V2 | Every FX rate `as_of` is Dec 31 of the tax year | error |
| V3 | `max_value_usd` recomputes within $1 — from `max_value_local ÷ units_per_usd`, or from the USD sum of `max_value_components` | error |
| V4 | Aggregate maximum vs the $10,000 threshold, with the arithmetic printed | info (warns when an undetermined maximum undercuts a "no filing" conclusion) |
| V5 | Threshold tripped → every account reportable unless a `not_reportable_reason` is recorded | error |
| V6 | 25 or more reportable accounts → Item 14a path, detail retained but not filed | info |
| V7 | Each reportable account has a complete institution address (Items 17, 19, 20, 22, 23; Item 21 State is optional) | warn |
| V8 | Prior-year `max_value_unknown` accounts are resolved, or carry a re-justification | warn — **never blocking**, Item 15a is a valid position |
| V9 | `conventions_applied` changed from the prior year for the same `account_key` | warn |
| V10 | Any separately-owned account → separate FBARs required, so `form_114a_spousal` must be false | error |
| V11 | Filing date against April 15 / October 15; retention horizon; delinquency relief pointer | info (warn past the extended deadline) |
| V12 | Reportable account asserting a determined maximum on a basis weaker than full-year statements, without electing Item 15a. Quiet on `anchored_full_year` **only** when the anchor's cited document is present in the workspace; distinct wording for `non_negativity_floor`, which is a proven bound rather than an estimate | warn |
| V13 | Every `source_documents[]` path exists in the workspace | warn |
| V14 | Reportable account citing no source at all (no `source_documents`, no `max_value_anchor`) whose maximum sits within 15% of the prior year's filed maximum — the signature of a carried-forward placeholder | warn |
| V15 | A document outside the roster (`external_account_counts[]`) lists more accounts at an institution than the roster carries | warn |
| V16 | An account number whose digit count differs from a clear majority of its siblings at the same institution — the shape of a dropped or doubled digit | warn |

**Severity contract:** `blocking` and `error` exit non-zero with
`"isError": true` in the payload. `warn`, `info` and `ok` do not. Warnings never
block — Item 15a elections and a Treasury rate not yet substituted are
legitimate interim states, and a tool that refused to run on them would be
worked around instead of used.

### V10: the two uses of Form 114a

Form 114a has two independent uses and the roster carries them as separate
flags:

- `form_114a_spousal` — one spouse authorized to file on the other's behalf.
  Available **only** when all accounts are jointly owned.
- `form_114a_preparer` — a third-party preparer authorized to file (Form 114
  box 44a). Unrelated to ownership, and entirely compatible with separate
  filings.

V10 looks only at the first. Conflating them hard-errors on the correct, normal
pattern: separate FBARs plus a paid preparer.

---

## 8. FBAR vs Form 8938 (FATCA)

Related, frequently confused, **not** interchangeable — different thresholds,
different agency, different form, and filing one does not satisfy the other. An
account can easily be reportable on both.

| | FBAR (FinCEN 114) | Form 8938 |
|---|---|---|
| Filed with | FinCEN, via BSA E-Filing — separate from the return | The Form 1040 |
| Threshold | Aggregate over $10,000 at any time | Higher, and varies by filing status and residence |
| Deadline | April 15, automatic extension to October 15 | The return's due date, including extensions |

Form 8938 is **out of scope** for this plugin. See `docs/limitations.md`.

---

## 9. Workflow

Phase 1 (Claude) extracts; Phase 3 (Python) does the arithmetic and rule
enforcement — the same split as every other tool here. Phase 0 (Python) is an
optional pre-processor that feeds Phase 1 where machine-readable statements
exist.

```
Phase 0  (Python)   statement_parser.py  -> account_timelines_<filer>_<year>.json
                    |-- per-source readers (running balance, two-sheet credit card,
                    |   period-end summary, PDF movement register,
                    |   point-in-time balance, daily value series)
                    |   -> normalized movement records
                    |-- continuity + gap detection
                    '-- timeline + derived max_value_basis
                            |
Phase 1  (Claude)   Read timelines + statements + the prior-year filed 114 PDF
                    -> emit fbar_roster_<filer>_<year>.json
                    -> USER CONFIRMATION GATE (SOP Phase 2)
                            |
Phase 3  (Python)   fbar_reconciler.py
                    |-- normalize + match         -> identity resolution
                    |-- disposition state machine -> every prior account resolved
                    |-- validators V1-V13
                    '-- reconciliation.md + worksheet.md + roster.json
```

### Matching unlabelled columns to real accounts

A multi-account export labels its columns however the exporting app happens to
— a nickname ("Брокерский счёт", "Брокерский счет 1"), a blank, or a
position — and often carries no account number at all. Match each column by its
**first non-blank date, never magnitude and never column order.**

Take each column's first non-blank, non-zero row date, and match it against
each candidate account's **contract-open date**, which each account's own
report states in its header line (`Инвестор: NAME / NUMBER от DATE`). An
account cannot have a value before it existed, so the earliest column belongs
to the earliest-opened account, and the match is forced rather than guessed.

Magnitude and column order both fail on the case that matters. A transposition
— two accounts' columns swapped in the workbook — preserves every magnitude and
every position, so neither signal changes; only the start dates disagree. One
such swap stood for over a week in a live workspace and was found this way.

Where two accounts were opened on the same date the method is silent. Say so
and request the account numbers; do not fall back to magnitude because the
better method ran out.

### A replayed series that dips below zero

Replaying a year of transactions from an anchor will occasionally show the
running balance going negative for a few rows around a cluster of transactions
sharing one timestamp — four postings in the same minute, settled by the bank
in an order the document does not record. The dip is arithmetically genuine for
the order you happened to sort in, and impossible for the account. It is not
evidence that the anchor is wrong.

**The resolution rule:**

1. **An anchor is judged by conservation, not by any assumed intra-tie order.**
   `final = opening + net sum` holds for every permutation of the movements.
   If the replayed final balance matches an independently known closing figure,
   the anchor and the ledger are both sound, and that conclusion is untouched
   by how the ties were ordered.
2. **The opening, the maximum and the closing are conservation results.** A dip
   strictly *between* two confirmed anchors changes none of them. Only the
   intermediate trajectory is affected, and the trajectory is not a filed
   figure.
3. **Rule: do not invent a plausible sub-minute order to remove the dip.** Any order
   that clears it is one you chose, and it will be indistinguishable in the
   workpaper from an order the document actually recorded. Report the condition
   and do not display a false-precision intra-minute path.

A dip that persists *outside* a same-timestamp cluster is a different finding
and does mean the anchor or the ledger is wrong. The distinguishing test is
whether every row inside the excursion shares one timestamp.

### Cross-validate a reconstruction before trusting it

**Never trust an anchored reconstruction on an account whose answer is not
otherwise knowable, until the same chain has reproduced a known answer on
another account in the same batch.**

A reconstruction has assumptions in it — which end of the anchor date the
movements fall on, whether the ledger is complete, how ties were handled. Every
one of them is invisible when the answer is unknown, because there is nothing
for a wrong answer to disagree with. Run the chain first on an account whose
maximum is already established from a balance-bearing statement or a prior
filing. If it reproduces that figure to the cent, the assumptions held for that
data, and the novel result earns the same confidence. If it does not, the
reconstruction is wrong in a way that would have been invisible on the account
you actually cared about.

Record which account was used as the control, and its agreement, in the
reconstructed account's `notes`. Next year's reviewer cannot re-derive that the
method was validated; they can only read that it was.

### When a broker report states holdings but no prices

A broker's trade-and-operations report states cash exactly and holdings exactly
— ISIN and share count — and, routinely, **no price at all**. Eight brokerage
accounts in one live cycle had the identical gap. Pricing those holdings by
hand from market data is not the answer: it puts a figure on a filing that the
institution never stated, on a date and a close nobody can reproduce later.

**Request a daily portfolio-value export** — a `.csv` or an `.xlsx` tab of one
row per date and one column per account, values already priced. It is the
closest thing to a bank's own daily balance column that a brokerage produces,
and it closed the gap for six accounts in one pass. Requesting another trade
report gets another report with the same gap.

**Phase 0 reports what the file says; it never decides what the filing claims.**
It opens the statements, normalizes numbers and dates, merges multi-currency
sub-accounts onto one axis at the December 31 rate, detects coverage gaps and
continuity breaks, and **derives** `max_value_basis` from what it found.
Reportability, ownership, which convention applies, the Item 15a election and
which FX source to record all stay with Phase 1. A figure the parser cannot
support from the file is emitted as absent with a reason, never as a default —
an empty export is reported as "no data", not as a zero balance.

That derivation is the point. If a human types `statement_full_year` into a
roster by hand, V12 warns only when someone has already admitted the evidence is
weak; derived from gap and continuity analysis, V12 fires honestly.

Phase 0 is optional and file-format-bound: it reads `.xlsx`, plain-text and
PDF **movement registers**. A register states transactions and period totals and
no balance of any kind, so it yields movements and a `max_value_basis` of
`estimate` — never a maximum value. **Balance-bearing PDF statements are read by
the agent's `Read` tool in Phase 1**, as they always were, and no profile derives
a balance from transaction deltas.

Two readers cover documents that are not statements at all. A **point-in-time balance**
document (`point_balance`) states one bank-confirmed level at one date, with no
period and no transactions; alone it supports nothing, and its value is as the
anchor a complete ledger is carried across. A **daily value series**
(`daily_value_series`) is a wide table — one row per date, one column per
account, values already priced — and is the only reader that yields several
accounts from one file.

```bash
# Phase 0: parse statements into balance timelines (where .xlsx/.txt exist)
python ./scripts/statement_parser.py parse \
    --sources    ./sources.json \
    --filer      primary \
    --output-dir ./out


# Reconcile the current year against the prior year
python ./scripts/fbar_reconciler.py reconcile \
    --current ./fbar_roster_primary_2025.json \
    --prior   ./fbar_roster_primary_2024.json \
    --output-dir ./out

# First cycle: validate one roster with no prior year
python ./scripts/fbar_reconciler.py validate --roster ./fbar_roster_primary_2025.json

# Keying worksheet only
python ./scripts/fbar_reconciler.py worksheet --roster ./fbar_roster_primary_2025.json
```

Outputs: `fbar_reconciliation_<filer>_<year>.md` (dispositions, findings,
conventions, threshold arithmetic), `fbar_worksheet_<filer>_<year>.md` (one
block per reportable account, labelled with live Form 114 item numbers), the
machine-readable findings JSON, and the normalized roster — which becomes next
year's `--prior` input. That round trip is what makes the reconciliation
self-sustaining after the first cycle.

The emitted roster deliberately carries **no `identity_changes`**. Those
describe the transition *into* the year just reconciled; a split's
`prior_account_keys` name accounts that no longer exist on the roster they would
be carried into, so keeping them would fail the very next run. The declarations
and their justifications live in the reconciliation report and findings JSON
written beside it — which is what the five-year recordkeeping rule needs — and
the account-level link survives in each record's `aliases[]`.

### Reading the prior-year filing

The filed Form 114 **Part II Continuation Statement** is a clean tabular PDF —
institution and address, account number, maximum value, the amount-unknown flag,
account type — and reads reliably with the `Read` tool. Direct extraction at
those tables and the typed value overlays; the form-body boilerplate text layer
is font-obfuscated. Field mapping is in `references/form_processing.md`.

**Take prior-year values from the filed form, never re-quoted from workpaper
prose.** Workpapers that restate last year's maxima drift by a dollar or two
through rounding. Harmless in isolation, and exactly the copy error the
roster-to-roster pipeline eliminates.

Record honestly what the workspace actually holds. `prior_year.source`
distinguishes a BSA acknowledgment copy (`form_114_filed`) from a preparer
review copy (`form_114_review_copy_pdf`) — they are not the same evidence.

### A document that identifies no account

App screenshots and partial exports arrive with no account number, no card
number and no holder name anywhere on the page. Two responses are wrong and one
is right:

- **Wrong:** attribute it to the account it probably belongs to. A plausible
  guess becomes a cited `source_documents[]` entry, and V13 will confirm the
  file exists — proving nothing about whether it is the right file.
- **Wrong:** drop it. An account whose only evidence was discarded looks like
  an account with no evidence, which is a different and much quieter problem.
- **Right:** file it under the convention below and leave the pending account
  pending.

**Name it `<Institution>_UNIDENTIFIED_<short description>.<ext>`** — for example
`TBank_UNIDENTIFIED_debit_card_screenshot.pdf`. Record in the account notes for
any account it *might* belong to that an unattributed document exists and does
**not** resolve the gap: never attribute a document without a visible identifier —
an account number, a card number, or a holder name printed on
the document itself. A filename someone chose is not an identifier; neither is
the folder it arrived in.

The correct next action is always to request a document that does carry an
identifier, not to reason about which account this one fits.

---

## 10. Manual checklist

Things no script can check. Run these before filing.

- [ ] **Every institution named anywhere in the workpapers resolves to a roster
      entry.** Free text in an action item or a note is not machine-reconcilable;
      a bank that appears in prose but on no roster is either a stray word or an
      entirely unrostered institution.
- [ ] Every account the taxpayer *holds* is on the roster, not merely every
      account with a statement. The threshold aggregates all foreign financial
      accounts, including ones that produced no income and ones that are trivial.
- [ ] Every `*_UNIDENTIFIED_*` document in the workspace has either been
      resolved to an account by a newly obtained identifier, or is explicitly
      noted as unresolved on the account it was thought to belong to. An
      unattributed document is a pending question, not a filed record.
- [ ] Signature-authority-only accounts (Part IV) are handled by hand — the
      reconciler carries them but does not reconcile or emit worksheets for them.
- [ ] Each spouse's filing obligation was decided separately. Any
      separately-owned account means two FBARs.
- [ ] The Item 15a election, where used, has a contemporaneous note saying why
      the maximum could not be determined.
- [ ] Supporting records for every account are actually in the workspace and
      will still be there five years from the due date.
- [ ] Figures were confirmed against final bank records before transmission.

---

## 11. Limits

This module produces **worksheets and findings, not a filed form.** It does not
generate BSA E-Filing XML or a transmittable PDF; it does not handle Form 8938,
Part IV or Part V. FinCEN 114 is
transmitted through BSA E-Filing by the taxpayer or their preparer, and every
figure must be confirmed against final bank records first.

Statement parsing is a **separate upstream tool** (Phase 0,
`scripts/statement_parser.py`), and it is narrower than it sounds. It reads
`.xlsx`, plain-text and PDF movement registers — **a balance-bearing PDF
statement stays with the agent's `Read` tool**, so a PDF-only institution gets
movements and an `estimate` basis, not a maximum value. **No balance is ever synthesized from transaction deltas alone** — a PDF movement register states no balance, so on its own it yields movements and an `estimate` basis, and balance-bearing PDFs stay with `Read`. The single exception is an ANCHORED reconstruction: where a document states a dated balance and every register reconciles against the bank's own period totals, the opening is carried from that citation and the year replayed exactly, yielding `anchored_full_year` with the anchor recorded. Where no anchor exists but the ledger is complete and reconciled, the account's non-negativity gives a `non_negativity_floor` — a proven bound, not an estimate. It never decides
reportability, ownership, conventions or the Item 15a election, and it never
reconstructs a balance it cannot evidence. The reconciler itself still takes
only normalized JSON.

This is not tax or legal advice. See `DISCLAIMER.md` and `docs/limitations.md`.
