#!/usr/bin/env python3
"""The wide daily portfolio-value reader (docs/fbar_enhancements_plan.md, F3/F4).

Two properties matter. The reader must emit ONE TIMELINE PER COLUMN rather than
one per file, because a single export covers every brokerage account the filer
holds. And a column must be matched to an account by its first non-blank date
against that account's contract-open date — never by magnitude and never by
position, both of which survive a transposition unchanged.

All fixtures synthetic.
"""

import os
import sys
from datetime import date
from decimal import Decimal

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from statement_parser import (  # noqa: E402
    EVIDENCE_PRICED_DAILY_SERIES,
    StatementParseError,
    match_columns_by_open_date,
)


def test_columns_match_accounts_by_first_date_against_contract_open_date():
    mapping = match_columns_by_open_date(
        first_dates={"Брокерский счёт": date(2025, 6, 2),
                     "Брокерский счет 1": date(2022, 3, 14)},
        contract_open_dates={"broker_a": date(2022, 3, 14),
                             "broker_b": date(2025, 6, 1)})
    assert mapping == {"Брокерский счет 1": "broker_a", "Брокерский счёт": "broker_b"}


def test_a_column_cannot_predate_the_account_it_is_matched_to():
    """An account cannot hold value before it existed. That is what forces the
    match rather than merely suggesting it."""
    with pytest.raises(StatementParseError) as exc:
        match_columns_by_open_date(
            first_dates={"col": date(2020, 1, 1)},
            contract_open_dates={"broker_a": date(2022, 3, 14)})
    assert "predates" in str(exc.value)


def test_two_accounts_opened_on_one_date_refuse_to_match():
    """The method is silent here and must say so rather than fall back to
    magnitude, which is the signal a transposition preserves."""
    with pytest.raises(StatementParseError) as exc:
        match_columns_by_open_date(
            first_dates={"a": date(2022, 3, 20), "b": date(2022, 3, 21)},
            contract_open_dates={"x": date(2022, 3, 14), "y": date(2022, 3, 14)})
    assert "same date" in str(exc.value)


def test_reader_emits_one_result_per_column(tmp_path):
    """One export covers every brokerage account; one ReadResult per file would
    merge them into a single fictional account."""
    from statement_parser import read_daily_value_series
    from statement_profiles import get_profile

    path = _write_series_csv(tmp_path)
    results = read_daily_value_series(
        str(path), get_profile("tbank_daily_values"),
        contract_open_dates={"broker_a": date(2025, 1, 1),
                             "broker_b": date(2025, 2, 1)})
    assert sorted(key for key, _ in results) == ["broker_a", "broker_b"]
    for _, result in results:
        assert result.evidence_kind == EVIDENCE_PRICED_DAILY_SERIES


def test_reader_treats_a_blank_cell_as_no_observation(tmp_path):
    """A blank is not a zero. An account that had not opened yet held nothing
    the document knows about, and a zero would be a determined balance."""
    from statement_parser import read_daily_value_series
    from statement_profiles import get_profile

    path = _write_series_csv(tmp_path)
    results = dict(read_daily_value_series(
        str(path), get_profile("tbank_daily_values"),
        contract_open_dates={"broker_a": date(2025, 1, 1),
                             "broker_b": date(2025, 2, 1)}))
    dates = [m.date for m in results["broker_b"].movements]
    assert date(2025, 1, 1) not in dates


def test_a_priced_daily_series_can_support_a_full_year():
    from statement_parser import EVIDENCE_PRICED_DAILY_SERIES, _EVIDENCE_CEILING
    assert _EVIDENCE_CEILING[EVIDENCE_PRICED_DAILY_SERIES] == "statement_full_year"


def test_fbar_guide_documents_requesting_daily_series_when_holdings_unpriced():
    guide_path = os.path.join(ROOT, "references", "fbar_guide.md")
    with open(guide_path, "r", encoding="utf-8") as f:
        text = f.read()
    assert "When a broker report states holdings but no prices" in text
    assert "Request a daily portfolio-value export" in text


def _write_series_csv(tmp_path):
    """Two account columns. The first is blank on 01.01, the second is not —
    which is what makes the two columns distinguishable by start date."""
    path = tmp_path / "values.csv"
    path.write_text(
        "Дата,Брокерский счёт,Брокерский счет 1\n"
        "01.01.2025,,1000.00\n"
        "01.02.2025,500.00,1100.00\n"
        "31.12.2025,700.00,1200.00\n",
        encoding="utf-8")
    return path


def _series_workspace(tmp_path, columns=("Брокерский счёт", "Брокерский счет 1")):
    """A workspace holding one daily-value export and the sources.json for it."""
    import json

    header = "Дата," + ",".join(columns) + "\n"
    body = ("01.01.2025," + ",".join("" for _ in columns) + "\n"
            "01.02.2025," + ",".join("500.00" for _ in columns) + "\n"
            "31.12.2025," + ",".join("700.00" for _ in columns) + "\n")
    (tmp_path / "values.csv").write_text(header + body, encoding="utf-8")

    (tmp_path / "sources.json").write_text(json.dumps({
        "tax_year": 2025,
        "fx_rates": [{"currency": "RUB", "units_per_usd": 81.996,
                      "as_of": "2025-12-31", "source": "treasury"}],
        "sources": [{
            "filer": "primary",
            "institution": "T-Bank",
            "glob": "values.csv",
            "reader": "daily_value_series",
            "profile": "tbank_daily_values",
            "account_key": "broker",
            "currency": "RUB",
            "contract_open_dates": {"broker_a": "2025-01-01",
                                    "broker_b": "2025-01-15"},
        }],
    }), encoding="utf-8")
    return tmp_path


def test_the_series_reader_reports_how_many_accounts_the_file_listed(tmp_path):
    """The count has to come from the DOCUMENT. A count typed by the same
    person who wrote the roster reproduces the roster's blind spot."""
    from statement_parser import load_sources, run_sources

    workspace = _series_workspace(tmp_path)
    config = load_sources(str(workspace / "sources.json"))
    output = run_sources(config, str(workspace), filer="primary")

    assert output["external_account_counts"] == [{
        "institution": "T-Bank",
        "account_type": "securities",
        "count": 2,
        "source_document": "values.csv",
        "note": "account columns found in the daily value series",
    }]


def test_the_reported_count_is_columns_found_not_columns_matched(tmp_path):
    """A column the matcher could not place still exists in the document, and
    it is exactly the account nobody rostered. Counting only matched columns
    would hide the finding V15 exists to surface.

    Three columns, two contract-open dates: the matcher raises, the run records
    the failure as a parser warning, and the COUNT is still 3.
    """
    from statement_parser import load_sources, run_sources

    workspace = _series_workspace(
        tmp_path, columns=("Брокерский счёт", "Брокерский счет 1", "ИИС"))
    config = load_sources(str(workspace / "sources.json"))
    output = run_sources(config, str(workspace), filer="primary")

    assert output["external_account_counts"][0]["count"] == 3
    # The unmatched column is reported as a PARSER warning, not as a key on the
    # count entry: that entry is pasted straight into a roster, where
    # `_reject_unknown` refuses any key outside EXTERNAL_COUNT_FIELDS.
    assert any("contract-open date" in w for w in output["parser_warnings"])
