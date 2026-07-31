# California Tax Guide <!-- YEAR -->2025<!-- /YEAR -->

California is **not** a scaled-down copy of the federal return. It has its own
progressive brackets, its own (much smaller) standard deduction, credits instead
of exemptions, and a long list of non-conformity items. Presenting federal-only
results to a California filer is wrong often enough that SKILL.md now requires a
`CA impact` section for any CA resident.

> **The single biggest mistake:** treating **13.3%** as California's tax rate.
> The top *bracket* is **12.3%**. 13.3% is the combined marginal rate above
> $1,000,000 once the 1% Behavioral Health Services Tax is added — and it applies
> only to the income above $1,000,000. At $200,000 of MFJ taxable income the real
> tax is about **$11,477**, not $26,600.

All figures below come from the sources listed at the bottom and are mirrored in
`scripts/tax_constants.json` under `states.CA.2025`.

---

## 1. Form 540 flow

```
Federal AGI (Form 1040 line 11)
  ± Schedule CA (540) Part I adjustments      <- non-conformity lives here
  = California AGI                            (Form 540 line 17)
  − CA standard deduction OR CA itemized      (line 18)
  = California taxable income                 (line 19)
  → tax from Schedule X / Y / Z               (line 31)
  − exemption credits                         (line 32)
  − other credits (renter's, etc.)            (line 46)
  + Behavioral Health Services Tax            (line 62)
  = total tax                                 (line 64)
```

Schedule CA Column B subtracts income California does not tax; Column C adds
income or disallowed deductions California does tax.

---

## 2. Non-conformity table

| Item | Federal | California | Effect |
|---|---|---|---|
| **HSA contributions** | Deductible / excluded from Box 1 | **Not deductible** | Add back on Schedule CA Column C |
| **HSA earnings** | Tax-deferred | **Taxable annually** | Report interest/dividends/gains inside the HSA every year |
| **QBI (§199A)** | Up to 20% deduction (computed by `qbi_calculator.py`; `tax_calculator.py --compute-qbi` applies it and adds it back here automatically) | **No such deduction** | Add back on Schedule CA — the federal `--compute-qbi` flow performs this add-back in one step, so never also subtract QBI in the CA state column |
| **Capital gains** | 0/15/20% preferential | **Ordinary rates** | No preferential bracket; a large LTCG is taxed at up to 12.3% |
| **Qualified dividends** | Preferential | **Ordinary rates** | Same |
| **Mortgage interest** | $750,000 acquisition debt | **$1,000,000** + $100,000 HELOC | CA is *more* generous — a savings opportunity |
| **SALT** | Capped deduction | **Not deductible at all** on the CA return | Remove state income tax from CA itemized |
| **Bonus depreciation** | Available | **Not allowed** | Recompute depreciation for CA |
| **OBBBA tips / overtime / senior / car-loan deductions** | Deductible 2025–2028 | **Not allowed** | CA conformity date is **2015-01-01**, so no post-2015 federal provision applies unless CA enacts it separately |
| **529 for K-12** | Qualified | Non-qualified for CA | CA taxes the earnings portion |

Because California's conformity date is fixed at **January 1, 2015**, the default
assumption for *any* newer federal provision is that California does **not**
follow it.

---

## 3. Tax rate schedules (2025)

Nine brackets, 1% to 12.3%. The MFJ boundaries are exactly **double** the single
boundaries (R&TC §17041).

**Schedule X — Single or Married/RDP Filing Separately**

| Taxable income over | but not over | Rate |
|---|---|---|
| $0 | $11,079 | 1.0% |
| $11,079 | $26,264 | 2.0% |
| $26,264 | $41,452 | 4.0% |
| $41,452 | $57,542 | 6.0% |
| $57,542 | $72,724 | 8.0% |
| $72,724 | $371,479 | 9.3% |
| $371,479 | $445,771 | 10.3% |
| $445,771 | $742,953 | 11.3% |
| $742,953 | — | 12.3% |

**Schedule Y — Married/RDP Filing Jointly or Qualifying Surviving Spouse**

| Taxable income over | but not over | Rate |
|---|---|---|
| $0 | $22,158 | 1.0% |
| $22,158 | $52,528 | 2.0% |
| $52,528 | $82,904 | 4.0% |
| $82,904 | $115,084 | 6.0% |
| $115,084 | $145,448 | 8.0% |
| $145,448 | $742,958 | 9.3% |
| $742,958 | $891,542 | 10.3% |
| $891,542 | $1,485,906 | 11.3% |
| $1,485,906 | — | 12.3% |

**Schedule Z — Head of Household**

| Taxable income over | but not over | Rate |
|---|---|---|
| $0 | $22,173 | 1.0% |
| $22,173 | $52,530 | 2.0% |
| $52,530 | $67,716 | 4.0% |
| $67,716 | $83,805 | 6.0% |
| $83,805 | $98,990 | 8.0% |
| $98,990 | $505,208 | 9.3% |
| $505,208 | $606,251 | 10.3% |
| $606,251 | $1,010,417 | 11.3% |
| $1,010,417 | — | 12.3% |

---

## 4. Standard deduction & exemption credits (2025)

| Item | Amount |
|---|---|
| Standard deduction — single / MFS | **$5,706** |
| Standard deduction — MFJ / HoH / QSS | **$11,412** |
| Personal exemption credit (each) | $153 |
| Blind exemption credit (each) | $153 |
| Senior (65+) exemption credit (each) | $153 |
| Dependent exemption credit (each) | **$475** |
| Exemption credit phase-out begins (federal AGI) | $252,203 |

California's exemptions are **credits**, not deductions — they reduce tax
dollar-for-dollar, so a dependent is worth $475 of tax regardless of bracket.

> **CA itemize-vs-standard is a SEPARATE decision.** The CA standard deduction is
> roughly a third of the federal one, so a filer who takes the federal standard
> deduction may still itemize for California. Remember that state income tax is
> **not** deductible on the CA return, and that CA allows mortgage interest on up
> to $1,000,000 of debt.

---

## 5. Behavioral Health Services Tax

Renamed from the **Mental Health Services Tax** for tax years beginning on or
after 2025-01-01 (Prop 1 / SB 326).

- **1% of taxable income above $1,000,000** — Form 540 line 62.
- It is a **separate additive tax**, not a tenth bracket. Below $1,000,000 of
  taxable income it is exactly $0.
- Only the excess is taxed: $1,500,000 of taxable income owes 1% × $500,000 = $5,000.

---

## 6. SDI / VPDI and excess-SDI credit

| Item | 2025 |
|---|---|
| SDI withholding rate | **1.2%** |
| Taxable wage limit | **None** — removed by SB 951 effective 2024-01-01 |

With no wage cap, SDI is now an uncapped 1.2% surtax on wages, which materially
changes RSU withholding-shortfall math for high earners: a $500,000 earner pays
$6,000 of SDI where the old cap would have stopped far earlier.

**Excess SDI credit (Form 540 line 74):** this credit exists ONLY for a year
that has a taxable wage cap, and ONLY when two or more employers withheld
SDI/VPDI. The credit is `total withheld − rate × wage_cap` — the amount by which
combined withholding exceeds what a single employer at the cap would have owed.

- **2024 and later (no cap, SB 951):** the credit CANNOT arise. With no wage cap
  there is no per-year maximum to exceed, so there is no "excess" no matter how
  many employers withheld. A single employer's over-withholding is recovered from
  that employer, not on the return.
- **2023 and earlier (capped years, relevant to amended returns):** with two or
  more employers, claim `total withheld − rate × wage_cap` on Form 540 line 74.
  A single employer's over-withholding is still recovered from the employer.

The `excess_sdi_credit()` function in `scripts/utils.py` encodes this cap-driven
logic (treating `wage_cap: null` as the semantic "no cap" rather than unverified
data), and `draft_reviewer.py`'s `check_excess_sdi` flags a Form 540 that omits
a credit the filer was entitled to — capped years only; for uncapped years it
emits at most an INFO explaining why no credit is possible.

---

## 7. Supplemental withholding (RSUs and bonuses)

| Payment type | CA withholding rate |
|---|---|
| **Bonuses and stock options (incl. RSU vesting)** | **10.23%** |
| Other supplemental (overtime, commissions, sales awards, severance, vacation) | 6.6% |

Source: EDD DE 44, the California Employer's Guide.

**This is withholding, not the final tax.** The vest is taxed on the return at
progressive rates up to 12.3%, so a filer whose CA marginal rate exceeds 10.23%
is under-withheld on every vest. Combine with the uncapped 1.2% SDI when
estimating a shortfall.

---

## 8. Estimated tax — California differs from federal

| Rule | Federal | California |
|---|---|---|
| Installment weighting | 25 / 25 / 25 / 25 | **30 / 40 / 0 / 30** |
| High-income threshold (110% of prior year) | AGI > $150,000 ($75,000 MFS) | Same |
| Prior-year safe harbor at very high income | Always available | **Unavailable** at CA AGI ≥ **$1,000,000** ($500,000 MFS) — must pay 90% of the *current* year |

Note there is **no third-quarter California installment**. A filer who mirrors
the federal 25/25/25/25 schedule will be underpaid in Q1 and Q2 and will owe a
penalty even if the annual total is right.

### 8a. Computing the schedule with the plugin

CA estimated tax is a **two-command** flow. The CA *tax* projection comes from
the tax calculator's CA engine (the single CA engine — F13 rule); the
estimated-tax calculator then turns that projection into the 30/40/0/30
installment schedule. Do not hand-enter the projection — run the first command.

```bash
# 1. Project the full-year CA tax (the single CA engine):
python ./scripts/tax_calculator.py --gross-income <amt> --filing-status <status> \
    --year 2025 --state CA --hsa-contributions <amt> --qbi-deduction <amt> \
    --obbba-deductions <amt>
#   -> read "CA tax" from the output.

# 2. Build the FTB 540-ES installment schedule from that projection:
python ./scripts/estimated_tax_calculator.py --projected-income <amt> \
    --prior-year-tax <federal-prior> --prior-year-agi <federal-prior-agi> \
    --filing-status <status> --year 2025 --output-format json \
    --state CA --projected-ca-tax <CA tax from step 1> \
    --prior-year-ca-tax <prior-year CA tax> --ca-agi <projected CA AGI> \
    --ca-withholding <CA withholding to date>
```

The JSON result gains a `state_payment_plan` section (the federal
`payment_plan` is unchanged). For no-income-tax states
(`--state WA|TX|FL|NV`) it carries a "no state estimated payments required"
note; any other state exits 1 (CA is the only implemented state engine).

### 8b. Worked example — 30/40/0/30 split

From `tests/test_ca_estimated_tax.py::test_installments_split_30_40_0_30`:
projected CA tax **$20,000**, prior-year CA tax **$30,000**, CA AGI **$200,000**,
single filer.

- Current-year leg: 90% × $20,000 = **$18,000**
- Prior-year leg: AGI > $150,000 → 110% × $30,000 = **$33,000**
- Required annual payment = lesser = **$18,000** (90% current-year basis)

| Quarter | Due date   | %  | Amount    |
|---------|------------|----|-----------|
| Q1      | 2025-04-15 | 30 | $5,400.00 |
| Q2      | 2025-06-16 | 40 | $7,200.00 |
| Q3      | 2025-09-15 |  0 | $0.00     |
| Q4      | 2026-01-15 | 30 | $5,400.00 |
| **Sum** |            |    | **$18,000.00** |

The four installments sum to the required payment to the cent. CA AGI at or
above **$1,000,000** ($500,000 MFS) forces the 90%-current-year basis
(`safe_harbor_available: false`) — the prior-year leg is never used there,
even when it is smaller.

---

## 9. Worked example — MFJ, W-2 + RSU + HSA

> This example is asserted end-to-end by
> `tests/test_california.py::test_gate_worked_example_matches_the_guide`, so the
> guide and the engine cannot drift apart.

**Facts.** Married filing jointly, California residents for the whole year.

| Item | Amount |
|---|---|
| W-2 Box 1 wages (includes $50,000 of RSU vesting income) | $250,000 |
| HSA family contribution via payroll (excluded from Box 1) | $8,550 |
| No other income, no dependents, not renters | — |

**Step 1 — Federal AGI.** $250,000. The HSA contribution was payroll-deducted, so
it is already excluded from Box 1 for federal purposes.

**Step 2 — California AGI (Schedule CA Column C).**

```
Federal AGI                                    250,000.00
+ HSA contribution add-back (CA non-conformity)   8,550.00
= California AGI                               258,550.00
```

**Step 3 — CA taxable income.**

```
California AGI                                 258,550.00
− CA standard deduction (MFJ)                   11,412.00
= California taxable income                    247,138.00
```

**Step 4 — Tax from Schedule Y.**

| Rate | Income in bracket | Tax |
|---|---|---|
| 1.0% | $22,158 | $221.58 |
| 2.0% | $30,370 | $607.40 |
| 4.0% | $30,376 | $1,215.04 |
| 6.0% | $32,180 | $1,930.80 |
| 8.0% | $30,364 | $2,429.12 |
| 9.3% | $101,690 | $9,457.17 |
| | **Total** | **$15,861.11** |

Cross-check against the FTB schedule row directly:
`$6,403.94 + 9.30% × ($247,138 − $145,448) = $15,861.11` ✓

**Step 5 — Credits and other taxes.**

```
CA tax before credits                           15,861.11
− exemption credit (2 personal × $153)             306.00
    (federal AGI $250,000 < $252,203, so no phase-out)
+ Behavioral Health Services Tax                     0.00
    (taxable income is below $1,000,000)
= CALIFORNIA TAX                                15,555.11
```

**Step 6 — What the filer should also know.**

- **SDI:** 1.2% × $250,000 = **$3,000**, uncapped, withheld by the employer.
- **RSU withholding gap:** the $50,000 vest was withheld for CA at 10.23%
  (**$5,115**), but this filer's CA marginal rate is **9.3%**, so on this return
  the vest is slightly *over*-withheld for CA. That flips to under-withholding
  once CA taxable income passes $742,958 and the marginal rate reaches 10.3%.
- **HSA cost in California:** the $8,550 contribution saved federal tax but is
  fully CA-taxable. At a 9.3% marginal rate that is **$795** of California tax
  the federal-only view would have missed — and the HSA's future earnings are
  CA-taxable every year as well.

**Reproduce it:**

```bash
python ./scripts/tax_calculator.py \
  --gross-income 250000 --filing-status mfj --year 2025 \
  --state CA --hsa-contributions 8550
```

---

## Sources

All retrieved 2026-07-30 and recorded in `scripts/tax_constants.json` `_sources`.

- FTB, *2025 California Tax Rate Schedules* — <https://www.ftb.ca.gov/forms/2025/2025-540-tax-rate-schedules.pdf>
- FTB, *2025 Form 540 and booklet* (standard deduction, exemption credits, renter's credit, line 62 Behavioral Health Services Tax) — <https://www.ftb.ca.gov/forms/2025/2025-540.pdf>
- FTB, *2025 Instructions for Form 540-ES* (30/40/0/30 installments, $1,000,000 rule) — <https://www.ftb.ca.gov/forms/2025/2025-540-es-instructions.html>
- EDD, *2025 California Employer's Guide (DE 44)* (10.23% / 6.6% supplemental withholding, 1.2% SDI with no wage limit) — <https://edd.ca.gov/siteassets/files/pdf_pub_ctr/de44.pdf>

**Scope:** only tax year 2025 California data has been collected. Requesting
another year raises rather than approximating. This guide is not tax advice; see
`DISCLAIMER.md`.
