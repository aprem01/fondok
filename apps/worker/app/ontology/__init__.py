"""Fondok concept registry (Phase 1.1).

One vocabulary for every P&L / KPI / OM / market concept the platform
names. The registry (``concepts.yaml``) is the source of truth; the nine
hand-maintained alias maps it absorbs are listed in ``DRIFT_NOTES.md``
together with every place they disagreed. Adapters that wire the legacy
consumers onto the registry land in the next phase — this package only
loads, validates and resolves.

Public surface: :mod:`app.ontology.registry` and :mod:`app.ontology.identities`.
"""

from .registry import (
    Basis,
    Identity,
    Registry,
    Resolution,
    Scope,
    concept_for_path,
    get_registry,
    identities,
    load_registry,
    registry_version,
    resolve,
    resolve_many,
)

__all__ = [
    "Basis",
    "Identity",
    "Registry",
    "Resolution",
    "Scope",
    "concept_for_path",
    "get_registry",
    "identities",
    "load_registry",
    "registry_version",
    "resolve",
    "resolve_many",
]
