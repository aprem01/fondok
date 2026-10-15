"""FON-88 — ``scripts/check_pipeline_version.py`` against throwaway git repos.

The guard fails a commit range that touches ``apps/worker/app/extraction/``
or ``apps/worker/app/agents/`` without bumping ``EXTRACTION_PIPELINE_VERSION``
in ``apps/worker/app/api/documents.py``, unless a commit message carries the
``pipeline-version: unchanged`` trailer.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_pipeline_version.py"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _load():
    spec = importlib.util.spec_from_file_location("check_pipeline_version", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _write(repo: Path, rel: str, body: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _write(r, "apps/worker/app/api/documents.py", 'EXTRACTION_PIPELINE_VERSION = "v8"\n')
    _write(r, "apps/worker/app/extraction/registry.py", "VERSION = 1\n")
    _write(r, "apps/worker/app/agents/extractor.py", "PROMPT = 'a'\n")
    _write(r, "apps/web/src/page.tsx", "export {}\n")
    _commit(r, "base")
    _git(r, "checkout", "-q", "-b", "feature")
    return r


def test_no_relevant_change_is_ok(repo: Path) -> None:
    mod = _load()
    _write(repo, "apps/web/src/page.tsx", "export const x = 1\n")
    _commit(repo, "web only")
    assert mod.main(["--repo", str(repo)]) == 0


def test_relevant_change_without_bump_fails(repo: Path, capsys) -> None:
    mod = _load()
    _write(repo, "apps/worker/app/extraction/registry.py", "VERSION = 2\n")
    _commit(repo, "tweak registry")
    assert mod.main(["--repo", str(repo)]) == 1
    err = capsys.readouterr().err
    assert "apps/worker/app/extraction/registry.py" in err
    assert "'v8' -> 'v8'" in err
    assert "pipeline-version: unchanged" in err  # the fix is spelled out


def test_agents_dir_is_relevant_too(repo: Path) -> None:
    mod = _load()
    _write(repo, "apps/worker/app/agents/extractor.py", "PROMPT = 'b'\n")
    _commit(repo, "prompt edit")
    assert mod.main(["--repo", str(repo)]) == 1


def test_relevant_change_with_bump_is_ok(repo: Path) -> None:
    mod = _load()
    _write(repo, "apps/worker/app/extraction/registry.py", "VERSION = 2\n")
    _write(repo, "apps/worker/app/api/documents.py", 'EXTRACTION_PIPELINE_VERSION = "v9"\n')
    _commit(repo, "tweak registry + bump")
    assert mod.main(["--repo", str(repo)]) == 0


def test_bump_in_a_later_commit_of_the_range_counts(repo: Path) -> None:
    mod = _load()
    _write(repo, "apps/worker/app/extraction/registry.py", "VERSION = 2\n")
    _commit(repo, "tweak registry")
    _write(repo, "apps/worker/app/api/documents.py", 'EXTRACTION_PIPELINE_VERSION = "v9"\n')
    _commit(repo, "bump")
    assert mod.main(["--repo", str(repo)]) == 0


def test_unchanged_trailer_opts_out(repo: Path) -> None:
    mod = _load()
    _write(repo, "apps/worker/app/extraction/registry.py", "VERSION = 1  # comment\n")
    _commit(repo, "refactor: comment only\n\npipeline-version: unchanged\n")
    assert mod.main(["--repo", str(repo)]) == 0


def test_trailer_must_be_on_its_own_line(repo: Path) -> None:
    mod = _load()
    _write(repo, "apps/worker/app/extraction/registry.py", "VERSION = 2\n")
    _commit(repo, "mentions pipeline-version: unchanged in prose but changes output")
    assert mod.main(["--repo", str(repo)]) == 1


def test_on_main_compares_against_first_parent(repo: Path) -> None:
    """A push to main: merge-base(HEAD, main) is HEAD itself, so the guard
    falls back to HEAD^ and still catches the missing bump."""
    mod = _load()
    _git(repo, "checkout", "-q", "main")
    _write(repo, "apps/worker/app/extraction/registry.py", "VERSION = 2\n")
    _commit(repo, "direct to main")
    assert mod.main(["--repo", str(repo)]) == 1
    _write(repo, "apps/worker/app/api/documents.py", 'EXTRACTION_PIPELINE_VERSION = "v9"\n')
    _commit(repo, "bump on main")
    # HEAD^ now is the un-bumped commit; only documents.py changed → nothing relevant
    assert mod.main(["--repo", str(repo)]) == 0


def test_explicit_base_ref(repo: Path) -> None:
    mod = _load()
    _write(repo, "apps/worker/app/extraction/registry.py", "VERSION = 2\n")
    _commit(repo, "tweak")
    assert mod.main(["--repo", str(repo), "--base", "main"]) == 1
    assert mod.main(["--repo", str(repo), "--base", "HEAD"]) == 0


def test_real_repo_version_regex_matches_documents_py() -> None:
    """The regex must read the real constant, else the guard is blind."""
    mod = _load()
    src = (_SCRIPT.parents[1] / mod.VERSION_FILE).read_text()
    m = mod.VERSION_RE.search(src)
    assert m is not None
    from app.api import documents as docs_module

    assert m.group(1) == docs_module.EXTRACTION_PIPELINE_VERSION
