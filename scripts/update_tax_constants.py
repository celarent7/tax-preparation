#!/usr/bin/env python3
"""
Update Tax Constants - Automate the update of tax_constants.json and 
tax_brackets_deductions.md when new IRS figures are released.
"""

import json
import os
import re
import argparse
from typing import Dict, Any

def get_plugin_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def format_currency(val):
    if val is None or val == float('inf'):
        return "unlimited"
    return f"${val:,.0f}"

def generate_bracket_table(brackets):
    lines = ["| Taxable Income | Tax Rate |", "|----------------|----------|"]
    prev = 0
    for b in brackets:
        top = b["top"]
        rate = f"{int(b['rate'] * 100)}%"
        if top is None:
            lines.append(f"| Over {format_currency(prev)} | {rate} |")
        else:
            lines.append(f"| {format_currency(prev + 1)} - {format_currency(top)} | {rate} |")
            prev = top
    return "\n".join(lines)

def generate_ltcg_table(ltcg_data):
    lines = ["| Filing Status | 0% Rate | 15% Rate | 20% Rate |", "|---------------|---------|----------|----------|"]
    for status, name in [("single", "Single"), ("married_jointly", "Married Filing Jointly"), 
                         ("married_separately", "Married Filing Separately"), ("head_of_household", "Head of Household")]:
        b = ltcg_data[status]
        # b[0] is 0% top, b[1] is 15% top
        lines.append(f"| {name} | Up to {format_currency(b[0]['top'])} | {format_currency(b[0]['top']+1)} - {format_currency(b[1]['top'])} | Over {format_currency(b[1]['top'])} |")
    return "\n".join(lines)

def update_markdown(data: Dict[str, Any]):
    root = get_plugin_root()
    md_path = os.path.join(root, "references", "tax_brackets_deductions.md")
    
    with open(md_path, "r") as f:
        content = f.read()
    
    year = str(data["tax_year"])
    
    # Update YEAR tags
    content = re.sub(r"<!-- YEAR -->.*?<!-- /YEAR -->", f"<!-- YEAR -->{year}<!-- /YEAR -->", content)
    
    # Update Brackets
    content = re.sub(r"<!-- SINGLE_BRACKETS -->.*?<!-- /SINGLE_BRACKETS -->", 
                     f"<!-- SINGLE_BRACKETS -->\n{generate_bracket_table(data['tax_brackets']['single'])}\n<!-- /SINGLE_BRACKETS -->", content, flags=re.DOTALL)
    content = re.sub(r"<!-- MFJ_BRACKETS -->.*?<!-- /MFJ_BRACKETS -->", 
                     f"<!-- MFJ_BRACKETS -->\n{generate_bracket_table(data['tax_brackets']['married_jointly'])}\n<!-- /MFJ_BRACKETS -->", content, flags=re.DOTALL)
    content = re.sub(r"<!-- MFS_BRACKETS -->.*?<!-- /MFS_BRACKETS -->", 
                     f"<!-- MFS_BRACKETS -->\n{generate_bracket_table(data['tax_brackets']['married_separately'])}\n<!-- /MFS_BRACKETS -->", content, flags=re.DOTALL)
    content = re.sub(r"<!-- HOH_BRACKETS -->.*?<!-- /HOH_BRACKETS -->", 
                     f"<!-- HOH_BRACKETS -->\n{generate_bracket_table(data['tax_brackets']['head_of_household'])}\n<!-- /HOH_BRACKETS -->", content, flags=re.DOTALL)
    
    # Update Standard Deductions
    sd = data["standard_deductions"]
    sd_table = f"| Filing Status | Standard Deduction |\n|---------------|-------------------|\n" \
               f"| Single | {format_currency(sd['single'])} |\n" \
               f"| Married Filing Jointly | {format_currency(sd['married_jointly'])} |\n" \
               f"| Married Filing Separately | {format_currency(sd['married_separately'])} |\n" \
               f"| Head of Household | {format_currency(sd['head_of_household'])} |"
    content = re.sub(r"<!-- STANDARD_DEDUCTIONS -->.*?<!-- /STANDARD_DEDUCTIONS -->", 
                     f"<!-- STANDARD_DEDUCTIONS -->\n{sd_table}\n<!-- /STANDARD_DEDUCTIONS -->", content, flags=re.DOTALL)
    
    # Update LTCG
    content = re.sub(r"<!-- LTCG_TABLE -->.*?<!-- /LTCG_TABLE -->", 
                     f"<!-- LTCG_TABLE -->\n{generate_ltcg_table(data['ltcg_brackets'])}\n<!-- /LTCG_TABLE -->", content, flags=re.DOTALL)

    # Write back
    with open(md_path, "w") as f:
        f.write(content)
    print(f"Updated {md_path}")

def main():
    parser = argparse.ArgumentParser(description="Update tax constants from JSON file")
    parser.add_argument("--file", help="Path to new tax_constants.json", required=True)
    args = parser.parse_args()
    
    with open(args.file, "r") as f:
        new_data = json.load(f)
        
    root = get_plugin_root()
    target_path = os.path.join(root, "scripts", "tax_constants.json")
    
    with open(target_path, "w") as f:
        json.dump(new_data, f, indent=2)
    print(f"Updated {target_path}")
    
    update_markdown(new_data)

if __name__ == "__main__":
    main()
