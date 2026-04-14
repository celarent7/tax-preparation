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
- **Alternative Minimum Tax (AMT)**: While the tool uses standard brackets, it does not perform a full Form 6251 AMT calculation.
- **Energy Credits**: High-depth technical validation of specific heat pump or solar equipment eligibility.
- **Estate & Gift Taxes**: Form 706 or Form 709.

---
**Recommendation**: If your situation involves any of the above, please use this tool only for preliminary data gathering and consult a **Qualified Tax Professional** before filing.
