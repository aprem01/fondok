"""FON-75 — detect a persisted engine run that predates part of its model.

The defect this exists to name: on 2026-09-12 a real deal opened with the
whole Stabilization section of Overview rendering five dashes. Nothing was
broken. The deal's persisted ``engine_outputs`` row had simply been written
*before* ``expense.outputs.stabilization`` existed, and no surface said so.
A per-section banner shipped for that one block; this module is the general
mechanism, so the next block addition is covered the day it deploys rather
than being noticed one screen at a time.

The property that makes it self-maintaining
-------------------------------------------
``engine_runner._persist_complete`` serialises engine output with
``model_dump_json()`` and **without** ``exclude_none``. Pydantic therefore
writes *every* field of the output model, including the ones whose value is
``None``. So in a persisted ``engine_outputs.outputs`` blob:

* ``"stabilization": null`` — the engine ran *with* that field and honestly
  could not resolve one. A real, correct refusal. **Not stale.**
* the ``"stabilization"`` key **absent entirely** — this run predates the
  field. **Stale.**

That distinction is free and exact. It needs no version stamp, no migration
and no hand-maintained list of blocks: an engine's declared output model is
read straight off its ``BaseEngine[TInput, TOutput]`` binding, which every
engine already writes down, so a new engine (and every new field on an
existing one) is covered simply by existing.

Read-path only. Nothing here computes, writes or alters an engine output —
it compares a model's declared field names against the keys a stored JSON
document happens to carry.

Bias
----
When anything is unreadable — an unregistered engine, an unbound generic, a
field whose annotation cannot be resolved — this module reports **nothing**.
A detection bug must never invent a stale warning on a healthy deal: a false
positive would tell an analyst to re-run a model that is already current,
which is exactly the kind of noise that gets a banner ignored.
"""

from __future__ import annotations

import logging
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

from ..engines.base import BaseEngine

# Imported, never edited — ``engine_runner`` keeps exactly one owner.
from .engine_runner import ENGINE_REGISTRY

logger = logging.getLogger(__name__)

__all__ = ["missing_block_paths", "output_model_for", "stale_engines"]


def output_model_for(engine_name: str) -> type[BaseModel] | None:
    """The engine's declared output model, read off its generic binding.

    Every concrete engine writes ``class XEngine(BaseEngine[XInput, XOutput])``
    (``capital.py``, ``expense.py``, ``debt.py``, ``returns.py``, ``revenue.py``,
    ``fb_revenue.py``, ``partnership.py``, ``sensitivity.py``, ``cash_flow.py``),
    so ``__orig_bases__`` carries the pair and the second element is the output
    model. Returns ``None`` — never stale — for an unknown engine or an
    unreadable binding.
    """
    engine_cls = ENGINE_REGISTRY.get(engine_name)
    if engine_cls is None:
        return None
    try:
        for base in getattr(engine_cls, "__orig_bases__", ()):
            if get_origin(base) is not BaseEngine:
                continue
            args = get_args(base)
            if len(args) != 2:
                continue
            output = args[1]
            if isinstance(output, type) and issubclass(output, BaseModel):
                return output
    except Exception:  # a detection bug must never invent a warning
        logger.debug("run_freshness: unreadable binding for %s", engine_name)
        return None
    return None


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    """The BaseModel behind ``X``, ``X | None`` or ``Optional[X]``, else None.

    Deliberately narrow: a bare model field or a model made optional. Lists,
    dicts and tuples of models are NOT descended into — a persisted list
    writes every element's fields the same way, so the extra depth buys no
    detection while multiplying every reported path by the number of
    projection years.
    """
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if get_origin(annotation) in (Union, UnionType):
        models = [
            arg
            for arg in get_args(annotation)
            if isinstance(arg, type) and issubclass(arg, BaseModel)
        ]
        if len(models) == 1:
            return models[0]
    return None


def _key_in(model: type[BaseModel], name: str, blob: dict[str, Any]) -> str | None:
    """The key ``blob`` carries this field under — its name or an alias — else None.

    No engine output model uses an alias today (the "a fresh run is stale for
    no engine" test would go red the moment one did and this lookup were a
    plain ``name in blob``), but ``model_dump_json()`` would then write the
    alias while ``model_fields`` stays keyed by the field name — and the
    mismatch would be a FALSE POSITIVE, telling an analyst to re-run a
    perfectly current model. Cheap insurance against the one failure mode here
    that can actually do harm.
    """
    if name in blob:
        return name
    field = model.model_fields[name]
    for alias in (field.serialization_alias, field.alias):
        if alias and alias in blob:
            return alias
    return None


def missing_block_paths(
    engine_name: str, outputs: dict[str, Any] | None
) -> list[str]:
    """Dotted paths of model fields ABSENT as keys from ``outputs``.

    A field present with value ``None`` is **not** missing — the engine
    answered, and the answer was "no value". Only an absent key means the
    run predates the field.

    Recurses one level into nested models that are present and non-null, so
    a field added later to (say) ``StabilizedYear`` is reported as
    ``stabilization.<field>`` rather than silently passing because the outer
    ``stabilization`` key exists.

    Returns ``[]`` for an unknown engine, an unreadable binding, or outputs
    that are not a JSON object (a failed / skipped / never-run row).
    """
    model = output_model_for(engine_name)
    if model is None or not isinstance(outputs, dict):
        return []

    missing: list[str] = []
    try:
        for name, field in model.model_fields.items():
            key = _key_in(model, name, outputs)
            if key is None:
                missing.append(name)
                continue
            value = outputs[key]
            if value is None or not isinstance(value, dict):
                continue
            nested = _nested_model(field.annotation)
            if nested is None:
                continue
            for sub_name in nested.model_fields:
                if _key_in(nested, sub_name, value) is None:
                    missing.append(f"{name}.{sub_name}")
    except Exception:  # a detection bug must never invent a warning
        logger.debug("run_freshness: unreadable model for %s", engine_name)
        return []
    return missing


def stale_engines(rows: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """``{engine: [missing paths]}`` over one run-scoped snapshot; ``{}`` fresh.

    ``rows`` is the ``{engine: envelope}`` shape every deal-wide reader already
    produces (``engine_runner.get_run_scoped_outputs``). Only ``complete`` rows
    are considered: a failed, skipped or still-running engine has no outputs to
    compare, and its own banner already explains the dashes.
    """
    stale: dict[str, list[str]] = {}
    for name, row in (rows or {}).items():
        if not isinstance(row, dict) or row.get("status") != "complete":
            continue
        paths = missing_block_paths(name, row.get("outputs"))
        if paths:
            stale[name] = paths
    return stale
