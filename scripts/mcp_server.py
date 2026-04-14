#!/usr/bin/env python3
"""
MCP Server for Tax Preparation Plugin.
Exposes tax calculation and analysis tools via the Model Context Protocol.
"""

import os
import sys
import json
import traceback
from typing import Dict, Any, List, Optional, Union
from mcp.server.fastmcp import FastMCP

# Add scripts directory to path to import local modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils import load_tax_constants
import tax_calculator
import rsu_calculator
import espp_calculator
import deduction_analyzer
import estimated_tax_calculator
import draft_reviewer
import tax_savings_finder

# Initialize FastMCP server
mcp = FastMCP("Tax Preparation")

def format_error(message: str, category: str = "system", is_retryable: bool = False) -> Dict[str, Any]:
    """Helper to format structured MCP error responses."""
    return {
        "content": [{"type": "text", "text": f"Error [{category}]: {message}"}],
        "isError": True,
        "metadata": {
            "errorCategory": category,
            "isRetryable": is_retryable
        }
    }

@mcp.tool()
def calculate_federal_tax(
    gross_income: float,
    year: str = "2025",
    filing_status: str = "single",
    deductions: float = 0,
    adjustments: float = 0,
    age_65_plus: int = 0,
    blind: int = 0,
    credits: float = 0
) -> Union[str, Dict[str, Any]]:
    """
    Calculate federal income tax based on income, filing status, and year.
    Returns a detailed breakdown of taxable income, brackets, and effective rates.
    """
    try:
        try:
            tax_data = load_tax_constants(year)
        except ValueError as e:
            return format_error(str(e), "validation")

        tax_brackets = tax_data["tax_brackets_processed"]
        standard_deductions = tax_data["standard_deductions"]
        additional_deductions = tax_data["additional_deduction_65_blind"]

        aliases = {"mfj": "married_jointly", "mfs": "married_separately",
                   "hoh": "head_of_household", "qss": "qualifying_surviving_spouse"}
        filing_status = aliases.get(filing_status.lower(), filing_status)
        if filing_status not in tax_brackets:
            return format_error(f"Invalid filing status '{filing_status}'. Use one of: {', '.join(tax_brackets.keys())}", "validation")

        agi = gross_income - adjustments
        sd = tax_calculator.calculate_standard_deduction(
            filing_status, standard_deductions, additional_deductions, age_65_plus, blind
        )
        deduction_used = max(sd, deductions)
        taxable_income = max(0, agi - deduction_used)
        
        tax_before_credits, breakdown = tax_calculator.calculate_tax(taxable_income, filing_status, tax_brackets)
        tax_after_credits = max(0, tax_before_credits - credits)
        effective_rate = tax_calculator.calculate_effective_rate(tax_after_credits, gross_income)
        marginal_rate = tax_calculator.calculate_marginal_rate(taxable_income, filing_status, tax_brackets)

        result = {
            "year": year,
            "filing_status": filing_status,
            "agi": agi,
            "taxable_income": taxable_income,
            "tax_liability": tax_after_credits,
            "effective_rate": f"{effective_rate}%",
            "marginal_rate": f"{round(marginal_rate * 100, 2)}%",
            "breakdown": breakdown
        }
        return json.dumps(result, indent=2)
    except Exception as e:
        return format_error(f"Unexpected error calculating tax: {str(e)}", "execution")

@mcp.tool()
def calculate_rsu_basis(
    shares_vested: float,
    fmv_at_vesting: float,
    shares_withheld: float = 0
) -> Union[str, Dict[str, Any]]:
    """
    Calculate the correct cost basis for RSU shares.
    Useful for correcting 1099-B forms that report $0 or incorrect basis.
    """
    try:
        if shares_vested < 0 or fmv_at_vesting < 0:
            return format_error("Shares and FMV must be non-negative.", "validation")

        total_basis = shares_vested * fmv_at_vesting
        basis_per_share = fmv_at_vesting
        net_shares = shares_vested - shares_withheld
        
        result = {
            "total_cost_basis": round(total_basis, 2),
            "basis_per_share": round(basis_per_share, 4),
            "net_shares_received": net_shares,
            "taxable_vesting_income": round(total_basis, 2),
            "note": "Use this total cost basis on Form 8949 to adjust broker-reported basis."
        }
        return json.dumps(result, indent=2)
    except Exception as e:
        return format_error(str(e), "execution")

@mcp.tool()
def analyze_rsu_withholding(
    vesting_income: float,
    ytd_wages: float = 0,
    filing_status: str = "single",
    state_rate: float = 0.05,
    year: str = "2025"
) -> Union[str, Dict[str, Any]]:
    """
    Estimate tax withholding shortfall for RSU vesting events.
    Helps identify if you will owe additional tax at year-end due to the 22% flat withholding.
    """
    try:
        try:
            tax_data = load_tax_constants(year)
        except ValueError as e:
            return format_error(str(e), "validation")

        result = rsu_calculator.calculate_withholding_estimate(
            vesting_income, ytd_wages, filing_status, state_rate, tax_data
        )
        return json.dumps(result, indent=2)
    except Exception as e:
        return format_error(str(e), "execution")

@mcp.tool()
def analyze_espp_sale(
    offering_date: str,
    purchase_date: str,
    sale_date: str,
    purchase_price: float,
    fmv_at_offering: float,
    fmv_at_purchase: float,
    sale_price: float,
    shares: float,
    reported_basis: Optional[float] = None
) -> Union[str, Dict[str, Any]]:
    """
    Analyze an ESPP sale to determine qualifying/disqualifying status and correct tax treatment.
    Calculates ordinary income vs capital gains and provides Form 8949 adjustments.
    """
    try:
        try:
            p_date = espp_calculator.parse_date(purchase_date)
            o_date = espp_calculator.parse_date(offering_date)
            s_date = espp_calculator.parse_date(sale_date)
        except ValueError as e:
            return format_error(f"Invalid date format: {str(e)}", "validation")
        
        purchase = espp_calculator.ESPPPurchase(
            offering_date=o_date,
            purchase_date=p_date,
            shares=shares,
            purchase_price=purchase_price,
            fmv_at_offering=fmv_at_offering,
            fmv_at_purchase=fmv_at_purchase
        )
        
        sale = espp_calculator.ESPPSale(
            sale_date=s_date,
            shares_sold=shares,
            sale_price=sale_price,
            purchase=purchase,
            reported_basis=reported_basis
        )
        
        breakdown = espp_calculator.calculate_tax_breakdown(sale, reported_basis)
        
        result = {
            "disposition_type": breakdown.disposition_type,
            "ordinary_income": breakdown.ordinary_income,
            "capital_gain_loss": breakdown.capital_gain_loss,
            "capital_gain_type": breakdown.capital_gain_type,
            "adjusted_basis": breakdown.adjusted_basis,
            "form_8949_code": breakdown.form_8949_adjustment_code,
            "form_8949_adjustment": breakdown.form_8949_adjustment_amount
        }
        return json.dumps(result, indent=2)
    except Exception as e:
        return format_error(str(e), "execution")

@mcp.tool()
def compare_deductions(
    agi: float,
    filing_status: str,
    mortgage_interest: float = 0,
    property_taxes: float = 0,
    state_income_tax: float = 0,
    state_sales_tax: float = 0,
    charitable_cash: float = 0,
    charitable_property: float = 0,
    medical_expenses: float = 0,
    age_65_plus: int = 0,
    blind: int = 0,
    year: str = "2025"
) -> Union[str, Dict[str, Any]]:
    """
    Compare standard deduction vs itemized deductions.
    Provides detailed breakdown of SALT cap, medical thresholds, and optimization tips.
    """
    try:
        try:
            tax_data = load_tax_constants(year)
        except ValueError as e:
            return format_error(str(e), "validation")

        sd_table = tax_data["standard_deductions"]
        add_table = tax_data["additional_deduction_65_blind"]
        
        salt_res = deduction_analyzer.analyze_salt_deduction(
            property_taxes, state_income_tax, state_sales_tax, filing_status
        )
        
        medical_res = deduction_analyzer.analyze_medical_deduction(medical_expenses, agi)
        
        # Simplified charitable for tool output
        total_charitable = charitable_cash + min(charitable_property, agi * 0.3)
        
        total_itemized = salt_res["deductible_amount"] + medical_res["deductible_amount"] + mortgage_interest + total_charitable
        
        sd_base = sd_table.get(filing_status, 15000)
        additional = add_table.get(filing_status, 2000)
        standard_deduction = sd_base + (additional * age_65_plus) + (additional * blind)
        
        use_itemized = total_itemized > standard_deduction
        
        result = {
            "year": year,
            "standard_deduction": standard_deduction,
            "total_itemized": round(total_itemized, 2),
            "recommendation": "Itemize" if use_itemized else "Standard Deduction",
            "salt_limited_by_cap": salt_res["lost_to_cap"] > 0,
            "medical_deductible": medical_res["deductible_amount"],
            "potential_savings": round(abs(total_itemized - standard_deduction), 2)
        }
        return json.dumps(result, indent=2)
    except Exception as e:
        return format_error(str(e), "execution")

@mcp.tool()
def review_draft_return(
    draft_json: str,
    computed_json: str,
    year: str = "2025"
) -> Union[str, Dict[str, Any]]:
    """
    Review a draft tax return by cross-checking it against computed data.
    Input should be JSON strings representing the draft and expected data.
    Catches errors (red flags), warnings, and provides advisories.
    """
    try:
        try:
            draft = json.loads(draft_json)
            computed = json.loads(computed_json)
        except json.JSONDecodeError as e:
            return format_error(f"Invalid JSON input: {str(e)}", "validation")

        try:
            tax_data = load_tax_constants(year)
        except ValueError as e:
            return format_error(str(e), "validation")

        # Initialize Review Context
        ctx = draft_reviewer.ReviewContext(
            year=year,
            tax_brackets=tax_data["tax_brackets_processed"],
            standard_deductions=tax_data["standard_deductions"],
            add_deduction_married=tax_data["additional_deduction_65_blind"]["married_jointly"],
            add_deduction_single=tax_data["additional_deduction_65_blind"]["single"],
            salt_cap=tax_data["salt_cap"],
            salt_cap_mfs=tax_data["salt_cap_mfs"],
            capital_loss_limit=tax_data["capital_loss_limit"],
            capital_loss_limit_mfs=tax_data["capital_loss_limit_mfs"],
            ss_wage_base=tax_data["se_tax"]["ss_wage_base"],
            se_tax_factor=tax_data["se_tax"]["se_adjustment_factor"],
            se_ss_rate=tax_data["se_tax"]["ss_rate"],
            se_medicare_rate=tax_data["se_tax"]["medicare_rate"],
            add_medicare_threshold_single=tax_data["se_tax"]["additional_medicare_threshold_single"],
            add_medicare_threshold_mfj=tax_data["se_tax"]["additional_medicare_threshold_married"],
            hsa_limit_self=tax_data["retirement_limits"]["hsa"]["single"],
            hsa_limit_family=tax_data["retirement_limits"]["hsa"]["family"],
            charitable_cash_limit=tax_data["credits"]["charitable_cash_limit"],
            charitable_property_limit=tax_data["charitable_property_agi_limit"],
            educator_limit=tax_data["educator_expense_limit"],
            student_loan_limit=tax_data["student_loan_interest_limit"]
        )

        findings = draft_reviewer.run_review(draft, computed, ctx)
        
        # Format findings for MCP output
        from dataclasses import asdict
        return json.dumps([asdict(f) for f in findings], indent=2, default=str)
    except Exception as e:
        return format_error(str(e), "execution")

if __name__ == "__main__":
    mcp.run()
