#!/usr/bin/env python3
"""
ESPP Calculator — Employee Stock Purchase Plan tax calculations.

Handles:
  - Qualifying vs disqualifying disposition determination
  - Cost basis calculation for both disposition types
  - Ordinary income computation
  - Capital gain/loss breakdown
  - Form 8949 entry generation
  - Lot tracking for multiple ESPP purchases
"""

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ESPP_MAX_DISCOUNT = 0.15  # Typical 15% discount
ESPP_ANNUAL_LIMIT = 25000  # $25,000 annual FMV limit under Section 423


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class ESPPPurchase:
    """Represents a single ESPP purchase lot."""
    offering_date: date
    purchase_date: date
    shares: float
    purchase_price: float      # Price you actually paid (after discount)
    fmv_at_offering: float     # FMV on offering/enrollment date
    fmv_at_purchase: float     # FMV on purchase date
    discount_pct: float = 0.15 # Discount percentage (typically 15%)
    # Optional: for look-back plans, the purchase price is the lesser of
    # (offering_date FMV * (1 - discount)) or (purchase_date FMV * (1 - discount))

    @property
    def total_cost(self) -> float:
        return round(self.shares * self.purchase_price, 2)

    @property
    def fmv_total_at_purchase(self) -> float:
        return round(self.shares * self.fmv_at_purchase, 2)


@dataclass
class ESPPSale:
    """Represents a sale of ESPP shares."""
    sale_date: date
    shares_sold: float
    sale_price: float
    # Link to purchase lot
    purchase: ESPPPurchase
    # Broker-reported values (may be incorrect)
    reported_proceeds: Optional[float] = None
    reported_basis: Optional[float] = None

    @property
    def actual_proceeds(self) -> float:
        return round(self.shares_sold * self.sale_price, 2)


@dataclass
class DispositionResult:
    """Result of ESPP disposition analysis."""
    disposition_type: str        # "qualifying" or "disqualifying"
    holding_period_met: bool
    offering_to_sale_days: int
    purchase_to_sale_days: int
    offering_requirement: int    # 730 days (2 years)
    purchase_requirement: int    # 366 days (1 year)
    sale_date: date
    offering_date: date
    purchase_date: date
    earliest_qualifying_date: date


@dataclass
class TaxBreakdown:
    """Complete tax breakdown for an ESPP sale."""
    disposition_type: str
    shares_sold: float
    purchase_price: float
    sale_price: float
    fmv_at_purchase: float
    fmv_at_offering: float
    # Calculated values
    ordinary_income: float
    ordinary_income_per_share: float
    adjusted_basis: float
    adjusted_basis_per_share: float
    capital_gain_loss: float
    capital_gain_type: str  # "long-term" or "short-term"
    total_proceeds: float
    # Form 8949 fields
    form_8949_adjustment_code: Optional[str]
    form_8949_adjustment_amount: Optional[float]
    reported_basis: Optional[float]


# ---------------------------------------------------------------------------
# Core Calculations
# ---------------------------------------------------------------------------

def parse_date(date_str: str) -> date:
    """Parse a date string in YYYY-MM-DD format."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        # Try other common formats
        for fmt in ["%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"]:
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Cannot parse date: {date_str}. Use YYYY-MM-DD format.")


def determine_disposition(
    offering_date: date,
    purchase_date: date,
    sale_date: date,
) -> DispositionResult:
    """
    Determine if an ESPP sale is a qualifying or disqualifying disposition.

    Qualifying disposition requires BOTH:
      1. Sale is > 2 years after the offering (enrollment/grant) date
      2. Sale is > 1 year after the purchase date
    """
    offering_to_sale = (sale_date - offering_date).days
    purchase_to_sale = (sale_date - purchase_date).days

    offering_requirement = 730   # 2 years
    purchase_requirement = 366   # 1 year + 1 day

    offering_met = offering_to_sale >= offering_requirement
    purchase_met = purchase_to_sale >= purchase_requirement

    # Earliest qualifying date is the later of the two requirements
    earliest_from_offering = offering_date + timedelta(days=offering_requirement)
    earliest_from_purchase = purchase_date + timedelta(days=purchase_requirement)
    earliest_qualifying = max(earliest_from_offering, earliest_from_purchase)

    is_qualifying = offering_met and purchase_met

    return DispositionResult(
        disposition_type="qualifying" if is_qualifying else "disqualifying",
        holding_period_met=is_qualifying,
        offering_to_sale_days=offering_to_sale,
        purchase_to_sale_days=purchase_to_sale,
        offering_requirement=offering_requirement,
        purchase_requirement=purchase_requirement,
        sale_date=sale_date,
        offering_date=offering_date,
        purchase_date=purchase_date,
        earliest_qualifying_date=earliest_qualifying,
    )


def calculate_tax_breakdown(
    sale: ESPPSale,
    reported_basis: Optional[float] = None,
) -> TaxBreakdown:
    """
    Calculate the full tax breakdown for an ESPP sale.

    QUALIFYING DISPOSITION:
      - Ordinary income = LESSER of:
          (a) Sale price - Purchase price (the actual gain), OR
          (b) FMV at offering date × discount % (the "statutory" discount)
        BUT: If sale price < purchase price, ordinary income = $0
      - Adjusted basis = Purchase price + Ordinary income
      - Capital gain = Sale price - Adjusted basis (long-term)

    DISQUALIFYING DISPOSITION:
      - Ordinary income = FMV at purchase - Purchase price (the "bargain element")
        This is the spread on the purchase date, regardless of later stock movement
      - Adjusted basis = Purchase price + Ordinary income = FMV at purchase
      - Capital gain = Sale price - FMV at purchase (short-term or long-term)
    """
    purchase = sale.purchase
    disposition = determine_disposition(
        purchase.offering_date, purchase.purchase_date, sale.sale_date
    )

    proceeds = sale.actual_proceeds
    shares = sale.shares_sold

    if disposition.disposition_type == "qualifying":
        # Qualifying disposition
        actual_gain_per_share = sale.sale_price - purchase.purchase_price
        statutory_discount_per_share = purchase.fmv_at_offering * purchase.discount_pct

        if sale.sale_price < purchase.purchase_price:
            # Sold at a loss below purchase price — no ordinary income
            ordinary_per_share = 0.0
        else:
            ordinary_per_share = min(actual_gain_per_share, statutory_discount_per_share)

        ordinary_income = round(ordinary_per_share * shares, 2)
        basis_per_share = purchase.purchase_price + ordinary_per_share
        adjusted_basis = round(basis_per_share * shares, 2)
        capital_gl = round(proceeds - adjusted_basis, 2)
        cap_type = "long-term"  # Always long-term for qualifying

    else:
        # Disqualifying disposition
        ordinary_per_share = purchase.fmv_at_purchase - purchase.purchase_price
        ordinary_income = round(ordinary_per_share * shares, 2)
        basis_per_share = purchase.fmv_at_purchase  # purchase_price + ordinary = FMV at purchase
        adjusted_basis = round(basis_per_share * shares, 2)
        capital_gl = round(proceeds - adjusted_basis, 2)

        # Holding period for the capital gain portion
        purchase_to_sale = (sale.sale_date - purchase.purchase_date).days
        cap_type = "long-term" if purchase_to_sale >= 366 else "short-term"

    # Form 8949 adjustment
    adj_code = None
    adj_amount = None
    if reported_basis is not None and abs(reported_basis - adjusted_basis) > 0.50:
        adj_code = "B"
        adj_amount = round(adjusted_basis - reported_basis, 2)

    return TaxBreakdown(
        disposition_type=disposition.disposition_type,
        shares_sold=shares,
        purchase_price=purchase.purchase_price,
        sale_price=sale.sale_price,
        fmv_at_purchase=purchase.fmv_at_purchase,
        fmv_at_offering=purchase.fmv_at_offering,
        ordinary_income=ordinary_income,
        ordinary_income_per_share=round(ordinary_per_share, 4),
        adjusted_basis=adjusted_basis,
        adjusted_basis_per_share=round(basis_per_share, 4),
        capital_gain_loss=capital_gl,
        capital_gain_type=cap_type,
        total_proceeds=proceeds,
        form_8949_adjustment_code=adj_code,
        form_8949_adjustment_amount=adj_amount,
        reported_basis=reported_basis,
    )


def process_lots(lots_data: List[Dict]) -> List[Dict]:
    """Process multiple ESPP lots from JSON data."""
    results = []
    for lot in lots_data:
        purchase = ESPPPurchase(
            offering_date=parse_date(lot["offering_date"]),
            purchase_date=parse_date(lot["purchase_date"]),
            shares=lot["shares"],
            purchase_price=lot["purchase_price"],
            fmv_at_offering=lot["fmv_at_offering"],
            fmv_at_purchase=lot["fmv_at_purchase"],
            discount_pct=lot.get("discount_pct", 0.15),
        )

        sale_info = lot.get("sale")
        if sale_info:
            sale = ESPPSale(
                sale_date=parse_date(sale_info["sale_date"]),
                shares_sold=sale_info.get("shares_sold", purchase.shares),
                sale_price=sale_info["sale_price"],
                purchase=purchase,
                reported_basis=sale_info.get("reported_basis"),
            )
            breakdown = calculate_tax_breakdown(sale, sale_info.get("reported_basis"))
            disposition = determine_disposition(
                purchase.offering_date, purchase.purchase_date,
                parse_date(sale_info["sale_date"])
            )
        else:
            breakdown = None
            disposition = None

        results.append({
            "purchase": {
                "offering_date": str(purchase.offering_date),
                "purchase_date": str(purchase.purchase_date),
                "shares": purchase.shares,
                "purchase_price": purchase.purchase_price,
                "fmv_at_offering": purchase.fmv_at_offering,
                "fmv_at_purchase": purchase.fmv_at_purchase,
                "discount_pct": purchase.discount_pct,
                "total_cost": purchase.total_cost,
                "fmv_total_at_purchase": purchase.fmv_total_at_purchase,
                "bargain_element": round(
                    (purchase.fmv_at_purchase - purchase.purchase_price) * purchase.shares, 2
                ),
            },
            "disposition": {
                "type": disposition.disposition_type,
                "offering_to_sale_days": disposition.offering_to_sale_days,
                "purchase_to_sale_days": disposition.purchase_to_sale_days,
                "earliest_qualifying_date": str(disposition.earliest_qualifying_date),
            } if disposition else None,
            "tax_breakdown": {
                "disposition_type": breakdown.disposition_type,
                "ordinary_income": breakdown.ordinary_income,
                "ordinary_income_per_share": breakdown.ordinary_income_per_share,
                "adjusted_basis": breakdown.adjusted_basis,
                "adjusted_basis_per_share": breakdown.adjusted_basis_per_share,
                "capital_gain_loss": breakdown.capital_gain_loss,
                "capital_gain_type": breakdown.capital_gain_type,
                "total_proceeds": breakdown.total_proceeds,
                "form_8949": {
                    "adjustment_code": breakdown.form_8949_adjustment_code,
                    "adjustment_amount": breakdown.form_8949_adjustment_amount,
                    "reported_basis": breakdown.reported_basis,
                    "correct_basis": breakdown.adjusted_basis,
                } if breakdown.form_8949_adjustment_code else None,
            } if breakdown else None,
        })

    return results


# ---------------------------------------------------------------------------
# Output Formatting
# ---------------------------------------------------------------------------

def format_disposition_result(result: DispositionResult) -> str:
    """Format disposition determination as text."""
    lines = []
    lines.append("")
    lines.append("=" * 60)
    lines.append("ESPP DISPOSITION ANALYSIS")
    lines.append("=" * 60)
    lines.append(f"Offering Date:  {result.offering_date}")
    lines.append(f"Purchase Date:  {result.purchase_date}")
    lines.append(f"Sale Date:      {result.sale_date}")
    lines.append("")

    # Offering period check
    offer_icon = "✅" if result.offering_to_sale_days >= result.offering_requirement else "❌"
    lines.append(f"{offer_icon} Offering → Sale: {result.offering_to_sale_days} days "
                 f"(need {result.offering_requirement}+ for qualifying)")

    # Purchase period check
    purchase_icon = "✅" if result.purchase_to_sale_days >= result.purchase_requirement else "❌"
    lines.append(f"{purchase_icon} Purchase → Sale: {result.purchase_to_sale_days} days "
                 f"(need {result.purchase_requirement}+ for qualifying)")

    lines.append("")
    if result.holding_period_met:
        lines.append("📗 QUALIFYING DISPOSITION")
        lines.append("   Ordinary income limited to the lesser of actual gain")
        lines.append("   or the statutory discount. Capital gain is long-term.")
    else:
        lines.append("📙 DISQUALIFYING DISPOSITION")
        lines.append("   Ordinary income = full bargain element (FMV at purchase - price paid).")
        lines.append(f"   Earliest qualifying sale date: {result.earliest_qualifying_date}")

    lines.append("=" * 60)
    lines.append("")
    return "\n".join(lines)


def format_tax_breakdown(breakdown: TaxBreakdown) -> str:
    """Format full tax breakdown as text."""
    lines = []
    lines.append("")
    lines.append("=" * 60)
    disp_label = "QUALIFYING" if breakdown.disposition_type == "qualifying" else "DISQUALIFYING"
    lines.append(f"ESPP TAX BREAKDOWN — {disp_label} DISPOSITION")
    lines.append("=" * 60)

    lines.append(f"\nShares Sold:         {breakdown.shares_sold}")
    lines.append(f"Purchase Price:      ${breakdown.purchase_price:,.2f}/share")
    lines.append(f"Sale Price:          ${breakdown.sale_price:,.2f}/share")
    lines.append(f"FMV at Purchase:     ${breakdown.fmv_at_purchase:,.2f}/share")
    lines.append(f"FMV at Offering:     ${breakdown.fmv_at_offering:,.2f}/share")

    lines.append(f"\n{'─' * 50}")
    lines.append("  TAX COMPUTATION")
    lines.append(f"{'─' * 50}")

    lines.append(f"\n1. Ordinary Income:  ${breakdown.ordinary_income:,.2f}")
    lines.append(f"   (${breakdown.ordinary_income_per_share:,.4f}/share)")
    if breakdown.disposition_type == "qualifying":
        lines.append("   [Lesser of actual gain or statutory discount]")
    else:
        lines.append("   [Bargain element: FMV at purchase − purchase price]")
        lines.append("   ⚠️  Report on W-2 (employer should include) or Form 1040 Line 1")

    lines.append(f"\n2. Adjusted Basis:   ${breakdown.adjusted_basis:,.2f}")
    lines.append(f"   (${breakdown.adjusted_basis_per_share:,.4f}/share)")
    lines.append("   [Purchase price + ordinary income recognized]")

    lines.append(f"\n3. Proceeds:         ${breakdown.total_proceeds:,.2f}")

    lines.append(f"\n4. Capital Gain/Loss: ${breakdown.capital_gain_loss:,.2f}")
    lines.append(f"   Type: {breakdown.capital_gain_type.upper()}")
    lines.append("   [Proceeds − Adjusted Basis]")

    # Form 8949 adjustment
    if breakdown.form_8949_adjustment_code:
        lines.append(f"\n{'─' * 50}")
        lines.append("  FORM 8949 ADJUSTMENT REQUIRED")
        lines.append(f"{'─' * 50}")
        lines.append(f"  Broker-reported basis:  ${breakdown.reported_basis:,.2f}")
        lines.append(f"  Correct basis:          ${breakdown.adjusted_basis:,.2f}")
        lines.append(f"  Adjustment code:        {breakdown.form_8949_adjustment_code}")
        lines.append(f"  Adjustment amount:      ${breakdown.form_8949_adjustment_amount:,.2f}")

    lines.append(f"\n{'─' * 50}")
    lines.append("  SUMMARY")
    lines.append(f"{'─' * 50}")
    lines.append(f"  Ordinary income (W-2):     ${breakdown.ordinary_income:,.2f}")
    lines.append(f"  Capital gain/loss:         ${breakdown.capital_gain_loss:,.2f} ({breakdown.capital_gain_type})")
    total = breakdown.ordinary_income + breakdown.capital_gain_loss
    lines.append(f"  Total economic gain/loss:  ${total:,.2f}")

    lines.append("\n" + "=" * 60)
    lines.append("")
    return "\n".join(lines)


def format_lots_summary(results: List[Dict]) -> str:
    """Format multi-lot summary as text."""
    lines = []
    lines.append("")
    lines.append("=" * 70)
    lines.append("ESPP LOT SUMMARY")
    lines.append("=" * 70)

    total_ordinary = 0
    total_capgain = 0

    for i, lot in enumerate(results, 1):
        p = lot["purchase"]
        lines.append(f"\n{'─' * 60}")
        lines.append(f"  LOT {i}: {p['shares']} shares purchased {p['purchase_date']}")
        lines.append(f"{'─' * 60}")
        lines.append(f"  Purchase price: ${p['purchase_price']:,.2f}/share "
                     f"(cost: ${p['total_cost']:,.2f})")
        lines.append(f"  FMV at purchase: ${p['fmv_at_purchase']:,.2f} "
                     f"| Bargain element: ${p['bargain_element']:,.2f}")

        disp = lot.get("disposition")
        tax = lot.get("tax_breakdown")

        if disp:
            icon = "📗" if disp["type"] == "qualifying" else "📙"
            lines.append(f"  {icon} {disp['type'].upper()} disposition")

        if tax:
            lines.append(f"  Ordinary income: ${tax['ordinary_income']:,.2f} "
                        f"| Cap gain: ${tax['capital_gain_loss']:,.2f} ({tax['capital_gain_type']})")
            total_ordinary += tax["ordinary_income"]
            total_capgain += tax["capital_gain_loss"]

            f8949 = tax.get("form_8949")
            if f8949:
                lines.append(f"  ⚠️  Form 8949 adjustment needed: "
                           f"code {f8949['adjustment_code']}, "
                           f"amount ${f8949['adjustment_amount']:,.2f}")
        else:
            lines.append("  (Not yet sold)")

    lines.append(f"\n{'=' * 70}")
    lines.append(f"TOTALS: Ordinary income ${total_ordinary:,.2f} | "
                f"Capital gain/loss ${total_capgain:,.2f}")
    lines.append("=" * 70)
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ESPP tax calculator — disposition analysis, cost basis, and Form 8949.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  disposition   Determine if a sale is qualifying or disqualifying
  basis         Calculate cost basis and tax breakdown for a sale
  lots          Process multiple ESPP lots from a JSON file

Examples:
  # Check disposition type
  %(prog)s disposition --offering-date 2023-01-01 --purchase-date 2023-06-30 --sale-date 2024-08-15

  # Calculate full tax breakdown
  %(prog)s basis --offering-date 2023-01-01 --purchase-date 2023-06-30 \\
    --fmv-at-offering 100 --fmv-at-purchase 110 --purchase-price 85 \\
    --sale-date 2025-08-01 --sale-price 130 --shares 100

  # Process multiple lots from file
  %(prog)s lots --file espp_purchases.json

ESPP Holding Period Rules:
  QUALIFYING requires BOTH:
    • > 2 years from offering (enrollment) date
    • > 1 year from purchase date
  DISQUALIFYING: Either condition not met
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # -- disposition subcommand --
    disp_parser = subparsers.add_parser("disposition", help="Check qualifying vs disqualifying")
    disp_parser.add_argument("--offering-date", required=True, help="Offering/enrollment date (YYYY-MM-DD)")
    disp_parser.add_argument("--purchase-date", required=True, help="Purchase date (YYYY-MM-DD)")
    disp_parser.add_argument("--sale-date", required=True, help="Sale date (YYYY-MM-DD)")
    disp_parser.add_argument("--output-format", choices=["text", "json"], default="text")

    # -- basis subcommand --
    basis_parser = subparsers.add_parser("basis", help="Calculate cost basis and tax breakdown")
    basis_parser.add_argument("--offering-date", required=True, help="Offering/enrollment date")
    basis_parser.add_argument("--purchase-date", required=True, help="Purchase date")
    basis_parser.add_argument("--fmv-at-offering", required=True, type=float, help="FMV on offering date")
    basis_parser.add_argument("--fmv-at-purchase", required=True, type=float, help="FMV on purchase date")
    basis_parser.add_argument("--purchase-price", required=True, type=float, help="Actual price paid per share")
    basis_parser.add_argument("--sale-date", required=True, help="Sale date")
    basis_parser.add_argument("--sale-price", required=True, type=float, help="Sale price per share")
    basis_parser.add_argument("--shares", required=True, type=float, help="Number of shares sold")
    basis_parser.add_argument("--discount-pct", type=float, default=0.15, help="Discount percentage (default: 0.15)")
    basis_parser.add_argument("--reported-basis", type=float, help="1099-B reported cost basis (to check for errors)")
    basis_parser.add_argument("--output-format", choices=["text", "json"], default="text")

    # -- lots subcommand --
    lots_parser = subparsers.add_parser("lots", help="Process multiple ESPP lots")
    lots_parser.add_argument("--file", required=True, help="Path to JSON file with ESPP lot data")
    lots_parser.add_argument("--output-format", choices=["text", "json"], default="text")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "disposition":
        result = determine_disposition(
            parse_date(args.offering_date),
            parse_date(args.purchase_date),
            parse_date(args.sale_date),
        )
        if args.output_format == "json":
            print(json.dumps({
                "disposition_type": result.disposition_type,
                "holding_period_met": result.holding_period_met,
                "offering_to_sale_days": result.offering_to_sale_days,
                "purchase_to_sale_days": result.purchase_to_sale_days,
                "earliest_qualifying_date": str(result.earliest_qualifying_date),
            }, indent=2))
        else:
            print(format_disposition_result(result))

    elif args.command == "basis":
        purchase = ESPPPurchase(
            offering_date=parse_date(args.offering_date),
            purchase_date=parse_date(args.purchase_date),
            shares=args.shares,
            purchase_price=args.purchase_price,
            fmv_at_offering=args.fmv_at_offering,
            fmv_at_purchase=args.fmv_at_purchase,
            discount_pct=args.discount_pct,
        )
        sale = ESPPSale(
            sale_date=parse_date(args.sale_date),
            shares_sold=args.shares,
            sale_price=args.sale_price,
            purchase=purchase,
        )
        breakdown = calculate_tax_breakdown(sale, args.reported_basis)

        if args.output_format == "json":
            print(json.dumps({
                "disposition_type": breakdown.disposition_type,
                "shares_sold": breakdown.shares_sold,
                "ordinary_income": breakdown.ordinary_income,
                "ordinary_income_per_share": breakdown.ordinary_income_per_share,
                "adjusted_basis": breakdown.adjusted_basis,
                "adjusted_basis_per_share": breakdown.adjusted_basis_per_share,
                "capital_gain_loss": breakdown.capital_gain_loss,
                "capital_gain_type": breakdown.capital_gain_type,
                "total_proceeds": breakdown.total_proceeds,
                "form_8949_adjustment": {
                    "code": breakdown.form_8949_adjustment_code,
                    "amount": breakdown.form_8949_adjustment_amount,
                    "reported_basis": breakdown.reported_basis,
                    "correct_basis": breakdown.adjusted_basis,
                } if breakdown.form_8949_adjustment_code else None,
            }, indent=2))
        else:
            print(format_tax_breakdown(breakdown))

    elif args.command == "lots":
        try:
            with open(args.file, "r") as f:
                lots_data = json.load(f)
        except FileNotFoundError:
            print(f"Error: File not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON: {e}", file=sys.stderr)
            sys.exit(1)

        # Handle both {"lots": [...]} and plain [...]
        if isinstance(lots_data, dict):
            lots_data = lots_data.get("lots", lots_data.get("purchases", []))

        results = process_lots(lots_data)

        if args.output_format == "json":
            print(json.dumps(results, indent=2))
        else:
            print(format_lots_summary(results))


if __name__ == "__main__":
    main()
