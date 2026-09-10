"""Phase 0.2 -- prompt SHA + ontology registry version stamped into
``extraction_results.agent_version``.

Locks three things:

  1. The two new parsers (``ps=`` / ``reg=``), and that the pre-existing
     ``router:`` / ``dt:`` parsers ignore the new segments.
  2. ``_tag_agent_version`` segment ORDER -- ``;ps=...;reg=...`` goes in
     front of ``;pv=vN`` so the extraction cache's ``LIKE '%;pv=vN'``
     suffix filter keeps hitting. Proven against the real lookup query,
     not just a string check.
  3. ``prompt_sha`` reflects the instructions actually sent: the legacy
     SYSTEM_PROMPT and the EXTRACTOR_USE_DYNAMIC_SCHEMAS path hash
     differently, and the hash is stable when the text is unchanged.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

from app.api import documents as docs_module
from app.api.documents import (
    EXTRACTION_PIPELINE_VERSION,
    _parse_doc_type_from_agent_version,
    _parse_prompt_sha_from_agent_version,
    _parse_registry_version_from_agent_version,
    _parse_route_from_agent_version,
    _tag_agent_version,
)

_PV = f";pv={EXTRACTION_PIPELINE_VERSION}"
_FULL = f"router:extract;dt:PNL_MONTHLY;extractor;ps=0123abcd;reg=7{_PV}"
_STAMPED_RE = re.compile(
    r"^(?P<base>.+);ps=(?P<ps>[0-9a-f]{8});reg=(?P<reg>[^;]+);pv=(?P<pv>v\d+)$"
)


# ───────────────────────── parsers ─────────────────────────


def test_parse_prompt_sha_and_registry_version() -> None:
    assert _parse_prompt_sha_from_agent_version(_FULL) == "0123abcd"
    assert _parse_registry_version_from_agent_version(_FULL) == "7"


@pytest.mark.parametrize(
    "legacy",
    [
        "router:extract;dt:PNL_MONTHLY;extractor;pv=v1",
        "router:extractor;extractor;pv=v3",
        "mock-evals;pv=v3",
        "template:sibling:v1;pv=v1",
        "template:str_trend:v1;pv=v1",
    ],
)
def test_parsers_return_none_on_legacy_rows(legacy: str) -> None:
    assert _parse_prompt_sha_from_agent_version(legacy) is None
    assert _parse_registry_version_from_agent_version(legacy) is None


@pytest.mark.parametrize("av", [None, "", "ps=", "reg=", ";;", "ps=;reg=;pv=v1"])
def test_parsers_handle_empty_and_malformed(av: str | None) -> None:
    assert _parse_prompt_sha_from_agent_version(av) is None
    assert _parse_registry_version_from_agent_version(av) is None


def test_route_and_doc_type_parsers_unaffected_by_new_segments() -> None:
    assert _parse_route_from_agent_version(_FULL) == "extract"
    assert _parse_doc_type_from_agent_version(_FULL) == "PNL_MONTHLY"
    # ps=/reg= tokens never masquerade as a route or doc_type, even when
    # they are the only segments present.
    bare = f"ps=0123abcd;reg=7{_PV}"
    assert _parse_route_from_agent_version(bare) is None
    assert _parse_doc_type_from_agent_version(bare) is None
    # And the legacy-row behaviour the FON-18 tests lock is unchanged.
    legacy = "router:extractor;extractor;pv=v3"
    assert _parse_route_from_agent_version(legacy) == "extractor"
    assert _parse_doc_type_from_agent_version(legacy) is None


# ───────────────────── _tag_agent_version ─────────────────────


def test_tag_agent_version_segment_order_ends_with_pv() -> None:
    from app.agents.extractor import PROMPT_SHA

    tagged = _tag_agent_version("router:T12;dt:T12;extractor")
    m = _STAMPED_RE.match(tagged)
    assert m, tagged
    assert m["base"] == "router:T12;dt:T12;extractor"
    # No extractor-supplied SHA on the base -> the default instructions
    # in effect are stamped (a code-version stamp, like pv).
    assert m["ps"] == PROMPT_SHA
    assert m["reg"] == docs_module._current_registry_version()
    assert m["pv"] == EXTRACTION_PIPELINE_VERSION
    # The spec's exact shape: ``;ps=...;reg=<v>;pv=v1`` -- and it ENDS
    # with ``;pv=v1`` so the cache's suffix filter still matches.
    assert tagged == f"router:T12;dt:T12;extractor;ps={PROMPT_SHA};reg={m['reg']}{_PV}"
    assert tagged.endswith(_PV)


def test_tag_agent_version_keeps_extractor_supplied_prompt_sha() -> None:
    """The graph path hands over ``...;extractor;ps=<sha of the instructions
    actually sent>``; the tagger must keep it, not overwrite it with the
    module default, and must not stamp a second ``ps=``."""
    reg = docs_module._current_registry_version()
    tagged = _tag_agent_version("router:extract;dt:OM;extractor;ps=feedbeef")
    assert tagged == f"router:extract;dt:OM;extractor;ps=feedbeef;reg={reg}{_PV}"
    assert _parse_prompt_sha_from_agent_version(tagged) == "feedbeef"
    assert tagged.count(";ps=") == 1


def test_tag_agent_version_explicit_prompt_sha_argument() -> None:
    reg = docs_module._current_registry_version()
    assert _tag_agent_version("mock-evals", prompt_sha="abcdef01") == (
        f"mock-evals;ps=abcdef01;reg={reg}{_PV}"
    )


def test_tag_agent_version_is_idempotent() -> None:
    once = _tag_agent_version("template:sibling:v1")
    assert _tag_agent_version(once) == once
    assert once.count(";pv=") == 1
    assert once.count(";reg=") == 1
    assert once.count(";ps=") == 1


def test_registry_version_stamp_defaults_to_zero_until_registry_lands() -> None:
    reg = docs_module._current_registry_version()
    assert reg and ";" not in reg
    try:
        import app.ontology.registry  # noqa: F401
    except ImportError:
        # Soft-import contract: no registry package yet -> ``reg=0``.
        assert reg == "0"
        assert _tag_agent_version("x").endswith(f";reg=0{_PV}")


def test_registry_version_stamp_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> int:
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(docs_module, "registry_version", _boom)
    assert docs_module._current_registry_version() == "0"
    assert _tag_agent_version("x").endswith(f";reg=0{_PV}")

    # A registry that reports a token containing the separator would
    # corrupt the segment grammar -> also falls back to "0".
    monkeypatch.setattr(docs_module, "registry_version", lambda: "1;pv=v9")
    assert docs_module._current_registry_version() == "0"


# ───────────── cache lookup still hits on the stamped format ─────────────


async def _seed_extracted_doc(
    *, tenant_id: str, content_hash: str, agent_version: str
) -> str:
    """Seed deal + EXTRACTED document + extraction_results row (mirrors
    the helpers in test_documents.py) and return the extraction id."""
    import json as _json
    from uuid import uuid4

    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    deal_id, doc_id, ext_id = str(uuid4()), str(uuid4()), str(uuid4())
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at) "
                "VALUES (:id, :tenant, :name, 'Draft', :ts, :ts)"
            ),
            {
                "id": deal_id,
                "tenant": tenant_id,
                "name": "Stamp Test Hotel",
                "ts": "2026-07-01 00:00:00",
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, content_hash, storage_key, size_bytes,
                    page_count, parser, extraction_data
                ) VALUES (
                    :id, :deal, :tenant, :filename, 'T12', :status,
                    :uploaded_at, :h, 'file:///tmp/dummy', 1024,
                    1, 'test-fixture', :ed
                )
                """
            ),
            {
                "id": doc_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "filename": "stamped.pdf",
                "status": docs_module.DOC_STATUS_EXTRACTED,
                "uploaded_at": "2026-07-01 00:00:00",
                "h": content_hash,
                "ed": _json.dumps(
                    {"parser": "test-fixture", "total_pages": 1, "pages": []}
                ),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO extraction_results (
                    id, document_id, deal_id, tenant_id,
                    fields, confidence_report, agent_version, created_at
                ) VALUES (:id, :doc, :deal, :tenant, :fields, :cr, :ver, :created)
                """
            ),
            {
                "id": ext_id,
                "doc": doc_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "fields": _json.dumps([]),
                "cr": _json.dumps({"overall": 0.9}),
                "ver": agent_version,
                "created": "2026-07-01 00:00:00",
            },
        )
        await session.commit()
    return ext_id


@pytest.mark.asyncio
async def test_stamped_agent_version_still_satisfies_cache_suffix_filter() -> None:
    """Seed a row in the NEW format and prove ``_lookup_extraction_cache``
    (the real ``LIKE '%;pv=vN'`` query) returns it -- the suffix contract
    the new segments must not break -- while the same shape under an older
    ``pv`` is still a MISS."""
    from app.database import get_session_factory

    tenant_id = "33333333-3333-3333-3333-333333333333"
    hit_hash = "f" * 64
    miss_hash = "e" * 64

    stamped = _tag_agent_version("router:extract;dt:T12;extractor;ps=feedbeef")
    assert _STAMPED_RE.match(stamped), stamped
    hit_ext_id = await _seed_extracted_doc(
        tenant_id=tenant_id, content_hash=hit_hash, agent_version=stamped
    )
    await _seed_extracted_doc(
        tenant_id=tenant_id,
        content_hash=miss_hash,
        agent_version="router:extract;dt:T12;extractor;ps=feedbeef;reg=0;pv=v0",
    )

    factory = get_session_factory()
    async with factory() as session:
        hit = await docs_module._lookup_extraction_cache(
            session, tenant_id=tenant_id, content_hash=hit_hash
        )
        miss = await docs_module._lookup_extraction_cache(
            session, tenant_id=tenant_id, content_hash=miss_hash
        )
    assert hit is not None and str(hit["id"]) == hit_ext_id
    assert hit["agent_version"] == stamped
    assert _parse_prompt_sha_from_agent_version(hit["agent_version"]) == "feedbeef"
    assert _parse_doc_type_from_agent_version(hit["agent_version"]) == "T12"
    assert miss is None


# ───────────────────────── prompt SHA ─────────────────────────


def test_prompt_sha_is_sha256_prefix_and_stable() -> None:
    from app.agents.extractor import PROMPT_SHA, SYSTEM_PROMPT, prompt_sha

    assert prompt_sha(SYSTEM_PROMPT) == PROMPT_SHA
    assert hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:8] == PROMPT_SHA
    assert re.fullmatch(r"[0-9a-f]{8}", PROMPT_SHA)
    assert prompt_sha("same instructions") == prompt_sha("same instructions")


def test_prompt_sha_changes_when_instructions_change() -> None:
    from app.agents.extractor import SYSTEM_PROMPT, prompt_sha

    assert prompt_sha(SYSTEM_PROMPT) != prompt_sha(SYSTEM_PROMPT + " ")
    assert prompt_sha("a") != prompt_sha("b")


async def _run_extractor_with_stubbed_llm(
    monkeypatch: pytest.MonkeyPatch, *, doc_type: str
) -> Any:
    """Drive ``run_extractor`` end-to-end with the per-document LLM call
    replaced by a canned result -- exercises the real prompt-selection
    code (legacy vs dynamic) without touching Anthropic or the DB."""
    from fondok_schemas import ConfidenceReport, DocType

    from app.agents import extractor as ex

    async def _fake_extract_one(doc: Any, *, deal_id: str, system_blocks: Any) -> Any:
        return (
            ex.ExtractedDocumentResult(
                document_id=doc.document_id,
                filename=doc.filename,
                doc_type=doc.doc_type,
                fields=[],
                confidence=ConfidenceReport(overall=0.9),
            ),
            None,
        )

    monkeypatch.setattr(ex, "_extract_one", _fake_extract_one)
    monkeypatch.setattr(ex, "check_budget", lambda *_a, **_k: None)
    payload = ex.ExtractorInput(
        tenant_id="00000000-0000-0000-0000-000000000001",
        deal_id="11111111-2222-3333-4444-555555555555",
        documents=[
            ex.ExtractorDocument(
                document_id="doc-1",
                filename="om.pdf",
                doc_type=DocType(doc_type),
                content="Total Revenue 1,000,000",
                source_pages=[1],
            )
        ],
    )
    return await ex.run_extractor(payload)


@pytest.mark.asyncio
async def test_run_extractor_stamps_legacy_prompt_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agents.extractor import PROMPT_SHA

    monkeypatch.delenv("EXTRACTOR_USE_DYNAMIC_SCHEMAS", raising=False)
    out = await _run_extractor_with_stubbed_llm(monkeypatch, doc_type="OM")
    assert out.success
    assert out.prompt_sha == PROMPT_SHA
    assert out.agent_version == f"extractor;ps={PROMPT_SHA}"
    # Splice (as _run_graph_extraction does) + tag = the persisted shape.
    reg = docs_module._current_registry_version()
    persisted = _tag_agent_version(f"router:extract;dt:OM;{out.agent_version}")
    assert persisted == f"router:extract;dt:OM;extractor;ps={PROMPT_SHA};reg={reg}{_PV}"


@pytest.mark.asyncio
async def test_run_extractor_stamps_dynamic_schema_prompt_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agents.extraction_schemas.loader import build_system_prompt
    from app.agents.extractor import PROMPT_SHA, SYSTEM_PROMPT, prompt_sha

    monkeypatch.setenv("EXTRACTOR_USE_DYNAMIC_SCHEMAS", "1")
    dynamic = build_system_prompt("OM")
    assert dynamic and dynamic != SYSTEM_PROMPT

    out = await _run_extractor_with_stubbed_llm(monkeypatch, doc_type="OM")
    assert out.success
    assert out.prompt_sha == prompt_sha(dynamic)
    assert out.prompt_sha != PROMPT_SHA
    assert out.agent_version == f"extractor;ps={prompt_sha(dynamic)}"


@pytest.mark.asyncio
async def test_run_extractor_no_prompt_sent_means_no_stamp() -> None:
    """The no-documents early return never builds a prompt, so it must not
    claim one."""
    from app.agents import extractor as ex

    out = await ex.run_extractor(
        ex.ExtractorInput(tenant_id="t", deal_id="d", document_uris=["s3://x"])
    )
    assert out.success
    assert out.prompt_sha is None
    assert out.agent_version is None
