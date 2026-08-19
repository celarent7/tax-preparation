# Golden statement corpus — FBAR Phase 0

Synthetic statements covering four of the five shapes
`scripts/statement_parser.py` reads, plus the `sources.json` that maps them.
`tests/test_statement_golden.py` parses this directory and asserts the figures
**hand-computed below**.

The fifth shape — the PDF movement register in `movements/` — is deliberately
**not** in `sources.json` and not part of the golden run. The golden corpus
exists to check hand-computed *maxima*, and a register states no balance, so it
produces no maximum to compute. It is exercised end-to-end by
`tests/test_statement_pdf.py` instead.

### Configuring the register reader

Its source entry lives in its own file, **`sources.register.json`** — a
complete, validated `sources.json`, not a fragment. Copy the entry into a
workspace `sources.json` to run it:

```json
{
  "filer": "example",
  "institution": "register-bank",
  "path": "movements",
  "glob": "*_movements_synthetic.pdf",
  "reader": "pdf_movement_register",
  "profile": "tbank_movements",
  "account_key": "example-register-0005",
  "currency": "RUB"
}
```

**`currency` is mandatory.** A register prints amounts but never names the
account's currency, so the reader refuses to guess — defaulting it would label
rubles as dollars, which is a wrong number rather than an error. The same is
true of `text_period_summary`.

**What comes out:** movements, and an `estimate` basis. No balance, and
therefore **no maximum value** — a register cannot answer the FBAR
maximum-value question, and reconstructing levels from the deltas would assert
evidence the document does not carry. Answering that question needs a
balance-bearing statement or an Item 15a election.

The parse is checked against the document's own stated deposit and expense
totals; a register that states none is read, with a warning that nothing
could reconcile it.

> **Every file here was written from scratch to exercise a shape.** None is a
> real statement with the numbers edited — that is how PII survives into a
> public repository. The names, addresses, IBANs and card numbers are invented
> placeholders, and the figures were chosen to make an arithmetic point rather
> than to resemble anyone's balances. Regenerate with
> `python3 tests/build_golden_statements.py`.

Run it:

```bash
python ./scripts/statement_parser.py parse \
    --sources ./examples/statements/sources.json \
    --output-dir /tmp/phase0
```

The invented rates in `sources.json` are round numbers so the arithmetic is
checkable by eye: **EUR 2.0**, **COP 4,000**, **RUB 100** units per one USD.
They are labelled `source: "other"` rather than `"treasury"`, because they are
not Treasury rates and a fixture must not pretend otherwise.

---

## `example-multicurrency-0002` — why the sum of maxima is the wrong answer

Two files, one per currency, grouped into one FBAR entry by `account_group`.
**Per-currency peaks fall on different dates**, which is the whole point of the
fixture.

| Date | USD | EUR | EUR in USD | Combined |
|---|---|---|---|---|
| 2025-01-01 | 1,000 | 400 | 200 | **$1,200** |
| 2025-03-15 | 3,000 | 400 | 200 | **$3,200** ← peak |
| 2025-08-20 | 500 | 400 | 200 | **$700** |
| 2025-09-10 | 500 | 4,000 | 2,000 | **$2,500** |
| 2025-12-31 | 500 | 4,000 | 2,000 | **$2,500** |

- **Maximum: $3,200.00 on 2025-03-15**, components USD 3,000 + EUR 400.
- The **sum of per-currency maxima is $5,000** (3,000 USD peak + 2,000 from the
  EUR peak) — an instant that never existed. It is 56% too high.
- December 31 balance: **$2,500.00**.
- Basis: **`statement_full_year`**. Both files state a full-year period, every
  currency is observed from January 1, there is no gap and no continuity break.

Note what makes the full-year basis reachable at all: **both currencies carry
an observation on the same opening date.** The earliest event in a
multi-currency account always belongs to exactly one currency, so without a
shared opening every such account would carry an unknown prefix and cap at
`statement_partial`.

## `example-savings-0003` — quarterly, sparse columns, D/MM dates

Two files. The first period opens **2024-12-31**, in the prior calendar year;
the movement rows are non-padded `D/MM` while the period header is `YYYY/MM/DD`;
`SUCURSAL` and `DCTO.` are empty on every row, so a six-column header yields
four values; and 9 January carries **two rows**.

| Date | Amount | Balance (COP) | In USD |
|---|---|---|---|
| 2024-12-31 | 0 | 4,000,000 | $1,000 |
| 2025-01-09 | +400,000 | 4,400,000 | $1,100 |
| 2025-01-09 | −200,000 | 4,200,000 | $1,050 |
| 2025-02-15 | +8,000,000 | 12,200,000 | **$3,050** ← peak |
| 2025-04-05 | 0 | 12,200,000 | $3,050 |
| 2025-12-31 | −200,000 | 12,000,000 | $3,000 |

- **Maximum: $3,050.00 on 2025-02-15** (12,200,000 COP).
- December 31 balance: **$3,000.00**.
- Basis: **`statement_full_year`**. Q1 clipped to the year is 01-01..03-31 and
  the second file is 04-01..12-31 — day-adjacent, so coverage is complete. The
  second file opens at exactly what the first closed at, so continuity holds.
- The intraday peak of $1,100 on 9 January is **kept**: taking the day's last
  row would have discarded it. It does not become the year's maximum, but a
  timeline that silently dropped it would be wrong about January.

## `example-summary-0004` — period-end summaries can never be a full year

Two `.txt` files, each two bounding balances and four totals. **There is no
transaction detail**, so anything that happened between the balance dates is
invisible.

| File | Period | Opening | Closing |
|---|---|---|---|
| `text 2.txt` | 2025-10-22 .. 2025-11-21 | 90,000 ₽ | 100,000 ₽ |
| `text.txt` | 2025-11-22 .. 2025-12-21 | 100,000 ₽ | 150,000 ₽ |

- **Maximum: $1,500.00 on 2025-12-21** (150,000 RUB).
- Basis: **`period_end_summaries_only`** — and this is the cap the *evidence
  kind* imposes, not the gaps. Twelve such files would still be twelve blind
  periods.
- Two gaps: **2025-01-01..2025-10-21** (year start) and
  **2025-12-22..2025-12-31** (year end, the 22nd-to-21st cadence).
- Note the filenames: `text.txt` is the **December** period and `text 2.txt` is
  **November**. Sorting these by name shuffles the year, which is why the
  period comes from the balance lines.
- The totals reconcile: 90,000 + 10,000 − 0 = 100,000, and
  100,000 + 70,000 − 20,000 = 150,000. That check is what would catch a lost
  U+2212 on the `Expenses` line.

## `0001` — a credit card is a liability

Two files for one card, each with a `DOLARES` and a `PESOS` sheet. The account
key comes from the **filename suffix**, because one directory holds several
cards and nothing else distinguishes them.

| Date | PESOS | DOLARES | Combined |
|---|---|---|---|
| 2024-12-30 | −400,000 | *unobserved* | −$100 (bounded) |
| 2025-01-10 | −400,000 | −100 | −$200 |
| 2025-01-15 | +50,000 | −100 | **−$87.50** ← least negative |
| 2025-02-05 | −800,000 | −100 | −$300 |
| 2025-02-20 | −800,000 | 0 | −$200 |

- **No `max_usd` at all.** The maximum of a mostly-negative series is its
  least-negative point, which has no filing meaning until
  `credit_card_positive_balance_only` is applied — and applying it is a filing
  decision, not the parser's.
- `max_signed_usd`: **−$87.50 on 2025-01-15**. `max_positive_usd`: **$0.00**,
  because the *combined* balance was never positive, even though the PESOS
  sheet shows a 50,000 overpayment.
- Components at that instant are **signed**: COP +50,000 and USD −100. Flooring
  the negative one while leaving the stated maximum as the signed sum is
  exactly what `v3_recompute_usd` hard-errors on.
- Basis: **`statement_partial`** — March through December is uncovered, which
  is the live shape of the problem: the missing month is December, the one that
  would establish the year-end balance.

---

## What Phase 0 does not decide

Every figure above is a statement about **the files**. Whether these accounts
are reportable, who owns them, which conventions apply, whether to elect the
Item 15a checkbox, and which FX source to record are all Phase 1 decisions —
and they go through the Phase 2 user-confirmation gate before reaching a
roster. See `references/fbar_guide.md` §9.
