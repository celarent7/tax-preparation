# Tax Form Draft Review Checklist

Comprehensive validation rules for reviewing a draft tax return before filing. Use alongside `draft_reviewer.py` for automated cross-checking against computed data.

---

## Form 1040 — Line-by-Line Validation

### Income Section (Lines 1–9)

| Line | Field | Validation Rule |
|------|-------|-----------------|
| 1a | Wages, salaries, tips | Must equal sum of all W-2 Box 1 amounts |
| 2a | Tax-exempt interest | Must equal sum of all 1099-INT Box 8 |
| 2b | Taxable interest | Must equal sum of all 1099-INT Box 1 |
| 3a | Qualified dividends | Must equal sum of all 1099-DIV Box 1b |
| 3b | Ordinary dividends | Must equal sum of all 1099-DIV Box 1a |
| 4a | IRA distributions (gross) | Must equal sum of all 1099-R Box 1 (IRA codes) |
| 4b | IRA distributions (taxable) | Must equal sum of all 1099-R Box 2a (IRA codes) |
| 5a | Pensions/annuities (gross) | Must equal sum of all 1099-R Box 1 (pension codes) |
| 5b | Pensions/annuities (taxable) | Must equal sum of all 1099-R Box 2a (pension codes) |
| 6a | Social Security (gross) | Must equal SSA-1099 Box 5 |
| 6b | Social Security (taxable) | Verify calculation: 0%, 50%, or 85% based on combined income |
| 7 | Capital gain or loss | Must equal Schedule D Line 21 (or 16 if no Schedule D required) |
| 8 | Other income | Sum of Schedule 1 additional income minus adjustments |
| 9 | Total income | Sum of lines 1a through 8 |

### Adjustments and Deductions (Lines 10–15)

| Line | Field | Validation Rule |
|------|-------|-----------------|
| 10 | Adjustments to income | Must equal Schedule 1 Part II total |
| 11 | Adjusted Gross Income | Line 9 minus Line 10 |
| 12 | Standard or itemized deduction | Must match correct amount for filing status OR Schedule A total |
| 13 | Qualified business income deduction | Verify Section 199A calculation if applicable |
| 14 | Total deductions | Line 12 plus Line 13 |
| 15 | Taxable income | Line 11 minus Line 14 (not less than zero) |

### Tax and Credits (Lines 16–24)

| Line | Field | Validation Rule |
|------|-------|-----------------|
| 16 | Tax | Must match tax table/schedule for taxable income and filing status |
| 17 | Amount from Schedule 2 Part I | AMT + excess premium tax credit repayment |
| 18 | Sum of 16 and 17 | Math check |
| 19 | Child/dependent credits | Verify eligibility and phaseout |
| 20 | Amount from Schedule 3 Part I | Other nonrefundable credits |
| 21 | Sum of 19 and 20 | Math check |
| 22 | Line 18 minus Line 21 | Must not be less than zero |
| 23 | Other taxes (Schedule 2 Part II) | SE tax, additional Medicare, etc. |
| 24 | Total tax | Line 22 plus Line 23 |

### Payments (Lines 25–33)

| Line | Field | Validation Rule |
|------|-------|-----------------|
| 25a | W-2 federal withholding | Must equal sum of all W-2 Box 2 |
| 25b | 1099 federal withholding | Must equal sum of all 1099 Box 4 amounts |
| 25c | Other withholding | Verify source |
| 25d | Total withholding | Sum of 25a + 25b + 25c |
| 26 | Estimated tax payments | Must match IRS records (Form 1040-ES payments) |
| 27 | Earned Income Credit | Verify eligibility, income limits, qualifying children |
| 28 | Additional Child Tax Credit | Verify calculation |
| 29 | American Opportunity Credit | Verify Form 8863 |
| 30 | Recovery rebate credit | Usually zero for current years |
| 31 | Amount from Schedule 3 Part II | Other refundable credits |
| 32 | Sum of 27–31 | Math check |
| 33 | Total payments | Line 25d + 26 + 32 |

### Refund or Amount Owed (Lines 34–37)

| Line | Field | Validation Rule |
|------|-------|-----------------|
| 34 | Overpayment | Line 33 minus Line 24 (if 33 > 24) |
| 35a | Refund amount | Must equal Line 34 minus Line 36 |
| 36 | Applied to next year | If applicable, verify amount |
| 37 | Amount you owe | Line 24 minus Line 33 (if 24 > 33) |

---

## Schedule A — Itemized Deductions

| Line | Category | Validation Rule |
|------|----------|-----------------|
| 1 | Medical/dental expenses | Verify total from receipts |
| 4 | Medical deduction | Line 1 minus 7.5% of AGI (Line 2 × 0.075); cannot be negative |
| 5a | State/local income taxes | Verify from W-2 Box 17, state estimated payments, prior year refund |
| 5b | State/local sales taxes | Alternative to 5a — cannot claim both |
| 5c | Real estate taxes | Verify from property tax bills |
| 5d | Personal property taxes | If applicable |
| 5e | Total SALT | Sum of chosen option |
| **6** | **SALT deduction** | **MUST be capped at $10,000 ($5,000 MFS)** — most common error |
| 8a | Home mortgage interest | Must match 1098 Box 1 |
| 10 | Mortgage interest points | Must match 1098 Box 6 |
| 11 | Investment interest | Subject to net investment income limitation |
| 12 | Gifts to charity (cash) | Verify receipts; 60% AGI limit |
| 13 | Gifts to charity (non-cash) | Verify appraisals if >$5,000; 30% AGI limit |
| 14 | Carryover from prior year | Must match prior year Schedule A |
| 17 | Total itemized deductions | Sum of all categories |

---

## Schedule B — Interest and Dividends

- Verify every 1099-INT and 1099-DIV is listed
- Check for missing accounts (compare to prior year)
- If total exceeds $1,500, Part III (foreign accounts) questions must be answered
- FBAR/FATCA: If "Yes" to foreign accounts, verify FBAR filing requirement

---

## Schedule C — Business Income

| Check | Rule |
|-------|------|
| Gross receipts | Must match sum of all 1099-NEC + 1099-K + unreported income |
| Expense categories | Each must have substantiation |
| Home office | Simplified ($5/sq ft × up to 300) or actual expenses — not both |
| Vehicle | Standard mileage (67¢/mile 2024) or actual — not both |
| Net profit/loss | Gross income minus total expenses |
| Reasonable test | Expense-to-income ratio > 80% may trigger audit flags |

---

## Schedule D / Form 8949 — Capital Gains

| Check | Rule |
|-------|------|
| Short-term totals | Must match sum of all 1099-B short-term transactions |
| Long-term totals | Must match sum of all 1099-B long-term transactions |
| Basis adjustments | Every code "B" adjustment must have supporting documentation |
| RSU cost basis | Verify NO entries show $0 basis (unless truly $0) |
| Wash sales | Verify disallowed amounts match 1099-B wash sale columns |
| Capital loss limit | Net loss capped at $3,000 ($1,500 MFS) |
| Carryforward | Excess loss must carry to next year |

---

## Schedule SE — Self-Employment Tax

| Check | Rule |
|-------|------|
| Net earnings | Must match Schedule C net profit (+ any K-1 SE income) |
| 92.35% factor | SE tax base = net earnings × 0.9235 |
| Social Security portion | 12.4% up to wage base ($168,600 for 2024, minus W-2 SS wages) |
| Medicare portion | 2.9% on all SE income, no cap |
| Additional Medicare | 0.9% if combined wages + SE income > $200k ($250k MFJ) |
| Deduction | Half of SE tax goes to Schedule 1 Line 15 |

---

## Top 20 Common Filing Errors

| # | Error | Detection Rule | Severity |
|---|-------|---------------|----------|
| 1 | RSU cost basis reported as $0 | 1099-B basis = 0 for RSU sales | 🔴 ERROR |
| 2 | SALT cap not applied | Schedule A Line 6 > $10,000 | 🔴 ERROR |
| 3 | Wrong filing status | MFS when MFJ would save money (or vice versa) | 🟡 WARNING |
| 4 | Missing income source | 1099 in documents but not on return | 🔴 ERROR |
| 5 | Math error in totals | Any line that should be a sum doesn't match | 🔴 ERROR |
| 6 | Missing estimated payments | Payments made but not listed on Line 26 | 🔴 ERROR |
| 7 | Standard deduction amount wrong | Doesn't match filing status / age / blind | 🔴 ERROR |
| 8 | Missing Schedule B Part III | Foreign accounts but Part III not completed | 🟡 WARNING |
| 9 | Charitable deduction without AGI limit | Cash >60% AGI or property >30% AGI | 🔴 ERROR |
| 10 | Missing Schedule SE | Self-employment income but no SE tax | 🔴 ERROR |
| 11 | Wrong tax bracket calculation | Tax on Line 16 doesn't match tables | 🔴 ERROR |
| 12 | Credit phaseout not applied | Income exceeds phaseout but full credit claimed | 🔴 ERROR |
| 13 | Missing state withholding on federal | W-2 Box 17 not captured for SALT deduction | 🟡 WARNING |
| 14 | Duplicate income entry | Same 1099 entered twice | 🔴 ERROR |
| 15 | Missing dependent SSN | Dependent claimed without SSN | 🔴 ERROR |
| 16 | HSA excess contribution | Contribution exceeds limit ($4,150/$8,300 for 2024) | 🟡 WARNING |
| 17 | Wrong holding period for RSUs | Grant date used instead of vesting date | 🟡 WARNING |
| 18 | Missing QBI deduction | Eligible but not claimed | 🟡 WARNING |
| 19 | Educator expense over limit | Exceeds $300 per educator | 🟡 WARNING |
| 20 | Student loan interest over limit | Exceeds $2,500 or income phaseout | 🟡 WARNING |

---

## Cross-Form Consistency Checks

These checks validate that values are consistent across multiple forms and schedules.

| Source | Destination | Rule |
|--------|-------------|------|
| W-2 Box 1 (all) | 1040 Line 1a | Must be equal |
| W-2 Box 2 (all) | 1040 Line 25a | Must be equal |
| W-2 Box 12 Code D | Not on 1040 | 401(k) is pre-tax; already excluded from Box 1 |
| W-2 Box 12 Code W | 1040 Schedule 1 or 8889 | HSA employer contribution |
| 1099-INT Box 1 (all) | Schedule B Part I total | Must be equal |
| 1099-DIV Box 1a (all) | Schedule B Part II total | Must be equal |
| 1099-B totals | Schedule D / Form 8949 | Proceeds and basis must match |
| 1098 Box 1 | Schedule A Line 8a | Must be equal (if itemizing) |
| Schedule C net profit | Schedule SE Line 2 | Must be equal |
| Schedule SE Line 13 | Schedule 1 Line 15 | Half of SE tax = deductible portion |
| Schedule D Line 21 | 1040 Line 7 | Capital gain/loss to main form |
| Schedule A Line 17 | 1040 Line 12 | If itemizing, must be equal |
| Prior year capital loss carryforward | Schedule D Line 6/14 | Must match prior year worksheet |

---

## Draft Input Format

When the user provides a draft return for review, extract values into this JSON structure:

```json
{
  "tax_year": 2024,
  "filing_status": "married_jointly",
  "draft_source": "TurboTax PDF",
  "form_1040": {
    "line_1a_wages": 150000,
    "line_2b_taxable_interest": 500,
    "line_3a_qualified_dividends": 1200,
    "line_3b_ordinary_dividends": 1800,
    "line_7_capital_gain_loss": 5000,
    "line_8_other_income": 0,
    "line_9_total_income": 157300,
    "line_10_adjustments": 3000,
    "line_11_agi": 154300,
    "line_12_deduction": 29200,
    "line_13_qbi_deduction": 0,
    "line_15_taxable_income": 125100,
    "line_16_tax": 18832,
    "line_19_child_credits": 4000,
    "line_24_total_tax": 18832,
    "line_25a_w2_withholding": 22000,
    "line_25d_total_withholding": 22000,
    "line_26_estimated_payments": 0,
    "line_33_total_payments": 22000,
    "line_34_overpayment": 3168,
    "line_35a_refund": 3168
  },
  "schedule_a": null,
  "schedule_b": {
    "interest_payers": ["Bank of America"],
    "dividend_payers": ["Vanguard Total Stock"],
    "foreign_accounts": false
  },
  "schedule_d": {
    "short_term_gain_loss": 2000,
    "long_term_gain_loss": 3000,
    "total_gain_loss": 5000
  }
}
```

Use the Read tool to extract these values from a PDF draft, or ask the user to provide them directly. The `draft_reviewer.py` script accepts this format.

---

## Tax Review Letter Template

When the user requests a formal Tax Review Letter, generate it using the following structure. Replace all `<placeholders>` with actual data from the review. Adjust sections based on what was actually found — omit empty sections.

**File naming**: `tax_review_letter_<YYYY>.md` (e.g., `tax_review_letter_2024.md`)

---

```markdown
# Tax Return Review — <TAX_YEAR> Federal Filing

**Prepared for**: <TAXPAYER_NAME>
**Filing Status**: <FILING_STATUS>
**Date of Review**: <REVIEW_DATE>
**Reviewed by**: AI-assisted analysis (Claude)

---

## Summary

This letter summarizes the findings from an automated and manual review of the
<TAX_YEAR> draft federal tax return (<DRAFT_SOURCE>). The review cross-checked
the draft against source documents (W-2s, 1099s, and other tax forms) and
validated calculations against IRS rules and rate tables.

**Overall result**: <ERRORS_FOUND | NO_ERRORS_FOUND>

| Category | Count |
|----------|-------|
| 🔴 Errors (must fix) | <N> |
| 🟡 Warnings (verify) | <N> |
| 🟢 Validated OK | <N> |
| ℹ️ Informational | <N> |

---

## Errors Requiring Correction

<For each ERROR finding, include:>

### <N>. <FIELD_NAME>
- **Issue**: <DESCRIPTION>
- **Draft value**: <DRAFT_VALUE>
- **Expected value**: <COMPUTED_VALUE>
- **How to fix**: <SPECIFIC_CORRECTION_INSTRUCTIONS>
- **IRS reference**: <FORM_AND_LINE_OR_PUBLICATION>

---

## Warnings — Please Verify

<For each WARNING finding, include:>

### <N>. <FIELD_NAME>
- **Concern**: <DESCRIPTION>
- **Draft value**: <DRAFT_VALUE>
- **Recommendation**: <WHAT_TO_CHECK>

---

## Items Validated Successfully

The following items were cross-checked and confirmed correct:

<Bulleted list of all OK findings, e.g.:>
- ✅ Total income (Line 9): $<AMOUNT> matches source documents
- ✅ Standard deduction: $<AMOUNT> correct for <FILING_STATUS>
- ✅ Withholding (Line 25a): $<AMOUNT> matches W-2 Box 2
- ✅ Refund calculation: $<AMOUNT> verified

---

## Informational Notes

<For each INFO finding, include a brief note, e.g.:>
- FBAR: Foreign accounts indicated on Schedule B. Verify FinCEN 114 is filed separately.
- RSU income: $<AMOUNT> in vesting income verified against W-2 Box 1.

---

## Scope of Review

This review covered:
- [ ] Form 1040 income lines against W-2/1099 source documents
- [ ] Deduction method and amount validation
- [ ] Tax computation against <TAX_YEAR> bracket tables
- [ ] Withholding and payment reconciliation
- [ ] Schedule D / Form 8949 (capital gains, cost basis)
- [ ] Schedule SE (self-employment tax) — if applicable
- [ ] Schedule B Part III (foreign accounts) — if applicable
- [ ] Cross-form consistency checks
- [ ] Common filing error patterns (Top 20)

**Not covered**: State returns, AMT calculations, complex entity structures,
audit risk assessment, or tax planning recommendations.

---

## Disclaimer

This review was performed using AI-assisted analysis tools and verified against
IRS publications and rate tables for tax year <TAX_YEAR>. It is intended as a
quality-check supplement and does not constitute professional tax advice. The
taxpayer and their licensed tax preparer (CPA/EA) bear final responsibility for
the accuracy of the filed return. We recommend reviewing all flagged items with
your tax professional before filing.
```

