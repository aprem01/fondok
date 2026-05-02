"""Deal Dossier — semantically-organized snapshot of a deal.

A dossier composes data from documents, extraction_results,
engine_outputs, variance flags, critic findings, and the deal row
itself into a single typed object an LLM can reason over end-to-end.

Treat it as the "Context Data Product" surface for a deal — the input
to the Researcher Q&A agent, the input to the Analyst memo agent, and
the export shape for institutional review packages. It's deliberately
read-only and pure-composition: no side effects, no LLM calls.
"""

from .schema import (
    DealDossier,
    DossierCitation,
    DossierDocument,
    DossierEngine,
    DossierField,
    DossierVarianceFlag,
)
from .builder import build_dossier

__all__ = [
    "DealDossier",
    "DossierCitation",
    "DossierDocument",
    "DossierEngine",
    "DossierField",
    "DossierVarianceFlag",
    "build_dossier",
]
