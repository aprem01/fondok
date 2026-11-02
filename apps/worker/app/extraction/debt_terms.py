"""Is an extraction a debt document, not a statement? (W5, 2026-11)

One predicate, shared by the post-extraction classifier
(``api.documents.refine_doc_type_post_extraction``, which retypes such an
extraction DEBT) and the T-12 loader's ranking
(``services.engine_runner._rank_pnl_shims``, which never lets one become the
primary statement — even when an analyst's P&L tag kept its doc_type).

Prod deal 76d10a64: ``Kimpton_Fondok_Debt_Upload_Template.xlsx`` was typed
T12 and extracted as 38 ``in_place_debt.*`` fields with no P&L line; as an
undated "current" T-12 it outranked every real P&L on the deal.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

#: Root namespaces of debt-term fields (the OM ``in_place_debt`` block and the
#: debt-document shapes the extractor emits for term sheets / templates).
DEBT_FIELD_ROOTS: frozenset[str] = frozenset(
    {"in_place_debt", "debt", "debt_terms", "loan", "loans", "financing", "senior_loan"}
)
#: Fewest debt-term fields that make an extraction a debt document.
MIN_DEBT_TERM_FIELDS = 3


def is_debt_terms_extraction(fields: Sequence[Any] | None) -> bool:
    """True when the extraction carries debt terms and no P&L statement line.

    A P&L line is any ``p_and_l_usali.*`` field with a numeric value; a debt
    term is any field under :data:`DEBT_FIELD_ROOTS`. A statement that also
    quotes its loan keeps its P&L type — only an extraction with zero P&L
    lines and at least :data:`MIN_DEBT_TERM_FIELDS` debt terms qualifies.
    """
    if not isinstance(fields, Sequence) or isinstance(fields, str | bytes):
        return False
    debt = 0
    for f in fields:
        if not isinstance(f, dict):
            continue
        name = str(f.get("field_name") or "").strip().lower()
        root = name.split(".", 1)[0]
        if root == "p_and_l_usali":
            v = f.get("value")
            if isinstance(v, int | float) and not isinstance(v, bool):
                return False
        elif root in DEBT_FIELD_ROOTS:
            debt += 1
    return debt >= MIN_DEBT_TERM_FIELDS


__all__ = ["DEBT_FIELD_ROOTS", "MIN_DEBT_TERM_FIELDS", "is_debt_terms_extraction"]
