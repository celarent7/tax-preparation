# Tax Planning & Optimization Strategies

Multi-year planning, estimated payments, state tax guidance, and life event tax strategies.

## Savings & Optimization Engine (`tax_savings_finder.py`)

`tax_savings_finder.py` is the proactive optimizer: it reads a single taxpayer
profile and emits ranked savings opportunities, each with a dollar estimate,
priority, deadline, and any state nonconformity caveat. Every statutory figure
comes from `scripts/tax_constants.json` via `utils.get_constant` — no
hard-coded limits — and every marginal-rate estimate reuses the existing
`tax_calculator` engines (no duplicated bracket walkers).

### Canonical profile schema

The finder takes a **flat** JSON profile (the legacy nested shape still loads,
with a `DeprecationWarning`, via an adapter in `savings_profile.py`). Unknown
keys are **rejected with a nearest-key suggestion** rather than silently
defaulting to zero — a typo like `"aggi"` raises an error pointing at `"agi"`.
Required: `filing_status`. All other fields are optional with documented
defaults. The full field list lives in `scripts/savings_profile.py`
(`CANONICAL_FIELDS`); a worked example is `examples/sample_tax_scenario.json`.

```bash
python ./scripts/tax_savings_finder.py --data <profile.json> --year 2025 \
    --output-format json
# Optional: run a subset of rules, or override the profile's state
python ./scripts/tax_savings_finder.py --data <profile.json> --year 2025 \
    --rules backdoor_roth,tlh --state CA
```

### Rule families (M1–M11)

Each rule is a pure function in `scripts/savings_rules/`, registered by `rule_id`
in `savings_rules/__init__.py`. Year-gated rules (OBBBA M10/M11) return
nothing — and never crash — outside their statutory window.

| ID | Rule | Logic (figures from `tax_constants.json`) |
|----|------|------------------------------------------|
| M1 | `k401k_headroom` | 401(k) elective-deferral headroom, per person (MFJ spouse computed independently). Age-banded catch-up: 50+ standard catch-up, 60–63 SECURE 2.0 super catch-up. |
| M2 | `backdoor_roth` | Nondeductible Trad IRA → Form 8606 → Roth conversion. Fires only at/above the Roth MAGI phase-out **start** (below it, a direct Roth is better). **Mandatory pro-rata warning** (IRC 408(d)(2)/(8)) when a pre-tax Trad IRA balance exists: taxable fraction = pretax / (pretax + contribution). No current-year deduction claimed. |
| M3 | `mega_backdoor_roth` | After-tax 401(k) contributions up to the §415(c) total-additions cap, less all employee + employer + after-tax additions. Downgraded to LOW when the plan disallows in-service rollover. |
| M4 | `tlh` | Tax-loss harvesting, quantified against the realized position via the exact §1222 netting ladder (net within class → cross-net → $3,000/$1,500-MFS ordinary offset → carryforward retaining character). Federal savings split by ordinary vs LTCG rate. |
| M5 | `appreciated_stock_donation` | Donate appreciated long-term stock vs cash: 30%-AGI FMV deduction **plus** the avoided LTCG + NIIT + (CA) gain tax. Short-term holdings are limited to **basis** (warning fires). Profiles that won't itemize get a **bunching** recommendation instead of a false savings claim. |
| M6 | `qcd` | Qualified Charitable Distribution for those 70½+: up to the indexed cap direct from an IRA to charity, excluded from AGI and counting toward the RMD. |
| M7 | `se_health_insurance` | Above-the-line SE health insurance deduction = min(premiums, net SE profit − ½ SE tax), reusing `calculate_se_tax` so it matches `estimated_tax_calculator.py`. |
| M8 | `section179_bonus` | §179 expensing (limit + phase-out from constants) and bonus depreciation. The 2025 OBBBA cutover (40% before / 100% after 2025-01-19) surfaces **both rates** and asks for an in-service date rather than guessing. CA: no bonus-depreciation conformity. |
| M9 | `hsa` | HSA headroom, split into federal and state savings. **CA does not conform**: contributions are added back on Schedule CA and earnings are CA-taxable → state savings $0 with a mandatory caveat. |
| M10 | `senior_bonus` | OBBBA senior bonus (2025–2028): $6,000 per qualifying 65+ person, phased out at 6%/dollar over $75,000 (single) / $150,000 (MFJ). The same shared helper backs `deduction_analyzer.py --age-65-plus`, so the two tools agree to the dollar. CA add-back. |
| M11 | `tips_deduction` / `overtime_deduction` | OBBBA above-the-line tips and overtime deductions (2025–2028), capped and phased down per $1,000 of MAGI over the threshold. CA add-back. |

### California nonconformity

When `state == "CA"`, rules that depend on a federal provision California does
not conform to attach a `state_caveat` and zero out state savings: HSA
deductibility (M9), bonus depreciation (M8), preferential capital-gains rates
(M4/M5), and the OBBBA tips/overtime/senior deductions (M10/M11). The
nonconformity flags live under `states.CA.<year>.nonconformity` in
`tax_constants.json`.

## Year-End Planning Opportunities

### Deduction Timing Strategies
- Accelerate deductions / defer income (or vice versa depending on bracket trajectory)
- Maximize retirement contributions before year-end
- Charitable giving strategies: bunching, donor-advised funds, QCDs for those 70.5+
- Roth conversion analysis in lower-income years
- Estimated payment adjustments to avoid penalties

### Bunching Strategy
If total itemized deductions are near the standard deduction threshold, consider bunching in alternating years:
- Prepay property taxes in December
- Make two years of charitable donations in one year
- Time elective medical procedures
- Use donor-advised fund for charitable bunching

### Multi-Year Projections
There is no dedicated projector script. Model each year by running the
calculator with that year's constants (2023–2026 are supported):
```bash
python ./scripts/tax_calculator.py --gross-income <amt> --filing-status <status> --year 2025
python ./scripts/tax_calculator.py --gross-income <amt> --filing-status <status> --year 2026
```

---

## Life Event Tax Impact

### Marriage
- Update withholding (new W-4s)
- Compare MFJ vs MFS (usually joint is better)
- Reassess itemized vs standard deduction with combined expenses
- Check education credit phaseouts with combined income

### Children
- Child Tax Credit ($2,200/child under 17 for 2025+ under OBBBA; $2,000 for 2024 and earlier)
- Dependent care credit for childcare expenses
- Education savings (529 plan, state deduction possible)
- Filing status may change to Head of Household if unmarried

### Home Purchase
- Mortgage interest deduction (up to $750k of acquisition debt, or $1M if the debt was incurred on or before 2017-12-15; California allows $1M regardless)
- Property tax deduction (subject to the SALT cap: $40,000 for 2025, $10,000 for 2018–2024 — see `draft_review_checklist.md`)
- Points paid at closing (deductible in year paid or amortized)
- First-time homebuyer considerations

### Job Change
- Review withholding to avoid underpayment
- Moving expenses (military only since 2018)
- Stock option/RSU forfeiture or acceleration
- 401(k) rollover decisions

### Retirement
- Distribution planning: minimize tax on withdrawals
- Roth conversions: fill low brackets before RMDs start
- Social Security timing: up to 85% taxable based on income
- Medicare IRMAA: manage MAGI to avoid premium surcharges ($103k single/$206k MFJ)

---

## Estimated Tax Payments

### Who Must Pay
- Expected tax liability of $1,000+ after withholding
- Self-employed individuals with no withholding
- Investors with significant investment income

### Safe Harbor Rules
Avoid penalties by paying the lesser of:
1. **100% of prior year tax** (110% if prior-year AGI > $150,000, or > $75,000 if married filing separately)
2. **90% of current year tax**

### Calculation
```bash
python ./scripts/estimated_tax_calculator.py --projected-income <amount> \
    --prior-year-tax <amount> --prior-year-agi <amount> \
    --filing-status <status> --year <year> --withholding-to-date <amount>
```

### Due Dates (Calendar Year)
| Period | Due Date |
|--------|----------|
| Q1 (Jan 1 - Mar 31) | April 15 |
| Q2 (Apr 1 - May 31) | June 15 |
| Q3 (Jun 1 - Aug 31) | September 15 |
| Q4 (Sep 1 - Dec 31) | January 15 (next year) |

### Payment Options
- **IRS Direct Pay**: irs.gov/payments (free)
- **EFTPS**: Electronic Federal Tax Payment System
- **Credit/debit card**: Third-party processors (fees apply)
- **Check**: Mail with Form 1040-ES voucher

---

## State Tax Preparation

### Filing Requirements
- Resident state: All income taxable
- Part-year resident: Income during residency period
- Nonresident: Source income from that state only
- Reciprocal agreements: Check between neighboring states

### Common State Adjustments
- State may not conform to all federal deductions/credits
- State-specific credits (property tax, EITC supplements)
- State retirement income exclusions
- Municipal bond interest (in-state vs out-of-state treatment)

### Multi-State Situations
- Track which income is sourced to which state
- Calculate credit for taxes paid to other states
- Be aware of state-specific rules for remote workers

---

## Itemized vs Standard Deduction Decision

### Standard Deduction (<!-- YEAR -->2025<!-- /YEAR -->)
| Filing Status | Amount |
|---------------|--------|
| Single | $15,750 |
| Married Filing Jointly / QSS | $31,500 |
| Married Filing Separately | $15,750 |
| Head of Household | $23,625 |
| Additional for 65+/blind | $1,600 (married) or $2,000 (single/HoH) |

> These are the **OBBBA** amounts, effective for tax years beginning after
> 2024-12-31. This table previously showed 2024 figures ($14,600 / $29,200 /
> $21,900) with no year marker while the surrounding text read as current —
> audit finding Y4. Prior-year amounts for amended returns live in
> `references/tax_brackets_deductions.md`.
>
> *Source: IRS Pub 501 (2025), <https://www.irs.gov/publications/p501>.*

### Itemized Deductions Summary
| Category | Limit | Notes |
|----------|-------|-------|
| Medical expenses | >7.5% of AGI | Only amount exceeding threshold |
| Casualty note | — | California: state income tax is NOT deductible on the CA return; see `california_tax_guide.md` |
| State/local taxes (SALT) | $40,000 cap for 2025 ($20,000 MFS), phased down above $500,000 MAGI; $10,000 for 2018–2024 | Property + income or sales tax |
| Mortgage interest | $750k limit ($1M if incurred on or before 2017-12-15) | Primary + one second home |
| Charitable contributions | 60% AGI cash, 30% appreciated | Substantiation requirements |
| Casualty/theft losses | Federally declared disasters only | $100 floor + 10% AGI |

### Comparison Script
```bash
python ./scripts/deduction_analyzer.py --agi <agi> --filing-status <status> --year <year> \
    --property-tax <amt> --state-income-tax <amt> --mortgage-interest <amt> \
    --charitable-cash <amt> --medical-expenses <amt>
```

### Decision Factors
- Compare total itemized vs standard amount
- If close, consider bunching strategy (see above)
- Even if standard is higher, check if state return benefits from itemizing
- Some deductions are above-the-line regardless (HSA, student loan interest, etc.)

---

## Investment Tax Optimization

### Capital Gains Rates (<!-- YEAR -->2025<!-- /YEAR -->)
- 0%: Taxable income up to $48,350 (single), $96,700 (MFJ)
- 15%: Up to $533,400 (single), $600,050 (MFJ)
- 20%: Above those thresholds

*Source: IRS Topic no. 409, <https://www.irs.gov/taxtopics/tc409>. Full table by
filing status: `references/investment_taxes.md`.*

### Net Investment Income Tax (NIIT)
3.8% on investment income when MAGI exceeds $200k (single/HoH), $250k (MFJ),
$125k (MFS). These thresholds are statutory and **not** inflation-indexed.

### Optimization Strategies
- **Tax-loss harvesting**: Offset gains with losses (watch wash sale rule)
- **Gain/loss timing**: Realize losses in high-income years, gains in low-income years
- **Long-term vs short-term**: Hold 12+ months for preferential rates
- **Asset location**: Tax-efficient placement across account types
- **Qualified dividends**: Understand holding period requirements
- **Capital loss carryforward**: Track unused losses ($3,000 annual limit against ordinary income)

### Capital gains and RSU/ESPP lot analysis
```bash
python ./scripts/rsu_calculator.py sale --data <vesting_and_sales.json>
python ./scripts/espp_calculator.py --data <espp_transactions.json>
```

To model the preferential-rate stack on a whole return:
```bash
python ./scripts/tax_calculator.py --gross-income <amt> --filing-status <status> \
    --year <year> --ltcg <amt> --qualified-dividends <amt>
```

---

## Filing Status Optimization

### Status Considerations
- **Married Filing Jointly vs Separately**: Usually joint is better; separate may help with income-driven loan repayments, medical deduction threshold, or liability concerns
- **Head of Household**: Requires unmarried, paying >50% household costs, qualifying person
- **Qualifying Surviving Spouse**: 2 years after spouse's death with dependent child

### Comparing filing statuses
There is no dedicated filing-status script. Run the calculator once per status
and compare — note that the MFS safe-harbor threshold is half the general one:
```bash
python ./scripts/tax_calculator.py --gross-income <amt> --filing-status mfj --year <year>
python ./scripts/tax_calculator.py --gross-income <amt> --filing-status mfs --year <year>
```

---

## Output Documents

### Tax Return Summary (Word document)
- Filing overview: Status, income summary, key deductions/credits
- Tax liability breakdown: Federal, state, FICA, estimated payments
- Refund or balance due with payment options
- Year-over-year comparison
- Planning recommendations for next year

### Tax Workpapers (Excel spreadsheet)
- Income detail by source
- Itemized deduction analysis
- Credit calculations with eligibility
- Estimated payment schedule
- Multi-year comparison

**Naming**: `tax_return_summary_YYYY.docx` / `tax_workpapers_YYYY.xlsx`

### Document Retention
- Tax returns: 7 years minimum
- Supporting documents: 7 years
- Investment purchase records: Until sold + 7 years
- Real estate records: Until sold + 7 years
