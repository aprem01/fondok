"""Document upload + extraction integration tests.

Exercises the full flow: synthesize a tiny PDF with reportlab, hit the
upload endpoint, kick the extract endpoint, poll until EXTRACTED, read
the extraction result.

The tests run with ``EVALS_MOCK=true`` so the agents return canned data
instead of calling Claude — keeps CI cheap and deterministic.
"""

from __future__ import annotations

import asyncio
import io
import os
import tempfile
from pathlib import Path
from uuid import UUID

import pytest

# Force SQLite + a temp DB before app imports — keeps tests isolated
# from any local fondok.db left behind by manual runs.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-documents.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-storage"
)
os.environ.setdefault("EVALS_MOCK", "true")

# Wipe any leftover storage tree.
import shutil  # noqa: E402

_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)


# ─────────────────────────── fixtures ───────────────────────────


def _build_sample_pdf() -> bytes:
    """Generate a 1-page hotel-themed PDF with reportlab.

    Stored in-memory so we never commit a binary blob. The page
    contains both prose and a table-like grid so the parser exercises
    its table-extraction path even on the fallback PyMuPDF backend.
    """
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "reportlab not installed; required for test PDF synthesis"
        ) from exc

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER
    text = c.beginText(72, height - 72)
    text.setFont("Helvetica-Bold", 14)
    text.textLine("Sample Hotel T-12 Operating Statement")
    text.setFont("Helvetica", 10)
    text.textLine("")
    text.textLine("Property: The Fondok Inn — Austin, TX")
    text.textLine("Period: Jan 2024 – Dec 2024")
    text.textLine("")
    text.textLine("Net Operating Income: $1,234,567")
    text.textLine("Occupancy: 74%")
    text.textLine("Average Daily Rate (ADR): $185.40")
    text.textLine("RevPAR: $137.20")
    text.textLine("")
    text.textLine("Department  | Revenue    | Expense   | Profit")
    text.textLine("Rooms       | 6,500,000  | 1,500,000 | 5,000,000")
    text.textLine("F&B         | 1,800,000  | 1,200,000 |   600,000")
    text.textLine("Other       |   200,000  |    80,000 |   120,000")
    c.drawText(text)
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture(scope="session")
def sample_pdf_bytes() -> bytes:
    pdf_path = Path(__file__).parent / "fixtures" / "sample_t12.pdf"
    if pdf_path.exists():
        return pdf_path.read_bytes()
    body = _build_sample_pdf()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(body)
    return body


@pytest.fixture
async def deal_id() -> str:
    """Insert a stub deal row directly so document FKs resolve."""
    from sqlalchemy import text

    from app.config import get_settings
    from app.database import dispose_engine, get_session_factory
    from app.migrations import run_startup_migrations

    # Make sure schema exists.
    await run_startup_migrations()

    settings = get_settings()
    factory = get_session_factory()
    from uuid import uuid4

    new_id = uuid4()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at)
                VALUES (:id, :tenant, :name, 'Draft', :ts, :ts)
                """
            ),
            {
                "id": str(new_id),
                "tenant": settings.DEFAULT_TENANT_ID,
                "name": "Test Hotel",
                "ts": "2026-04-27 00:00:00",
            },
        )
        await session.commit()
    yield str(new_id)
    await dispose_engine()


# ─────────────────────────── parser ───────────────────────────


@pytest.mark.asyncio
async def test_parse_pymupdf_fallback(sample_pdf_bytes: bytes) -> None:
    """Without LLAMA_CLOUD_API_KEY the parser must use PyMuPDF."""
    # Defensive: ensure no leftover key in the env for this test.
    os.environ.pop("LLAMA_CLOUD_API_KEY", None)

    from app.extraction import parse_pdf

    parsed = await parse_pdf(sample_pdf_bytes, "sample_t12.pdf")
    assert parsed.filename == "sample_t12.pdf"
    assert parsed.total_pages >= 1
    assert parsed.pages, "no pages parsed"
    page_text = parsed.pages[0].text.lower()
    assert "net operating income" in page_text
    assert parsed.parser.startswith("pymupdf")
    assert parsed.content_hash and len(parsed.content_hash) == 64


@pytest.mark.asyncio
async def test_parse_xls_str_trend() -> None:
    """STR CoStar Trend reports ship as legacy .xls — each sheet
    becomes one ParsedPage, table grids preserved, parser='xlrd'.
    """
    from app.extraction import parse_document

    xls_path = Path(__file__).parent / "fixtures" / "sample_str_trend.xls"
    if not xls_path.exists():
        pytest.skip("sample_str_trend.xls fixture not present")

    body = xls_path.read_bytes()
    parsed = await parse_document(body, "sample_str_trend.xls")

    assert parsed.parser == "xlrd"
    assert parsed.total_pages >= 8, "expected multi-sheet workbook"
    assert all(p.metadata.get("sheet_name") for p in parsed.pages), (
        "every page must carry its sheet name"
    )
    # By Measure sheet (sheet 2) carries the headline Occ/ADR/RevPAR table
    by_measure = parsed.pages[1]
    assert "Occupancy" in by_measure.text or "occupancy" in by_measure.text.lower()
    assert by_measure.tables, "table grid must be preserved"
    assert parsed.content_hash and len(parsed.content_hash) == 64


@pytest.mark.asyncio
async def test_parse_unsupported_extension_raises() -> None:
    """Any extension other than the registered set is a hard ParseError —
    callers (the upload background task) catch this and mark the row
    PARSE_FAILED rather than silently dropping the file.
    """
    from app.extraction import ParseError, parse_document

    with pytest.raises(ParseError, match="unsupported file extension"):
        await parse_document(b"some-bytes", "notes.txt")


@pytest.mark.asyncio
async def test_parse_docx_extracts_paragraphs_and_tables() -> None:
    """Sam QA 2026-06-29: HMA Summary + Business Plan docs were
    failing as 'unsupported file extension .docx'. python-docx now
    handles them — paragraphs in document order, tables both as
    structured grids AND folded into the text stream so the LLM
    extractor sees them positionally.
    """
    pytest.importorskip("docx")
    from io import BytesIO

    from docx import Document  # type: ignore[import-untyped]

    from app.extraction import parse_document

    d = Document()
    d.add_heading("Anglers HMA Summary", 0)
    d.add_paragraph("Term: 25 years from 2018.")
    t = d.add_table(rows=2, cols=2)
    t.rows[0].cells[0].text = "Base fee"
    t.rows[0].cells[1].text = "3.0% of gross"
    t.rows[1].cells[0].text = "Incentive"
    t.rows[1].cells[1].text = "15% of GOP"
    buf = BytesIO()
    d.save(buf)
    body = buf.getvalue()

    parsed = await parse_document(body, "hma.docx")

    assert parsed.parser == "python-docx"
    assert parsed.total_pages == 1
    page = parsed.pages[0]
    assert "Anglers HMA Summary" in page.text
    assert "25 years" in page.text
    # Table folded into text + preserved as structured grid.
    assert "Base fee" in page.text
    assert "3.0% of gross" in page.text
    assert len(page.tables) == 1
    table = page.tables[0]
    assert ["Base fee", "3.0% of gross"] in table
    assert ["Incentive", "15% of GOP"] in table
    assert parsed.content_hash and len(parsed.content_hash) == 64


@pytest.mark.asyncio
async def test_parse_docx_corrupt_file_surfaces_clear_error() -> None:
    """A non-OOXML payload uploaded as .docx must surface a clean
    ParseError instead of crashing or hanging. (Sam QA: a couple of
    staged .docx files were corrupted 'not a zip file' — the no-
    silent-failures pass requires this to read as actionable.)
    """
    pytest.importorskip("docx")
    from app.extraction import ParseError, parse_document

    with pytest.raises(ParseError, match="python-docx failed to open"):
        await parse_document(b"not a real docx", "broken.docx")


def test_unknown_doc_type_falls_back_to_filename_hint() -> None:
    """Regression: when the router returns ``UNKNOWN`` (LLM rate-limit,
    credit-balance exhausted, off-list emission), the extraction
    pipeline must fall back to the filename hint instead of crashing
    on Pydantic enum validation.

    The fix lives at apps/worker/app/api/documents.py
    `_run_graph_extraction` — it now intersects the router output with
    the valid DocType enum members and falls back to ``_guess_doc_type``
    when there's no match. This test exercises both halves: the enum
    set and the filename hint.
    """
    from fondok_schemas import DocType

    from app.api.documents import _guess_doc_type

    valid = {dt.value for dt in DocType}
    assert "UNKNOWN" not in valid, (
        "UNKNOWN must NOT appear as a real DocType — it's a sentinel "
        "the router emits when it can't classify"
    )

    # The filename hint must always resolve to a valid DocType so the
    # fallback never re-introduces the original crash.
    for filename in [
        "sample_OM.pdf",
        "sample_T12.pdf",
        "sample_str_trend.xls",
        "sample_cbre_horizons.pdf",
        "sample_pnl_benchmark.pdf",
        "Random_File.pdf",  # falls back to T12 default
    ]:
        hint = _guess_doc_type(filename)
        assert hint in valid, (
            f"_guess_doc_type returned {hint!r} for {filename!r}, "
            f"which is not a valid DocType — the UNKNOWN fallback "
            f"would still crash"
        )


# ─────────────────────────── storage ───────────────────────────


@pytest.mark.asyncio
async def test_local_raw_store(tmp_path: Path) -> None:
    """LocalRawStore put/get roundtrip with a deterministic key."""
    from app.storage import LocalRawStore

    store = LocalRawStore(tmp_path / "raw")
    body = b"hello-world"
    key = await store.put(
        tenant_id="t1",
        deal_id="d1",
        content_hash="abc123",
        filename="thing.pdf",
        bytes_=body,
    )
    assert key.startswith("file://")
    assert await store.exists(key)
    got = await store.get(key)
    assert got == body


# ─────────────────────────── upload ───────────────────────────


@pytest.mark.asyncio
async def test_upload_pdf(sample_pdf_bytes: bytes, deal_id: str) -> None:
    """Upload writes a documents row, parks the file in the raw store,
    and returns immediately with the row at status ``PARSING``.

    The actual PDF parse + extraction runs as a background task so
    dense uploads don't blow through the proxy's HTTP timeout. Per-row
    fields populated synchronously: id, filename, content_hash,
    storage_key, status. Fields populated asynchronously after parse:
    page_count, parser, extraction_data.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.storage import get_raw_store, reset_raw_store_cache

    reset_raw_store_cache()  # pick up the test DOCUMENT_STORAGE_ROOT

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        files = {
            "files": ("sample_t12.pdf", sample_pdf_bytes, "application/pdf"),
        }
        r = await client.post(f"/deals/{deal_id}/documents/upload", files=files)

    assert r.status_code == 201, r.text
    body = r.json()
    assert isinstance(body, list) and len(body) == 1
    rec = body[0]
    assert rec["filename"] == "sample_t12.pdf"
    # Synchronous parts of the upload:
    assert rec["status"] == "PARSING"
    assert rec["content_hash"] and len(rec["content_hash"]) == 64
    assert rec["storage_key"].startswith("file://")
    # Async parts haven't filled in yet (parse is in flight).
    assert rec["page_count"] is None
    assert rec["parser"] is None

    # File exists on disk where we said it would.
    store = get_raw_store()
    assert await store.exists(rec["storage_key"])


# ─────────────────────────── end-to-end ───────────────────────────


@pytest.mark.asyncio
async def test_extraction_flow_end_to_end(
    sample_pdf_bytes: bytes, deal_id: str
) -> None:
    """Upload → extract → poll → fetch extraction. Uses EVALS_MOCK."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.storage import reset_raw_store_cache

    reset_raw_store_cache()
    os.environ["EVALS_MOCK"] = "true"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Upload
        r = await client.post(
            f"/deals/{deal_id}/documents/upload",
            files={
                "files": ("sample_t12.pdf", sample_pdf_bytes, "application/pdf"),
            },
        )
        assert r.status_code == 201, r.text
        doc_id = r.json()[0]["id"]
        UUID(doc_id)  # validates shape

        # List
        r = await client.get(f"/deals/{deal_id}/documents")
        assert r.status_code == 200
        assert any(d["id"] == doc_id for d in r.json())

        # Upload now auto-chains parse → extract on its own background
        # task — no explicit POST /extract needed. We just poll the
        # extraction endpoint until status flips to EXTRACTED. (The
        # explicit /extract route is still available as a manual
        # re-run mechanism but doesn't need to fire on every upload.)
        final_status = None
        for _ in range(80):  # ~8s budget — async parse + extract
            r = await client.get(
                f"/deals/{deal_id}/documents/{doc_id}/extraction"
            )
            assert r.status_code == 200, r.text
            final_status = r.json()["status"]
            if final_status in ("EXTRACTED", "FAILED", "PARSE_FAILED"):
                break
            await asyncio.sleep(0.1)

        assert final_status == "EXTRACTED", (
            f"expected EXTRACTED, got {final_status}: {r.json()}"
        )

        body = r.json()
        assert body["fields"], "mock extraction should populate fields"
        # Mock payload always emits noi_year_1.
        assert any(f["field_name"] == "noi_year_1" for f in body["fields"])
        # The mock payload starts at overall 0.9, but the citation
        # verifier runs after extraction and promotes any field it
        # confirms verbatim against the source page (Sam QA 2026-05-14
        # critic-promote). The mock T-12 fixture carries "Net Operating
        # Income: $1,234,567" so noi_year_1 verifies MATCH and is
        # floored to 0.98 — the rolled-up overall lands at-or-above the
        # raw 0.9. Assert the floor, not an exact value.
        assert body["confidence_report"]["overall"] >= 0.9


# ─────────────────────────── graph ───────────────────────────


def test_graph_compiles_with_real_nodes() -> None:
    """The real graph (with bound node functions) must compile.

    Distinct from ``test_smoke.test_graph_compiles`` because we assert
    on the node set — the wiring must include every pipeline stage.
    """
    from app.graph import build_graph

    g = build_graph()
    assert g is not None

    # The compiled graph exposes its node set on .nodes.
    expected = {
        "route",
        "extract",
        "normalize",
        "gate1_review",
        "run_engines",
        "analyze",
        "variance",
        "gate2_review",
        "finalize",
    }
    assert expected.issubset(set(g.nodes))
