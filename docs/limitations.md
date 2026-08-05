# Scope Limitations & Out-of-Scope Scenarios

The Tax Preparation Plugin is designed for standard US individual and family tax preparation. To ensure accuracy and reduce liability, users should be aware of the following **out-of-scope** scenarios. 

**This tool is NOT designed to handle the following without professional CPA oversight:**

## 1. Complex Business Entities
- **K-1 Income**: Complex partnership allocations, basis tracking for S-Corp shareholders, or passive activity loss limitations.
- **Corporate Returns**: Form 1120 or 1120-S preparation.
- **Complex Rental Activity**: Short-term rental loops (e.g., 7-day rule) or complex depreciation/recapture beyond standard MACRS.

## 2. International Situations
- **Foreign Earned Income**: Form 2555 (FEIE) or complex housing exclusions.
- **FATCA/FBAR Depth**: While we provide an FBAR tracker, complex offshore trust reporting (Form 3520) is not supported.
- **Non-Resident Aliens**: Form 1040-NR and treaty-based position claims.

## 3. Advanced Investment Logic
- **Cross-Broker Wash Sales**: The RSU/Investment scripts analyze sales within the provided data. They cannot automatically detect wash sales occurring across different brokerage firms unless all data is merged into a single input.
- **Crypto Complexity**: Complex DeFi activities, liquidity pooling, or NFT bridging are not explicitly handled by the core scripts.
- **Section 1256 Contracts**: Specialized treatment for futures and options.

## 4. Specific Tax Credits & Taxes
- **Alternative Minimum Tax (AMT)**: The tool does not perform a full Form 6251 AMT calculation. It **does** emit an `amt_risk` warning when an ISO bargain element, a large SALT amount lost to the cap, or income inside the exemption phase-out zone is present — a trigger flag + an optional quantitative quick screen (`tax_calculator.py --amt-quick-test`, with `--salt-deducted` for the SALT add-back and `--iso-bargain-element` for the ISO spread). The screen approximates AMTI and tentative minimum tax at 26%/28% and reports whether Form 6251 is likely required; it deliberately ignores preferential-rate carve-outs and several preference items (named in its output), so it overstates TMT for filers with large LTCG/qualified-dividend income. Full Form 6251 remains out of scope.
- **Energy Credits**: High-depth technical validation of specific heat pump or solar equipment eligibility. Note that most clean-energy credits were terminated by OBBBA during 2025 — see the termination table in `references/credits_guide.md`.
- **Estate & Gift Taxes**: Form 706 or Form 709.
- **QBI (§199A)**: The constants carry the thresholds and phase-in ranges, but no full QBI calculator with SSTB / W-2-wage / UBIA limits is implemented.

## 5. State Coverage

- **California** is the only state with a real engine: progressive brackets, Schedule CA non-conformity add-backs (HSA, QBI, OBBBA deductions), CA standard-vs-itemized, exemption and renter's credits, the Behavioral Health Services Tax, SDI, and the CA estimated-tax rules. **Tax years 2023–2025** are populated from FTB/EDD primary sources (2026 is not yet published by the FTB and raises rather than approximating). Note that **2023 SDI was capped** (rate 0.9%, wage cap $153,164) — the last capped year before SB 951 removed the cap for 2024+ — so the excess-SDI credit (Form 540 line 74) is live for amended 2023 returns with two or more employers; for 2024–2025 (uncapped) it cannot arise. The CA 529 non-conformity calculator and reviewer check are **2025-only** (see plan C4); 2023/2024 529 distributions are out of scope for that screen.
- **WA, TX, FL, NV** are recognized as no-income-tax states. The WA capital gains excise tax is modeled but its annual standard deduction is unverified, so it returns no figure rather than a stale one.
- **All other states** carry only a **top marginal rate flagged `estimate_only`**. That is not an effective rate and must never be presented as one.

## 6. Unverified Constants

Some statutory figures could not be confirmed against a primary source during
the last data refresh and are stored as `null` with an entry in
`_sources.TODO_VERIFY` in `scripts/tax_constants.json`. Reading one raises
`UnverifiedConstantError` rather than returning a guess. Currently this includes
the 2026 SALT cap, the 2026 Social Security wage base, and the 2023/2026
retirement-plan contribution limits. See the remediation log for the full list.

---
**Recommendation**: If your situation involves any of the above, please use this tool only for preliminary data gathering and consult a **Qualified Tax Professional** before filing.
