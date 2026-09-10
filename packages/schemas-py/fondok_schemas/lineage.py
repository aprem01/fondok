"""Evidence lineage — one queryable graph from a KPI down to a document page.

Phase 2.3. The evidence chain already exists in pieces: the engines emit a
per-value :mod:`~fondok_schemas.provenance` sidecar (``traces_to`` /
``assumption_key`` / ``source``), ``_load_engine_inputs`` records which
``SOURCE_*`` label produced each assumption (``__sources__``, and — where the
runner supplies it — which extraction row produced it, ``__source_fields__``),
the documents carry pages, and the IC memo carries citations. Nothing joined
them. This module is the join: one **read-only, additive** record that an
analyst (or an auditor) can walk end to end.

The graph is deliberately boring — nodes and edges, no nesting:

    kpi:returns.levered_irr
      └─ computed_from → engine:returns.levered_irr
           └─ computed_from → engine:expense.years[0].noi
                └─ computed_from → engine:revenue.years[0].total_revenue
                     └─ computed_from → engine:revenue.years[0].rooms_revenue
                          └─ seeded_from → assumption:starting_occupancy
                               └─ extracted_from → field:<extraction_result_id>:<field_name>
                                    └─ extracted_from → doc:<document_id>
                                         └─ located_on → page:<document_id>:4

Node ids are stable, human-readable and prefix-typed so a UI can route on the
prefix alone (``kpi:`` … ``page:``). Edges read left to right as
"``src`` ⟨``rel``⟩ ``dst``" and always point from the derived thing toward what
it came from, so following edges whose ``src`` is the current node walks
*downward* from a root toward evidence — one step per level, never inverted.

Refusals carry the node they belong to: :attr:`Refusal.concept` is either the
ontology concept id or the full node id the refusal is about, and
:attr:`Refusal.document_id` is set whenever the refusal concerns a document, so
a consumer can attach each one to the exact step it broke on.

Two invariants make the record trustworthy:

* **Nothing is silently dropped.** A root, an assumption or a reference that
  cannot be walked to a page lands in :attr:`LineageRecord.unresolved` as a
  typed :class:`~fondok_schemas.reasons.Refusal` — the same vocabulary the
  dashes on every tab use. An empty ``unresolved`` means every root reached a
  page; it never means "we stopped looking".
* **The record is pinned to a run.** It carries the ``run_id`` it was built
  from plus the registry / pipeline versions in force, and :attr:`stale` says
  whether the deal's documents or the deal row itself have moved since that
  run started. A stale record is still served (the analyst can see what the
  numbers *were* grounded in) — it just cannot claim to describe today.

``normalized_line`` is part of the node vocabulary but is not emitted yet:
there is no persisted USALI-normalized spread to point a node at. It stays in
the enum so the walk gains that hop without a schema change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .provenance import State
from .reasons import ReasonCode, Refusal

#: What a node *is*. The prefix on :attr:`LineageNode.id` mirrors it 1:1.
NodeKind = Literal[
    "kpi",              # a headline number a tab renders — a lineage root
    "engine_value",     # one modeled value in an engine's provenance sidecar
    "assumption",       # a canonical underwriting assumption (a __sources__ key)
    "normalized_line",  # a USALI-normalized statement line (reserved — see module doc)
    "extracted_field",  # one field on one extraction_results row
    "document",         # an uploaded document
    "page",             # one page of one document
    "override",         # an analyst field_overrides entry (carries the note)
    "seed",             # a seed / platform default — terminal, carries a reason
    "benchmark",        # a benchmark or market feed (CBRE / HOST / portfolio / STR)
    "memo_section",     # an IC-memo section that cites the evidence
]

#: How a node relates to the node it points at.
EdgeRel = Literal[
    "computed_from",    # produced by a formula over the target
    "seeded_from",      # the target is the input assumption / seed behind it
    "normalized_from",  # normalized out of the target statement line
    "extracted_from",   # read out of the target field / document
    "located_on",       # the target page is where the value physically sits
    "overridden_by",    # an analyst override replaced the modeled value
    "cited_in",         # the target memo section cites this evidence
]


class LineageNode(BaseModel):
    """One thing in the evidence chain.

    ``id`` is ``"<kind-prefix>:<identity>"`` and is unique within a record:

    ==================  ==================================================
    ``kpi``             ``kpi:returns.levered_irr``
    ``engine_value``    ``engine:expense.years[0].noi``
    ``assumption``      ``assumption:starting_occupancy``
    ``normalized_line`` ``line:<extraction_result_id>:<concept>``
    ``extracted_field`` ``field:<extraction_result_id>:<field_name>``
    ``document``        ``doc:<document_id>``
    ``page``            ``page:<document_id>:<n>``
    ``override``        ``override:<assumption_key>``
    ``seed``            ``seed:<assumption_key>``
    ``benchmark``       ``benchmark:<assumption_key>``
    ``memo_section``    ``memo:<section_id>``
    ==================  ==================================================

    Every kind has its own prefix — a consumer routes on the prefix alone and
    never has to guess a namespace. ``line:`` is reserved: no
    ``normalized_line`` node is emitted yet (see the module docstring), but the
    prefix is fixed now so adding that hop is not a breaking change.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: NodeKind
    label: str
    #: The number (or text, for a descriptive field) this node carries.
    #: ``None`` when the node is structural (a document, a page) or refused.
    value: float | str | None = None
    unit: str | None = None
    #: Ontology concept id (``occupancy``, ``noi`` …) when one applies.
    concept: str | None = None
    #: Terminal ``SOURCE_*`` label (``t12_actual``, ``seed``, ``analyst_override`` …).
    source: str | None = None
    #: FON-65 grounding classification, carried through from the value's trace.
    state: State | None = None
    #: Why this node is a dash / a dead end, when it is one.
    reason: ReasonCode | None = None
    #: Free-form extras — formula, note, page count, doc_type, scope, basis …
    meta: dict[str, Any] = Field(default_factory=dict)


class LineageEdge(BaseModel):
    """A directed link from a derived node toward the evidence behind it."""

    model_config = ConfigDict(extra="forbid")

    src: str
    dst: str
    rel: EdgeRel
    #: The calculation rationale on the ``src`` side, when the engine supplied
    #: one (``"noi = gop less management_fee, ffe_reserve and fixed_charges"``),
    #: or the reason a bridge edge exists when the lineage service derived it.
    formula: str | None = None
    #: Free-form extras. ``meta["link"]`` says HOW the link was established and
    #: is the honesty tell for the whole record:
    #:
    #: * ``"asserted"``   — the engine named the assumption on the trace itself
    #:                      (``ValueInput.assumption_key`` / ``ValueTrace.assumption_key``);
    #: * ``"traces_to"``  — the engine pointed at another traced value;
    #: * ``"name_match"`` — inferred: the input's NAME matched the canonical
    #:                      assumption vocabulary;
    #: * ``"bridge"``     — inferred: derived from the engine dependency graph.
    #:
    #: The first two are assertions by the engine that computed the value; the
    #: last two are inferences by the lineage service. See
    #: :attr:`LineageRecord.meta` for the per-record tally.
    meta: dict[str, Any] = Field(default_factory=dict)


class LineageRecord(BaseModel):
    """The whole evidence graph for one deal, pinned to one engine run."""

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    #: The canonical run the record describes. ``None`` when the deal has
    #: never completed a full engine chain (the record then reflects whatever
    #: latest-per-engine outputs exist).
    run_id: UUID | None = None
    #: ``app.ontology.registry.registry_version()`` at build time.
    registry_version: int
    #: ``app.api.documents.EXTRACTION_PIPELINE_VERSION`` at build time.
    pipeline_version: str
    generated_at: datetime
    #: Node ids of the KPI roots — the entry points a UI offers.
    roots: list[str] = Field(default_factory=list)
    nodes: list[LineageNode] = Field(default_factory=list)
    edges: list[LineageEdge] = Field(default_factory=list)
    #: Every link the walk could not complete, each with its reason code.
    unresolved: list[Refusal] = Field(default_factory=list)
    #: True when a document was uploaded — or the deal row edited — after the
    #: run started, so the graph describes inputs that have since moved.
    stale: bool = False
    #: Record-level extras. ``meta["link_provenance"]`` tallies
    #: :attr:`LineageEdge.meta`'s ``link`` across the whole graph —
    #: ``{"asserted": n, "traces_to": n, "name_match": n, "bridge": n}`` — so
    #: how much of a given deal's evidence chain is *asserted by the engines*
    #: versus *inferred by this service* is measurable, not a matter of
    #: reading the code.
    meta: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "EdgeRel",
    "LineageEdge",
    "LineageNode",
    "LineageRecord",
    "NodeKind",
]
