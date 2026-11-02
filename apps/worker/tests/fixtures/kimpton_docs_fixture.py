"""The Kimpton Angler DOCUMENTS fixture — topline methodology v2 (FON-116 /
FON-117, ``topline-growth-plan.md`` §5.1.2). A TEST FIXTURE, seeded explicitly.

The seed fixture (``kimpton_seed``) has no documents, so under topline v2 it
proves the "no market forecast" state is deterministic. This fixture proves
the full path: a database deal carrying the deal's own market documents, run
through ``run_all_engines`` exactly as production runs a deal (no
``assumptions=`` seam, no demo branch):

* ``ANG-20250500-USD-E.xlsx`` — the May 2025 STAR report, as the STR
  template extractor reads it (``evals/golden-set/kimpton-angler/input/
  str_ang_2025_05_extracted.json``): the competitive set the reference
  Index Analysis tab keys for 2025.
* ``Miami Beach-Hospitality-Submarket-2025-12-10.pdf`` — the CoStar
  submarket report's class-bucket series (``costar_submarket_extracted.json``).
* ``field_overrides`` = the reference Index Analysis inputs
  (:data:`KIMPTON_REFERENCE_SCENARIO`) + the Kimpton partnership terms + an
  acquisition close of 2025-12-31, which places operating Year 1 on calendar
  2026 so the model years line up with the reference tab's columns (the
  reference model's own close, 2025-09-30, puts Fondok's Year 1 on 2025).
* the deal row: 132 keys, $36.4M, positioning ``upper-upscale`` (the CoStar
  Luxury & Upper Upscale bucket).

Every other assumption is the production default set (which the seed fixture
mirrors). Ids and timestamps are fixed so the run is byte-reproducible.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .kimpton_seed import (
    KIMPTON_KEYS,
    KIMPTON_PURCHASE_PRICE,
    kimpton_partnership_field_overrides,
)

KIMPTON_DOCS_FIXTURE_DEAL_ID = "5b0c1f0e-6d43-5c1b-9a7e-2f6b1d0c4a11"
KIMPTON_DOCS_FIXTURE_TENANT_ID = "5b0c1f0e-6d43-5c1b-9a7e-2f6b1d0c4a10"

_INPUT = Path(__file__).resolve().parents[4] / "evals" / "golden-set" / "kimpton-angler" / "input"

#: The reference Index Analysis tab's analyst inputs in Fondok's keys (index
#: y−1 = model year y = calendar 2025 + y): 2026 growth 0.0% / −2.0% (the
#: tab's own), 2027–2029 from the CoStar forecast (unset here), 2030 0.5% /
#: 2.0%; occupancy penetration 100 every year, ADR 97 / 98 / 99 / 100.
KIMPTON_REFERENCE_SCENARIO: dict[str, Any] = {
    "occupancy_growth_by_year": [0.0, None, None, None, 0.005],
    "adr_growth_by_year": [-0.02, None, None, None, 0.02],
    "index_mpi_target": 100,
    "index_ari_target": 100,
    "index_mpi_target_by_year": [100, 100, 100, 100],
    "index_ari_target_by_year": [97, 98, 99, 100],
}

_DOCS: tuple[tuple[str, str, str], ...] = (
    # (document id, input file, doc_type)
    ("5b0c1f0e-6d43-5c1b-9a7e-2f6b1d0c4a21", "str_ang_2025_05_extracted.json", "STR_TREND"),
    ("5b0c1f0e-6d43-5c1b-9a7e-2f6b1d0c4a22", "costar_submarket_extracted.json", "MARKET_STUDY"),
)
_NOW = datetime(2025, 12, 31, 12, 0, tzinfo=UTC)


def kimpton_docs_overrides() -> dict[str, Any]:
    return {
        **kimpton_partnership_field_overrides(),
        **json.loads(json.dumps(KIMPTON_REFERENCE_SCENARIO)),
        "acquisition_close_date": "2025-12-31",
    }


async def seed_kimpton_docs_deal(session: Any) -> tuple[str, str]:
    """Insert the deal row, its two documents and their extractions.
    Returns ``(deal_id, tenant_id)``."""
    from sqlalchemy import text

    deal_id, tenant_id = KIMPTON_DOCS_FIXTURE_DEAL_ID, KIMPTON_DOCS_FIXTURE_TENANT_ID
    await session.execute(
        text(
            """
            INSERT INTO deals (id, tenant_id, name, status, keys, purchase_price,
                               positioning, field_overrides, created_at, updated_at)
            VALUES (:id, :tenant, 'Kimpton Angler (documents fixture)', 'Draft', :keys,
                    :price, 'upper-upscale', :ov, :now, :now)
            """
        ),
        {
            "id": deal_id,
            "tenant": tenant_id,
            "keys": KIMPTON_KEYS,
            "price": KIMPTON_PURCHASE_PRICE,
            "ov": json.dumps(kimpton_docs_overrides()),
            "now": _NOW,
        },
    )
    for i, (doc_id, name, doc_type) in enumerate(_DOCS):
        payload = json.loads((_INPUT / name).read_text())
        await session.execute(
            text(
                """
                INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type,
                                       status, uploaded_at)
                VALUES (:id, :deal, :tenant, :fn, :dt, 'EXTRACTED', :now)
                """
            ),
            {"id": doc_id, "deal": deal_id, "tenant": tenant_id, "fn": payload["filename"],
             "dt": doc_type, "now": _NOW},
        )
        await session.execute(
            text(
                """
                INSERT INTO extraction_results (id, document_id, deal_id, tenant_id,
                                                fields, confidence_report,
                                                agent_version, created_at)
                VALUES (:id, :doc, :deal, :tenant, :f, '{}', 'fixture', :at)
                """
            ),
            {
                "id": doc_id[:-2] + f"b{i}",
                "doc": doc_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "f": json.dumps(payload["fields"]),
                "at": _NOW,
            },
        )
    await session.commit()
    return deal_id, tenant_id


__all__ = [
    "KIMPTON_DOCS_FIXTURE_DEAL_ID",
    "KIMPTON_DOCS_FIXTURE_TENANT_ID",
    "KIMPTON_REFERENCE_SCENARIO",
    "kimpton_docs_overrides",
    "seed_kimpton_docs_deal",
]
