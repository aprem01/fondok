"""C7 (go-live audit) — no eval mock in production code.

The extraction pipeline used to read ``EVALS_MOCK`` inside
``app/api/documents.py`` and return canned fields as a document's extraction.
The canned payload now lives in ``evals/mock_extraction.py`` and reaches the
pipeline only through the injection seam (``set_extraction_backend``).
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_production_module_reads_no_eval_env_switch() -> None:
    import app.api.documents as documents

    src = inspect.getsource(documents)
    assert 'os.environ.get("EVALS_MOCK"' not in src
    assert "_mock_extraction_payload" not in src
    assert "1234567.0" not in src  # the canned NOI is not in the worker


@pytest.mark.real_extraction_backend
@pytest.mark.asyncio
async def test_env_var_cannot_switch_the_production_backend(monkeypatch) -> None:
    """``EVALS_MOCK=true`` in the environment changes nothing: the pipeline
    runs the LLM backend, which drives the (here stubbed) graph."""
    import app.api.documents as documents

    monkeypatch.setenv("EVALS_MOCK", "true")
    backend = documents.current_extraction_backend()
    assert backend is documents.LLM_EXTRACTION_BACKEND
    assert backend.critic_narrative_pass is True

    calls: list[str] = []

    async def _graph(**kwargs):
        calls.append(kwargs["doc_id"])
        return [{"field_name": "x", "value": 1}], {"overall": 1.0}, "router:T12;extractor", "T12"

    monkeypatch.setattr(documents, "_run_graph_extraction", _graph)
    monkeypatch.setattr(documents, "_sibling_reuse_allowed", lambda _dt: False)
    fields, _conf, version, doc_type = await backend.extract(
        documents.ExtractionRequest(
            session=None,  # type: ignore[arg-type] — never touched on this path
            deal_id="d",
            tenant_id="t",
            doc_id="doc-1",
            storage_key=None,
            filename="t12.xlsx",
            extraction_data=None,
            user_provided_doc_type=None,
            force=False,
        )
    )
    assert calls == ["doc-1"]
    assert fields == [{"field_name": "x", "value": 1}]
    assert (version, doc_type) == ("router:T12;extractor", "T12")


def test_suite_runs_on_the_canned_backend_from_evals() -> None:
    import app.api.documents as documents

    backend = documents.current_extraction_backend()
    assert backend.name == "mock-evals"
    assert backend.critic_narrative_pass is False
    assert backend is not documents.LLM_EXTRACTION_BACKEND


def test_golden_set_harness_installs_the_seam_without_env() -> None:
    """``evals/run.py`` runs green with no ``EVALS_MOCK`` in its environment
    and reports the canned backend it installed (the CI golden-set job)."""
    env = {k: v for k, v in os.environ.items() if k != "EVALS_MOCK"}
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "evals" / "run.py"), "--junit-output", ""],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert "extraction backend: mock-evals" in proc.stderr
    assert "failed=0" in proc.stdout
