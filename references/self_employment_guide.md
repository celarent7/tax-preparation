# Self-Employment Tax Guide

Comprehensive guide for self-employed individuals covering taxes, deductions, and retirement options.

## Self-Employment Tax Basics

### Who Pays Self-Employment Tax
- Sole proprietors
- Partners in partnerships
- LLC members (if taxed as sole prop or partnership)
- Independent contractors
- Gig economy workers
- Anyone with net self-employment income of $400+

### Tax Components (2024)
| Component | Rate | Wage Base |
|-----------|------|-----------|
| Social Security | 12.4% | $168,600 |
| Medicare | 2.9% | Unlimited |
| Additional Medicare | 0.9% | Over $200k single / $250k MFJ |
| **Total SE Tax** | **15.3%** | (up to SS limit) |

### Calculation Steps
1. Calculate net self-employment income (Schedule C profit or Schedule K-1)
2. Multiply by 92.35% (adjustment for employer-equivalent portion)
3. Calculate Social Security tax on amount up to wage base
4. Calculate Medicare tax on total amount
5. Deduct half of SE tax on Form 1040 Schedule 1

### Example Calculation
```
Net SE income: $100,000
SE tax base: $100,000 x 0.9235 = $92,350
Social Security: $92,350 x 12.4% = $11,451
Medicare: $92,350 x 2.9% = $2,678
Total SE tax: $14,129
Above-the-line deduction: $7,065 (half of SE tax)
```

---

## Business Deductions

### Ordinary and Necessary Standard
Deductible expenses must be:
- **Ordinary**: Common and accepted in your trade/business
- **Necessary**: Helpful and appropriate for your business

### Common Business Expense Categories

#### Office Expenses
- Office supplies (paper, pens, postage)
- Software subscriptions
- Computer equipment and peripherals
- Furniture (depreciate if over $2,500)
- Internet service (business portion)
- Phone service (business portion)

#### Professional Services
- Accounting and bookkeeping
- Legal fees (business-related)
- Consulting fees
- Freelance/contractor payments (issue 1099s!)
- Payroll services

#### Marketing and Advertising
- Website hosting and domain
- Online advertising (Google, Facebook, etc.)
- Print advertising
- Business cards and promotional materials
- Trade show expenses

#### Travel Expenses
- Airfare, train, bus
- Hotel accommodations
- Rental car
- Taxi/rideshare (business trips)
- 50% of business meals while traveling
- Tips related to deductible expenses

#### Insurance
- Business liability insurance
- Professional liability/E&O insurance
- Business property insurance
- Workers' compensation (if employees)
- Health insurance (see below)

#### Education and Training
- Courses related to current business
- Professional certifications
- Industry conferences
- Books and publications
- Professional memberships

### Non-Deductible Expenses
- Personal expenses
- Commuting costs (home to regular office)
- Political contributions
- Fines and penalties
- Clothing (unless specialty uniform)

---

## Home Office Deduction

### Qualification Requirements
- **Regular and exclusive use**: Space used only for business
- **Principal place of business**: Where you conduct substantial administrative/management activities
- **Exceptions**: Storage of inventory, daycare facilities

### Calculation Methods

#### Simplified Method
- $5 per square foot
- Maximum 300 square feet
- **Maximum deduction: $1,500**
- No depreciation recapture
- Easy recordkeeping

#### Regular Method
- Calculate actual expenses proportionally
- Business percentage = Office sq ft / Total home sq ft

**Direct expenses** (100% deductible):
- Painting office only
- Office-specific repairs
- Dedicated office equipment

**Indirect expenses** (proportional):
- Mortgage interest or rent
- Property taxes
- Utilities (electric, gas, water)
- Homeowners insurance
- Repairs and maintenance
- Depreciation of home

### Regular Method Example
```
Home: 2,000 sq ft, Office: 200 sq ft = 10%
Annual rent: $24,000 x 10% = $2,400
Utilities: $3,600 x 10% = $360
Renters insurance: $300 x 10% = $30
Internet: $1,200 x 50% = $600 (business use estimate)
Total home office deduction: $3,390
```

### Important Considerations
- Cannot exceed business income (carryforward for regular method)
- Depreciation recapture on sale of home (regular method)
- Document with photos and measurements
- Keep log of business use

### Tool support (`tax_savings_finder.py`, rule M12)

The savings finder compares both methods and recommends the larger, using the
constants `home_office_simplified_rate` ($5/sq ft) and `home_office_max_sqft`
(300 sq ft). Set three profile keys: `home_office_sqft`, `home_total_sqft`,
and `home_office_actual_expenses`. The deduction is capped at Schedule C net
income (IRC 280A(c)(5)), and the savings estimate covers both the income-tax
effect (at the marginal rate) and the SE-tax effect (recomputed via the shared
`calculate_se_tax`, not approximated).

```bash
python ./scripts/tax_savings_finder.py --data <profile.json> --year 2025 \
    --rules home_office --output-format json
```

**Worked example (acceptance test A11):** 200 sq ft office in a 2,000 sq ft
home, $30,000 of actual home expenses, $80,000 SE profit.

- Simplified: 200 sq ft × $5 = **$1,000** (under the 300 sq ft cap)
- Actual: 10% × $30,000 = **$3,000**
- Recommendation: **actual-expense method (Form 8829)** at $3,000, with a
  depreciation-recapture caveat. (Simplified wins only when the business-use
  percentage is low; sq ft is capped at 300 either way.)

---

## Vehicle Deduction

### Standard Mileage Method (2024)
- **Business**: 67 cents per mile
- Keep mileage log with: date, destination, business purpose, miles
- Add: Parking and tolls (business-related)
- Cannot use if: Used depreciation other than straight-line, claimed Section 179, fleet of 5+ vehicles

### Actual Expense Method
Deduct actual costs proportionally:
- Gas and oil
- Repairs and maintenance
- Insurance
- Registration fees
- Lease payments
- Depreciation (owned vehicles)

**Business percentage** = Business miles / Total miles

### Which Method to Choose
| Situation | Recommended Method |
|-----------|-------------------|
| High-mileage, fuel-efficient vehicle | Standard mileage |
| Expensive vehicle, low mileage | Actual expenses |
| New vehicle, want simplicity | Standard mileage |
| Older vehicle, high repair costs | Actual expenses |

### Documentation Requirements
- Mileage log for either method
- Receipts for actual expenses
- Records showing business purpose
- Calculation of business vs personal use

### First-Year Rules
- Can switch from standard to actual (with restrictions)
- Cannot switch from actual to standard if used MACRS depreciation
- Make decision carefully in year 1

---

## Health Insurance Deduction

### Self-Employed Health Insurance Deduction
- Deduct 100% of health insurance premiums
- Includes: Medical, dental, vision, long-term care
- Above-the-line deduction (reduces AGI)
- Available for: Self, spouse, dependents, children under 27

### Eligibility Requirements
- Cannot be eligible for employer-subsidized coverage (yours or spouse's)
- Business must have net profit (limited to net profit)
- If S-corp, premiums must be included in W-2 wages

### Interaction with Premium Tax Credit
- Cannot double-dip: Choose either PTC or SE health deduction
- Usually SE deduction is better if profitable
- Calculate both to determine optimal strategy

### Long-Term Care Insurance Limits (2024)
| Age at End of Year | Maximum Deduction |
|-------------------|-------------------|
| 40 or under | $470 |
| 41-50 | $880 |
| 51-60 | $1,760 |
| 61-70 | $4,710 |
| Over 70 | $5,880 |

---

## Estimated Tax Payments

### Who Must Pay
- Expected tax liability of $1,000+ after withholding
- Self-employed individuals (no withholding)
- Investors with significant investment income

### Due Dates (2024 Tax Year)
| Period | Due Date |
|--------|----------|
| Q1 (Jan 1 - Mar 31) | April 15, 2024 |
| Q2 (Apr 1 - May 31) | June 17, 2024 |
| Q3 (Jun 1 - Aug 31) | September 16, 2024 |
| Q4 (Sep 1 - Dec 31) | January 15, 2025 |

### Safe Harbor Rules
Avoid penalties by paying the lesser of:
1. **100% of prior year tax** (110% if AGI > $150,000)
2. **90% of current year tax**

### Additional Medicare Tax (Form 8959)
The 0.9% Additional Medicare Tax applies to mixed W-2 + SE filers in **two
parts**, and `estimated_tax_calculator.py` computes it that way rather than
as 0.9% on raw total income over one shared threshold:

- **Part I (wages):** 0.9% on W-2 wages above your filing-status threshold
  ($200,000 single / $250,000 MFJ / $125,000 MFS).
- **Part II (SE):** 0.9% on your **SE base — 92.35% of net SE profit** — above
  the threshold **as reduced by the wages already counted in Part I**. The
  threshold is *reduced*, not shared: every dollar of wages below the threshold
  lowers the SE threshold dollar-for-dollar.

Charging 0.9% on raw (wages + SE) over the threshold overstates the tax: it
skips the 7.65% SE haircut and misallocates the threshold. **Worked example
(single, $150k W-2 + $100k SE):** wage part $0 (under $200k); SE base $92,350;
SE threshold after wages $50,000; SE part (92,350 − 50,000) × 0.9% = **$381.15**
— not the $450 the raw-total approximation produces. Pass `--projected-wages`
explicitly so the projection does not default wages to `--projected-income`
(which includes SE income).

### Calculation Methods

#### Annualized Income Method
- Calculate actual income for each period
- Apply to full-year estimate
- Useful for seasonal or variable income

#### Equal Quarterly Payments
- Estimate annual tax, divide by 4
- Simpler but may over/underpay

### Payment Options
- **IRS Direct Pay**: irs.gov/payments (free)
- **EFTPS**: Electronic Federal Tax Payment System
- **Credit/debit card**: Third-party processors (fees apply)
- **Check**: Mail with Form 1040-ES voucher

---

## Retirement Plans for Self-Employed

### SEP-IRA
**Best for**: High earners, simple administration, last-minute contributions

| Feature | Details |
|---------|---------|
| Contribution limit | 25% of net SE income, up to $69,000 |
| Deadline | Tax filing deadline (with extensions) |
| Vesting | Immediate |
| Employee coverage | Must cover all eligible employees |
| Administrative requirements | Minimal |

**Contribution calculation**:
```
Net SE income: $150,000
Minus half SE tax: $150,000 - $10,597 = $139,403
SEP contribution: $139,403 x 25% = $34,851
```

### Solo 401(k)
**Best for**: Maximizing contributions, no employees, wanting Roth option

| Feature | Details |
|---------|---------|
| Employee contribution | $23,000 ($30,500 if 50+) |
| Employer contribution | 25% of compensation |
| Total limit | $69,000 ($76,500 if 50+) |
| Roth option | Available |
| Loan provision | Available |
| Deadline | End of calendar year for employee portion |

**Contribution calculation**:
```
Net SE income: $150,000
Employee portion: $23,000
Employer portion: $139,403 x 25% = $34,851
Total: $57,851 (under $69,000 limit)
```

### SIMPLE IRA
**Best for**: Small businesses with employees, lower administrative costs

| Feature | Details |
|---------|---------|
| Employee contribution | $16,000 ($19,500 if 50+) |
| Employer match | Up to 3% of compensation |
| Employer nonelective | 2% of compensation alternative |
| Deadline | October 1 to establish |
| Vesting | Immediate |

### Defined Benefit Plan
**Best for**: Very high earners 50+, want to shelter substantial income

| Feature | Details |
|---------|---------|
| Contribution limit | Actuarially determined (can be $200k+) |
| Complexity | High - requires actuary |
| Cost | Higher administrative fees |
| Commitment | Annual contributions required |

### Plan Comparison Summary
| Plan | Max Contribution | Setup Deadline | Best For |
|------|-----------------|----------------|----------|
| SEP-IRA | $69,000 | Tax deadline | Simplicity, high income |
| Solo 401(k) | $69,000-$76,500 | Dec 31 | Max contributions, Roth |
| SIMPLE IRA | $19,500 | Oct 1 | Small businesses |
| Defined Benefit | $200k+ | Varies | Very high earners 50+ |

---

## Recordkeeping Requirements

### What to Keep
- Income records (invoices, 1099s, bank statements)
- Expense receipts
- Mileage logs
- Home office measurements and calculations
- Asset purchase records (for depreciation)
- Contracts and agreements
- Prior year tax returns

### How Long to Keep
| Document Type | Retention Period |
|---------------|------------------|
| Tax returns | 7 years minimum |
| Income/expense records | 7 years |
| Asset records | Until sold + 7 years |
| Employment tax records | 4 years after due date |

### Digital Recordkeeping Tips
- Scan receipts immediately (they fade)
- Use cloud backup
- Organize by year and category
- Keep digital copies of 1099s received
- Photograph mileage log regularly

---

## Common Audit Triggers

### High-Risk Areas
1. **Home office deduction**: Have documentation ready
2. **Vehicle deduction**: Maintain contemporaneous mileage log
3. **Meals and entertainment**: Document business purpose
4. **Cash businesses**: Keep detailed records
5. **Large charitable deductions**: Get appraisals for non-cash donations
6. **Consistent losses**: IRS may reclassify as hobby

### How to Reduce Audit Risk
- Report all income (IRS receives copies of 1099s)
- Document, document, document
- Be reasonable with deductions
- File on time
- Respond promptly to IRS notices
- Consider professional preparation for complex returns

---

## Special Situations

### Gig Economy Workers
- Track all income (even if no 1099 received)
- Deduct platform fees as business expense
- Track mileage carefully
- Keep records of tips received
- May qualify for QBI deduction

### Independent Contractors
- Confirm proper classification (not employee)
- Use Form W-9 to collect client info
- Issue 1099-NEC to contractors you pay $600+
- Consider LLC for liability protection

### S-Corporation Election
**Potential benefits**:
- Reduced self-employment tax on distributions
- Reasonable salary + distributions strategy

**Requirements**:
- Pay yourself reasonable salary
- Process payroll, file employment tax returns
- More administrative complexity

**When to consider**: Net profit consistently over $50,000-$80,000

### QBI Deduction (Section 199A)

The Qualified Business Income deduction is up to **20% of qualified business
income (QBI)** from a pass-through trade or business. This plugin computes it
with `./scripts/qbi_calculator.py` — **never estimate it as a flat 20% of
Schedule C profit.** Profit is reduced first by the attributable above-the-line
items (½ SE tax, SE health insurance, SE retirement), and the deduction is then
capped by both a wage/UBIA limit and an overall taxable-income limit.

**Who it covers here:** sole proprietors / single-member LLCs filing Schedule C,
plus qualified REIT/PTP dividends from 1099-DIV Box 5. K-1 pass-throughs
(S-corp/partnership QBI), multi-business aggregation, and PTP income are out of
scope — supplying `k1_qbi` is rejected with a pointer to `./docs/limitations.md`.

**Thresholds and phase-in ranges** (single/HOH/MFS share one threshold; MFS
maps to the single threshold explicitly — never a silent default):

| Year | Single/HOH/MFS threshold | MFJ threshold | Phase-in range (single / MFJ) | $400 minimum (2026+) |
|---|---|---|---|---|
| 2023 | $182,100 | $364,200 | $50,000 / $100,000 | — |
| 2024 | $191,950 | $383,900 | $50,000 / $100,000 | — |
| 2025 | $197,300 | $394,600 | $50,000 / $100,000 | — |
| 2026 | $201,750 ($201,775 MFS) | $403,500 | $75,000 / $150,000 | $400 (active QBI ≥ $1,000) |

OBBBA (P.L. 119-21) made QBI **permanent**, **widened the 2026 phase-in ranges**
to $75K/$150K, **split the MFS threshold** from single for 2026, and added a
**$400 minimum deduction** for 2026+ (greater-of; overrides the overall
taxable-income limit when active QBI is at least $1,000).

**Computation order (Form 8995 / 8995-A):**

1. **Adjusted QBI** = Schedule C net profit − ½ SE tax − SE health insurance −
   SE retirement (the ½ SE tax is reused byte-identical from
   `estimated_tax_calculator.calculate_se_tax`, not recomputed).
2. **Threshold test** on taxable income *before* the QBI deduction:
   - **Below threshold** → Form 8995 (simplified): component = 20% × adjusted
     QBI. SSTB status and wages/UBIA are irrelevant below the threshold.
   - **Within phase-in range** → Form 8995-A: `applicable_pct = 1 −
     (TI − threshold)/range`. For an **SSTB**, QBI, wages, and UBIA are each
     multiplied by `applicable_pct` first (≤ 0 zeroes the SSTB component).
     Wage-limit phase-in: tentative = 20% × QBI; full limit =
     `max(50% × wages, 25% × wages + 2.5% × UBIA)`; if tentative > full limit,
     reduction = (tentative − full limit) × (TI − threshold)/range.
   - **Above range** → full wage/UBIA limit binds; SSTB component = $0.
3. **REIT/PTP component** = 20% × qualified REIT dividends (1099-DIV Box 5) —
   never wage-limited.
4. **Overall limit:** deduction = `min(QBI component + REIT component, 20% ×
   (taxable income before QBI − net capital gain))`, where net capital gain
   includes qualified dividends (§1(h)(11)/§199A(e)(3)).
5. **Negative QBI:** if adjusted QBI < 0, the QBI component is $0 and the loss
   **carries forward** to reduce next year's QBI (reported prominently).
   Negative REIT/PTP carries forward separately — no netting against QBI.
6. **2026+ minimum:** when the $400 minimum constant is non-null, the year is
   ≥ 2026, and active QBI ≥ $1,000, the deduction is at least $400. If the
   constant is unverified (`null`), the engine **raises** rather than guessing.

**SSTB (specified service trade or business)** — the tool *asks*, never
infers: health, law, accounting, consulting, financial services, performing
arts, athletics, and any business whose principal asset is the reputation or
skill of its employees/owners (Reg. §1.199A-5). Engineering and architecture are
exempted by statute.

**Worked examples (mirrors `tests/test_qbi_calculator.py`):**

- *Below threshold (Q1).* MFJ 2025, adjusted QBI $80,000, TI $150,000, no
  wages/UBIA → deduction = 20% × $80,000 = **$16,000**, Form 8995. SSTB status
  has no effect below the threshold.
- *Adjusted-QBI derivation (Q2).* Schedule C $100,000, SE health $10,000 →
  ½ SE tax = 92,350 × 15.3% ÷ 2 = $7,064.77 (matches
  `calculate_se_tax`); adjusted QBI = 100,000 − 7,064.77 − 10,000 =
  **$82,935.23**.
- *Wage limit binds (Q5).* MFJ 2025, TI $600,000 (above range), QBI $300,000,
  wages $50,000, UBIA $0 → component = min(60,000, 50%×50,000=25,000) =
  **$25,000**. With UBIA $1,000,000 → max(25,000, 25%×50,000+2.5%×1,000,000 =
  12,500+25,000=37,500) = **$37,500**.
- *Overall taxable-income limit (Q6).* QBI component $20,000, TI before QBI
  $90,000, net capital gain $60,000 → deduction = min(20,000, 20%×30,000) =
  **$6,000**.
- *REIT-only (Q7).* $40.26 of 1099-DIV Box 5 dividends, no SE income →
  20% × $40.26 = **$8.05**, never wage-limited, Form 8995.
- *Negative QBI (Q8).* Adjusted QBI −$30,000, REIT $1,000 → QBI component $0,
  REIT component $200 (not netted), carryforward **−$30,000** reported.
- *Year gating (Q9).* Single filer at TI $260,000: 2025 (range $50K → above
  range, full wage limit) vs 2026 (range $75K → still inside phase-in) give
  different results. The 2026 $400 minimum fires only when active QBI ≥ $1,000.

**CLI:**

```bash
python ./scripts/qbi_calculator.py \
  --se-net-profit 120000 --se-health-insurance 8000 \
  --w2-wages-paid 0 --ubia 0 --sstb \
  --taxable-income-before-qbi 210000 --net-capital-gain 15000 \
  --reit-dividends 40.26 --filing-status mfj --year 2025 --output-format json
```

`tax_calculator.py --compute-qbi` runs the engine and feeds the result into the
existing CA add-back path automatically; `estimated_tax_calculator.py` subtracts
the computed deduction from projected taxable income by default (`--no-qbi` for a
control run); `draft_reviewer.py` recomputes line 13 / Form 8995 line 15 and
flags a draft that subtracts QBI in the CA state column.
