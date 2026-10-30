"""FON-96 — fill a deal's market / submarket from an Offering Memorandum,
without guessing.

Runs after the OM's metadata sync (``api/documents.py``). The deal must have
no market at all (``market IS NULL AND str_market_id IS NULL``) and no
analyst claim on the field (``market_source <> 'analyst'``). Then:

* **market** — every location phrase the OM emits (``property_overview.city``
  / ``.location`` / ``.submarket``, each also combined with
  ``property_overview.state`` when the phrase carries no state) is normalised
  with :func:`market_name_key` and compared for EXACT equality with the
  normalised name of every **metro** market in the loaded list
  (``"Northhaven, TS"`` ≡ ``"Northhaven - TS USA"``). Regional markets —
  named in words — are never matched from a city.
* **submarket** — only once the market matched uniquely: the OM's
  ``property_overview.submarket`` phrase, normalised, must equal exactly one
  of that market's submarket names.
* Exactly one candidate at a level → written; zero or two → nothing at that
  level, logged ``market_match: none`` / ``market_match: ambiguous``. The
  market is never written when ambiguous, and the submarket is never written
  without the market.

Provenance: ``market_source = 'om_exact_match'`` and ``market_source_detail``
(document id, page, the matched phrases) in the same UPDATE, whose predicate
re-checks the fill-only rule so an analyst's value is never overwritten, even
by a race; one ``audit_log`` row ``deal.market_matched_from_extraction``.

Licence gate (default closed): the fill runs only when the deal's tenant may
be served the list (:func:`app.services.str_markets.may_serve_str_markets` —
internal, or external with both switches on). For a withheld or unregistered
tenant nothing is written and ``market_match: withheld`` is logged; the
canonical name, key and market type never reach a workspace the list is
withheld from.

Best-effort: any failure is logged and the extraction completes — a market
suggestion is never a gate on the document. Reads extraction OUTPUT rows
only; nothing here touches ``app/extraction`` or ``app/agents``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

MARKET_SOURCE_ANALYST = "analyst"
MARKET_SOURCE_OM_EXACT_MATCH = "om_exact_match"
AUDIT_ACTION = "deal.market_matched_from_extraction"

#: OM fields read as a possible "<City>, <ST>" phrase, in priority order.
_LOCATION_FIELDS: tuple[str, ...] = (
    "property_overview.city",
    "property_overview.location",
    "property_overview.submarket",
)
_SUBMARKET_FIELD = "property_overview.submarket"
_STATE_FIELD = "property_overview.state"


def market_name_key(name: str) -> str:
    """Comparison key shared by the list's market names and the OM's phrases:
    lowercase, periods dropped, ``, / -`` folded to spaces, whitespace
    collapsed, a trailing ``usa`` token removed — so ``"Northhaven - TS USA"``
    and ``"Northhaven, TS"`` compare equal. The web's ``locationKey`` has the
    same shape (minus the ``usa`` strip, which the web never sees)."""
    key = str(name or "").lower().replace(".", "")
    key = re.sub(r"[,/\-]+", " ", key)
    key = " ".join(key.split())
    if key.endswith(" usa"):
        key = key[: -len(" usa")].strip()
    return key


@dataclass(frozen=True)
class _Phrase:
    text: str
    field: str
    page: int | None


def _coerce_page(f: dict[str, Any]) -> int | None:
    for k in ("source_page", "page_number", "page"):
        v = f.get(k)
        if v is None:
            continue
        with contextlib.suppress(TypeError, ValueError):
            return int(v)
    return None


def _phrases(fields: list[dict[str, Any]], names: tuple[str, ...]) -> list[_Phrase]:
    out: list[_Phrase] = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        name = (f.get("field_name") or "").strip().lower()
        if name not in names:
            continue
        value = f.get("value")
        if not isinstance(value, str) or not value.strip():
            continue
        out.append(_Phrase(text=" ".join(value.split()), field=name, page=_coerce_page(f)))
    return out


def om_market_phrases(fields: list[dict[str, Any]]) -> list[_Phrase]:
    """The OM phrases a metro market name may equal: each location field as
    written, plus ``"<phrase>, <state>"`` when a state field exists and the
    phrase carries none (``"Southport"`` + ``"TZ"`` → ``"Southport, TZ"``)."""
    phrases = _phrases(fields, _LOCATION_FIELDS)
    states = [p.text for p in _phrases(fields, (_STATE_FIELD,))]
    out: list[_Phrase] = list(phrases)
    for p in phrases:
        if re.search(r"\b[A-Za-z]{2}$", p.text) and ("," in p.text or " - " in p.text):
            continue  # already "<City>, <ST>"-shaped
        for st in states:
            out.append(_Phrase(text=f"{p.text}, {st}", field=p.field, page=p.page))
    return out


async def _rollback(session: AsyncSession, what: str) -> None:
    try:
        await session.rollback()
    except Exception:
        logger.warning("market_match: rollback after %s failed", what, exc_info=True)


async def match_market_from_om(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
    fields: list[dict[str, Any]],
    doc_type: str | None,
    doc_id: str | None,
) -> None:
    """The fill step described in the module docstring. The caller has
    already applied the FON-84 gate (OM only)."""
    from ..api.deals import _deals_has_market_column, _deals_has_market_source
    from .str_markets import has_market_type_column, may_serve_str_markets

    try:
        for col in ("str_market_id", "market", "submarket", "submarket_id"):
            if not await _deals_has_market_column(session, col):
                return
        if not await _deals_has_market_source(session):
            return
        if not await has_market_type_column(session):
            return
        served, why = await may_serve_str_markets(session, tenant_id)
        if not served:
            logger.info("market_match: withheld deal=%s tenant=%s (%s)", deal_id, tenant_id, why)
            return

        row = (
            await session.execute(
                text(
                    "SELECT market, str_market_id, submarket, submarket_id, market_source "
                    "FROM deals WHERE id = :id AND tenant_id = :tenant"
                ),
                {"id": deal_id, "tenant": str(tenant_id)},
            )
        ).first()
        if row is None:
            return
        cur = row._mapping
        if cur.get("market") is not None or cur.get("str_market_id") is not None:
            logger.info("market_match: kept deal=%s (market already set)", deal_id)
            return
        if cur.get("market_source") == MARKET_SOURCE_ANALYST:
            logger.info("market_match: kept deal=%s (analyst claim on the field)", deal_id)
            return

        phrases = om_market_phrases(fields)
        if not phrases:
            return

        # Reference data — no tenant predicate by design (services/str_markets.py).
        metros = (
            await session.execute(
                text(
                    "SELECT str_market_id, market_name FROM str_markets "
                    "WHERE submarket_id IS NULL AND market_type = 'metro'"
                )
            )
        ).fetchall()
        by_key: dict[str, dict[str, str]] = {}
        for m in metros:
            by_key.setdefault(market_name_key(m[1]), {})[str(m[0])] = str(m[1])

        candidates: dict[str, str] = {}
        matched_phrase: _Phrase | None = None
        for p in phrases:
            hit = by_key.get(market_name_key(p.text))
            if not hit:
                continue
            for key, name in hit.items():
                if key not in candidates:
                    candidates[key] = name
                    if matched_phrase is None:
                        matched_phrase = p
        if not candidates:
            logger.info(
                "market_match: none deal=%s phrases=%s", deal_id, [p.text for p in phrases][:6],
            )
            return
        if len(candidates) > 1 or matched_phrase is None:
            logger.info(
                "market_match: ambiguous deal=%s candidates=%s", deal_id, sorted(candidates.values()),
            )
            return
        (market_key, market_name), = candidates.items()

        # Submarket — exactly one of this market's own, fill-only.
        submarket_key: str | None = None
        submarket_name: str | None = None
        submarket_phrase: _Phrase | None = None
        submarket_match = "not_attempted"
        sub_phrases = _phrases(fields, (_SUBMARKET_FIELD,))
        if sub_phrases and cur.get("submarket") is None and cur.get("submarket_id") is None:
            subs = (
                await session.execute(
                    text(
                        "SELECT submarket_id, submarket_name FROM str_markets "
                        "WHERE str_market_id = :mid AND submarket_id IS NOT NULL"
                    ),
                    {"mid": market_key},
                )
            ).fetchall()
            sub_by_key: dict[str, dict[str, str]] = {}
            for srow in subs:
                sub_by_key.setdefault(market_name_key(srow[1]), {})[str(srow[0])] = str(srow[1])
            sub_candidates: dict[str, str] = {}
            for p in sub_phrases:
                hit = sub_by_key.get(market_name_key(p.text))
                if not hit:
                    continue
                for key, name in hit.items():
                    if key not in sub_candidates:
                        sub_candidates[key] = name
                        if submarket_phrase is None:
                            submarket_phrase = p
            if len(sub_candidates) == 1 and submarket_phrase is not None:
                (submarket_key, submarket_name), = sub_candidates.items()
                submarket_match = "exact"
            else:
                submarket_match = "ambiguous" if sub_candidates else "none"
                submarket_phrase = sub_phrases[0]
                logger.info(
                    "market_match: submarket %s deal=%s market=%s phrase=%r",
                    submarket_match, deal_id, market_name, submarket_phrase.text,
                )

        detail: dict[str, Any] = {
            "document_id": str(doc_id) if doc_id else None,
            "doc_type": doc_type,
            "market_phrase": matched_phrase.text,
            "market_field": matched_phrase.field,
            "market_page": matched_phrase.page,
            "submarket_phrase": submarket_phrase.text if submarket_phrase else None,
            "submarket_field": submarket_phrase.field if submarket_phrase else None,
            "submarket_page": submarket_phrase.page if submarket_phrase else None,
            "submarket_match": submarket_match,
            "rule": "exact normalised match on a metro market name; submarket only within the matched market",
        }
        is_sqlite = session.bind is not None and session.bind.dialect.name == "sqlite"
        detail_sql = ":detail" if is_sqlite else "CAST(:detail AS JSONB)"
        updated_at_sql = "updated_at = CURRENT_TIMESTAMP" if is_sqlite else "updated_at = NOW()"
        sets = [
            "market = :market", "str_market_id = :mid",
            "market_source = :source", f"market_source_detail = {detail_sql}", updated_at_sql,
        ]
        params: dict[str, Any] = {
            "market": market_name, "mid": market_key,
            "source": MARKET_SOURCE_OM_EXACT_MATCH, "detail": json.dumps(detail),
            "id": deal_id, "tenant": str(tenant_id), "analyst": MARKET_SOURCE_ANALYST,
        }
        where_sub = ""
        if submarket_key is not None:
            sets += ["submarket = :sub", "submarket_id = :sid"]
            params.update({"sub": submarket_name, "sid": submarket_key})
            where_sub = " AND submarket IS NULL AND submarket_id IS NULL"
        result = await session.execute(
            text(
                f"UPDATE deals SET {', '.join(sets)} "
                "WHERE id = :id AND tenant_id = :tenant "
                "AND market IS NULL AND str_market_id IS NULL "
                f"AND (market_source IS NULL OR market_source <> :analyst){where_sub}"
            ),
            params,
        )
        await session.commit()
        if not result.rowcount:
            logger.info("market_match: kept deal=%s (changed under us)", deal_id)
            return
    except Exception:
        logger.exception("market_match: failed for deal=%s", deal_id)
        await _rollback(session, "market match")
        return

    try:
        from ..audit import log_audit

        await log_audit(
            session,
            tenant_id=tenant_id,
            action=AUDIT_ACTION,
            resource_type="deal",
            resource_id=deal_id,
            input_payload={
                "market": market_name,
                "market_key": market_key,
                "submarket": submarket_name,
                "submarket_key": submarket_key,
                "doc_type": doc_type,
                "document_id": str(doc_id) if doc_id else None,
                "mode": "fill",
                "source": MARKET_SOURCE_OM_EXACT_MATCH,
                "detail": detail,
                "rule": "FON-96 — OM location phrase = exactly one metro market name, fill-only",
            },
        )
        await session.commit()
    except Exception:
        logger.warning("market_match: audit write failed for deal=%s", deal_id)
        await _rollback(session, "market match audit")
    logger.info(
        "market_match: deal=%s market=%r submarket=%r from OM phrase %r",
        deal_id, market_name, submarket_name, matched_phrase.text,
    )


__all__ = [
    "AUDIT_ACTION",
    "MARKET_SOURCE_ANALYST",
    "MARKET_SOURCE_OM_EXACT_MATCH",
    "market_name_key",
    "match_market_from_om",
    "om_market_phrases",
]
