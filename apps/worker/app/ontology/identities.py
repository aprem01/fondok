"""USALI identities evaluated over registry resolutions.

Each concept may declare an ``identity`` (``gop = total_revenue -
dept_expenses - undistributed_expenses``). This module resolves every term
through :func:`app.ontology.registry.resolve_many`, evaluates the
expression with the scorer's safe AST evaluator
(``usali_scorer._evaluate``) and reports the drift between the document's
stated value and the computed one. Nothing here changes engine math — it
is a read-only consistency check the next-phase adapters can build on.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .registry import Basis, Identity, Scope, identities, resolve_many


@dataclass(frozen=True)
class IdentityResult:
    concept: str
    expression: str
    stated: float | None
    computed: float | None
    drift: float | None
    ok: bool | None
    tolerance: float
    missing: tuple[str, ...]
    terms: dict[str, float | None]


def _evaluate_expression(expression: str, terms: Mapping[str, float]) -> float | None:
    """Evaluate ``expression`` over ``terms`` with the scorer's safe evaluator."""
    from ..services.usali_scorer import (
        _evaluate,
        _MissingFieldError,
        _UnsupportedFormulaError,
    )

    try:
        return float(_evaluate(ast.parse(expression, mode="eval"), dict(terms)))
    except (_MissingFieldError, _UnsupportedFormulaError, ZeroDivisionError, ValueError):
        return None


def evaluate_identity(
    fields: Any,
    identity: Identity,
    *,
    doc_type: str | None = None,
    want: Scope = "annual",
    basis: Basis | None = None,
    tenant_aliases: Mapping[str, Sequence[Any]] | None = None,
    allow_token_match: bool = False,
) -> IdentityResult:
    """Resolve the identity's terms and its own concept, then compare."""
    kw: dict[str, Any] = {
        "doc_type": doc_type, "want": want, "basis": basis,
        "tenant_aliases": tenant_aliases, "allow_token_match": allow_token_match,
    }
    resolved = resolve_many(fields, [identity.concept, *identity.terms], **kw)
    terms: dict[str, float | None] = {}
    missing: list[str] = []
    for t in identity.terms:
        v = resolved[t].value
        num = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        if num is None and t in identity.optional_terms:
            num = 0.0
        terms[t] = num
        if num is None:
            missing.append(t)
    stated_raw = resolved[identity.concept].value
    stated = (
        float(stated_raw)
        if isinstance(stated_raw, (int, float)) and not isinstance(stated_raw, bool)
        else None
    )
    if missing or stated is None:
        return IdentityResult(
            identity.concept, identity.expression, stated, None, None, None,
            identity.tolerance, tuple(missing), terms,
        )
    computed = _evaluate_expression(identity.expression, {k: v for k, v in terms.items() if v is not None})
    if computed is None or not math.isfinite(computed):
        return IdentityResult(
            identity.concept, identity.expression, stated, None, None, None,
            identity.tolerance, (), terms,
        )
    if stated == 0.0:
        drift = 0.0 if computed == 0.0 else math.inf
    else:
        drift = abs(stated - computed) / abs(stated)
    return IdentityResult(
        identity.concept, identity.expression, stated, computed, drift,
        drift <= identity.tolerance + 1e-12, identity.tolerance, (), terms,
    )


def evaluate_identities(fields: Any, **kw: Any) -> list[IdentityResult]:
    """Evaluate every registry identity on one payload (see :func:`evaluate_identity`)."""
    return [evaluate_identity(fields, ident, **kw) for ident in identities()]


__all__ = ["IdentityResult", "evaluate_identities", "evaluate_identity"]
