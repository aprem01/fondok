"""Op-ratio precedence resolver — pick the winning source per ratio.

Wave 2 P2.7 (Sam's June 2026 ask: "Wants op-ratios extracted from
CBRE / in-house portfolio P&Ls (not HOST defaults)."). Before this
module Fondok hand-coded the source precedence inside
``_load_engine_inputs`` — analyst-override beat T-12 beat
``pnl_benchmark`` beat seed. Sam needs two additional tiers, so we
externalize the precedence chain into a tested resolver.

Precedence chain, highest to lowest:

    1. ``analyst_override``  — explicit analyst intent (with note)
    2. ``t12_actual``        — subject hotel's own historical P&L
    3. ``portfolio_pnl``     — analyst firm's in-house portfolio benchmark
    4. ``cbre_horizons``     — CBRE Horizons benchmark for same chain scale
    5. ``pnl_benchmark``     — HostStats-equivalent generic industry default
    6. ``seed``              — Kimpton fixture last-resort default

Why this order:

* Override beats everything because the analyst's intent is final.
* T-12 (the subject's own actuals) beats every benchmark — when we
  know what the hotel actually spent, that's the most credible
  Y1 anchor.
* Portfolio P&L (the firm's own roll-up) beats CBRE Horizons because
  the firm OWNS the underlying P&Ls; CBRE's roll-up is a black box of
  third-party reports.
* CBRE Horizons beats the generic HostStats default because CBRE is
  segmented by chain scale + submarket; HostStats is one-size-fits-all.
* Seed is the absolute fallback that keeps the engine from crashing
  when nothing else is available.

Chain-scale matching for CBRE Horizons:

CBRE Horizons reports are segmented by chain scale (Upper Upscale /
Upscale / Upper Midscale / etc). When the subject deal carries a
chain-scale tag and the CBRE candidate doesn't match, the resolver
falls THROUGH to the next-lower tier (HostStats -> seed) instead of
applying a mismatched benchmark. A debug log records the fall-through
so analysts can trace it on the Engine Run page.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger(__name__)


RATIO_PRECEDENCE: tuple[str, ...] = (
    "analyst_override",
    "t12_actual",
    "portfolio_pnl",
    "cbre_horizons",
    "pnl_benchmark",
    "seed",
)


@dataclass(frozen=True)
class RatioValue:
    """One candidate ratio value with provenance metadata."""

    value: float
    source: str
    document_id: str | None = None
    chain_scale: str | None = None


def _chain_scale_matches(
    candidate: RatioValue, subject_chain_scale: str | None
) -> bool:
    """Loose chain-scale equality (case+whitespace+underscore-insensitive)."""
    if candidate.chain_scale is None or subject_chain_scale is None:
        return True

    def _norm(s: str) -> str:
        return s.strip().lower().replace("_", " ").replace("-", " ")

    return _norm(candidate.chain_scale) == _norm(subject_chain_scale)


def resolve_ratio(
    field_name: str,
    candidates: Mapping[str, RatioValue | None],
    *,
    subject_chain_scale: str | None = None,
) -> RatioValue | None:
    """Pick the highest-precedence non-None ratio for ``field_name``.

    Chain-scale enforcement applies only to the ``cbre_horizons`` tier
    (today). When a CBRE candidate carries a chain_scale that doesn't
    match the subject's, the resolver skips it and falls through to
    the next-lower tier.
    """
    for source in RATIO_PRECEDENCE:
        v = candidates.get(source)
        if v is None:
            continue
        if source == "cbre_horizons" and not _chain_scale_matches(
            v, subject_chain_scale
        ):
            logger.debug(
                "op_ratio_precedence: cbre_horizons candidate for %s skipped "
                "due to chain-scale mismatch (candidate=%r, subject=%r); "
                "falling through to next tier.",
                field_name,
                v.chain_scale,
                subject_chain_scale,
            )
            continue
        return v
    return None


def resolve_all(
    candidates_by_field: Mapping[str, Mapping[str, RatioValue | None]],
    *,
    subject_chain_scale: str | None = None,
) -> dict[str, RatioValue]:
    """Apply :func:`resolve_ratio` across every field in one pass."""
    out: dict[str, RatioValue] = {}
    for field_name, cands in candidates_by_field.items():
        winner = resolve_ratio(
            field_name, cands, subject_chain_scale=subject_chain_scale
        )
        if winner is not None:
            out[field_name] = winner
    return out


__all__ = [
    "RATIO_PRECEDENCE",
    "RatioValue",
    "resolve_all",
    "resolve_ratio",
]
