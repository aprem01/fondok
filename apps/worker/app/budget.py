"""Per-deal cost budget enforcement.

Each agent appends ``ModelCall``-shaped entries to ``DealState`` as it
runs. Before kicking off an agent that costs real money (Extractor /
Analyst / etc.) the graph node calls ``check_budget(state, stage=...)``
to sum prior spend and refuse to proceed if cumulative cost would
breach ``DEFAULT_DEAL_BUDGET_USD``.

Design goals:
  * Cheap — pure Python, no I/O beyond reading the state dict.
  * Conservative — we price *before* the call so a runaway retry loop
    can't burn through the ceiling in one overrun.
  * Honest — unknown models cost 0; we log a warning instead of failing
    open or closed.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import get_settings

logger = logging.getLogger(__name__)


class BudgetExceededError(RuntimeError):
    """Raised when a pending agent call would push a deal over budget."""

    def __init__(self, *, deal_id: str, spent_usd: float, budget_usd: float) -> None:
        self.deal_id = deal_id
        self.spent_usd = spent_usd
        self.budget_usd = budget_usd
        super().__init__(
            f"deal {deal_id}: spend ${spent_usd:.4f} has reached the "
            f"${budget_usd:.2f} budget — refusing further LLM calls"
        )


# Per-million-token pricing (input, output) for the models the worker
# actually invokes. Add a new entry when a tenant pins a different model
# via env var. Unknown prefixes price at 0 with a warning.
_PRICING: dict[str, tuple[float, float]] = {
    # Claude 4.x family
    "claude-opus-4-7": (15.00, 75.00),
    "claude-opus-4": (15.00, 75.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-4": (1.00, 5.00),
    # Legacy 3.x — kept so an old override doesn't silently price at 0.
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-5-haiku": (0.80, 4.00),
}


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _price_for(model: str) -> tuple[float, float]:
    m = (model or "").lower()
    for prefix, price in _PRICING.items():
        if m.startswith(prefix):
            return price
    if m:
        logger.warning("budget: no pricing entry for model=%s — counting as $0", m)
    return (0.0, 0.0)


def estimate_spent_usd(model_calls: list[Any]) -> float:
    """Sum USD spend across a DealState's ``model_calls`` list.

    Trusts ``cost_usd`` on the call when present; otherwise recomputes
    from token counts using the per-model pricing table.
    """
    total = 0.0
    for call in model_calls or []:
        stored = _attr(call, "cost_usd", None)
        if stored is not None:
            try:
                total += float(stored)
                continue
            except (TypeError, ValueError):
                pass
        model = str(_attr(call, "model", "") or "")
        in_tok = int(_attr(call, "input_tokens", 0) or 0)
        out_tok = int(_attr(call, "output_tokens", 0) or 0)
        in_price, out_price = _price_for(model)
        total += (in_tok * in_price + out_tok * out_price) / 1_000_000
    return total


def check_budget(state: dict[str, Any] | Any, *, stage: str) -> None:
    """Raise ``BudgetExceededError`` if ``state``'s accumulated spend
    is at or above the configured deal budget.

    A ``DEFAULT_DEAL_BUDGET_USD=0`` setting disables enforcement.
    """
    settings = get_settings()
    budget = float(settings.DEFAULT_DEAL_BUDGET_USD or 0.0)
    if budget <= 0:
        return

    model_calls = (
        state.get("model_calls")
        if isinstance(state, dict)
        else _attr(state, "model_calls")
    ) or []
    spent = estimate_spent_usd(list(model_calls))
    deal_id = (
        str(state.get("deal_id"))
        if isinstance(state, dict)
        else str(_attr(state, "deal_id"))
    ) or "<unknown>"

    if spent >= budget:
        logger.error(
            "budget: deal=%s stage=%s spent=$%.4f budget=$%.2f → halting",
            deal_id,
            stage,
            spent,
            budget,
        )
        raise BudgetExceededError(
            deal_id=deal_id, spent_usd=spent, budget_usd=budget
        )

    warn_at = float(settings.DEAL_BUDGET_WARN_AT or 0.0)
    if warn_at > 0 and spent >= warn_at * budget:
        logger.warning(
            "budget: deal=%s stage=%s spent=$%.4f of $%.2f (%.0f%%)",
            deal_id,
            stage,
            spent,
            budget,
            (spent / budget) * 100,
        )


def budget_status(model_calls: list[Any]) -> dict[str, Any]:
    """Return a UI-ready budget snapshot for the /costs endpoint."""
    settings = get_settings()
    budget = float(settings.DEFAULT_DEAL_BUDGET_USD or 0.0)
    spent = estimate_spent_usd(model_calls)
    if budget <= 0:
        return {
            "enabled": False,
            "spent_usd": round(spent, 6),
            "budget_usd": 0.0,
            "pct_used": 0.0,
            "warn_threshold_pct": 0.0,
            "state": "disabled",
        }
    pct = spent / budget
    warn_at = float(settings.DEAL_BUDGET_WARN_AT or 0.0)
    state_label = "ok"
    if pct >= 1.0:
        state_label = "exceeded"
    elif pct >= warn_at:
        state_label = "warn"
    return {
        "enabled": True,
        "spent_usd": round(spent, 6),
        "budget_usd": round(budget, 2),
        "pct_used": round(pct, 4),
        "warn_threshold_pct": round(warn_at, 4),
        "state": state_label,
    }


__all__ = [
    "BudgetExceededError",
    "budget_status",
    "check_budget",
    "estimate_spent_usd",
]
