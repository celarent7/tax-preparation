# Form 8949 Adjustment Guide

Use Form 8949 to report capital gains/losses and correct broker-reported cost
basis errors on 1099-B. The key distinction is between a broker who reported a
**wrong** basis (use code B with a signed column (g) adjustment) and a broker
who reported **no** basis at all (noncovered shares — enter the correct basis
directly in column (e), **no** code).

---

## The signed column (g) convention

**column (g) = reported basis − correct basis**

With code B, column (g) is the adjustment to gain/loss:

```
column (g) = (proceeds − correct basis) − (proceeds − reported basis)
           = reported basis − correct basis
```

- When the broker **under-reports** basis (reported < correct, the typical
  RSU double-taxation case where the 1099-B shows a basis that is too low),
  column (g) is **negative** — it reduces the gain (or increases the loss).
- When the broker **over-reports** basis (reported > correct), column (g) is
  **positive** — it increases the gain.
- When no adjustment is needed, leave column (g) blank (code field blank too).

> Enter column (g) as a **NEGATIVE number** when it reduces gain. A common
> filing mistake is to enter the adjustment as a positive number, which
> doubles the gain and over-taxes the sale. The sign is the whole point.

The legacy `adjustment_amount` field some tools emit equals
`correct basis − reported basis` (the positive magnitude); that is **not**
what goes in column (g). Column (g) is the signed mirror: `reported − correct`.

---

## When adjustments Are Required (code B)

Code B applies **only** when the 1099-B reported a basis that is **wrong** —
i.e. a nonzero figure that does not match the correct cost basis. For RSUs this
occurs when the broker reports:
- Grant-date FMV instead of vesting-date FMV
- A basis that does not include the W-2 ordinary-income component (ESPP
  disqualifying dispositions — see your employer's `*_rsu_specifics.md` notes,
  if you have added one)
- Any other incorrect nonzero figure

## When NO code is used (noncovered shares — Box B / Box E)

If the 1099-B reports **no** basis at all (blank, "N/A", or $0 for
noncovered shares), do **not** use code B. Instead:
- Enter the **correct** cost basis directly in **column (e)**
- Leave the adjustment code and column (g) **blank**
- The gain in column (h) is then `proceeds − correct basis` directly.

This is the Box B (short-term, basis not reported) or Box E (long-term, basis
not reported) reporting category — most older RSU lots fall here because the
broker was not required to report basis.

---

## Adjustment Codes

| Code | Meaning | When to Use |
|------|---------|-------------|
| B | Basis reported to IRS is incorrect | Broker reported a **wrong** (nonzero) basis on 1099-B |
| O | Other (describe in adjustment column) | Other adjustments not covered by specific codes |

> Code B is **not** for "basis missing". Missing/noncovered basis is a Box B/E
> category that takes the correct basis in column (e) with **no** code.

---

## Reporting Categories

Match the category from the 1099-B box:

| Box | Description | Form 8949 Section |
|-----|-------------|-------------------|
| A | Short-term, basis reported to IRS | Part I, Box A |
| B | Short-term, basis NOT reported | Part I, Box B |
| D | Long-term, basis reported to IRS | Part II, Box D |
| E | Long-term, basis NOT reported | Part II, Box E |

Box A/D (basis reported) → use code B **only if** the reported basis is wrong.
Box B/E (basis not reported) → put the correct basis in column (e), **no** code.

---

## Worked examples

### Example 1 — 1099-B reports NO basis (noncovered, Box E): no code

50 AMZN shares sold 03/20/2024 for $9,500; vested 01/15/2023 at $180 FMV.
Correct cost basis = 50 × $180 = $9,000. The 1099-B shows **no** basis
(noncovered lot, Box E).

```
Column (a): Description      - AMZN (50 sh)
Column (b): Date Acquired    - 01/15/2023
Column (c): Date Sold        - 03/20/2024
Column (d): Proceeds         - $9,500
Column (e): Cost Basis       - $9,000   (correct basis entered directly)
Column (f): Adjustment Code  - (blank — no code for noncovered)
Column (g): Adjustment Amount- (blank)
Column (h): Gain/Loss        - $500     ($9,500 − $9,000)
```

No adjustment code, no column (g). The correct basis goes straight into
column (e).

### Example 2 — 1099-B reports a WRONG basis (Box D): code B, negative column (g)

Same 50 shares, correct basis $9,000, but the broker reported a **wrong**
basis of $1,000 on the 1099-B (Box D, basis reported but incorrect).

```
Column (a): Description      - AMZN (50 sh)
Column (b): Date Acquired    - 01/15/2023
Column (c): Date Sold        - 03/20/2024
Column (d): Proceeds         - $9,500
Column (e): Cost Basis       - $1,000   (as reported on 1099-B)
Column (f): Adjustment Code  - B
Column (g): Adjustment Amount- −$8,000  (reported − correct = 1,000 − 9,000)
Column (h): Gain/Loss        - $500     ($9,500 − $9,000)
```

Column (g) = reported basis − correct basis = $1,000 − $9,000 = **−$8,000**,
entered as a **negative** number. It replaces the too-low reported gain
($9,500 − $1,000 = $8,500) with the correct $500 gain.

### Example 3 — broker OVER-reports basis (Box D): code B, positive column (g)

Correct basis $9,000; broker reported $10,000 (too high).

```
Column (e): Cost Basis       - $10,000  (as reported on 1099-B)
Column (f): Adjustment Code  - B
Column (g): Adjustment Amount- +$1,000  (reported − correct = 10,000 − 9,000)
Column (h): Gain/Loss        - -$500    ($9,500 − $9,000)
```

Here column (g) is **positive** — it increases the reported (too-low) gain to
the correct figure.

---

## Adjustment Calculation

```
column (g) = Reported Cost Basis − Correct Cost Basis

Where:
  Correct Cost Basis = Shares Sold × FMV at Vesting Date
  Reported Cost Basis = Value shown on 1099-B (must be nonzero for code B)

  Sign: negative when reported < correct (under-reported, the usual case)
        positive when reported > correct (over-reported)
```

The `calculate_lot_sale()` function in `scripts/rsu_calculator.py` returns
`column_g_adjustment` (this signed value) and `basis_missing_on_1099b` (True
when no basis was reported, meaning no code and the basis goes in column (e)).

---

## Generating Form 8949 Data

```bash
python ./scripts/rsu/generate_form_8949.py \
  --sales-file "sales_records.json" \
  --vesting-file "vesting_records.json" \
  --output "form_8949_data.json"
```

---

## Verification

After generating Form 8949 entries, verify each line:

1. **Noncovered (Box B/E, no code):** column (e) = correct basis; column (h) =
   proceeds − correct basis. Code and column (g) blank.
2. **Wrong basis (Box A/D, code B):** reported basis + column (g) = correct
   cost basis (i.e. column (g) = reported − correct, negative when the broker
   under-reported). Column (h) = proceeds − correct basis.
3. Short-term vs long-term classification matches the holding period
   (> 1 year measured by calendar anniversary, per Pub 544).
4. The sign of column (g): negative when reducing gain, positive when
   increasing it. A positive number for an under-reported basis is the
   classic double-taxation error.