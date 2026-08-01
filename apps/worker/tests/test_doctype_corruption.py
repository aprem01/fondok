"""Regression tests for the doc_type corruption root causes (FON-18).

Two bugs let non-canonical doc_type tokens reach the DB, silently dropping
docs from coverage + P&L ranking:

  1. EXTRACTOR — the extraction cache-hit path read the graph ROUTE
     ("extractor") back out of ``agent_version`` and used it as the
     doc_type. Fix: encode the real doc_type in a ``dt:`` segment and
     recover THAT (falling back to the filename guess on legacy rows).

  2. PNLMONTHLY — the post-extraction refinement bound the underscore-
     STRIPPED comparison form (``_canonical_doc_type``) as the persisted
     doc_type, turning "PNL_MONTHLY" into "PNLMONTHLY". Fix: bind the real
     enum value. (Covered end-to-end via reclassify + coverage; here we
     lock the agent_version round-trip that the cache path depends on.)
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

from app.api.documents import (  # noqa: E402
    _parse_doc_type_from_agent_version,
    _parse_route_from_agent_version,
)


def test_dt_segment_recovers_real_doc_type() -> None:
    av = "router:extract;dt:PNL_MONTHLY;extractor;pv=v3"
    assert _parse_doc_type_from_agent_version(av) == "PNL_MONTHLY"
    # The route parser still returns the route, unaffected by dt:.
    assert _parse_route_from_agent_version(av) == "extract"


def test_legacy_agent_version_has_no_dt_segment() -> None:
    # Pre-fix rows carry only the route — so dt: recovery returns None and
    # the caller falls back to the filename guess (NOT the route, which is
    # what stamped "EXTRACTOR").
    legacy = "router:extractor;extractor;pv=v3"
    assert _parse_doc_type_from_agent_version(legacy) is None
    # Prove the trap: the route parser on a legacy row yields "extractor",
    # which is exactly the value that must NOT become a doc_type.
    assert _parse_route_from_agent_version(legacy) == "extractor"


def test_empty_and_mock_agent_versions() -> None:
    assert _parse_doc_type_from_agent_version(None) is None
    assert _parse_doc_type_from_agent_version("") is None
    assert _parse_doc_type_from_agent_version("mock-evals;pv=v3") is None
