#!/usr/bin/env python3
"""Validate the labelled extraction corpus under evals/corpus/ (stdlib only).

Checks
------
* ``manifest.yaml`` parses (a small YAML subset — see ``_parse_manifest``) and every
  case has the required keys with sane values.
* every ``labels/<case-id>.json`` is a list of label records that match the schema
  documented in ``README.md``; every case in the manifest has a label file and every
  label file has a manifest case.
* ``page`` is an int >= 1; ``tolerance`` is a non-negative number; enums are respected;
  ``concept`` is either a known registry id or ``null`` with a ``proposed_concept``.
* ``status: confirmed`` records carry ``confirmed_by``, ``confirmed_at`` (ISO date) and
  ``source``; provisional records still carry ``source`` (where the value came from).
* ``concepts_used.txt`` matches the label files (``--write-concepts`` regenerates it).

Prints confirmed / provisional counts per case and exits 1 on any failure.

Usage::

    python evals/corpus/validate_corpus.py            # validate
    python evals/corpus/validate_corpus.py --quiet    # only failures + totals
    python evals/corpus/validate_corpus.py --write-concepts
    FONDOK_CORPUS_DIR=/path/to/docs python evals/corpus/validate_corpus.py
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import sys
from pathlib import Path

CORPUS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = CORPUS_ROOT.parents[1]
MANIFEST = CORPUS_ROOT / "manifest.yaml"
LABELS_DIR = CORPUS_ROOT / "labels"
CONCEPTS_FILE = CORPUS_ROOT / "concepts_used.txt"

# Registry concept ids (Phase 5.1 hand-off list). The concept registry being built in
# parallel is the eventual source of truth; keep this list in sync with it.
REGISTRY_CONCEPTS: frozenset[str] = frozenset({
    "rooms_revenue", "fb_revenue", "other_revenue", "total_revenue",
    "rooms_dept_expense", "fb_dept_expense", "other_dept_expense", "dept_expenses_total", "dept_profit_total",
    "undistributed_total", "ag_expense", "sales_marketing", "property_ops", "utilities", "it_expense",
    "gop", "mgmt_fee", "ebitda", "property_tax", "insurance", "ffe_reserve", "fixed_charges_total", "noi",
    "occupancy", "adr", "revpar", "available_rooms", "rooms_sold", "keys",
    "purchase_price", "exit_cap_rate", "renovation_budget", "closing_costs", "working_capital",
    "loan_amount", "interest_rate", "amortization_years", "ltv",
})
BASES = ("actual", "broker", "om_history", "market")
SCOPES = ("annual", "ttm", "ytd", "quarterly", "monthly")
STATUSES = ("confirmed", "provisional")
REQUIRED_KEYS = ("concept", "field_name", "basis", "scope", "value", "unit", "page", "tolerance", "status",
                 "confirmed_by", "confirmed_at", "source")
OPTIONAL_KEYS = ("proposed_concept", "period", "cell", "raw_label", "note")
MANIFEST_CASE_KEYS = ("id", "filename", "doc_type", "path", "path_base", "document_available", "live_document_id",
                      "payload_fixture", "golden_case", "notes")
DOC_TYPES = ("PROPERTY_INFO", "CAPEX", "ROOM_MIX", "INSURANCE", "STR_TREND", "T12", "PNL", "OM", "CBRE_HORIZONS")


# ----------------------------------------------------------------------------- tiny YAML subset
def _scalar(raw: str):
    raw = raw.strip()
    if raw == "" or raw == "null" or raw == "~":
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        return json.loads(raw)  # JSON-compatible double-quoted string
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return raw[1:-1].replace("''", "'")
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _parse_manifest(text: str) -> dict:
    """Parse the YAML subset used by manifest.yaml.

    Supported: top-level ``key: scalar``; top-level ``key:`` introducing a list of
    mappings written as ``  - key: scalar`` followed by ``    key: scalar`` lines;
    comments (``# ...``) and blank lines. Nothing else — keep the manifest in this shape.
    """
    out: dict = {}
    current_list: list | None = None
    current_item: dict | None = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.split(" #", 1)[0] if not line.lstrip().startswith("#") else ""
        if not stripped.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        body = stripped.strip()
        if indent == 0:
            current_item = None
            key, sep, rest = body.partition(":")
            if not sep:
                raise ValueError(f"manifest line {lineno}: expected 'key:'")
            if rest.strip() == "":
                current_list = []
                out[key.strip()] = current_list
            else:
                current_list = None
                out[key.strip()] = _scalar(rest)
            continue
        if current_list is None:
            raise ValueError(f"manifest line {lineno}: indented line outside a list")
        if body.startswith("- "):
            current_item = {}
            current_list.append(current_item)
            body = body[2:]
        if current_item is None:
            raise ValueError(f"manifest line {lineno}: mapping line before any '- ' item")
        key, sep, rest = body.partition(":")
        if not sep:
            raise ValueError(f"manifest line {lineno}: expected 'key: value' inside list item")
        current_item[key.strip()] = _scalar(rest)
    return out


# ----------------------------------------------------------------------------- validation
class Problems:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, msg: str) -> None:
        self.items.append(msg)

    def __bool__(self) -> bool:
        return bool(self.items)


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _iso_date(s) -> bool:
    if not isinstance(s, str):
        return False
    try:
        dt.date.fromisoformat(s[:10])
        return True
    except ValueError:
        return False


def validate_label(case_id: str, idx: int, lab, problems: Problems) -> None:
    where = f"{case_id}[{idx}]"
    if not isinstance(lab, dict):
        problems.add(f"{where}: label is not an object")
        return
    for k in REQUIRED_KEYS:
        if k not in lab:
            problems.add(f"{where}: missing key '{k}'")
    unknown = set(lab) - set(REQUIRED_KEYS) - set(OPTIONAL_KEYS)
    if unknown:
        problems.add(f"{where}: unknown keys {sorted(unknown)}")
    if any(k not in lab for k in REQUIRED_KEYS):
        return
    fname = lab["field_name"]
    where = f"{case_id}[{idx}] {fname}"
    if not isinstance(fname, str) or not fname.strip():
        problems.add(f"{where}: field_name must be a non-empty string")
    concept = lab["concept"]
    if concept is None:
        pc = lab.get("proposed_concept")
        if not isinstance(pc, str) or not pc.strip():
            problems.add(f"{where}: concept is null but proposed_concept is missing")
    elif concept not in REGISTRY_CONCEPTS:
        problems.add(f"{where}: unknown concept '{concept}' (use null + proposed_concept)")
    elif lab.get("proposed_concept"):
        problems.add(f"{where}: proposed_concept is only allowed when concept is null")
    if lab["basis"] not in BASES:
        problems.add(f"{where}: basis '{lab['basis']}' not in {BASES}")
    scope = lab["scope"]
    if scope is None:
        if not lab.get("note"):
            problems.add(f"{where}: scope null (static fact) requires a note")
    elif scope not in SCOPES:
        problems.add(f"{where}: scope '{scope}' not in {SCOPES}")
    if lab["value"] is None or isinstance(lab["value"], (list, dict)):
        problems.add(f"{where}: value must be a number, string or boolean")
    unit = lab["unit"]
    if unit is not None and (not isinstance(unit, str) or not unit.strip()):
        problems.add(f"{where}: unit must be a non-empty string or null")
    page = lab["page"]
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        problems.add(f"{where}: page must be an int >= 1 (got {page!r})")
    tol = lab["tolerance"]
    if not _is_number(tol) or tol < 0:
        problems.add(f"{where}: tolerance must be a non-negative number (got {tol!r})")
    status = lab["status"]
    if status not in STATUSES:
        problems.add(f"{where}: status '{status}' not in {STATUSES}")
    src = lab["source"]
    if not isinstance(src, str) or not src.strip():
        problems.add(f"{where}: source must be a non-empty string")
    if status == "confirmed":
        if not isinstance(lab["confirmed_by"], str) or not lab["confirmed_by"].strip():
            problems.add(f"{where}: confirmed label needs confirmed_by")
        if not _iso_date(lab["confirmed_at"]):
            problems.add(f"{where}: confirmed label needs an ISO confirmed_at date (got {lab['confirmed_at']!r})")
    else:
        if lab["confirmed_by"] is not None or lab["confirmed_at"] is not None:
            problems.add(f"{where}: provisional label must not carry confirmed_by/confirmed_at")
    for k in ("period", "cell", "raw_label", "note"):
        if k in lab and lab[k] is not None and not isinstance(lab[k], str):
            problems.add(f"{where}: {k} must be a string")


def validate_manifest(manifest: dict, problems: Problems) -> dict[str, dict]:
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        problems.add("manifest: 'cases' must be a non-empty list")
        return {}
    by_id: dict[str, dict] = {}
    for i, case in enumerate(cases):
        cid = case.get("id")
        where = f"manifest case #{i} ({cid})"
        for k in MANIFEST_CASE_KEYS:
            if k not in case:
                problems.add(f"{where}: missing key '{k}'")
        if not isinstance(cid, str) or not cid:
            problems.add(f"{where}: id must be a non-empty string")
            continue
        if cid in by_id:
            problems.add(f"{where}: duplicate id")
        by_id[cid] = case
        if case.get("doc_type") not in DOC_TYPES:
            problems.add(f"{where}: doc_type {case.get('doc_type')!r} not in {DOC_TYPES}")
        if case.get("path_base") not in ("corpus_dir", "repo"):
            problems.add(f"{where}: path_base must be 'corpus_dir' or 'repo'")
        if not isinstance(case.get("document_available"), bool):
            problems.add(f"{where}: document_available must be true/false")
        if not isinstance(case.get("path"), str) or not case.get("path"):
            problems.add(f"{where}: path must be a non-empty string")
    return by_id


def corpus_dir(manifest: dict) -> Path:
    env = os.environ.get(manifest.get("corpus_dir_env") or "FONDOK_CORPUS_DIR")
    if env:
        return Path(env).expanduser()
    return REPO_ROOT / str(manifest.get("corpus_dir_default") or "")


def concepts_text(counts: collections.Counter, proposed: collections.Counter) -> str:
    lines = [
        "# Concept ids used by evals/corpus/labels/*.json (tab-separated: concept_id, label count).",
        "# Regenerate/verify with: python evals/corpus/validate_corpus.py --write-concepts",
        "# --- registry concept ids ---",
    ]
    lines += [f"{c}\t{n}" for c, n in sorted(counts.items())]
    lines.append("# --- concept: null, grouped by proposed_concept (for the registry builder to decide) ---")
    lines += [f"proposed:{c}\t{n}" for c, n in sorted(proposed.items())]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    quiet = "--quiet" in argv
    write_concepts = "--write-concepts" in argv
    problems = Problems()

    try:
        manifest = _parse_manifest(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"FAIL: cannot read manifest: {exc}")
        return 1
    cases = validate_manifest(manifest, problems)

    label_files = {p.stem: p for p in sorted(LABELS_DIR.glob("*.json"))}
    for cid in cases:
        if cid not in label_files:
            problems.add(f"case '{cid}' has no labels/{cid}.json (an empty list is fine)")
    for stem in label_files:
        if stem not in cases:
            problems.add(f"labels/{stem}.json has no manifest case")

    counts: collections.Counter = collections.Counter()
    proposed: collections.Counter = collections.Counter()
    rows: list[tuple[str, int, int]] = []
    for cid, path in label_files.items():
        try:
            labels = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.add(f"labels/{cid}.json: invalid JSON ({exc})")
            continue
        if not isinstance(labels, list):
            problems.add(f"labels/{cid}.json: top level must be a list")
            continue
        n_conf = 0
        for i, lab in enumerate(labels):
            validate_label(cid, i, lab, problems)
            if isinstance(lab, dict):
                if lab.get("status") == "confirmed":
                    n_conf += 1
                if lab.get("concept") is None:
                    if lab.get("proposed_concept"):
                        proposed[lab["proposed_concept"]] += 1
                else:
                    counts[lab["concept"]] += 1
        rows.append((cid, n_conf, len(labels) - n_conf))

    expected = concepts_text(counts, proposed)
    if write_concepts:
        CONCEPTS_FILE.write_text(expected, encoding="utf-8")
    else:
        try:
            actual = CONCEPTS_FILE.read_text(encoding="utf-8")
        except OSError:
            actual = ""
        if actual != expected:
            problems.add("concepts_used.txt is out of date — run validate_corpus.py --write-concepts")

    base = corpus_dir(manifest)
    availability: list[str] = []
    for cid, case in cases.items():
        root = base if case.get("path_base") == "corpus_dir" else REPO_ROOT
        p = root / str(case.get("path"))
        exists = p.exists()
        if case.get("document_available") and not exists:
            availability.append(f"  (info) {cid}: manifest says available but not found at {p}")

    if not quiet:
        print(f"corpus dir: {base} ({'exists' if base.exists() else 'NOT FOUND'})")
        print(f"{'case':<56} {'confirmed':>9} {'provisional':>11} {'total':>6}")
        for cid, c, p_ in rows:
            print(f"{cid:<56} {c:>9} {p_:>11} {c + p_:>6}")
    tc = sum(r[1] for r in rows)
    tp = sum(r[2] for r in rows)
    print(f"{'TOTAL':<56} {tc:>9} {tp:>11} {tc + tp:>6}   cases={len(rows)} concepts={len(counts)} proposed={len(proposed)}")
    for line in availability:
        print(line)
    if problems:
        print(f"FAIL: {len(problems.items)} problem(s)")
        for msg in problems.items:
            print("  - " + msg)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
