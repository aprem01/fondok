"""``scripts/check_golden_gate.py`` — the CI gate around the Kimpton golden.

The wrapper runs ``scripts/compare_golden_baseline.py --exit-nonzero-on-headline-diff``
and must:

* pass (0) when the compare reports no headline differences;
* FAIL (1) when the compare exits 3 (fixture moved) and no commit in the
  range carries the ``golden: expected`` trailer;
* pass (0) when the compare exits 3 and a commit in the range carries it;
* never pass when the compare could not run (exit 2 → 2).

The compare itself is substituted by a tiny script with a scripted exit code
so these run in milliseconds; one test at the bottom drives the REAL compare
script against a deliberately altered baseline to prove the flag it relies on.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_GATE = _SCRIPTS / "check_golden_gate.py"
_COMPARE = _SCRIPTS / "compare_golden_baseline.py"
_REPO = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _load():
    spec = importlib.util.spec_from_file_location("check_golden_gate", _GATE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "README").write_text("base\n")
    _commit(r, "base")
    _git(r, "checkout", "-q", "-b", "feature")
    return r


@pytest.fixture
def fake_compare(tmp_path: Path) -> Path:
    """A stand-in compare script: exits with the code in ``EXIT`` file and
    records the argv it was called with, so the test can prove the wrapper
    passed the gate flag."""
    script = tmp_path / "fake_compare.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "here = pathlib.Path(__file__).parent\n"
        "(here / 'argv.json').write_text(json.dumps(sys.argv[1:]))\n"
        "print('fake compare report')\n"
        "sys.exit(int((here / 'EXIT').read_text()))\n"
    )
    return script


def _cmd(fake: Path, exit_code: int) -> str:
    (fake.parent / "EXIT").write_text(str(exit_code))
    return f"{sys.executable} {fake} --exit-nonzero-on-headline-diff"


def test_no_differences_passes(repo: Path, fake_compare: Path, capsys) -> None:
    mod = _load()
    assert mod.main(["--repo", str(repo), "--compare-cmd", _cmd(fake_compare, 0)]) == 0
    assert "no headline differences — ok" in capsys.readouterr().out


def test_headline_diff_without_trailer_fails(repo: Path, fake_compare: Path, capsys) -> None:
    mod = _load()
    (repo / "engine.py").write_text("x = 2\n")
    _commit(repo, "change a calculation")
    assert mod.main(["--repo", str(repo), "--compare-cmd", _cmd(fake_compare, 3)]) == 1
    err = capsys.readouterr().err
    assert "FAIL" in err
    assert "golden: expected" in err  # the fix is spelled out
    assert "--write evals/baselines/kimpton-golden-" in err


def test_headline_diff_with_trailer_passes(repo: Path, fake_compare: Path, capsys) -> None:
    mod = _load()
    (repo / "engine.py").write_text("x = 2\n")
    _commit(repo, "Move exit cap rate to the Year-4 convention\n\nRe-baselined.\n\ngolden: expected\n")
    assert mod.main(["--repo", str(repo), "--compare-cmd", _cmd(fake_compare, 3)]) == 0
    out = capsys.readouterr().out
    assert "golden: expected" in out
    assert "ok" in out


def test_trailer_is_case_insensitive_but_must_be_its_own_line(repo: Path, fake_compare: Path) -> None:
    mod = _load()
    _commit(repo, "a\n\nGolden: Expected\n")
    assert mod.main(["--repo", str(repo), "--compare-cmd", _cmd(fake_compare, 3)]) == 0
    # A mention inside a sentence is not consent.
    r2 = repo.parent / "repo2"
    shutil.copytree(repo, r2)
    _git(r2, "reset", "-q", "--hard", "HEAD^")
    _commit(r2, "a\n\nI did not add golden: expected because the move is wrong\n")
    assert mod.main(["--repo", str(r2), "--compare-cmd", _cmd(fake_compare, 3)]) == 1


def test_trailer_only_counts_inside_the_range(repo: Path, fake_compare: Path) -> None:
    """A trailer on an OLD commit (already on main) does not bless a new move."""
    mod = _load()
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "earlier deliberate move\n\ngolden: expected\n")
    _git(repo, "checkout", "-q", "feature")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "rebase", "-q", "main")
    _commit(repo, "accidental move")
    assert mod.main(["--repo", str(repo), "--compare-cmd", _cmd(fake_compare, 3)]) == 1


def test_cannot_run_is_never_a_pass(repo: Path, fake_compare: Path, capsys) -> None:
    mod = _load()
    _commit(repo, "whatever\n\ngolden: expected\n")  # even with the trailer
    assert mod.main(["--repo", str(repo), "--compare-cmd", _cmd(fake_compare, 2)]) == 2
    assert "could not run" in capsys.readouterr().err


def test_default_compare_cmd_passes_the_gate_flag(fake_compare: Path, repo: Path, monkeypatch) -> None:
    mod = _load()
    monkeypatch.setattr(mod, "COMPARE_SCRIPT", fake_compare)
    (fake_compare.parent / "EXIT").write_text("0")
    assert mod.main(["--repo", str(repo)]) == 0
    argv = json.loads((fake_compare.parent / "argv.json").read_text())
    assert argv == ["--exit-nonzero-on-headline-diff"]


def test_on_main_itself_the_base_is_the_parent(repo: Path, fake_compare: Path) -> None:
    """A push to main: HEAD is main's tip, so the range is HEAD^..HEAD."""
    mod = _load()
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "landed on main\n\ngolden: expected\n")
    assert mod.main(["--repo", str(repo), "--compare-cmd", _cmd(fake_compare, 3)]) == 0
    _commit(repo, "a second landing without the trailer")
    assert mod.main(["--repo", str(repo), "--compare-cmd", _cmd(fake_compare, 3)]) == 1


# ─── the real compare script's flag ─────────────────────────────────────────


def _run_compare(*extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_COMPARE), *extra],
        capture_output=True, text=True, timeout=300, cwd=str(_REPO),
    )


@pytest.mark.slow
def test_real_compare_flag_exits_3_only_when_the_fixture_moved(tmp_path: Path) -> None:
    """Prove the flag the gate relies on against the committed baseline: a
    clean baseline exits 0 with or without the flag; a baseline with one
    headline value altered exits 0 as a report and 3 as a gate."""
    import importlib

    spec = importlib.util.spec_from_file_location("compare_golden_baseline", _COMPARE)
    cmp_mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(cmp_mod)
    baseline_path = Path(cmp_mod.DEFAULT_BASELINE)
    baseline = json.loads(baseline_path.read_text())

    # Alter one headline number in a copy: the returns engine's levered IRR
    # is a headline field on every build of run_compare's field list.
    returns = baseline["engines"]["returns"]["outputs"]
    key = next(k for k in ("levered_irr", "unlevered_irr") if isinstance(returns.get(k), (int, float)))
    returns[key] = float(returns[key]) + 0.05
    moved = tmp_path / "moved.json"
    moved.write_text(json.dumps(baseline))

    clean = _run_compare("--exit-nonzero-on-headline-diff")
    assert clean.returncode == 0, (clean.stdout[-800:], clean.stderr[-800:])
    assert "No headline differences" in clean.stdout

    report = _run_compare("--baseline", str(moved))
    assert report.returncode == 0, (report.stdout[-800:], report.stderr[-800:])
    assert "Headline fields changed: 0" not in report.stdout

    gated = _run_compare("--baseline", str(moved), "--exit-nonzero-on-headline-diff")
    assert gated.returncode == 3, (gated.stdout[-800:], gated.stderr[-800:])
