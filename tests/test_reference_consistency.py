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

    Forward-looking `docs/*_plan.md` roadmaps are excluded: a plan doc naming
    `scripts/<engine>.py` for a deferred block (e.g. `qbi_calculator_plan.md`,
    `savings_engine_plan.md` before its scripts exist) is a roadmap, not an
    operational instruction that sends an agent to a missing file mid-workflow.
    """
    import glob

    pattern = re.compile(r"(?:\./)?scripts/([A-Za-z0-9_/]+\.py)")
    missing = []

    doc_paths = (glob.glob(os.path.join(REFS, "**", "*.md"), recursive=True)
                 + [p for p in glob.glob(os.path.join(ROOT, "docs", "*.md"))
                    if not p.endswith("_plan.md")]
                 + [os.path.join(ROOT, "skills", "tax-preparation", "SKILL.md")])

    for doc in doc_paths:
        with open(doc) as f:
            text = f.read()
        for name in pattern.findall(text):
            if not os.path.exists(os.path.join(ROOT, "scripts", name)):
                missing.append(f"{os.path.relpath(doc, ROOT)} -> scripts/{name}")

    assert not missing, "docs reference non-existent scripts: " + "; ".join(sorted(set(missing)))


# ---------------------------------------------------------------------------
# FBAR — fbar_guide.md figures must match the constants and the code registry
# ---------------------------------------------------------------------------


def test_fbar_guide_figures_match_constants():
    """Every dollar figure and period in the rules table comes from the constants.

    The guide is the human-readable face of the `fbar` block; a number restated
    here that drifts from `tax_constants.json` would send an agent to the wrong
    threshold or the wrong penalty ceiling.
    """
    from fbar_schema import fbar_penalty_maximum  # noqa: E402

    fbar = TAX_2025["fbar"]
    rows = "\n".join(rows_between(read_ref("fbar_guide.md"),
                                  "<!-- FBAR_RULES -->", "<!-- /FBAR_RULES -->"))

    # Prose spells small counts out; accept either rendering so the test binds
    # to the constant rather than to a house style.
    words = {5: "five", 25: "twenty-five"}

    def stated(value, unit):
        return (f"{value} {unit}" in rows) or (f"{words.get(value, value)} {unit}" in rows)

    assert f"${fbar['threshold_usd']:,}" in rows
    assert stated(fbar["recordkeeping_years"], "years")
    assert stated(fbar["aggregate_max_account_count_detail_limit"], "or more accounts")

    penalty = fbar_penalty_maximum(fbar, __import__("datetime").date(2025, 6, 1))
    assert f"${penalty['non_willful_max']:,}" in rows
    assert f"${penalty['willful_max']:,}" in rows
    assert f"{int(penalty['willful_balance_percent'] * 100)}%" in rows

    deadline = fbar["deadline"]
    assert f"April {deadline['due_day']}" in rows
    assert f"October {deadline['extended_day']}" in rows


def test_fbar_guide_threshold_boundary_is_stated_as_exceeds():
    """`threshold_test` is 'exceeds'; the guide must not read as >=."""
    fbar = TAX_2025["fbar"]
    assert fbar["threshold_test"] == "exceeds"
    text = read_ref("fbar_guide.md")
    assert "exceeded $10,000 at any time" in text
    assert "Exactly $10,000 does not trip it" in text


def test_fbar_guide_conventions_match_the_code_registry():
    """The published convention table and CONVENTIONS cannot drift apart.

    These are unconfirmed, filer-adopted treatments. A convention applied by the
    engine but undocumented (or documented but unimplemented) is exactly the gap
    that makes them look like settled rules.
    """
    from fbar_schema import CONVENTIONS  # noqa: E402

    rows = rows_between(read_ref("fbar_guide.md"),
                        "<!-- FBAR_CONVENTIONS -->", "<!-- /FBAR_CONVENTIONS -->")
    published = {c.strip().strip("`")
                 for row in rows
                 for c in [row.strip().strip("|").split("|")[0]]
                 if c.strip().startswith("`")}
    assert published == set(CONVENTIONS), (
        f"fbar_guide.md documents {sorted(published)}, "
        f"fbar_schema.CONVENTIONS has {sorted(CONVENTIONS)}"
    )


def test_fbar_guide_validator_table_covers_every_implemented_validator():
    import fbar_reconciler as fr  # noqa: E402

    text = read_ref("fbar_guide.md")
    implemented = sorted(
        (name.split("_")[0].upper() for name in dir(fr)
         if re.fullmatch(r"v\d+_\w+", name)),
        key=lambda v: int(v[1:]),
    )
    missing = [v for v in implemented if f"| {v} |" not in text]
    assert not missing, f"fbar_guide.md validator table is missing {missing}"


def test_fbar_guide_dispositions_match_the_engine():
    import fbar_reconciler as fr  # noqa: E402

    text = read_ref("fbar_guide.md")
    missing = [d for d in fr.DISPOSITION_SEVERITY if f"`{d}`" not in text]
    assert not missing, f"fbar_guide.md is missing dispositions {missing}"


def test_fbar_guide_does_not_claim_to_file():
    """The plugin emits worksheets, never a transmittable form."""
    text = read_ref("fbar_guide.md")
    assert "worksheets and findings, not a filed form" in text
    assert "BSA E-Filing" in text


def test_limitations_no_longer_claims_an_fbar_tracker():
    """docs/limitations.md asserted 'we provide an FBAR tracker' when none existed."""
    with open(os.path.join(ROOT, "docs", "limitations.md")) as f:
        text = f.read()
    assert "we provide an FBAR tracker" not in text
    assert "fbar_reconciler.py" in text
    assert "Form 8938" in text


def test_requirements_lists_no_unimported_third_party_package():
    """`pdfplumber` and `pandas` sat in requirements.txt imported by nothing."""
    import glob

    with open(os.path.join(ROOT, "requirements.txt")) as f:
        declared = [line.split("==")[0].split(">=")[0].strip()
                    for line in f
                    if line.strip() and not line.strip().startswith("#")]

    sources = "\n".join(
        open(p).read()
        for folder in ("scripts", "tests", "evals")
        for p in glob.glob(os.path.join(ROOT, folder, "**", "*.py"), recursive=True)
    )

    unused = [pkg for pkg in declared
              if pkg != "pytest"
              and not re.search(rf"^\s*(?:import|from)\s+{re.escape(pkg)}\b", sources, re.M)]
    assert not unused, f"requirements.txt declares packages nothing imports: {unused}"


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


# ---------------------------------------------------------------------------
# Phase 0 — the "does not parse statements" claim must not go stale again
# ---------------------------------------------------------------------------

#: Distributed files that a reader takes as a statement of what the plugin can
#: do. `docs/*_plan.md` are excluded here for the same reason
#: `test_every_script_referenced_in_docs_exists` excludes them: a plan is a
#: roadmap, not a published capability claim.
def _distributed_docs():
    import glob

    return (glob.glob(os.path.join(REFS, "**", "*.md"), recursive=True)
            + [p for p in glob.glob(os.path.join(ROOT, "docs", "*.md"))
               if not p.endswith("_plan.md")]
            + [os.path.join(ROOT, "skills", "tax-preparation", "SKILL.md"),
               os.path.join(ROOT, "README.md")])


#: The general denial. Deliberately narrow: `fbar_guide.md` and
#: `limitations.md` both state the TRUE narrower limit — a PDF movement register
#: states no balance, so balance-bearing PDF statements stay with the `Read`
#: tool — and that must not trip this.
#:
#: Matched against text with markdown emphasis STRIPPED. The claim is written
#: as "does **not** parse bank statements", so a pattern anchored on plain
#: words never fires and the test silently passes on the very string it exists
#: to catch.
_NO_PARSING_CLAIMS = (
    r"not parse bank statements",
    r"does not parse statements",
    r"no bank statement parsing",
)


def _plain(text):
    """Strip markdown emphasis and collapse whitespace, for phrase matching."""
    return " ".join(re.sub(r"[*_`]", "", text).split())


def test_no_distributed_doc_claims_the_plugin_cannot_parse_statements():
    """F6: the claim appeared in THREE distributed files, and §12 of the plan
    listed one. Amending one and shipping the tool would leave two published
    false statements.

    The claim went stale silently once; this makes a second time a test
    failure rather than a discovery.
    """
    if not os.path.exists(os.path.join(ROOT, "scripts", "statement_parser.py")):
        pytest.skip("Phase 0 tool not present, so the claim is still true")

    stale = []
    for doc in _distributed_docs():
        with open(doc) as f:
            text = _plain(f.read())
        for pattern in _NO_PARSING_CLAIMS:
            if re.search(pattern, text, re.IGNORECASE):
                stale.append(f"{os.path.relpath(doc, ROOT)} matches {pattern!r}")

    assert not stale, (
        "scripts/statement_parser.py exists, so these published claims are "
        "false: " + "; ".join(sorted(stale))
    )


def test_the_staleness_check_actually_fires_on_the_claim_it_guards():
    """The patterns are matched against emphasis-stripped text, and this is why.

    Written as `does **not** parse bank statements` — the form the claim
    actually took in `docs/limitations.md` — a pattern anchored on plain words
    never fires, and the guard passes on the one string it exists to catch.
    """
    withdrawn = ("It does **not** generate BSA E-Filing XML or a transmittable "
                 "PDF, does **not** parse bank statements into maxima, and "
                 "covers Part II only.")
    assert any(re.search(pattern, _plain(withdrawn), re.IGNORECASE)
               for pattern in _NO_PARSING_CLAIMS)

    # The narrower limit that replaced it is still sayable.
    kept = ("**A PDF movement register states no balance**, so balance-bearing "
            "PDF statements stay with the `Read` tool.")
    assert not any(re.search(pattern, _plain(kept), re.IGNORECASE)
                   for pattern in _NO_PARSING_CLAIMS)


def test_the_reconciler_docstring_points_at_the_phase_0_tool():
    """The bullet is AMENDED, not deleted: the reconciler still takes only
    normalized JSON, and statement parsing is now a separate upstream tool."""
    with open(os.path.join(ROOT, "scripts", "fbar_reconciler.py")) as f:
        text = f.read()
    # Collapse the wrapping so the assertions bind to the words, not the
    # column the docstring happens to break at.
    docstring = " ".join(text.split('"""')[1].split())

    assert "No statement parsing" in docstring
    assert "statement_parser.py" in docstring
    assert "SEPARATE UPSTREAM TOOL" in docstring
    assert "still takes only normalized JSON" in docstring


def test_fbar_guide_documents_phase_0_in_the_workflow():
    text = read_ref("fbar_guide.md")
    workflow = text.split("## 9. Workflow", 1)[1].split("## 10.", 1)[0]

    assert "Phase 0" in workflow
    assert "statement_parser.py" in workflow
    assert "account_timelines_" in workflow
    # The boundary travels with the tool, or the tool reads as doing more.
    assert "never decides what the filing claims" in workflow
    # The boundary MOVED on 2026-08-18 rather than disappearing: PDF movement
    # registers are parsed, balance-bearing PDFs are not. Pinning the residual
    # half is what stops the doc drifting back to a flat "PDFs are unread" or
    # forward to an unqualified "PDFs are parsed" — both false.
    assert "Balance-bearing PDF statements are read by\nthe agent's `Read` tool" in workflow
    assert "no profile derives\na balance from transaction deltas" in workflow


def test_limitations_states_the_phase_0_boundary():
    with open(os.path.join(ROOT, "docs", "limitations.md")) as f:
        text = f.read()
    fbar_bullet = next(line for line in text.splitlines()
                       if "FBAR (FinCEN 114)" in line)

    assert "statement_parser.py" in fbar_bullet
    assert "Phase 0" in fbar_bullet
    # The narrower limits that replaced the withdrawn one. A PDF register IS
    # parsed as of 2026-08-18; what it cannot do is state a level, and that is
    # the claim this bullet has to keep making.
    assert "a PDF movement register states no balance" in fbar_bullet
    assert "balance-bearing PDF statements" in fbar_bullet
    assert "Item 15a" in fbar_bullet


def test_skill_registers_the_phase_0_tool_so_the_agent_can_reach_it():
    """A Phase 0 tool that is not in the script table never runs — no amount of
    correct Python fixes that."""
    with open(os.path.join(ROOT, "skills", "tax-preparation", "SKILL.md")) as f:
        text = f.read()

    assert "| `./scripts/statement_parser.py` |" in text
    assert "| `./scripts/statement_profiles.py` |" in text

    fbar_step = text.split("### Step 6: FBAR", 1)[1].split("## Scripts Reference", 1)[0]
    assert "Phase 0" in fbar_step
    assert "statement_parser.py parse" in fbar_step
    assert "sources.json" in fbar_step


def test_fbar_guide_requires_a_live_treasury_rate_check():
    """A rate labelled `treasury` can still be a wrong number typed by hand.

    V1 checks the LABEL. Only fetching the rate checks the rate, so the guide
    has to say to fetch it rather than leaving it as a remedy after a warning.
    """
    text = read_ref("fbar_guide.md")
    assert "Verify the rate, do not inherit it" in text
    # The endpoint must appear somewhere other than the V1 validator row.
    outside_table = [ln for ln in text.splitlines()
                     if "api.fiscaldata.treasury.gov" in ln and not ln.startswith("|")]
    assert outside_table, "the fiscaldata endpoint appears only inside the V1 table row"


def test_fbar_guide_names_the_unidentified_document_convention():
    """A document with no visible identifier gets a name, not a guess.

    The two failure modes are silent attribution (wrong number on a filing) and
    silent dropping (an account with no evidence that nobody notices). The
    convention exists to make the third option reachable.
    """
    text = read_ref("fbar_guide.md")
    assert "UNIDENTIFIED" in text
    assert "never attribute a document without a visible identifier" in text


def test_fbar_guide_gives_a_credit_account_decision_procedure():
    """The convention says WHAT to do; the filer needs to know HOW to decide.

    Every register-only statement lacks a balance column, so 'did it ever carry
    a positive balance' cannot be read off the document. Four checks answer it.
    """
    text = read_ref("fbar_guide.md")
    assert "Deciding which side of the line an account falls on" in text
    for check in ("Net direction over the year",
                  "Interest and fees charged TO the filer",
                  "The account's age",
                  "A parallel foreign declaration"):
        assert check in text, f"missing check: {check}"


def test_credit_convention_note_points_at_the_procedure():
    """The registry note must not still read as 'unconfirmed, good luck'."""
    from fbar_schema import CONVENTIONS  # noqa: E402
    note = CONVENTIONS["credit_card_positive_balance_only"]["note"]
    assert "four checks" in note
    assert "fbar_guide.md" in note


def test_fbar_guide_forbids_matching_columns_by_magnitude():
    """Two accounts' columns were transposed for a week; magnitude hid it.

    A transposition preserves both magnitudes and both column positions, so
    the only signal that survives it is WHEN each column starts.
    """
    text = read_ref("fbar_guide.md")
    assert "first non-blank date, never magnitude and never column order" in text
    assert "contract-open date" in text


def test_fbar_guide_documents_the_same_timestamp_dip():
    """The dip is a display artefact of an assumed order, not a bad anchor.

    Opening and maximum are conservation results: final = opening + net sum,
    whatever the internal order. Distrusting the anchor because an assumed
    intra-minute path dips negative inverts the actual logic.
    """
    text = read_ref("fbar_guide.md")
    assert "A replayed series that dips below zero" in text
    assert "conservation, not by any assumed intra-tie order" in text
    assert "do not invent a plausible sub-minute order" in text





