#!/usr/bin/env python3
"""
Tax Savings Finder - Analyze a taxpayer profile to find savings opportunities.

Refactored (P6 / docs/savings_engine_plan.md) around a RULES registry in
`scripts/savings_rules/`. Each rule is a pure function taking the canonical
profile (loaded & validated by `savings_profile.load_profile`) and the year's
tax constants, and returning a list of `TaxOpportunity`. The finder only loads
data, runs the selected rules, and serializes — no rule logic lives here.

Process rules (docs/implementation_prompt.md) apply: every statutory figure
comes from `tax_constants.json` via `utils.get_constant` (no hard-coded
figures); unknown filing status / year / state / unverified constant raises a
clear error (no silent fallbacks); marginal-rate math reuses the existing
engines in `utils` + `tax_calculator` (no duplicated bracket walkers).
"""

import argparse
import json
import os
import sys
from typing import Dict, List

# Add the scripts directory to the path to import utils / the rules package
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import load_tax_constants, make_year_arg

import savings_rules
from savings_rules import RULES, opportunity_to_dict
from savings_profile import load_profile, ProfileError


def run_rules(profile: Dict, tax_data: Dict, only: List[str] = None) -> List:
    """Run the selected rules against the profile, in stable registry order."""
    selected = only or list(RULES.keys())
    unknown = [r for r in selected if r not in RULES]
    if unknown:
        raise ValueError(
            "Unknown --rules selection: " + ", ".join(unknown)
            + ". Available: " + ", ".join(sorted(RULES)) + "."
        )
    opportunities = []
    for rule_id in selected:
        opportunities.extend(RULES[rule_id](profile, tax_data))
    return opportunities


def main():
    parser = argparse.ArgumentParser(description="Tax Savings Finder")
    parser.add_argument("--data", required=True, help="Path to JSON file with user tax profile")
    make_year_arg(parser)
    parser.add_argument("--output-format", choices=["json", "text"], default="text")
    parser.add_argument("--state", type=str, default=None,
                        help="Override the profile's state of residence (e.g. CA, TX) "
                             "to enable/disable state-aware caveats")
    parser.add_argument("--rules", type=str, default=None,
                        help="Comma-separated rule_id list to run a subset, e.g. "
                             "backdoor_roth,tlh. Default: all rules.")

    args = parser.parse_args()

    # Load Tax Data for specified year
    try:
        tax_data = load_tax_constants(args.year)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Load & validate the user profile (canonical flat shape or legacy nested)
    try:
        profile = load_profile(args.data)
    except (ProfileError, ValueError) as e:
        print(f"Error: {e}")
        sys.exit(1)

    # CLI --state overrides the profile's state (used by tests & one-off runs).
    if args.state is not None:
        profile["state"] = args.state

    only = None
    if args.rules:
        only = [r.strip() for r in args.rules.split(",") if r.strip()]

    # Run the selected rules
    try:
        opportunities = run_rules(profile, tax_data, only=only)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    if args.output_format == "json":
        # Round-trip contract (G1/G2): json.loads of this output succeeds and
        # every opportunity carries all fields.
        print(json.dumps([opportunity_to_dict(o) for o in opportunities], indent=2))
    else:
        print(f"\n--- {args.year} Tax Savings Opportunities ---")
        if not opportunities:
            print("No immediate savings opportunities identified.")
        else:
            for i, opt in enumerate(opportunities, 1):
                print(f"{i}. [{opt.category}] {opt.name} ({opt.priority.value})")
                print(f"   Description: {opt.description}")
                print(f"   Potential:   {opt.potential_savings}")
                print(f"   Action:      {opt.action_required}")
                if opt.state_caveat:
                    print(f"   ⚠️  State:    {opt.state_caveat}")
                if opt.deadline:
                    print(f"   Deadline:   {opt.deadline}")
                print("-" * 45)


if __name__ == "__main__":
    main()