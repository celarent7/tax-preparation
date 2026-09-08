# FBAR Workflow Enhancements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the FBAR pipeline first-class support for the reconstruction
techniques, document types and defect patterns that four live filing sessions
proved are routine, without weakening the "Phase 0 reports what the file says"
guarantee that makes `max_value_basis` worth deriving.

**Architecture:** Three staged releases along the dependency graph, not the
proposal's priority labels. **3.4.4** is documentation and convention text only
— zero code risk, ships immediately. **3.5.0** extends the roster schema and
adds three validators, all reachable from a hand-authored roster with no new
reader. **3.6.0** adds the two new Phase 0 readers (a point-in-time balance
anchor and a wide daily-value series) plus the anchored replay that makes the
3.5.0 vocabulary *derivable* rather than merely assertable.

**Tech Stack:** Python 3 standard library first (`decimal`, `json`, `re`,
`csv`, `datetime`), `openpyxl` for `.xlsx`, `pytest`. No new dependencies.

**Spec:** the FBAR enhancement proposal of 2026-09-08, a workspace document kept outside this repository
(referred to below as "the proposal"; findings are cited as F1–F12).

## Global Constraints

- **Decimal only for money.** `float` never touches a currency figure in
  `statement_parser.py` or `fbar_schema.py` arithmetic. The roster JSON stores
  numbers, the parser stores `Decimal`; `money()` is the one conversion point.
- **Standard library first.** No new entry in `requirements.txt`.
- **Unknown keys are rejected, with the nearest valid key named.** Every new
  schema field must be added to its `*_FIELDS` table, or a roster using it
  fails to load. This is the repo convention (`_reject_unknown`,
  `_reject_unknown_keys`) and applies to `ACCOUNT_FIELDS`, `ROSTER_FIELDS`,
  and `SOURCE_FIELDS` alike.
- **Evidence is capped, never promoted.** `weaken_basis` is the only way a
  basis changes. Any new member of the vocabulary must be placed in
  `BASIS_STRENGTH` deliberately, because position *is* strength.
- **No balance is synthesized from transaction deltas alone.** Task 3.6.2
  refines this rule; it does not repeal it. Read that task's rationale before
  touching any replay code.
- **`normalize_account_number` output is never displayed or filed.**
  `assert_display_safe` enforces it. Item 18 takes the institution's own
  rendering.
- **A new validator function named `vNN_*` in `fbar_reconciler.py` is
  auto-discovered by `tests/test_reference_consistency.py::test_fbar_guide_validator_table_covers_every_implemented_validator`,
  which requires a matching `| VNN |` row in `references/fbar_guide.md` §7.**
  Adding a validator without the guide row fails the suite. This is by design.
- **`set(BASIS_STRENGTH) == set(MAX_VALUE_BASES)`** is asserted by
  `tests/test_statement_readers.py:243`. The two tuples move together or the
  suite fails.
- **All fixtures synthetic.** No real account number, holder name, institution
  address or file path from a live workspace enters the repository. The repo
  has been scrubbed of these once already (commit `a31f3cc`).
- **Run the full suite before each commit:** `python -m pytest tests/ -q`.

---

## Finding verification against HEAD

The proposal was reconstructed against plugin **v3.4.1**. This repository is at
**v3.4.3**, and five commits since (`b93371d`, `de59a00`, `56293fc`, `bb1c4ef`,
`ad6e586`) touch the exact readers the proposal analyzed. Every finding was
re-checked against HEAD before planning:

| F | Claim | Verdict at HEAD | Evidence |
|---|---|---|---|
| 1 | No point-balance profile, no anchored reconstruction | **Open** | No `anchor`/`PointBalance` symbol anywhere; `READERS` has four entries, none point-in-time |
| 2 | `MAX_VALUE_BASES` has no anchored/floor category | **Open** | `fbar_schema.py:66-72` — five members, unchanged |
| 3 | No profile for a daily priced value series | **Open** | No `csv` reader; `PROFILES` has six entries, all single-account ledgers |
| 4 | Column-to-account matching is manual | **Open** | No `contract_open_dates` or equivalent side-input anywhere |
| 5 | No validator for an unsourced figure tracking last year | **Open** | `validate()` runs V1–V13; no `v14_*` exists |
| 6 | Same-timestamp ties undocumented | **Open** (adjacent machinery exists) | `BOUND_UNORDERED` and `MovementRecord.order_key` handle *intraday ordering for a balance-bearing file*; neither covers a **replayed** series dipping negative between two confirmed anchors. Commit `f714236` documented the intraday tie-break, not this. |
| 7 | No `contract_number` on the account schema | **Open**, and narrower than proposed | `MovementRegisterProfile` **already** parses `contract_line` and `account_line` separately, so Phase 0 knows the distinction. The loss is purely in `ACCOUNT_FIELDS`, which has no field to carry it. |
| 8 | `credit_card_positive_balance_only` has no decision procedure | **Open** | `fbar_schema.py:118-131` still reads "unconfirmed as phrased" with no method |
| 9 | No account-count reconciliation against a summary | **Open** | V6 counts roster accounts against the 25-account rule only; nothing compares to an external count |
| 10 | No account-number length check | **Open**, and misplaced as proposed | `normalize_account_number` is a pure fold on the matching hot path returning a bare `str`. It has **no channel to emit a warning**, and its docstring forbids its output reaching display. The check belongs in the reconciler, where `Finding` is already the output type. |
| 11 | Live Treasury verification is not a named step | **Open** | `fiscaldata` appears once in the guide, inside the V1 table row — as a *remedy after a warning*, never as a step performed first |
| 12 | No convention for an unattributable document | **Open** | No `UNIDENTIFIED` convention in guide or schema |

**Nothing in the proposal has been closed by the 3.4.1 → 3.4.3 work.** Two
findings are re-scoped (F7 narrower, F10 relocated); the rest stand as written.

---

## Design decisions taken before task 1

These are the choices an executor must not re-litigate mid-task.

### D1 — An anchor is a **Phase 0 input**, not a Phase 1 assertion

The proposal (F2) asks V12 to treat a new `anchored_full_year` basis the same
as `statement_full_year`. Taken naively that reintroduces exactly the hole
`derive_basis` exists to close. Its own docstring says so:

> If a human types `statement_full_year` into a roster by hand, V12 is
> decorative — it warns only when someone has already admitted the evidence is
> weak.

A plain new enum member is a value anyone can type to silence V12. The
proposal already contains the fix as a subordinate clause — V12 goes quiet
"once the roster schema records the anchor's own citation" — and **this plan
makes that the load-bearing design**:

1. The anchor is declared in `sources.json` and read by a real reader
   (`point_balance`), so `derive_basis` *derives* `anchored_full_year` from a
   document, exactly as it derives every other basis. This is the primary path.
2. Where a roster is hand-authored (a prior-year filed figure supplying the
   opening, with no machine-readable file), `max_value_basis:
   "anchored_full_year"` is accepted **only** alongside a structured
   `max_value_anchor` object naming its own dated source. `_validate_account`
   hard-errors without it, and V12 stays quiet only when the anchor validates
   *and* its cited document is present under the workspace.

Path 1 is the one to reach for. Path 2 exists because the prior-year filed
Form 114 is a PDF read by the agent's `Read` tool, not a Phase 0 file, and
refusing it would make the strongest evidence in practice unrepresentable.

### D2 — Where the two new bases sit in `BASIS_STRENGTH`

```
statement_full_year          strongest
anchored_full_year           exact arithmetic, but the opening is cited, not observed
statement_partial
period_end_summaries_only
non_negativity_floor         a proven bound; strictly more than a guess
estimate
unknown                      weakest
```

`anchored_full_year` sits *below* `statement_full_year` because the opening is
an assertion from another document rather than a column in this one — an
honest ordering, and it costs nothing: V12's skip list names both explicitly,
so the ordering governs `weaken_basis` capping and nothing else.

`non_negativity_floor` sits *below* `period_end_summaries_only` and *above*
`estimate`. A floor says less about the maximum than two bounding balances do,
but it is a proven lower bound rather than an analyst's guess, and V12's
message must say so.

### D3 — Replay is permitted only against the register's own reconciled totals

`_assert_register_reconciles` (`statement_parser.py:1799`) already checks a
parsed register's rows against the bank's stated period totals. That check is
the completeness proof that makes an anchored replay honest, and Task 3.6.2
consumes it: **a replay that cannot cite a reconciled register is refused, not
downgraded.** This is why the no-synthesis rule survives — the balance is not
invented from deltas, it is carried from a cited, dated observation across a
ledger the bank's own arithmetic confirms is complete.

### D4 — F10's check moves to the reconciler; F12's roster bucket is declined

F10 becomes validator V16 over sibling accounts at one institution, for the
reason in the verification table. F12's optional `unidentified_documents`
roster bucket is **declined**: no validator would consume it, nothing would
notice if it went stale, and a schema field nothing reads is a field that
drifts. The naming convention in the guide is the whole deliverable.

### D5 — `.integrity-sha256.txt` is vestigial and out of scope

Its paths point at a directory this repository is not (`.../taxes/.claude/plugins/...`),
its first line hashes the empty string, and no script or test reads it. Do not
regenerate it as part of this work; raise it separately.

---

## File structure

**Created:**

| Path | Responsibility |
|---|---|
| `scripts/anchor_reconstruction.py` | Pure arithmetic: carry a dated balance across movements to a target date; derive opening/max/closing for a year; detect a mid-window negative excursion. No I/O, no profiles, no policy. Kept out of `statement_parser.py` because that file is already 2,670 lines and this is a self-contained algebra with its own test surface. |
| `tests/test_anchor_reconstruction.py` | Unit tests for the algebra above, including the F6 tie case. |
| `tests/test_daily_value_series.py` | Reader tests for the wide daily-value profile and its column matching. |

**Modified:**

| Path | Change |
|---|---|
| `references/fbar_guide.md` | F4 recipe, F6 pitfall, F8 decision procedure, F11 workflow step, F12 convention; §7 validator rows for V14–V16; §9 workflow and Phase 0 reader list |
| `scripts/fbar_schema.py` | `MAX_VALUE_BASES` +2; `ACCOUNT_FIELDS` + `contract_number`, `max_value_anchor`; `ROSTER_FIELDS` + `external_account_counts`; `CONVENTIONS` note text for the credit convention |
| `scripts/fbar_reconciler.py` | V12 skip logic; new `v14_unsourced_figure_tracks_prior_year`, `v15_institution_account_count`, `v16_account_number_length`; `_institution_key` reuse; `match_accounts` contract-number join |
| `scripts/statement_parser.py` | `BASIS_STRENGTH` +2; `EVIDENCE_DECLARED_ANCHOR` and `EVIDENCE_PRICED_DAILY_SERIES`; `_EVIDENCE_CEILING` entries; `READERS` +2; `SOURCE_FIELDS` + anchor/series keys; `read_point_balance`, `read_daily_value_series`; `derive_basis` anchored branch |
| `scripts/statement_profiles.py` | `PointBalanceProfile`, `DailyValueSeriesProfile`, registry entries |
| `skills/tax-preparation/SKILL.md` | Step 6 workflow additions; scripts-table rows for `statement_parser.py` and `fbar_reconciler.py` |
| `plugin.json` | Version bump, once per tranche |
| `tests/test_fbar_validators.py`, `tests/test_statement_basis.py`, `tests/test_statement_readers.py`, `tests/test_reference_consistency.py` | New cases per task |

---

# Tranche 1 — v3.4.4, documentation and conventions only

**Covers F11, F12, F8, F4 (recipe half), F6 (pitfall half).** No behaviour
changes. Every task is testable through `tests/test_reference_consistency.py`,
which is this repo's established way of proving a reference document says what
the code assumes it says — the same pattern as
`test_fbar_guide_threshold_boundary_is_stated_as_exceeds`.

Why these five first: each is a procedure that was worked out live and then
lost, none depends on any other task in this plan, and a filer reading the
guide next cycle gets the benefit without waiting for a reader to be written.

---

### Task 1.1: Name the live Treasury rate check as a workflow step (F11)

A stale hand-maintained rate (79.10 RUB/USD from a commercial site) propagated
across an entire workspace for weeks. V1 catches a rate *labelled* non-Treasury;
it cannot catch a wrong number labelled `treasury`. Only fetching the rate
catches that, and nothing currently tells anyone to.

**Files:**
- Modify: `references/fbar_guide.md` — new subsection under §3 "Exchange rates" (after line 141, before the `---` that closes §3)
- Test: `tests/test_reference_consistency.py`

**Interfaces:**
- Consumes: nothing
- Produces: the phrase `api.fiscaldata.treasury.gov` appearing in the guide outside the V1 table row; Task 1.6 counts on no other task editing §3

- [x] **Step 1: Write the failing test**

Append to `tests/test_reference_consistency.py`:

```python
def test_fbar_guide_requires_a_live_treasury_rate_check():
    """A rate labelled `treasury` can still be a wrong number typed by hand.

    V1 checks the LABEL. Only fetching the rate checks the rate, so the guide
    has to say to fetch it rather than leaving it as a remedy after a warning.
    """
    text = read_ref("fbar_guide.md")
    assert "Verify the rate, do not inherit it" in text
    # The endpoint must appear somewhere other than the V1 validator row.
    outside_table = [ln for ln in text.splitlines()
                     if "api.fiscaldata.treasury.gov" in ln and not ln.startswith("|")]
    assert outside_table, "the fiscaldata endpoint appears only inside the V1 table row"
```

- [x] **Step 2: Run it and confirm it fails**

Run: `python -m pytest tests/test_reference_consistency.py::test_fbar_guide_requires_a_live_treasury_rate_check -v`
Expected: FAIL — `assert "Verify the rate, do not inherit it" in text`

- [x] **Step 3: Add the subsection to the guide**

Insert immediately after the "Exchange rates" paragraph in §3 (the one ending
"...erase exactly the finding it exists to produce."), before §3's closing `---`:

```markdown
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
```

- [x] **Step 4: Run the test and confirm it passes**

Run: `python -m pytest tests/test_reference_consistency.py -q`
Expected: PASS, no regressions

- [x] **Step 5: Commit**

```bash
git add references/fbar_guide.md tests/test_reference_consistency.py
git commit -m "Make the live Treasury rate check a step, not a remedy"
```

---

### Task 1.2: Name the convention for an unattributable document (F12)

One debit-card statement screenshot in a live batch carried no account number,
no card number and no holder name. Guessing which account it belonged to and
silently dropping it are both wrong; the third option needs a name so it is
reachable without inventing it again.

**Files:**
- Modify: `references/fbar_guide.md` — new subsection at the end of §9, after "Reading the prior-year filing"
- Modify: `references/fbar_guide.md` — one new bullet in §10
- Test: `tests/test_reference_consistency.py`

**Interfaces:**
- Consumes: nothing
- Produces: the literal token `UNIDENTIFIED` in the guide, relied on by nothing else in this plan

Per **D4**, the proposal's optional `unidentified_documents` roster bucket is
declined. Do not add it.

- [x] **Step 1: Write the failing test**

```python
def test_fbar_guide_names_the_unidentified_document_convention():
    """A document with no visible identifier gets a name, not a guess.

    The two failure modes are silent attribution (wrong number on a filing) and
    silent dropping (an account with no evidence that nobody notices). The
    convention exists to make the third option reachable.
    """
    text = read_ref("fbar_guide.md")
    assert "UNIDENTIFIED" in text
    assert "never attribute a document without a visible identifier" in text
```

- [x] **Step 2: Run it and confirm it fails**

Run: `python -m pytest tests/test_reference_consistency.py::test_fbar_guide_names_the_unidentified_document_convention -v`
Expected: FAIL — `assert "UNIDENTIFIED" in text`

- [x] **Step 3: Add the subsection**

Append to §9, after the "Reading the prior-year filing" subsection and before
§9's closing `---`:

```markdown
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
**not** resolve the gap. **Never attribute a document without a visible
identifier** — an account number, a card number, or a holder name printed on
the document itself. A filename someone chose is not an identifier; neither is
the folder it arrived in.

The correct next action is always to request a document that does carry an
identifier, not to reason about which account this one fits.
```

- [x] **Step 4: Add the §10 checklist bullet**

Insert into §10's list, after the "Every account the taxpayer *holds*..." bullet:

```markdown
- [ ] Every `*_UNIDENTIFIED_*` document in the workspace has either been
      resolved to an account by a newly obtained identifier, or is explicitly
      noted as unresolved on the account it was thought to belong to. An
      unattributed document is a pending question, not a filed record.
```

- [x] **Step 5: Run the tests and commit**

Run: `python -m pytest tests/test_reference_consistency.py -q`
Expected: PASS

```bash
git add references/fbar_guide.md tests/test_reference_consistency.py
git commit -m "Name the convention for a document that identifies no account"
```

---

### Task 1.3: Promote `credit_card_positive_balance_only` to a worked decision procedure (F8)

The convention states *what* the treatment is and admits it is unconfirmed. It
says nothing about how to determine which side of the line a given account
falls on — which is the hard part, because every T-Bank movement statement is a
register with no balance column at all.

**Files:**
- Modify: `references/fbar_guide.md` — new subsection in §5, after the conventions table's closing marker
- Modify: `scripts/fbar_schema.py:118-131` — the `note` text of `CONVENTIONS["credit_card_positive_balance_only"]`
- Test: `tests/test_reference_consistency.py`

**Interfaces:**
- Consumes: nothing
- Produces: the four-check procedure, referenced by Task 2.6 (`assess_credit_account`) as the checks it mechanizes

The convention **id** must not change — `test_fbar_guide_conventions_match_the_code_registry` compares the published ids against `CONVENTIONS` keys, and the guide table row is inside the `<!-- FBAR_CONVENTIONS -->` markers. **Put the new prose *outside* those markers**, below the table, or the id-extraction in that test will read the new lines as table rows.

- [x] **Step 1: Write the failing test**

```python
def test_fbar_guide_gives_a_credit_account_decision_procedure():
    """The convention says WHAT to do; the filer needs to know HOW to decide.

    Every register-only statement lacks a balance column, so 'did it ever carry
    a positive balance' cannot be read off the document. Four checks answer it.
    """
    text = read_ref("fbar_guide.md")
    assert "Deciding which side of the line an account falls on" in text
    for check in ("Net direction over the year",
                  "Interest and fees charged TO the filer",
                  "The account's age",
                  "A parallel foreign declaration"):
        assert check in text, f"missing check: {check}"


def test_credit_convention_note_points_at_the_procedure():
    """The registry note must not still read as 'unconfirmed, good luck'."""
    from fbar_schema import CONVENTIONS  # noqa: E402
    note = CONVENTIONS["credit_card_positive_balance_only"]["note"]
    assert "four checks" in note
    assert "fbar_guide.md" in note
```

- [x] **Step 2: Run and confirm both fail**

Run: `python -m pytest tests/test_reference_consistency.py -k "credit" -v`
Expected: 2 FAILED

- [x] **Step 3: Add the guide subsection**

Insert into §5 **after** the `<!-- /FBAR_CONVENTIONS -->` line and the two
paragraphs following it, before §5's closing `---`:

```markdown
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

Checks 1 and 2 are mechanical arithmetic over the register. Checks 3 and 4 are
judgment and stay with Phase 1. Record the
outcome in the account's `notes` with the checks that drove it, because the
convention is unconfirmed and next year's reviewer needs the reasoning, not
just the answer.
```

- [x] **Step 4: Update the registry note**

In `scripts/fbar_schema.py`, replace the `note` of
`CONVENTIONS["credit_card_positive_balance_only"]`:

```python
        "note": (
            "Directionally consistent with a credit card not being a financial "
            "account absent a credit balance owed to the holder, but "
            "unconfirmed as phrased. Determining which side of the line a "
            "given account falls on is NOT read off the document — every "
            "movement register lacks a balance column — so the four checks in "
            "references/fbar_guide.md §5 supply the procedure: net direction, "
            "interest charged to the filer, account age, and any parallel "
            "foreign declaration's own exclusions."
        ),
```

- [x] **Step 5: Run the tests and commit**

Run: `python -m pytest tests/test_reference_consistency.py tests/test_fbar_validators.py -q`
Expected: PASS

```bash
git add references/fbar_guide.md scripts/fbar_schema.py tests/test_reference_consistency.py
git commit -m "Give the credit-account convention a worked decision procedure"
```

---

### Task 1.4: Document the column-to-account matching recipe (F4)

Twice in one session a multi-account export had columns labelled only by
nickname. Matching by magnitude or column order would have preserved a genuine
transposition that had stood for over a week. First-non-blank-date against
contract-open-date caught it.

**Files:**
- Modify: `references/fbar_guide.md` — new subsection in §9, before the Phase 0 code block
- Test: `tests/test_reference_consistency.py`

**Interfaces:**
- Consumes: nothing
- Produces: the recipe that Task 3.4 implements as `match_columns_by_open_date`

- [x] **Step 1: Write the failing test**

```python
def test_fbar_guide_forbids_matching_columns_by_magnitude():
    """Two accounts' columns were transposed for a week; magnitude hid it.

    A transposition preserves both magnitudes and both column positions, so
    the only signal that survives it is WHEN each column starts.
    """
    text = read_ref("fbar_guide.md")
    assert "first non-blank date, never magnitude and never column order" in text
    assert "contract-open date" in text
```

- [x] **Step 2: Run and confirm it fails**

Run: `python -m pytest tests/test_reference_consistency.py::test_fbar_guide_forbids_matching_columns_by_magnitude -v`
Expected: FAIL

- [x] **Step 3: Add the subsection**

Insert into §9, immediately before the "Phase 0 reports what the file says"
paragraph:

```markdown
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
```

- [x] **Step 4: Run and commit**

Run: `python -m pytest tests/test_reference_consistency.py -q`
Expected: PASS

```bash
git add references/fbar_guide.md tests/test_reference_consistency.py
git commit -m "Document how an unlabelled export column is matched to an account"
```

---

### Task 1.5: Document the same-timestamp negative dip as a named pitfall (F6)

A naive replay of one account's transactions dipped negative around four
same-minute rows. The opening and the maximum were unaffected — both are
conservation results — but the dip looks like proof the anchor is wrong, and a
future session will spend hours "fixing" the ordering unless told not to.

**Files:**
- Modify: `references/fbar_guide.md` — new subsection in §9, after the matching recipe from Task 1.4
- Test: `tests/test_reference_consistency.py`

**Interfaces:**
- Consumes: nothing
- Produces: the resolution rule Task 3.3 implements as `detect_negative_excursion` returning an informational note

This is **distinct from** the existing `BOUND_UNORDERED` machinery. That covers
a balance-bearing file whose intraday order changes which observed balance is
the peak. This covers a *replayed* series between two independently confirmed
anchors, where the peak is order-invariant and only the intermediate trajectory
is affected.

- [ ] **Step 1: Write the failing test**

```python
def test_fbar_guide_documents_the_same_timestamp_dip():
    """The dip is a display artefact of an assumed order, not a bad anchor.

    Opening and maximum are conservation results: final = opening + net sum,
    whatever the internal order. Distrusting the anchor because an assumed
    intra-minute path dips negative inverts the actual logic.
    """
    text = read_ref("fbar_guide.md")
    assert "A replayed series that dips below zero" in text
    assert "conservation, not by any assumed intra-tie order" in text
    assert "do not invent a plausible sub-minute order" in text
```

- [ ] **Step 2: Run and confirm it fails**

Run: `python -m pytest tests/test_reference_consistency.py::test_fbar_guide_documents_the_same_timestamp_dip -v`
Expected: FAIL

- [ ] **Step 3: Add the subsection**

```markdown
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
3. **Do not invent a plausible sub-minute order to remove the dip.** Any order
   that clears it is one you chose, and it will be indistinguishable in the
   workpaper from an order the document actually recorded. Report the condition
   and do not display a false-precision intra-minute path.

A dip that persists *outside* a same-timestamp cluster is a different finding
and does mean the anchor or the ledger is wrong. The distinguishing test is
whether every row inside the excursion shares one timestamp.
```

- [ ] **Step 4: Run and commit**

Run: `python -m pytest tests/test_reference_consistency.py -q`
Expected: PASS

```bash
git add references/fbar_guide.md tests/test_reference_consistency.py
git commit -m "Name the same-timestamp dip as a data artefact, not a bad anchor"
```

---

### Task 1.6: Release v3.4.4

**Files:**
- Modify: `plugin.json:4`

- [x] **Step 1: Run the whole suite**

Run: `python -m pytest tests/ -q`
Expected: all PASS. If anything fails, fix it before bumping — a version bump on a red suite is the one thing that makes a tranche boundary useless.

- [x] **Step 2: Confirm no behaviour changed**

Run: `git diff --stat 3.4.3..HEAD -- scripts/`
Expected: only `scripts/fbar_schema.py`, and only inside the `note` string of one convention. Any other script in that diff means a task exceeded its scope.

- [x] **Step 3: Bump the version**

In `plugin.json`, `"version": "3.4.3"` → `"version": "3.4.4"`.

- [x] **Step 4: Commit and tag**

```bash
git add plugin.json
git commit -m "Release 3.4.4: FBAR workflow procedures from four live filing sessions"
git tag 3.4.4
```

---

# Tranche 2 — v3.5.0, schema and validators

**Covers F2, F7, F5, F9, F10, and F8's mechanical half.** Everything here is
reachable from a hand-authored roster; no new reader is required. Tranche 3
then makes the F2 vocabulary *derivable* from documents, which is what makes it
trustworthy — but the vocabulary has to exist first, and the validators have
value on hand-authored rosters immediately.

---

### Task 2.1: Extend the basis vocabulary (F2, part 1 of 3)

Two reconstruction methods used in live work are stronger than anything
`MAX_VALUE_BASES` can express. An anchored full-year replay is exact and
currently has to be recorded as `estimate`; a non-negativity floor is a proven
lower bound and reads as a guess.

**Files:**
- Modify: `scripts/fbar_schema.py:66-72` (`MAX_VALUE_BASES`)
- Modify: `scripts/statement_parser.py:500-506` (`BASIS_STRENGTH`)
- Test: `tests/test_statement_readers.py`, `tests/test_statement_basis.py`

**Interfaces:**
- Consumes: nothing
- Produces: the string constants `"anchored_full_year"` and
  `"non_negativity_floor"`, valid in `max_value_basis` and orderable by
  `weaken_basis`. Tasks 2.2, 2.3, 3.2 and 3.5 all depend on these exact spellings.

Placement follows **D2**. `weaken_basis` takes `max(..., key=BASIS_STRENGTH.index)`,
so a member's position *is* its strength; getting it wrong silently changes
what every cap does.

- [x] **Step 1: Write the failing test**

Append to `tests/test_statement_readers.py`:

```python
def test_new_bases_are_ordered_between_their_neighbours():
    """Position in BASIS_STRENGTH IS strength — `weaken_basis` is a max() on it.

    An anchored replay is exact arithmetic on a CITED opening, so it ranks just
    below a directly observed full year. A non-negativity floor is a proven
    bound, so it outranks a guess but says less about a peak than two bounding
    balances do.
    """
    from statement_parser import BASIS_STRENGTH, weaken_basis

    assert BASIS_STRENGTH.index("statement_full_year") \
        < BASIS_STRENGTH.index("anchored_full_year") \
        < BASIS_STRENGTH.index("statement_partial")
    assert BASIS_STRENGTH.index("period_end_summaries_only") \
        < BASIS_STRENGTH.index("non_negativity_floor") \
        < BASIS_STRENGTH.index("estimate")

    # Capping still only ever weakens.
    assert weaken_basis("anchored_full_year", "statement_partial") == "statement_partial"
    assert weaken_basis("statement_partial", "anchored_full_year") == "statement_partial"
    assert weaken_basis("non_negativity_floor", "estimate") == "estimate"
```

- [x] **Step 2: Run and confirm it fails**

Run: `python -m pytest tests/test_statement_readers.py -k "ordered_between" -v`
Expected: FAIL — `ValueError: 'anchored_full_year' is not a max_value_basis`

The existing `set(BASIS_STRENGTH) == set(MAX_VALUE_BASES)` test at
`tests/test_statement_readers.py:243` will also fail the moment only one of the
two tuples is edited. That is intentional — it is the coupling guard.

- [x] **Step 3: Extend both tuples**

`scripts/fbar_schema.py`:

```python
MAX_VALUE_BASES = (
    "statement_full_year",       # statements covering the whole year (FinCEN's safe harbour)
    "anchored_full_year",        # a cited dated opening + a complete, reconciled ledger
    "statement_partial",         # statements for part of the year only
    "period_end_summaries_only",  # period-end balances; intra-period peaks invisible
    "non_negativity_floor",      # opening unknown; a PROVEN floor, not a guess
    "estimate",
    "unknown",
)
```

`scripts/statement_parser.py`:

```python
BASIS_STRENGTH = (
    "statement_full_year",
    "anchored_full_year",
    "statement_partial",
    "period_end_summaries_only",
    "non_negativity_floor",
    "estimate",
    "unknown",
)
```

- [x] **Step 4: Extend the `MAX_VALUE_BASES` docstring comment in `fbar_schema.py`**

Replace the comment block above `MAX_VALUE_BASES` with:

```python
#: How the reported maximum was established. Drives V12: a reportable account
#: whose basis is weaker than a full year of statements, and which has NOT
#: elected the Item 15a "amount unknown" checkbox, is asserting a maximum it
#: cannot support.
#:
#: Two members describe RECONSTRUCTIONS rather than observations, and both were
#: added because the ad hoc alternative was recording exact work as `estimate`:
#:
#:   * `anchored_full_year` — the opening comes from a dated, cited anchor (a
#:     prior-year filed figure, a declaration, or a live balance carried back to
#:     the year boundary) and a COMPLETE transaction ledger is then replayed.
#:     This is exact when both halves hold, which is why it requires
#:     `max_value_anchor` and why V12 checks that citation rather than trusting
#:     the word. Without that requirement it would be a value anyone can type to
#:     silence V12 — precisely the hole `statement_parser.derive_basis` exists
#:     to close.
#:   * `non_negativity_floor` — the opening is unknown, but the account cannot
#:     go negative, so the minimum opening consistent with the ledger yields a
#:     PROVABLE floor on the true maximum. V12 still warns: a floor may
#:     understate. It warns differently, because a bound with a known
#:     relationship to the truth is not a guess.
```

- [x] **Step 5: Run and commit**

Run: `python -m pytest tests/test_statement_readers.py tests/test_statement_basis.py tests/test_fbar_validators.py -q`
Expected: PASS

```bash
git add scripts/fbar_schema.py scripts/statement_parser.py tests/test_statement_readers.py
git commit -m "Give the basis vocabulary a word for an anchored replay and a proven floor"
```

---

### Task 2.2: Require an anchor's citation on the account schema (F2, part 2 of 3)

Per **D1**, this is what stops `anchored_full_year` becoming a free pass. The
basis claims an opening came from somewhere; the schema makes it say where.

**Files:**
- Modify: `scripts/fbar_schema.py` — `ACCOUNT_FIELDS`, new `ANCHOR_FIELDS` + `ANCHOR_SOURCE_KINDS`, `_validate_anchor`, call site in `_validate_account`
- Test: `tests/test_fbar_validators.py`

**Interfaces:**
- Consumes: `MAX_VALUE_BASES` from Task 2.1
- Produces: `account["max_value_anchor"]` — either `None` or a dict with keys
  `as_of` (`date`), `amount` (`float`), `currency` (`str`, upper), `source_document`
  (`str`), `source_kind` (one of `ANCHOR_SOURCE_KINDS`), `note` (`str` or `None`).
  Task 2.3 (V12) and Task 3.5 both read this shape.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_fbar_validators.py`:

```python
def test_anchored_basis_requires_an_anchor():
    """The basis asserts an opening came from somewhere. Say where.

    Without this, `anchored_full_year` is a word that silences V12 — which is
    the exact hole derive_basis was built to close.
    """
    with pytest.raises(RosterError) as exc:
        roster([account("a", max_value_basis="anchored_full_year")])
    assert "max_value_anchor" in str(exc.value)


def test_anchor_requires_every_field():
    for missing in ("as_of", "amount", "currency", "source_document", "source_kind"):
        anchor = {"as_of": "2025-01-01", "amount": 1000.0, "currency": "RUB",
                  "source_document": "prior/form_114_2024.pdf",
                  "source_kind": "prior_year_filing"}
        del anchor[missing]
        with pytest.raises(RosterError) as exc:
            roster([account("a", max_value_basis="anchored_full_year",
                            max_value_anchor=anchor)])
        assert missing in str(exc.value)


def test_anchor_source_kind_is_an_enum():
    with pytest.raises(RosterError) as exc:
        roster([account("a", max_value_basis="anchored_full_year",
                        max_value_anchor={"as_of": "2025-01-01", "amount": 1000.0,
                                          "currency": "RUB",
                                          "source_document": "x.pdf",
                                          "source_kind": "vibes"})])
    assert "vibes" in str(exc.value)


def test_an_anchor_on_a_non_anchored_basis_is_kept_not_rejected():
    """A recorded anchor is evidence even where the basis is weaker.

    An account may have an anchor and still be capped at `statement_partial`
    by a coverage gap. Dropping the anchor there would discard the citation
    that explains a figure.
    """
    r = roster([account("a", max_value_basis="statement_partial",
                        max_value_anchor={"as_of": "2025-01-01", "amount": 1000.0,
                                          "currency": "RUB",
                                          "source_document": "x.pdf",
                                          "source_kind": "declaration"})])
    assert r["accounts"][0]["max_value_anchor"]["currency"] == "RUB"


def test_anchor_as_of_must_be_a_date():
    with pytest.raises(RosterError):
        roster([account("a", max_value_basis="anchored_full_year",
                        max_value_anchor={"as_of": "January 1", "amount": 1.0,
                                          "currency": "RUB", "source_document": "x.pdf",
                                          "source_kind": "declaration"})])
```

- [x] **Step 2: Run and confirm they fail**

Run: `python -m pytest tests/test_fbar_validators.py -k "anchor" -v`
Expected: 5 FAILED — the first with `Unknown key(s) in roster.accounts[0]: 'max_value_anchor'`, which is the unknown-key rejection working correctly.

- [x] **Step 3: Add the field, the enum and the validator**

In `scripts/fbar_schema.py`, after `PRIOR_YEAR_SOURCES`:

```python
#: Where an `anchored_full_year` opening balance came from. Each member names a
#: DOCUMENT the filer can produce, which is the point: an anchor is a citation,
#: not a recollection.
ANCHOR_SOURCE_KINDS = (
    "prior_year_filing",      # the filed Form 114's own Item 15 for this account
    "declaration",            # a foreign-regime asset declaration stating a dated balance
    "point_balance_document",  # a bank-issued "balance as of <date>" letter
    "statement_closing",      # a statement's own stated closing balance
)

ANCHOR_FIELDS: Dict[str, Any] = {
    "as_of": None,            # REQUIRED — the date the anchor balance is stated for
    "amount": None,           # REQUIRED — in `currency`, the institution's own figure
    "currency": None,         # REQUIRED — ISO-4217
    "source_document": None,  # REQUIRED — workspace-relative path; V13 checks it exists
    "source_kind": None,      # REQUIRED — ANCHOR_SOURCE_KINDS
    "note": None,             # how the anchor was carried to the year boundary, if it was
}

_ANCHOR_REQUIRED = ("as_of", "amount", "currency", "source_document", "source_kind")
```

Add to `ACCOUNT_FIELDS`, immediately after `"max_value_basis"`:

```python
    "max_value_anchor": None,            # REQUIRED when max_value_basis is anchored_full_year
```

Add the section validator beside `_validate_institution`:

```python
def _validate_anchor(raw: Any, where: str) -> Optional[Dict[str, Any]]:
    """The citation behind an `anchored_full_year` opening balance.

    An anchored basis claims a replay was exact because the opening came from
    somewhere real. This is where it says where. V12 goes quiet on an anchored
    basis ONLY when this validates and V13 finds the cited document — otherwise
    the basis is a word that silences a validator, which is the failure mode
    `statement_parser.derive_basis` exists to prevent.
    """
    if raw in (None, {}):
        return None
    if not isinstance(raw, dict):
        raise RosterError(f"{where} must be an object.")
    _reject_unknown(raw, ANCHOR_FIELDS, where)
    anchor = {k: raw.get(k, v) for k, v in ANCHOR_FIELDS.items()}
    for key in _ANCHOR_REQUIRED:
        if anchor[key] in (None, ""):
            raise RosterError(
                f"{where}.{key} is required. An anchor without it cannot be "
                "traced back to the document it came from, which is the only "
                "thing that distinguishes an anchored replay from an assertion."
            )
    anchor["as_of"] = parse_date(anchor["as_of"], f"{where}.as_of")
    anchor["amount"] = _number(anchor["amount"], f"{where}.amount")
    anchor["currency"] = str(anchor["currency"]).strip().upper()
    anchor["source_document"] = str(anchor["source_document"])
    _require_enum(anchor["source_kind"], ANCHOR_SOURCE_KINDS, f"{where}.source_kind")
    return anchor
```

In `_validate_account`, after the `max_value_basis` enum check:

```python
    acct["max_value_anchor"] = _validate_anchor(
        acct["max_value_anchor"], f"{where}.max_value_anchor")
    if acct["max_value_basis"] == "anchored_full_year" and not acct["max_value_anchor"]:
        raise RosterError(
            f"{where}.max_value_basis is 'anchored_full_year' but no "
            "max_value_anchor is recorded. That basis asserts the opening "
            "balance came from a dated, cited document; without the citation "
            "it is indistinguishable from a hand-typed 'statement_full_year' "
            "and would silence V12 on no evidence at all."
        )
```

- [x] **Step 4: Run and confirm they pass**

Run: `python -m pytest tests/test_fbar_validators.py -q`
Expected: PASS

- [x] **Step 5: Extend V13 to cover the anchor's document**

The anchor's `source_document` must be checked for existence exactly like
`source_documents[]`. In `v13_source_documents`, replace the `missing`
computation:

```python
    for acct in roster["accounts"]:
        cited = list(acct["source_documents"])
        anchor = acct.get("max_value_anchor")
        if anchor and anchor.get("source_document"):
            cited.append(anchor["source_document"])
        missing = [p for p in cited
                   if not os.path.exists(os.path.join(workspace, p))]
```

Add the test:

```python
def test_v13_covers_the_anchor_document(tmp_path):
    """An anchor citing a file that is not there is the same defect as any
    other dangling citation — and a worse one, since V12 trusts it."""
    r = roster([account("a", max_value_basis="anchored_full_year",
                        max_value_anchor={"as_of": "2025-01-01", "amount": 1.0,
                                          "currency": "RUB",
                                          "source_document": "nowhere.pdf",
                                          "source_kind": "declaration"})])
    ids = [f.id for f in run(r, workspace=str(tmp_path))["findings"]]
    assert "V13" in ids
```

- [x] **Step 6: Run and commit**

Run: `python -m pytest tests/ -q`
Expected: PASS

```bash
git add scripts/fbar_schema.py scripts/fbar_reconciler.py tests/test_fbar_validators.py
git commit -m "Make an anchored basis carry the citation it claims to rest on"
```

---

### Task 2.3: Teach V12 the two new bases (F2, part 3 of 3)

V12 currently tells every non-`statement_full_year` account to "obtain records
that cover the year, or elect Item 15a." For an anchored replay that is wrong
advice — the records *do* cover the year. For a floor it is right advice in the
wrong words.

**Files:**
- Modify: `scripts/fbar_reconciler.py:955-980` (`v12_max_value_basis`)
- Modify: `references/fbar_guide.md` §7 — the V12 row
- Test: `tests/test_fbar_validators.py`

**Interfaces:**
- Consumes: `max_value_anchor` (Task 2.2), the two basis strings (Task 2.1)
- Produces: no new symbols; V12's `detail` gains `"anchor_verified"` (bool) on anchored accounts

`v12_max_value_basis` takes only `roster` today. It needs `workspace` to check
the anchor's document, so its signature changes — update the call in
`validate()` in the same step.

- [x] **Step 1: Write the failing tests**

```python
def _anchor(**overrides):
    base = {"as_of": "2025-01-01", "amount": 1000.0, "currency": "RUB",
            "source_document": "anchor.pdf", "source_kind": "prior_year_filing"}
    base.update(overrides)
    return base


def test_v12_is_quiet_on_a_verified_anchored_replay(tmp_path):
    """The records DO cover the year — via a cited opening, not a balance column.

    Telling this filer to obtain year-covering records or elect Item 15a is
    advice for a problem they do not have.
    """
    (tmp_path / "anchor.pdf").write_bytes(b"%PDF-synthetic")
    r = roster([account("a", max_value_basis="anchored_full_year",
                        max_value_anchor=_anchor())])
    v12 = [f for f in run(r, workspace=str(tmp_path))["findings"] if f.id == "V12"]
    assert v12 == []


def test_v12_still_warns_when_the_anchor_document_is_missing(tmp_path):
    """An unverifiable anchor is an assertion. V12 must not take its word."""
    r = roster([account("a", max_value_basis="anchored_full_year",
                        max_value_anchor=_anchor(source_document="gone.pdf"))])
    v12 = [f for f in run(r, workspace=str(tmp_path))["findings"] if f.id == "V12"]
    assert len(v12) == 1
    assert "cannot be verified" in v12[0].message
    assert v12[0].detail["anchor_verified"] is False


def test_v12_warns_on_an_anchored_basis_with_no_workspace_to_check():
    """No workspace means no verification, so the claim stays unproven."""
    r = roster([account("a", max_value_basis="anchored_full_year",
                        max_value_anchor=_anchor())])
    v12 = [f for f in run(r)["findings"] if f.id == "V12"]
    assert len(v12) == 1


def test_v12_uses_bound_language_for_a_floor():
    """A proven floor is not a guess, and the message must not call it one."""
    r = roster([account("a", max_value_basis="non_negativity_floor")])
    v12 = [f for f in run(r)["findings"] if f.id == "V12"]
    assert len(v12) == 1
    assert "proven floor" in v12[0].message
    assert "may understate" in v12[0].message
    assert "estimate" not in v12[0].message


def test_v12_language_for_a_real_estimate_is_unchanged():
    r = roster([account("a", max_value_basis="estimate")])
    v12 = [f for f in run(r)["findings"] if f.id == "V12"]
    assert "obtain records that cover the year" in v12[0].message
```

- [x] **Step 2: Run and confirm they fail**

Run: `python -m pytest tests/test_fbar_validators.py -k "v12" -v`
Expected: 4 FAILED (the last passes already — it is the regression guard)

- [x] **Step 3: Rewrite V12**

```python
def v12_max_value_basis(roster: Dict[str, Any],
                        workspace: Optional[str] = None) -> List[Finding]:
    """A stated maximum should rest on evidence that could show the maximum.

    Period-end summaries cannot: an account can peak and fall back between two
    statement dates and show nothing. Asserting a determined maximum on that
    basis, without electing Item 15a, claims more certainty than the records
    support.

    Two bases are reconstructions rather than observations and are handled on
    their own terms:

      * `anchored_full_year` is EXACT — a cited dated opening replayed across a
        complete ledger — so the safe-harbour nudge is wrong advice, but only
        once the citation is verifiable. An anchor whose document is not in the
        workspace (or with no workspace to look in) is an assertion, and this
        validator's whole purpose is to not take an assertion's word for it.
      * `non_negativity_floor` still warrants a warning — a floor may
        understate — but calling it an estimate misdescribes it. It is a proven
        bound with a known direction of error, and filing it is defensible.
    """
    out = []
    for acct in roster["accounts"]:
        if not acct["reportable"] or acct["max_value_unknown"]:
            continue
        basis = acct["max_value_basis"]
        if basis == "statement_full_year":
            continue

        if basis == "anchored_full_year":
            anchor = acct.get("max_value_anchor") or {}
            cited = anchor.get("source_document")
            verified = bool(
                workspace and cited
                and os.path.exists(os.path.join(workspace, cited)))
            if verified:
                continue
            out.append(Finding(
                id="V12", severity="warn", account_key=acct["account_key"],
                message=(
                    f"{acct['account_key']!r} reports an 'anchored_full_year' maximum, but "
                    f"the anchor cannot be verified: its cited document "
                    f"{cited!r} is not present"
                    + (f" under {workspace}" if workspace else " (no workspace was given to check)")
                    + ". An anchored replay is exact only if the opening it started from is "
                    "real; until the citation resolves, this is an assertion that would "
                    "otherwise silence this check."),
                detail={"max_value_basis": basis, "anchor_verified": False,
                        "source_document": cited},
            ))
            continue

        if basis == "non_negativity_floor":
            out.append(Finding(
                id="V12", severity="warn", account_key=acct["account_key"],
                message=(
                    f"{acct['account_key']!r} reports a proven floor on its maximum: the "
                    "opening balance is unknown, and the figure is the smallest maximum "
                    "consistent with the account never going negative. This is a rigorous "
                    "bound, not an estimate — but it MAY UNDERSTATE the true maximum, so "
                    "either obtain the opening balance, or record in the account notes that "
                    "the filed figure is a floor and why the opening could not be obtained."),
                detail={"max_value_basis": basis},
            ))
            continue

        out.append(Finding(
            id="V12", severity="warn", account_key=acct["account_key"],
            message=(
                f"{acct['account_key']!r} reports a determined maximum on a "
                f"{basis!r} basis, without electing Item 15a. FinCEN "
                "permits reliance on periodic statements only where they FAIRLY REFLECT "
                "the maximum during the year. Either obtain records that cover the year, "
                "or elect Item 15a and note why."),
            detail={"max_value_basis": basis},
        ))
    return out
```

- [x] **Step 4: Update the call site**

In `validate()`, change `findings += v12_max_value_basis(roster)` to:

```python
    findings += v12_max_value_basis(roster, workspace)
```

- [x] **Step 5: Update the guide's V12 row**

In `references/fbar_guide.md` §7, replace the V12 row:

```markdown
| V12 | Reportable account asserting a determined maximum on a basis weaker than full-year statements, without electing Item 15a. Quiet on `anchored_full_year` **only** when the anchor's cited document is present in the workspace; distinct wording for `non_negativity_floor`, which is a proven bound rather than an estimate | warn |
```

- [x] **Step 6: Run and commit**

Run: `python -m pytest tests/ -q`
Expected: PASS

```bash
git add scripts/fbar_reconciler.py references/fbar_guide.md tests/test_fbar_validators.py
git commit -m "Stop V12 giving safe-harbour advice to an exact anchored replay"
```

---

### Task 2.4: Carry the contract number alongside the account number (F7)

T-Bank issues two structurally different identifiers per bank product — a
contract number (`Номер договора`) and a ledger number (`Номер лицевого счёта`)
— printed together on every statement. `MovementRegisterProfile` already parses
both (`contract_line`, `account_line`), so Phase 0 knows the difference; the
roster has nowhere to put it, and conflating the two cost real time twice in one
session.

**Files:**
- Modify: `scripts/fbar_schema.py` — `ACCOUNT_FIELDS`, normalization in `_validate_account`
- Modify: `scripts/fbar_reconciler.py` — `match_accounts`, new match reason
- Modify: `references/fbar_guide.md` §3 (roster fields) and §4 (matching order)
- Test: `tests/test_fbar_normalization.py`, `tests/test_fbar_reconciler.py`

**Interfaces:**
- Consumes: nothing
- Produces: `account["contract_number"]` (`str` or `None`),
  `account["contract_number_normalized"]` (derived, matching-only), and the
  match reason constant `MATCH_CONTRACT_NUMBER = "contract_number"`

A contract number is often the **more stable cross-year join key** — a ledger
number is renumbered on product migration while the contract survives — which
is exactly the property `account_key` already prizes. It therefore belongs in
the matching ladder, not merely in the record.

- [x] **Step 1: Write the failing tests**

In `tests/test_fbar_normalization.py`:

```python
def test_contract_number_is_normalized_for_matching_only():
    from fbar_schema import validate_roster
    r = validate_roster({
        "schema_version": "1.0", "tax_year": 2025, "filer": {"slug": "t"},
        "accounts": [{"account_key": "a", "account_number": "40817 810 0000 0012 3474",
                      "account_type": "bank", "contract_number": "5000-000-001"}],
    }, origin="<test>")
    acct = r["accounts"][0]
    assert acct["contract_number"] == "5000-000-001"          # as the institution states it
    assert acct["contract_number_normalized"] == "5000000001"  # matching key only
```

In `tests/test_fbar_reconciler.py`:

```python
def test_a_renumbered_ledger_matches_on_the_contract_number():
    """A bank migrates a product and issues a new ledger number; the contract
    number survives. Without it this is one closed plus one new account."""
    import fbar_reconciler as fr
    prior = roster([account("old", account_number="40817810000000123474",
                            contract_number="5000000001")], year=2024)
    current = roster([account("new", account_number="40817810900000112233",
                              contract_number="5000000001")])
    matches = fr.match_accounts(current, prior)["matches"]
    assert matches["old"]["current_keys"] == ["new"]
    assert matches["old"]["method"] == fr.MATCH_CONTRACT_NUMBER


def test_a_contract_number_never_matches_across_institutions():
    """Contract numbers are institution-scoped; two banks may reuse a string."""
    import fbar_reconciler as fr
    prior = roster([account("old", contract_number="1234567890",
                            institution={"name": "Bank One", "country": "GE",
                                         "address": {"street": "s", "city": "c",
                                                     "postal_code": "p"}})], year=2024)
    current = roster([account("new", contract_number="1234567890",
                              institution={"name": "Bank Two", "country": "GE",
                                           "address": {"street": "s", "city": "c",
                                                       "postal_code": "p"}})])
    assert fr.match_accounts(current, prior)["matches"]["old"]["current_keys"] != ["new"]
```

- [x] **Step 2: Run and confirm they fail**

Run: `python -m pytest tests/test_fbar_normalization.py tests/test_fbar_reconciler.py -k "contract" -v`
Expected: 3 FAILED, the first with `Unknown key(s) ... 'contract_number'`

- [x] **Step 3: Add the schema fields**

In `ACCOUNT_FIELDS`, immediately after `"account_number_normalized"`:

```python
    "contract_number": None,             # the institution's OTHER identifier, where it issues one
    "contract_number_normalized": None,  # derived; matching only, never displayed or filed
```

In `_validate_account`, beside the existing `account_number` handling:

```python
    # A contract number is not an alias of the ledger number — it is a second,
    # structurally different identifier the institution issues alongside it
    # (T-Bank prints «Номер договора» and «Номер лицевого счёта» on every
    # statement). It is frequently the MORE STABLE cross-year key, because a
    # ledger number is reissued on product migration while the contract is not.
    # Brokerage products use one number for both roles; leave this absent there
    # rather than duplicating the account number into it, so "absent" keeps
    # meaning "the institution issues only one".
    if acct["contract_number"] not in (None, ""):
        acct["contract_number"] = str(acct["contract_number"])
        if not acct["contract_number_normalized"]:
            acct["contract_number_normalized"] = normalize_account_number(
                acct["contract_number"])
    else:
        acct["contract_number"] = None
        acct["contract_number_normalized"] = None
```

- [x] **Step 4: Add the matching rung**

In `scripts/fbar_reconciler.py`, beside the other match-reason constants:

```python
MATCH_CONTRACT_NUMBER = "contract_number"
```

Each rung in `match_accounts` is a **full pass over the prior accounts** before
the next one runs — the docstring explains why, and a per-account ladder would
let a weaker rung consume a current account a stronger one was going to claim.
So this is a new pass, inserted **after rung 2 (exact `account_key`) and before
the combined rungs 3-4**: a surviving contract number is stronger evidence of
identity than a ledger number that may have been reissued, but weaker than the
filer's own declaration or an unchanged key.

Renumber the comments on the rungs below it, and update the ladder list in
`match_accounts`' docstring to seven rungs.

```python
    # Rung 3 — a contract number that survived a ledger renumbering.
    #
    # Institution-scoped, like every other number-based rung: two banks may
    # issue the same digits, and matching across institutions would merge
    # unrelated accounts. A false merge HIDES an account; a false split only
    # produces a reviewable finding, which is why the asymmetry always
    # resolves this way here.
    for pa in prior_accounts:
        pkey = pa["account_key"]
        if pkey in matches:
            continue
        p_contract = pa.get("contract_number_normalized")
        if not p_contract:
            continue
        hit = next(
            (ca for ca in current_accounts
             if ca["account_key"] not in matched_current_keys
             and ca.get("contract_number_normalized") == p_contract
             and _institution_key(ca) == _institution_key(pa)),
            None,
        )
        if hit is None:
            continue
        matches[pkey] = {"current_keys": [hit["account_key"]],
                         "method": MATCH_CONTRACT_NUMBER}
        matched_current_keys.add(hit["account_key"])
```

- [x] **Step 5: Document it**

Add to §3 of `references/fbar_guide.md`, in the roster-fields discussion:

```markdown
**`contract_number`** — a second identifier the institution issues *alongside*
the account number, where it issues one. T-Bank prints «Номер договора» and
«Номер лицевого счёта» on every bank statement; they are different numbers for
the same account, not a renumbering. Record both. The contract number is
usually the more stable cross-year join key, because a ledger number is reissued
on product migration and a contract number is not. Brokerage and IIS products
use one number for both roles — leave `contract_number` absent there, so
"absent" keeps meaning "the institution issues only one" rather than "nobody
filled it in".
```

Add to §4's matching-order list, above the normalized-account-number rung:

```markdown
4. **Contract number**, normalized, within the same institution. Scoped by
   institution because two banks may issue the same digits, and a false merge
   hides an account while a false split only produces a reviewable finding.
```

Renumber the rungs below it.

- [x] **Step 6: Run and commit**

Run: `python -m pytest tests/ -q`
Expected: PASS

```bash
git add scripts/fbar_schema.py scripts/fbar_reconciler.py references/fbar_guide.md tests/test_fbar_normalization.py tests/test_fbar_reconciler.py
git commit -m "Tell an institution's contract number apart from its ledger number"
```

---

### Task 2.5: V14 — a figure with no source that tracks last year's filed number (F5)

Four brokerage accounts carried "2025 max" figures in a workbook for months,
presented with no caveat. No source document existed for any of them, and each
sat within 0.2%–51% of the *prior year's filed* maximum — the signature of a
carried-forward placeholder. Nothing in V1–V13 would have caught it; it
surfaced only because every dollar figure was traced back to a cited file by
hand.

**Files:**
- Modify: `scripts/fbar_reconciler.py` — new `v14_unsourced_figure_tracks_prior_year`, call in `validate()`
- Modify: `references/fbar_guide.md` §7 — new V14 row
- Test: `tests/test_fbar_validators.py`

**Interfaces:**
- Consumes: `prior_to_current()` (existing), `max_value_anchor` (Task 2.2)
- Produces: `v14_unsourced_figure_tracks_prior_year(current, prior, resolved=None, tolerance=0.15) -> List[Finding]`

**Why a new validator rather than extending V13.** The proposal asks the
question. V13 takes `(roster, workspace, fbar)` and checks file existence for a
single roster; this check needs the *prior* roster and the matching ladder to
find the same account across a renumbering. Bolting a second, differently-shaped
condition onto V13 would mean V13 fires for two unrelated reasons under one id,
and the guide's severity table could no longer describe it in one line.

**Deliberate narrowing from the proposal.** The proposal's condition is "no
`source_documents`, **or every cited source predates the current tax year**".
The second half is not implemented, on purpose: a `source_documents` entry is a
path, and a path is not a date. Inferring a year from a filename is exactly the
filename-sniffing `statement_parser` refuses everywhere else, and it would
misfire on any workspace whose folders are named by filing year rather than tax
year. **The condition implemented is "cites nothing at all"** — which is the
case that actually occurred. Record this narrowing in the docstring so the next
reader does not think it was forgotten.

- [x] **Step 1: Write the failing tests**

```python
def test_v14_flags_an_uncited_figure_that_tracks_last_year():
    """The signature of a carried-forward placeholder: no source, same number."""
    prior = roster([account("a", max_value_usd=12000.0, max_value_local=12000.0)],
                   year=2024)
    current = roster([account("a", max_value_usd=12050.0, max_value_local=12050.0,
                              source_documents=[])])
    v14 = [f for f in run(current, prior)["findings"] if f.id == "V14"]
    assert len(v14) == 1
    assert "no current-year source" in v14[0].message
    assert v14[0].detail["delta_pct"] == pytest.approx(0.42, abs=0.01)


def test_v14_is_quiet_when_a_source_is_cited():
    prior = roster([account("a", max_value_usd=12000.0, max_value_local=12000.0)],
                   year=2024)
    current = roster([account("a", max_value_usd=12050.0, max_value_local=12050.0,
                              source_documents=["statements/2025_q4.xlsx"])])
    assert [f for f in run(current, prior)["findings"] if f.id == "V14"] == []


def test_v14_is_quiet_when_the_figure_moved():
    """A figure that moved 60% was not copied forward, cited or not."""
    prior = roster([account("a", max_value_usd=12000.0, max_value_local=12000.0)],
                   year=2024)
    current = roster([account("a", max_value_usd=30000.0, max_value_local=30000.0,
                              source_documents=[])])
    assert [f for f in run(current, prior)["findings"] if f.id == "V14"] == []


def test_v14_is_quiet_on_an_admitted_estimate():
    """`estimate` and `unknown` already say the evidence is weak. V14 exists for
    figures presented WITHOUT a caveat."""
    prior = roster([account("a", max_value_usd=12000.0, max_value_local=12000.0)],
                   year=2024)
    current = roster([account("a", max_value_usd=12050.0, max_value_local=12050.0,
                              max_value_basis="estimate", source_documents=[])])
    assert [f for f in run(current, prior)["findings"] if f.id == "V14"] == []


def test_v14_accepts_a_verified_anchor_as_a_citation(tmp_path):
    """An anchor IS a cited source; it just lives in its own field."""
    (tmp_path / "anchor.pdf").write_bytes(b"%PDF-synthetic")
    prior = roster([account("a", max_value_usd=12000.0, max_value_local=12000.0)],
                   year=2024)
    current = roster([account("a", max_value_usd=12050.0, max_value_local=12050.0,
                              max_value_basis="anchored_full_year",
                              source_documents=[],
                              max_value_anchor={"as_of": "2025-01-01", "amount": 1.0,
                                                "currency": "USD",
                                                "source_document": "anchor.pdf",
                                                "source_kind": "prior_year_filing"})])
    findings = run(current, prior, workspace=str(tmp_path))["findings"]
    assert [f for f in findings if f.id == "V14"] == []


def test_v14_follows_a_renumbering():
    """The accounts whose figures were copied forward are often the ones that
    got a new number, so V14 must use the same matching ladder as everything else."""
    prior = roster([account("old", account_number="1111", contract_number="C-1",
                            max_value_usd=12000.0, max_value_local=12000.0)], year=2024)
    current = roster([account("new", account_number="2222", contract_number="C-1",
                              max_value_usd=12050.0, max_value_local=12050.0,
                              source_documents=[])])
    v14 = [f for f in run(current, prior)["findings"] if f.id == "V14"]
    assert len(v14) == 1
    assert v14[0].account_key == "new"


def test_v14_needs_a_prior_year():
    current = roster([account("a", source_documents=[])])
    assert [f for f in run(current)["findings"] if f.id == "V14"] == []
```

- [x] **Step 2: Run and confirm they fail**

Run: `python -m pytest tests/test_fbar_validators.py -k "v14" -v`
Expected: 6 FAILED, 1 PASSED (`test_v14_needs_a_prior_year` passes trivially)

- [x] **Step 3: Implement V14**

Add after `v13_source_documents`:

```python
#: How close a current figure has to sit to the prior year's filed figure
#: before an absent citation looks like a carry-forward rather than a
#: coincidence. 15% is wide on purpose: the observed defects ranged from 0.2%
#: to 51%, and the cheap direction to be wrong in is a warning nobody needed.
V14_TRACKING_TOLERANCE = 0.15


def v14_unsourced_figure_tracks_prior_year(
        current: Dict[str, Any],
        prior: Optional[Dict[str, Any]],
        resolved: Optional[Dict[str, Dict[str, Any]]] = None,
        tolerance: float = V14_TRACKING_TOLERANCE) -> List[Finding]:
    """A confident figure, no source, and last year's number. That is a copy.

    Four accounts in one live cycle carried a stated current-year maximum with
    no caveat, no source document anywhere, and a figure sitting between 0.2%
    and 51% of the prior year's FILED maximum. Each had been carried forward.
    V13 could not see it — every account cited nothing, so nothing was missing —
    and V12 could not either, because the basis had been typed as though the
    evidence existed.

    Two conditions, both required:

      * the account cites NO source at all — neither `source_documents[]` nor a
        `max_value_anchor`; and
      * the stated maximum sits within `tolerance` of the prior year's.

    **Narrower than the original proposal, deliberately.** That version also
    fired when every cited source predated the tax year. A `source_documents`
    entry is a PATH, and a path is not a date; deriving a year from a filename
    is the filename-sniffing this module refuses everywhere else, and it would
    misfire on any workspace foldered by filing year. "Cites nothing" is the
    case that actually occurred and the only one that is mechanically decidable.

    `estimate` and `unknown` are exempt: they have already said the evidence is
    weak, and this validator exists for figures presented without a caveat.
    """
    if prior is None:
        return []
    if resolved is None:
        resolved = prior_to_current(current, prior)

    out = []
    for pa in prior["accounts"]:
        ca = resolved.get(pa["account_key"])
        if ca is None or not ca["reportable"] or ca["max_value_unknown"]:
            continue
        if ca["max_value_basis"] in ("estimate", "unknown"):
            continue
        if ca["source_documents"] or ca.get("max_value_anchor"):
            continue
        current_max, prior_max = ca["max_value_usd"], pa["max_value_usd"]
        if current_max is None or not prior_max:
            continue

        delta_pct = abs(current_max - prior_max) / abs(prior_max) * 100.0
        if delta_pct > tolerance * 100.0:
            continue
        out.append(Finding(
            id="V14", severity="warn", account_key=ca["account_key"],
            message=(
                f"{ca['account_key']!r} states a ${current_max:,.2f} maximum on a "
                f"{ca['max_value_basis']!r} basis with no current-year source — no "
                f"source_documents and no max_value_anchor — and the figure tracks the "
                f"{prior['tax_year']} filed maximum of ${prior_max:,.2f} to within "
                f"{delta_pct:.2f}%. Verify it was independently derived this year and was "
                "not simply carried forward; if it was derived, cite the document it came "
                "from."),
            detail={"current_max_usd": current_max, "prior_max_usd": prior_max,
                    "delta_pct": round(delta_pct, 2),
                    "max_value_basis": ca["max_value_basis"]},
        ))
    return out
```

Wire it into `validate()`, after the V13 line:

```python
    findings += v14_unsourced_figure_tracks_prior_year(roster, prior, resolved)
```

- [x] **Step 4: Add the guide row**

`references/fbar_guide.md` §7, after the V13 row:

```markdown
| V14 | Reportable account citing no source at all (no `source_documents`, no `max_value_anchor`) whose maximum sits within 15% of the prior year's filed maximum — the signature of a carried-forward placeholder | warn |
```

- [x] **Step 5: Run and commit**

Run: `python -m pytest tests/ -q`
Expected: PASS — including `test_fbar_guide_validator_table_covers_every_implemented_validator`, which auto-discovers `v14_*` and requires the row added in step 4.

```bash
git add scripts/fbar_reconciler.py references/fbar_guide.md tests/test_fbar_validators.py
git commit -m "Catch a maximum that cites nothing and tracks last year's filing"
```

---

### Task 2.6: V15 — an account count that does not reconcile (F9)

A portfolio-value workbook's summary tab listed five brokerage-family accounts
where the roster had four. The fifth was real, open since 2022, on no prior
filing, and confirmed at $0 throughout. It surfaced only because someone
counted rows against memory.

**Files:**
- Modify: `scripts/fbar_schema.py` — `ROSTER_FIELDS`, `EXTERNAL_COUNT_FIELDS`, `_validate_external_count`
- Modify: `scripts/fbar_reconciler.py` — `v15_external_account_count`, call in `validate()`
- Modify: `references/fbar_guide.md` §3 and §7
- Test: `tests/test_fbar_validators.py`

**Interfaces:**
- Consumes: `_institution_key()` (existing)
- Produces: `roster["external_account_counts"]` — a list of
  `{institution, account_type, count, source_document, note}` — and
  `v15_external_account_count(roster) -> List[Finding]`

The count is declared on the roster rather than computed, so this ships without
waiting on Tranche 3. Task 3.5 makes `read_daily_value_series` emit the same
shape automatically; the validator does not change when it does.

- [x] **Step 1: Write the failing tests**

```python
def _inst(name):
    return {"name": name, "country": "GE",
            "address": {"street": "s", "city": "c", "postal_code": "p"}}


def test_v15_flags_a_summary_listing_more_accounts_than_the_roster():
    r = roster([account("a", institution=_inst("T-Bank"), account_type="securities"),
                account("b", institution=_inst("T-Bank"), account_type="securities")],
               external_account_counts=[
                   {"institution": "T-Bank", "account_type": "securities", "count": 3,
                    "source_document": "portfolio_values.xlsx"}])
    v15 = [f for f in run(r)["findings"] if f.id == "V15"]
    assert len(v15) == 1
    assert "3" in v15[0].message and "2" in v15[0].message
    assert v15[0].detail["surplus"] == 1


def test_v15_is_quiet_when_the_counts_agree():
    r = roster([account("a", institution=_inst("T-Bank"), account_type="securities")],
               external_account_counts=[
                   {"institution": "T-Bank", "account_type": "securities", "count": 1,
                    "source_document": "portfolio_values.xlsx"}])
    assert [f for f in run(r)["findings"] if f.id == "V15"] == []


def test_v15_is_quiet_when_the_roster_has_more():
    """A roster with MORE accounts than one summary tab is normal — a summary
    covers the products it covers. Only a surplus on the document's side means
    an account nobody has rostered."""
    r = roster([account("a", institution=_inst("T-Bank"), account_type="securities"),
                account("b", institution=_inst("T-Bank"), account_type="securities")],
               external_account_counts=[
                   {"institution": "T-Bank", "account_type": "securities", "count": 1,
                    "source_document": "portfolio_values.xlsx"}])
    assert [f for f in run(r)["findings"] if f.id == "V15"] == []


def test_v15_counts_only_the_named_account_type_when_one_is_given():
    r = roster([account("a", institution=_inst("T-Bank"), account_type="securities"),
                account("b", institution=_inst("T-Bank"), account_type="bank")],
               external_account_counts=[
                   {"institution": "T-Bank", "account_type": "securities", "count": 2,
                    "source_document": "portfolio_values.xlsx"}])
    assert len([f for f in run(r)["findings"] if f.id == "V15"]) == 1


def test_v15_matches_the_institution_through_homoglyph_drift():
    """`_institution_key` folds Cyrillic/Latin confusables; a count keyed to
    'Т-Bank' with a Cyrillic Т must still find 'T-Bank'."""
    r = roster([account("a", institution=_inst("T-Bank"), account_type="securities")],
               external_account_counts=[
                   {"institution": "Т-Bank", "account_type": "securities", "count": 2,
                    "source_document": "portfolio_values.xlsx"}])
    assert len([f for f in run(r)["findings"] if f.id == "V15"]) == 1


def test_external_count_rejects_an_unknown_key():
    with pytest.raises(RosterError):
        roster([account("a")],
               external_account_counts=[{"institution": "T-Bank", "cnt": 2,
                                         "source_document": "x.xlsx"}])
```

- [x] **Step 2: Run and confirm they fail**

Run: `python -m pytest tests/test_fbar_validators.py -k "v15 or external_count" -v`
Expected: 6 FAILED, the first with `Unknown key(s) in roster: 'external_account_counts'`

- [x] **Step 3: Add the schema section**

In `scripts/fbar_schema.py`, beside `PRIOR_YEAR_FIELDS`:

```python
#: An account count stated by a document OUTSIDE the roster — a portfolio
#: summary tab, a bank's own account list. Recorded so a mismatch is mechanical
#: rather than a matter of someone remembering how many there should be. One
#: live cycle turned up a real, never-filed account this way, open since 2022
#: and $0 throughout, found only because a person counted rows.
EXTERNAL_COUNT_FIELDS: Dict[str, Any] = {
    "institution": None,      # REQUIRED — matched by the same fold as account numbers
    "account_type": None,     # optional; None counts every type at that institution
    "count": None,            # REQUIRED — what the document lists
    "source_document": None,  # REQUIRED — workspace-relative path to the document
    "note": None,
}
```

Add `"external_account_counts": []` to `ROSTER_FIELDS`, and a
`_validate_external_count(raw, index)` following the shape of
`_validate_fx_rate`: reject unknown keys, require `institution`, `count` and
`source_document`, coerce `count` to a non-negative `int`, and enum-check
`account_type` against `ACCOUNT_TYPES` when it is not `None`. Wire it into the
roster loader beside the `fx_rates` loop.

- [x] **Step 4: Implement V15**

```python
def v15_external_account_count(roster: Dict[str, Any]) -> List[Finding]:
    """A document that lists more accounts than the roster has.

    Surplus in one direction only. A roster holding MORE accounts than a
    summary tab is ordinary — a summary covers the products it covers, and the
    filer may hold others elsewhere. A summary holding more than the roster
    means an account exists that nobody has rostered, which is the one failure
    this module cannot otherwise detect for itself.

    It does not try to say WHICH account is missing; naming the surplus is
    enough to prompt the manual follow-up, and guessing would be worse than the
    count.
    """
    out = []
    for entry in roster.get("external_account_counts", []):
        wanted_type = entry["account_type"]
        key = normalize_account_number(entry["institution"])
        rostered = [a for a in roster["accounts"]
                    if _institution_key(a) == key
                    and (wanted_type is None or a["account_type"] == wanted_type)]
        surplus = entry["count"] - len(rostered)
        if surplus <= 0:
            continue
        scope = f"{wanted_type} " if wanted_type else ""
        out.append(Finding(
            id="V15", severity="warn",
            message=(
                f"{entry['source_document']} lists {entry['count']} {scope}account(s) at "
                f"{entry['institution']!r}; the roster has {len(rostered)}. "
                f"{surplus} account(s) appear in the document and on no roster entry. "
                "Identify each one before filing — an account with no evidence and an "
                "account nobody rostered look identical from here, and only one of them "
                "is safe."),
            detail={"institution": entry["institution"], "account_type": wanted_type,
                    "document_count": entry["count"], "roster_count": len(rostered),
                    "surplus": surplus, "source_document": entry["source_document"]},
        ))
    return out
```

Wire into `validate()` after V14. Add the guide row to §7:

```markdown
| V15 | A document outside the roster (`external_account_counts[]`) lists more accounts at an institution than the roster carries | warn |
```

And document the field in §3 alongside the other roster-level sections.

- [x] **Step 5: Run and commit**

Run: `python -m pytest tests/ -q`
Expected: PASS

```bash
git add scripts/fbar_schema.py scripts/fbar_reconciler.py references/fbar_guide.md tests/test_fbar_validators.py
git commit -m "Reconcile a summary document's account count against the roster"
```

---

### Task 2.7: V16 — an account number that looks truncated beside its siblings (F10)

One account number was recorded with 19 digits in one document and 20 in
another. It was caught only because a third, independently sourced document was
consulted for an unrelated reason.

**Files:**
- Modify: `scripts/fbar_reconciler.py` — `v16_account_number_length`, call in `validate()`
- Modify: `references/fbar_guide.md` §7
- Test: `tests/test_fbar_validators.py`

**Interfaces:**
- Consumes: `normalize_account_number`, `_institution_key` (both existing)
- Produces: `v16_account_number_length(roster) -> List[Finding]`

**This does not go in `normalize_account_number`,** which the proposal
suggests. That function is a pure fold on the matching hot path, returns a bare
`str`, and its docstring forbids its output reaching display — it has no channel
to emit a warning, and giving it one would mean every match attempt could log.
The reconciler already has `Finding` as its output type. Per **D4**.

A hard length rule per country is also not what this does. Russian ledger
accounts are 20 digits, but the roster is not limited to Russia and hard-coding
a table of national formats is a maintenance burden with a false-positive
tail. **Comparison against siblings at the same institution needs no table**
and is what actually caught the defect.

- [x] **Step 1: Write the failing tests**

These reuse the `_inst(name)` helper added to `tests/test_fbar_validators.py`
in Task 2.6. Do Task 2.6 first, or add the helper here.

```python
def test_v16_flags_a_short_number_among_siblings():
    """Three 20-digit siblings and one 19-digit outlier is a dropped digit."""
    accounts = [account(k, institution=_inst("T-Bank"),
                        account_number=n)
                for k, n in [("a", "40817810000000123474"),
                             ("b", "40817810000000123475"),
                             ("c", "40817810000000123476"),
                             ("d", "4081781000000012347")]]
    v16 = [f for f in run(roster(accounts))["findings"] if f.id == "V16"]
    assert len(v16) == 1
    assert v16[0].account_key == "d"
    assert v16[0].detail == {"digits": 19, "sibling_digits": 20, "siblings": 3}


def test_v16_is_quiet_without_enough_siblings_to_compare():
    """Two accounts of different lengths do not establish which is the norm."""
    accounts = [account("a", institution=_inst("T-Bank"), account_number="40817810000000123474"),
                account("b", institution=_inst("T-Bank"), account_number="4081781000000012347")]
    assert [f for f in run(roster(accounts))["findings"] if f.id == "V16"] == []


def test_v16_is_quiet_when_every_sibling_agrees():
    accounts = [account(k, institution=_inst("T-Bank"), account_number=n)
                for k, n in [("a", "40817810000000123474"),
                             ("b", "40817810000000123475"),
                             ("c", "40817810000000123476")]]
    assert [f for f in run(roster(accounts))["findings"] if f.id == "V16"] == []


def test_v16_does_not_compare_across_institutions():
    """An IBAN and a Russian ledger number have no business being compared."""
    accounts = [account("a", institution=_inst("Bank One"), account_number="GE29TB1234567890123456"),
                account("b", institution=_inst("Bank One"), account_number="GE29TB1234567890123457"),
                account("c", institution=_inst("Bank One"), account_number="GE29TB1234567890123458"),
                account("d", institution=_inst("Bank Two"), account_number="1234")]
    assert [f for f in run(roster(accounts))["findings"] if f.id == "V16"] == []


def test_v16_needs_a_clear_majority_length():
    """Two 20s and two 19s name no norm; saying which is wrong would be a guess."""
    accounts = [account(k, institution=_inst("T-Bank"), account_number=n)
                for k, n in [("a", "40817810000000123474"),
                             ("b", "40817810000000123475"),
                             ("c", "4081781000000012347"),
                             ("d", "4081781000000012348")]]
    assert [f for f in run(roster(accounts))["findings"] if f.id == "V16"] == []
```

- [x] **Step 2: Run and confirm they fail**

Run: `python -m pytest tests/test_fbar_validators.py -k "v16" -v`
Expected: 2 FAILED (`flags_a_short_number`, `needs_a_clear_majority` — the rest pass vacuously with no V16 implemented, which is exactly why they are written as regression guards and must be re-run after step 3)

- [x] **Step 3: Implement V16**

```python
#: How many same-length siblings at one institution establish a norm to compare
#: against. Two agreeing numbers plus one outlier is the smallest set where
#: "the outlier is wrong" beats "the two are wrong", and a majority is required
#: on top of that so an even split names no norm.
V16_MIN_SIBLINGS = 2


def v16_account_number_length(roster: Dict[str, Any]) -> List[Finding]:
    """An account number whose digit count differs from its siblings'.

    A hand-transcribed account number lost a digit in one live cycle — 19
    digits where the institution issues 20 — and was caught only because an
    unrelated third document happened to be consulted. A byte-comparison cannot
    see it (the numbers genuinely differ) and `normalize_account_number` cannot
    report it (it is a pure fold with nowhere to put a warning, and its output
    must never be displayed).

    No table of national formats. Russian ledger accounts are 20 digits, but a
    roster is not limited to Russia, and a hard-coded format table would carry a
    permanent false-positive tail for every institution nobody thought of.
    Comparison against SIBLINGS at the same institution needs no table and is
    what actually caught the defect.

    Silent unless there is a clear majority to be an outlier from: an even split
    between two lengths names no norm, and picking one would be a guess dressed
    as a finding.
    """
    out = []
    by_institution: Dict[str, List[Dict[str, Any]]] = {}
    for acct in roster["accounts"]:
        by_institution.setdefault(_institution_key(acct), []).append(acct)

    for siblings in by_institution.values():
        lengths: Dict[str, int] = {}
        for acct in siblings:
            digits = sum(ch.isdigit() for ch
                         in normalize_account_number(acct["account_number"]))
            lengths[acct["account_key"]] = digits
        counts: Dict[int, int] = {}
        for digits in lengths.values():
            counts[digits] = counts.get(digits, 0) + 1

        modal, modal_count = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
        if modal_count < V16_MIN_SIBLINGS or modal_count * 2 <= len(siblings):
            continue

        for acct in siblings:
            digits = lengths[acct["account_key"]]
            if digits == modal:
                continue
            out.append(Finding(
                id="V16", severity="warn", account_key=acct["account_key"],
                message=(
                    f"{acct['account_key']!r} has a {digits}-digit account number where "
                    f"{modal_count} other account(s) at the same institution have {modal} "
                    "digits. A transcription that drops or gains a digit produces exactly "
                    "this shape, and no other check can see it — the numbers genuinely "
                    "differ, so matching cannot object. Verify the number against the "
                    "institution's own document, and prefer the contract number as the "
                    "cross-reference where one exists."),
                detail={"digits": digits, "sibling_digits": modal,
                        "siblings": modal_count},
            ))
    return out
```

Wire into `validate()` after V15, and add the guide row:

```markdown
| V16 | An account number whose digit count differs from a clear majority of its siblings at the same institution — the shape of a dropped or doubled digit | warn |
```

- [x] **Step 4: Run and commit**

Run: `python -m pytest tests/ -q`
Expected: PASS

```bash
git add scripts/fbar_reconciler.py references/fbar_guide.md tests/test_fbar_validators.py
git commit -m "Flag an account number that lost a digit against its siblings"
```

---

### Task 2.8: `assess_credit_account` — the mechanical half of the credit decision (F8)

Task 1.3 documented four checks. Two of them are arithmetic over a movement
register and should not be redone by hand; two are judgment and stay with
Phase 1. This implements the first two and says so.

**Files:**
- Modify: `scripts/fbar_reconciler.py` — `assess_credit_account`, `DEBTOR_SIGNATURE_PATTERNS`
- Test: `tests/test_fbar_validators.py`

**Interfaces:**
- Consumes: the movement dicts in a Phase 0 timeline JSON — each has at least
  `{"date": str, "description": str, "amount": float | None, "currency": str}`
- Produces:
  ```python
  assess_credit_account(movements: List[Dict[str, Any]]) -> Dict[str, Any]
  # {"net_change": Decimal, "direction": "toward_debt" | "toward_zero" | "flat",
  #  "debtor_signature_rows": [{"date": str, "description": str, "amount": Decimal}],
  #  "checks_performed": ["net_direction", "debtor_signature"],
  #  "checks_remaining": ["account_age", "parallel_declaration"]}
  ```

This is **not** a validator. It answers a question Phase 1 asks; it does not
run during `validate()` and emits no `Finding`. Wiring it into the validator
suite would make it fire on every account whether or not anyone was asking
about a credit product.

- [x] **Step 1: Write the failing tests**

`tests/test_fbar_validators.py` currently imports only `date` from `datetime`.
Add `from decimal import Decimal` at the top of the file first — these tests
compare `Decimal` results, and comparing them against floats would pass on
these values and quietly stop testing the thing that matters.

```python
def _mv(day, desc, amount):
    return {"date": f"2025-01-{day:02d}", "description": desc,
            "amount": amount, "currency": "RUB"}


def test_assess_credit_account_reports_net_direction():
    import fbar_reconciler as fr
    result = fr.assess_credit_account([_mv(1, "Purchase", -500.0),
                                       _mv(2, "Purchase", -300.0),
                                       _mv(3, "Repayment", 200.0)])
    assert result["direction"] == "toward_debt"
    assert result["net_change"] == Decimal("-600.00")


def test_assess_credit_account_finds_the_debtor_signature():
    """Interest charged TO the holder is a debtor signature an asset account
    never has, in either language the register is printed in."""
    import fbar_reconciler as fr
    result = fr.assess_credit_account([_mv(1, "Loan interest", -42.0),
                                       _mv(2, "Проценты по кредиту", -17.0),
                                       _mv(3, "Grocery store", -100.0)])
    assert [r["description"] for r in result["debtor_signature_rows"]] == [
        "Loan interest", "Проценты по кредиту"]


def test_assess_credit_account_names_what_it_did_not_check():
    """Age and a parallel declaration are judgment calls. The result must say
    they are outstanding rather than read as a complete answer."""
    import fbar_reconciler as fr
    result = fr.assess_credit_account([_mv(1, "Purchase", -500.0)])
    assert result["checks_remaining"] == ["account_age", "parallel_declaration"]


def test_assess_credit_account_ignores_rows_with_no_amount():
    """A balance observation is not a movement; summing it would double-count."""
    import fbar_reconciler as fr
    result = fr.assess_credit_account([_mv(1, "Opening", None), _mv(2, "Purchase", -50.0)])
    assert result["net_change"] == Decimal("-50.00")


def test_assess_credit_account_on_an_empty_register_is_flat_not_an_error():
    import fbar_reconciler as fr
    result = fr.assess_credit_account([])
    assert result["direction"] == "flat"
    assert result["net_change"] == Decimal("0")
```

- [x] **Step 2: Run and confirm they fail**

Run: `python -m pytest tests/test_fbar_validators.py -k "assess_credit" -v`
Expected: 5 FAILED with `AttributeError: module 'fbar_reconciler' has no attribute 'assess_credit_account'`

- [x] **Step 3: Implement it**

```python
#: Descriptions that only ever appear on an account the filer OWES money on.
#: Kept as data because the register's language varies by export and by
#: vintage, and because a reader should be able to see the whole list without
#: reading the function.
DEBTOR_SIGNATURE_PATTERNS = (
    r"loan\s+interest",
    r"проценты\s+по\s+кредиту",
    r"credit\s+interest\s+charged",
    r"начисленные\s+проценты",
)


def assess_credit_account(movements: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The two mechanical checks from the credit-account decision procedure.

    `credit_card_positive_balance_only` (fbar_schema.CONVENTIONS) makes a credit
    account reportable only where it carried a positive balance in the filer's
    favour. Every movement register lacks a balance column, so that cannot be
    read off the document; references/fbar_guide.md §5 gives four checks that
    answer it between them. Two are arithmetic and live here:

      1. NET DIRECTION over the period, from a neutral zero baseline. The
         baseline is fictional and the LEVEL it produces means nothing — but
         the direction is real and order-invariant, which is the whole reason
         this is safe to compute at all.
      2. DEBTOR SIGNATURE — interest or fees charged TO the filer. An asset
         account pays interest to the holder; it never charges it.

    Checks 3 (account age) and 4 (a parallel foreign declaration's own
    exclusions) are judgment and stay with Phase 1. They are named in
    `checks_remaining` so this result cannot be mistaken for a complete answer,
    which is the failure mode a helper like this invites.

    This is NOT a validator. It answers a question when asked; it does not run
    in `validate()` and returns no Finding.
    """
    patterns = [re.compile(p, re.IGNORECASE) for p in DEBTOR_SIGNATURE_PATTERNS]

    net = Decimal("0")
    signature_rows = []
    for row in movements:
        amount = row.get("amount")
        if amount is not None:
            net += Decimal(str(amount))
        description = str(row.get("description") or "")
        if any(p.search(description) for p in patterns):
            signature_rows.append({
                "date": row.get("date"),
                "description": description,
                "amount": None if amount is None else Decimal(str(amount)),
            })

    if net > 0:
        direction = "toward_zero"
    elif net < 0:
        direction = "toward_debt"
    else:
        direction = "flat"

    return {
        "net_change": net,
        "direction": direction,
        "debtor_signature_rows": signature_rows,
        "checks_performed": ["net_direction", "debtor_signature"],
        "checks_remaining": ["account_age", "parallel_declaration"],
    }
```

Confirm `re` and `Decimal` are already imported at the top of
`fbar_reconciler.py`; add whichever is missing.

- [x] **Step 4: Point the guide at it**

Task 1.3 shipped the four checks in v3.4.4 without naming a function, because
none existed yet. Now one does. In `references/fbar_guide.md` §5, replace:

```markdown
Checks 1 and 2 are mechanical arithmetic over the register. Checks 3 and 4 are
```

with:

```markdown
Checks 1 and 2 are mechanical and `fbar_reconciler.assess_credit_account()`
reports them from a parsed register. Checks 3 and 4 are
```

- [x] **Step 5: Run and commit**

Run: `python -m pytest tests/ -q`
Expected: PASS

```bash
git add scripts/fbar_reconciler.py references/fbar_guide.md tests/test_fbar_validators.py
git commit -m "Mechanize the two arithmetic checks in the credit-account procedure"
```

---

### Task 2.9: Update the skill surfaces and release v3.5.0

`skills/tax-preparation/SKILL.md` is the second FBAR surface, not an
afterthought: its scripts-table rows spell out the validator range and the
basis rules in prose, and an agent reads them without opening the guide.

**Files:**
- Modify: `skills/tax-preparation/SKILL.md:254` (the `fbar_reconciler.py` row), `:169-221` (Step 6)
- Modify: `plugin.json:4`
- Test: `tests/test_reference_consistency.py`

- [x] **Step 1: Write the failing test**

```python
def test_skill_md_states_the_current_validator_range():
    """SKILL.md's scripts table names the validator range in prose. An agent
    reads that row without opening the guide, so it cannot lag the code.

    This is a LIVE COUPLING, not a 3.5.0 artefact: it recomputes the highest
    implemented validator every run, so adding a V17 later fails here until
    SKILL.md is updated too. That is the intent — do not pin the number.
    """
    import fbar_reconciler as fr
    with open(os.path.join(ROOT, "skills", "tax-preparation", "SKILL.md")) as f:
        text = f.read()
    highest = max(int(n.split("_")[0][1:]) for n in dir(fr)
                  if re.fullmatch(r"v\d+_\w+", n))
    assert f"V1–V{highest}" in text or f"V1-V{highest}" in text
```

- [x] **Step 2: Run and confirm it fails**

Run: `python -m pytest tests/test_reference_consistency.py -k "validator_range" -v`
Expected: FAIL — SKILL.md still says `V1–V13`

- [x] **Step 3: Update the `fbar_reconciler.py` scripts-table row**

Replace `validators V1–V13 (Treasury FX provenance, USD recomputation,
$10,000 threshold arithmetic, 25-account rule, addresses, Item 15a
carry-forward, Form 114a spousal scope, retention)` with:

```
validators V1–V16 (Treasury FX provenance, USD recomputation, $10,000 threshold arithmetic, 25-account rule, addresses, Item 15a carry-forward, Form 114a spousal scope, retention, an uncited maximum that tracks the prior year's filing, a summary document listing more accounts than the roster, and an account number whose digit count differs from its siblings')
```

In the same row, after "matches accounts across years through renumbering,
splits, merges and Cyrillic/Latin homoglyph drift", add ", and through a
surviving contract number where a ledger number was reissued".

- [x] **Step 4: Add the anchored-basis note to Step 6**

In `skills/tax-preparation/SKILL.md` Step 6, after the roster-authoring bullet
(item 2, the one naming `fbar_schema.py`):

```markdown
   Where a year's opening balance comes from a cited document rather than a
   statement's own balance column — a prior-year filed Form 114 figure, a
   foreign asset declaration, a bank's "balance as of <date>" letter — set
   `max_value_basis: "anchored_full_year"` and record the citation in
   `max_value_anchor`. The schema requires the citation, and V12 stays quiet
   only once V13 finds the document. Where the opening is genuinely unknown but
   the account cannot go negative, `non_negativity_floor` records a proven
   bound; V12 still warns, because a floor may understate.
```

- [x] **Step 5: Run the whole suite**

Run: `python -m pytest tests/ -q`
Expected: PASS

- [x] **Step 6: Bump and commit**

`plugin.json`: `"version": "3.4.4"` → `"version": "3.5.0"`.

```bash
git add skills/tax-preparation/SKILL.md plugin.json tests/test_reference_consistency.py
git commit -m "Release 3.5.0: anchored and floor bases, contract numbers, V14-V16"
git tag 3.5.0
```

---

# Tranche 3 — v3.6.0, the two new readers

**Covers F1, F3, and the automated halves of F4, F6 and F9.** This is where the
Tranche 2 vocabulary stops being assertable and starts being *derived* — per
**D1**, that is the whole point.

---

### Task 3.1: A profile and reader for a point-in-time balance document (F1, part 1 of 3)

A "Document on the available balance" states one bank-confirmed balance, as of
one date, against one card or account number. It has no period and no
transactions, so every existing profile's assumptions fail on it: `SheetProfile`
and `MovementRegisterProfile` both treat the statement's own period as the
reporting window, and there isn't one.

**Files:**
- Modify: `scripts/statement_profiles.py` — `PointBalanceProfile`, `TBANK_POINT_BALANCE`, registry
- Modify: `scripts/statement_parser.py` — `EVIDENCE_DECLARED_ANCHOR`, `_EVIDENCE_CEILING`, `read_point_balance`, `READERS`, the reader-dispatch branch in `read_source`
- Test: `tests/test_statement_readers.py`

**Interfaces:**
- Consumes: `MovementRecord`, `ReadResult`, `StatementPeriod`, `RUSSIAN_NUMBERS`
- Produces: `read_point_balance(path, profile, currency) -> ReadResult` — one
  `MovementRecord` with `amount=None` and `balance_after` set, `periods=[]`,
  `evidence_kind=EVIDENCE_DECLARED_ANCHOR`. Task 3.2 consumes this shape.

A point balance emitted as a `MovementRecord` with `amount=None` and a stated
`balance_after` is exactly the existing "pure balance observation" shape a
period-end summary already produces. Nothing new is being asserted: **the
document states this balance, and the reader reports what the document says.**

- [x] **Step 1: Write the failing tests**

```python
POINT_BALANCE_FIXTURE = """\
Document on the available balance
Card number: 5536 91** **** 1234
Available balance on 15.03.2026: 123 456,78 ₽
"""


def test_point_balance_reads_one_dated_balance(tmp_path):
    from statement_parser import EVIDENCE_DECLARED_ANCHOR, read_point_balance
    from statement_profiles import get_profile

    path = tmp_path / "balance.txt"
    path.write_text(POINT_BALANCE_FIXTURE, encoding="utf-8")
    result = read_point_balance(str(path), get_profile("tbank_point_balance"),
                                currency="RUB")

    assert len(result.movements) == 1
    movement = result.movements[0]
    assert movement.date == date(2026, 3, 15)
    assert movement.balance_after == Decimal("123456.78")
    assert movement.amount is None          # a level, not a flow
    assert result.evidence_kind == EVIDENCE_DECLARED_ANCHOR
    assert result.periods == []             # a point has no period


def test_point_balance_refuses_a_document_with_two_balances(tmp_path):
    """Two dated balances is a period summary wearing a point balance's name.
    Reading only the first would silently discard the other."""
    from statement_parser import StatementParseError, read_point_balance
    from statement_profiles import get_profile

    path = tmp_path / "balance.txt"
    path.write_text(POINT_BALANCE_FIXTURE
                    + "Available balance on 16.03.2026: 1,00 ₽\n", encoding="utf-8")
    with pytest.raises(StatementParseError) as exc:
        read_point_balance(str(path), get_profile("tbank_point_balance"), currency="RUB")
    assert "two" in str(exc.value).lower() or "2 " in str(exc.value)


def test_point_balance_with_no_balance_line_is_no_data(tmp_path):
    """An unreadable document states nothing; it is not a zero balance."""
    from statement_parser import read_point_balance
    from statement_profiles import get_profile

    path = tmp_path / "balance.txt"
    path.write_text("Document on the available balance\n", encoding="utf-8")
    result = read_point_balance(str(path), get_profile("tbank_point_balance"),
                                currency="RUB")
    assert result.no_data is True
    assert result.movements == []
    assert result.warnings


def test_point_balance_caps_the_basis_at_estimate_on_its_own():
    """One dated balance is a LEVEL at one instant. It says nothing about the
    peak, so alone it must never support a maximum — only anchored to a
    complete ledger (Task 3.3) does it strengthen anything."""
    from statement_parser import EVIDENCE_DECLARED_ANCHOR, _EVIDENCE_CEILING
    assert _EVIDENCE_CEILING[EVIDENCE_DECLARED_ANCHOR] == "estimate"
```

- [x] **Step 2: Run and confirm they fail**

Run: `python -m pytest tests/test_statement_readers.py -k "point_balance" -v`
Expected: 4 FAILED with `ImportError` / `StatementParseError: Unknown profile 'tbank_point_balance'`

- [x] **Step 3: Add the profile**

In `scripts/statement_profiles.py`, after the Shape E section:

```python
# ---------------------------------------------------------------------------
# Shape F — one balance, one date, no period and no transactions
# ---------------------------------------------------------------------------

#: A bank-issued "balance as of <date>" document. It is not a statement: there
#: is no period, no transaction detail and nothing to reconcile against. What
#: it gives is a CONFIRMED LEVEL at one instant, which is the one thing a
#: movement register can never supply and the one thing a register needs to
#: become a balance timeline.
#:
#: Alone it supports nothing — a level at one instant says nothing about a
#: peak, and `_EVIDENCE_CEILING` caps it at `estimate` on exactly that ground.
#: Its value is as the anchor in `anchor_reconstruction`, where a complete,
#: totals-reconciled ledger carries it to a year boundary.
@dataclass(frozen=True)
class PointBalanceProfile:
    name: str
    #: Regex with `date` and `balance` groups. Matched per line; the document
    #: must state EXACTLY ONE, or it is a period summary under another name.
    balance_line: str
    #: Regex with a `value` group naming the card or account the balance is
    #: for. A point balance with no identifier is an orphan document — see
    #: the UNIDENTIFIED convention in fbar_guide.md §9.
    identifier_line: str
    numbers: NumberFormat = RUSSIAN_NUMBERS
    notes: str = ""


TBANK_POINT_BALANCE = PointBalanceProfile(
    name="tbank_point_balance",
    balance_line=(
        r"(?:Available balance|Доступный остаток)\s+on\s+"
        r"(?P<date>\d{2}\.\d{2}\.\d{4})\s*:\s*(?P<balance>.+?)\s*$"
    ),
    identifier_line=r"(?:Card number|Номер карты|Номер счета):\s*(?P<value>.+?)\s*$",
    numbers=RUSSIAN_NUMBERS,
    notes=(
        "Issued on request, so the as-of date is the REQUEST date and lands "
        "wherever the filer asked — mid-March for a December year-end, in the "
        "case this profile was written from. That is not a defect: the point "
        "of the document is that the level is bank-confirmed, and carrying it "
        "back to the year boundary is arithmetic over a ledger, not a reading "
        "problem."
    ),
)
```

Register it in `PROFILES`.

- [x] **Step 4: Add the evidence kind and the reader**

In `scripts/statement_parser.py`, beside the other evidence constants:

```python
EVIDENCE_DECLARED_ANCHOR = "declared_anchor"
```

and in `_EVIDENCE_CEILING`:

```python
    EVIDENCE_DECLARED_ANCHOR: "estimate",
```

The ceiling is `estimate`, not `anchored_full_year`, deliberately: this file
alone supports nothing. `derive_basis` promotes the *account* to
`anchored_full_year` in Task 3.3 only when a reconciled full-year ledger is
present alongside it, and promotion happens there, never here — `weaken_basis`
only ever caps.

```python
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
```

Add `"point_balance": "read_point_balance"` to `READERS`, and add
`"point_balance"` to the currency-required reader tuple in `read_source` — a
point balance states an amount without naming its currency, exactly like a
register.

- [x] **Step 5: Run and commit**

Run: `python -m pytest tests/ -q`
Expected: PASS

```bash
git add scripts/statement_profiles.py scripts/statement_parser.py tests/test_statement_readers.py
git commit -m "Read a bank's point-in-time balance document as a dated anchor"
```

---

### Task 3.2: `anchor_reconstruction.py` — the algebra (F1 part 2 of 3, F6 automated half)

Pure arithmetic, no I/O, no profiles, no policy. Kept out of
`statement_parser.py` because that file is already 2,670 lines and this is a
self-contained algebra with its own test surface.

**Files:**
- Create: `scripts/anchor_reconstruction.py`
- Create: `tests/test_anchor_reconstruction.py`

**Interfaces:**
- Consumes: `MovementRecord` (duck-typed on `.date`, `.amount`, `.order_key()`)
- Produces:

  ```python
  @dataclass(frozen=True)
  class Anchor:
      when: date
      balance: Decimal
      source_file: str

  @dataclass
  class Replay:
      opening: Decimal
      closing: Decimal
      maximum: Decimal
      date_of_maximum: date
      minimum: Decimal
      negative_excursions: List[Dict[str, Any]]

  carry_balance(anchor: Anchor, movements, target: date) -> Decimal
  replay_year(opening: Decimal, movements, tax_year: int) -> Replay
  non_negativity_floor(movements, tax_year: int) -> Tuple[Decimal, Decimal]
  ```

- [ ] **Step 1: Write the failing tests**

Create `tests/test_anchor_reconstruction.py`:

```python
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
```

- [ ] **Step 2: Run and confirm they fail**

Run: `python -m pytest tests/test_anchor_reconstruction.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'anchor_reconstruction'`

- [ ] **Step 3: Write the module**

Create `scripts/anchor_reconstruction.py`:

```python
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
                              "same_timestamp": True}
        elif running < 0:
            open_excursion["end"] = movement.date
            open_excursion["low"] = min(open_excursion["low"], running)
            if movement.date != open_excursion["start"]:
                # An excursion spanning more than one date cannot be a
                # same-minute batch-processing artefact. That distinction is
                # the whole diagnostic value of this report.
                open_excursion["same_timestamp"] = False
        elif open_excursion is not None:
            excursions.append(open_excursion)
            open_excursion = None

    if open_excursion is not None:
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
```

- [ ] **Step 4: Run and confirm they pass**

Run: `python -m pytest tests/test_anchor_reconstruction.py -v`
Expected: 11 PASSED

- [ ] **Step 5: Commit**

```bash
git add scripts/anchor_reconstruction.py tests/test_anchor_reconstruction.py
git commit -m "Add the algebra for carrying a dated balance across a ledger"
```

---

### Task 3.3: Derive `anchored_full_year` and `non_negativity_floor` in Phase 0 (F1 part 3 of 3; F2's derivation)

This is the task **D1** exists for. Until now `anchored_full_year` is a value a
human can type; after this it is a value the parser *derives* from a document,
and V12 becomes honest about it the same way it is honest about every other
basis.

**Files:**
- Modify: `scripts/statement_parser.py` — `derive_basis`, `build_timeline`, `AccountTimeline`, `timeline_to_json`
- Modify: `references/fbar_guide.md` §9 — the cross-validation requirement
- Test: `tests/test_statement_basis.py`, `tests/test_reference_consistency.py`

**Interfaces:**
- Consumes: `anchor_reconstruction.{Anchor, carry_balance, replay_year, non_negativity_floor}` (Task 3.2), `EVIDENCE_DECLARED_ANCHOR` (Task 3.1)
- Produces: `derive_basis(..., anchor_available: bool = False, ledger_reconciled: bool = False)`; `AccountTimeline.reconstruction` (dict or `None`), surfaced in `timeline_to_json` under `"reconstruction"`

**Promotion never happens.** `weaken_basis` still only ever caps — the change is
*which cap* the balance-free branch applies. A register with no anchor caps at
`estimate` as it always has; a register with a verified anchor caps at
`anchored_full_year`; a register with no anchor but complete, reconciled
coverage caps at `non_negativity_floor`. Gaps, breaks and inferred periods then
cap further exactly as before, so an anchored account with a coverage gap
correctly lands on `statement_partial`.

**The two preconditions are not negotiable**, per **D3**:

1. **A dated anchor exists**, from a `point_balance` document or a
   `statement_closing` — a level someone's document actually stated.
2. **Every register in the account group reconciles against its own stated
   period totals.** `_assert_register_reconciles` already proves this. A replay
   over a ledger that does not reconcile is a replay over an incomplete ledger,
   and the exactness claim is false. **Refuse it; do not downgrade it** — a
   silently downgraded reconstruction still shows a figure someone will use.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_statement_basis.py`:

```python
def test_a_register_with_a_verified_anchor_is_anchored_not_an_estimate():
    """A complete, reconciled ledger plus a cited dated level is exact.

    Recording this as `estimate` was the ad hoc workaround this replaces: it
    told the filer to obtain year-covering records they already had.
    """
    from statement_parser import derive_basis
    basis, reasons = derive_basis(
        [_register_result()], gaps=[], continuity_breaks=[],
        interval_uncertainty=[], has_balances=False,
        anchor_available=True, ledger_reconciled=True)
    assert basis == "anchored_full_year"
    assert any("anchor" in r for r in reasons)


def test_a_register_with_no_anchor_but_full_reconciled_coverage_is_a_floor():
    from statement_parser import derive_basis
    basis, reasons = derive_basis(
        [_register_result()], gaps=[], continuity_breaks=[],
        interval_uncertainty=[], has_balances=False,
        anchor_available=False, ledger_reconciled=True)
    assert basis == "non_negativity_floor"
    assert any("cannot go negative" in r for r in reasons)


def test_an_unreconciled_ledger_stays_an_estimate_even_with_an_anchor():
    """Exactness rests on the ledger being COMPLETE. The bank's own totals are
    what prove that; without them the anchor buys nothing."""
    from statement_parser import derive_basis
    basis, _ = derive_basis(
        [_register_result()], gaps=[], continuity_breaks=[],
        interval_uncertainty=[], has_balances=False,
        anchor_available=True, ledger_reconciled=False)
    assert basis == "estimate"


def test_an_anchored_account_with_a_coverage_gap_is_still_partial():
    """Capping composes. An anchor does not close a hole in the year."""
    from statement_parser import derive_basis
    basis, _ = derive_basis(
        [_register_result()],
        gaps=[{"start": "2025-03-01", "end": "2025-03-31", "days": 30,
               "position": "middle"}],
        continuity_breaks=[], interval_uncertainty=[], has_balances=False,
        anchor_available=True, ledger_reconciled=True)
    assert basis == "statement_partial"


def test_the_defaults_preserve_existing_behaviour():
    """Every existing caller passes neither flag and must be unaffected."""
    from statement_parser import derive_basis
    basis, _ = derive_basis([_register_result()], gaps=[], continuity_breaks=[],
                            interval_uncertainty=[], has_balances=False)
    assert basis == "estimate"


def test_an_anchor_is_declined_on_a_multi_currency_group():
    """`carry_balance` sums Decimals with no currency awareness, and this group
    merges currencies onto a USD axis. Converting the anchor at a rate its own
    document never mentioned would be the silent wrong number this module
    exists to prevent."""
    from statement_parser import build_timeline

    timeline = build_timeline(
        "grp", 2025,
        [_register_result(), _register_result(currency="USD"), _anchor_result()],
        fx_rates={"RUB": Decimal("81.996"), "USD": Decimal("1")})
    assert timeline.reconstruction is None
    assert timeline.max_value_basis != "anchored_full_year"
    assert any("SINGLE-CURRENCY" in w for w in timeline.parser_warnings)


def test_two_anchors_that_disagree_produce_no_reconstruction():
    """Each anchor is carried independently and they must agree. A
    disagreement means the ledger between them is incomplete — which is exactly
    the precondition the exactness claim rests on."""
    from statement_parser import build_timeline

    timeline = build_timeline(
        "grp", 2025,
        [_register_result(),
         _anchor_result(when=date(2025, 6, 1), balance=Decimal("1000"),
                        source_file="bal_a.pdf"),
         _anchor_result(when=date(2025, 6, 1), balance=Decimal("9999"),
                        source_file="bal_b.pdf")],
        fx_rates={"RUB": Decimal("81.996")})
    assert timeline.reconstruction is None
    assert any("DIFFERENT January 1 openings" in w for w in timeline.parser_warnings)
```

`_register_result()` gains a `currency="RUB"` keyword, and `_anchor_result()` is
its sibling — a `ReadResult` with `evidence_kind=EVIDENCE_DECLARED_ANCHOR`,
`totals_reconciled=None`, and one `MovementRecord` carrying `amount=None` and a
stated `balance_after`, exactly as `read_point_balance` emits in Task 3.1. Both
`_register_result` results must carry `totals_reconciled=True`, or
`ledger_reconciled` is false and these tests pass for the wrong reason.

Add the fixture helper near the top of the file, following the module's
existing fixture style:

```python
def _register_result(currency="RUB"):
    """A movement register: transaction-level evidence, no balance anywhere.

    `totals_reconciled=True` because the bank's own period totals matched the
    parsed rows — the completeness proof every anchored reconstruction rests on.
    """
    from statement_parser import EVIDENCE_RUNNING_BALANCE, MovementRecord, ReadResult
    source = f"reg_{currency.lower()}.pdf"
    return ReadResult(
        movements=[MovementRecord(date=date(2025, 1, 10), description="txn",
                                  currency=currency, source_file=source,
                                  source_row=1, amount=Decimal("100"),
                                  balance_after=None)],
        source_file=source, currencies=[currency],
        evidence_kind=EVIDENCE_RUNNING_BALANCE, totals_reconciled=True)


def _anchor_result(when=date(2025, 6, 1), balance=Decimal("1000"),
                   source_file="bal.pdf", currency="RUB"):
    """One stated level at one date, as `read_point_balance` emits it.

    No period: a point balance has no span and must contribute no coverage.
    `totals_reconciled` stays None — it is not a register, and reading that
    None as a failure would block every reconstruction it exists to enable.
    """
    from statement_parser import EVIDENCE_DECLARED_ANCHOR, MovementRecord, ReadResult
    return ReadResult(
        movements=[MovementRecord(date=when, description="stated available balance",
                                  currency=currency, source_file=source_file,
                                  source_row=1, amount=None, balance_after=balance)],
        source_file=source_file, currencies=[currency],
        evidence_kind=EVIDENCE_DECLARED_ANCHOR)
```

- [ ] **Step 2: Run and confirm they fail**

Run: `python -m pytest tests/test_statement_basis.py -k "anchor or floor or reconciled or defaults or currency" -v`
Expected: 6 FAILED (four on `TypeError: derive_basis() got an unexpected keyword argument 'anchor_available'`, two on `TypeError: ReadResult.__init__() got an unexpected keyword argument 'totals_reconciled'`), 1 PASSED (`test_the_defaults_preserve_existing_behaviour`)

- [ ] **Step 3: Extend `derive_basis`**

Change the signature:

```python
def derive_basis(read_results: List[ReadResult],
                 gaps: List[Dict[str, Any]],
                 continuity_breaks: List[Dict[str, Any]],
                 interval_uncertainty: List[Dict[str, Any]],
                 has_balances: bool,
                 anchor_available: bool = False,
                 ledger_reconciled: bool = False) -> Tuple[str, List[str]]:
```

Replace the `if not has_balances:` branch with:

```python
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
```

- [ ] **Step 4a: Record reconciliation as a fact, not only as an exception**

`_assert_register_reconciles` currently *raises* on a mismatch and records
nothing on success, so "this ledger reconciles" is not reachable from
`build_timeline`. Add the field to `ReadResult`:

```python
    #: Whether the register's parsed rows reconcile against the bank's own
    #: stated period totals. `True` when they do, `False` when the reader
    #: accepted the file with a caveat, and **None when the file is not a
    #: register at all** — a running-balance sheet has no totals to reconcile,
    #: and reading its None as a failure would block every anchored
    #: reconstruction on a group that happens to hold one.
    totals_reconciled: Optional[bool] = None
```

and set `result.totals_reconciled = True` in `read_movement_register_lines`
immediately after `_assert_register_reconciles` returns without raising.

- [ ] **Step 4b: Wire the reconstruction into `build_timeline`**

Insert this immediately **before** the `basis, reasons = derive_basis(...)`
line (`statement_parser.py:2125-2126`), and change that call to pass the two
new flags:

```python
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
```

`has_balances` is computed a few lines above this block already; move that
assignment up if the ordering does not work out, and import `Anchor`,
`carry_balance`, `non_negativity_floor` and `replay_year` from
`anchor_reconstruction` at the top of `statement_parser.py`.

- [ ] **Step 4c: Carry it onto the timeline and into the JSON**

Add to `AccountTimeline`:

```python
    #: What an anchored reconstruction established, or None. Carries the
    #: DERIVATION, not just the answer — the five-year recordkeeping rule wants
    #: to see how a figure was reached, and this figure was reached by
    #: arithmetic over a cited document rather than read off a column.
    reconstruction: Optional[Dict[str, Any]] = None
```

Pass `reconstruction=reconstruction` in the `AccountTimeline(...)` construction
at the end of `build_timeline`, and add to `timeline_to_json`:

```python
        "reconstruction": timeline.reconstruction,
```

Every figure inside it has already been through `money()`, so it is
JSON-serializable as built and needs no further conversion.

- [ ] **Step 5: Add the cross-validation requirement to the guide**

The proposal makes this a requirement, not a suggestion, and it is what made
the first live result trustworthy. Add to `references/fbar_guide.md` §9, after
the "A replayed series that dips below zero" subsection from Task 1.5:

```markdown
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
```

Add the matching test:

```python
def test_fbar_guide_requires_cross_validating_a_reconstruction():
    text = read_ref("fbar_guide.md")
    assert "Cross-validate a reconstruction before trusting it" in text
    assert "until the same chain has reproduced a known answer" in text
```

- [ ] **Step 6: Run and commit**

Run: `python -m pytest tests/ -q`
Expected: PASS

```bash
git add scripts/statement_parser.py references/fbar_guide.md tests/test_statement_basis.py tests/test_reference_consistency.py
git commit -m "Derive an anchored basis from a cited level and a reconciled ledger"
```

---

### Task 3.4: A reader for a wide daily portfolio-value series (F3), with column matching (F4)

T-Bank's broker reports give exact cash and exact holdings (ISIN + share count)
and **never a price** — confirmed across eight brokerage accounts in one
session, all with the identical gap. A separate export, a daily-value time
series, gives the total priced portfolio value per day per account and closed
the gap for six accounts in one pass.

**Files:**
- Modify: `scripts/statement_profiles.py` — `DailyValueSeriesProfile`, `TBANK_DAILY_VALUES`, registry
- Modify: `scripts/statement_parser.py` — `EVIDENCE_PRICED_DAILY_SERIES`, `_EVIDENCE_CEILING`, `read_daily_value_series`, `match_columns_by_open_date`, `READERS`, `SOURCE_FIELDS`
- Create: `tests/test_daily_value_series.py`
- Modify: `references/fbar_guide.md` §9 — what to request when a broker report cannot be priced

**Interfaces:**
- Consumes: `SheetProfile` conventions, `parse_decimal`, `parse_full_date`
- Produces:
  - `read_daily_value_series(path, profile, contract_open_dates=None) -> List[Tuple[str, ReadResult]]` — **one `ReadResult` per column**, unlike every other reader, which returns one per file
  - `match_columns_by_open_date(first_dates: Dict[str, date], contract_open_dates: Dict[str, date]) -> Dict[str, str]` — column label → `account_key`
  - `SOURCE_FIELDS["contract_open_dates"]` — `{account_key: "YYYY-MM-DD"}`

This is the **only** reader that returns several accounts from one file, so
`read_source` needs a branch for it rather than the single-result path. Keep
that branch explicit and commented; a reader whose return shape differs from
its siblings is exactly the thing a future maintainer will assume away.

A priced daily series is graded `statement_full_year`-capable
(`_EVIDENCE_CEILING[EVIDENCE_PRICED_DAILY_SERIES] = "statement_full_year"`): it
is the closest thing to a bank's own daily balance column that a brokerage
produces, values are already priced, and gap/continuity analysis then caps it
honestly if days are missing.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daily_value_series.py`:

```python
#!/usr/bin/env python3
"""The wide daily portfolio-value reader (docs/fbar_enhancements_plan.md, F3/F4).

Two properties matter. The reader must emit ONE TIMELINE PER COLUMN rather than
one per file, because a single export covers every brokerage account the filer
holds. And a column must be matched to an account by its first non-blank date
against that account's contract-open date — never by magnitude and never by
position, both of which survive a transposition unchanged.

All fixtures synthetic.
"""

import os
import sys
from datetime import date
from decimal import Decimal

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from statement_parser import (  # noqa: E402
    EVIDENCE_PRICED_DAILY_SERIES,
    StatementParseError,
    match_columns_by_open_date,
)


def test_columns_match_accounts_by_first_date_against_contract_open_date():
    mapping = match_columns_by_open_date(
        first_dates={"Брокерский счёт": date(2025, 6, 2),
                     "Брокерский счет 1": date(2022, 3, 14)},
        contract_open_dates={"broker_a": date(2022, 3, 14),
                             "broker_b": date(2025, 6, 1)})
    assert mapping == {"Брокерский счет 1": "broker_a", "Брокерский счёт": "broker_b"}


def test_a_column_cannot_predate_the_account_it_is_matched_to():
    """An account cannot hold value before it existed. That is what forces the
    match rather than merely suggesting it."""
    with pytest.raises(StatementParseError) as exc:
        match_columns_by_open_date(
            first_dates={"col": date(2020, 1, 1)},
            contract_open_dates={"broker_a": date(2022, 3, 14)})
    assert "predates" in str(exc.value)


def test_two_accounts_opened_on_one_date_refuse_to_match():
    """The method is silent here and must say so rather than fall back to
    magnitude, which is the signal a transposition preserves."""
    with pytest.raises(StatementParseError) as exc:
        match_columns_by_open_date(
            first_dates={"a": date(2022, 3, 20), "b": date(2022, 3, 21)},
            contract_open_dates={"x": date(2022, 3, 14), "y": date(2022, 3, 14)})
    assert "same date" in str(exc.value)


def test_reader_emits_one_result_per_column(tmp_path):
    """One export covers every brokerage account; one ReadResult per file would
    merge them into a single fictional account."""
    from statement_parser import read_daily_value_series
    from statement_profiles import get_profile

    path = _write_series_csv(tmp_path)
    results = read_daily_value_series(
        str(path), get_profile("tbank_daily_values"),
        contract_open_dates={"broker_a": date(2025, 1, 1),
                             "broker_b": date(2025, 2, 1)})
    assert sorted(key for key, _ in results) == ["broker_a", "broker_b"]
    for _, result in results:
        assert result.evidence_kind == EVIDENCE_PRICED_DAILY_SERIES


def test_reader_treats_a_blank_cell_as_no_observation(tmp_path):
    """A blank is not a zero. An account that had not opened yet held nothing
    the document knows about, and a zero would be a determined balance."""
    from statement_parser import read_daily_value_series
    from statement_profiles import get_profile

    path = _write_series_csv(tmp_path)
    results = dict(read_daily_value_series(
        str(path), get_profile("tbank_daily_values"),
        contract_open_dates={"broker_a": date(2025, 1, 1),
                             "broker_b": date(2025, 2, 1)}))
    dates = [m.date for m in results["broker_b"].movements]
    assert date(2025, 1, 1) not in dates


def test_a_priced_daily_series_can_support_a_full_year():
    from statement_parser import EVIDENCE_PRICED_DAILY_SERIES, _EVIDENCE_CEILING
    assert _EVIDENCE_CEILING[EVIDENCE_PRICED_DAILY_SERIES] == "statement_full_year"


def _write_series_csv(tmp_path):
    """Two account columns. The first is blank on 01.01, the second is not —
    which is what makes the two columns distinguishable by start date."""
    path = tmp_path / "values.csv"
    path.write_text(
        "Дата,Брокерский счёт,Брокерский счет 1\n"
        "01.01.2025,,1000.00\n"
        "01.02.2025,500.00,1100.00\n"
        "31.12.2025,700.00,1200.00\n",
        encoding="utf-8")
    return path
```

- [ ] **Step 2: Run and confirm they fail**

Run: `python -m pytest tests/test_daily_value_series.py -v`
Expected: collection error — `ImportError: cannot import name 'EVIDENCE_PRICED_DAILY_SERIES'`

- [ ] **Step 3: Add the profile**

```python
# ---------------------------------------------------------------------------
# Shape G — a wide daily value series: one row per date, one column per account
# ---------------------------------------------------------------------------

#: A daily portfolio-value export — `.csv`, or an `.xlsx` tab named
#: «Стоимость по дням, RUB». Values are ALREADY PRICED: cash plus securities at
#: that day's price, which is the exact figure a broker's trade report cannot
#: give. T-Bank's own «Отчет о сделках и операциях» states cash and holdings
#: (ISIN + share count) and no price at all — checked across eight brokerage
#: accounts, every one with the identical gap. The next document to request is
#: this one, never another trade report.
#:
#: Wide, not long: one date column and N account columns. Every other profile
#: here describes a single account's ledger, which is why this needs its own
#: shape and its own reader return type.
@dataclass(frozen=True)
class DailyValueSeriesProfile:
    name: str
    #: Header text of the date column.
    date_column: str
    #: Regex matched against the remaining header cells to decide which are
    #: account columns. Everything else — a total, a note — is skipped.
    account_column_pattern: str
    numbers: NumberFormat = PLAIN_NUMBERS
    date_kind: str = "full"
    #: Sheet to read for an .xlsx export. None means "a CSV, one table".
    sheet_pattern: Optional[str] = None
    notes: str = ""


TBANK_DAILY_VALUES = DailyValueSeriesProfile(
    name="tbank_daily_values",
    date_column="Дата",
    account_column_pattern=r"^(Брокерский|ИИС|Broker|IIS)",
    numbers=PLAIN_NUMBERS,
    date_kind="full",
    sheet_pattern=r"Стоимость по дням",
    notes=(
        "Columns are labelled by NICKNAME and carry no account number, so a "
        "column is matched to an account by its first non-blank date against "
        "that account's contract-open date (`Инвестор: NAME / NUMBER от DATE` "
        "in the account's own report). Never by magnitude and never by column "
        "order: a transposition preserves both, and one stood undetected in a "
        "live workbook for over a week.\n\n"
        "A BLANK CELL IS NOT A ZERO. Before an account opened there is no "
        "observation, and emitting 0.00 there would assert a determined "
        "balance the export never states — the §9.6 empty-export rule in a "
        "different costume."
    ),
)
```

- [ ] **Step 4: Add the evidence kind, the matcher and the reader**

```python
EVIDENCE_PRICED_DAILY_SERIES = "priced_daily_series"
```

with `_EVIDENCE_CEILING[EVIDENCE_PRICED_DAILY_SERIES] = "statement_full_year"`.

```python
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
                f"Column {label!r} has a value on {first_date}, before {key!r} was "
                f"opened on {opened}. An account cannot hold value before it existed, "
                "so this pairing is wrong and the contract-open dates or the column "
                "set need checking before anything is read from this file.")
        mapping[label] = key
    return mapping
```

Then the reader:

```python
def read_daily_value_series(path: str, profile, currency: str,
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
    header, data_rows = rows[0], rows[1:]

    date_index = header.index(profile.date_column)
    column_pattern = re.compile(profile.account_column_pattern)
    account_columns = {label: index for index, label in enumerate(header)
                       if index != date_index and column_pattern.search(label or "")}
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
        when = parse_full_date(row[date_index], where=f"{path}:{row_number}")
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
        key = mapping[label]
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
        result.periods.append(StatementPeriod(start=entries[0][1], end=entries[-1][1]))
        out.append((key, result))
    return out
```

`_read_table(path, profile)` is a small helper this task also adds: it reads a
`.csv` with `csv.reader` when `profile.sheet_pattern` is `None`, and otherwise
opens the `.xlsx` and returns `read_sheet_rows` for the first sheet whose name
matches `sheet_pattern`.

`currency` is threaded from the source entry exactly as `read_point_balance`
threads it — a value column states an amount without naming its currency, so
defaulting it would label rubles as dollars. **Add `"daily_value_series"` to
`read_source`'s currency-required reader tuple**, alongside `"point_balance"`,
`"text_period_summary"` and `"pdf_movement_register"`.

Add `"daily_value_series": "read_daily_value_series"` to `READERS` and
`"contract_open_dates": None` to `SOURCE_FIELDS`. This is the **only** reader
returning several accounts from one file, so `read_source` needs its own
branch:

```python
        if source["reader"] == "daily_value_series":
            # The ONLY reader that returns several accounts from one file: a
            # daily-value export covers every brokerage account the filer
            # holds. Keyed by the matcher, not by `account_key_for`, because
            # the file's own columns decide which accounts it describes.
            out.extend(reader(match, profile, currency=source["currency"],
                              contract_open_dates=_parse_open_dates(source)))
            continue
```

`_parse_open_dates(source)` maps the source entry's `contract_open_dates`
`{account_key: "YYYY-MM-DD"}` through `parse_full_date`, returning `{}` when
the key is absent — which `match_columns_by_open_date` then refuses, naming the
missing configuration rather than matching positionally.

- [ ] **Step 5: Document what to request when a broker report cannot be priced**

Add to `references/fbar_guide.md` §9:

```markdown
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
```

- [ ] **Step 6: Run and commit**

Run: `python -m pytest tests/ -q`
Expected: PASS

```bash
git add scripts/statement_profiles.py scripts/statement_parser.py references/fbar_guide.md tests/test_daily_value_series.py
git commit -m "Read a wide daily portfolio-value export, one timeline per column"
```

---

### Task 3.5: Have the series reader report its own account count (F9, automated half)

Task 2.6 built V15 against a hand-declared count. This makes the count arrive
from the document itself, which is the only version that catches an account
nobody knew about — a hand-declared count is written by the same person who
would have noticed the surplus.

**Files:**
- Modify: `scripts/statement_parser.py` — `run_sources`, `write_outputs`, `render_parse_report`
- Test: `tests/test_daily_value_series.py`

**Interfaces:**
- Consumes: `read_daily_value_series` (Task 3.4), `EXTERNAL_COUNT_FIELDS` (Task 2.6)
- Produces: `external_account_counts` in the Phase 0 output JSON, in exactly the
  shape `fbar_schema.EXTERNAL_COUNT_FIELDS` defines, ready to paste into a roster

- [ ] **Step 1: Write the failing test**

```python
def _series_workspace(tmp_path, columns=("Брокерский счёт", "Брокерский счет 1")):
    """A workspace holding one daily-value export and the sources.json for it."""
    import json

    header = "Дата," + ",".join(columns) + "\n"
    body = ("01.01.2025," + ",".join("" for _ in columns) + "\n"
            "01.02.2025," + ",".join("500.00" for _ in columns) + "\n"
            "31.12.2025," + ",".join("700.00" for _ in columns) + "\n")
    (tmp_path / "values.csv").write_text(header + body, encoding="utf-8")

    (tmp_path / "sources.json").write_text(json.dumps({
        "tax_year": 2025,
        "fx_rates": [{"currency": "RUB", "units_per_usd": 81.996,
                      "as_of": "2025-12-31", "source": "treasury"}],
        "sources": [{
            "filer": "primary",
            "institution": "T-Bank",
            "glob": "values.csv",
            "reader": "daily_value_series",
            "profile": "tbank_daily_values",
            "account_key": "broker",
            "currency": "RUB",
            "contract_open_dates": {"broker_a": "2025-01-01",
                                    "broker_b": "2025-01-15"},
        }],
    }), encoding="utf-8")
    return tmp_path


def test_the_series_reader_reports_how_many_accounts_the_file_listed(tmp_path):
    """The count has to come from the DOCUMENT. A count typed by the same
    person who wrote the roster reproduces the roster's blind spot."""
    from statement_parser import load_sources, run_sources

    workspace = _series_workspace(tmp_path)
    config = load_sources(str(workspace / "sources.json"))
    output = run_sources(config, str(workspace), filer="primary")

    assert output["external_account_counts"] == [{
        "institution": "T-Bank",
        "account_type": "securities",
        "count": 2,
        "source_document": "values.csv",
        "note": "account columns found in the daily value series",
    }]


def test_the_reported_count_is_columns_found_not_columns_matched(tmp_path):
    """A column the matcher could not place still exists in the document, and
    it is exactly the account nobody rostered. Counting only matched columns
    would hide the finding V15 exists to surface.

    Three columns, two contract-open dates: the matcher raises, the run records
    the failure as a parser warning, and the COUNT is still 3.
    """
    from statement_parser import load_sources, run_sources

    workspace = _series_workspace(
        tmp_path, columns=("Брокерский счёт", "Брокерский счет 1", "ИИС"))
    config = load_sources(str(workspace / "sources.json"))
    output = run_sources(config, str(workspace), filer="primary")

    assert output["external_account_counts"][0]["count"] == 3
    # The unmatched column is reported as a PARSER warning, not as a key on the
    # count entry: that entry is pasted straight into a roster, where
    # `_reject_unknown` refuses any key outside EXTERNAL_COUNT_FIELDS.
    assert any("contract-open date" in w for w in output["parser_warnings"])
```

`run_sources` returns timelines grouped by filer today. This task changes its
return shape to a dict carrying three keys — `{"timelines": {...},
"external_account_counts": [...], "parser_warnings": [...]}` — so update its two
existing callers (`main` and `_print_parse_summary`) in the same step, and keep
the timelines under an explicit key rather than merging the counts into the same
mapping.

Each count entry carries **exactly** the keys in
`fbar_schema.EXTERNAL_COUNT_FIELDS` and no others. It is meant to be pasted
into a roster, and `_reject_unknown` refuses any extra key — so a match failure
or any other complaint goes into `parser_warnings`, never onto the entry.

- [ ] **Step 2: Run and confirm they fail**

Run: `python -m pytest tests/test_daily_value_series.py -k "reports_how_many or columns_found" -v`
Expected: 2 FAILED with `KeyError: 'external_account_counts'`

- [ ] **Step 3: Implement**

In `run_sources`, accumulate one entry per `daily_value_series` source: the
institution from the source entry, `account_type` `"securities"` where the
source says so, `count` = **the number of account columns the file contained**,
not the number successfully matched, and `source_document` = the file's
basename. Emit the list in the Phase 0 output JSON beside the timelines, and
print it in `render_parse_report` under a "What the document itself counted"
heading.

Counting columns *found* rather than columns *matched* is the whole point: an
unmatched column is precisely the account that is on no roster, and counting
matched columns would make the surplus disappear at exactly the moment it
mattered.

- [ ] **Step 4: Run and commit**

Run: `python -m pytest tests/ -q`
Expected: PASS

```bash
git add scripts/statement_parser.py tests/test_daily_value_series.py
git commit -m "Report the account count a daily-value export states for itself"
```

---

### Task 3.6: Update the skill surfaces and release v3.6.0

**Files:**
- Modify: `skills/tax-preparation/SKILL.md:255` (the `statement_parser.py` row), Step 6
- Modify: `references/fbar_guide.md` §9 (the Phase 0 diagram and reader list), §11 (Limits)
- Modify: `plugin.json:4`
- Test: `tests/test_reference_consistency.py`

Both SKILL.md's `statement_parser.py` row and §9/§11 of the guide state the
reader list and the no-synthesis rule **verbatim**. Tranche 3 changes both, and
a plan that updates only the guide ships half-done.

- [ ] **Step 1: Write the failing test**

```python
def test_the_documented_reader_list_matches_the_registry():
    """SKILL.md and the guide both enumerate the readers in prose. An agent
    picks a reader from that prose without opening the code."""
    from statement_parser import READERS
    with open(os.path.join(ROOT, "skills", "tax-preparation", "SKILL.md")) as f:
        skill = f.read()
    guide = read_ref("fbar_guide.md")
    human = {"xlsx_running_balance": "running balance",
             "xlsx_two_sheet_card": "two-sheet credit card",
             "text_period_summary": "period-end summary",
             "pdf_movement_register": "PDF movement register",
             "point_balance": "point-in-time balance",
             "daily_value_series": "daily value series"}
    assert set(human) == set(READERS), "reader registry changed; update this map"
    for phrase in human.values():
        assert phrase in skill, f"SKILL.md does not mention the {phrase} reader"
        assert phrase in guide, f"fbar_guide.md does not mention the {phrase} reader"
```

- [ ] **Step 2: Run and confirm it fails**

Run: `python -m pytest tests/test_reference_consistency.py -k "reader_list" -v`
Expected: FAIL — neither document mentions the two new readers

- [ ] **Step 3: Update the `statement_parser.py` scripts-table row**

Two edits inside that row:

- extend the reader list to `(running balance, two-sheet credit card, period-end summary, PDF movement register, point-in-time balance, daily value series)`;
- replace the closing no-synthesis sentence, keeping the rule and stating its
  one narrow exception:

```
**No balance is ever synthesized from transaction deltas alone** — a PDF movement register states no balance, so on its own it yields movements and an `estimate` basis, and balance-bearing PDFs stay with `Read`. The single exception is an ANCHORED reconstruction: where a document states a dated balance and every register reconciles against the bank's own period totals, the opening is carried from that citation and the year replayed exactly, yielding `anchored_full_year` with the anchor recorded. Where no anchor exists but the ledger is complete and reconciled, the account's non-negativity gives a `non_negativity_floor` — a proven bound, not an estimate.
```

- [ ] **Step 4: Update the guide's §9 diagram and §11**

In §9's Phase 0 diagram, add the two readers to the per-source-readers line. In
the paragraph beginning "Phase 0 is optional and file-format-bound", add:

```markdown
Two readers cover documents that are not statements at all. A **point-in-time
balance** document (`point_balance`) states one bank-confirmed level at one
date, with no period and no transactions; alone it supports nothing, and its
value is as the anchor a complete ledger is carried across. A **daily value
series** (`daily_value_series`) is a wide table — one row per date, one column
per account, values already priced — and is the only reader that yields several
accounts from one file.
```

Update §11's "Limits" paragraph the same way as the SKILL.md row in step 3, so
the two do not disagree.

- [ ] **Step 5: Add the anchored workflow to SKILL.md Step 6**

After the Phase 0 invocation block:

```markdown
   Where an account's statements are movement registers with no balance
   column, ask whether a dated balance document exists — a bank "balance as of
   <date>" letter, a prior-year filed maximum, a foreign asset declaration.
   One such citation plus a reconciled full-year register yields an exact
   maximum rather than an estimate. Before trusting it, run the same
   reconstruction on an account whose answer is already known and confirm it
   reproduces that figure; see `./references/fbar_guide.md` §9.
```

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 7: Bump, commit, tag**

`plugin.json`: `"version": "3.5.0"` → `"version": "3.6.0"`.

```bash
git add skills/tax-preparation/SKILL.md references/fbar_guide.md plugin.json tests/test_reference_consistency.py
git commit -m "Release 3.6.0: point-balance anchors and priced daily value series"
git tag 3.6.0
```

---

## Deferred and declined

Recorded so a later reader can see these were decided, not missed.

| Item | Decision |
|---|---|
| F12's `unidentified_documents` roster bucket | **Declined** (D4). No validator would consume it; a schema field nothing reads is a field that drifts. The guide convention is the deliverable. |
| F10 as a check inside `normalize_account_number` | **Relocated** to V16 (D4). The function is a pure fold with no channel for a warning and a docstring forbidding its output from reaching display. |
| F5's "every cited source predates the tax year" condition | **Not implemented** (Task 2.5). A `source_documents` entry is a path, not a date; inferring a year from a filename is the filename-sniffing this module refuses everywhere else. |
| F3's dedicated `"priced_daily_series"` basis value | **Declined.** The distinction is already carried by `evidence_kind`, which is what `_EVIDENCE_CEILING` grades. A basis value would duplicate it in the vocabulary V12 reads, for no decision V12 makes differently. |
| Regenerating `.integrity-sha256.txt` | **Out of scope** (D5). Its paths point at a directory this repository is not, its first line hashes the empty string, and nothing reads it. Raise separately. |
| Automatic resolution of a same-timestamp negative excursion | **Declined** (Task 1.5, Task 3.2). Any ordering that clears the dip is one we chose, and it would be indistinguishable in the workpaper from an order the document recorded. |

---

## Traceability

| Finding | Tranche | Task(s) |
|---|---|---|
| F1 — live balance + trailing statement reconstruction | 3 | 3.1, 3.2, 3.3 |
| F2 — basis vocabulary between estimate and full year | 2, 3 | 2.1, 2.2, 2.3, 3.3 |
| F3 — daily priced portfolio-value export | 3 | 3.4 |
| F4 — matching unlabelled columns | 1, 3 | 1.4 (recipe), 3.4 (`match_columns_by_open_date`) |
| F5 — uncited figure tracking the prior year | 2 | 2.5 (V14) |
| F6 — same-timestamp negative dip | 1, 3 | 1.5 (pitfall), 3.2 (`negative_excursions`) |
| F7 — contract number vs ledger number | 2 | 2.4 |
| F8 — credit-account decision procedure | 1, 2 | 1.3 (four checks), 2.8 (`assess_credit_account`) |
| F9 — account count mismatch | 2, 3 | 2.6 (V15), 3.5 (count from the document) |
| F10 — account-number transcription safeguard | 2 | 2.7 (V16) |
| F11 — live Treasury rate check | 1 | 1.1 |
| F12 — unattributable document convention | 1 | 1.2 |
