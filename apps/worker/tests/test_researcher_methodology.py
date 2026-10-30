"""Atlas reads the methodology (FON-120, plan C-3 / C.5).

* a "how is the levered IRR calculated" question selects the ``pricing``
  section and always carries ``reasons`` and ``lineage``;
* the block is capped (~8k tokens) and places the always-included sections
  before a long matched one, dropping ``detailed`` text before skipping a
  section;
* the SYSTEM_PROMPT carries the rule (answer from the block, cite
  ``(methodology, <section id>)``, never derive an unstated formula);
* a citation with ``methodology_section`` projects to
  ``ResearcherCitation.methodology_section`` with ``document_id="methodology"``,
  survives the unknown-document filter only when the block carried that
  section, and reaches ``AskCitation``.

``agents/researcher.py`` sits under the extraction pipeline-version guard
directory; this change is not an extraction change — the commit carries
``pipeline-version: unchanged``.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key-not-real")

from app.agents.researcher import (  # noqa: E402
    METHODOLOGY_DOCUMENT_ID,
    SYSTEM_PROMPT,
    ResearcherCitation,
    _CitationEnv,
    _project_citations,
    build_methodology_system_block,
)
from app.api.dossier import AskCitation  # noqa: E402
from app.methodology import build_selection, load_package, select_section_ids  # noqa: E402


@pytest.fixture(scope="module")
def pkg():
    return load_package()


def test_irr_question_selects_pricing_plus_the_always_included_sections(pkg) -> None:
    ids = select_section_ids("How is the levered IRR calculated?", pkg)
    assert ids[0] == "pricing"
    assert "reasons" in ids and "lineage" in ids
    sel = build_selection("How is the levered IRR calculated?", pkg)
    # index order in the block; both levels of pricing present
    assert sel.section_ids == ["pricing", "reasons", "lineage"]
    assert "### pricing — " in sel.text and "[detailed]" in sel.text
    assert "Max Price Solver" in sel.text
    assert sel.skipped == [] and sel.dropped_detailed == []


def test_block_is_capped_and_never_crowds_out_reasons_and_lineage(pkg) -> None:
    q = "What exit cap rate does the model use and why is the Year 3 stabilized NOI a dash?"
    sel = build_selection(q, pkg)
    assert len(sel.text) <= pkg.atlas_max_chars
    assert {"reasons", "lineage"} <= set(sel.section_ids)
    assert "projection" in sel.section_ids
    # A tiny budget keeps the always-included sections and drops the rest.
    tiny = build_selection(q, pkg, max_chars=9000)
    assert tiny.section_ids == ["reasons", "lineage"]
    assert "projection" in tiny.skipped


def test_keywords_match_on_word_boundaries(pkg) -> None:
    """Review finding: substring matching let generic words (model, str, pin,
    keys, note, sale) pull the two longest sections into every prompt. A
    keyword now matches a whole word (``str`` is not ``structure``, ``pin`` is
    not ``pinned``); a trailing ``*`` is an explicit prefix match."""
    from app.methodology.select import keyword_pattern

    assert not keyword_pattern("str").search("the structure of this deal")
    assert not keyword_pattern("pin").search("the noi is pinned")
    assert keyword_pattern("pinned").search("the noi is pinned")
    assert keyword_pattern("stabiliz*").search("the stabilization year")
    assert keyword_pattern("p&l").search("on the p&l tab") and not keyword_pattern("p&l").search("p&ls")
    assert keyword_pattern("t-12").search("from the t-12") and not keyword_pattern("t-12").search("t-120")
    # no index.yaml keyword is a bare generic word any more
    generic = {"model", "str", "note", "sale", "pin", "keys", "source", "market", "index", "page", "trace", "engine"}
    for s in pkg.sections:
        assert not (set(s.keywords) & generic), (s.id, set(s.keywords) & generic)


def test_generic_model_question_selects_engines_not_sources(pkg) -> None:
    """The reviewer's probe: 'structure' is the capital / debt structure
    (engines); nothing in it is a sources question. Before the fix this pulled
    sources + engines (31k chars) and the cap dropped the engines detail."""
    q = "What does the model say about the structure of this deal?"
    ids = select_section_ids(q, pkg)
    assert "engines" in ids and "sources" not in ids and "projection" not in ids
    sel = build_selection(q, pkg)
    assert sel.section_ids == ["engines", "reasons", "lineage"]
    # the relevant section keeps its detailed level under the default cap
    assert sel.dropped_detailed == [] and sel.skipped == []
    assert "### engines — " in sel.text and "[detailed]" in sel.text
    assert "Debt — defaults and clamps" in sel.text
    assert len(sel.text) <= pkg.atlas_max_chars


def test_cap_keeps_the_relevant_detailed_level(pkg) -> None:
    """With one matched section the budget always has room for its detailed
    text, even for the longest section on the page (projection)."""
    for q, sid in (
        ("What exit cap rate does the model use?", "projection"),
        ("Which STR report feeds the comp set?", "sources"),
        ("How is the levered IRR calculated?", "pricing"),
    ):
        sel = build_selection(q, pkg)
        assert sel.section_ids[0] == sid, (q, sel.section_ids)
        assert sid not in sel.dropped_detailed, (q, sel.dropped_detailed)
        assert f"### {sid} — " in sel.text and "[detailed]" in sel.text


def test_unrelated_question_still_carries_reasons_and_lineage(pkg) -> None:
    sel = build_selection("Tell me about this deal", pkg)
    assert sel.section_ids == ["reasons", "lineage"]


def test_block_text_is_plain_and_cites_sections(pkg) -> None:
    text, ids = build_methodology_system_block("How is the levered IRR calculated?")
    assert ids == ["pricing", "reasons", "lineage"]
    assert text.startswith("=== METHODOLOGY")
    assert "cite as (methodology, <section id>)" in text
    assert "{{" not in text and "**" not in text and ":::" not in text
    # the dash reason table reaches Atlas with labels, not codes
    assert "No source document" in text


def test_system_prompt_carries_the_methodology_rule() -> None:
    flat = " ".join(SYSTEM_PROMPT.split())
    assert "(methodology, <section id>)" in flat
    assert "Never derive or restate a formula, default or precedence rule the block does not state" in flat
    assert "methodology_section" in flat


def test_citation_envelope_accepts_methodology_section() -> None:
    env = _CitationEnv(document_id=METHODOLOGY_DOCUMENT_ID, methodology_section="pricing")
    assert env.methodology_section == "pricing"
    with pytest.raises(Exception):
        _CitationEnv(document_id="methodology", methodology_section="pricing", extra="no")  # type: ignore[call-arg]


def test_projection_keeps_methodology_citations_for_sections_in_the_block() -> None:
    cites = [
        _CitationEnv(document_id="doc-1", page=3),
        _CitationEnv(document_id=METHODOLOGY_DOCUMENT_ID, methodology_section="pricing", excerpt="lower-of rule"),
        _CitationEnv(document_id=METHODOLOGY_DOCUMENT_ID, methodology_section="controls"),  # not in the block
        _CitationEnv(document_id="doc-ghost", page=1),  # unknown document
    ]
    out = _project_citations(cites, {"doc-1"}, {"pricing", "reasons", "lineage"})
    assert [c.document_id for c in out] == ["doc-1", METHODOLOGY_DOCUMENT_ID]
    m = out[1]
    assert isinstance(m, ResearcherCitation)
    assert m.methodology_section == "pricing" and m.page is None and m.excerpt == "lower-of rule"


def test_projection_without_a_block_drops_methodology_citations() -> None:
    cites = [_CitationEnv(document_id=METHODOLOGY_DOCUMENT_ID, methodology_section="pricing")]
    assert _project_citations(cites, {"doc-1"}) == []
    assert _project_citations(cites, {"doc-1"}, set()) == []


def test_ask_citation_carries_the_section() -> None:
    c = AskCitation(document_id=METHODOLOGY_DOCUMENT_ID, methodology_section="lineage")
    assert c.model_dump()["methodology_section"] == "lineage"
    assert AskCitation(document_id="doc-1", page=2).methodology_section is None
