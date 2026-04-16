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
  version: 2.2.0
  last-updated: 2026-04-13
  target-users: individuals, families, self-employed
  tax-year: 2025
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

> **Estimated payment safe harbor**: Use `estimated_tax_calculator.py` for quarterly payment amounts. The IRS safe harbor is the **lesser of** (a) 90% of current-year projected tax or (b) 100% of prior-year tax (110% if prior-year AGI > $150,000). Always compute both and present the lower figure to minimize required payments.

### Step 3: Determine Deductions
Compare standard deduction against itemized total:
```bash
python ./scripts/deduction_analyzer.py --agi <confirmed_agi> --mortgage-interest <amount> --charitable-cash <amount> --year 2025
```

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
| `./docs/limitations.md` | **Out-of-scope scenarios** (AMT, K-1s, international) |

### Optional Employer-Specific References

These files may or may not be present. Check with `Glob` before attempting to read — silently skip if absent.

| Reference | Contents |
|-----------|----------|
| `./references/rsu/epam_rsu_specifics.md` | EPAM LTI/ESPP plan details, UBS brokerage specifics, withholding rates, Form 8949 adjustment patterns |

If present, load this file whenever the user mentions EPAM, UBS One Source, EPAM RSUs, or EPAM ESPP — it contains employer-specific plan terms, withholding rates, and Form 8949 adjustment examples that override the generic RSU guidance.

---

## Limitations

This skill provides tax preparation assistance but does not replace professional tax advice. See `./docs/limitations.md` for a list of unsupported complex tax scenarios (e.g., K-1s, AMT, Form 2555).

Tax laws change frequently. Verify all information against current IRS publications. This skill's inline figures use the tax year specified in the metadata `tax-year` field. If the user is filing for a different year, check `./references/tax_brackets_deductions.md` and use WebSearch for the latest adjustments.
