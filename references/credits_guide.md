# Tax Credits Comprehensive Guide

Detailed eligibility requirements and calculations for major federal tax credits.

## Child Tax Credit (CTC)

### Basic Requirements
- Child must be under age 17 at end of tax year
- Child must be a qualifying child (relationship, residency, support tests)
- Child must have valid Social Security Number (SSN)
- Child must be claimed as dependent on your return

### Credit Amount
- **Full credit**: $2,000 per qualifying child
- **Refundable portion (ACTC)**: Up to $1,700 per child

### Income Phaseouts (2024)
| Filing Status | Phaseout Begins | Phaseout Rate |
|---------------|-----------------|---------------|
| Single | $200,000 | $50 per $1,000 |
| Married Filing Jointly | $400,000 | $50 per $1,000 |
| Married Filing Separately | $200,000 | $50 per $1,000 |
| Head of Household | $200,000 | $50 per $1,000 |

### Refundable Portion Calculation
1. Calculate earned income over $2,500
2. Multiply by 15%
3. Refundable amount is lesser of: result, $1,700 per child, or unused CTC

### Credit for Other Dependents
- $500 for dependents who don't qualify for CTC
- Includes: Children 17+, elderly parents, other qualifying relatives
- Same income phaseouts as CTC

---

## Earned Income Tax Credit (EITC)

### Eligibility Requirements
- Must have earned income (wages, self-employment, etc.)
- AGI must be below threshold for filing status and number of children
- Investment income must not exceed $11,600 (2024)
- Must have valid SSN for yourself, spouse, and qualifying children
- Cannot file Form 2555 (Foreign Earned Income)
- Cannot be a qualifying child of another taxpayer

### Qualifying Child Requirements
- **Age**: Under 19, or under 24 if full-time student, or any age if permanently disabled
- **Relationship**: Son, daughter, stepchild, foster child, sibling, or descendant
- **Residency**: Lived with you in US for more than half the year
- **Joint return**: Cannot file joint return (unless only to claim refund)

### Credit Amounts (2024)
| Children | Maximum Credit | Earned Income for Max | Phaseout Begins (Single) | Phaseout Begins (MFJ) |
|----------|----------------|----------------------|-------------------------|----------------------|
| 0 | $632 | $7,830 | $9,800 | $16,370 |
| 1 | $4,213 | $12,390 | $22,720 | $29,640 |
| 2 | $6,960 | $17,400 | $22,720 | $29,640 |
| 3+ | $7,830 | $17,400 | $22,720 | $29,640 |

### Calculation Method
1. Find earned income and AGI
2. Use lower of earned income or AGI for phaseout calculation
3. Calculate credit from IRS EITC table or formula
4. Apply phaseout reduction if income exceeds threshold

### Special Rules
- **No qualifying children**: Must be age 25-64 (19-24 if former foster youth or homeless youth)
- **Combat pay election**: Can choose to include or exclude nontaxable combat pay
- **Married Filing Separately**: Generally ineligible (exception for victims of domestic abuse)

---

## Child and Dependent Care Credit

### Qualifying Expenses
- Care for qualifying person while you work or look for work
- Includes: Daycare, preschool, babysitters, day camp, before/after school programs
- Does NOT include: Overnight camp, school tuition (K-12+), food, clothing, entertainment

### Qualifying Persons
- **Dependent child**: Under age 13 when care provided
- **Disabled spouse**: Physically or mentally incapable of self-care
- **Disabled dependent**: Any age, incapable of self-care, lived with you more than half year

### Expense Limits
- One qualifying person: $3,000
- Two or more qualifying persons: $6,000
- Reduced by employer-provided dependent care benefits (Form 2441)

### Credit Percentage
| AGI | Credit Percentage |
|-----|-------------------|
| $0 - $15,000 | 35% |
| $15,001 - $17,000 | 34% |
| $17,001 - $19,000 | 33% |
| $19,001 - $21,000 | 32% |
| $21,001 - $23,000 | 31% |
| $23,001 - $25,000 | 30% |
| $25,001 - $27,000 | 29% |
| $27,001 - $29,000 | 28% |
| $29,001 - $31,000 | 27% |
| $31,001 - $33,000 | 26% |
| $33,001 - $35,000 | 25% |
| $35,001 - $37,000 | 24% |
| $37,001 - $39,000 | 23% |
| $39,001 - $41,000 | 22% |
| $41,001 - $43,000 | 21% |
| Over $43,000 | 20% |

### Calculation Example
- 2 children in daycare, $12,000 total expenses
- AGI: $50,000
- Expense limit: $6,000 (capped)
- Credit percentage: 20%
- Credit: $6,000 x 20% = $1,200

### Requirements
- Must report care provider's name, address, and SSN/EIN on Form 2441
- Must have earned income (both spouses if married)
- If married, must file jointly (few exceptions)

---

## American Opportunity Tax Credit (AOTC)

### Eligibility Requirements
- Student enrolled at least half-time in degree program
- First four years of higher education only
- Student has not completed four years of higher education before tax year
- No felony drug conviction
- Student claimed as dependent OR claiming for self/spouse

### Qualifying Expenses
- Tuition and required fees
- Books, supplies, and equipment required for courses
- Does NOT include: Room and board, transportation, insurance, student activities

### Credit Calculation
- 100% of first $2,000 of expenses = $2,000
- 25% of next $2,000 of expenses = $500
- **Maximum credit**: $2,500 per student

### Refundable Portion
- 40% of credit is refundable (up to $1,000)
- Not refundable if: Under 24 with unearned income over $2,600, claiming as own dependent

### Income Phaseouts (2024)
| Filing Status | Full Credit | Phaseout Range | No Credit |
|---------------|-------------|----------------|-----------|
| Single | Under $80,000 | $80,000 - $90,000 | Over $90,000 |
| MFJ | Under $160,000 | $160,000 - $180,000 | Over $180,000 |

### Documentation Required
- Form 1098-T from institution
- Receipts for books and supplies
- Enrollment verification

---

## Lifetime Learning Credit

> **Tool-supported.** `scripts/credit_eligibility_checker.py` computes the LLC —
> 20% of up to $10,000 of expenses, the shared MAGI phase-out, the MFS bar, and
> the AOTC same-student exclusion — alongside the other credits. Supply the
> per-return expense amount and (if applicable) the AOTC-exclusion flag through a
> `--data-file` JSON with `education_expenses_llc` and optional
> `aotc_claimed_for_same_student`; the checker reports it in the same run as the
> other credits. (The `--education-expenses` CLI flag drives the AOTC, not the
> LLC — the two credits use separate expense keys because the LLC is per-return
> while the AOTC is per-student.)

### Eligibility Requirements
- Student enrolled in eligible institution (accredited college, university, vocational school)
- Taking courses to acquire/improve job skills OR as part of degree program
- No felony drug conviction
- Not claimed for same student as AOTC in same year

### Qualifying Expenses
- Tuition and required fees
- Books and supplies only if required to be purchased from institution
- Does NOT include: Room and board, transportation, insurance

### Credit Calculation
- 20% of up to $10,000 qualifying expenses
- **Maximum credit**: $2,000 per tax return (not per student)
- Nonrefundable

### Income Phaseouts (2024)
| Filing Status | Full Credit | Phaseout Range | No Credit |
|---------------|-------------|----------------|-----------|
| Single | Under $80,000 | $80,000 - $90,000 | Over $90,000 |
| MFJ | Under $160,000 | $160,000 - $180,000 | Over $180,000 |

### AOTC vs Lifetime Learning Comparison
| Feature | AOTC | Lifetime Learning |
|---------|------|-------------------|
| Maximum credit | $2,500/student | $2,000/return |
| Refundable | Yes (40%) | No |
| Years available | First 4 years | Unlimited |
| Enrollment | Half-time minimum | One or more courses |
| Degree required | Yes | No |

---

## Saver's Credit (Retirement Savings Contributions Credit)

### Eligibility Requirements
- Age 18 or older by end of tax year
- Not a full-time student
- Not claimed as dependent on another return
- Made contributions to: 401(k), 403(b), 457, IRA, SIMPLE IRA, SEP

### Credit Rates (2024)
| Filing Status | 50% Credit | 20% Credit | 10% Credit | No Credit |
|---------------|------------|------------|------------|-----------|
| Single | $0 - $23,000 | $23,001 - $25,000 | $25,001 - $38,250 | Over $38,250 |
| Head of Household | $0 - $34,500 | $34,501 - $37,500 | $37,501 - $57,375 | Over $57,375 |
| MFJ | $0 - $46,000 | $46,001 - $50,000 | $50,001 - $76,500 | Over $76,500 |

### Credit Calculation
- Maximum contribution eligible: $2,000 ($4,000 MFJ)
- Maximum credit: $1,000 ($2,000 MFJ)
- Reduce by distributions from retirement accounts in testing period

### Testing Period for Distributions
Reduce credit by distributions received:
- During tax year
- During previous 2 tax years
- Before due date of return for tax year

---

---

## ⚠️ OBBBA clean-energy credit terminations <!-- YEAR -->2025<!-- /YEAR -->

**These credits have been terminated on an accelerated schedule.** Every
clean-energy credit described in this file is subject to a hard cut-off. Check
the date before telling a taxpayer they qualify — and for a return being
prepared now, check whether they *missed* one.

| Credit | IRC § | Terminated for |
|---|---|---|
| New Clean Vehicle Credit | §30D | vehicles **acquired after 2025-09-30** |
| Used Clean Vehicle Credit | §25E | vehicles **acquired after 2025-09-30** |
| Qualified Commercial Clean Vehicle Credit | §45W | vehicles **acquired after 2025-09-30** |
| Energy Efficient Home Improvement Credit | §25C | property **placed in service after 2025-12-31** |
| Residential Clean Energy Credit | §25D | expenditures **made after 2025-12-31** |

**"Acquired" for the vehicle credits** (§§25E, 30D, 45W) means the date a
*written binding contract* is entered into **and** a payment is made — including
a nominal down payment or a trade-in. A taxpayer with a binding contract and a
payment on or before 2025-09-30 may still claim the credit when the vehicle is
placed in service, even if delivery is later.

*Source: IRS, "FAQs for modification of sections 25C, 25D, 25E, 30C, 30D, 45L,
45W and 179D under Public Law 119-21 (One, Big, Beautiful Bill)",
<https://www.irs.gov/newsroom/faqs-for-modification-of-sections-25c-25d-25e-30c-30d-45l-45w-and-179d-under-public-law-119-21-139-stat-72-july-4-2025-commonly-known-as-the-one-big-beautiful-bill>
(retrieved 2026-07-30).*

**California note:** CA does not conform to these federal credits and has no
equivalent state credit; see `references/california_tax_guide.md`.

**Tool enforcement.** `scripts/credit_eligibility_checker.py` now enforces these
dates automatically for 2023–2025 returns: pass the purchase/in-service date
(`--ev-purchase-date` for the vehicle credits) and the checker marks a vehicle
acquired after 2025-09-30 (or §25C/§25D property after 2025-12-31) ineligible
with a termination note. The energy-credit dataset (`energy_credits` in
`scripts/tax_constants.json`) carries the `terminated_after` keys only in the
2025 year block; 2023/2024 ran all year and 2026 has no energy block at all, so
requesting energy credits for 2026 returns "not available" rather than raising.

---

## Residential Clean Energy Credit

### Qualifying Property (30% credit)
- Solar electric (photovoltaic) systems
- Solar water heaters
- Fuel cell property
- Small wind energy property
- Geothermal heat pumps
- Battery storage technology (3 kWh+ capacity)

### Credit Rate Schedule
| Year | Credit Rate |
|------|-------------|
| 2022-2032 | 30% |
| 2033 | 26% |
| 2034 | 22% |

### Requirements
- Property must be for residence in US
- Original use must begin with taxpayer
- Must meet performance and quality standards
- Installation costs qualify (labor, wiring, piping)

### Carryforward
- Nonrefundable credit
- Unused credit carries forward to next tax year
- No carryback

---

## Energy Efficient Home Improvement Credit

### Annual Credit Limits (2024+)
| Improvement Type | Credit | Annual Limit |
|------------------|--------|--------------|
| Total annual limit | 30% | $1,200 |
| Qualified energy property | 30% | $600 each item |
| Windows/skylights | 30% | $600 |
| Doors (exterior) | 30% | $500 total ($250/door) |
| Home energy audit | 30% | $150 |
| Heat pumps | 30% | $2,000 (separate limit) |
| Biomass stoves | 30% | $2,000 (separate limit) |

### Qualifying Improvements
- Building envelope: Insulation, windows, doors, roofs
- HVAC: Central air, furnaces, boilers, heat pumps
- Water heaters: Heat pump water heaters, gas/oil water heaters
- Home energy audits by qualified auditor

### Requirements
- Property must meet energy efficiency standards
- Must be for primary residence
- New property (not used)
- Credit resets annually (can claim each year)

---

## Foreign Tax Credit

> **Tool-supported (the no-Form-1116 election only).** When all foreign tax is
> passive-category (1099-DIV Box 7 / 1099-INT Box 6) and totals $300 or less
> ($600 MFJ), `scripts/credit_eligibility_checker.py` flags it as claimable
> directly on Schedule 3 line 1 — a commonly missed credit on international
> index-fund 1099-DIVs. Supply `foreign_tax_paid` via a `--data-file`. Above the
> election limit the checker does NOT compute the credit — it flags that Form
> 1116 is required and out of scope (see `docs/limitations.md`).

### Eligibility
- Paid or accrued foreign income tax
- Tax must be imposed on you
- Must be actual tax (not deposit or penalty)
- Must be compulsory (not voluntary)

### Calculation Methods
1. **Per-country method**: Calculate limit separately for each country
2. **Overall method**: Calculate single limit for all foreign income

### Credit Limitation
- Credit cannot exceed US tax on foreign income
- Formula: (Foreign source taxable income / Total taxable income) x US tax
- Excess credit carries back 1 year, forward 10 years

### Form 1116 vs Direct Credit
- **Under $300 ($600 MFJ)**: Can claim directly without Form 1116
- **Over threshold**: Must file Form 1116, calculate limitation

---

## Premium Tax Credit (ACA)

### Eligibility Requirements
- Enrolled in qualified health plan through Marketplace
- Not eligible for affordable employer coverage or government programs
- Household income between 100-400% of Federal Poverty Level
- File joint return if married (exception for victims of domestic abuse)

### Calculation
- Based on second-lowest-cost Silver plan (benchmark plan)
- Expected contribution = Income percentage x household income
- Credit = Benchmark premium - Expected contribution

### Income Percentages (2024)
| % of Federal Poverty Level | Expected Contribution |
|---------------------------|----------------------|
| 100-150% | 0% - 4% of income |
| 150-200% | 4% - 6.3% of income |
| 200-250% | 6.3% - 8.5% of income |
| 250-300% | 8.5% of income |
| 300-400% | 8.5% of income |
| Over 400% | 8.5% cap (no cliff) |

### Advance vs Year-End
- **Advance payments**: Paid directly to insurer, estimated monthly
- **Year-end reconciliation**: Form 8962, may owe or receive additional credit
- Report income changes to Marketplace to avoid large reconciliation

---

## Adoption Credit

### Credit Amount (2024)
- Maximum credit: $16,810 per eligible child
- Applies to qualified adoption expenses

### Qualifying Expenses
- Reasonable and necessary adoption fees
- Court costs and attorney fees
- Traveling expenses
- Other expenses directly related to adoption

### Income Phaseouts (2024)
| Income Range | Credit Treatment |
|--------------|------------------|
| Under $252,150 | Full credit |
| $252,150 - $292,150 | Phased out |
| Over $292,150 | No credit |

### Special Needs Adoption
- Full credit available regardless of actual expenses
- Child must have special needs determination from state
- Domestic adoptions only

### Carryforward
- Nonrefundable credit
- Unused credit carries forward 5 years
