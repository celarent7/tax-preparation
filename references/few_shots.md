# Few-Shot Examples for Tax Extraction & Analysis

Use these examples to guide high-accuracy data extraction and script parameterization.

## 1. W-2 Extraction (Box 12 Codes)
**Input**: Box 12 shows `D: 22,500`, `W: 3,000`, `V: 5,400`.
**Extraction Reasoning**:
- Code `D` is 401(k) contribution. Check against 2025 limit ($23,500).
- Code `W` is Employer HSA. Impact: Reduces deductible HSA limit for user.
- Code `V` is Income from Exercise of Nonstatutory Stock Options. Included in Box 1 already.

## 2. RSU Cost Basis Correction (Form 8949 Code B)
**Input**: 1099-B shows 50 shares sold, Proceeds $10,000, Cost Basis $0. Vesting statement shows FMV at vest was $180.00.
**Correct Analysis**:
- Broker basis is $0 (incorrect for RSU).
- Adjusted basis = 50 * $180 = $9,000.
- Form 8949 reporting:
  - Proceeds: $10,000
  - Cost Basis: $0 (per 1099-B)
  - Adjustment Code: **B**
  - Adjustment Amount: **$9,000**
  - Resulting Gain: $1,000

## 3. ESPP Disposition Type
**Scenario**: Offering Date 2023-01-01, Purchase Date 2023-06-30, Sale Date 2025-02-15.
**Logic**:
- > 2 years from Offering? (2023-01-01 to 2025-02-15 = Yes)
- > 1 year from Purchase? (2023-06-30 to 2025-02-15 = Yes)
- **Classification**: **Qualifying Disposition**. Use `espp_calculator.py basis` with appropriate parameters.

## 4. SALT Cap Optimization
**Scenario**: Property Tax $8,500, State Income Tax $4,200, State Sales Tax $1,500.
**Logic**:
- Compare Income Tax ($4,200) vs Sales Tax ($1,500). Choose $4,200.
- Total SALT = $8,500 + $4,200 = $12,700.
- Deductible amount = **$10,000** (IRS Cap).
- Lost to cap = $2,700.
