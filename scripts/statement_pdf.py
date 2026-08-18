#!/usr/bin/env python3
"""PDF -> text lines. The ONLY module that imports a PDF library.

Kept separate from `statement_parser` so the container concern and the parsing
concern can be tested apart: every register-parsing rule is exercised against
literal `List[str]` in `test_statement_readers.py`, with no PDF involved.

`layout=True` preserves horizontal spacing, which is what makes a register's
columns survive as text. Without it pdfplumber emits words in reading order and
the amount columns collide — an operation amount and an account amount become
one token, which is a silent wrong number rather than a parse failure.
"""

import os
import sys
from typing import List

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from statement_parser import StatementParseError  # noqa: E402


class PdfExtractionError(StatementParseError):
    """The file could not be turned into text at all."""


def extract_text_lines(path: str) -> List[str]:
    """Every text line of every page, in document order.

    Lines are returned VERBATIM, leading and trailing spaces included. The
    indentation is not noise: it is the column geometry `layout=True` exists to
    preserve, and the reader's patterns are anchored with `\\s*` precisely so
    that this layer never has to decide which spaces mattered.

    Raises rather than returning `[]` when nothing extracts. An image-only
    scan and an empty statement are different findings: the second is a
    no-data result about the account, the first means this tool never read the
    file. Collapsing them would let a scan be reported as an empty export.
    """
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise PdfExtractionError(
            "pdfplumber is required to read PDF statements; it is pinned in "
            "requirements.txt. Install it with `pip install -r requirements.txt`."
        ) from exc

    lines: List[str] = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text = page.extract_text(layout=True) or ""
                lines.extend(text.splitlines())
    except PdfExtractionError:
        raise
    except Exception as exc:
        raise PdfExtractionError(f"{path}: cannot be opened as a PDF — {exc}") from exc

    if not any(line.strip() for line in lines):
        raise PdfExtractionError(
            f"{path}: no extractable text. This is an image-only scan or an "
            "empty file. It is NOT an empty statement — that finding requires "
            "a file this tool actually read.")
    return lines
