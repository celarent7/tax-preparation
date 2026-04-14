#!/usr/bin/env python3
"""
Utility functions for the tax preparation plugin.
Handles centralized loading of tax constants and path resolution.
"""

import json
import os
from typing import Dict, Any, List, Tuple

def get_plugin_root() -> str:
    """Get the root directory of the plugin."""
    # Since this file is in scripts/, the root is the parent directory
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_tax_constants(year: str = "2025") -> Dict[str, Any]:
    """Load tax constants for a specific year from the centralized JSON file."""
    root = get_plugin_root()
    constants_path = os.path.join(root, "scripts", "tax_constants.json")
    
    with open(constants_path, "r") as f:
        full_data = json.load(f)
    
    if year not in full_data:
        raise ValueError(f"Tax data for year {year} not found in constants file.")
        
    data = full_data[year]
    
    # Process Federal Income Tax brackets
    processed_brackets = {}
    for status, bracket_list in data["tax_brackets"].items():
        processed_brackets[status] = [
            (b["top"] if b["top"] is not None else float('inf'), b["rate"])
            for b in bracket_list
        ]
    data["tax_brackets_processed"] = processed_brackets
    
    # Process LTCG brackets
    processed_ltcg = {}
    for status, bracket_list in data["ltcg_brackets"].items():
        processed_ltcg[status] = [
            (b["top"] if b["top"] is not None else float('inf'), b["rate"])
            for b in bracket_list
        ]
    data["ltcg_brackets_processed"] = processed_ltcg
    
    # Include shared constants (not specific to a year, or mapped from shared)
    data["state_tax_rates"] = full_data.get("state_tax_rates", {})
    data["filing_status_map"] = full_data.get("filing_status_map", {})
    
    return data
