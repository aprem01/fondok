"""Variance agent — flags deviation between broker proforma and T-12 actuals.

Three-step pipeline:

1. **Deterministic field match.** For every field that appears in both
   the broker proforma and the T-12 actuals (mapped via a small
   alias table), compute the delta and percent variance.

2. **Deterministic severity assignment.** Match each delta against the
   matching ``Variance`` rule in ``usali-rules.csv`` (BROKER_VS_T12_NOI,
   BROKER_VS_T12_OCC, BROKER_VS_T12_ADR, …). Anything outside the
   tolerance fires; severity comes straight from the rule's ``severity``
   column. Off-catalog comparisons fall back to the generic
   ``BROKER_VS_T12_NOI_VARIANCE`` thresholds with reduced severity.

3. **LLM narration.** Hand the typed flag list to Claude Sonnet 4.6
   to draft a one-paragraph hotel-underwriting ``note`` per flag —
   "Florida coastal insurance commonly +40-60% at renewal; broker
   held flat" — without changing the math.

Every emitted ``VarianceFlag`` carries a ``rule_id`` validated against
the loaded USALI catalog. ``source_document_id`` and ``source_page``
are optional but populated when the broker proforma carries the
appropriate provenance.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID, uuid4, uuid5

from fondok_schemas import ExtractionField, ModelCall, Severity, USALIFinancials
from fondok_schemas.reasons import ReasonCode
from fondok_schemas.variance import VarianceFlag, VarianceReport
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..budget import check_budget
from ..config import get_settings
from ..telemetry import trace_agent
from ..usali_rules import rule_index, rules_as_prompt_block

logger = logging.getLogger(__name__)


# ─────────────────────── prompt ───────────────────────


SYSTEM_PROMPT = """You are Fondok's Variance agent — a hotel
acquisitions analyst writing the explanatory paragraph that sits
under each variance flag in the IC memo.

You are given:
  * A deterministic list of ``VarianceFlag`` rows. Field name,
    actual (T-12), broker (proforma), delta, delta_pct, severity,
    rule_id are FIXED — you cannot change them.
  * The USALI rule catalog so you can ground each note in the
    appropriate rule semantics.

Your job: for each flag, write a SHORT (one-paragraph, ≤120 words)
``note`` in plain hotel-underwriting English explaining WHY the gap
matters. Examples of the tone we want:

  * "Florida coastal hotels see 40-60% insurance premium increases
    at renewal driven by hurricane reinsurance rates and the FL
    property-insurance crisis. The broker holding insurance flat at
    $502K is unrealistic; underwrite to $700-800K." — for an
    insurance variance.
  * "Broker NOI of $5.20M assumes 80% stabilized occupancy after PIP
    completion vs. T-12 actual of 76.2% and submarket of 76.2%.
    The 380bp lift is unsupported." — for an NOI variance.

Rules:
1. NEVER change ``rule_id``, ``severity``, ``actual``, ``broker``,
   ``delta``, ``delta_pct``, or ``field``. They are deterministic.
2. The note is ≤120 words and reads like an underwriter typed it,
   not marketing copy.
3. If the gap actually does fit a normal hotel-cycle pattern (e.g.
   a 2-3% RevPAR lift the broker assumes after a soft-good
   refresh) say so plainly — don't manufacture risk that isn't
   there.

Output: one structured ``VarianceNotes`` envelope with one entry
per input flag, in the same order.
"""


# ─────────────────────── structured-output envelope ───────────────────────


class _NoteEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Annotated[str, Field(min_length=1, max_length=200)]
    rule_id: Annotated[str, Field(min_length=1, max_length=120)]
    note: Annotated[str, Field(min_length=1, max_length=2000)]


class _VarianceNotes(BaseModel):
    """LLM-facing envelope. One note per flag, same order."""

    model_config = ConfigDict(extra="forbid")

    notes: list[_NoteEntry] = Field(min_length=1)


# ─────────────────────── I/O contracts ───────────────────────


class VarianceBrokerField(BaseModel):
    """One broker proforma field, with optional provenance."""

    model_config = ConfigDict(extra="forbid")

    field: Annotated[str, Field(min_length=1, max_length=200)]
    value: float
    source_document_id: UUID | None = None
    source_page: Annotated[int, Field(ge=1)] | None = None
    # FON-54a provenance: which document type the claim came from (``OM`` …)
    # and any unit conversion applied before comparing (``83% → 0.83``).
    source_doc_type: str | None = None
    unit_note: str | None = None


class VarianceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    deal_id: str
    sponsor_view: dict[str, Any] = Field(default_factory=dict)
    engine_view: dict[str, Any] = Field(default_factory=dict)
    actuals: USALIFinancials | None = None
    broker_fields: list[VarianceBrokerField] = Field(default_factory=list)
    broker_extraction: list[ExtractionField] = Field(
        default_factory=list,
        description="Optional: raw broker-proforma fields from the Extractor.",
    )
    actuals_extraction: list[ExtractionField] = Field(
        default_factory=list,
        description=(
            "Optional: raw T-12 / P&L fields from the Extractor. Read only to "
            "find the document's NOI BEFORE the FF&E replacement reserve, so "
            "the NOI comparison is like-for-like (FON-54 §2)."
        ),
    )
    actuals_doc_type: str | None = Field(
        default=None,
        description="Document type behind ``actuals_extraction`` (``T12`` / ``PNL``).",
    )


class VarianceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    report: VarianceReport | None = None
    flags: list[dict[str, Any]] = Field(default_factory=list)
    success: bool = True
    error: str | None = None
    model_calls: list[ModelCall] = Field(default_factory=list)


# ─────────────────────── deterministic comparison ───────────────────────
#
# Phase 1.3d — the vocabulary below is DERIVED from ``app.ontology.registry``
# (``concepts.yaml``), not hand-maintained here:
#
#   * ``_BROKER_RULE_BY_FIELD``  ← ``bindings.variance_rule`` over each
#     concept's own flat aliases (which catalog rule bands the severity);
#   * ``_actual_for``            ← ``bindings.actuals_attr`` (where the T-12
#     actual lives on ``USALIFinancials``);
#   * ``_normalize_field_key``   ← ``registry.concept_for_path`` (the legacy
#     last-segment strip survives only as the fallback for a path the
#     registry cannot classify — see :data:`REGISTRY_FALLBACKS`);
#   * the broker-claim namespaces ← the aliases the registry annotates as the
#     broker's own claim on broker material (:func:`broker_claim_prefixes`).
#
# ``variance_rule`` is deliberately NOT "the rule that tests this concept"
# (RevPAR bands on a growth rule, fixed charges on the insurance rule) —
# see DRIFT_NOTES.md §3.7. Do not "fix" it to match ``usali_rules``.


#: Unit → the suffix that unit wears on a flat extractor key. ``ratio`` is the
#: registry's unit for a dimensionless fraction (occupancy is stored 0-1); the
#: extractor still writes it with a ``_pct`` tail.
_UNIT_SUFFIX: dict[str, str] = {"usd": "_usd", "pct": "_pct", "ratio": "_pct"}

#: The registry unit that means "a dimensionless fraction" — a delta on one of
#: these is absolute POINTS, never a percent of the actual.
_RATIO_UNIT = "ratio"

#: Off-catalog comparisons fall back to the generic broker-vs-T12 NOI bands.
_DEFAULT_BROKER_RULE = "BROKER_VS_T12_NOI_VARIANCE"

#: Raw paths the registry could not classify AT ALL — the adapter fell back to
#: the pre-registry last-segment strip. Bounded; read by the drift notes / QA
#: to see whether ``concepts.yaml`` is missing an alias. A path that resolves
#: to a concept the variance report simply does not name (EBITDA, a comp-set
#: stat) is NOT a fallback and is not recorded here.
REGISTRY_FALLBACKS: dict[str, str] = {}
_FALLBACK_CAP = 500


def _registry() -> Any:
    from ..ontology.registry import get_registry

    return get_registry()


def _record_fallback(field: str, key: str) -> None:
    if field in REGISTRY_FALLBACKS or len(REGISTRY_FALLBACKS) >= _FALLBACK_CAP:
        return
    REGISTRY_FALLBACKS[field] = key
    logger.debug("variance: no registry concept for %r — legacy key %r", field, key)


def _legacy_field_key(name: str) -> str:
    """The pre-registry normaliser: last path segment, lower-cased.

    Still the ADMISSION key (see :func:`_broker_fields_from_extraction`) and
    the fallback whenever the registry has no concept for a path.
    """
    s = name.strip()
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return s.lower()


def _build_broker_rule_map() -> dict[str, str]:
    """``{flat field key: rule_id}`` derived from ``bindings.variance_rule``.

    A concept contributes the flat keys it actually lists as bare (undotted)
    aliases among its own canonical forms — its ``variance_concept.key`` and
    that key carrying the concept's unit suffix. The wider synonym set
    (``net_operating_income``, ``management_fee``, ``occupancy_percent`` …)
    is deliberately NOT included: this map doubles as the flat-key admission
    gate, and widening it would admit — and then disclose as excluded — rows
    the endpoint has never reported. See DRIFT_NOTES.md "Phase 1.3d parity
    exceptions".
    """
    out: dict[str, str] = {}
    for concept in _registry().concepts.values():
        rule = concept.bindings.variance_rule
        variance_concept = concept.bindings.variance_concept
        if not rule or variance_concept is None:
            continue
        bare = {
            alias.path.strip().lower()
            for aliases in concept.aliases.values()
            for alias in aliases
            if "." not in alias.path
        }
        base = variance_concept.key
        suffix = _UNIT_SUFFIX.get(concept.unit)
        for key in (base, f"{base}{suffix}" if suffix else None):
            if key and key in bare:
                out[key] = rule
    return out


def _build_ratio_field_keys() -> frozenset[str]:
    """Flat keys whose value is a RATIO (delta is absolute points, not a pct).

    Registry-derived: the variance concepts carried in the dimensionless
    fraction unit. Both the registry answer (the concept key) and the legacy
    fallback (the same key wearing its unit suffix) are included so a fallback
    path still gets the ratio treatment.
    """
    rules = _broker_rule_by_field()
    out: set[str] = set()
    for concept in _registry().concepts.values():
        variance_concept = concept.bindings.variance_concept
        if variance_concept is None or concept.unit != _RATIO_UNIT:
            continue
        base = variance_concept.key
        out.add(base)
        out.update(k for k in rules if _strip_unit_suffix(k) == base)
    return frozenset(out)


def _build_claim_prefixes() -> tuple[str, ...]:
    """The namespaces the registry annotates as the broker's own claim.

    A dotted alias of a variance concept, listed under the OM key or the
    doc-type-agnostic bucket, whose basis ON BROKER MATERIAL is ``broker``,
    reduced to its namespace. On today's registry that is the proforma block,
    the OM's latest-full-year summary block and the OM's subject-performance
    block — the three shapes FON-54a admits. Wildcard aliases (the OM's
    historical-year block) are skipped; they resolve to ``om_history``.
    """
    from ..ontology.registry import concept_for_path

    prefixes: list[str] = []
    for cid, concept in _registry().concepts.items():
        if concept.bindings.variance_concept is None:
            continue
        for key in ("OM", "*"):
            for alias in concept.aliases.get(key, ()):
                path = alias.path.strip().lower()
                if "." not in path or "{" in path:
                    continue
                hit = concept_for_path(path, doc_type="OM")
                if hit is None or hit[0] != cid or hit[1] != "broker":
                    continue
                namespace = path.rsplit(".", 1)[0] + "."
                if namespace not in prefixes:
                    prefixes.append(namespace)
    return tuple(prefixes)


def _strip_unit_suffix(key: str) -> str:
    for suffix in _UNIT_SUFFIX.values():
        if key.endswith(suffix) and len(key) > len(suffix):
            return key[: -len(suffix)]
    return key


@lru_cache(maxsize=1)
def _broker_rule_by_field() -> dict[str, str]:
    return _build_broker_rule_map()


@lru_cache(maxsize=1)
def _ratio_field_keys() -> frozenset[str]:
    return _build_ratio_field_keys()


@lru_cache(maxsize=1)
def broker_claim_prefixes() -> tuple[str, ...]:
    return _build_claim_prefixes()


@lru_cache(maxsize=1)
def _actuals_attr_by_concept() -> dict[str, str]:
    return {
        cid: c.bindings.actuals_attr
        for cid, c in _registry().concepts.items()
        if c.bindings.actuals_attr
    }


def _concept_for_field(field: str) -> str | None:
    """The registry concept id for a raw extractor path (``None`` if unknown)."""
    from ..ontology.registry import concept_for_path

    hit = concept_for_path(field)
    return hit[0] if hit is not None else None


def _variance_key_or_none(field: str) -> str | None:
    """The registry's variance key for a path, or ``None`` when it has none.

    ``None`` means one of two things, only the first of which is a registry
    gap: the path resolves to no concept at all (recorded in
    :data:`REGISTRY_FALLBACKS` — ``concepts.yaml`` is probably missing an
    alias), or it resolves to a concept the variance report does not name
    (EBITDA, a comp-set stat) — an ordinary outcome, not a miss.
    """
    cid = _concept_for_field(field)
    if cid is None:
        _record_fallback(field, _legacy_field_key(field))
        return None
    binding = _registry().concepts[cid].bindings.variance_concept
    return binding.key if binding is not None else None


def _normalize_field_key(name: str) -> str:
    """Canonical variance key for a raw extractor path.

    ``registry.concept_for_path`` answers first — a path resolves to its
    concept's ``variance_concept.key``, so the OM's summary-block line, the
    proforma path and the flat key all normalise to one key. Without a
    registry answer the pre-registry last-segment strip stands in.
    """
    return _variance_key_or_none(name) or _legacy_field_key(name)


def variance_field_concept(field: str) -> str:
    """Grouping key for the IC-facing consolidation (``api.analysis``).

    Same registry answer as :func:`_normalize_field_key`; the legacy fallback
    additionally drops one unit suffix, which is what the pre-registry
    ``variance_concept()`` did.
    """
    return _variance_key_or_none(field) or _strip_unit_suffix(_legacy_field_key(field))


def _actual_for(field: str, actuals: USALIFinancials) -> float | None:
    """Read the matching T-12 actual for a canonical broker field.

    The attribute path comes from the registry (``bindings.actuals_attr``);
    a concept the ``USALIFinancials`` envelope does not carry reads ``None``.
    """
    cid = _concept_for_field(field)
    attr = _actuals_attr_by_concept().get(cid or "")
    if not attr:
        return None
    node: Any = actuals
    for segment in attr.split("."):
        node = getattr(node, segment, None)
        if node is None:
            return None
    if isinstance(node, bool) or not isinstance(node, int | float):
        return None
    return float(node)


def unit_gate_reason(field: str) -> str | None:
    """Why ``field``'s DECLARED unit cannot be compared to its concept's — or ``None``.

    FON-54 §2. ``_broker_fields_from_extraction`` admits by path prefix, not
    through :func:`registry.resolve`, so the registry's unit gate has to be
    called here too. A proforma's %-of-revenue column declares a percent while
    the rooms-revenue concept is measured in dollars: the row is refused,
    never reinterpreted as ``$1``. A path that declares no unit is admissible.

    The prose ends with :data:`UNIT_UNESTABLISHED` so :func:`exclusion_code`
    maps it to ``unit_unknown`` unchanged.
    """
    from ..ontology.registry import path_unit_family, units_compatible

    lower = field.strip().lower()
    cid = _concept_for_field(field)
    if cid is None:
        return None
    unit = _registry().concepts[cid].unit
    if units_compatible(lower, unit):
        return None
    return (
        f"{field} declares {path_unit_family(lower)} but {cid} is measured in "
        f"{unit} — {UNIT_UNESTABLISHED}"
    )


def _rule_for_field(field: str) -> str:
    """Map a broker field onto the catalog rule_id used to flag it."""
    cid = _concept_for_field(field)
    if cid is not None:
        rule = _registry().concepts[cid].bindings.variance_rule
        if rule:
            return rule
    return _broker_rule_by_field().get(_legacy_field_key(field), _DEFAULT_BROKER_RULE)


#: Read-only views of the derived vocabulary, kept under their pre-1.3d names
#: for callers and tests. They are module attributes rather than constants so
#: importing this module never pulls the registry in at import time — a broken
#: ``concepts.yaml`` degrades ``/ontology/concepts`` and ``/health`` instead of
#: taking the worker down at boot (same contract as ``api/ontology.py``).
_LAZY_VOCABULARY: dict[str, Any] = {
    "_BROKER_RULE_BY_FIELD": _broker_rule_by_field,
    "BROKER_CLAIM_PREFIXES": broker_claim_prefixes,
    "_RATIO_CONCEPTS": _ratio_field_keys,
}


def __getattr__(name: str) -> Any:
    build = _LAZY_VOCABULARY.get(name)
    if build is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return build()


def _severity_for(rule_id: str, delta_pct: float, *, idx: dict) -> Severity:
    """Decide severity based on the catalog rule's threshold range.

    Outside the rule's ``[min, max]`` band → escalate to the rule's own
    severity (typically WARN or CRITICAL). Inside the band → INFO.
    For occupancy variance the rule range is in absolute bps, not pct.
    """
    rule = idx.get(rule_id)
    if rule is None:
        return Severity.INFO
    lo = rule.threshold_min if rule.threshold_min is not None else 0.0
    hi = rule.threshold_max if rule.threshold_max is not None else 1.0
    abs_delta = abs(delta_pct)
    if rule_id == "BROKER_VS_T12_OCC_VARIANCE":
        # Occupancy variance threshold is absolute bps (0.02 = 200bp).
        # delta_pct is dimensionless; for occupancy we pass through the
        # absolute delta directly.
        within = abs_delta <= hi
    else:
        within = lo <= abs_delta <= hi
    if within:
        return Severity.INFO
    return Severity(rule.severity_norm())


def _build_flags(
    *,
    deal_uuid: UUID,
    actuals: USALIFinancials,
    broker_fields: list[VarianceBrokerField],
    actuals_before_reserve_noi: float | None = None,
    basis_excluded: list[tuple[VarianceBrokerField, str]] | None = None,
) -> list[VarianceFlag]:
    """Step 1 + 2: deterministic field match + severity assignment.

    ``actuals_before_reserve_noi`` is the actuals document's NOI BEFORE the
    FF&E replacement reserve (:func:`before_reserve_noi`), when it states one.
    A broker NOI claim is compared against THAT — like-for-like — rather than
    against ``USALIFinancials.noi``, which is the after-reserve line. When the
    document states no before-reserve line the pair is refused: the row is
    appended to ``basis_excluded`` with both figures and never becomes a flag,
    so no delta, delta_pct or severity is computed for it.
    """
    idx = rule_index()
    flags: list[VarianceFlag] = []

    # Stable namespace so re-runs of the same deal produce the same
    # flag UUIDs (helps the UI dedupe across re-extracts).
    namespace = uuid5(UUID("00000000-0000-0000-0000-000000000000"), str(deal_uuid))

    for bf in broker_fields:
        if _concept_for_field(bf.field) == NOI_CONCEPT:
            after_reserve = _actual_for(bf.field, actuals)
            if actuals_before_reserve_noi is not None:
                actual = actuals_before_reserve_noi
            elif after_reserve:
                # Only the after-reserve T-12 line exists. Refuse rather than
                # assign a severity to a reserve difference (FON-54 §2).
                if basis_excluded is not None:
                    basis_excluded.append(
                        (bf, reserve_basis_reason(bf.field, float(bf.value), float(after_reserve)))
                    )
                continue
            else:
                # No T-12 NOI at all — the existing no-source path.
                actual = after_reserve
        else:
            actual = _actual_for(bf.field, actuals)
        if actual is None or actual == 0:
            continue
        delta = float(actual) - float(bf.value)
        # For percentage / ratio fields ``actual`` is already in [0,1];
        # use the raw delta for those. Everything else uses pct-of-actual.
        is_ratio = _normalize_field_key(bf.field) in _ratio_field_keys()
        if is_ratio:
            delta_pct = abs(delta)
        else:
            delta_pct = delta / abs(actual) if actual else None

        rule_id = _rule_for_field(bf.field)
        severity = _severity_for(rule_id, abs(delta_pct or 0.0), idx=idx)
        if severity is Severity.INFO and (delta_pct is None or abs(delta_pct) < 0.001):
            # Numerically identical — skip the flag entirely.
            continue

        # FON-54a plausibility guard (last line of defence): two figures more
        # than BASIS_MISMATCH_PCT apart are not on the same basis (a percent
        # vs a fraction, a month vs a year). Report both raw figures for
        # review at INFO severity — never escalate to a Critical variance.
        note: str | None = None
        if delta_pct is not None and abs(delta_pct) > BASIS_MISMATCH_PCT:
            severity = Severity.INFO
            note = (
                f"Basis mismatch — needs review: broker {float(bf.value):,.4g} vs "
                f"T-12 {float(actual):,.4g} on {bf.field} are not on the same basis "
                f"({abs(delta_pct):.0%} apart). No variance severity assigned."
            )

        flag_uuid = uuid5(namespace, bf.field)
        flags.append(
            VarianceFlag(
                id=flag_uuid,
                deal_id=deal_uuid,
                field=bf.field,
                actual=float(actual),
                broker=float(bf.value),
                delta=delta,
                delta_pct=delta_pct,
                severity=severity,
                rule_id=rule_id,
                source_document_id=bf.source_document_id,
                source_page=bf.source_page,
                note=note,
            )
        )
    return flags


def is_basis_mismatch(delta_pct: float | None) -> bool:
    """True when a flag's |delta_pct| exceeds the plausibility guard."""
    return delta_pct is not None and abs(delta_pct) > BASIS_MISMATCH_PCT


def _validate_rule_ids(flags: list[VarianceFlag]) -> list[str]:
    """Every emitted flag's rule_id must exist in the loaded catalog."""
    idx = rule_index()
    problems: list[str] = []
    for f in flags:
        if f.rule_id not in idx:
            problems.append(f"{f.field}: rule_id={f.rule_id!r} not in catalog")
    return problems


# ─────────────────────── LLM narration ───────────────────────


def _format_flags_for_llm(flags: list[VarianceFlag]) -> str:
    lines = ["=== FLAGS (deterministic — DO NOT MODIFY) ==="]
    for f in flags:
        lines.append(
            f"- field={f.field} rule_id={f.rule_id} severity={f.severity.value} "
            f"actual={f.actual:,.4f} broker={f.broker:,.4f} "
            f"delta={f.delta:,.4f} delta_pct={f.delta_pct}"
        )
    return "\n".join(lines)


def _build_user_prompt(flags: list[VarianceFlag]) -> str:
    parts: list[str] = [
        _format_flags_for_llm(flags),
        "",
        (
            "Draft one ``note`` per flag in the same order. Notes are "
            "≤120 words each, plain hotel-underwriting English. Do not "
            "modify the field, rule_id, or numbers."
        ),
    ]
    return "\n".join(parts)


def _build_llm() -> Any:
    """Sonnet 4.6 for narration; deterministic temperature."""
    from ..llm import build_structured_llm

    return build_structured_llm(
        role="variance",
        schema=_VarianceNotes,
        max_tokens=4096,
        timeout=120,
        temperature=0.1,
    )


async def _invoke_llm(
    llm: Any, messages: list[Any], usage: Any | None = None
) -> _VarianceNotes:
    config = {"callbacks": [usage]} if usage is not None else None
    raw = await llm.ainvoke(messages, config=config)
    if isinstance(raw, _VarianceNotes):
        return raw
    if isinstance(raw, BaseModel):
        return _VarianceNotes.model_validate(raw.model_dump())
    if isinstance(raw, dict):
        return _VarianceNotes.model_validate(raw)
    raise ValueError(f"Unexpected Variance LLM return: {type(raw).__name__}")


# ─────────────────────── public entry point ───────────────────────


def _to_uuid(deal_id: str) -> UUID:
    """Coerce a string deal_id to UUID; fall back to a deterministic v5."""
    try:
        return UUID(deal_id)
    except (TypeError, ValueError):
        return uuid5(UUID("00000000-0000-0000-0000-000000000000"), deal_id)


# ─────────────── FON-54a: broker-claim admission + unit normalisation ───────────────
#
# Sam's deal (FON-54, "implausibly large variances") showed the two ways a
# raw extractor row can masquerade as the broker's claim about the subject:
#   * it comes from an ACTUALS document (a T-12 / P&L) whose extractor output
#     happens to use a broker-style path (the OM's own summary-block path on a
#     2023 P&L, a bare P&L GOP line on a 2019 P&L) or a flat key that is in
#     the rule table;
#   * it is a market / historical row inside the OM — a competitive-set
#     segment stat, or the OM's historical-year block — neither of which is
#     the broker's pro-forma claim.
# ``_broker_fields_from_extraction(strict=True, doc_type=…)`` admits only the
# broker's own claim; the rejected rows are handed back (``excluded``) so the
# variance report can show *what* was excluded and why.
#
# Phase 1.3d: which namespaces ARE the broker's claim is now the registry's
# answer (:func:`broker_claim_prefixes` — the aliases whose basis on broker
# material is ``broker``), and "explicitly the broker's, wherever it sits" is
# the registry's own path rule (:func:`is_explicit_broker_path`). The
# doc-type buckets below stay local: they are about the DOCUMENT, not a path.

#: |delta_pct| above which two figures are not on the same basis (a percent vs
#: a fraction, a month vs a year). Reported as "Basis mismatch — needs
#: review" at INFO severity instead of a Critical variance.
BASIS_MISMATCH_PCT = 3.0

#: Document types whose extraction is broker material (the OM / proforma).
BROKER_DOC_TYPES: frozenset[str] = frozenset({"OM", "BROKER", "BROKER_PROFORMA", "PROFORMA"})

#: Document types whose extraction is the subject's ACTUALS.
ACTUALS_DOC_TYPES: frozenset[str] = frozenset({"T12", "PNL", "P&L", "FINANCIALS"})

#: STR / CoStar reports — STR-reported subject (or submarket) performance.
STR_DOC_TYPES: frozenset[str] = frozenset({"STR", "STR_TREND", "STR_SEGMENTATION", "COSTAR"})

#: Third-party market data (CBRE Horizons, benchmarks) — not the broker's claim.
MARKET_DOC_TYPES: frozenset[str] = frozenset({"CBRE_HORIZONS", "CBRE", "PNL_BENCHMARK", "HOTSTATS", "BENCHMARK"})


#: Every unit refusal :func:`normalize_broker_value` produces ends with this.
#: It is the one rejection that is NOT about where the row came from, so it is
#: the one that maps to ``unit_unknown`` rather than ``basis_excluded``
#: (Phase 4.1). The prose itself is unchanged — the existing FON-54a tests
#: assert ``"not established" in reason`` and still do.
UNIT_UNESTABLISHED = "unit not established"

# ─── NOI reserve basis (FON-54 §2, FON-5) ───
#
# USALI 11th carries two profit lines and Fondok's registry names both:
# ``ebitda`` is "EBITDA" — stated BEFORE the FF&E replacement reserve;
# ``noi`` is "EBITDA Less Replacement Reserve" — after it
# (``noi.identity = gop - mgmt_fee - ffe_reserve - fixed_charges``).
# ``USALIFinancials.noi``, the T-12 side of every comparison, is the
# after-reserve line by definition ("post mgmt fee, FF&E reserve, fixed
# charges"), while a broker proforma / OM summary states NOI before the
# reserve. Comparing the two is a basis difference, not a variance: on Sam's
# deal it read as a Critical +87% overstatement.
#
# So the NOI comparison is made like-for-like on the BEFORE-reserve basis when
# the actuals document states one (registry concept ``ebitda``, whose aliases
# live in ``concepts.yaml``); when it states only the after-reserve line, the
# pair is refused with ``basis_mismatch``, both figures disclosed and NO
# severity. The before-reserve figure is only ever READ from the document —
# never reconstructed from the after-reserve line plus the reserve.

#: The after-reserve rollup concept — what a broker NOI claim resolves to.
NOI_CONCEPT = "noi"

#: The before-reserve rollup concept — the like-for-like basis.
NOI_BEFORE_RESERVE_CONCEPT = "ebitda"

#: Tail that makes :func:`exclusion_code` report ``basis_mismatch``.
RESERVE_BASIS_UNMATCHED = "not on the same FF&E-reserve basis"


def reserve_basis_reason(field: str, broker: float, actual: float) -> str:
    """Prose for a NOI pair that straddles the FF&E reserve — both figures named."""
    return (
        f"{field}: broker ${broker:,.0f} is NOI before the FF&E replacement "
        f"reserve; the only T-12 line is ${actual:,.0f} after it, and the "
        f"document states no before-reserve (EBITDA) line — "
        f"{RESERVE_BASIS_UNMATCHED}"
    )


def before_reserve_noi(fields: Any, *, doc_type: str | None = None) -> float | None:
    """The document's NOI BEFORE the FF&E reserve, when it states one.

    Straight through :func:`registry.resolve` on the ``ebitda`` concept, so
    every alias stays in ``concepts.yaml`` and period slices / incompatible
    units are filtered by the resolver rather than restated here.
    """
    from ..ontology.registry import resolve

    value = resolve(
        fields, NOI_BEFORE_RESERVE_CONCEPT, doc_type=doc_type, want="annual"
    ).value
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def exclusion_code(reason: str) -> ReasonCode:
    """Machine-readable code for one prose ``excluded`` reason (Phase 4.1).

    The prose stays exactly as FON-54a wrote it — an analyst reading the
    Technical detail sees the same sentence. This is the parallel channel:
    a row dropped because its unit could not be established is
    ``unit_unknown``; a NOI pair that straddles the FF&E reserve is
    ``basis_mismatch``; every other rejection is a *source* rejection (an
    actuals / STR / market document, a comp-set segment, the OM's history)
    and is ``basis_excluded`` — "not admitted as a broker claim".
    """
    text = reason.strip()
    if text.endswith(UNIT_UNESTABLISHED):
        return ReasonCode.UNIT_UNKNOWN
    if text.endswith(RESERVE_BASIS_UNMATCHED):
        return ReasonCode.BASIS_MISMATCH
    return ReasonCode.BASIS_EXCLUDED


def non_broker_source_reason(doc_type: str | None) -> str:
    """Why a claim-path row from ``doc_type`` is NOT the broker's claim (FON-54a part 3).

    Live on Sam's deal: subject-performance rows from STR_TREND documents
    (CoStar submarket PDFs, STR ``ANG-…-USD-E`` reports) were admitted as
    broker claims and one headlined the occupancy flag. STR-reported
    performance is a *reading* of the subject / submarket, not what the
    broker asserts in the OM.
    """
    dtype = (doc_type or "").strip().upper()
    if not dtype:
        return "document type unknown — cannot be established as broker material; not the broker's claim"
    if dtype in ACTUALS_DOC_TYPES:
        return f"from an actuals document ({dtype}) — a T-12 / P&L line, not a broker claim"
    if dtype in STR_DOC_TYPES:
        return f"STR-reported subject / submarket performance ({dtype}), not the broker's claim"
    if dtype in MARKET_DOC_TYPES:
        return f"third-party market data ({dtype}), not the broker's claim"
    return f"from a {dtype} document — not broker material, not the broker's claim"

_PERIOD_SLICE_TAGS: tuple[str, ...] = (
    ".monthly.",
    ".quarterly.",
    ".weekly.",
    ".daily.",
    ".ytd.",
    ".mtd.",
    ".qtd.",
)

_MULTI_YEAR_TAGS: tuple[str, ...] = (
    "_year_2_", "_year_3_", "_year_4_", "_year_5_",
    "_year2_", "_year3_", "_year4_", "_year5_",
    "_stabilized_", "stabilized.",
    "year_2.", "year_3.", "year_4.", "year_5.",
)

def is_period_slice(path: str) -> bool:
    """A monthly / quarterly / YTD … slice — never comparable to an annual T-12 line."""
    lower = path.lower()
    return any(tag in lower for tag in _PERIOD_SLICE_TAGS)


def is_om_historical_year(path: str) -> bool:
    """The OM's historical-year block — history, not a proforma claim.

    A statement namespace carrying a four-digit year segment, or an explicit
    ``historical_performance.*`` / ``historical.*`` path. The registry owns
    the rule (``registry.is_om_historical_year``, which feeds its
    ``om_history`` basis); this is the variance agent's name for it.
    """
    from ..ontology.registry import is_om_historical_year as _rule

    return _rule(path)


def is_market_segment(path: str) -> bool:
    """A competitive-set / market-segment stat — market data, not the subject.

    The registry owns the rule (``registry.is_market_segment``, which feeds
    its ``market`` basis).
    """
    from ..ontology.registry import is_market_segment as _rule

    return _rule(path)


def is_explicit_broker_path(path: str) -> bool:
    """The path itself says "this is the broker's", wherever the row sits.

    The registry's basis rule with no document to lean on: only a path the
    registry reads as explicitly the broker's own comes back ``broker``
    (a document default cannot apply without a document type).
    """
    from ..ontology.registry import _basis_for

    return _basis_for(path.strip().lower(), None, None) == "broker"


def is_broker_claim_path(path: str) -> bool:
    """The path is the broker's claim about the subject (FON-54a).

    Either explicitly the broker's, or inside one of the namespaces the
    registry annotates as the broker's claim on broker material.
    """
    lower = path.strip().lower()
    return is_explicit_broker_path(lower) or lower.startswith(broker_claim_prefixes())


def is_forward_projection(path: str) -> bool:
    lower = path.lower()
    if ".forecast." in lower or ".projection." in lower:
        return True
    return any(tag in lower for tag in _MULTI_YEAR_TAGS)


def normalize_broker_value(
    field: str, value: float, unit: str | None = None
) -> tuple[float | None, str | None]:
    """Bring a raw extractor value onto the T-12's basis before comparing.

    * occupancy-style ratios → a fraction in [0, 1]: ``83`` / ``83%`` → 0.83;
      a value above 100 has no established unit → ``(None, reason)``;
    * currency → whole dollars: a unit of thousands (``$000``, ``k``,
      ``thousands``) is scaled ×1,000; anything else is taken as dollars.
    Returns ``(normalised_value, note)`` — ``note`` explains any conversion.
    """
    key = _normalize_field_key(field)
    u = (unit or "").strip().lower()
    if key in _ratio_field_keys():
        if value < 0:
            return None, f"occupancy {value} is negative — unit not established"
        if value <= 1.0:
            return float(value), None
        if value <= 100.0:
            return float(value) / 100.0, f"occupancy {value:g}% read as {value / 100.0:.3f}"
        return None, f"occupancy {value:g} exceeds 100% — unit not established"
    if u in ("$000", "$000s", "000", "000s", "k", "$k", "thousands", "usd_thousands", "usd000"):
        return float(value) * 1000.0, f"{unit} scaled ×1,000 to dollars"
    return float(value), None


def _broker_fields_from_extraction(
    fields: list[ExtractionField],
    *,
    doc_type: str | None = None,
    strict: bool = False,
    excluded: list[tuple[ExtractionField, str]] | None = None,
    out_of_scope: list[tuple[ExtractionField, ReasonCode]] | None = None,
) -> list[VarianceBrokerField]:
    """Pull the broker-proforma rows out of an Extractor field list.

    Legacy (``strict=False``) behaviour: anything on an explicitly-broker path
    or a flat key the rule table knows about is admitted — the pipeline hands
    this function the OM's extraction only.

    ``strict=True`` (the variance endpoint, which sees EVERY document's rows):
    a row is the broker's claim only when
      * its path is explicitly the broker's wherever it sits
        (:func:`is_explicit_broker_path`), or
      * it is a claim path (:func:`is_broker_claim_path`) or a flat known key
        ON BROKER MATERIAL (``doc_type`` in :data:`BROKER_DOC_TYPES`).
    A claim-path row from any other document — a T-12 / P&L (actuals), an STR
    / CoStar report (STR-reported subject or submarket performance), CBRE or
    another market report, CAPEX / INSURANCE / PROPERTY_INFO, or an unknown
    document type — is excluded with :func:`non_broker_source_reason`.
    In both modes market-segment rows, the OM's historical-year blocks,
    period slices and forward projections are dropped. Rows rejected for a
    *source* reason are appended to ``excluded`` with the reason so the report
    can disclose them; period slices and projections are dropped silently
    (they were never candidates). Values are unit-normalised
    (:func:`normalize_broker_value`); a value whose unit cannot be established
    is excluded with the reason.

    Phase 4.1: pass ``out_of_scope`` to also collect the silent drops with a
    :class:`ReasonCode` — a monthly / YTD slice is ``period_mismatch``, a
    forward projection ``not_applicable``. ``excluded`` is untouched by this
    (the FON-54a disclosure list keeps exactly the rows it had), so the
    variance report can say "this concept was never comparable" without
    changing what Technical detail shows.
    """
    out: list[VarianceBrokerField] = []
    dtype = (doc_type or "").strip().upper() or None
    from_broker_doc = dtype in BROKER_DOC_TYPES

    def _reject(f: ExtractionField, reason: str) -> None:
        if excluded is not None:
            excluded.append((f, reason))

    for f in fields:
        name = f.field_name
        lower = name.lower()
        explicit_broker = is_explicit_broker_path(lower)
        claim_path = explicit_broker or lower.startswith(broker_claim_prefixes())
        # Parity exception (Phase 1.3d): the flat-key gate stays keyed on the
        # PRE-registry last-segment strip. Classifying the gate through
        # ``concept_for_path`` would admit — and then disclose as excluded —
        # statement rows the endpoint has never reported (a bare
        # ``gross_operating_profit`` line, a parent-qualified rooms revenue).
        # See DRIFT_NOTES.md "Phase 1.3d parity exceptions".
        known = _legacy_field_key(name) in _broker_rule_by_field()
        if not (claim_path or known):
            continue
        if not isinstance(f.value, int | float):
            continue
        # Scope guard (Sam QA 2026-05-13): the variance comparison only makes
        # sense between the broker's Year-1 proforma and the T-12 actual for
        # the same line. Any field whose path implies a different time slice
        # or a forward projection would produce nonsense flags (single-month
        # broker $702K vs annual T-12 $14M, etc.). Drop them up-front.
        if is_period_slice(name) or ".ttm." in lower or is_forward_projection(name):
            if out_of_scope is not None:
                out_of_scope.append(
                    (
                        f,
                        ReasonCode.NOT_APPLICABLE
                        if is_forward_projection(name)
                        else ReasonCode.PERIOD_MISMATCH,
                    )
                )
            continue
        # Source guards (FON-54a).
        if is_market_segment(name):
            _reject(f, "market-segment stat, not the broker's claim about the subject")
            continue
        if is_om_historical_year(name):
            _reject(f, "OM historical-year block — history, not the proforma claim")
            continue
        if strict and not explicit_broker and not from_broker_doc:
            # A claim path or a flat known key is the broker's claim ONLY on
            # broker material. From a T-12 / P&L it is an actual; from an STR
            # / CoStar report it is STR-reported performance; from CBRE it is
            # market data; from anything else it is simply not broker material.
            _reject(f, non_broker_source_reason(dtype))
            continue
        # Unit gate (FON-54 §2) — BEFORE any normalisation or comparison. A
        # path that declares a unit its concept does not carry (the OM's
        # %-of-revenue proforma column against a dollar T-12 line) is refused
        # here, so it never becomes a ``VarianceBrokerField`` and no delta,
        # delta_pct or severity is ever computed for it.
        unit_reason = unit_gate_reason(name)
        if unit_reason is not None:
            _reject(f, unit_reason)
            continue
        value, unit_note = normalize_broker_value(name, float(f.value), f.unit)
        if value is None:
            _reject(f, unit_note or UNIT_UNESTABLISHED)
            continue
        out.append(
            VarianceBrokerField(
                field=name,
                value=value,
                source_page=f.source_page if f.source_page >= 1 else None,
                source_doc_type=dtype,
                unit_note=unit_note,
            )
        )
    return out


@trace_agent("Variance")
async def run_variance(payload: VarianceInput) -> VarianceOutput:
    """Compare broker proforma against T-12 actuals and narrate."""
    started = datetime.now(UTC)
    t0 = time.monotonic()

    if payload.actuals is None or not (
        payload.broker_fields or payload.broker_extraction
    ):
        logger.info(
            "variance: insufficient input (deal=%s actuals=%s broker_fields=%d) — empty report",
            payload.deal_id,
            payload.actuals is not None,
            len(payload.broker_fields),
        )
        deal_uuid = _to_uuid(payload.deal_id)
        return VarianceOutput(
            deal_id=payload.deal_id,
            report=VarianceReport(deal_id=deal_uuid, flags=[]),
            flags=[],
            success=True,
            model_calls=[],
        )

    try:
        check_budget(
            {"deal_id": payload.deal_id, "model_calls": []}, stage="variance"
        )
    except Exception as exc:
        logger.warning("variance: budget check raised: %s", exc)
        return VarianceOutput(
            deal_id=payload.deal_id,
            report=None,
            flags=[],
            success=False,
            error=str(exc),
        )

    # Step 1 + 2 — deterministic.
    deal_uuid = _to_uuid(payload.deal_id)
    broker_fields = list(payload.broker_fields)
    if payload.broker_extraction:
        broker_fields.extend(
            _broker_fields_from_extraction(payload.broker_extraction)
        )
    flags = _build_flags(
        deal_uuid=deal_uuid,
        actuals=payload.actuals,
        broker_fields=broker_fields,
        actuals_before_reserve_noi=(
            before_reserve_noi(
                payload.actuals_extraction, doc_type=payload.actuals_doc_type
            )
            if payload.actuals_extraction
            else None
        ),
    )

    rule_problems = _validate_rule_ids(flags)
    if rule_problems:
        logger.error("variance: rule validation failed — %s", "; ".join(rule_problems))
        return VarianceOutput(
            deal_id=payload.deal_id,
            report=None,
            flags=[],
            success=False,
            error="rule_id validation: " + "; ".join(rule_problems),
        )

    if not flags:
        logger.info("variance: no flags fired (deal=%s)", payload.deal_id)
        return VarianceOutput(
            deal_id=payload.deal_id,
            report=VarianceReport(deal_id=deal_uuid, flags=[]),
            flags=[],
            success=True,
            model_calls=[],
        )

    # Step 3 — LLM narration. Errors don't drop the flags; we just emit
    # them with a stub note so downstream consumers still see the variance.
    from ..llm import build_agent_system_blocks, cached_system_message_blocks
    from ..usage import UsageCapture

    # 4-block system prompt: agent instructions (uncached) + USALI rules
    # + brand catalog + schema addendum (all cached).
    system_blocks = build_agent_system_blocks(
        role="variance",
        agent_instructions=SYSTEM_PROMPT,
    )
    rules_as_prompt_block()  # warm the catalog cache
    messages = [
        cached_system_message_blocks(system_blocks, role="variance"),
        HumanMessage(content=_build_user_prompt(flags)),
    ]
    usage = UsageCapture()
    notes_envelope: _VarianceNotes | None = None
    llm_error: str | None = None
    try:
        llm = _build_llm()
        notes_envelope = await _invoke_llm(llm, messages, usage=usage)
    except (ValidationError, Exception) as exc:  # noqa: BLE001 - error path
        logger.warning("variance: narration LLM failed (%s)", exc)
        llm_error = f"{type(exc).__name__}: {exc}"

    # Merge notes back onto the flags by (field, rule_id).
    note_by_key: dict[tuple[str, str], str] = {}
    if notes_envelope is not None:
        for entry in notes_envelope.notes:
            note_by_key[(entry.field, entry.rule_id)] = entry.note

    enriched: list[VarianceFlag] = []
    for f in flags:
        note = note_by_key.get((f.field, f.rule_id))
        if note is None:
            note = (
                f"{f.field}: broker={f.broker:,.2f} vs actual={f.actual:,.2f} "
                f"({f.delta_pct or 0:.1%}). Severity {f.severity.value} per "
                f"rule {f.rule_id}."
            )
        enriched.append(f.model_copy(update={"note": note}))

    critical = sum(1 for f in enriched if f.severity is Severity.CRITICAL)
    warn = sum(1 for f in enriched if f.severity is Severity.WARN)
    info = sum(1 for f in enriched if f.severity is Severity.INFO)
    report = VarianceReport(
        deal_id=deal_uuid,
        flags=enriched,
        critical_count=critical,
        warn_count=warn,
        info_count=info,
    )

    completed = datetime.now(UTC)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    settings = get_settings()
    # Variance shares the Analyst model setting unless an env override
    # (ANTHROPIC_VARIANCE_MODEL) is in place — the LLM factory reads
    # that env var directly via `_role_model("variance")`.
    fallback_model = (
        getattr(settings, "ANTHROPIC_VARIANCE_MODEL", None)
        or settings.ANTHROPIC_ANALYST_MODEL
    )
    model_calls: list[ModelCall] = []
    if notes_envelope is not None:
        model_calls.append(
            ModelCall(
                model=usage.model or fallback_model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=0.0,
                trace_id=payload.deal_id,
                started_at=started,
                completed_at=completed,
                cache_creation_input_tokens=usage.cache_creation_input_tokens,
                cache_read_input_tokens=usage.cache_read_input_tokens,
                agent_name="variance",
            )
        )

    logger.info(
        "variance OK deal=%s flags=%d (CRIT=%d WARN=%d INFO=%d) in %dms",
        payload.deal_id,
        len(enriched),
        critical,
        warn,
        info,
        elapsed_ms,
    )

    # Legacy flags list (dict[]) for callers (e.g. graph node) that
    # haven't migrated to the typed VarianceReport yet.
    legacy = [f.model_dump(mode="json") for f in enriched]

    # Persist for the cost dashboard. Best-effort.
    if model_calls:
        from ..cost_persistence import persist_model_calls_standalone

        await persist_model_calls_standalone(
            deal_id=payload.deal_id,
            tenant_id=payload.tenant_id,
            calls=model_calls,
        )

    return VarianceOutput(
        deal_id=payload.deal_id,
        report=report,
        flags=legacy,
        success=llm_error is None,
        error=llm_error,
        model_calls=model_calls,
    )


__all__ = [
    "VarianceBrokerField",
    "VarianceInput",
    "VarianceOutput",
    "run_variance",
]
