# Document Gathering Checklist

Comprehensive checklist for collecting all required tax documents. Run through each section to ensure nothing is missing.

## Income Documents

### Employment Income
- [ ] W-2 from each employer (check Box 12 codes for retirement, HSA, etc.)
- [ ] Final pay stub (to verify W-2 accuracy)
- [ ] Stock compensation statements (RSUs, ISOs, ESPP)
- [ ] Deferred compensation statements

### Self-Employment/Business Income
- [ ] 1099-NEC for each client paying $600+
- [ ] 1099-K from payment platforms (Venmo, PayPal, Square)
- [ ] Income records for cash/check payments (no 1099)
- [ ] Expense receipts organized by category
- [ ] Mileage log (if claiming vehicle)
- [ ] Home office measurements and total home square footage
- [ ] Asset purchase records (for depreciation)
- [ ] Prior depreciation schedules

### Investment Income
- [ ] 1099-B (all brokerage accounts - may be multiple pages)
- [ ] 1099-DIV (dividends)
- [ ] 1099-INT (interest)
- [ ] 1099-OID (original issue discount)
- [ ] K-1s from partnerships, S-corps, estates, trusts
- [ ] Cryptocurrency transaction records
- [ ] Cost basis records for any sales
- [ ] Records of wash sales
- [ ] Capital loss carryforward from prior year

### Retirement Income
- [ ] 1099-R (each distribution)
- [ ] SSA-1099 (Social Security)
- [ ] RMD documentation
- [ ] Roth conversion records
- [ ] Basis in traditional IRA (Form 8606 history)

### Other Income
- [ ] 1099-G (unemployment, state refunds)
- [ ] 1099-MISC (miscellaneous income)
- [ ] Gambling W-2G
- [ ] Alimony received (pre-2019 divorces)
- [ ] Rental income records
- [ ] Jury duty pay
- [ ] Prize/award letters

---

## Deduction Documents

### Medical Expenses (if itemizing)
- [ ] Health insurance premium statements (1095-A, 1095-B, 1095-C)
- [ ] Explanation of Benefits (EOB) for all medical services
- [ ] Prescription receipts
- [ ] Medical mileage log
- [ ] Long-term care insurance premiums (Form 1099-LTC)
- [ ] HSA distributions (1099-SA) and contributions (5498-SA)

### Property & Interest
- [ ] 1098 Mortgage Interest Statement (each loan)
- [ ] Property tax bills/receipts
- [ ] HUD-1 or Closing Disclosure (if purchased/refinanced)
- [ ] Home equity loan statements

### Charitable Contributions
- [ ] Written acknowledgment for donations $250+
- [ ] Receipts for all cash donations
- [ ] Non-cash donation itemization with fair market values
- [ ] Qualified appraisal for property donations over $5,000
- [ ] Stock donation records
- [ ] Mileage log for charitable driving

### Education
- [ ] 1098-T Tuition Statement
- [ ] 1098-E Student Loan Interest
- [ ] Receipts for books, supplies, equipment
- [ ] 529 plan contribution/distribution records
- [ ] Scholarship/grant letters

### Childcare
- [ ] Provider name, address, SSN or EIN
- [ ] Total paid per provider
- [ ] Dependent care FSA records (W-2 Box 10)

---

## Prior Year Documents

Always request these from the user:
- [ ] Prior year tax return (federal and state)
- [ ] Capital loss carryforward schedule
- [ ] Charitable contribution carryforward
- [ ] AMT credit carryforward
- [ ] Net operating loss carryforward
- [ ] Passive activity loss carryforward
- [ ] Investment interest expense carryforward
- [ ] Form 8606 (IRA basis tracking)
- [ ] Prior depreciation schedules
- [ ] **Prior year filed FinCEN 114 (FBAR)** — if any foreign account exists.
      This is the reconciliation baseline; without it there is no mechanical
      guarantee that every account on last year's filing was carried forward,
      closed out, or consciously dropped. Note whether it is a BSA E-Filing
      acknowledgment copy or a preparer review copy — they are not the same
      evidence.

---

## Foreign Accounts (FBAR / FinCEN 114)

Collect these whenever the taxpayer holds **any** account outside the US — bank,
brokerage, e-money wallet or credit card — regardless of whether it produced
taxable income. The filing test is the aggregate **maximum** value across all
such accounts exceeding $10,000 at any point in the year, so a single account
that looks trivial can still be the one that trips it.

- [ ] Statements for each account covering the **whole calendar year** — the
      maximum can only be relied on from statements that fairly reflect it.
      Period-end summaries alone hide intra-period peaks.
- [ ] Account number **as the institution states it** (Item 18 takes it
      verbatim, including punctuation and non-Latin characters)
- [ ] Institution name, full mailing address, city, postal code and country
      (Items 17, 19–23)
- [ ] Account type: bank, securities, or other (with a description)
- [ ] Ownership: separately owned, jointly owned (with the number of joint
      owners), or signature authority only
- [ ] Opening date for any account opened during the year; closing date for any
      account closed during the year
- [ ] **Form 114a** if a spouse or a third-party preparer will file on the
      taxpayer's behalf — the two authorizations are independent
- [ ] Treasury Reporting Rate of Exchange for **December 31** of the tax year,
      for every non-USD currency held
      (<https://fiscaldata.treasury.gov/datasets/treasury-reporting-rates-exchange/>)
- [ ] Prior year's roster or filed Form 114 (see above)

> Supporting records must be retained for **five years from the FBAR due date**.
> Collect them into the workspace now — `fbar_reconciler.py` warns on any cited
> source document that is not actually there.

---

## Documentation Red Flags

Stop and request documentation when you see:
- Large charitable donations relative to income (need substantiation)
- Home office deduction (need measurements, exclusive use documentation)
- Business vehicle (need contemporaneous mileage log)
- Large medical expenses (need receipts)
- Casualty losses (need before/after appraisals, insurance claims)
- Gambling losses (need session log with wins and losses)
- Non-cash charitable over $500 (need Form 8283)
- Non-cash charitable over $5,000 (need qualified appraisal)

---

## Missing Document Resolution

When documents are missing:
1. **Can obtain**: Direct user to source (IRS transcript, employer, institution)
2. **Reconstructible**: Help reconstruct from bank statements, credit cards
3. **Irreplaceable**: Document what's available, note limitations
4. **Required for deduction**: Cannot claim without - explain consequences

**IRS Transcript Options** (when documents lost):
- Wage and Income Transcript: Shows all W-2, 1099 data reported to IRS
- Account Transcript: Shows processed return data
- Request at: IRS.gov/Individuals/Get-Transcript
