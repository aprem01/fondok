"""Sibling-template extraction reuse (TASK T2).

WHY
---
A hotel data room ships five annual P&Ls (2019-2023) that are the SAME
workbook template with different numbers. Without this module each file
costs an independent Sonnet extraction (~$0.25) — and because each call
is independent, the LLM emits *different* schema paths per year
(``revenue.fb_revenue_usd`` vs ``food_and_beverage.revenue_usd``), the
schema-drift problem behind the F&B-blank and GOP-margin-leak bugs.
Learning one cell-mapping from the first LLM extraction and re-applying
it deterministically to same-template siblings kills both the cost AND
the drift at the root.

HOW (three phases, all deterministic)
-------------------------------------
1. **Template fingerprint** (``compute_template_fingerprint``) —
   computed at parse time and stored on ``documents.template_fingerprint``.
   The fingerprint is a sha256 over the sorted, digit-stripped names of
   the workbook's *visible* sheets.

   Why names only, and not the full "label skeleton"? We measured the
   real target dataset (Angler's 2019-2023 detailed P&Ls, ~49-sheet SAP
   BPC/EVDRE workbooks): 32 of 49 sheets change their label skeleton
   between two adjacent years (line items appear/disappear), and the
   2023 book carries 49 extra ``veryHidden`` GUID-named macro sheets.
   Exact skeleton hashing groups NOTHING in practice. Sheet names
   (digit-stripped, visible only) group 2020-2023 correctly while
   separating the 2019 book (different venue/BCC sheet set) and
   unrelated fixtures. A looser fingerprint is safe here because
   grouping is only a *candidate* filter — the verification gate
   (coverage floor + USALI identity-score comparison) rejects any
   mapping that does not actually fit the new workbook, in which case
   the doc falls back to the normal LLM extraction.

2. **Provenance recovery** (``learn_mapping``) — the LLM does not tell
   us which cell a value came from, so after a successful extraction we
   search the parsed grid for cells whose numeric value matches each
   extracted field (tolerance: round(x, 2); also value*1000 / value/1000
   for unit-scaled sheets). Each matching cell is anchored by LABELS,
   never coordinates:

       (sheet, section, row_label, col_header)

   * ``row_label``  — nearest non-numeric cell to the LEFT of the cell
     (EVDRE workbooks carry control columns at col 0-11, so "leftmost in
     row" is wrong; "nearest left" lands on the real line-item label).
   * ``section``    — rightmost label of the nearest preceding row that
     has NO numeric cells (e.g. "Revenues" vs "Departmental Expense" —
     disambiguates the two "Rooms" rows every P&L has).
   * ``col_header`` — the nearest 3 non-numeric labels scanning UPWARD
     in the cell's column ("actual|jan|periodic"). Date-like headers
     normalize to month-only ("2023-01-31 00:00:00" → "m01") so the
     anchor transfers across years. All other digits are stripped.

   AMBIGUITY: a value matching several anchor keys is narrowed by
   field-name-token ↔ label matching; if several anchors survive they
   are ALL kept as *consensus anchors* (the sibling must resolve every
   found anchor to the same value or the field is dropped). Values
   matching more than ``_MAX_ANCHORS`` cells, zero values, and keys
   that are non-unique in the source sheet are dropped outright —
   fallback covers them.

3. **Sibling application** (``apply_mapping``) — locate each entry's
   anchors in the new workbook's grid (label-anchored, so inserted rows
   don't break it), require consensus across anchors, and emit
   ``(field_name, new_value, confidence=0.95)``.

Measured on the real Angler's siblings (learn from 2022, apply to
2019/2020/2021/2023): 118/130 numeric fields recovered at learn time
(91%), 107-110/118 located per sibling year (91-93%), and 0 wrong
values against label-derived ground truth.

PERSISTENCE — why a separate ``template_mappings`` table
--------------------------------------------------------
The mapping is keyed by ``(tenant_id, fingerprint)`` and must be
consulted BEFORE any extraction row exists for the new document.
Storing it on ``extraction_results`` would force a JOIN through
``documents`` on fingerprint plus JSON filtering on every dispatch, and
the content-hash cache's pipeline-version bumps (``;pv=vN``) would
spuriously invalidate mappings whose anchors are still perfectly good.
A dedicated table gives an O(1) lookup and an independent lifecycle.

Safety posture: template reuse must NEVER be the reason an extraction
fails — every entry point here is wrapped so that any exception logs
and falls back to the LLM path.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
from typing import Any

from ..extraction.numeric import coerce_cell_number

logger = logging.getLogger(__name__)

MAPPING_VERSION = "v1"
# ``agent_version`` base persisted for sibling-applied extractions. The
# dispatcher suffixes ``;pv=vN`` via ``_tag_agent_version`` — the
# content-hash cache's LIKE filter depends on that suffix.
SIBLING_AGENT_VERSION_BASE = "template:sibling:v1"

# Verification-gate knobs (see ``passes_gates``).
COVERAGE_FLOOR = 0.70
USALI_SCORE_DROP_TOLERANCE = 15.0
# Below this many learned entries a mapping is not worth persisting:
# replacing a 100+ field LLM extraction with a handful of numbers would
# silently degrade coverage even when every number is right.
MIN_MAPPING_FIELDS = 10
# A value matching more cells than this is too promiscuous to anchor
# (small integers, repeated percentages).
_MAX_ANCHORS = 24
_SIBLING_CONFIDENCE = 0.95

_DATE_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T]00:00:00)?$")
# How many labels above the cell make up the column-header anchor.
_HEADER_K = 3


# ─────────────────────────── cell helpers ───────────────────────────


def _numeric_cell_value(s: str | None) -> float | None:
    """Parse a grid cell into a float, or None if it reads as a label.

    Thin wrapper over the shared ``coerce_cell_number`` (in
    ``extraction/numeric``) so the learner, the STR-trend extractor and
    the CBRE extractor all read a cell the same way — ``$(1,234)`` is
    ``-1234.0`` on every path, never ``None`` on one and a number on
    another. Handles ``$1,234.56``, ``(1,234)`` negatives, ``74%``, and
    plain floats; date-shaped strings are labels (month headers), not
    values.
    """
    return coerce_cell_number(s)


# Metric fields the schema constrains to a 0..1 ratio (the extractor
# emits e.g. 0.74, never 74). usali_scorer exposes no clean ratio-field
# set, so this small local list stands in per the task note. Used only
# as a belt-and-braces guard in apply_mapping against a percent grid
# cell (74.0) leaking through un-scaled.
_RATIO_METRIC_LEAVES = {"occupancy", "gop_margin", "noi_margin"}


def _is_ratio_field(field_name: str) -> bool:
    """True when the field is a known 0..1 ratio metric.

    Matches the leaf segment (dotted paths like
    ``ttm_summary_per_om.occupancy_pct``) against a small canonical set
    plus the ``*_pct`` / ``*_margin`` naming conventions.
    """
    leaf = (field_name or "").lower().rsplit(".", 1)[-1]
    return (
        leaf in _RATIO_METRIC_LEAVES
        or leaf.endswith("_pct")
        or leaf.endswith("_margin")
    )


def _normalize_label(s: str | None) -> str:
    """Digit-stripped, whitespace-collapsed, lowercased label text.

    Date-shaped labels keep their MONTH ("2023-01-31 00:00:00" → "m01")
    so monthly column anchors transfer across sibling years while the
    year itself never leaks into an anchor.
    """
    s = (s or "").strip()
    m = _DATE_RE.match(s)
    if m:
        return f"m{int(m.group(2)):02d}"
    s = re.sub(r"\d+", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _page_meta(page: Any) -> dict[str, Any]:
    if isinstance(page, dict):
        return page.get("metadata") or {}
    return getattr(page, "metadata", None) or {}


def _page_tables(page: Any) -> list[list[list[str]]]:
    if isinstance(page, dict):
        return page.get("tables") or []
    return getattr(page, "tables", None) or []


def _page_num(page: Any) -> int:
    if isinstance(page, dict):
        return int(page.get("page_num", 1))
    return int(getattr(page, "page_num", 1))


def _iter_visible_sheets(pages: list[Any]):
    """Yield (normalized_sheet_name, page_num, grid) for visible sheets.

    Pages may be ``ParsedPage`` models or the plain dicts persisted in
    ``documents.extraction_data``. Sheets without a ``sheet_state``
    (older parses, xlrd path) count as visible.
    """
    for p in pages:
        meta = _page_meta(p)
        if meta.get("sheet_state", "visible") != "visible":
            continue
        raw_name = meta.get("sheet_name") or f"sheet{_page_num(p)}"
        name = _normalize_label(raw_name)
        for grid in _page_tables(p):
            yield name, _page_num(p), grid


def _is_workbook_parser(parser: str | None) -> bool:
    return (parser or "").lower() in ("openpyxl", "xlrd")


# ─────────────────────────── fingerprint ───────────────────────────


def compute_template_fingerprint(
    pages: list[Any], *, parser: str | None
) -> str | None:
    """Workbook template fingerprint, or None for non-workbook docs.

    Two workbooks with the same fingerprint are treated as CANDIDATE
    same-template siblings (see module docstring for why sheet names,
    not label skeletons). Prefixed ``tplv1:`` so a future algorithm
    change can never collide with rows written by this version.
    """
    if not _is_workbook_parser(parser):
        return None
    names = sorted(name for name, _, _ in _iter_visible_sheets(pages))
    if not names:
        return None
    digest = hashlib.sha256(
        json.dumps(names, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return f"tplv1:{digest}"


# ─────────────────────────── grid indexing ───────────────────────────


def _index_grid(grid: list[list[str]]) -> list[dict[str, Any]]:
    """Anchor every numeric cell in a 2D string grid.

    Returns dicts ``{row_label, section, col_header, value, raw}``.
    See module docstring for the anchor semantics.
    """
    ncols = max((len(r) for r in grid), default=0)
    col_labels: list[list[tuple[int, str]]] = [[] for _ in range(ncols)]
    for ri, row in enumerate(grid):
        for ci in range(min(len(row), ncols)):
            c = (row[ci] or "").strip()
            if c and _numeric_cell_value(c) is None:
                nl = _normalize_label(c)
                if nl:
                    col_labels[ci].append((ri, nl))

    def header_for(ci: int, ri: int) -> str:
        above = [lb for lri, lb in col_labels[ci] if lri < ri]
        return "|".join(above[-_HEADER_K:][::-1])

    cells: list[dict[str, Any]] = []
    section = ""
    for ri, row in enumerate(grid):
        numcells = [
            (ci, _numeric_cell_value(c), c) for ci, c in enumerate(row)
        ]
        numcells = [(ci, v, c) for ci, v, c in numcells if v is not None]
        labels_in_row = [
            (ci, _normalize_label(c))
            for ci, c in enumerate(row)
            if (c or "").strip()
            and _numeric_cell_value(c) is None
            and _normalize_label(c)
        ]
        if not numcells:
            if labels_in_row:
                # rightmost label ≈ the label sitting over the data
                # columns (left-side cells are EVDRE control columns)
                section = labels_in_row[-1][1]
            continue
        for ci, v, raw in numcells:
            lefts = [lb for lci, lb in labels_in_row if lci < ci]
            cells.append(
                {
                    "row_label": lefts[-1] if lefts else "",
                    "section": section,
                    "col_header": header_for(ci, ri),
                    "value": v,
                    "raw": raw,
                }
            )
    return cells


def _build_key_index(
    pages: list[Any],
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    """Map anchor key → {values: set, page_num, raw} over all sheets."""
    idx: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for name, pnum, grid in _iter_visible_sheets(pages):
        for c in _index_grid(grid):
            key = (name, c["section"], c["row_label"], c["col_header"])
            slot = idx.setdefault(
                key, {"values": set(), "page_num": pnum, "raw": c["raw"]}
            )
            slot["values"].add(round(float(c["value"]), 2))
    return idx


# ─────────────────────────── provenance recovery ───────────────────────────

_TOKEN_DROP = {"", "usd", "usali", "p", "and", "l", "pct", "annual", "monthly", "total"}
_TOKEN_SYNONYMS = {
    "fb": ("food", "beverage", "f&b"),
    "adr": ("average daily rate",),
    "revpar": ("revenue per av",),
    "gop": ("gross operating",),
    "noi": ("net operating",),
    "occupancy": ("occup",),
}


def _label_match(field_name: str, key: tuple[str, str, str, str]) -> bool:
    """True when a field-name token appears in the anchor's labels.

    Matches against row_label + col_header only — the section label is
    deliberately excluded because generic tokens ("revenues") appear in
    the section of EVERY key in that block and would defeat narrowing.
    """
    toks = set(re.split(r"[^a-z&]+", field_name.lower())) - _TOKEN_DROP
    hay = " ".join((key[2], key[3]))
    for t in toks:
        if t and t in hay:
            return True
        for s in _TOKEN_SYNONYMS.get(t, ()):
            if s in hay:
                return True
    return False


def learn_mapping(
    pages: list[Any], fields: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, int]]:
    """Recover cell provenance for extracted numeric fields.

    Returns ``(entries, stats)`` where ``entries`` maps field_name →
    ``{"keys": [[sheet, section, row_label, col_header], ...],
    "scale": float, "unit": str|None}`` and stats counts
    numeric/matched/ambiguous/nomatch.
    """
    idx = _build_key_index(pages)
    by_value: dict[float, set[tuple[str, str, str, str]]] = {}
    for key, slot in idx.items():
        for v in slot["values"]:
            by_value.setdefault(v, set()).add(key)

    entries: dict[str, Any] = {}
    stats = {"numeric": 0, "matched": 0, "ambiguous": 0, "nomatch": 0}
    for f in fields:
        name = f.get("field_name")
        v = f.get("value")
        if isinstance(v, str):
            v = _numeric_cell_value(v)
        if not name or isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        stats["numeric"] += 1
        target = round(float(v), 2)
        if target == 0:
            stats["ambiguous"] += 1
            continue
        # (grid_value, mapping_scale): mapping stores grid → field scale
        # (``field_value = grid_value * scale``). The ×100/÷100 variants
        # bridge the percent↔ratio mismatch: the extractor emits
        # occupancy/margins as a 0..1 ratio (0.74, schema ge=0,le=1) but
        # the grid cell reads ``74%`` → ``74.0`` (see _numeric_cell_value),
        # so a ratio field only matches its grid cell at scale 0.01.
        candidates: list[tuple[tuple[str, str, str, str], float]] = []
        for grid_val, scale in (
            (target, 1.0),
            (round(target / 1000.0, 2), 1000.0),
            (round(target * 1000.0, 2), 0.001),
            (round(target / 100.0, 2), 100.0),
            (round(target * 100.0, 2), 0.01),
        ):
            for key in by_value.get(grid_val, ()):
                candidates.append((key, scale))
        # anchors must be unique in the source sheet to be stable
        keys = {k for k, _ in candidates if len(idx[k]["values"]) == 1}
        if not keys:
            stats["nomatch"] += 1
            continue
        scales = {s for k, s in candidates if k in keys}
        if len(scales) > 1:
            if 1.0 in scales:
                keys = {k for k, s in candidates if k in keys and s == 1.0}
                scales = {1.0}
            else:
                stats["ambiguous"] += 1
                continue
        if len(keys) > 1:
            # narrow by field-name tokens; if narrowing fails keep ALL
            # matches as consensus anchors (sibling must agree)
            narrowed = {k for k in keys if _label_match(name, k)}
            if narrowed:
                keys = narrowed
        if len(keys) > _MAX_ANCHORS:
            stats["ambiguous"] += 1
            continue
        entries[name] = {
            "keys": sorted(list(k) for k in keys),
            "scale": next(iter(scales)),
            "unit": f.get("unit"),
        }
        stats["matched"] += 1
    return entries, stats


# ─────────────────────────── sibling application ───────────────────────────


def apply_mapping(
    pages: list[Any], entries: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Apply a learned mapping to a sibling workbook's parsed pages.

    Label-anchored (never coordinates), so inserted rows don't break
    lookups. Every anchor key found in the sibling must resolve to the
    SAME value, else the field is dropped (consensus rule).

    Returns ``(fields, stats)`` — fields in the extraction envelope
    shape with confidence 0.95.
    """
    idx = _build_key_index(pages)
    fields: list[dict[str, Any]] = []
    stats = {"found": 0, "conflict": 0, "absent": 0}
    for name, entry in entries.items():
        resolved: set[float] = set()
        page_num = None
        raw = None
        for key in entry.get("keys", ()):
            slot = idx.get(tuple(key))
            if slot is not None:
                resolved |= slot["values"]
                if page_num is None:
                    page_num = slot["page_num"]
                    raw = slot["raw"]
        if not resolved:
            stats["absent"] += 1
            continue
        if len(resolved) != 1:
            stats["conflict"] += 1
            continue
        value = next(iter(resolved)) * float(entry.get("scale") or 1.0)
        # Belt-and-braces: a known 0..1 ratio metric that still lands
        # above 1.5 came from a percent cell (74.0) whose scale wasn't
        # applied — treat it as a percent and divide back to a ratio.
        if _is_ratio_field(name) and value > 1.5:
            value = value / 100.0
        anchor = entry["keys"][0]
        fields.append(
            {
                "field_name": name,
                "value": value,
                "unit": entry.get("unit"),
                "source_page": page_num,
                "confidence": _SIBLING_CONFIDENCE,
                "raw_text": f"{anchor[2]} ... {raw}",
            }
        )
        stats["found"] += 1
    return fields, stats


# ─────────────────────────── verification gate ───────────────────────────


def _usali_score_for(fields: list[dict[str, Any]]) -> float | None:
    """USALI identity score over an extraction envelope (None = inconclusive)."""
    from .usali_scorer import flatten_extraction_fields, score_extraction

    flat = flatten_extraction_fields(fields)
    return score_extraction(flat).score


def passes_gates(
    *,
    entries: dict[str, Any],
    applied_fields: list[dict[str, Any]],
    apply_stats: dict[str, int],
    source_usali_score: float | None,
) -> tuple[bool, str]:
    """Coverage + USALI identity gates. Returns (ok, reason)."""
    total = len(entries)
    found = apply_stats.get("found", 0)
    if total == 0:
        return False, "empty_mapping"
    coverage = found / total
    if coverage < COVERAGE_FLOOR:
        return False, (
            f"coverage {found}/{total} ({coverage:.0%}) below "
            f"{COVERAGE_FLOOR:.0%} floor"
        )
    if source_usali_score is not None:
        sibling_score = _usali_score_for(applied_fields)
        if sibling_score is None:
            return False, (
                f"sibling USALI score inconclusive vs source "
                f"{source_usali_score:.1f}"
            )
        if sibling_score < source_usali_score - USALI_SCORE_DROP_TOLERANCE:
            return False, (
                f"sibling USALI score {sibling_score:.1f} more than "
                f"{USALI_SCORE_DROP_TOLERANCE:.0f} points below source "
                f"{source_usali_score:.1f}"
            )
    return True, f"coverage {found}/{total} ({coverage:.0%})"


# ─────────────────────────── persistence orchestrators ───────────────────────────
#
# Both functions take the caller's AsyncSession and keep every query
# tenant-scoped (tenant_middleware requires the predicate). SQL is
# dialect-neutral (works on Postgres and the SQLite dev path).


async def _fingerprint_for(session, tenant_id: str, doc_id: str) -> str | None:
    """The workbook's stored ``template_fingerprint`` (or None).

    Shared preamble for both orchestrators below — keeps the
    tenant-scoping predicate (``tenant_middleware`` requires it) in one
    place so the two paths can never drift on how they resolve a doc's
    fingerprint.
    """
    from sqlalchemy import text

    row = (
        await session.execute(
            text(
                "SELECT template_fingerprint FROM documents "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": str(doc_id), "tenant": str(tenant_id)},
        )
    ).first()
    return row._mapping["template_fingerprint"] if row else None


async def maybe_learn_mapping(
    session,
    *,
    tenant_id: str,
    doc_id: str,
    doc_type: str | None,
    fields: list[dict[str, Any]],
    extraction_data: dict[str, Any] | None,
) -> None:
    """After a successful LLM extraction, persist a template mapping.

    No-op when: the doc has no fingerprint (non-workbook / legacy
    parse), a mapping already exists for (tenant, fingerprint), or
    provenance recovery finds fewer than ``MIN_MAPPING_FIELDS`` fields.
    Best-effort: never raises.
    """
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy import text

    try:
        if not fields or not isinstance(extraction_data, dict):
            return
        pages = extraction_data.get("pages") or []
        if not pages:
            return
        fingerprint = await _fingerprint_for(session, tenant_id, doc_id)
        if not fingerprint:
            return
        existing = (
            await session.execute(
                text(
                    "SELECT id FROM template_mappings "
                    "WHERE tenant_id = :tenant AND fingerprint = :fp "
                    "LIMIT 1"
                ),
                {"tenant": str(tenant_id), "fp": fingerprint},
            )
        ).first()
        if existing is not None:
            return
        entries, stats = learn_mapping(pages, fields)
        if len(entries) < MIN_MAPPING_FIELDS:
            logger.info(
                "sibling template: not persisting mapping for doc=%s "
                "fingerprint=%s — only %d/%d numeric fields recovered "
                "(floor %d). stats=%s",
                doc_id,
                fingerprint,
                len(entries),
                stats.get("numeric", 0),
                MIN_MAPPING_FIELDS,
                stats,
            )
            return
        # Score the SOURCE over the mapped subset so the sibling gate
        # compares apples to apples (the sibling only carries mapped
        # numeric fields, never the source's string fields).
        subset = [f for f in fields if f.get("field_name") in entries]
        try:
            source_score = _usali_score_for(subset)
        except Exception:  # noqa: BLE001 — scoring is additive
            source_score = None
        mapping_json = {
            "version": MAPPING_VERSION,
            "source_doc_id": str(doc_id),
            "source_doc_type": doc_type,
            "source_usali_score": source_score,
            "learn_stats": stats,
            "entries": entries,
        }
        await session.execute(
            text(
                "INSERT INTO template_mappings "
                "(id, tenant_id, fingerprint, source_doc_id, mapping_json, "
                " created_at) "
                "VALUES (:id, :tenant, :fp, :src, :mj, :created)"
            ),
            {
                "id": str(uuid4()),
                "tenant": str(tenant_id),
                "fp": fingerprint,
                "src": str(doc_id),
                "mj": json.dumps(mapping_json),
                "created": datetime.now(UTC),
            },
        )
        await session.commit()
        logger.info(
            "sibling template: learned mapping fingerprint=%s source_doc=%s "
            "fields=%d (of %d numeric; %d ambiguous dropped) "
            "source_usali_score=%s",
            fingerprint,
            doc_id,
            len(entries),
            stats.get("numeric", 0),
            stats.get("ambiguous", 0),
            f"{source_score:.1f}" if source_score is not None else "n/a",
        )
    except Exception:  # noqa: BLE001 — learning must never break extraction
        with contextlib.suppress(Exception):
            await session.rollback()
        logger.exception(
            "sibling template: mapping learn failed for doc=%s (non-fatal)",
            doc_id,
        )


async def try_sibling_reuse(
    session,
    *,
    tenant_id: str,
    doc_id: str,
    extraction_data: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], str | None] | None:
    """Attempt a zero-LLM extraction from a stored sibling mapping.

    Returns ``(fields, confidence_report, source_doc_type)`` on a HIT
    that passes the verification gates, else ``None`` (caller falls
    through to the LLM). Never raises.
    """
    from sqlalchemy import text

    try:
        if not isinstance(extraction_data, dict):
            return None
        pages = extraction_data.get("pages") or []
        if not pages:
            return None
        fingerprint = await _fingerprint_for(session, tenant_id, doc_id)
        if not fingerprint:
            return None
        mrow = (
            await session.execute(
                text(
                    "SELECT source_doc_id, mapping_json FROM template_mappings "
                    "WHERE tenant_id = :tenant AND fingerprint = :fp "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"tenant": str(tenant_id), "fp": fingerprint},
            )
        ).first()
        if mrow is None:
            logger.info(
                "sibling template MISS: doc=%s fingerprint=%s reason=no_mapping",
                doc_id,
                fingerprint,
            )
            return None
        raw_mj = mrow._mapping["mapping_json"]
        mapping_json = json.loads(raw_mj) if isinstance(raw_mj, str) else raw_mj
        # A version bump must actually invalidate stale mappings: skip
        # any mapping whose stored ``version`` predates the current
        # anchor/scale semantics rather than applying it blind (a
        # version lever that silently does nothing is a footgun).
        mapping_version = mapping_json.get("version")
        if mapping_version != MAPPING_VERSION:
            logger.info(
                "sibling template MISS: doc=%s fingerprint=%s "
                "reason=stale_mapping_version (%s != %s)",
                doc_id,
                fingerprint,
                mapping_version,
                MAPPING_VERSION,
            )
            return None
        source_doc_id = mrow._mapping["source_doc_id"]
        if str(source_doc_id) == str(doc_id):
            # A reprocess of the source doc itself must re-run the LLM
            # (its own mapping proves nothing about itself).
            logger.info(
                "sibling template MISS: doc=%s fingerprint=%s reason=self_source",
                doc_id,
                fingerprint,
            )
            return None
        entries = mapping_json.get("entries") or {}
        applied_fields, apply_stats = apply_mapping(pages, entries)
        ok, reason = passes_gates(
            entries=entries,
            applied_fields=applied_fields,
            apply_stats=apply_stats,
            source_usali_score=mapping_json.get("source_usali_score"),
        )
        if not ok:
            logger.info(
                "sibling template FALLBACK: doc=%s fingerprint=%s "
                "source_doc=%s reason=%s (stats=%s)",
                doc_id,
                fingerprint,
                source_doc_id,
                reason,
                apply_stats,
            )
            return None
        logger.info(
            "sibling template HIT: doc=%s fingerprint=%s source_doc=%s "
            "fields=%d zero LLM cost (%s)",
            doc_id,
            fingerprint,
            source_doc_id,
            len(applied_fields),
            reason,
        )
        confidence = {
            "overall": _SIBLING_CONFIDENCE,
            "by_field": {
                f["field_name"]: _SIBLING_CONFIDENCE for f in applied_fields
            },
            "low_confidence_fields": [],
            "requires_human_review": False,
        }
        return (
            applied_fields,
            confidence,
            mapping_json.get("source_doc_type"),
        )
    except Exception:  # noqa: BLE001 — reuse must never fail an extraction
        with contextlib.suppress(Exception):
            await session.rollback()
        logger.exception(
            "sibling template FALLBACK: doc=%s reason=exception (non-fatal)",
            doc_id,
        )
        return None


__all__ = [
    "COVERAGE_FLOOR",
    "MIN_MAPPING_FIELDS",
    "SIBLING_AGENT_VERSION_BASE",
    "USALI_SCORE_DROP_TOLERANCE",
    "apply_mapping",
    "compute_template_fingerprint",
    "learn_mapping",
    "maybe_learn_mapping",
    "passes_gates",
    "try_sibling_reuse",
]
