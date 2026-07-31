#!/usr/bin/env python3
"""Data model for savings opportunities (shared across all rule modules)."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Priority(Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class TaxOpportunity:
    """A single savings/optimization recommendation.

    Fields beyond the original finder output (docs/savings_engine_plan.md §1.2):
      savings_federal / savings_state: dollar benefit, federal and state.
        `None` means the rule deliberately makes no current-year dollar claim
        (e.g. backdoor Roth is tax-free growth, not a deduction).
      state_caveat: state non-conformity note (CA add-back, etc.) or None.
      deadline: last action date (e.g. "April 15, 2026") or None.
      rule_id: stable identifier ("backdoor_roth", "tlh", ...) for --rules filtering.
      assumptions: human-readable list of simplifying assumptions made.
      deduction_amount: the raw above-the-line / itemized deduction dollars the
        rule computes, BEFORE multiplying by a marginal rate. Exposed so two
        tools that share the same deduction helper (e.g. deduction_analyzer.py
        and the M10 senior-bonus rule) can be checked for agreement to the
        dollar without dividing savings by a rate (A9 shared-helper test).
        None for rules that make no deduction-dollar claim.
    """
    category: str
    name: str
    description: str
    potential_savings: str
    priority: Priority
    action_required: str
    documentation_needed: List[str] = field(default_factory=list)
    rule_id: str = ""
    savings_federal: Optional[float] = None
    savings_state: Optional[float] = None
    state_caveat: Optional[str] = None
    deadline: Optional[str] = None
    assumptions: List[str] = field(default_factory=list)
    deduction_amount: Optional[float] = None


def opportunity_to_dict(opportunity: TaxOpportunity) -> Dict[str, Any]:
    """Serialize an opportunity to JSON-safe primitives.

    The Priority enum is flattened to its string value (audit D1) so json.dumps
    succeeds and the output round-trips through json.loads.
    """
    d = {
        "category": opportunity.category,
        "name": opportunity.name,
        "description": opportunity.description,
        "potential_savings": opportunity.potential_savings,
        "priority": opportunity.priority.value,
        "action_required": opportunity.action_required,
        "documentation_needed": opportunity.documentation_needed,
        "rule_id": opportunity.rule_id,
        "savings_federal": opportunity.savings_federal,
        "savings_state": opportunity.savings_state,
        "state_caveat": opportunity.state_caveat,
        "deadline": opportunity.deadline,
        "assumptions": opportunity.assumptions,
        "deduction_amount": opportunity.deduction_amount,
    }
    return d