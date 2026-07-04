"""Deterministic template extractors for standardized report formats.

STR / CoStar Trend reports (and, later, CBRE Hotel Horizons) ship a
STANDARDIZED tab structure — every report from the vendor has the same
sheets with data in predictable positions. For those files a
deterministic parser extracts the same fields the LLM extractor would,
at $0 instead of ~$0.30/doc, and with zero hallucination risk.

Contract:

* ``try_template_extract(parsed, doc_type)`` returns ``None`` whenever
  the document doesn't match a known template with high confidence —
  the caller then falls through to the existing LLM extraction path.
  A false negative costs one LLM call; a false positive costs
  correctness, so every detector in this package biases HARD toward
  returning ``None``.
* On a hit, ``TemplateExtractResult.fields`` carries the exact same
  field shape the LLM extractor emits
  (``{"field_name": str, "value": Any, "confidence": float,
  "unit": str | None}``) using the canonical field-path namespace from
  ``app/agents/extraction_schemas/str_trend.md`` so every downstream
  consumer (market-data rollup, STR forecast loader, comp-set drift)
  works unchanged.
"""

from .str_trend import TemplateExtractResult, try_template_extract

__all__ = ["TemplateExtractResult", "try_template_extract"]
