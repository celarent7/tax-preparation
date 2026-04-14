# User Usage Guide: Tax Preparation Plugin

This guide provides step-by-step instructions for the most common tax preparation tasks using this plugin.

## 1. Reviewing your W-2
Use this workflow to verify that your employer withheld the correct amount of tax and to identify any discrepancies.

**Steps:**
1. **Provide your W-2**: Upload a PDF of your W-2 or provide the values from Boxes 1, 2, 17, and 19.
2. **Run the Analysis**:
   ```bash
   python scripts/tax_calculator.py --income-sources w2_data.json
   ```
3. **Check Box 12**: If you have codes like `V` (Stock Options) or `D/E` (401k), the agent will automatically cross-reference these with your deduction limits.

## 2. Calculating RSU Capital Gains
RSUs are frequently double-taxed because brokers often report a $0 cost basis on Form 1099-B. This tool fixes that.

**Steps:**
1. **Gather Vesting Data**: You need your vesting confirmation statements (showing FMV at vest).
2. **Calculate Correct Basis**:
   ```bash
   python scripts/rsu_calculator.py basis --shares-vested 100 --fmv 150.00 --shares-withheld 30
   ```
3. **Analyze a Sale**:
   ```bash
   python scripts/rsu_calculator.py sale --vesting-file my_vestings.csv --sale-date 2025-06-01 --shares 50 --sale-price 175.00
   ```
4. **Form 8949**: The script will provide the "Adjustment Amount" and "Code B" you need to enter into your tax software to avoid overpaying.

## 3. Self-Employment Quarterly Payments
If you have 1099 income, you must pay estimated taxes to avoid penalties.

**Steps:**
1. **Estimate Net Income**: Total your gross receipts and subtract business expenses.
2. **Calculate Payments**:
   ```bash
   python scripts/estimated_tax_calculator.py --projected-income 85000 --prior-year-tax 12000
   ```
3. **Schedule**: The output will provide four specific dates and amounts for your 1040-ES vouchers.

## 4. Final Draft Review
Before you hit "File," run your data through the Draft Reviewer to catch common errors.

**Steps:**
1. **Export Draft**: Save a PDF or summary of your draft return from TurboTax/H&R Block.
2. **Run Review**:
   ```bash
   python scripts/draft_reviewer.py --draft draft_return.json --computed my_actual_data.json
   ```
3. **Fix Red Flags**: Pay attention to 🔴 **ERROR** items, which are high-likelihood audit triggers or math errors.
