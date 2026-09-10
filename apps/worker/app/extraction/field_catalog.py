"""Field catalog — registry-derived alias maps for the underwriting engines.

**Phase 1.3a.** The alias→canonical mapping is no longer defined here: it is
DERIVED from the concept registry (``app/ontology/concepts.yaml``, loaded by
``app.ontology.registry``). A concept joins an engine namespace by carrying a
``bindings.field_catalog: {namespace, key, percentage_key}`` binding; its
``aliases`` entries under the namespace's document keys are the paths the
engines match. ``PERIOD_TYPE_RANK`` is ``registry.period_types`` verbatim and
``OM_PERCENTAGE_KEYS`` is every OM-namespace binding flagged ``percentage_key``.

``field_catalog.yaml`` is **superseded** and no longer defines any mapping. It
survives one more phase in a single, deliberately narrow role: the *scope gate*
(``_load_scope``). The registry knows strictly MORE aliases per canonical key
than the YAML ever listed (e.g. ``occupancy`` has 3 catalog aliases and 17
registry aliases — the un-nested USALI form, ``occ``, ``occ_pct``, the broker
and OM-summary forms …). Phase 1.3a is a parity phase: no engine may start
matching a path it did not match before, so the YAML's path list narrows the
registry's alias lists to the frozen Phase-1.3a set. Widening the engines to
the full registry vocabulary
is a real underwriting-behaviour change and belongs to its own change set, with
its own golden diff — at which point ``_load_scope``, ``_CATALOG_PATH`` and
``field_catalog.yaml`` all delete together. Until then the loader ASSERTS at
import that every scoped path is a registry alias of the bound concept, so the
YAML can never again drift away from the registry (see
``tests/test_ontology_conformance.py::test_field_catalog_parity``).

Exposed module-level constants (consumed by engine_runner):
    * T12_EXPENSE_FIELD_ALIASES
    * T12_REVENUE_FIELD_ALIASES
    * OM_CAPITAL_FIELD_ALIASES
    * OM_DEBT_FIELD_ALIASES
    * PERIOD_TYPE_RANK
    * OM_PERCENTAGE_KEYS
    * ALIAS_LISTS_BY_NAMESPACE  (Phase 1.3a; the ordered ``{namespace: {key:
      [alias, ...]}}`` the four dicts are inverted from — the shape the parity
      test pins)

All four alias dicts are immutable once loaded — re-loading requires
a worker restart. That keeps the engine input shape predictable
within a single request lifecycle.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from ..ontology.registry import get_registry

logger = logging.getLogger(__name__)

_CATALOG_PATH = Path(__file__).parent / "field_catalog.yaml"

#: Engine namespace → the concept alias-map keys whose paths that namespace's
#: documents can carry. ``engine_runner`` feeds ``t12_*`` from the P&L family
#: only (``doc_type IN ('T12','PNL','PNL_MONTHLY','PNL_YTD')``) and ``om_*``
#: from OMs only, so the doc-key selection mirrors the queries exactly.
#: ``"*"`` holds the aliases that are valid on any document.
_NAMESPACE_ALIAS_KEYS: dict[str, tuple[str, ...]] = {
    "t12_expense": ("PNL_FAMILY", "*"),
    "t12_revenue": ("PNL_FAMILY", "*"),
    "om_capital": ("OM", "*"),
    "om_debt": ("OM", "*"),
}

#: Namespaces whose canonical keys the OM percentage normalization applies to.
_OM_NAMESPACES: frozenset[str] = frozenset({"om_capital", "om_debt"})


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


def _registry_alias_lists() -> dict[str, dict[str, list[str]]]:
    """``{namespace: {canonical: [alias path, ...]}}`` straight off the registry.

    Alias order is registry order (the document-key order in
    ``_NAMESPACE_ALIAS_KEYS``, then declaration order within each key) — the
    same precedence the resolver applies. Pattern aliases (those carrying a
    ``{year}`` / ``{n}`` placeholder) are skipped: the engines do a flat
    ``dict.get``, not a regex match.
    """
    out: dict[str, dict[str, list[str]]] = {ns: {} for ns in _NAMESPACE_ALIAS_KEYS}
    for concept in get_registry().concepts.values():
        binding = concept.bindings.field_catalog
        if binding is None:
            continue
        doc_keys = _NAMESPACE_ALIAS_KEYS.get(binding.namespace)
        if doc_keys is None:
            raise ValueError(
                f"field_catalog: concept '{concept.id}' binds to unknown "
                f"namespace '{binding.namespace}' — add it to "
                f"_NAMESPACE_ALIAS_KEYS (and to engine_runner) first."
            )
        paths: list[str] = []
        for doc_key in doc_keys:
            for alias in concept.aliases.get(doc_key, []):
                path = alias.path.strip().lower()
                if not path or "{" in path or path in paths:
                    continue
                paths.append(path)
        if binding.key in out[binding.namespace]:
            raise ValueError(
                f"field_catalog: canonical key "
                f"'{binding.namespace}.{binding.key}' is claimed by two "
                f"concepts (one of them '{concept.id}')."
            )
        out[binding.namespace][binding.key] = paths
    return out


def _load_scope() -> dict[str, dict[str, list[str]]]:
    """Phase-1.3a scope gate — see the module docstring.

    Reads ``field_catalog.yaml`` for its alias PATHS only. The canonical key an
    alias maps to comes from the registry binding, never from this file; the
    YAML merely says which of the registry's paths the engines are allowed to
    match in this phase. Deletes with the YAML next phase.
    """
    if not _CATALOG_PATH.exists():
        raise FileNotFoundError(
            f"field_catalog.yaml not found at {_CATALOG_PATH} — it is still "
            "the Phase-1.3a scope gate for the registry-derived alias maps. "
            "Check the Docker image's COPY directives."
        )
    with _CATALOG_PATH.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"field_catalog: expected a mapping at the top level; "
            f"got {type(raw).__name__}"
        )
    scope: dict[str, dict[str, list[str]]] = {}
    for namespace in _NAMESPACE_ALIAS_KEYS:
        block = raw.get(namespace, {})
        if not isinstance(block, dict):
            raise ValueError(
                f"field_catalog: namespace '{namespace}' must be a mapping; "
                f"got {type(block).__name__}"
            )
        scope[namespace] = {
            str(key): [str(a).strip().lower() for a in (aliases or []) if str(a).strip()]
            for key, aliases in block.items()
        }
    return scope


def _gate(
    registry_lists: dict[str, dict[str, list[str]]],
    scope: dict[str, dict[str, list[str]]],
) -> dict[str, dict[str, list[str]]]:
    """Narrow the registry alias lists to the Phase-1.3a scope, in registry order.

    Every scoped path must be a registry alias of the concept bound to that
    ``(namespace, canonical)`` — anything else is drift between the YAML and
    the registry and raises at import rather than silently dropping an alias
    the engines rely on.
    """
    gated: dict[str, dict[str, list[str]]] = {}
    for namespace, keys in scope.items():
        available = registry_lists[namespace]
        out: dict[str, list[str]] = {}
        for canonical, paths in keys.items():
            known = available.get(canonical)
            if known is None:
                raise ValueError(
                    f"field_catalog: '{namespace}.{canonical}' is in "
                    f"field_catalog.yaml but no concept binds to it — the "
                    f"registry is the source of truth; add the "
                    f"bindings.field_catalog entry to concepts.yaml."
                )
            unknown = [p for p in paths if p not in known]
            if unknown:
                raise ValueError(
                    f"field_catalog: {namespace}.{canonical} scopes "
                    f"{unknown!r}, which the registry does not list as an "
                    f"alias of the bound concept. Add the alias to "
                    f"concepts.yaml (registry first), then re-scope."
                )
            allowed = set(paths)
            out[canonical] = [p for p in known if p in allowed]
        gated[namespace] = out
    return gated


_REGISTRY_ALIAS_LISTS: dict[str, dict[str, list[str]]] = _registry_alias_lists()

#: The ordered ``{namespace: {canonical: [alias, ...]}}`` the four flat dicts
#: below are inverted from. Public so the parity test can pin alias ORDER, not
#: just the flattened mapping.
ALIAS_LISTS_BY_NAMESPACE: dict[str, dict[str, list[str]]] = _gate(
    _REGISTRY_ALIAS_LISTS, _load_scope()
)

T12_EXPENSE_FIELD_ALIASES: dict[str, str] = _invert_aliases(
    ALIAS_LISTS_BY_NAMESPACE["t12_expense"]
)
T12_REVENUE_FIELD_ALIASES: dict[str, str] = _invert_aliases(
    ALIAS_LISTS_BY_NAMESPACE["t12_revenue"]
)
OM_CAPITAL_FIELD_ALIASES: dict[str, str] = _invert_aliases(
    ALIAS_LISTS_BY_NAMESPACE["om_capital"]
)
OM_DEBT_FIELD_ALIASES: dict[str, str] = _invert_aliases(
    ALIAS_LISTS_BY_NAMESPACE["om_debt"]
)

# P&L row-ranking preference by period_type (lower rank = preferred). Annual
# T-12s outrank YTD / monthly so a 5-month YTD upload can't clobber the
# full-year baseline. (Eshan's QA: ~89% YTD occupancy was beating ~81% annual.)
PERIOD_TYPE_RANK: dict[str, int] = {
    str(k).strip().lower(): int(v) for k, v in get_registry().period_types.items()
}

# Percentage-style keys (0..100 → 0..1 normalization). Extractors emit either a
# 0..1 ratio or a 0..100 percent for these; engine_runner divides by 100 when
# the value is > 1.0 so the engine always sees a 0..1 fraction. The registry
# unit for all three is ``ratio`` — ``percentage_key`` is the binding that says
# "the wire format may be a percent", which is the distinction that matters here.
OM_PERCENTAGE_KEYS: frozenset[str] = frozenset(
    c.bindings.field_catalog.key
    for c in get_registry().concepts.values()
    if c.bindings.field_catalog is not None
    and c.bindings.field_catalog.percentage_key
    and c.bindings.field_catalog.namespace in _OM_NAMESPACES
)

logger.info(
    "field_catalog loaded from registry v%d: t12_expense=%d t12_revenue=%d "
    "om_capital=%d om_debt=%d period_ranks=%d percentage_keys=%d",
    get_registry().version,
    len(T12_EXPENSE_FIELD_ALIASES),
    len(T12_REVENUE_FIELD_ALIASES),
    len(OM_CAPITAL_FIELD_ALIASES),
    len(OM_DEBT_FIELD_ALIASES),
    len(PERIOD_TYPE_RANK),
    len(OM_PERCENTAGE_KEYS),
)


__all__ = [
    "ALIAS_LISTS_BY_NAMESPACE",
    "T12_EXPENSE_FIELD_ALIASES",
    "T12_REVENUE_FIELD_ALIASES",
    "OM_CAPITAL_FIELD_ALIASES",
    "OM_DEBT_FIELD_ALIASES",
    "PERIOD_TYPE_RANK",
    "OM_PERCENTAGE_KEYS",
]
