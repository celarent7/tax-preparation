#!/usr/bin/env python3
"""The regression test: parse the committed corpus, check the hand-computed answers.

`examples/statements/README.md` computes every figure below by hand, in a table
a reader can check without running anything. This file asserts the parser
agrees. It is the regression target the plan calls for (§10.1) because the
alternative — diffing against the hand-made workpapers — cannot be one: those
live outside the repository, four of the five lack the structure to compare
against, and one institution is a known intended mismatch.

The corpus is committed rather than generated here, so a change that alters
what a reader extracts fails against fixed bytes instead of moving a fixture
along with the code.
"""

import os
import sys
from datetime import date
from decimal import Decimal

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from statement_parser import (  # noqa: E402
    load_sources,
    main,
    run_sources,
    timeline_to_json,
    to_usd,
)

GOLDEN = os.path.join(ROOT, "examples", "statements")
SOURCES = os.path.join(GOLDEN, "sources.json")


@pytest.fixture(scope="module")
def timelines():
    config = load_sources(SOURCES)
    by_filer = run_sources(config, GOLDEN)["timelines"]
    assert list(by_filer) == ["example"]
    return {t.account_key: t for t in by_filer["example"]}


def test_the_corpus_covers_all_four_shapes(timelines):
    assert sorted(timelines) == [
        "0001",                        # Shape D — two-sheet credit card
        "example-multicurrency-0002",  # Shape A — multi-currency
        "example-savings-0003",        # Shape A — single currency, quarterly
        "example-summary-0004",        # Shape B — period-end summaries
    ]


# ---------------------------------------------------------------------------
# The fixture that demonstrates why the sum-of-maxima answer is wrong
# ---------------------------------------------------------------------------

def test_multicurrency_peak_is_the_combined_series_not_the_sum_of_maxima(timelines):
    timeline = timelines["example-multicurrency-0002"]
    assert timeline.max_usd == Decimal("3200")
    assert timeline.date_of_maximum == date(2025, 3, 15)
    assert timeline.max_components == {"USD": Decimal("3000.00"),
                                       "EUR": Decimal("400.00")}

    # Per-currency peaks fall on different dates: USD 3,000 in March, EUR 4,000
    # (= $2,000) in September. Summing them invents an instant that never was.
    rates = load_sources(SOURCES)["fx_rates"]
    sum_of_maxima = Decimal("3000") + to_usd(Decimal("4000"), "EUR", rates)
    assert sum_of_maxima == Decimal("5000")
    assert timeline.max_usd < sum_of_maxima


def test_multicurrency_reaches_the_full_year_safe_harbour(timelines):
    """Reachable only because both currencies carry an observation on the same
    opening date — the earliest event otherwise belongs to exactly one
    currency, leaving the other with an unknown prefix."""
    timeline = timelines["example-multicurrency-0002"]
    assert timeline.max_value_basis == "statement_full_year"
    assert timeline.max_usd_bound is None
    assert timeline.gaps == []
    assert timeline.interval_uncertainty == []
    assert timeline.balance_dec_31 == Decimal("2500")


# ---------------------------------------------------------------------------
# Quarterly, sparse columns, D/MM dates, a same-date pair
# ---------------------------------------------------------------------------

def test_savings_maximum_and_full_year_coverage(timelines):
    timeline = timelines["example-savings-0003"]
    assert timeline.max_usd == Decimal("3050")
    assert timeline.date_of_maximum == date(2025, 2, 15)
    assert timeline.max_components == {"COP": Decimal("12200000.00")}
    assert timeline.balance_dec_31 == Decimal("3000")


def test_savings_quarters_are_day_adjacent_so_coverage_is_complete(timelines):
    """Q1 clipped to the year is 01-01..03-31 and the second file is
    04-01..12-31, so the two are contiguous and the year has no gap."""
    timeline = timelines["example-savings-0003"]
    assert timeline.gaps == []
    assert timeline.max_value_basis == "statement_full_year"
    assert [f["kind"] for f in timeline.continuity_breaks] == ["reconciles"]


def test_the_january_intraday_peak_is_kept_not_collapsed(timelines):
    """Two rows share 9 January. Taking the day's last would silently discard
    the $1,100 instant — it is not the year's maximum, but a timeline that
    dropped it would be wrong about January."""
    timeline = timelines["example-savings-0003"]
    ninth = [p for p in timeline.combined_usd if p.date == date(2025, 1, 9)]
    assert [p.usd for p in ninth] == [Decimal("1100"), Decimal("1050")]


def test_the_prior_year_row_is_dated_in_the_prior_year(timelines):
    """`31/12` in a period opening 2024/12/31 is December 2024, not 2025."""
    timeline = timelines["example-savings-0003"]
    assert timeline.combined_usd[0].date == date(2024, 12, 31)


# ---------------------------------------------------------------------------
# Period-end summaries
# ---------------------------------------------------------------------------

def test_summaries_can_never_reach_a_full_year(timelines):
    timeline = timelines["example-summary-0004"]
    assert timeline.max_value_basis == "period_end_summaries_only"
    assert timeline.max_usd == Decimal("1500")
    assert timeline.date_of_maximum == date(2025, 12, 21)


def test_summaries_leave_the_year_uncovered_at_both_ends(timelines):
    timeline = timelines["example-summary-0004"]
    assert [(g["start"], g["end"], g["position"]) for g in timeline.gaps] == [
        ("2025-01-01", "2025-10-21", "year_start"),
        ("2025-12-22", "2025-12-31", "year_end"),
    ]


def test_the_filenames_do_not_sort_into_period_order(timelines):
    """`text.txt` is the December period and `text 2.txt` is November, so the
    period has to come from the balance lines."""
    timeline = timelines["example-summary-0004"]
    by_file = {p.source_file: p for p in timeline.periods}
    assert by_file["text.txt"].start == date(2025, 11, 22)
    assert by_file["text 2.txt"].start == date(2025, 10, 22)
    assert sorted(by_file) == ["text 2.txt", "text.txt"]
    assert by_file["text 2.txt"].start < by_file["text.txt"].start


def test_the_summary_totals_reconcile_so_no_sign_was_lost(timelines):
    timeline = timelines["example-summary-0004"]
    assert not any("a sign was lost" in w for w in timeline.parser_warnings)


# ---------------------------------------------------------------------------
# The credit card
# ---------------------------------------------------------------------------

def test_the_card_gets_no_bare_maximum(timelines):
    timeline = timelines["0001"]
    assert timeline.is_liability is True
    assert timeline.max_usd is None
    assert timeline.max_signed_usd == Decimal("-87.50")
    assert timeline.date_of_maximum == date(2025, 1, 15)


def test_the_card_never_carried_a_positive_combined_balance(timelines):
    """The PESOS sheet shows a 50,000 overpayment, but the card as a whole was
    always in debit — which is the figure the reportability question needs."""
    timeline = timelines["0001"]
    assert timeline.max_positive_usd == Decimal("0")
    assert timeline.max_components == {"COP": Decimal("50000.00"),
                                       "USD": Decimal("-100.00")}


def test_the_card_components_are_signed(timelines):
    """Flooring the negative one while leaving the stated maximum as the signed
    sum is what `v3_recompute_usd` hard-errors on."""
    timeline = timelines["0001"]
    assert timeline.max_components["USD"] < 0


def test_the_missing_december_caps_the_card_basis(timelines):
    timeline = timelines["0001"]
    assert timeline.max_value_basis == "statement_partial"
    assert [(g["start"], g["end"]) for g in timeline.gaps] == [
        ("2025-03-01", "2025-12-31")]


def test_the_card_key_comes_from_the_filename_suffix(timelines):
    """One directory holds several cards and nothing else distinguishes them."""
    timeline = timelines["0001"]
    assert timeline.account_key == "0001"
    assert len(timeline.source_files) == 2


# ---------------------------------------------------------------------------
# The CLI, end to end
# ---------------------------------------------------------------------------

def test_the_cli_writes_both_artifacts_and_exits_zero(tmp_path, capsys):
    """Exit code is non-zero only on unreadable input. This corpus has a
    coverage gap, a bounded card and two blind periods — every one of them a
    warning, none of them a failure."""
    import json

    code = main(["parse", "--sources", SOURCES, "--output-dir", str(tmp_path)])
    capsys.readouterr()
    assert code == 0

    timelines_json = tmp_path / "account_timelines_example_2025.json"
    assert timelines_json.exists()
    payload = json.loads(timelines_json.read_text(encoding="utf-8"))
    assert payload["tax_year"] == 2025
    assert len(payload["accounts"]) == 4

    reports = sorted(p.name for p in tmp_path.glob("statement_parse_*_2025.md"))
    assert reports == [
        "statement_parse_card-bank_2025.md",
        "statement_parse_multicurrency-bank_2025.md",
        "statement_parse_savings-bank_2025.md",
        "statement_parse_summary-bank_2025.md",
    ]


def test_the_report_carries_the_specified_frontmatter(tmp_path, capsys):
    main(["parse", "--sources", SOURCES, "--output-dir", str(tmp_path)])
    capsys.readouterr()
    report = (tmp_path / "statement_parse_multicurrency-bank_2025.md").read_text(
        encoding="utf-8")
    for field in ("institution:", "taxpayer:", "tax_year:", "accounts_analyzed:",
                  "parser_warnings:"):
        assert field in report.split("---")[1]
    assert "3,200.00" in report
    assert "`statement_full_year`" in report


def test_the_card_report_says_the_convention_is_not_applied(tmp_path, capsys):
    main(["parse", "--sources", SOURCES, "--output-dir", str(tmp_path)])
    capsys.readouterr()
    report = (tmp_path / "statement_parse_card-bank_2025.md").read_text(encoding="utf-8")
    assert "credit_card_positive_balance_only" in report
    assert "signed, no convention applied" in report


def test_an_unknown_key_in_sources_json_is_refused(tmp_path):
    import json

    config = json.loads(open(SOURCES, encoding="utf-8").read())
    config["sources"][0]["acount_key"] = "typo"     # one letter short
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    code = main(["parse", "--sources", str(path)])
    assert code == 1


def test_the_emitted_payload_is_stable_across_runs(timelines):
    """Determinism is what makes this a regression test at all. An unspecified
    tie-break on `date_of_maximum` would silently defeat it."""
    config = load_sources(SOURCES)
    again = run_sources(config, GOLDEN)["timelines"]["example"]
    assert [timeline_to_json(t) for t in again] == \
        [timeline_to_json(timelines[t.account_key]) for t in again]


# ---------------------------------------------------------------------------
# §10.2 — the metadata block is not a movement, checked on the ARTIFACT
# ---------------------------------------------------------------------------

def test_no_report_leaks_the_metadata_block(tmp_path, capsys):
    """The rendered markdown is what actually leaves the machine.

    `test_client_name_and_address_never_reach_a_movement_record` checks a
    dataclass; this checks the file. `render_parse_report` interpolates
    `parser_warnings` wholesale, and those now come from three places — the
    readers, the year-end caveat and the account-group note — so a warning that
    quoted a cell would put the client's name into a published artifact without
    any MovementRecord being involved.

    The savings fixture puts an invented client name and street address in rows
    2-3 of every file, so this is a check against real bytes.
    """
    from statement_fixtures import FAKE_ADDRESS, FAKE_CARD, FAKE_CLIENT

    main(["parse", "--sources", SOURCES, "--output-dir", str(tmp_path)])
    capsys.readouterr()

    reports = list(tmp_path.glob("*.md")) + list(tmp_path.glob("*.json"))
    assert reports
    for artifact in reports:
        text = artifact.read_text(encoding="utf-8")
        assert FAKE_CLIENT not in text, artifact.name
        assert FAKE_ADDRESS not in text, artifact.name
        assert FAKE_CARD not in text, artifact.name
