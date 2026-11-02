"""R2-I2 — the API's note-required keys and the browser's mirror agree in BOTH
directions.

``apps/web/src/lib/overrideNote.ts`` decides in the browser whether Save asks
for a justification; ``api/deals.py::_override_needs_note`` decides whether the
PATCH is refused (422 ``override_note_required``). A key the browser exempts
and the API does not is a save that always fails (the topline review's I-6
blocker); the reverse asks the analyst for a note nobody stores as required.
The web suite pins the same equality from its side
(``__tests__/overrideNote.test.ts``); this one fails the WORKER's CI when a
worker change diverges.
"""

from __future__ import annotations

import re
from pathlib import Path

OVERRIDE_NOTE_TS = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "overrideNote.ts"


def _browser_exempt_keys() -> set[str]:
    src = OVERRIDE_NOTE_TS.read_text(encoding="utf-8")
    start = src.index("const NOTE_EXEMPT_KEYS")
    body = src[start : src.index("]);", start)]
    # Strip line comments, then read the quoted keys.
    body = "\n".join(line.split("//", 1)[0] for line in body.splitlines())
    return set(re.findall(r"'([^']+)'", body))


def _browser_patterns() -> set[str]:
    src = OVERRIDE_NOTE_TS.read_text(encoding="utf-8")
    start = src.index("const NOTE_EXEMPT_PATTERNS")
    body = src[start : src.index("];", start)]
    body = "\n".join(line.split("//", 1)[0] for line in body.splitlines())
    return set(re.findall(r"^\s*/(.+)/,?\s*$", body, flags=re.M))


def test_exempt_override_keys_match_in_both_directions() -> None:
    from app.api.deals import _NOTE_EXEMPT_KEYS
    from app.services.engine_runner import _OVERRIDE_NON_ENGINE_KEYS

    worker = set(_NOTE_EXEMPT_KEYS) | set(_OVERRIDE_NON_ENGINE_KEYS)
    browser = _browser_exempt_keys()
    assert browser, "could not read NOTE_EXEMPT_KEYS from overrideNote.ts"
    assert sorted(browser - worker) == [], "exempt in the browser but note-required by the API"
    assert sorted(worker - browser) == [], "exempt in the API but note-required by the browser"


def test_exempt_patterns_match() -> None:
    from app.api.deals import _NOTE_EXEMPT_PATTERNS

    assert {p.pattern for p in _NOTE_EXEMPT_PATTERNS} == _browser_patterns()


def test_the_topline_blockers_need_a_note_on_both_sides() -> None:
    from app.api.deals import _override_needs_note
    from app.services.engine_runner import _OVERRIDE_NON_ENGINE_KEYS

    browser = _browser_exempt_keys()
    for key in ("stabilization_year", "topline_benchmark_level", "index_methodology"):
        assert _override_needs_note(key, _OVERRIDE_NON_ENGINE_KEYS)
        assert key not in browser
