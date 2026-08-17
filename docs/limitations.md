# Scope Limitations & Out-of-Scope Scenarios

The Tax Preparation Plugin is designed for standard US individual and family tax preparation. To ensure accuracy and reduce liability, users should be aware of the following **out-of-scope** scenarios. 

**This tool is NOT designed to handle the following without professional CPA oversight:**

## 1. Complex Business Entities
- **K-1 Income**: Complex partnership allocations, basis tracking for S-Corp shareholders, or passive activity loss limitations.
- **Corporate Returns**: Form 1120 or 1120-S preparation.
- **Complex Rental Activity**: Short-term rental loops (e.g., 7-day rule) or complex depreciation/recapture beyond standard MACRS.

## 2. International Situations
- **Foreign Earned Income**: Form 2555 (FEIE) or complex housing exclusions.
- **FBAR (FinCEN 114)**: `scripts/fbar_reconciler.py` produces **worksheets and findings, not a filed form**. It reconciles a year's foreign-account roster against the prior year, forces every prior-year account to an explicit disposition, and emits a keying worksheet labelled with Form 114 item numbers. It does **not** generate BSA E-Filing XML or a transmittable PDF (transmission goes through the taxpayer or their preparer), and covers **Part II (separately owned) and Part III (jointly owned) only** — Part IV (signature authority, no financial interest) and Part V (consolidated) are recorded in the roster but reconciled by hand. Several treatments it applies are **filer-adopted conventions unconfirmed against FinCEN guidance** (multi-currency sub-accounts, credit cards with no positive balance, negative balances as zero); they are surfaced in every report rather than presented as settled law. Statement parsing is a **separate upstream tool** — `scripts/statement_parser.py` (that is Phase 0) turns `.xlsx` and plain-text statements into balance timelines and derives `max_value_basis` from the coverage it found; the reconciler still takes only normalized JSON. Phase 0 is narrower than it sounds: **PDF statements are not parsed** and stay with the agent's `Read` tool, so a PDF-only institution gets no timeline; it does **not** decide reportability, ownership, which convention applies or whether to elect Item 15a, and it reports an empty export as "no data" rather than as a zero balance. See `references/fbar_guide.md`.
- **FATCA / Form 8938**: Not implemented. It is a separate regime from FBAR with different thresholds, a different agency and a different form, and filing one does not satisfy the other. Complex offshore trust reporting (Form 3520) is also not supported.
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
- **529 plan scope:** the CA 529 non-conformity coverage (the `ca_529_nonqualified_withdrawal` calculator, the `check_529` reviewer check, and the `plan_529_ca` savings caveat) models only the *distribution side* — federally-qualified-but-CA-non-qualified K-12 and Roth-rollover withdrawals, the earnings add-back, and the 2.5% CA additional tax. It does **not** model contribution-side multi-state deduction shopping (e.g. deducting contributions against another state's 529 plan while a CA resident), gift-tax / 5-year-election mechanics on contributions, or basis tracking across multiple distributions. Those are out of scope; the calculator takes the earnings portion as an input from the 529 statement.

## 6. Unverified Constants

Some statutory figures could not be confirmed against a primary source during
the last data refresh and are stored as `null` with an entry in
`_sources.TODO_VERIFY` in `scripts/tax_constants.json`. Reading one raises
`UnverifiedConstantError` rather than returning a guess. Currently this includes
the 2026 Social Security wage base and the 2023/2026 retirement-plan
contribution limits. (The 2026 SALT cap was resolved from the enrolled statute
text — IRC 164(b)(6)/(b)(7) as amended by Pub. L. 119-21 §70120 — and is no
longer unverified; see `_sources.obbba_salt_2026`.) See the remediation log for
the full list.

---
**Recommendation**: If your situation involves any of the above, please use this tool only for preliminary data gathering and consult a **Qualified Tax Professional** before filing.
