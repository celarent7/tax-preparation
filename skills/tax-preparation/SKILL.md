---
name: tax-preparation
description: >
  This skill should be used whenever the user mentions anything related to US individual taxes,
  tax filing, or tax documents — even if they don't explicitly say "taxes." Trigger on W-2, 1099
  (any type: -B, -NEC, -DIV, -INT, -R), Schedule C, Schedule D, Form 1040, Form 8949, RSUs,
  ESPPs, stock vesting, cost basis, capital gains, wash sales, self-employment tax, SE tax,
  freelance income, quarterly estimated payments, standard vs itemized deduction, SALT cap,
  mortgage interest deduction, Child Tax Credit, EITC, FBAR, tax liability calculation, refund
  estimation, underpayment penalty, tax brackets, deduction optimization, or draft return review.
  Also trigger when the user provides tax document data (wages, withholding, proceeds, basis
  figures) and wants to know the tax impact. This skill provides scripts, reference data, and
  domain knowledge for US individual tax preparation that produces more accurate and structured
  results than general knowledge alone.
allowed-tools: Read, Bash, WebSearch, WebFetch, Grep, Glob, Task, Skill, Write, AskUserQuestion
metadata:
  version: 3.4.2
  last-updated: 2026-08-15
  target-users: individuals, families, self-employed
  tax-year: 2025
  supported-tax-years: "2023-2026"
  state-coverage: "federal + California (2023–2025)"
---
# Tax Preparation Skill

## Security & Execution Protocol
1. **Local Paths Only**: Always use relative paths (starting with `./`) for script execution and file reading. Never attempt to access files outside the current plugin directory.
2. **Environment Isolation**: Perform all calculations within the provided Python environment. Do not attempt to install new system packages.
3. **Data Privacy**: Remind users to use anonymized data if they are working in a non-secure or shared LLM environment.
4. **Script Execution Fallback**: If Bash script execution is unavailable in your environment, replicate the script logic manually using the constants in `./scripts/tax_constants.json`. The scripts are transparent Python — read them to understand the exact formulas, then perform the same calculations step-by-step and show your work.

## Orchestration Guardrails (SOP)

### Fast Path (no documents)
When the user provides all values directly in their message (wages, AGI, basis, etc.) and there are no PDFs to read, skip Phase 1. Present a brief structured summary of the values extracted from the conversation, ask for confirmation, then proceed to Phase 3.

### Full Path (documents present)
When the user provides PDF documents (W-2s, 1099s, brokerage statements), follow all three phases:

### Phase 1: Extraction & Normalization
Use the `Read` tool to extract data from provided PDFs.
- Categorize data using the field-mapping tables in `./references/form_processing.md`.
- For ambiguous form fields, consult `./references/few_shots.md` for extraction examples.
- **Required field — tax year**: every checkpoint must state the tax year explicitly. Supported years come from `supported_years` in `./scripts/tax_constants.json` (currently **2023–2026**).
  > **Tax-year stop rule**: if the requested year is not in `supported_years`, **stop and tell the user** rather than approximating with an adjacent year's figures. Bracket, deduction and credit amounts are indexed annually and OBBBA changed several of them mid-decade, so a neighbouring year is not a safe substitute.
- **Required field — state of residence**: needed to decide whether the state layer (Step 3.5) applies.
- **Checkpoint**: Present the extracted data in a structured JSON block to the user and ask: *"Please confirm these values match your documents before we proceed to calculations."*

### Phase 2: Verification Gate
Do not invoke calculation scripts until the user has explicitly confirmed the extracted data from Phase 1.
- If the user identifies an error, re-read or manually adjust the JSON before proceeding.

### Phase 3: Analysis & Computation
Execute the relevant `./scripts/` tools (or replicate their logic manually per the fallback rule above).
- Cross-reference multiple documents (e.g., matching 1099-B sales to RSU vesting records).
- If a script returns an `isError: true` flag, explain the validation error to the user and request the missing data.

---

## PDF Document Reading

**IMPORTANT**: This skill reads PDF documents directly. When users provide tax documents (W-2s, 1099s, statements), use the Read tool with the file path to view and extract data.

The Read tool supports PDF files and extracts both text and visual content. After reading a document:
1. Extract relevant fields using the field-mapping tables in `./references/form_processing.md`
2. **Execute Orchestration Guardrail**: Confirm extracted data with the user before using it in any scripts.
3. Record in JSON format for calculations.

For RSU and stock compensation documents, always verify cost basis against vesting records. The 1099-B cost basis is frequently incorrect for RSUs. See `./references/form_processing.md` for detailed extraction procedures, brokerage platform import guidance, and the RSU verification process.

---

## Core Workflow (Enforced Sequence)

### Step 1: Gather & Confirm Documents
Collect all tax documents. Use the checklist in `./references/document_checklist.md`.
- **Extraction**: Read documents and present findings for user confirmation.
- **Verification**: Ensure Filing Status, Dependents, and State Residency are established.

### Step 2: Calculate Income & Adjustments
**Prerequisite**: All W-2 and 1099 values must be confirmed.
```bash
python ./scripts/tax_calculator.py --gross-income <confirmed_amount> --adjustments <amount> --year 2025
```

> **QBI (§199A) — never estimate as a flat 20% of profit.** If Schedule C profit
> exists, run the QBI calculator (or `tax_calculator.py --compute-qbi`, which
> calls the engine and feeds the result into the CA add-back automatically).
> Profit is first reduced by ½ SE tax, SE health insurance, and SE retirement;
> the deduction is then capped by a wage/UBIA limit and an overall
> taxable-income limit. See `./references/self_employment_guide.md`.

> **Net capital loss year:** For a net-capital-loss year, pass `--net-capital-loss`
> (positive number) instead of `--ltcg`; the tool applies the annual offset limit and
> reports the carryforward.

> **ISO exercisers — AMT:** an ISO bargain element is a classic AMT preference item.
> The `amt_risk` flag always surfaces this; for a quantitative screen, also pass
> `--amt-quick-test` with `--iso-bargain-element <spread>` and `--salt-deducted <SALT
> inside your itemized deduction>`. A positive result means Form 6251 must actually be
> prepared — the screen overstates TMT for filers with large LTCG/qualified-dividend
> income. See `./docs/limitations.md` §4.

> **Estimated payment safe harbor**: Use `estimated_tax_calculator.py` for quarterly payment amounts. The IRS safe harbor is the **lesser of** (a) 90% of current-year projected tax or (b) 100% of prior-year tax — **110% if prior-year AGI exceeded $150,000, or $75,000 for married filing separately**. Always compute both and present the lower figure to minimize required payments.
>
> The MFS threshold is half the general one; applying $150,000 to an MFS filer
> understates the required payment and causes an underpayment penalty.
>
> For mixed W-2 + SE filers pass `--projected-wages` explicitly — the 0.9%
> Additional Medicare projection coordinates wages and SE income per Form 8959.
> *Source: IRS Form 1040-ES, <https://www.irs.gov/pub/irs-pdf/f1040es.pdf>.*

### Step 3: Determine Deductions
Compare standard deduction against itemized total:
```bash
python ./scripts/deduction_analyzer.py --agi <confirmed_agi> --mortgage-interest <amount> --charitable-cash <amount> --year 2025
```
Add `--student-loan-interest <amount>` when the taxpayer paid qualified student-loan
interest (1098-E): it surfaces the above-the-line IRC 221 deduction with its MAGI
phase-out (separate from the itemize-vs-standard decision; MFS filers are
ineligible).

### Step 3.5: State Layer

**If the taxpayer is a California resident, always run the CA comparison — never present federal-only results to a CA filer.**

```bash
python ./scripts/tax_calculator.py --gross-income <amt> --filing-status <status> --year <year> \
    --state CA --hsa-contributions <amt> --qbi-deduction <amt> --obbba-deductions <amt>
```

The final summary must contain a **`CA impact`** section that explicitly lists every non-conformity adjustment applied, because California diverges from federal law in ways that change the answer materially:

- **HSA contributions are not deductible** for CA and HSA earnings are CA-taxable annually (added back).
- **No QBI deduction** — CA has no IRC §199A.
- **No OBBBA deductions** — CA's conformity date is 2015-01-01, so tips / overtime / senior / car-loan interest deductions are added back.
- **Capital gains and qualified dividends are taxed as ordinary income** — there is no preferential CA rate.
- **CA itemize-vs-standard is a separate decision** — the CA standard deduction is roughly a third of the federal one, and state income tax is not deductible on the CA return.
- **RSU vesting** is withheld at CA's 10.23% supplemental rate, plus uncapped 1.2% SDI, but taxed on the return at progressive rates.

Full detail, including the worked example, is in `./references/california_tax_guide.md`.

For states with no income tax (WA, TX, FL, NV) say so explicitly. For other states the constants carry only a **top marginal rate flagged `estimate_only`** — never present it as an effective rate.

**CA freelancers / uneven-income filers:** also run `estimated_tax_calculator.py --state CA --projected-ca-tax … --prior-year-ca-tax … --ca-agi …` — the federal quarterly schedule alone is incomplete; CA uses **30/40/0/30** installments (no Q3 payment) and **bars the prior-year safe harbor at $1M+ CA AGI** ($500k MFS), forcing 90% of the current year. See `./references/california_tax_guide.md` §8.

### Step 4: Handle Complex Compensation (RSU/ESPP)
**Prerequisite**: Vesting dates and FMVs must be verified against statements.
```bash
# Calculate RSU cost basis correction
python ./scripts/rsu_calculator.py basis --shares-vested <shares> --fmv <fmv>
```

### Step 5: Draft Return Review
**Workflow:**
1. **Accept the draft**: Read the PDF draft return.
2. **Automated Review**: Run the cross-check tool.
   ```bash
   python ./scripts/draft_reviewer.py --draft <extracted_draft.json> --computed <your_verified_data.json> --year 2025
   ```
3. **Checklist Validation**: Manually verify items in `./references/draft_review_checklist.md` that require human judgment.

> **Excess SDI (multi-employer, CA):** for a capped SDI year (2023 and earlier),
> two or more employers whose combined SDI/VPDI withholding exceeds `rate ×
> wage_cap` claim the excess on Form 540 line 74. For 2024+ (SB 951 removed the
> wage cap) the credit cannot arise — do not flag a missing line 74. The
> reviewer's `check_excess_sdi` handles this automatically for CA returns.

### Step 6: FBAR (foreign accounts)

**Trigger**: any foreign financial account — bank, brokerage, e-money wallet or
credit card held outside the US — regardless of whether it produced taxable
income. Schedule B Part III answering "Yes" is a trigger, not the test.

FBAR is filed **separately from the return**, through FinCEN's BSA E-Filing
system, and is due April 15 with an automatic extension to October 15.

**Workflow:**
0. **Parse the statements** (Phase 0, optional): where the account has `.xlsx`
   or plain-text statements, run them through the parser first rather than
   reading balances by hand. It emits balance timelines with a **derived**
   `max_value_basis`, so V12 in step 3 warns about the evidence rather than
   about someone's typing.
   ```bash
   python ./scripts/statement_parser.py parse \
       --sources ./sources.json --filer primary --output-dir ./out
   ```
   `sources.json` maps files to readers and account keys and lives at the
   **workspace root beside the data**, never in this repo — see
   `./examples/statements/` for a worked corpus and `sources.json`. **PDF movement
   registers are parsed** (`pdf_movement_register`); they state transactions
   and period totals and **no balance**, so they yield movements and a
   `max_value_basis` of `estimate`, never a maximum value. Balance-bearing PDF
   statements are still read with `Read` in step 1; no profile derives a
   balance from transaction deltas. The parser
   reports what the files say and nothing more: reportability, ownership,
   conventions and the Item 15a election are all step 1. Treat an empty export
   as "no data", not as a zero balance.
1. **Build the roster** (Phase 1): read the Phase 0 timelines (where they
   exist), each account's remaining statements and the
   prior-year filed Form 114 PDF, and emit
   `fbar_roster_<filer_slug>_<year>.json` per the schema in
   `./scripts/fbar_schema.py`. Extract prior-year values from the filed form's
   Part II Continuation Statement, never re-quoted from workpaper prose. Confirm
   the roster with the user (Phase 2 gate) before running anything.
2. **Reconcile against the prior year** (Phase 3):
   ```bash
   python ./scripts/fbar_reconciler.py reconcile \
       --current ./fbar_roster_primary_2025.json \
       --prior   ./fbar_roster_primary_2024.json \
       --output-dir ./out
   ```
   First cycle, with no prior roster: `validate --roster <file>`.
3. **Resolve blocking findings.** The tool exits non-zero with `isError: true`
   when a prior-year account has no disposition (`UNRESOLVED`), a current-year
   account has no explanation (`NEW_UNEXPLAINED`), or a reporting-model change
   carries no written justification. Each is a question only the taxpayer can
   answer — surface it, do not guess.
4. **Manual checklist**: work through §10 of `./references/fbar_guide.md`.

> **Separate FBARs.** Spouses file one FBAR only when **all** accounts are
> jointly owned and both sign Form 114a. Any separately-owned account forces two
> filings. Note that Form 114a's *third-party preparer* authorization (Form 114
> box 44a) is a different, unrelated use of the same form and does not affect
> this.

> **Item 15a "amount unknown" is a legitimate filing position**, not a defect —
> available to filers with fewer than 25 accounts. The tool warns so it does not
> ride along unresolved for years; it never blocks.

> **Exchange rates**: the Treasury Reporting Rate of Exchange for the **last day
> of the calendar year** — not the rate on the date the account peaked, and not a
> commercial mid-market rate.

**This produces a keying worksheet, never a filed form.** Transmission goes
through the taxpayer or their preparer. Form 8938 (FATCA), Part IV
(signature-authority-only) and Part V (consolidated) are out of scope — see
`./docs/limitations.md`.

---

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `./scripts/tax_calculator.py` | Core tax liability calculation |
| `./scripts/deduction_analyzer.py` | Standard vs itemized comparison |
| `./scripts/estimated_tax_calculator.py` | Quarterly payment calculations |
| `./scripts/rsu_calculator.py` | RSU cost basis, withholding, and 8949 adjustments |
| `./scripts/espp_calculator.py` | ESPP disposition and cost basis analysis |
| `./scripts/draft_reviewer.py` | Cross-check draft returns against verified data |
| `./scripts/credit_eligibility_checker.py` | Tax-credit eligibility & amount checker (CTC, EITC, CDCC, AOTC, Saver's) incl. LLC, no-1116 FTC, and 2023–2025 clean-energy credits (30D/25E/25C/25D). `--year` switches the constants; vehicle credits enforce the OBBBA 2025-09-30 cut-off by purchase date and the §30D/§25E lesser-of-current/prior-MAGI rule; 2026 returns "not available" for energy credits rather than raising. |
| `./scripts/qbi_calculator.py` | §199A QBI deduction (Form 8995/8995-A): adjusted QBI, SSTB phase-out, wage/UBIA limit, REIT/PTP component, overall taxable-income limit, negative-QBI carryforward, and the 2026 OBBBA $400 minimum. Sole-prop/Schedule-C + 1099-DIV Box 5 only — K-1 pass-throughs are rejected (see `./docs/limitations.md`). |
| `./scripts/fbar_reconciler.py` | FinCEN 114 (FBAR) year-over-year reconciliation: matches accounts across years through renumbering, splits, merges and Cyrillic/Latin homoglyph drift; forces every prior-year account to an explicit disposition; runs validators V1–V13 (Treasury FX provenance, USD recomputation, $10,000 threshold arithmetic, 25-account rule, addresses, Item 15a carry-forward, Form 114a spousal scope, retention); emits a reconciliation report, a Form 114 keying worksheet, and next year's roster. Part II/III only. See `./references/fbar_guide.md`. |
| `./scripts/statement_parser.py` | FBAR **Phase 0**: parses `.xlsx`, plain-text and PDF-register bank statements into per-account balance timelines. Per-source readers (running balance, two-sheet credit card, period-end summary, PDF movement register) driven by declarative profiles and an explicit `sources.json` — never by sniffing file contents. Merges multi-currency sub-accounts onto one time axis and converts at the December 31 Treasury rate, so the maximum is the peak of the *combined* series rather than the sum of per-currency maxima. Detects coverage gaps and continuity breaks, reports an unknowable intraday order or an unobserved currency as a *bound* rather than a figure, and **derives** `max_value_basis` from the evidence found. Emits signed per-currency components with no convention applied, and no bare maximum for a liability. **No balance is ever synthesized from transaction deltas** — a PDF movement register states no balance, so it yields movements and an `estimate` basis, and balance-bearing PDFs stay with `Read`. Decides nothing about reportability, ownership, conventions or Item 15a. See `./examples/statements/README.md`. |
| `./scripts/statement_profiles.py` | Declarative column maps for the statement parser, plus the institutions the corpus cannot specify a profile for and why |
| `./scripts/fbar_schema.py` | FBAR roster schema, enums, loader and the account-number normalizer (matching only — never for display) |
| `./scripts/tax_savings_finder.py` | Proactive savings engine — reads a flat taxpayer profile and emits ranked opportunities (M1–M12: 401(k) headroom, backdoor Roth + pro-rata, mega backdoor, tax-loss harvesting, appreciated-stock donation, QCD, SE health insurance, §179/bonus, HSA, OBBBA senior bonus, tips/overtime, home office (M12)) with dollar estimates, deadlines, and CA nonconformity caveats. See `./references/tax_planning.md`. |

---

## Reference Documents

Load these when specific technical guidance is needed beyond built-in knowledge.

| Reference | Contents |
|-----------|----------|
| `./references/tax_brackets_deductions.md` | Current year brackets, standard deductions, contribution limits |
| `./references/credits_guide.md` | Eligibility and calculations for major tax credits |
| `./references/investment_taxes.md` | Capital gains, wash sales, RSU/ESPP treatment |
| `./references/form_processing.md` | **Mandatory field-mapping** for extraction |
| `./references/few_shots.md` | **Few-shot examples** for ambiguous form extraction |
| `./references/draft_review_checklist.md` | Validation rules for draft returns |
| `./references/fbar_guide.md` | FinCEN 114 rules, roster schema, dispositions, validators, and the manual pre-filing checklist |
| `./docs/limitations.md` | **Out-of-scope scenarios** (AMT, K-1s, international) |

### Optional Employer-Specific References

These files may or may not be present. Check with `Glob` before attempting to read — silently skip if absent.

| Reference | Contents |
|-----------|----------|
| `./references/rsu/*_rsu_specifics.md` (user-supplied) | Employer-specific equity-plan details — LTI/ESPP terms, brokerage specifics, withholding rates, Form 8949 adjustment patterns. Not distributed; present only if the filer has added one. |

If such a file is present, load it whenever the user mentions their employer's equity plan by name, their equity brokerage or plan portal, or their employer RSUs or ESPP — it contains employer-specific plan terms, withholding rates, and Form 8949 adjustment examples that override the generic RSU guidance.

---

## Limitations

This skill provides tax preparation assistance but does not replace professional tax advice. See `./docs/limitations.md` for a list of unsupported complex tax scenarios (e.g., K-1s, AMT, Form 2555).

Tax laws change frequently. Verify all information against current IRS publications. This skill's inline figures use the tax year specified in the metadata `tax-year` field. If the user is filing for a different year, check `./references/tax_brackets_deductions.md` and use WebSearch for the latest adjustments.
