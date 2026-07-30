#!/usr/bin/env python3
"""Asserts the markdown reference library agrees with tax_constants.json.

Covers audit findings:
  Y3 — references/tax_brackets_deductions.md showed pre-OBBBA 2025 standard
       deductions while the constants showed the correct OBBBA amounts. SKILL.md
       tells an agent it may fall back to either, so the two must not disagree.
  Y4 — references/investment_taxes.md and references/tax_planning.md presented
       2024 LTCG/standard-deduction figures as current, with no year markers.

These are doc-only findings, so the test parses the published tables and
compares them against the authoritative constants rather than restating numbers.
"""

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REFS = os.path.join(ROOT, "references")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from utils import load_tax_constants  # noqa: E402

TAX_2025 = load_tax_constants("2025")
TAX_2024 = load_tax_constants("2024")


def read_ref(name):
    with open(os.path.join(REFS, name)) as f:
        return f.read()


def money(text):
    """'$15,750' -> 15750"""
    return int(text.replace("$", "").replace(",", "").strip())


def rows_between(text, start_marker, end_marker):
    """Extract markdown table rows between two HTML comment markers."""
    block = text.split(start_marker, 1)[1].split(end_marker, 1)[0]
    return [line for line in block.splitlines()
            if line.strip().startswith("|") and "---" not in line]


# ---------------------------------------------------------------------------
# Y3 — standard deductions in tax_brackets_deductions.md
# ---------------------------------------------------------------------------

STATUS_LABELS = {
    "Single": "single",
    "Married Filing Jointly": "married_jointly",
    "Married Filing Separately": "married_separately",
    "Head of Household": "head_of_household",
    "Qualifying Surviving Spouse": "qualifying_surviving_spouse",
}


def test_reference_standard_deductions_match_constants():
    """Y3: the published 2025 standard deduction table matches the constants."""
    rows = rows_between(read_ref("tax_brackets_deductions.md"),
                        "<!-- STANDARD_DEDUCTIONS -->", "<!-- /STANDARD_DEDUCTIONS -->")

    published = {}
    for row in rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[1].startswith("$"):
            label = cells[0].replace("*", "").strip()
            if label in STATUS_LABELS:
                published[STATUS_LABELS[label]] = money(cells[1])

    assert published, "could not parse the standard deduction table"
    for status, amount in published.items():
        assert amount == TAX_2025["standard_deductions"][status], (
            f"reference says {status}={amount}, constants say "
            f"{TAX_2025['standard_deductions'][status]}"
        )


def test_reference_does_not_contain_pre_obbba_2025_amounts():
    """Y3: the superseded $15,000/$30,000/$22,500 trio must not read as current.

    They may still appear inside the explanatory warning that names them as
    wrong, so this checks the parsed table specifically, then asserts the
    warning is present to explain them.
    """
    text = read_ref("tax_brackets_deductions.md")
    rows = rows_between(text, "<!-- STANDARD_DEDUCTIONS -->", "<!-- /STANDARD_DEDUCTIONS -->")
    table = "\n".join(rows)
    for stale in ("$15,000", "$30,000", "$22,500"):
        assert stale not in table, f"pre-OBBBA figure {stale} still in the live table"
    assert "OBBBA" in text


# ---------------------------------------------------------------------------
# Y4 — LTCG tables in investment_taxes.md
# ---------------------------------------------------------------------------

LTCG_LABELS = {
    "Single": "single",
    "Married Filing Jointly / QSS": "married_jointly",
    "Married Filing Separately": "married_separately",
    "Head of Household": "head_of_household",
}


@pytest.mark.parametrize("marker,tax_data,year", [
    ("LTCG_TABLE_2025", TAX_2025, "2025"),
    ("LTCG_TABLE_2024", TAX_2024, "2024"),
])
def test_investment_taxes_ltcg_tables_match_constants(marker, tax_data, year):
    """Y4: each LTCG table is year-stamped and matches that year's constants."""
    rows = rows_between(read_ref("investment_taxes.md"),
                        f"<!-- {marker} -->", f"<!-- /{marker} -->")

    checked = 0
    for row in rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) != 4 or cells[0] not in LTCG_LABELS:
            continue
        status = LTCG_LABELS[cells[0]]
        brackets = tax_data["ltcg_brackets"][status]

        # "$0 - $48,350" -> the 0% ceiling is the second figure
        zero_ceiling = money(cells[1].split("-")[-1])
        assert zero_ceiling == brackets[0]["top"], f"{year} {status} 0% ceiling"

        # "$48,351 - $533,400" -> the 15% ceiling is the second figure
        fifteen_ceiling = money(cells[2].split("-")[-1])
        assert fifteen_ceiling == brackets[1]["top"], f"{year} {status} 15% ceiling"
        checked += 1

    assert checked == 4, f"expected 4 filing-status rows in {marker}, parsed {checked}"


def test_investment_taxes_tables_are_year_marked():
    """Y4: the file carries explicit year markers, not bare 'current' tables."""
    text = read_ref("investment_taxes.md")
    assert "<!-- YEAR -->2025<!-- /YEAR -->" in text
    assert "<!-- YEAR -->2024<!-- /YEAR -->" in text


def test_tax_planning_standard_deduction_matches_constants():
    """Y4: tax_planning.md's standard deduction list is 2025, not 2024."""
    text = read_ref("tax_planning.md")
    section = text.split("### Standard Deduction", 1)[1].split("###", 1)[0]

    assert "<!-- YEAR -->2025<!-- /YEAR -->" in section
    for label, status in STATUS_LABELS.items():
        if label in ("Qualifying Surviving Spouse",):
            continue
        expected = TAX_2025["standard_deductions"][status]
        assert f"${expected:,}" in section, (
            f"tax_planning.md standard deduction table missing {label} = ${expected:,}"
        )

    # The superseded 2024 amounts must no longer be presented as current. They
    # may appear in the explanatory note that names them as superseded, so this
    # checks the live table rows only.
    table_rows = "\n".join(line for line in section.splitlines()
                           if line.strip().startswith("|"))
    for stale in ("$14,600", "$29,200", "$21,900"):
        assert stale not in table_rows, f"stale 2024 figure {stale} still in the table"


def test_tax_planning_capital_gains_matches_constants():
    """Y4: tax_planning.md's capital gains summary uses 2025 thresholds."""
    text = read_ref("tax_planning.md")
    section = text.split("### Capital Gains Rates", 1)[1].split("###", 1)[0]

    single = TAX_2025["ltcg_brackets"]["single"]
    mfj = TAX_2025["ltcg_brackets"]["married_jointly"]
    for expected in (single[0]["top"], mfj[0]["top"], single[1]["top"], mfj[1]["top"]):
        assert f"${expected:,}" in section, f"missing ${expected:,}"


# ---------------------------------------------------------------------------
# F2 — the SALT rule in the draft review checklist
# ---------------------------------------------------------------------------

def test_checklist_salt_rule_is_year_aware():
    """F2: the checklist must not flag a correct 2025 SALT figure as an error."""
    text = read_ref("draft_review_checklist.md")

    assert "SALT cap by year" in text
    salt_2025 = TAX_2025["salt"]
    assert f"${salt_2025['cap']:,}" in text
    assert f"${salt_2025['cap_mfs']:,}" in text
    assert f"${salt_2025['phasedown_magi_start']:,}" in text

    # The old unconditional rule must be gone.
    assert "Schedule A Line 6 > $10,000" not in text
    assert "MUST be capped at $10,000" not in text


# ---------------------------------------------------------------------------
# D5 — references must not name scripts that do not exist
# ---------------------------------------------------------------------------

def test_every_script_referenced_in_docs_exists():
    """D5: `tax_planning.md` invoked a "Comparison Script", a
    "portfolio-analyzer" integration and an "Analysis Script" that are not in
    this repo (upstream leftovers). An agent following those instructions hits
    a missing file mid-workflow.

    This scans every reference/doc for `scripts/<name>.py` and asserts the file
    is present, so the class of defect cannot be reintroduced.
    """
    import glob

    pattern = re.compile(r"(?:\./)?scripts/([A-Za-z0-9_/]+\.py)")
    missing = []

    doc_paths = (glob.glob(os.path.join(REFS, "**", "*.md"), recursive=True)
                 + glob.glob(os.path.join(ROOT, "docs", "*.md"))
                 + [os.path.join(ROOT, "skills", "tax-preparation", "SKILL.md")])

    for doc in doc_paths:
        with open(doc) as f:
            text = f.read()
        for name in pattern.findall(text):
            if not os.path.exists(os.path.join(ROOT, "scripts", name)):
                missing.append(f"{os.path.relpath(doc, ROOT)} -> scripts/{name}")

    assert not missing, "docs reference non-existent scripts: " + "; ".join(sorted(set(missing)))


# ---------------------------------------------------------------------------
# Y6 — plugin.json and SKILL.md metadata versions must stay in sync
# ---------------------------------------------------------------------------

def test_plugin_and_skill_versions_match():
    """Y6: plugin.json was 2.0.0 while SKILL.md metadata said 2.2.0."""
    import json

    with open(os.path.join(ROOT, "plugin.json")) as f:
        plugin_version = json.load(f)["version"]

    with open(os.path.join(ROOT, "skills", "tax-preparation", "SKILL.md")) as f:
        skill_text = f.read()
    match = re.search(r"^\s+version:\s*([0-9]+\.[0-9]+\.[0-9]+)\s*$", skill_text, re.M)
    assert match, "SKILL.md metadata has no version"

    assert plugin_version == match.group(1), (
        f"plugin.json {plugin_version} != SKILL.md {match.group(1)}"
    )


def test_skill_declares_the_supported_years_it_actually_has():
    """The tax-year stop rule is only useful if the advertised range is right."""
    from utils import SUPPORTED_YEARS

    with open(os.path.join(ROOT, "skills", "tax-preparation", "SKILL.md")) as f:
        text = f.read()
    assert f"{SUPPORTED_YEARS[0]}–{SUPPORTED_YEARS[-1]}" in text or \
           f"{SUPPORTED_YEARS[0]}-{SUPPORTED_YEARS[-1]}" in text


def test_skill_has_the_state_layer_step():
    """Blueprint 3.3(a): a CA filer must never get federal-only results."""
    with open(os.path.join(ROOT, "skills", "tax-preparation", "SKILL.md")) as f:
        text = f.read()
    assert "Step 3.5: State Layer" in text
    assert "CA impact" in text
    assert "california_tax_guide.md" in text


def test_skill_has_the_tax_year_stop_rule():
    """Blueprint 3.3(b): stop rather than approximate an unsupported year."""
    with open(os.path.join(ROOT, "skills", "tax-preparation", "SKILL.md")) as f:
        text = f.read()
    assert "Tax-year stop rule" in text
    assert "supported_years" in text


def test_skill_safe_harbor_note_includes_mfs_threshold():
    """Blueprint 3.3(c): the $75,000 MFS threshold must be stated (F9)."""
    with open(os.path.join(ROOT, "skills", "tax-preparation", "SKILL.md")) as f:
        text = f.read()
    assert "$75,000" in text and "$150,000" in text
