"""Field catalog loader — reads field_catalog.yaml at module import.

The catalog externalizes the alias maps that previously lived as
hardcoded Python dicts in services/engine_runner.py. Adding a new
extracted-field alias is now a YAML edit (no code change, no deploy
required if hot-reloaded — though today we still need a worker
restart since the loader runs at import time).

Each alias-namespace in the YAML maps a canonical key (the engine's
internal name) to a list of acceptable Extractor-emitted paths. The
loader inverts that to a flat alias→canonical dict so the existing
lookup pattern in engine_runner stays a single dict.get() call.

Exposed module-level constants (consumed by engine_runner):
    * T12_EXPENSE_FIELD_ALIASES
    * T12_REVENUE_FIELD_ALIASES
    * OM_CAPITAL_FIELD_ALIASES
    * OM_DEBT_FIELD_ALIASES
    * PERIOD_TYPE_RANK
    * OM_PERCENTAGE_KEYS

All four alias dicts are immutable once loaded — re-loading requires
a worker restart. That keeps the engine input shape predictable
within a single request lifecycle.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_CATALOG_PATH = Path(__file__).parent / "field_catalog.yaml"


def _invert_aliases(spec: dict[str, list[str]]) -> dict[str, str]:
    """Turn {canonical: [aliases]} into {alias: canonical}.

    Same canonical can have many aliases; same alias must NOT point to
    two different canonicals — a misconfigured catalog would silently
    bias whichever entry was iterated last. We log + raise on conflict
    so the misconfig surfaces at boot rather than mid-extraction.
    """
    out: dict[str, str] = {}
    for canonical, aliases in spec.items():
        if not isinstance(aliases, list):
            raise ValueError(
                f"field_catalog: aliases for '{canonical}' must be a list; "
                f"got {type(aliases).__name__}"
            )
        for alias in aliases:
            alias_lc = str(alias).strip().lower()
            if not alias_lc:
                continue
            prior = out.get(alias_lc)
            if prior is not None and prior != canonical:
                raise ValueError(
                    f"field_catalog: alias '{alias_lc}' maps to both "
                    f"'{prior}' and '{canonical}' — every alias must "
                    f"resolve to exactly one canonical key."
                )
            out[alias_lc] = canonical
    return out


def _load_catalog() -> dict[str, dict | list]:
    if not _CATALOG_PATH.exists():
        raise FileNotFoundError(
            f"field_catalog.yaml not found at {_CATALOG_PATH} — "
            "the engine_runner alias maps depend on it. Check the "
            "Docker image's COPY directives."
        )
    with _CATALOG_PATH.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"field_catalog: expected a mapping at the top level; "
            f"got {type(raw).__name__}"
        )
    return raw


_CATALOG = _load_catalog()

T12_EXPENSE_FIELD_ALIASES: dict[str, str] = _invert_aliases(
    _CATALOG.get("t12_expense", {})
)
T12_REVENUE_FIELD_ALIASES: dict[str, str] = _invert_aliases(
    _CATALOG.get("t12_revenue", {})
)
OM_CAPITAL_FIELD_ALIASES: dict[str, str] = _invert_aliases(
    _CATALOG.get("om_capital", {})
)
OM_DEBT_FIELD_ALIASES: dict[str, str] = _invert_aliases(
    _CATALOG.get("om_debt", {})
)

_period_rank = _CATALOG.get("period_type_rank", {})
PERIOD_TYPE_RANK: dict[str, int] = {
    str(k).strip().lower(): int(v)
    for k, v in (_period_rank.items() if isinstance(_period_rank, dict) else [])
}

_pct_keys = _CATALOG.get("percentage_keys", [])
OM_PERCENTAGE_KEYS: frozenset[str] = frozenset(
    str(k).strip().lower() for k in (_pct_keys if isinstance(_pct_keys, list) else [])
)

logger.info(
    "field_catalog loaded: t12_expense=%d t12_revenue=%d om_capital=%d "
    "om_debt=%d period_ranks=%d percentage_keys=%d",
    len(T12_EXPENSE_FIELD_ALIASES),
    len(T12_REVENUE_FIELD_ALIASES),
    len(OM_CAPITAL_FIELD_ALIASES),
    len(OM_DEBT_FIELD_ALIASES),
    len(PERIOD_TYPE_RANK),
    len(OM_PERCENTAGE_KEYS),
)


__all__ = [
    "T12_EXPENSE_FIELD_ALIASES",
    "T12_REVENUE_FIELD_ALIASES",
    "OM_CAPITAL_FIELD_ALIASES",
    "OM_DEBT_FIELD_ALIASES",
    "PERIOD_TYPE_RANK",
    "OM_PERCENTAGE_KEYS",
]
