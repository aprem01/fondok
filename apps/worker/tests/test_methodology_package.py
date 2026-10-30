"""packages/methodology — parser, loader invariants, text rendering (FON-120).

The package is the ONE source of the Methodology page at three levels; the
worker parses it at boot and Atlas reads it. These tests pin the dialect and
the invariants the plan's C.5 names:

* every section has ``conceptual`` or an explicit ``conceptual: none``;
  ``detailed`` likewise;
* every frontmatter anchor is a testid in the body, and vice versa;
* no slot, badge source, reason code or icon the renderers do not know;
* every ``detailed`` block that states a figure cites its source and the
  cited path exists;
* the dialect refuses raw HTML, unmatched marks and unknown attributes with
  a file:line message.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key-not-real")

from app.methodology import loader as loader_mod  # noqa: E402
from app.methodology.loader import MethodologyError, load_package, package_root  # noqa: E402
from app.methodology.parser import (  # noqa: E402
    MethodologyParseError,
    iter_blocks,
    iter_inline,
    parse_inline,
    parse_section,
)
from app.methodology.slots import KNOWN_SLOTS, client_reason_explanation  # noqa: E402
from app.methodology.text import inline_text, level_text  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]


# ───────────────────────────── the real package ─────────────────────────────


def test_package_root_is_next_to_apps() -> None:
    root = package_root()
    assert root == REPO_ROOT / "packages" / "methodology"
    assert (root / "index.yaml").exists()
    assert (root / "sections").is_dir()


def test_real_package_loads_and_validates_against_the_repo() -> None:
    pkg = load_package(repo_root=REPO_ROOT)
    assert [s.id for s in pkg.sections] == [
        "extraction", "projection", "sources", "engines", "pricing", "ic-memo",
        "reasons", "concept-registry", "lineage", "regression-baselines", "controls",
    ]
    for s in pkg.sections:
        assert s.has("conceptual") or "conceptual" in s.none_markers, s.id
        assert s.has("detailed") or "detailed" in s.none_markers, s.id
    # The admin detail exists for the four sections the plan names (C-2).
    assert {s.id for s in pkg.sections_with("detailed")} == {"projection", "sources", "engines", "pricing"}


def test_section_numbering_is_per_level() -> None:
    pkg = load_package()
    # The client page has no concept-registry section; the engineering page has it.
    assert pkg.number_of("lineage", "conceptual") == 8
    assert pkg.number_of("lineage", "engineering") == 9
    assert pkg.number_of("controls", "conceptual") == 10
    assert pkg.number_of("controls", "engineering") is None
    assert len(pkg.sections_with("conceptual")) == 10
    assert len(pkg.sections_with("engineering")) == 10


def test_every_slot_in_the_package_is_known_to_both_renderers() -> None:
    pkg = load_package()
    seen: set[str] = set()
    for s in pkg.sections:
        for lvl in s.levels.values():
            for b in iter_blocks(lvl.blocks):
                if b["type"] == "slot":
                    seen.add(b["name"])
            for n in iter_inline(lvl.blocks):
                if n["type"] == "slot":
                    seen.add(n["name"])
    assert seen <= KNOWN_SLOTS
    assert {"reason-code-table", "mgmt-fee-guidance", "mgmt-fee-disclaimer"} <= seen


def test_text_rendering_covers_every_level_without_markup() -> None:
    pkg = load_package()
    for s in pkg.sections:
        for name, lvl in s.levels.items():
            text = level_text(lvl.blocks)
            assert text.strip(), (s.id, name)
            # no dialect left over: tokens, marks, fences (a literal "<name>"
            # the markdown escaped is fine — it is prose, not markup)
            assert "{{" not in text and "**" not in text and ":::" not in text, (s.id, name)
    # Slots render from the registry: the client reason table hides the codes,
    # the engineering one prints them.
    reasons = pkg.by_id["reasons"]
    client = level_text(reasons.levels["conceptual"].blocks)
    engineering = level_text(reasons.levels["engineering"].blocks)
    assert "No source document" in client and "no_document" not in client
    assert "no_document" in engineering


def test_client_reason_explanation_strips_engine_keys() -> None:
    assert client_reason_explanation(
        "An analyst NOI schedule (noi_override_by_year / terminal_noi_override) is active."
    ) == "An analyst NOI schedule is active."
    assert client_reason_explanation("Plain sentence.") == "Plain sentence."


def test_detailed_levels_cite_sources_that_exist() -> None:
    pkg = load_package(repo_root=REPO_ROOT)
    for s in pkg.sections_with("detailed"):
        comments = s.levels["detailed"].comments
        assert comments, f"{s.id}: detailed level has no source comments"
        assert all(c.startswith("source:") for _, c in comments), s.id


# ───────────────────────────── the dialect ─────────────────────────────


def test_inline_marks_and_tokens() -> None:
    nodes = parse_inline("A **bold** and *em* with `code_x` and {{badge:seed}} {{reason:no_document}} {{slot:registry-count}}.")
    kinds = [n["type"] for n in nodes]
    assert kinds == ["text", "strong", "text", "em", "text", "code", "text", "badge", "text", "reason", "text", "slot", "text"]
    assert nodes[1]["children"] == [{"type": "text", "value": "bold"}]
    assert nodes[5]["value"] == "code_x"
    assert nodes[7]["source"] == "seed"
    assert nodes[9]["code"] == "no_document"


def test_inline_escapes_and_hard_break() -> None:
    nodes = parse_inline("not \\*bold\\* and \\<reason\\> \\\nnext line")
    assert inline_text(nodes) == "not *bold* and <reason> \nnext line"


@pytest.mark.parametrize(
    "text,fragment",
    [
        ("raw <b>html</b>", "raw HTML is not allowed"),
        ("an **unmatched strong", "unmatched"),
        ("{{slot:}}", "malformed token"),
        ("[bad](javascript:alert(1))", "href must be internal"),
    ],
)
def test_inline_rejects(text: str, fragment: str) -> None:
    with pytest.raises(MethodologyParseError, match=fragment):
        parse_inline(text, path="x.md", line=7)


_SECTION = """---
id: demo
title: "Demo"
decisions: []
anchors:
  - demo-rule
  - demo-para
  - demo-item
---
<!-- provenance comment -->
::: conceptual
::: intro
The intro.
:::

::: card {testid=demo-rule}
#### A heading

{testid=demo-para} A paragraph with **bold** text
that wraps.

- {testid=demo-item} **Label.** Text
- plain item

| A | B |
| --- | --- |
| 1 | `x_y` |

{{slot:reason-code-table}}
:::
:::
::: detailed {title="Demo — detail"}
<!-- source: apps/worker/app/engines/returns.py -->
Exit cap default 7.0%.
:::
"""


def test_parse_section_structure() -> None:
    parsed = parse_section(_SECTION, path="demo.md")
    assert parsed.frontmatter["id"] == "demo"
    assert set(parsed.levels) == {"conceptual", "detailed"}
    blocks = parsed.levels["conceptual"].blocks
    assert blocks[0]["type"] == "container" and blocks[0]["kind"] == "intro"
    card = blocks[1]
    assert card["kind"] == "card" and card["attrs"] == {"testid": "demo-rule"}
    kinds = [b["type"] for b in card["children"]]
    assert kinds == ["heading", "paragraph", "list", "table", "slot"]
    para = card["children"][1]
    assert para["attrs"] == {"testid": "demo-para"}
    # soft line break joins with a space — no stray newline in the text node
    assert inline_text(para["inline"]) == "A paragraph with bold text that wraps."
    lst = card["children"][2]
    assert lst["items"][0]["attrs"] == {"testid": "demo-item"}
    assert lst["items"][1]["attrs"] == {}
    assert parsed.levels["detailed"].title == "Demo — detail"
    assert parsed.levels["detailed"].comments and parsed.levels["detailed"].comments[0][1].startswith("source:")


@pytest.mark.parametrize(
    "mutation,fragment",
    [
        (lambda s: s.replace("::: card", "::: panel"), "unknown container kind"),
        (lambda s: s.replace("{testid=demo-rule}", "{test-id=demo-rule}"), "unknown attribute"),
        (lambda s: s.replace("#### A heading", "## A heading"), "headings inside a section"),
        (lambda s: s.replace("::: conceptual", "::: conceptual\n::: conceptual"), "not closed"),
    ],
)
def test_parse_section_rejects(mutation, fragment: str) -> None:
    with pytest.raises(MethodologyParseError, match=fragment):
        parse_section(mutation(_SECTION), path="demo.md")


# ───────────────────────────── loader validation ─────────────────────────────


def _write_package(tmp_path: Path, section: str, *, index_extra: str = "") -> Path:
    root = tmp_path / "methodology"
    (root / "sections").mkdir(parents=True)
    (root / "index.yaml").write_text(
        "version: 1\n"
        "pages:\n"
        "  conceptual: {eyebrow: M, title: T, subtitle: S}\n"
        "  detailed: {eyebrow: M, title: T, subtitle: S}\n"
        "  engineering: {eyebrow: M, title: T, subtitle: S}\n"
        "sections:\n"
        "  - id: demo\n    file: 01-demo.md\n    keywords: [demo]\n"
        f"{index_extra}",
        encoding="utf-8",
    )
    (root / "sections" / "01-demo.md").write_text(section, encoding="utf-8")
    return root


def test_loader_accepts_a_minimal_valid_package(tmp_path: Path) -> None:
    root = _write_package(tmp_path, _SECTION)
    pkg = load_package(root, repo_root=REPO_ROOT)
    assert [s.id for s in pkg.sections] == ["demo"]
    assert pkg.by_id["demo"].anchors == ["demo-rule", "demo-para", "demo-item"]
    assert pkg.by_id["demo"].level_title("detailed") == "Demo — detail"
    assert pkg.by_id["demo"].level_title("conceptual") == "Demo"


@pytest.mark.parametrize(
    "mutation,fragment",
    [
        # a testid in the body that the frontmatter does not list
        (lambda s: s.replace("  - demo-para\n", ""), "testid=demo-para is in the body but not listed"),
        # a frontmatter anchor nothing renders
        (lambda s: s.replace("  - demo-rule", "  - demo-rule\n  - ghost"), "anchor 'ghost' is listed"),
        # a detailed figure without a citation
        (lambda s: s.replace("<!-- source: apps/worker/app/engines/returns.py -->\n", ""), "no '<!-- source"),
        # a cited path that does not exist
        (lambda s: s.replace("apps/worker/app/engines/returns.py", "apps/worker/app/engines/nope.py"), "does not exist"),
        # an unknown slot
        (lambda s: s.replace("{{slot:reason-code-table}}", "{{slot:nope}}"), "unknown block slot"),
        # a badge source the registry does not have
        (lambda s: s.replace("**bold**", "{{badge:not_a_source}}"), "not in the registry"),
        # detailed removed with no marker
        (lambda s: s[: s.index("::: detailed")], "no '::: detailed' level"),
    ],
)
def test_loader_rejects(tmp_path: Path, mutation, fragment: str) -> None:
    section = mutation(_SECTION)
    root = _write_package(tmp_path, section)
    with pytest.raises(MethodologyError, match=fragment):
        load_package(root, repo_root=REPO_ROOT)


def test_loader_requires_the_explicit_none_marker_only_when_the_level_is_absent(tmp_path: Path) -> None:
    section = _SECTION.replace("anchors:", "detailed: none\nanchors:")
    root = _write_package(tmp_path, section)
    with pytest.raises(MethodologyError, match="marker but a '::: detailed' level is present"):
        load_package(root, repo_root=REPO_ROOT)


def test_file_name_must_match_order_and_id(tmp_path: Path) -> None:
    root = _write_package(tmp_path, _SECTION)
    (root / "sections" / "01-demo.md").rename(root / "sections" / "02-demo.md")
    (root / "index.yaml").write_text(
        (root / "index.yaml").read_text().replace("file: 01-demo.md", "file: 02-demo.md")
    )
    with pytest.raises(MethodologyError, match="must be filed as 01-demo.md"):
        load_package(root, repo_root=REPO_ROOT)


def test_env_override_points_the_worker_at_another_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FONDOK_METHODOLOGY_DIR", str(tmp_path / "elsewhere"))
    assert loader_mod.package_root() == tmp_path / "elsewhere"
