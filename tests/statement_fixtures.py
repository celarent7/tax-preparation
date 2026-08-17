#!/usr/bin/env python3
"""Synthetic statement builders shared by the Phase 0 parser tests.

**Every fixture here is written from scratch to exercise a SHAPE.** No file is
ever produced by copying a live statement and editing the numbers, which is how
PII survives into a public repository (plan §10.2). The names, addresses and
account numbers below are invented placeholders and the figures are chosen to
make an arithmetic point, not to resemble anyone's balances.
"""

import re
import zipfile

import openpyxl

# Invented identifiers. Shaped like the real ones so the parsing is exercised,
# resembling nobody.
FAKE_IBAN = "GE00TB0000000000000002"
FAKE_CARD = "************0000"
FAKE_CLIENT = "NOMBRE APELLIDO"
FAKE_ADDRESS = "CALLE 00 # 00-00 CIUDAD"


def write_xlsx(path, sheets, strip_dimensions=False):
    """Write a workbook from `{sheet_name: [[row values], ...]}`.

    `strip_dimensions=True` removes the `<dimension/>` record from every sheet,
    which is what makes `ws.max_row` report `None` under `read_only=True` —
    hazard 9.11, and the only way to build a fixture that actually exercises
    it. openpyxl always emits the record, so a fixture saved normally would
    pass the test without testing anything.
    """
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        worksheet = workbook.create_sheet(title=name)
        for row in rows:
            worksheet.append(row)
    workbook.save(str(path))

    if strip_dimensions:
        _strip_dimension_records(str(path))
    return str(path)


def _strip_dimension_records(path):
    source = zipfile.ZipFile(path)
    entries = [(item, source.read(item.filename)) for item in source.infolist()]
    source.close()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        for item, data in entries:
            if item.filename.startswith("xl/worksheets/"):
                data = re.sub(rb"<dimension[^>]*/>", b"", data)
            target.writestr(item, data)


def tbc_sheet_name(currency):
    return f"{FAKE_IBAN}-{currency}"


def tbc_workbook(path, currency, rows, *, strip_dimensions=False):
    """A TBC-shaped file: a `Summary` sheet plus one transaction sheet.

    Two header rows — Georgian first, English second, data from the third
    (hazard 9.9). `rows` are `(date, description, paid_out, paid_in, balance)`.
    TBC writes `''` into the unused side of the Paid Out / Paid In pair, and
    the fixtures pass that through verbatim (hazard 9.12).
    """
    transactions = [
        ["თარიღი", "დანიშნულება", "გატანა", "შემოტანა", "ნაშთი"],
        ["Date", "Description", "Paid Out", "Paid In", "Balance"],
    ]
    transactions.extend(list(row) for row in rows)
    return write_xlsx(path, {
        "Summary": [["Account", FAKE_IBAN], ["Currency", currency]],
        tbc_sheet_name(currency): transactions,
    }, strip_dimensions=strip_dimensions)


def bancolombia_savings_workbook(path, period_start, period_end, rows,
                                 *, strip_dimensions=False):
    """A Bancolombia savings-shaped file: one `Extracto` sheet.

    Metadata block, `Resumen:` totals, then `Movimientos:` and the six-column
    header. `rows` are `(fecha, descripcion, valor, saldo)` and are written with
    `SUCURSAL` and `DCTO.` EMPTY, exactly as the corpus has them — a six-column
    header that consistently yields four values (hazard 9.1).

    The client name and address sit in the metadata block so the tests can
    assert they never reach a MovementRecord (plan §10.2).
    """
    sheet = [
        ["Extracto de cuenta de ahorros"],
        ["Cliente:", FAKE_CLIENT],
        ["Dirección:", FAKE_ADDRESS],
        ["Período Desde:", period_start],
        ["Período Hasta:", period_end],
        [],
        ["Resumen:"],
        ["Saldo anterior", "0.00"],
        [],
        ["Movimientos:"],
        ["FECHA", "DESCRIPCIÓN", "SUCURSAL", "DCTO.", "VALOR", "SALDO"],
    ]
    for fecha, descripcion, valor, saldo in rows:
        sheet.append([fecha, descripcion, None, None, valor, saldo])
    return write_xlsx(path, {"Extracto": sheet}, strip_dimensions=strip_dimensions)


def bancolombia_card_workbook(path, period_start, period_end, pesos, dolares):
    """A Bancolombia card-shaped file: `DOLARES` and `PESOS` for the same card.

    The billing period runs ~30th to ~30th, so a January file opens on
    December 30 of the PRIOR calendar year. Rows are `(fecha, descripcion,
    valor, saldo)`; balances are LIABILITIES and are normally negative.
    """
    def sheet(rows):
        block = [
            ["Estado de cuenta Mastercard"],
            ["Tarjeta", FAKE_CARD],
            ["Cupo Total", "1,000.00"],
            ["Período Facturado Desde:", period_start],
            ["Período Facturado Hasta:", period_end],
            [],
            ["FECHA", "DESCRIPCIÓN", "VALOR", "SALDO"],
        ]
        block.extend(list(row) for row in rows)
        return block

    return write_xlsx(path, {"DOLARES": sheet(dolares), "PESOS": sheet(pesos)})


def empty_wise_workbook(path):
    """Wise's actual shape: an `All transactions` sheet that is ONE cell.

    Not a header row — a single cell containing `ID`. This is a *load* case as
    well as a *no-data* case, and it must yield "no data", not a parse error
    and not `$0` (hazard 9.6).
    """
    return write_xlsx(path, {"All transactions": [["ID"]]})


def tbank_summary_text(month, year, open_date, open_balance,
                       close_date, close_balance,
                       income="0 ₽", expenses="−0 ₽"):
    """T-Bank's whole file: two bounding balances and four totals, ~179 bytes.

    There is no transaction detail. Everything between the two balances is
    invisible, which is why this shape must never yield a determined maximum.
    The minus sign is U+2212, not an ASCII hyphen (hazard 9.4).
    """
    return (
        f"Statement for {month} {year}\n"
        f"Balance on {open_date}: {open_balance}\n"
        f"Income: {income}\n"
        f"Expenses: {expenses}\n"
        f"Interest: 0 ₽\n"
        f"Cashback: 0 ₽\n"
        f"Balance on {close_date}: {close_balance}\n"
    )
