#!/usr/bin/env python3
"""
RSU Calculator - Comprehensive tool for RSU cost basis, withholding, lot tracking, and tax reporting.

Features:
- Cost basis calculation from vesting records
- Withholding shortfall estimation
- Multi-lot tracking with FIFO/specific ID
- Capital gains calculation for sales
- Form 8949 adjustment generation
- CSV/JSON input support for statement imports
"""

import argparse
import json
import csv
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict
from decimal import Decimal, ROUND_HALF_UP

# Add the scripts directory to the path to import utils
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import load_tax_constants


@dataclass
class VestingLot:
    """Represents a single RSU vesting lot."""
    vesting_date: str
    shares_vested: float
    fmv_at_vesting: float
    shares_withheld: float = 0
    net_shares: float = 0
    grant_date: Optional[str] = None
    grant_id: Optional[str] = None

    def __post_init__(self):
        if self.net_shares == 0:
            self.net_shares = self.shares_vested - self.shares_withheld
        self.cost_basis_per_share = self.fmv_at_vesting
        self.total_cost_basis = self.fmv_at_vesting * self.shares_vested
        self.vesting_income = self.total_cost_basis
        self.shares_remaining = self.net_shares


@dataclass
class SaleLot:
    """Represents a sale transaction."""
    sale_date: str
    shares_sold: float
    sale_price: float
    from_vesting_date: Optional[str] = None
    reported_basis_1099b: float = 0

    def __post_init__(self):
        self.proceeds = self.shares_sold * self.sale_price


@dataclass
class TaxLotResult:
    """Result of selling from a specific tax lot."""
    vesting_date: str
    shares_sold: float
    cost_basis_per_share: float
    sale_price: float
    proceeds: float
    cost_basis: float
    gain_loss: float
    holding_period: str  # "short_term" or "long_term"
    holding_days: int
    basis_adjustment_needed: bool
    reported_basis: float
    correct_basis: float
    adjustment_amount: float
    form_8949_code: str


def parse_date(date_str: str) -> datetime:
    """Parse date string in various formats."""
    formats = ["%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {date_str}")


def calculate_holding_period(vesting_date: str, sale_date: str) -> Tuple[str, int]:
    """Calculate holding period and classification."""
    vesting = parse_date(vesting_date)
    sale = parse_date(sale_date)
    days = (sale - vesting).days

    # Long-term if held more than 1 year (365 days)
    if days > 365:
        return "long_term", days
    return "short_term", days


def calculate_marginal_rate(taxable_income: float, filing_status: str, tax_brackets: Dict) -> float:
    """Get marginal tax rate for given income and filing status."""
    brackets = tax_brackets.get(filing_status, tax_brackets["single"])
    for bracket_top, rate in brackets:
        if taxable_income <= bracket_top:
            return rate
    return brackets[-1][1]


def calculate_withholding_estimate(
    vesting_income: float,
    ytd_wages: float = 0,
    filing_status: str = "single",
    state_rate: float = 0.05,
    tax_data: Dict = None
) -> Dict:
    """
    Estimate withholding on RSU vesting and compare to actual tax owed.

    Returns dict with typical withholding vs estimated actual tax.
    """
    if tax_data is None:
        tax_data = load_tax_constants("2025")
        
    tax_brackets = tax_data["tax_brackets_processed"]
    standard_deductions = tax_data["standard_deductions"]
    
    withholding_rates = tax_data["withholding"]
    fica_rates = tax_data["fica_employee"]
    se_tax_data = tax_data["se_tax"]

    # Typical employer withholding
    if vesting_income > 1000000:
        federal_withholding_rate = withholding_rates["supplemental_rate_over_1m"]
    else:
        federal_withholding_rate = withholding_rates["supplemental_rate"]

    federal_withheld = vesting_income * federal_withholding_rate

    # Social Security (check if over wage base)
    ss_wage_base = se_tax_data["ss_wage_base"]
    ss_rate = fica_rates["ss_rate"]
    
    remaining_ss_wages = max(0, ss_wage_base - ytd_wages)
    ss_wages = min(vesting_income, remaining_ss_wages)
    ss_withheld = ss_wages * ss_rate

    # Medicare
    medicare_rate = fica_rates["medicare_rate"]
    medicare_withheld = vesting_income * medicare_rate

    # Additional Medicare (typically not withheld correctly)
    threshold_key = f"additional_medicare_threshold_{filing_status}"
    if filing_status == "married_jointly":
        threshold_key = "additional_medicare_threshold_married"
    elif filing_status == "married_separately":
        threshold_key = "additional_medicare_threshold_married_separately"
    
    medicare_threshold = se_tax_data.get(threshold_key, 200000)
    
    if ytd_wages + vesting_income > medicare_threshold:
        additional_medicare_income = min(vesting_income, ytd_wages + vesting_income - medicare_threshold)
        additional_medicare = additional_medicare_income * se_tax_data["additional_medicare_rate"]
    else:
        additional_medicare = 0

    # State withholding (simplified)
    state_withheld = vesting_income * state_rate

    total_withheld = federal_withheld + ss_withheld + medicare_withheld + state_withheld

    # Estimate actual tax (simplified - assumes this income on top of other income)
    total_income = ytd_wages + vesting_income
    standard_deduction = standard_deductions.get(filing_status, 15000)
    taxable_income = max(0, total_income - standard_deduction)

    marginal_rate = calculate_marginal_rate(taxable_income, filing_status, tax_brackets)
    estimated_federal_tax = vesting_income * marginal_rate
    estimated_state_tax = vesting_income * state_rate
    estimated_total = estimated_federal_tax + ss_withheld + medicare_withheld + additional_medicare + estimated_state_tax

    shortfall = estimated_total - total_withheld

    return {
        "vesting_income": round(vesting_income, 2),
        "withholding": {
            "federal": round(federal_withheld, 2),
            "federal_rate": federal_withholding_rate,
            "social_security": round(ss_withheld, 2),
            "medicare": round(medicare_withheld, 2),
            "state": round(state_withheld, 2),
            "total_withheld": round(total_withheld, 2)
        },
        "estimated_actual_tax": {
            "marginal_bracket": marginal_rate,
            "federal": round(estimated_federal_tax, 2),
            "social_security": round(ss_withheld, 2),
            "medicare": round(medicare_withheld + additional_medicare, 2),
            "additional_medicare": round(additional_medicare, 2),
            "state": round(estimated_state_tax, 2),
            "total": round(estimated_total, 2)
        },
        "shortfall": round(shortfall, 2),
        "shortfall_percent": round((shortfall / vesting_income) * 100, 1) if vesting_income > 0 else 0
    }


def calculate_lot_sale(
    lot: VestingLot,
    shares_to_sell: float,
    sale_price: float,
    sale_date: str,
    reported_basis_1099b: float = 0
) -> TaxLotResult:
    """Calculate tax implications of selling shares from a specific lot."""

    holding_period, holding_days = calculate_holding_period(lot.vesting_date, sale_date)

    proceeds = shares_to_sell * sale_price
    cost_basis = shares_to_sell * lot.cost_basis_per_share
    gain_loss = proceeds - cost_basis

    # Check if basis adjustment needed
    basis_adjustment_needed = abs(reported_basis_1099b - cost_basis) > 0.01 if reported_basis_1099b else True
    adjustment_amount = cost_basis - reported_basis_1099b if basis_adjustment_needed else 0
    form_8949_code = "B" if basis_adjustment_needed else ""

    return TaxLotResult(
        vesting_date=lot.vesting_date,
        shares_sold=shares_to_sell,
        cost_basis_per_share=lot.cost_basis_per_share,
        sale_price=sale_price,
        proceeds=round(proceeds, 2),
        cost_basis=round(cost_basis, 2),
        gain_loss=round(gain_loss, 2),
        holding_period=holding_period,
        holding_days=holding_days,
        basis_adjustment_needed=basis_adjustment_needed,
        reported_basis=reported_basis_1099b,
        correct_basis=round(cost_basis, 2),
        adjustment_amount=round(adjustment_amount, 2),
        form_8949_code=form_8949_code
    )


def process_sale_fifo(lots: List[VestingLot], sale: SaleLot) -> List[TaxLotResult]:
    """Process a sale using FIFO (First In, First Out) method."""
    results = []
    shares_remaining = sale.shares_sold

    # Sort lots by vesting date (oldest first)
    sorted_lots = sorted(lots, key=lambda x: parse_date(x.vesting_date))

    for lot in sorted_lots:
        if shares_remaining <= 0:
            break

        if lot.shares_remaining <= 0:
            continue

        shares_from_lot = min(shares_remaining, lot.shares_remaining)

        # Allocate reported basis proportionally if provided
        reported_basis = 0
        if sale.reported_basis_1099b and sale.shares_sold > 0:
            reported_basis = (shares_from_lot / sale.shares_sold) * sale.reported_basis_1099b

        result = calculate_lot_sale(
            lot=lot,
            shares_to_sell=shares_from_lot,
            sale_price=sale.sale_price,
            sale_date=sale.sale_date,
            reported_basis_1099b=reported_basis
        )
        results.append(result)

        lot.shares_remaining -= shares_from_lot
        shares_remaining -= shares_from_lot

    if shares_remaining > 0.001:
        print(f"Warning: Not enough shares in lots to cover sale. {shares_remaining:.4f} shares unaccounted for.", file=sys.stderr)

    return results


def process_sale_specific_id(lots: List[VestingLot], sale: SaleLot) -> List[TaxLotResult]:
    """Process a sale from a specific vesting lot."""
    results = []

    # Find the matching lot
    matching_lot = None
    for lot in lots:
        if lot.vesting_date == sale.from_vesting_date and lot.shares_remaining >= sale.shares_sold:
            matching_lot = lot
            break

    if not matching_lot:
        # Fall back to FIFO if specific lot not found or insufficient shares
        print(f"Warning: Specific lot {sale.from_vesting_date} not found or insufficient shares. Using FIFO.", file=sys.stderr)
        return process_sale_fifo(lots, sale)

    result = calculate_lot_sale(
        lot=matching_lot,
        shares_to_sell=sale.shares_sold,
        sale_price=sale.sale_price,
        sale_date=sale.sale_date,
        reported_basis_1099b=sale.reported_basis_1099b
    )
    results.append(result)
    matching_lot.shares_remaining -= sale.shares_sold

    return results


def load_vesting_data(file_path: str) -> List[VestingLot]:
    """Load vesting records from CSV or JSON file."""
    lots = []
    if file_path.endswith('.csv'):
        with open(file_path, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                lots.append(VestingLot(
                    vesting_date=row['vesting_date'],
                    shares_vested=float(row['shares_vested']),
                    fmv_at_vesting=float(row['fmv_at_vesting']),
                    shares_withheld=float(row.get('shares_withheld', 0)),
                    grant_date=row.get('grant_date'),
                    grant_id=row.get('grant_id')
                ))
    elif file_path.endswith('.json'):
        with open(file_path, mode='r', encoding='utf-8') as f:
            data = json.load(f)
            # Handle list or object with 'vesting_lots' key
            vesting_list = data.get('vesting_lots', data) if isinstance(data, dict) else data
            for item in vesting_list:
                lots.append(VestingLot(
                    vesting_date=item['vesting_date'],
                    shares_vested=item['shares_vested'],
                    fmv_at_vesting=item['fmv_at_vesting'],
                    shares_withheld=item.get('shares_withheld', 0),
                    grant_date=item.get('grant_date'),
                    grant_id=item.get('grant_id')
                ))
    return lots


def main():
    parser = argparse.ArgumentParser(description="RSU Tax and Cost Basis Calculator")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Withholding shortfall command
    withhold_parser = subparsers.add_parser("withholding", help="Calculate withholding shortfall")
    withhold_parser.add_argument("--vesting-income", type=float, required=True, help="Total income from vesting")
    withhold_parser.add_argument("--ytd-wages", type=float, default=0, help="Year-to-date wages before this vesting")
    withhold_parser.add_argument("--year", type=str, default="2025", choices=["2024", "2025"], help="Tax year")
    withhold_parser.add_argument("--filing-status", default="single", help="Filing status")
    withhold_parser.add_argument("--state-rate", type=float, default=0.05, help="Flat state tax rate (e.g., 0.05 for 5%%)")

    # Lot tracking command
    lots_parser = subparsers.add_parser("lots", help="Load and display vesting lot summary")
    lots_parser.add_argument("--vesting-file", required=True, help="Path to CSV or JSON with vesting history")
    lots_parser.add_argument("--summary-threshold", type=int, default=15, help="Collapse output if more than N lots")

    # Sale analysis command
    sale_parser = subparsers.add_parser("sale", help="Calculate tax implications of a sale")
    sale_parser.add_argument("--vesting-file", required=True, help="Path to vesting history")
    sale_parser.add_argument("--sale-date", required=True, help="Date of sale")
    sale_parser.add_argument("--shares", type=float, required=True, help="Number of shares sold")
    sale_parser.add_argument("--sale-price", type=float, required=True, help="Price per share")
    sale_parser.add_argument("--reported-basis", type=float, default=0, help="Cost basis reported on 1099-B")
    sale_parser.add_argument("--method", choices=["fifo", "specific"], default="fifo", help="Cost basis method")
    sale_parser.add_argument("--from-date", help="Vesting date for specific ID method")
    sale_parser.add_argument("--year", type=str, default="2025", choices=["2024", "2025"], help="Tax year")
    sale_parser.add_argument("--summary-threshold", type=int, default=10, help="Collapse output if more than N lots")

    # Simple basis calculation
    basis_parser = subparsers.add_parser("basis", help="Quick cost basis for a single vesting")
    basis_parser.add_argument("--shares-vested", type=float, required=True)
    basis_parser.add_argument("--fmv", type=float, required=True)
    basis_parser.add_argument("--shares-withheld", type=float, default=0)
    basis_parser.add_argument("--year", type=str, default="2025", choices=["2024", "2025"], help="Tax year")

    args = parser.parse_args()

    if args.command == "withholding":
        if args.vesting_income < 0 or args.ytd_wages < 0:
            print("Error: Income and wages must be non-negative.")
            sys.exit(1)
            
        try:
            tax_data = load_tax_constants(args.year)
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)
            
        result = calculate_withholding_estimate(
            args.vesting_income, args.ytd_wages, args.filing_status, args.state_rate, tax_data
        )
        print(f"\n--- RSU Withholding Analysis ({args.year}) ---")
        print(f"Vesting Income:       ${result['vesting_income']:,.2f}")
        print("-" * 35)
        print("Typical Withholding:")
        print(f"  Federal (22%):      ${result['withholding']['federal']:,.2f}")
        print(f"  Social Security:    ${result['withholding']['social_security']:,.2f}")
        print(f"  Medicare:           ${result['withholding']['medicare']:,.2f}")
        print(f"  State ({args.state_rate*100}%):      ${result['withholding']['state']:,.2f}")
        print(f"  Total Withheld:     ${result['withholding']['total_withheld']:,.2f}")
        print("-" * 35)
        print("Estimated Actual Tax Liability:")
        print(f"  Federal ({result['estimated_actual_tax']['marginal_bracket']*100}%): ${result['estimated_actual_tax']['federal']:,.2f}")
        print(f"  Total FICA:         ${result['estimated_actual_tax']['medicare'] + result['estimated_actual_tax']['social_security']:,.2f}")
        print(f"  State:              ${result['estimated_actual_tax']['state']:,.2f}")
        print(f"  Total Owed:         ${result['estimated_actual_tax']['total']:,.2f}")
        print("-" * 35)
        
        shortfall = result['shortfall']
        if shortfall > 0:
            print(f"⚠️  ESTIMATED SHORTFALL: ${shortfall:,.2f} ({result['shortfall_percent']}%)")
            print("Consider making an estimated payment or increasing W-4 withholding.")
        else:
            print(f"✅ NO SHORTFALL: Likely overwithheld by ${abs(shortfall):,.2f}")

    elif args.command == "lots":
        lots = load_vesting_data(args.vesting_file)
        num_lots = len(lots)
        print(f"\n--- RSU Vesting Lot Summary ({num_lots} lots) ---")
        
        if num_lots > args.summary_threshold:
            total_vested = sum(l.shares_vested for l in lots)
            total_remaining = sum(l.shares_remaining for l in lots)
            avg_basis = sum(l.cost_basis_per_share * l.shares_vested for l in lots) / total_vested if total_vested > 0 else 0
            print(f"AGGREGATE: Vested: {total_vested:,.2f} | Remaining: {total_remaining:,.2f} | Avg Basis: ${avg_basis:,.2f}")
            print(f"Showing first 3 and last 3 lots due to summary threshold ({args.summary_threshold}).\n")

        print(f"{'Vesting Date':<12} | {'Grant ID':<12} | {'Vested':>8} | {'Remaining':>8} | {'Basis/Sh':>10}")
        print("-" * 65)
        for i, lot in enumerate(lots):
            if num_lots > args.summary_threshold and 3 <= i < num_lots - 3:
                if i == 3:
                    print(f"... [{num_lots - 6} lots hidden for context efficiency] ...")
                continue
            print(f"{lot.vesting_date:<12} | {lot.grant_id or 'N/A':<12} | {lot.shares_vested:>8.2f} | {lot.shares_remaining:>8.2f} | ${lot.cost_basis_per_share:>9.2f}")

    elif args.command == "sale":
        if args.shares < 0 or args.sale_price < 0 or args.reported_basis < 0:
            print("Error: Shares, price, and basis must be non-negative.")
            sys.exit(1)
            
        lots = load_vesting_data(args.vesting_file)
        sale = SaleLot(
            sale_date=args.sale_date,
            shares_sold=args.shares,
            sale_price=args.sale_price,
            reported_basis_1099b=args.reported_basis,
            from_vesting_date=args.from_date
        )
        
        if args.method == "specific":
            results = process_sale_specific_id(lots, sale)
        else:
            results = process_sale_fifo(lots, sale)
            
        num_results = len(results)
        print(f"\n--- RSU Sale Analysis ({num_results} lots affected) ---")
        print(f"Total Shares Sold: {args.shares:,.2f} @ ${args.sale_price:,.2f}")
        print(f"Total Proceeds:    ${sale.proceeds:,.2f}")
        print("-" * 45)
        
        total_basis = 0
        total_gain = 0
        for i, r in enumerate(results):
            if num_results > args.summary_threshold and 3 <= i < num_results - 3:
                if i == 3:
                    print(f"... [{num_results - 6} intermediate lots summarized for efficiency] ...")
                total_basis += r.cost_basis
                total_gain += r.gain_loss
                continue

            print(f"Lot {r.vesting_date}: {r.shares_sold:.2f} shares")
            print(f"  Holding: {r.holding_period} ({r.holding_days} days)")
            print(f"  Basis:   ${r.cost_basis:,.2f} (${r.cost_basis_per_share:,.2f}/sh)")
            print(f"  Gain:    ${r.gain_loss:,.2f}")
            if r.basis_adjustment_needed:
                print(f"  ⚠️  ADJUSTMENT NEEDED (Form 8949): Code {r.form_8949_code}, Amount ${r.adjustment_amount:,.2f}")
            total_basis += r.cost_basis
            total_gain += r.gain_loss
            
        print("-" * 45)
        print(f"TOTAL COST BASIS: ${total_basis:,.2f}")
        print(f"TOTAL GAIN/LOSS:  ${total_gain:,.2f}")

    elif args.command == "basis":
        if args.shares_vested < 0 or args.fmv < 0 or args.shares_withheld < 0:
            print("Error: Vested shares, FMV, and withheld shares must be non-negative.")
            sys.exit(1)
            
        lot = VestingLot(
            vesting_date="N/A",
            shares_vested=args.shares_vested,
            fmv_at_vesting=args.fmv,
            shares_withheld=args.shares_withheld
        )
        print(f"\n--- Quick Basis Calculation ---")
        print(f"Shares Vested:   {lot.shares_vested:,.4f}")
        print(f"FMV at Vest:     ${lot.fmv_at_vesting:,.4f}")
        print(f"Shares Received: {lot.net_shares:,.4f}")
        print("-" * 30)
        print(f"TOTAL COST BASIS: ${lot.total_cost_basis:,.2f}")
        print(f"BASIS PER SHARE:  ${lot.cost_basis_per_share:,.4f}")
        print("\nNote: Use the TOTAL COST BASIS on Form 8949 to adjust your broker's reported basis.")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
