"""Generate the synthetic movement-register PDF fixture.

Hand-rolled rather than produced by a library: the fixture exists to prove the
extraction layer finds the right LINES, which needs a valid PDF with a known
uncompressed text stream and nothing more. Adding a PDF writer as a dependency
to make test data would be a heavier commitment than the fixture is worth.

The content is invented. Real registers carry the client's name, address and
account numbers, and this repository is public.
"""
import os

LINES = [
    "Movement of funds statement",
    "Client: NOMBRE APELLIDO",
    "Contract number: 0000000000",
    "Account number: 00000000000000000000",
    "Movement of funds for the period 01.01.2025 - 31.12.2025",
    "Date        Settled     Operation      Account",
    # NEWEST FIRST, as the real register prints it. The reader reverses these,
    # so a fixture written in chronological order would pass every assertion
    # about WHICH rows were found while never exercising the reversal — and the
    # reversal is the whole reason `newest_first` exists.
    #
    # MIXED SEPARATORS, also as the real register prints it: the rows are
    # DOT-decimal and the totals below are COMMA-decimal, in one document.
    # The figures are invented; only the typography is copied. A fixture
    # uniform in one separator passes every assertion here while never
    # exercising the two-format split, which is how that gap survived a full
    # green suite once already.
    "02.11.2025  02.11.2025  -1 100.00 R    -1 100.00 R",
    "14.07.2025  14.07.2025  +2 500.00 R    +2 500.00 R",
    "20.03.2025  20.03.2025  -250.50 R      -250.50 R",
    "05.01.2025  05.01.2025  +1 000.00 R    +1 000.00 R",
    "Deposits: 3 500,00 R",
    "Expenses: 1 350,50 R",
]


def build_pdf(lines):
    """Return the bytes of a one-page PDF whose text stream is `lines`."""
    stream_parts = ["BT", "/F1 10 Tf", "12 TL", "40 750 Td"]
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        stream_parts.append(f"({escaped}) Tj T*")
    stream_parts.append("ET")
    stream = "\n".join(stream_parts).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n").encode()
    return bytes(out)


def main():
    target = os.path.join(os.path.dirname(__file__), os.pardir, "examples",
                          "statements", "movements")
    os.makedirs(target, exist_ok=True)
    path = os.path.join(target, "tbank_movements_synthetic.pdf")
    with open(path, "wb") as handle:
        handle.write(build_pdf(LINES))
    print(f"wrote {os.path.realpath(path)}")


if __name__ == "__main__":
    main()
