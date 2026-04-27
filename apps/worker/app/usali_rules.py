"""USALI rule catalog loader.

The Variance, Normalizer, and Analyst agents all reason about USALI
P&L identities (RevPAR = Occupancy × ADR; GOP = Revenue − Departmental
− Undistributed; etc.) and the typical industry ranges (GOP margin
15-55%, FF&E reserve 3-5% of revenue, …).

The canonical catalog lives at
``evals/golden-set/usali-rules.csv``. We read it once, cache the parsed
rows, and expose:

* ``USALIRule`` — typed row (rule_id, name, severity, threshold range).
* ``load_usali_rules()`` — cached parser; returns the full list.
* ``rules_as_prompt_block()`` — formatted text suitable for embedding
  in a system prompt (LLM-readable summary, not the raw CSV).

Loading happens off the local filesystem; the path can be overridden
via ``FONDOK_USALI_RULES_PATH`` for tests or alternate catalogs.
"""

from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


# Default location: repo-root/evals/golden-set/usali-rules.csv. Resolved
# relative to this file so tests + production agree without a CWD
# dependency.
_DEFAULT_RULES_PATH = (
    Path(__file__).resolve().parents[3]
    / "evals"
    / "golden-set"
    / "usali-rules.csv"
)


@dataclass(frozen=True)
class USALIRule:
    """One row in the USALI catalog, parsed and lightly typed."""

    rule_id: str
    name: str
    category: str
    formula_or_check: str
    threshold_min: float | None
    threshold_max: float | None
    severity: str
    description: str

    def severity_norm(self) -> str:
        """Normalize severity to the Severity enum's canonical casing."""
        s = (self.severity or "").strip().upper()
        if s == "CRITICAL":
            return "Critical"
        if s == "WARN":
            return "Warn"
        return "Info"


def _parse_threshold(raw: str) -> float | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _resolve_path() -> Path:
    override = os.environ.get("FONDOK_USALI_RULES_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return _DEFAULT_RULES_PATH


@lru_cache(maxsize=1)
def load_usali_rules() -> list[USALIRule]:
    """Parse the USALI rules CSV. Cached for the process lifetime."""
    path = _resolve_path()
    if not path.exists():
        logger.warning("usali-rules: catalog not found at %s — returning empty list", path)
        return []
    rules: list[USALIRule] = []
    with path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rule_id = (row.get("rule_id") or "").strip()
            if not rule_id:
                continue
            rules.append(
                USALIRule(
                    rule_id=rule_id,
                    name=(row.get("name") or "").strip(),
                    category=(row.get("category") or "").strip(),
                    formula_or_check=(row.get("formula_or_check") or "").strip(),
                    threshold_min=_parse_threshold(row.get("threshold_min", "")),
                    threshold_max=_parse_threshold(row.get("threshold_max", "")),
                    severity=(row.get("severity") or "INFO").strip().upper(),
                    description=(row.get("description") or "").strip(),
                )
            )
    logger.info("usali-rules: loaded %d rules from %s", len(rules), path)
    return rules


def rules_as_prompt_block(rules: list[USALIRule] | None = None) -> str:
    """Format the rule catalog as a prompt-friendly text block.

    Each line is a single rule; the block is short enough to live inside
    a cached system prompt (~3-4k tokens for the full catalog) and
    LLM-readable without further structuring.
    """
    rules = rules if rules is not None else load_usali_rules()
    lines = ["=== USALI RULE CATALOG ==="]
    for r in rules:
        lo = "" if r.threshold_min is None else f"{r.threshold_min:g}"
        hi = "" if r.threshold_max is None else f"{r.threshold_max:g}"
        rng = f"[{lo}..{hi}]" if (lo or hi) else ""
        lines.append(
            f"- {r.rule_id} ({r.severity}) {r.name}: "
            f"{r.formula_or_check} {rng} — {r.description}"
        )
    return "\n".join(lines)


def rule_index() -> dict[str, USALIRule]:
    """Map ``rule_id`` → ``USALIRule`` for O(1) validator lookups."""
    return {r.rule_id: r for r in load_usali_rules()}


__all__ = [
    "USALIRule",
    "load_usali_rules",
    "rule_index",
    "rules_as_prompt_block",
]
