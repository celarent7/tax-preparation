#!/usr/bin/env python3
"""Regenerate the committed golden corpus under `examples/statements/`.

Run: `python3 tests/build_golden_statements.py`

The corpus is **committed**, not generated at test time, because it is the
regression target: a change that alters what a reader extracts should show up
as a failing assertion against fixed bytes, not as a fixture that quietly moves
with the code.

**Every file is written from scratch to exercise a shape.** None is a live
statement with the numbers edited, which is how PII survives into a public
repository (plan §10.2). The names, addresses, IBANs and card numbers are
invented placeholders and the figures are chosen to make an arithmetic point.

The expected answers are hand-computed in `examples/statements/README.md` and
asserted by `tests/test_statement_golden.py`.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from statement_fixtures import (  # noqa: E402
    bancolombia_card_workbook,
    bancolombia_savings_workbook,
    tbank_summary_text,
    tbc_workbook,
)

GOLDEN = os.path.join(ROOT, "examples", "statements")


def build():
    os.makedirs(GOLDEN, exist_ok=True)

    # -- Shape A, multi-currency: per-currency peaks on DIFFERENT dates -----
    # This is the fixture that demonstrates why the sum-of-maxima answer is
    # wrong. USD peaks in March, EUR peaks in September; the combined peak is
    # March, and it is smaller than either sum.
    multicurrency = os.path.join(GOLDEN, "multicurrency")
    os.makedirs(multicurrency, exist_ok=True)
    tbc_workbook(os.path.join(multicurrency, "account_statement_USD.xlsx"), "USD", [
        ("2025-01-01", "Opening balance", "", "1000.00", "1000.00"),
        ("2025-03-15", "Incoming transfer", "", "2000.00", "3000.00"),
        ("2025-08-20", "Outgoing transfer", "2500.00", "", "500.00"),
        ("2025-12-31", "Year-end position", "0", "", "500.00"),
    ])
    tbc_workbook(os.path.join(multicurrency, "account_statement_EUR.xlsx"), "EUR", [
        ("2025-01-01", "Opening balance", "", "400.00", "400.00"),
        ("2025-09-10", "Incoming transfer", "", "3600.00", "4000.00"),
        ("2025-12-31", "Year-end position", "0", "", "4000.00"),
    ])

    # -- Shape A, single currency, quarterly, non-padded D/MM dates ---------
    # Sparse SUCURSAL / DCTO. columns, two same-date rows, and a Q1 period that
    # opens in the PRIOR calendar year.
    savings = os.path.join(GOLDEN, "savings")
    os.makedirs(savings, exist_ok=True)
    bancolombia_savings_workbook(
        os.path.join(savings, "Extracto_2025Q1.xlsx"), "2024/12/31", "2025/03/31", [
            ("31/12", "Saldo inicial", "0.00", "4,000,000.00"),
            ("9/01", "Consignación", "400,000.00", "4,400,000.00"),
            ("9/01", "Retiro", "-200,000.00", "4,200,000.00"),
            ("15/02", "Consignación", "8,000,000.00", "12,200,000.00"),
        ])
    bancolombia_savings_workbook(
        os.path.join(savings, "Extracto_2025Q2Q4.xlsx"), "2025/04/01", "2025/12/31", [
            ("5/04", "Nota de ajuste", "0.00", "12,200,000.00"),
            ("31/12", "Retiro", "-200,000.00", "12,000,000.00"),
        ])

    # -- Shape B: period-end summaries, no transaction detail ---------------
    # Two periods running the 22nd to the 21st, so the year is uncovered at
    # both ends. Filenames deliberately do NOT sort into period order.
    summaries = os.path.join(GOLDEN, "summaries")
    os.makedirs(summaries, exist_ok=True)
    with open(os.path.join(summaries, "text.txt"), "w", encoding="utf-8") as handle:
        handle.write(tbank_summary_text(
            month="December", year="2025",
            open_date="22.11.2025", open_balance="+100 000,00 ₽",
            close_date="21.12.2025", close_balance="+150 000,00 ₽",
            income="+70 000,00 ₽", expenses="−20 000,00 ₽"))
    with open(os.path.join(summaries, "text 2.txt"), "w", encoding="utf-8") as handle:
        handle.write(tbank_summary_text(
            month="November", year="2025",
            open_date="22.10.2025", open_balance="+90 000,00 ₽",
            close_date="21.11.2025", close_balance="+100 000,00 ₽",
            income="+10 000,00 ₽", expenses="−0,00 ₽"))

    # -- Shape D: a two-sheet credit card, December missing -----------------
    cards = os.path.join(GOLDEN, "cards")
    os.makedirs(cards, exist_ok=True)
    bancolombia_card_workbook(
        os.path.join(cards, "Extracto_202501_Mastercard_Detallado_0001.xlsx"),
        "30/12/2024", "30/01/2025",
        pesos=[("30/12", "Compra", "-400,000.00", "-400,000.00"),
               ("15/01", "Pago", "450,000.00", "50,000.00")],
        dolares=[("10/01", "Compra", "-100.00", "-100.00")])
    bancolombia_card_workbook(
        os.path.join(cards, "Extracto_202502_Mastercard_Detallado_0001.xlsx"),
        "30/01/2025", "28/02/2025",
        pesos=[("5/02", "Compra", "-850,000.00", "-800,000.00")],
        dolares=[("20/02", "Pago", "100.00", "0.00")])

    print(f"Wrote the golden corpus under {GOLDEN}")


if __name__ == "__main__":
    build()
