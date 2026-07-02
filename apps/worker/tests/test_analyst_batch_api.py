"""Tests for the analyst Message Batches API lane — Task V (2026-07).

The batch lane submits the memo draft to Anthropic's
``POST /v1/messages/batches`` endpoint, persists a ``pending_batches``
row, and hands the memo back to the memo cache once the polling worker
picks up the ended batch. These tests exercise the four failure modes
Sam needs to trust:

1. **submit**  — a live batch client is called, the batch id is
   captured, and a ``pending_batches`` row is inserted.
2. **poll status** — a batch that is still ``in_progress`` moves the
   row's status but does NOT drain results.
3. **ingest results** — an ``ended`` batch parses the tool_use envelope,
   projects a memo, writes it to the memo cache, and persists a
   half-price ``ModelCall`` row.
4. **error path** — a batch that ends with an errored request marks
   the pending row ``failed`` with the error message; no memo is
   written.

All four use a fake ``BatchClient`` (see ``_FakeBatchClient`` below) so
the test never hits the network. The batch flag is force-enabled in
each test via ``get_settings().ANALYST_BATCH_API_ENABLED = True``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

# Per-test SQLite DB — must be set before any ``app.*`` import binds Settings().
_TEST_DB = Path(__file__).resolve().parent / "fondok-analyst-batch.db"
os.environ.setdefault(
    "DATABASE_URL", f"sqlite+aiosqlite:///{_TEST_DB.as_posix()}"
)
# NOTE on ANTHROPIC_API_KEY: we deliberately do NOT set the env var
# here, because ``tests/test_agents.py`` module-level pytestmark
# ``skipif(not os.environ.get("ANTHROPIC_API_KEY"))`` gets flipped by
# any earlier test module that sets the key at import time — pytest
# imports test modules in collection order, so if we ran first we'd
# make the live-LLM suite try to run without a real key. Every test
# below passes a ``_FakeBatchClient`` explicitly so :class:`BatchClient`
# (the only path that reads the setting) is never constructed.

TENANT = "11111111-1111-1111-1111-111111111111"


# ─────────────────────── fixtures ───────────────────────


@pytest.fixture(autouse=True)
async def _reset_db_and_flag(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Enable the batch flag + truncate all rows between tests.

    We don't ``unlink`` the sqlite file because the SQLAlchemy engine
    holds an open connection; deleting the file behind its back leaves
    the process with a stale FD that raises ``attempt to write a
    readonly database`` on the next INSERT. Instead we DELETE all rows
    and let the schema stay in place across the module.
    """
    from app.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    settings = get_settings()
    monkeypatch.setattr(settings, "ANALYST_BATCH_API_ENABLED", True, raising=False)

    from app.database import get_engine
    from app.migrations import run_startup_migrations

    await run_startup_migrations()

    engine = get_engine()
    async with engine.begin() as conn:
        for table in ("pending_batches", "model_calls"):
            try:
                await conn.execute(text(f"DELETE FROM {table}"))
            except Exception:
                pass
    yield
    async with engine.begin() as conn:
        for table in ("pending_batches", "model_calls"):
            try:
                await conn.execute(text(f"DELETE FROM {table}"))
            except Exception:
                pass


def _sample_analyst_input() -> Any:
    """Build a minimally-populated AnalystInput.

    We don't need the full spread + variance report to test the batch
    plumbing — the batch path only asks the payload for the messages,
    which is exercised by ``_build_batch_request``.
    """
    from app.agents.analyst import AnalystInput

    return AnalystInput(
        tenant_id=TENANT,
        deal_id=str(uuid.uuid4()),
        deal_data={"name": "Hyatt Chicago"},
        engine_results={"irr_levered": 0.18},
    )


def _sample_memo_envelope_dict() -> dict[str, Any]:
    """Return the JSON payload the LLM would emit as its tool_use.input."""
    sections = [
        {
            "section_id": sid,
            "title": sid.replace("_", " ").title(),
            "body": (
                f"Body for {sid}. This asset sits at 68% occupancy and a "
                "$189 ADR; RevPAR runs $128 vs a $121 comp-set peer."
            ),
            "citations": [
                {
                    "document_id": "doc-1",
                    "page": 1,
                    "field": "occupancy",
                    "excerpt": "TTM occupancy 68%",
                }
            ],
        }
        for sid in (
            "investment_thesis",
            "market_analysis",
            "deal_overview",
            "financial_analysis",
            "risk_factors",
            "recommendation",
        )
    ]
    return {
        "sections": sections,
        "overall_confidence": 0.9,
        "low_confidence_fields": [],
        "requires_human_review": False,
    }


class _FakeBatchClient:
    """In-memory stand-in for :class:`app.agents.analyst_batch.BatchClient`.

    Tests configure the responses ``submit`` / ``retrieve`` / ``results``
    hand back before invoking the code under test. Every call records
    the batch_id it received so assertions can verify the polling loop
    talked to the right batch.
    """

    def __init__(
        self,
        *,
        submit_response: dict[str, Any] | None = None,
        retrieve_response: dict[str, Any] | None = None,
        results_response: list[dict[str, Any]] | None = None,
        raises_on_submit: Exception | None = None,
    ) -> None:
        self.submit_response = submit_response or {"id": "batch_test_abc"}
        self.retrieve_response = retrieve_response or {
            "processing_status": "in_progress"
        }
        self.results_response = results_response or []
        self.raises_on_submit = raises_on_submit
        self.submit_calls: list[list[dict[str, Any]]] = []
        self.retrieve_calls: list[str] = []
        self.results_calls: list[str] = []

    def submit(self, requests: list[dict[str, Any]]) -> Any:
        if self.raises_on_submit is not None:
            raise self.raises_on_submit
        self.submit_calls.append(requests)
        return self.submit_response

    def retrieve(self, batch_id: str) -> Any:
        self.retrieve_calls.append(batch_id)
        return self.retrieve_response

    def results(self, batch_id: str) -> Any:
        self.results_calls.append(batch_id)
        return self.results_response


async def _fetch_pending(batch_id: str) -> dict[str, Any] | None:
    from app.database import get_engine

    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT id, batch_id, deal_id, tenant_id, status, error
                  FROM pending_batches
                 WHERE batch_id = :bid
                """
            ),
            {"bid": batch_id},
        )
        row = result.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "batch_id": row[1],
            "deal_id": row[2],
            "tenant_id": row[3],
            "status": row[4],
            "error": row[5],
        }


# ─────────────────────── 1. submit ───────────────────────


async def test_run_analyst_batch_submits_and_persists_row() -> None:
    """Submit → batch_id captured, pending_batches row inserted queued."""
    from app.agents.analyst_batch import run_analyst_batch

    payload = _sample_analyst_input()
    fake = _FakeBatchClient(submit_response={"id": "batch_submit_1"})
    result = await run_analyst_batch(payload, client=fake)

    assert result.status == "queued"
    assert result.batch_id == "batch_submit_1"
    assert result.error is None

    # Batch API received exactly one request with the analyst's model.
    assert len(fake.submit_calls) == 1
    (submitted,) = fake.submit_calls
    assert len(submitted) == 1
    req = submitted[0]
    assert req["custom_id"].startswith(f"analyst:{payload.deal_id}:")
    assert "claude-opus" in req["params"]["model"] or "opus" in req["params"]["model"]
    # System + user message present.
    assert isinstance(req["params"]["system"], list)
    assert req["params"]["system"], "system blocks empty"
    assert req["params"]["messages"][0]["role"] == "user"
    # Tool-forced structured output.
    assert req["params"]["tool_choice"]["name"] == "emit_investment_memo"

    row = await _fetch_pending("batch_submit_1")
    assert row is not None
    assert row["status"] == "queued"
    assert row["deal_id"] == payload.deal_id
    assert row["tenant_id"] == TENANT
    assert row["error"] is None


async def test_run_analyst_batch_flag_off_short_circuits() -> None:
    """When the flag is off the submit never touches the client."""
    from app.agents.analyst_batch import run_analyst_batch
    from app.config import get_settings

    settings = get_settings()
    settings.ANALYST_BATCH_API_ENABLED = False  # type: ignore[attr-defined]

    payload = _sample_analyst_input()
    fake = _FakeBatchClient()
    result = await run_analyst_batch(payload, client=fake)

    assert result.status == "disabled"
    assert result.batch_id is None
    assert fake.submit_calls == []
    # No pending row inserted.
    assert (await _fetch_pending("batch_test_abc")) is None


# ─────────────────────── 2. poll status (still running) ───────────────────────


async def test_poll_marks_in_progress_but_leaves_row_open() -> None:
    """A batch still ``in_progress`` upgrades status but doesn't ingest."""
    from app.agents.analyst_batch import poll_pending_batches, run_analyst_batch

    payload = _sample_analyst_input()
    submit_fake = _FakeBatchClient(submit_response={"id": "batch_poll_1"})
    await run_analyst_batch(payload, client=submit_fake)

    poll_fake = _FakeBatchClient(
        retrieve_response={"processing_status": "in_progress"}
    )
    tally = await poll_pending_batches(client=poll_fake)

    assert tally["checked"] == 1
    assert tally["pending"] == 1
    assert tally["completed"] == 0
    assert poll_fake.retrieve_calls == ["batch_poll_1"]
    assert poll_fake.results_calls == []  # never asked for results

    row = await _fetch_pending("batch_poll_1")
    assert row is not None
    assert row["status"] == "in_progress"


# ─────────────────────── 3. ingest results (success) ───────────────────────


async def test_poll_ingests_ended_batch_and_marks_complete() -> None:
    """An ``ended`` batch parses the envelope, writes memo cache, persists cost."""
    from app.agents.analyst_batch import poll_pending_batches, run_analyst_batch

    payload = _sample_analyst_input()
    submit_fake = _FakeBatchClient(submit_response={"id": "batch_ingest_1"})
    await run_analyst_batch(payload, client=submit_fake)

    envelope = _sample_memo_envelope_dict()
    results_payload = [
        {
            "custom_id": f"analyst:{payload.deal_id}:xxxx",
            "result": {
                "type": "succeeded",
                "message": {
                    "id": "msg_test",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "emit_investment_memo",
                            "input": envelope,
                        }
                    ],
                    "usage": {
                        "input_tokens": 5_000,
                        "output_tokens": 2_000,
                        "cache_creation_input_tokens": 4_500,
                        "cache_read_input_tokens": 0,
                    },
                },
            },
        }
    ]
    poll_fake = _FakeBatchClient(
        retrieve_response={"processing_status": "ended"},
        results_response=results_payload,
    )
    tally = await poll_pending_batches(client=poll_fake)

    assert tally["completed"] == 1
    assert tally["failed"] == 0
    row = await _fetch_pending("batch_ingest_1")
    assert row is not None
    assert row["status"] == "complete"
    assert row["error"] is None

    # ModelCall row landed with a discounted cost.
    from app.database import get_engine

    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "SELECT agent_name, model, cost_usd, input_tokens, output_tokens "
                "FROM model_calls WHERE deal_id = :d"
            ),
            {"d": payload.deal_id},
        )
        mc = result.fetchone()
    assert mc is not None
    assert mc[0] == "analyst"
    assert "opus" in mc[1]
    # Cost should be non-zero AND less than the full-rate equivalent
    # (batch = 50%). Compute the full rate for comparison.
    from app.budget import _price_for

    in_p, out_p = _price_for(mc[1])
    # 5_000 input tokens = 4_500 cache-create + 500 plain + 0 cache-read.
    # Prices are per-1M-token — divide the sum by 1e6 to get USD.
    plain = mc[3] - 4_500
    full_rate = (
        plain * in_p + 4_500 * in_p * 1.25 + mc[4] * out_p
    ) / 1_000_000
    assert 0 < mc[2] < full_rate  # discount actually applied
    # Allow ~1% float drift.
    assert abs(mc[2] - full_rate * 0.5) / max(full_rate * 0.5, 1e-9) < 0.01


# ─────────────────────── 4. error path ───────────────────────


async def test_poll_marks_failed_when_request_errored() -> None:
    """A batch whose per-request result is ``errored`` moves row → failed."""
    from app.agents.analyst_batch import poll_pending_batches, run_analyst_batch

    payload = _sample_analyst_input()
    submit_fake = _FakeBatchClient(submit_response={"id": "batch_err_1"})
    await run_analyst_batch(payload, client=submit_fake)

    results_payload = [
        {
            "custom_id": f"analyst:{payload.deal_id}:xxxx",
            "result": {
                "type": "errored",
                "error": {"type": "invalid_request", "message": "bad schema"},
            },
        }
    ]
    poll_fake = _FakeBatchClient(
        retrieve_response={"processing_status": "ended"},
        results_response=results_payload,
    )
    tally = await poll_pending_batches(client=poll_fake)

    assert tally["failed"] == 1
    assert tally["completed"] == 0
    row = await _fetch_pending("batch_err_1")
    assert row is not None
    assert row["status"] == "failed"
    assert row["error"] and "no memo" in row["error"].lower()


async def test_poll_marks_expired_on_timeout() -> None:
    """Anthropic returns ``expired`` after the 24h window — row moves to expired."""
    from app.agents.analyst_batch import poll_pending_batches, run_analyst_batch

    payload = _sample_analyst_input()
    submit_fake = _FakeBatchClient(submit_response={"id": "batch_expired_1"})
    await run_analyst_batch(payload, client=submit_fake)

    poll_fake = _FakeBatchClient(
        retrieve_response={"processing_status": "expired"}
    )
    tally = await poll_pending_batches(client=poll_fake)

    assert tally["expired"] == 1
    row = await _fetch_pending("batch_expired_1")
    assert row is not None
    assert row["status"] == "expired"
    assert row["error"] and "expired" in row["error"].lower()


async def test_run_analyst_batch_submit_exception_returns_error_status() -> None:
    """A raised exception on the submit call yields ``status='error'``, no row."""
    from app.agents.analyst_batch import run_analyst_batch

    payload = _sample_analyst_input()
    boom = RuntimeError("simulated API 500")
    fake = _FakeBatchClient(raises_on_submit=boom)

    result = await run_analyst_batch(payload, client=fake)
    assert result.status == "error"
    assert result.batch_id is None
    assert result.error and "simulated API 500" in result.error

    # No row leaked.
    from app.database import get_engine

    engine = get_engine()
    async with engine.begin() as conn:
        r = await conn.execute(
            text("SELECT COUNT(*) FROM pending_batches")
        )
        (count,) = r.fetchone()  # type: ignore[misc]
    assert count == 0


# ─────────────────────── output parity guard ───────────────────────


async def test_batch_request_uses_same_system_prompt_as_sync() -> None:
    """Batch path must build the same system prompt as the sync single-shot path.

    This is the parity contract: identical system + user messages,
    identical model, identical tool schema. Only the transport differs.
    We verify parity by rebuilding the sync path's messages and
    comparing key structural fields.
    """
    from app.agents.analyst import SYSTEM_PROMPT, _build_user_prompt
    from app.agents.analyst_batch import (
        _build_batch_request,
        _envelope_tool_schema,
    )

    payload = _sample_analyst_input()
    req = _build_batch_request(
        payload,
        custom_id="analyst:test:0",
        tool_schema=_envelope_tool_schema(),
    )

    # System prompt content contains the SYSTEM_PROMPT text (as one of
    # the cached blocks).
    system_texts = [
        b["text"] for b in req["params"]["system"] if b.get("type") == "text"
    ]
    joined_system = "\n\n".join(system_texts)
    # SYSTEM_PROMPT's opening line is a stable anchor.
    assert "Analyst agent" in joined_system
    assert "InvestmentMemoEnvelope" in joined_system or "investment_thesis" in joined_system

    # User prompt is byte-identical to what the sync path builds.
    user_block = req["params"]["messages"][0]["content"][0]
    assert user_block["text"] == _build_user_prompt(payload)

    # The tool schema matches _InvestmentMemoEnvelope's JSON schema.
    from app.agents.analyst import _InvestmentMemoEnvelope

    assert req["params"]["tools"][0]["input_schema"] == (
        _InvestmentMemoEnvelope.model_json_schema()
    )

    # SYSTEM_PROMPT constant is referenced (proves single source of truth).
    assert SYSTEM_PROMPT.strip() in joined_system
