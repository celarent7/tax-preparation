#!/usr/bin/env python3
"""The PDF text-extraction layer: containers only, no parsing.

Every register-parsing rule is exercised against literal `List[str]` in
`test_statement_readers.py`. What is left here is the one thing that genuinely
needs a PDF: that a real file turns into the lines the reader expects, and that
a file yielding no text says so rather than looking empty.

The fixture is synthetic, built by `tests/build_golden_pdf.py`. This repository
is public and a real register carries the client's name and account numbers.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from statement_parser import StatementParseError  # noqa: E402
from statement_pdf import extract_text_lines  # noqa: E402

from build_golden_pdf import build_pdf  # noqa: E402

FIXTURE = os.path.join(ROOT, "examples", "statements", "movements",
                       "tbank_movements_synthetic.pdf")


def test_extraction_returns_the_lines_in_document_order():
    lines = extract_text_lines(FIXTURE)
    assert any(line.strip().startswith("Movement of funds for the period")
               for line in lines)
    # `layout=True` indents each line to its position on the page, so the date
    # is found after stripping. The lines themselves are NOT stripped by the
    # extraction layer: horizontal spacing is what keeps the two amount columns
    # apart, and that is the evidence the reader is reading.
    dates = [line.split()[0] for line in lines if line.strip()[:2].isdigit()]
    # Document order, NOT time order: the register prints newest-first and
    # this layer must not quietly sort it. Reversing is the reader's job.
    assert dates == ["02.11.2025", "14.07.2025", "20.03.2025", "05.01.2025"]


def test_a_pdf_that_yields_no_text_is_an_explicit_error(tmp_path):
    """A scanned/image-only PDF extracts to nothing. Returning [] would let the
    reader report an empty export — a no-data finding — when the truth is that
    the file was never read. Those are different findings and only one of them
    is the filer's problem.

    The fixture is a VALID, well-formed PDF carrying an empty text stream —
    which is what a scan looks like to a text extractor. A truncated or
    malformed file would take the different branch below, and using one here
    would leave this branch untested while appearing to cover it.
    """
    scan = tmp_path / "scan.pdf"
    scan.write_bytes(build_pdf([]))
    with pytest.raises(StatementParseError, match="no extractable text"):
        extract_text_lines(str(scan))


def test_a_file_that_is_not_a_pdf_is_a_different_error(tmp_path):
    """Distinct from the empty-scan case on purpose. `no extractable text` says
    the document was read and stated nothing; this says the document was never
    opened. Reporting the second as the first would blame the account for a
    problem in the file."""
    broken = tmp_path / "truncated.pdf"
    broken.write_bytes(b"%PDF-1.4\n%%EOF\n")
    with pytest.raises(StatementParseError, match="cannot be opened as a PDF"):
        extract_text_lines(str(broken))


def test_the_synthetic_fixture_reads_end_to_end_and_reconciles():
    """The one test that puts a real PDF through the real reader. Everything
    else about the register's rules is pinned against literal lines; what this
    adds is that `layout=True` actually keeps the two amount columns apart in
    a file pdfplumber produced, and that the stated totals reconcile."""
    from statement_parser import read_pdf_movement_register
    from statement_profiles import TBANK_MOVEMENTS

    result = read_pdf_movement_register(FIXTURE, TBANK_MOVEMENTS, currency="RUB")
    assert len(result.movements) == 4
    assert [m.date.month for m in result.movements] == [1, 3, 7, 11]
    assert all(m.balance_after is None for m in result.movements)
    assert any("no balance of any kind" in w for w in result.warnings)
