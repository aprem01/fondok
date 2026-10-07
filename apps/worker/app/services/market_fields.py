"""Shared field-row plumbing for the Market tab readers.

The Market overview reads raw ``extraction_results`` rows (one JSON list of
``{field_name, value, unit, source_page, ...}`` dicts per extraction) for
two document families — STR / STR_TREND (comp set, TTM blend) and
MARKET_STUDY (demand / supply growth). Both readers want the same thing:
every field as one flat row that still knows WHICH document and page it
came from, so each displayed number can name its source (FON-61 E-007 /
E-008 / E-009 — "provenance must name the document and field").

Pure helpers — no DB, no I/O.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FieldRow:
    """One extracted field with its document identity.

    ``extraction_id`` groups rows back into the extraction they came from
    (one report = one extraction) — the comp-set roster must come from ONE
    report, never a by-index merge of two.
    """

    field_name: str
    value: Any
    unit: str | None
    page: int | None
    doc_name: str | None
    doc_id: str | None
    extraction_id: str | None
    doc_type: str | None = None
    # The extractor's optional period tag (CBRE-style ``actual`` / ``forecast``).
    period: str | None = None

    @property
    def lname(self) -> str:
        return self.field_name.strip().lower()


@dataclass(frozen=True)
class FieldRef:
    """The provenance of ONE input: document + field + page + value as read."""

    field_name: str
    value: Any
    doc_name: str | None
    doc_id: str | None
    page: int | None

    @classmethod
    def of(cls, row: FieldRow, value: Any | None = None) -> FieldRef:
        return cls(
            field_name=row.field_name,
            value=row.value if value is None else value,
            doc_name=row.doc_name,
            doc_id=row.doc_id,
            page=row.page,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "value": self.value,
            "doc_name": self.doc_name,
            "doc_id": self.doc_id,
            "page": self.page,
        }


def coerce_float(v: Any) -> float | None:
    """``"4.2%"`` / ``"$1,234"`` / ``1234`` → float; anything else → None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        cleaned = v.replace(",", "").replace("$", "").replace("%", "").strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def coerce_int(v: Any) -> int | None:
    f = coerce_float(v)
    if f is None:
        return None
    try:
        return round(f)
    except (OverflowError, ValueError):
        return None


def coerce_page(v: Any) -> int | None:
    p = coerce_int(v)
    return p if p is not None and p >= 1 else None


def pct_points(v: Any) -> float | None:
    """A growth / share value as PERCENT POINTS.

    The extractor convention is a 0..1 decimal (``0.042`` for 4.2%), but
    rows also arrive as whole percents (``4.2``) and strings (``"4.2%"``).
    Same normalization rule the web uses for cap rates (``v <= 1 → v * 100``):
    a magnitude at or below 1.0 is read as a fraction.
    """
    f = coerce_float(v)
    if f is None:
        return None
    return f * 100.0 if abs(f) <= 1.0 else f


def doc_type_from_agent_version(agent_version: str | None) -> str | None:
    """The ``dt:<doc_type>`` segment of an ``extraction_results.agent_version``.

    Mirrors ``api.documents._parse_doc_type_from_agent_version`` (format
    ``router:{route};dt:{doc_type};extractor;ps=…;reg=…;pv=vN``) without
    importing the documents router. None for legacy / mock rows.
    """
    if not agent_version:
        return None
    for segment in str(agent_version).split(";"):
        segment = segment.strip()
        if segment.startswith("dt:"):
            return segment[len("dt:"):].strip().upper() or None
    return None


MARKET_STUDY_FIELD_PREFIXES = ("market_study.", "pnl_benchmark.market.")


def extraction_lane(
    *,
    doc_type: str | None,
    agent_version: str | None,
    ai_proposed_doc_type: str | None = None,
    field_names: Iterable[str] = (),
) -> str | None:
    """Which Market-tab reader an extraction belongs to — by the LANE it was
    extracted in, not the analyst's document tag.

    Live case (FON-61): the tester's CoStar reports carry the analyst's
    ``doc_type = 'STR_TREND'`` tag while their extraction ran in the
    MARKET_STUDY lane (``agent_version`` ``dt:MARKET_STUDY``, fields
    ``market_study.*`` / ``pnl_benchmark.market.*``). Selecting by the tag
    answered ``no_document`` for a deal that had both reports.

    ``"MARKET_STUDY"`` when the tag, the ``dt:`` stamp, the router's
    proposal or the field namespaces say so; ``"STR"`` for an STR /
    STR_TREND tag or stamp that is NOT a market study; None otherwise.
    """
    tag = (doc_type or "").strip().upper()
    stamp = doc_type_from_agent_version(agent_version)
    proposed = (ai_proposed_doc_type or "").strip().upper()
    if "MARKET_STUDY" in {tag, stamp, proposed}:
        return "MARKET_STUDY"
    if any(str(n).strip().lower().startswith(MARKET_STUDY_FIELD_PREFIXES) for n in field_names):
        return "MARKET_STUDY"
    if tag in {"STR", "STR_TREND"} or stamp in {"STR", "STR_TREND"}:
        return "STR"
    return None


def parse_extraction_records(records: Iterable[Mapping[str, Any]]) -> list[FieldRow]:
    """Flatten DB extraction records (newest first) into ``FieldRow``s.

    Each record carries ``fields`` (JSON text or list), ``document_id``,
    ``filename``, ``doc_type`` and ``extraction_id``. Malformed JSON or a
    non-list payload skips that record rather than failing the overview.
    Row order is preserved, so "first hit wins" over the result equals
    "newest extraction wins".
    """
    out: list[FieldRow] = []
    for rec in records:
        raw = rec.get("fields")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw else None
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(raw, list):
            continue
        doc_id = rec.get("document_id")
        ext_id = rec.get("extraction_id")
        doc_name = rec.get("filename")
        doc_type = rec.get("doc_type")
        for f in raw:
            if not isinstance(f, dict):
                continue
            name = f.get("field_name")
            if not isinstance(name, str) or not name.strip():
                continue
            unit = f.get("unit")
            period = f.get("period")
            out.append(
                FieldRow(
                    field_name=name.strip(),
                    value=f.get("value"),
                    unit=str(unit).strip().lower() if isinstance(unit, str) and unit.strip() else None,
                    page=coerce_page(f.get("source_page") or f.get("page_number")),
                    doc_name=str(doc_name).strip() if isinstance(doc_name, str) and doc_name.strip() else None,
                    doc_id=str(doc_id) if doc_id is not None else None,
                    extraction_id=str(ext_id) if ext_id is not None else None,
                    doc_type=str(doc_type).upper() if isinstance(doc_type, str) else None,
                    period=str(period).strip().lower() if isinstance(period, str) and period.strip() else None,
                )
            )
    return out


__all__ = [
    "MARKET_STUDY_FIELD_PREFIXES",
    "FieldRef",
    "FieldRow",
    "coerce_float",
    "coerce_int",
    "coerce_page",
    "doc_type_from_agent_version",
    "extraction_lane",
    "parse_extraction_records",
    "pct_points",
]
