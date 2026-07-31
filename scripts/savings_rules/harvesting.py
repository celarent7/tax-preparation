#!/usr/bin/env python3
"""Tax-loss harvesting (M4).

Implements the §1222 netting ladder exactly, then quantifies the value of
harvesting currently-unrealized losses. Marginal rates come from the shared
engine helpers (ordinary via the federal bracket walker, LTCG via the
preferential-rate stacking worksheet) — no bracket code is duplicated here.
"""

from typing import Any, Dict, List, Tuple

from .engine import (
    ca_marginal_rate,
    ca_nonconformity,
    constant,
    federal_ltcg_marginal_rate,
    federal_ordinary_marginal_rate,
    filing_status_of,
)
from .models import Priority, TaxOpportunity


# ---------------------------------------------------------------------------
# §1222 netting ladder
# ---------------------------------------------------------------------------


def net_capital_gains(tax_data: Dict[str, Any], filing_status: str,
                      st_gains: float, st_losses: float,
                      lt_gains: float, lt_losses: float,
                      carryforward: float = 0.0) -> Dict[str, float]:
    """Net gains/losses per §1222, returning the taxable position.

    Steps (IRS Pub 550, §1222):
      1. Net within each class: net ST = ST gains − ST losses, net LT = LT
         gains − LT losses (the opening carryforward is treated as a LT loss
         — most carryforwards are long-term; document this assumption).
      2. Cross-net the remainders.
      3. If a net loss remains, deduct up to the capital-loss limit ($3,000,
         or $1,500 MFS) against ordinary income; the $3,000 is taken from the
         SHORT-TERM loss first, then long-term.
      4. Any unused loss carries forward RETAINING ITS CHARACTER.

    Returns a dict: taxable_st, taxable_lt, ordinary_offset, carryforward_st,
    carryforward_lt (all >= 0).
    """
    # MFS uses the halved capital-loss limit.
    if filing_status == "married_separately":
        cap = constant(tax_data, "capital_loss_limit_mfs")
    else:
        cap = constant(tax_data, "capital_loss_limit")

    # The opening carryforward is assumed LT (documented assumption).
    lt_losses_total = lt_losses + carryforward

    net_st = st_gains - st_losses
    net_lt = lt_gains - lt_losses_total

    taxable_st = 0.0
    taxable_lt = 0.0
    ordinary_offset = 0.0
    cf_st = 0.0
    cf_lt = 0.0

    def apply_loss(loss_amount: float, character: str) -> None:
        """Split a residual loss into ordinary offset + carryforward."""
        nonlocal ordinary_offset, cf_st, cf_lt
        off = min(cap, loss_amount)
        ordinary_offset += off
        remaining = loss_amount - off
        if remaining > 0:
            if character == "st":
                cf_st += remaining
            else:
                cf_lt += remaining

    if net_st >= 0 and net_lt >= 0:
        taxable_st = net_st
        taxable_lt = net_lt
    elif net_st < 0 and net_lt >= 0:
        # ST loss offsets LT gain first, then ordinary / carryforward.
        st_loss = -net_st
        offset = min(net_lt, st_loss)
        taxable_lt = net_lt - offset
        remaining = st_loss - offset
        if remaining > 0:
            apply_loss(remaining, "st")
    elif net_st >= 0 and net_lt < 0:
        # LT loss offsets ST gain first.
        lt_loss = -net_lt
        offset = min(net_st, lt_loss)
        taxable_st = net_st - offset
        remaining = lt_loss - offset
        if remaining > 0:
            apply_loss(remaining, "lt")
    else:
        # Both classes net to a loss: take the $3,000 from ST first, then LT.
        st_loss = -net_st
        lt_loss = -net_lt
        from_st = min(cap, st_loss)
        ordinary_offset += from_st
        cf_st += st_loss - from_st
        remaining_cap = cap - from_st
        if remaining_cap > 0:
            from_lt = min(remaining_cap, lt_loss)
            ordinary_offset += from_lt
            cf_lt += lt_loss - from_lt

    return {
        "taxable_st": round(taxable_st, 2),
        "taxable_lt": round(taxable_lt, 2),
        "ordinary_offset": round(ordinary_offset, 2),
        "carryforward_st": round(cf_st, 2),
        "carryforward_lt": round(cf_lt, 2),
    }


# ---------------------------------------------------------------------------
# M4 rule
# ---------------------------------------------------------------------------


def rule_tax_loss_harvesting(profile: Dict[str, Any],
                             tax_data: Dict[str, Any]) -> List[TaxOpportunity]:
    """M4: quantify harvesting unrealized losses against the realized position."""
    unreal_st = float(profile.get("unrealized_st_losses", 0) or 0)
    unreal_lt = float(profile.get("unrealized_lt_losses", 0) or 0)
    if unreal_st <= 0 and unreal_lt <= 0:
        return []

    fs = filing_status_of(profile)
    r_st_g = profile.get("realized_st_gains", 0) or 0
    r_st_l = profile.get("realized_st_losses", 0) or 0
    r_lt_g = profile.get("realized_lt_gains", 0) or 0
    r_lt_l = profile.get("realized_lt_losses", 0) or 0
    cf = profile.get("capital_loss_carryforward", 0) or 0

    # Position BEFORE harvesting (realized only + carryforward).
    before = net_capital_gains(tax_data, fs, r_st_g, r_st_l, r_lt_g, r_lt_l, cf)
    # Position AFTER harvesting (realize the unrealized losses too).
    after = net_capital_gains(tax_data, fs, r_st_g, r_st_l + unreal_st,
                              r_lt_g, r_lt_l + unreal_lt, cf)

    ordinary_rate = federal_ordinary_marginal_rate(tax_data, fs, profile["taxable_income"])
    ltcg_rate = federal_ltcg_marginal_rate(tax_data, fs, profile["taxable_income"])

    # Federal savings = reduction in taxable ST (ordinary) + reduction in
    # taxable LT (LTCG rate) + increase in the ordinary-income offset.
    saved_st = (before["taxable_st"] - after["taxable_st"]) * ordinary_rate
    saved_lt = (before["taxable_lt"] - after["taxable_lt"]) * ltcg_rate
    saved_ordinary = (after["ordinary_offset"] - before["ordinary_offset"]) * ordinary_rate
    savings_federal = round(max(0.0, saved_st + saved_lt + saved_ordinary), 2)

    total_harvested = unreal_st + unreal_lt
    new_cf = after["carryforward_st"] + after["carryforward_lt"]

    # CA caveat: CA has no preferential gains rate, so state savings use CA
    # ordinary rates. Only the ST/LT taxable reductions matter for CA (the
    # $3,000 ordinary offset also reduces CA ordinary income at the CA rate).
    savings_state = None
    state_caveat = None
    if str(profile.get("state", "")).upper() == "CA":
        nc = ca_nonconformity(tax_data)
        if not nc.get("capital_gains_preferential", True):
            ca_rate = ca_marginal_rate(tax_data, str(tax_data.get("tax_year", "2025")),
                                       fs, profile["agi"]) or 0.0
            ca_saved = ((before["taxable_st"] - after["taxable_st"])
                        + (before["taxable_lt"] - after["taxable_lt"])
                        + (after["ordinary_offset"] - before["ordinary_offset"])) * ca_rate
            savings_state = round(max(0.0, ca_saved), 2)
            state_caveat = (
                "California has no preferential capital-gains rate: harvested losses "
                "that offset gains save at your CA ORDINARY marginal rate, not a LTCG "
                "rate. State savings shown use that ordinary rate."
            )

    return [TaxOpportunity(
        category="Investments",
        name="Tax-Loss Harvesting",
        description=(f"Harvest ${total_harvested:,.0f} of unrealized losses "
                     f"(${unreal_st:,.0f} ST, ${unreal_lt:,.0f} LT) to offset gains "
                     "and/or up to $3,000 of ordinary income."),
        potential_savings=(f"~${savings_federal:,.0f} federal"
                          + (f" + ${savings_state:,.0f} CA" if savings_state else "")),
        priority=Priority.MEDIUM,
        action_required=("Sell the losing positions to realize the losses; avoid "
                         "buying substantially identical securities within 30 days "
                         "(wash-sale rule)."),
        documentation_needed=["Trade confirmations", "1099-B", "Wash-sale tracking log"],
        rule_id="tlh",
        savings_federal=savings_federal,
        savings_state=savings_state,
        state_caveat=state_caveat,
        deadline="By the last business day of the tax year (Dec 31).",
        assumptions=[f"Federal ordinary marginal rate {ordinary_rate:.1%}; LTCG rate {ltcg_rate:.0%}.",
                    "ST losses offset ST gains first (ordinary rate), then LT gains, "
                    "then up to $3,000 of ordinary income.",
                    f"Opening carryforward ${cf:,.0f} treated as long-term.",
                    f"Post-harvest carryforward ${new_cf:,.0f} retains its character.",
                    "Wash-sale rule: see references/investment_taxes.md (30-day window)."],
    )]