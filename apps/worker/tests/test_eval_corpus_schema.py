"""Schema check for the labelled extraction corpus (``evals/corpus/``).

Runs ``evals/corpus/validate_corpus.py`` in-process. No database, no network, no
worker imports — the validator is stdlib-only and reads the manifest + label JSON.
"""

from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path

# Keep the session-wide sqlite fixture in conftest.py pointed at a throwaway file so a
# developer's shell-level DATABASE_URL never leaks into this test module.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-eval-corpus.db"
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DB}")

REPO_ROOT = Path(__file__).resolve().parents[3]
VALIDATOR = REPO_ROOT / "evals" / "corpus" / "validate_corpus.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_corpus", VALIDATOR)
    assert spec and spec.loader, VALIDATOR
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_corpus_files_exist() -> None:
    corpus = REPO_ROOT / "evals" / "corpus"
    assert (corpus / "manifest.yaml").is_file()
    assert (corpus / "README.md").is_file()
    assert (corpus / "concepts_used.txt").is_file()
    assert (corpus / "labels" / "_provisional_policy.md").is_file()
    assert any((corpus / "labels").glob("*.json"))


def test_corpus_validates(capsys) -> None:
    validator = _load_validator()
    rc = validator.main(["--quiet"])
    out = capsys.readouterr().out
    assert rc == 0, out


def test_every_confirmed_label_is_traceable() -> None:
    """Belt-and-braces on the rule that matters most: a confirmed label always says who
    confirmed it, when, and from which source."""
    import json

    labels_dir = REPO_ROOT / "evals" / "corpus" / "labels"
    seen_confirmed = 0
    for path in sorted(labels_dir.glob("*.json")):
        for lab in json.loads(path.read_text()):
            if lab["status"] == "confirmed":
                seen_confirmed += 1
                assert lab["confirmed_by"], (path.name, lab["field_name"])
                assert lab["confirmed_at"], (path.name, lab["field_name"])
                assert lab["source"], (path.name, lab["field_name"])
    assert seen_confirmed > 0
