"""Real export builders — Excel acquisition model, IC memo PDF, IC deck PPTX.

Each builder accepts pure-Python dicts shaped like the engine outputs
(see ``evals/golden-set/kimpton-angler/expected/{model,memo}.json``) and
writes a polished file to disk. They are deliberately isolated from the
agent runtime — the API layer composes engine outputs into the input dict
and then calls the relevant builder.
"""

from __future__ import annotations

from .excel import build_excel
from .memo_pdf import build_memo_pdf
from .presentation import build_pptx

__all__ = ["build_excel", "build_memo_pdf", "build_pptx"]
