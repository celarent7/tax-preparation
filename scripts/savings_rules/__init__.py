"""Savings & Optimization Engine rule registry (docs/savings_engine_plan.md §1.2).

Each rule is a pure function ``(profile, tax_data) -> list[TaxOpportunity]``.
The finder iterates the RULES registry (or a filtered subset via --rules).
Rule families live one per module: retirement, harvesting, charitable,
self_employment, obbba.
"""

from typing import Any, Callable, Dict, List

from .models import Priority, TaxOpportunity, opportunity_to_dict  # noqa: F401
from .retirement import (
    rule_401k_headroom,
    rule_backdoor_roth,
    rule_mega_backdoor_roth,
    rule_hsa,
)
from .harvesting import rule_tax_loss_harvesting
from .charitable import rule_appreciated_stock_donation, rule_qcd
from .self_employment import rule_se_health_insurance, rule_section179_bonus
from .obbba import rule_senior_bonus, rule_tips_overtime

#: Stable rule_id -> rule function. The finder filters on rule_id via --rules.
RULES: Dict[str, Callable[[Dict[str, Any], Dict[str, Any]], List[TaxOpportunity]]] = {
    "k401_headroom": rule_401k_headroom,
    "backdoor_roth": rule_backdoor_roth,
    "mega_backdoor_roth": rule_mega_backdoor_roth,
    "hsa": rule_hsa,
    "tlh": rule_tax_loss_harvesting,
    "appreciated_stock_donation": rule_appreciated_stock_donation,
    "qcd": rule_qcd,
    "se_health_insurance": rule_se_health_insurance,
    "section179_bonus": rule_section179_bonus,
    "senior_bonus": rule_senior_bonus,
    "tips_deduction": rule_tips_overtime,  # M11 rule_id also covers overtime
}

__all__ = [
    "RULES", "Priority", "TaxOpportunity", "opportunity_to_dict",
]