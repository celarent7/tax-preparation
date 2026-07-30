#!/usr/bin/env python3
"""Tests for scripts/tax_savings_finder.py.

Covers audit findings:
  D1 — `asdict` was never imported, so `--output-format json` raised NameError;
       and the `Priority` enum is not JSON-serializable even once imported.
       This code path had clearly never been executed.
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "tax_savings_finder.py")

sys.path.insert(0, os.path.join(ROOT, "scripts"))


@pytest.fixture
def synthetic_profile(tmp_path):
    """A synthetic taxpayer profile (never real data — ground rule 7).

    Chosen so at least one opportunity fires (HDHP with room left in the HSA),
    which is what forces a Priority enum through the JSON serializer.
    """
    profile = {
        "filing_status": "married_jointly",
        "age": 41,
        "agi": 150000,
        "has_hdhp": True,
        "hsa_coverage": "family",
        "hsa_contributions": 2000,
        "ira_contributions": 0,
        "itemized_total": 25000,
    }
    path = tmp_path / "synthetic_profile.json"
    path.write_text(json.dumps(profile))
    return str(path)


def run_finder(profile_path, *extra):
    return subprocess.run(
        [sys.executable, SCRIPT, "--data", profile_path, *extra],
        capture_output=True,
        text=True,
    )


def test_json_output_does_not_crash(synthetic_profile):
    """D1: --output-format json previously raised NameError: asdict."""
    result = run_finder(synthetic_profile, "--output-format", "json")
    assert result.returncode == 0, f"crashed: {result.stderr}"
    assert "NameError" not in result.stderr
    assert "TypeError" not in result.stderr


def test_json_output_is_valid_parseable_json(synthetic_profile):
    """D1: the Priority enum must serialize to its string value, not blow up."""
    result = run_finder(synthetic_profile, "--output-format", "json")
    assert result.returncode == 0, f"crashed: {result.stderr}"

    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert payload, "synthetic profile should yield at least one opportunity"

    for opportunity in payload:
        # priority must be a plain string like "HIGH", not a repr of the enum
        assert isinstance(opportunity["priority"], str)
        assert opportunity["priority"] in {"HIGH", "MEDIUM", "LOW"}
        assert "Priority." not in opportunity["priority"]
        assert isinstance(opportunity["documentation_needed"], list)


def test_hsa_opportunity_uses_family_limit(synthetic_profile):
    """The HSA headroom figure comes from constants, not a hard-coded number."""
    from utils import load_tax_constants

    family_limit = load_tax_constants("2025")["retirement_limits"]["hsa"]["family"]
    result = run_finder(synthetic_profile, "--output-format", "json", "--year", "2025")
    payload = json.loads(result.stdout)

    hsa = next(o for o in payload if "HSA" in o["name"])
    expected_headroom = family_limit - 2000
    assert f"{expected_headroom:,.0f}" in hsa["description"]


def test_text_output_still_works(synthetic_profile):
    """Regression: fixing JSON mode must not disturb the default text mode."""
    result = run_finder(synthetic_profile)
    assert result.returncode == 0, f"crashed: {result.stderr}"
    assert "Tax Savings Opportunities" in result.stdout
