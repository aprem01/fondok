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
from typing import Annotated, Any
from uuid import UUID, uuid4, uuid5

from fondok_schemas import ExtractionField, ModelCall, Severity, USALIFinancials
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


class VarianceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    report: VarianceReport | None = None
    flags: list[dict[str, Any]] = Field(default_factory=list)
    success: bool = True
    error: str | None = None
    model_calls: list[ModelCall] = Field(default_factory=list)


# ─────────────────────── deterministic comparison ───────────────────────


# Map canonical field names to (T-12 actual extractor, BROKER_VS_T12 rule_id).
# Each entry: how to read the actual off ``USALIFinancials`` and which
# rule from the catalog covers it.

_BROKER_RULE_BY_FIELD: dict[str, str] = {
    "noi": "BROKER_VS_T12_NOI_VARIANCE",
    "noi_usd": "BROKER_VS_T12_NOI_VARIANCE",
    "occupancy": "BROKER_VS_T12_OCC_VARIANCE",
    "occupancy_pct": "BROKER_VS_T12_OCC_VARIANCE",
    "adr": "BROKER_VS_T12_ADR_VARIANCE",
    "adr_usd": "BROKER_VS_T12_ADR_VARIANCE",
    "revpar": "REVPAR_GROWTH_RANGE",
    "revpar_usd": "REVPAR_GROWTH_RANGE",
    "rooms_revenue": "BROKER_VS_T12_NOI_VARIANCE",
    "rooms_revenue_usd": "BROKER_VS_T12_NOI_VARIANCE",
    "fb_revenue": "FB_DEPT_MARGIN_FULL",
    "fb_revenue_usd": "FB_DEPT_MARGIN_FULL",
    "total_revenue": "BROKER_VS_T12_NOI_VARIANCE",
    "total_revenue_usd": "BROKER_VS_T12_NOI_VARIANCE",
    "departmental_expenses": "DEPT_EXPENSE_SUM",
    "departmental_expenses_usd": "DEPT_EXPENSE_SUM",
    "undistributed_expenses": "A_AND_G_PCT_REVENUE",
    "undistributed_expenses_usd": "A_AND_G_PCT_REVENUE",
    "gop": "GOP_MARGIN_RANGE",
    "gop_usd": "GOP_MARGIN_RANGE",
    "mgmt_fee": "MGMT_FEE_RANGE",
    "mgmt_fee_usd": "MGMT_FEE_RANGE",
    "ffe_reserve": "FFE_RESERVE_RANGE",
    "ffe_reserve_usd": "FFE_RESERVE_RANGE",
    "fixed_charges": "INSURANCE_PER_KEY",
    "fixed_charges_usd": "INSURANCE_PER_KEY",
    "insurance": "INSURANCE_PER_KEY",
    "insurance_usd": "INSURANCE_PER_KEY",
}


def _normalize_field_key(name: str) -> str:
    """Strip namespace prefixes and unit suffixes the OM extractor uses."""
    s = name.strip()
    # Drop dotted path prefixes ("broker_proforma.noi_usd" → "noi_usd").
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return s.lower()


def _actual_for(field: str, actuals: USALIFinancials) -> float | None:
    """Read the matching T-12 actual for a canonical broker field."""
    f = _normalize_field_key(field)
    if f in ("noi", "noi_usd"):
        return actuals.noi
    if f in ("occupancy", "occupancy_pct"):
        return actuals.occupancy
    if f in ("adr", "adr_usd"):
        return actuals.adr
    if f in ("revpar", "revpar_usd"):
        return actuals.revpar
    if f in ("rooms_revenue", "rooms_revenue_usd"):
        return actuals.rooms_revenue
    if f in ("fb_revenue", "fb_revenue_usd"):
        return actuals.fb_revenue
    if f in ("total_revenue", "total_revenue_usd"):
        return actuals.total_revenue
    if f in ("departmental_expenses", "departmental_expenses_usd"):
        return actuals.dept_expenses.total
    if f in ("undistributed_expenses", "undistributed_expenses_usd"):
        return actuals.undistributed.total
    if f in ("gop", "gop_usd"):
        return actuals.gop
    if f in ("mgmt_fee", "mgmt_fee_usd"):
        return actuals.mgmt_fee
    if f in ("ffe_reserve", "ffe_reserve_usd"):
        return actuals.ffe_reserve
    if f in ("fixed_charges", "fixed_charges_usd"):
        return actuals.fixed_charges.total
    if f in ("insurance", "insurance_usd"):
        return actuals.fixed_charges.insurance
    return None


def _rule_for_field(field: str) -> str:
    """Map a broker field onto the catalog rule_id used to flag it."""
    f = _normalize_field_key(field)
    return _BROKER_RULE_BY_FIELD.get(f, "BROKER_VS_T12_NOI_VARIANCE")


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
) -> list[VarianceFlag]:
    """Step 1 + 2: deterministic field match + severity assignment."""
    idx = rule_index()
    flags: list[VarianceFlag] = []

    # Stable namespace so re-runs of the same deal produce the same
    # flag UUIDs (helps the UI dedupe across re-extracts).
    namespace = uuid5(UUID("00000000-0000-0000-0000-000000000000"), str(deal_uuid))

    for bf in broker_fields:
        actual = _actual_for(bf.field, actuals)
        if actual is None or actual == 0:
            continue
        delta = float(actual) - float(bf.value)
        # For percentage / ratio fields ``actual`` is already in [0,1];
        # use the raw delta for those. Everything else uses pct-of-actual.
        is_ratio = _normalize_field_key(bf.field) in (
            "occupancy",
            "occupancy_pct",
        )
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
#     happens to use a broker-style path (``ttm_summary_per_om.occupancy_pct``
#     on a 2023 P&L, ``p_and_l_usali.gop`` on a 2019 P&L) or a flat key that
#     is in the rule table;
#   * it is a market / historical row inside the OM — a ``ttm_performance.
#     segment.*`` competitive-set stat, or the OM's historical-year block
#     ``p_and_l_usali.<year>.*`` — neither is the broker's pro-forma claim.
# ``_broker_fields_from_extraction(strict=True, doc_type=…)`` admits only the
# broker's own claim; the rejected rows are handed back (``excluded``) so the
# variance report can show *what* was excluded and why.

#: |delta_pct| above which two figures are not on the same basis (a percent vs
#: a fraction, a month vs a year). Reported as "Basis mismatch — needs
#: review" at INFO severity instead of a Critical variance.
BASIS_MISMATCH_PCT = 3.0

#: Paths that ARE the broker's claim about the subject property.
BROKER_CLAIM_PREFIXES: tuple[str, ...] = (
    "broker_proforma.",
    "broker.",
    "ttm_summary_per_om.",
    "ttm_performance.subject.",
)

#: Document types whose extraction is broker material (the OM / proforma).
BROKER_DOC_TYPES: frozenset[str] = frozenset({"OM", "BROKER", "BROKER_PROFORMA", "PROFORMA"})

#: Document types whose extraction is the subject's ACTUALS.
ACTUALS_DOC_TYPES: frozenset[str] = frozenset({"T12", "PNL", "P&L", "FINANCIALS"})

#: STR / CoStar reports — STR-reported subject (or submarket) performance.
STR_DOC_TYPES: frozenset[str] = frozenset({"STR", "STR_TREND", "STR_SEGMENTATION", "COSTAR"})

#: Third-party market data (CBRE Horizons, benchmarks) — not the broker's claim.
MARKET_DOC_TYPES: frozenset[str] = frozenset({"CBRE_HORIZONS", "CBRE", "PNL_BENCHMARK", "HOTSTATS", "BENCHMARK"})


def non_broker_source_reason(doc_type: str | None) -> str:
    """Why a claim-path row from ``doc_type`` is NOT the broker's claim (FON-54a part 3).

    Live on Sam's deal: ``ttm_performance.subject.*`` rows from STR_TREND
    documents (CoStar submarket PDFs, STR ``ANG-…-USD-E`` reports) were
    admitted as broker claims and one headlined the occupancy flag. STR-
    reported performance is a *reading* of the subject / submarket, not what
    the broker asserts in the OM.
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

_RATIO_CONCEPTS: frozenset[str] = frozenset({"occupancy", "occupancy_pct"})


def is_period_slice(path: str) -> bool:
    """A monthly / quarterly / YTD … slice — never comparable to an annual T-12 line."""
    lower = path.lower()
    return any(tag in lower for tag in _PERIOD_SLICE_TAGS)


def is_om_historical_year(path: str) -> bool:
    """The OM's historical-year block (``p_and_l_usali.2021.gop_usd``,
    ``historical_performance.2022.*``) — history, not a proforma claim."""
    lower = path.lower()
    if lower.startswith(("historical_performance.", "historical.")):
        return True
    parts = lower.split(".")
    return any(p.isdigit() and len(p) == 4 and p.startswith(("19", "20")) for p in parts[:-1])


def is_market_segment(path: str) -> bool:
    """A competitive-set / market-segment stat (``ttm_performance.segment.*``)."""
    lower = path.lower()
    return ".segment." in lower or lower.startswith(("segment.", "market.", "comp_set.", "compset."))


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
    if key in _RATIO_CONCEPTS:
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
) -> list[VarianceBrokerField]:
    """Pull the broker-proforma rows out of an Extractor field list.

    Legacy (``strict=False``) behaviour: anything under a ``broker_proforma.*``
    / ``broker.*`` path or a flat key the rule table knows about is admitted —
    the pipeline hands this function the OM's extraction only.

    ``strict=True`` (the variance endpoint, which sees EVERY document's rows):
    a row is the broker's claim only when
      * its path is explicitly ``broker_proforma.*`` / ``broker.*`` (wherever
        it sits), or
      * it is a claim path (:data:`BROKER_CLAIM_PREFIXES`) or a flat known key
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
        key = _normalize_field_key(name)
        explicit_broker = lower.startswith(("broker_proforma.", "broker."))
        claim_path = lower.startswith(BROKER_CLAIM_PREFIXES)
        known = key in _BROKER_RULE_BY_FIELD
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
        value, unit_note = normalize_broker_value(name, float(f.value), f.unit)
        if value is None:
            _reject(f, unit_note or "unit not established")
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
