"""Load the market / submarket taxonomy list into ``str_markets`` — Sam #6 / FON-96.

Two input formats:

``--format str-csv`` (the original) — STR's definition list WITH vendor ids,
exported to CSV with exactly these columns (header row required, order free,
case-insensitive)::

    str_market_id, market_name, submarket_id, submarket_name, state, country

``--format costar-geography`` (FON-96) — the CoStar "Markets & Submarkets"
geography export, which carries NO vendor ids: a ``.xlsx`` workbook (the
first sheet whose header has the columns) or a CSV with ``Submarket, Market,
State`` and, optionally, ``Market Type`` (header case-insensitive). Fondok
mints deterministic taxonomy keys from the names and stores them in the
``str_market_id`` / ``submarket_id`` columns::

    market_key    = 'costar:' + slug(Market)              costar:northhaven-ts-usa
    submarket_key = market_key + '/' + slug(Submarket)    costar:northhaven-ts-usa/riverside

One market-level row per market plus one row per submarket is written.
``market_type`` is the file's Market Type (``Metro`` / ``Regional``), or —
when the column is absent or blank — inferred from the name (``"City - ST
USA"`` → metro, words → regional). Validation refuses the file when a
Market or Submarket is blank, a market carries two states, a metro name's
``ST`` differs from its State, ``(Market, Submarket)`` repeats, a Market
Type is neither Metro nor Regional or contradicts the name, or two distinct
names collide on one slug (that check is what makes the key safe). The run
prints ``markets / submarkets / states`` so the operator can compare them
with the file's own notes.

Usage (from ``apps/worker``, against the DB in ``DATABASE_URL``)::

    # Dry run (the default) — validates and prints what would change
    uv run python scripts/load_str_markets.py --format costar-geography geo.xlsx --version 2026-10-costar-geography

    # Apply — REPLACES the whole table and writes reference_datasets in the
    # same transaction; --licensed-external records CoStar's confirmation
    uv run python scripts/load_str_markets.py --format costar-geography geo.xlsx \\
        --version 2026-10-costar-geography --commit [--licensed-external]

    # List deals whose stored keys are not in the new file (names stay)
    uv run python scripts/load_str_markets.py --format costar-geography geo.xlsx \\
        --version 2026-10-costar-geography --report-orphans

``--commit`` is refused while the file has any problem; nothing is written
partially. Names are copied verbatim (whitespace tidied); this script never
invents a name, and the only values it adds are the keys and the inferred
``market_type``.

Licensing (FON-96): the CoStar export must NOT be loaded into the production
database, committed, or distributed until CoStar confirms Fondok may use it.
Loading a local / staging copy for internal QA is what the serving gate
(``reference_datasets.licensed_for_external`` + the
``MARKET_TAXONOMY_SERVE_EXTERNAL`` setting, both default off) exists for.

``str_markets`` / ``reference_datasets`` are NOT tenant-scoped: reference
data identical for every workspace, no ``tenant_id``, no customer or deal
data (see ``app/migrations.py``). A reload never touches ``deals``: a deal
keeps the keys and names it was saved with; ``--report-orphans`` lists the
deals whose keys the new file no longer carries.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import getpass
import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.services.str_markets import (
    CSV_COLUMNS,
    DATASET_NAME,
    MARKET_TYPES,
    SOURCE_COSTAR_GEOGRAPHY,
    StrMarketRow,
    infer_market_type,
    market_key,
    metro_name_state,
    primary_key,
    row_key,
    submarket_key,
)

REQUIRED = ("str_market_id", "market_name")

#: costar-geography columns (lower-cased header names).
COSTAR_REQUIRED = ("submarket", "market", "state")
COSTAR_OPTIONAL = ("market type",)


@dataclass
class ParseResult:
    rows: list[StrMarketRow] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    ignored_columns: list[str] = field(default_factory=list)
    #: SHA-256 of the source file (costar-geography); '' for the legacy CSV.
    source_sha256: str = ""
    #: costar-geography only — the file's own source tag.
    source: str | None = None

    @property
    def market_ids(self) -> set[str]:
        return {r.str_market_id for r in self.rows}

    @property
    def market_count(self) -> int:
        return len(self.market_ids)

    @property
    def submarket_count(self) -> int:
        return sum(1 for r in self.rows if r.submarket_id)

    @property
    def state_count(self) -> int:
        return len({r.state for r in self.rows if r.state})


def _tidy(v: Any) -> str | None:
    if v is None:
        return None
    t = " ".join(str(v).split())
    return t or None


# ───────────────────────── legacy STR CSV ─────────────────────────


def parse_csv(path: Path) -> ParseResult:
    """Read and validate the legacy STR CSV. Never raises on bad content —
    every problem is listed (with its line number) so one run shows them all."""
    out = ParseResult()
    try:
        fh = path.open(newline="", encoding="utf-8-sig")
    except OSError as exc:
        out.problems.append(f"cannot open {path}: {exc}")
        return out
    with fh:
        reader = csv.DictReader(fh)
        header = [(_tidy(h) or "").lower() for h in (reader.fieldnames or [])]
        missing = [c for c in CSV_COLUMNS if c not in header]
        if missing:
            out.problems.append(
                f"header is missing column(s) {missing}; expected {list(CSV_COLUMNS)}"
            )
            return out
        out.ignored_columns = [h for h in header if h and h not in CSV_COLUMNS]
        raw_names = reader.fieldnames or []
        seen: dict[str, int] = {}
        market_names: dict[str, tuple[str, int]] = {}
        for i, raw in enumerate(reader, start=2):  # line 1 is the header
            rec = {
                (_tidy(k) or "").lower(): _tidy(raw.get(k))
                for k in raw_names
            }
            if not any(rec.get(c) for c in CSV_COLUMNS):
                continue  # blank line
            bad = [c for c in REQUIRED if not rec.get(c)]
            if bad:
                out.problems.append(f"line {i}: {', '.join(bad)} is blank")
                continue
            mid = str(rec["str_market_id"])
            name = str(rec["market_name"])
            sub_id = rec.get("submarket_id")
            sub_name = rec.get("submarket_name")
            if sub_name and not sub_id:
                out.problems.append(
                    f"line {i}: submarket_name {sub_name!r} has no submarket_id"
                )
                continue
            prior = market_names.get(mid)
            if prior and prior[0] != name:
                out.problems.append(
                    f"line {i}: str_market_id {mid!r} is named {name!r} here but "
                    f"{prior[0]!r} on line {prior[1]}"
                )
                continue
            market_names.setdefault(mid, (name, i))
            key = row_key(mid, sub_id)
            if key in seen:
                out.problems.append(
                    f"line {i}: duplicate of line {seen[key]} "
                    f"(str_market_id {mid!r}, submarket_id {sub_id or '(none)'!r})"
                )
                continue
            seen[key] = i
            out.rows.append(
                StrMarketRow(
                    str_market_id=mid,
                    market_name=name,
                    submarket_id=sub_id,
                    submarket_name=sub_name,
                    state=rec.get("state"),
                    country=rec.get("country"),
                )
            )
    if not out.rows and not out.problems:
        out.problems.append("the CSV has no data rows")
    return out


# ───────────────────────── costar-geography ─────────────────────────


def _read_table(path: Path) -> tuple[list[str], list[tuple[int, list[Any]]], list[str]]:
    """``(header, [(line_no, cells), …], problems)`` from a CSV or an .xlsx
    (the first sheet whose header carries the required columns)."""
    problems: list[str] = []
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        try:
            import openpyxl

            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        except Exception as exc:
            problems.append(f"cannot open {path}: {exc}")
            return [], [], problems
        for ws in wb.worksheets:
            header: list[str] = []
            rows: list[tuple[int, list[Any]]] = []
            for i, cells in enumerate(ws.iter_rows(values_only=True), start=1):
                if not header:
                    if cells and any(c is not None and str(c).strip() for c in cells):
                        header = [(_tidy(c) or "").lower() for c in cells]
                    continue
                rows.append((i, list(cells)))
            if header and all(c in header for c in COSTAR_REQUIRED):
                return header, rows, problems
        problems.append(
            f"no sheet in {path.name} has the columns {list(COSTAR_REQUIRED)} "
            f"(sheets: {wb.sheetnames})"
        )
        return [], [], problems
    try:
        fh = path.open(newline="", encoding="utf-8-sig")
    except OSError as exc:
        problems.append(f"cannot open {path}: {exc}")
        return [], [], problems
    with fh:
        reader = csv.reader(fh)
        header = []
        rows = []
        for i, cells in enumerate(reader, start=1):
            if not header:
                if any(str(c).strip() for c in cells):
                    header = [(_tidy(c) or "").lower() for c in cells]
                continue
            rows.append((i, list(cells)))
    return header, rows, problems


def parse_costar_geography(path: Path) -> ParseResult:
    """Read and validate a CoStar geography export (CSV or .xlsx), deriving
    the taxonomy keys. Never raises on bad content — every problem is listed
    with its line number."""
    out = ParseResult(source=SOURCE_COSTAR_GEOGRAPHY)
    header, raw_rows, problems = _read_table(path)
    out.problems.extend(problems)
    if problems:
        return out
    try:
        out.source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        out.problems.append(f"cannot read {path}: {exc}")
        return out
    missing = [c for c in COSTAR_REQUIRED if c not in header]
    if missing:
        out.problems.append(
            f"header is missing column(s) {missing}; expected "
            f"{list(COSTAR_REQUIRED)} (+ optional {list(COSTAR_OPTIONAL)})"
        )
        return out
    known = set(COSTAR_REQUIRED) | set(COSTAR_OPTIONAL)
    out.ignored_columns = [h for h in header if h and h not in known]
    idx = {name: header.index(name) for name in header if name}
    has_type = "market type" in idx

    def cell(cells: list[Any], name: str) -> str | None:
        i = idx.get(name)
        return _tidy(cells[i]) if i is not None and i < len(cells) else None

    # market name → (state, line, market_type, type_line)
    markets: dict[str, dict[str, Any]] = {}
    # market name → {submarket name: line}
    subs: dict[str, dict[str, int]] = {}
    for i, cells in raw_rows:
        sub = cell(cells, "submarket")
        mkt = cell(cells, "market")
        state = cell(cells, "state")
        mtype_raw = cell(cells, "market type") if has_type else None
        if not any((sub, mkt, state, mtype_raw)):
            continue  # blank line
        if not sub:
            out.problems.append(f"line {i}: Submarket is blank")
            continue
        if not mkt:
            out.problems.append(f"line {i}: Market is blank")
            continue
        if not state:
            out.problems.append(f"line {i}: State is blank")
            continue
        inferred = infer_market_type(mkt)
        if mtype_raw:
            mtype = mtype_raw.lower()
            if mtype not in MARKET_TYPES:
                out.problems.append(
                    f"line {i}: Market Type {mtype_raw!r} is neither Metro nor Regional"
                )
                continue
            if mtype != inferred:
                out.problems.append(
                    f"line {i}: Market Type {mtype_raw!r} but the name is "
                    + ('not a "City - ST USA" market name' if mtype == "metro"
                       else 'a "City - ST USA" market name')
                    + f" ({mkt!r})"
                )
                continue
        else:
            mtype = inferred
        name_state = metro_name_state(mkt)
        if name_state is not None and name_state != state:
            out.problems.append(
                f"line {i}: State {state!r} but the market name says {name_state!r} ({mkt!r})"
            )
            continue
        prior = markets.get(mkt)
        if prior is None:
            markets[mkt] = {"state": state, "line": i, "market_type": mtype}
        elif prior["state"] != state:
            out.problems.append(
                f"line {i}: market {mkt!r} has state {state!r} here but "
                f"{prior['state']!r} on line {prior['line']}"
            )
            continue
        seen_subs = subs.setdefault(mkt, {})
        if sub in seen_subs:
            out.problems.append(
                f"line {i}: duplicate of line {seen_subs[sub]} (Market {mkt!r}, Submarket {sub!r})"
            )
            continue
        seen_subs[sub] = i

    # Slug collisions — distinct names that would share one key. Submarkets
    # are compared within their own market (two markets colliding is the
    # market-level report below, once).
    for mkt, names in subs.items():
        by_sub_key: dict[str, str] = {}
        for sub in names:
            k = submarket_key(mkt, sub)
            other = by_sub_key.get(k)
            if other is not None and other != sub:
                out.problems.append(
                    f"submarket names {other!r} and {sub!r} under {mkt!r} "
                    f"collide on one key {k!r}"
                )
                continue
            by_sub_key[k] = sub
    by_mkt_key: dict[str, str] = {}
    for mkt in markets:
        k = market_key(mkt)
        other = by_mkt_key.get(k)
        if other is not None and other != mkt:
            out.problems.append(
                f"market names {other!r} and {mkt!r} collide on one key {k!r}"
            )
            continue
        by_mkt_key[k] = mkt

    if out.problems:
        return out
    for mkt, meta in markets.items():
        country = "USA" if mkt.endswith(" USA") else None
        mk = market_key(mkt)
        out.rows.append(
            StrMarketRow(
                str_market_id=mk, market_name=mkt, submarket_id=None, submarket_name=None,
                state=meta["state"], country=country, market_type=meta["market_type"],
                source=SOURCE_COSTAR_GEOGRAPHY, dataset_version=None,
            )
        )
        for sub in subs.get(mkt, {}):
            out.rows.append(
                StrMarketRow(
                    str_market_id=mk, market_name=mkt,
                    submarket_id=submarket_key(mkt, sub), submarket_name=sub,
                    state=meta["state"], country=country, market_type=meta["market_type"],
                    source=SOURCE_COSTAR_GEOGRAPHY, dataset_version=None,
                )
            )
    if not out.rows:
        out.problems.append("the file has no data rows")
    return out


# ───────────────────────── database ─────────────────────────


async def _table_exists(conn, table: str = "str_markets") -> bool:
    if conn.dialect.name == "sqlite":
        sql = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :t"
    else:
        sql = "SELECT 1 FROM information_schema.tables WHERE table_name = :t"
    return (await conn.execute(text(sql), {"t": table})).first() is not None


async def plan_and_apply(
    engine,
    parsed: ParseResult,
    *,
    commit: bool,
    dataset_version: str | None = None,
    licensed_external: bool = False,
    licence_note: str | None = None,
    loaded_by: str | None = None,
) -> dict[str, Any]:
    """Compare the file with the table; with ``commit`` replace the table and
    (when ``dataset_version`` is given) the ``reference_datasets`` row, in
    ONE transaction.

    Returns a summary. Refuses (``applied`` False) when the file has problems.
    Reads and writes only the two reference tables (no tenant_id).
    """
    summary: dict[str, Any] = {
        "csv_rows": len(parsed.rows),
        "csv_markets": parsed.market_count,
        "csv_submarkets": parsed.submarket_count,
        "csv_states": parsed.state_count,
        "problems": list(parsed.problems),
        "applied": False,
        "dataset_version": dataset_version,
        "licensed_for_external": bool(licensed_external),
    }
    async with engine.connect() as conn:
        exists = await _table_exists(conn)
        summary["table_exists"] = exists
        current: set[str] = set()
        if exists:
            current = {
                str(r[0])
                for r in (await conn.execute(text("SELECT id FROM str_markets"))).fetchall()
            }
        incoming = {primary_key(r) for r in parsed.rows}
        summary["table_rows"] = len(current)
        summary["would_add"] = len(incoming - current)
        summary["would_remove"] = len(current - incoming)
        summary["unchanged_keys"] = len(incoming & current)

    if not commit or parsed.problems:
        return summary

    if not summary["table_exists"]:
        from app.migrations import run_startup_migrations

        await run_startup_migrations()
    async with engine.begin() as conn:
        has_extra = await _column_exists(conn, "str_markets", "market_type")
        if not has_extra:
            from app.migrations import run_startup_migrations

            await run_startup_migrations()
            has_extra = await _column_exists(conn, "str_markets", "market_type")
        await conn.execute(text("DELETE FROM str_markets"))
        extra_cols = ", market_type, source, dataset_version" if has_extra else ""
        extra_vals = ", :market_type, :source, :dataset_version" if has_extra else ""
        await conn.execute(
            text(
                f"""
                INSERT INTO str_markets (
                    id, str_market_id, market_name, submarket_id,
                    submarket_name, state, country{extra_cols}
                ) VALUES (
                    :id, :str_market_id, :market_name, :submarket_id,
                    :submarket_name, :state, :country{extra_vals}
                )
                """
            ),
            [
                {
                    "id": primary_key(r),
                    **{k: v for k, v in r.as_dict().items()
                       if has_extra or k not in ("market_type", "source", "dataset_version")},
                    **({"dataset_version": dataset_version} if has_extra else {}),
                }
                for r in parsed.rows
            ],
        )
        has_dataset_table = await _table_exists(conn, "reference_datasets")
        if (dataset_version or licensed_external) and not has_dataset_table:
            # Loud, inside the transaction: the version / licence flag would be
            # silently dropped otherwise, and the gate reads that flag.
            raise RuntimeError(
                "reference_datasets table is missing — cannot record the dataset "
                "version / licence flag; run the startup migrations first. Nothing written."
            )
        if has_dataset_table:
            # The row describes the rows now in the table and nothing else: a
            # load without --version (legacy str-csv) clears it.
            await conn.execute(
                text("DELETE FROM reference_datasets WHERE dataset = :d"), {"d": DATASET_NAME}
            )
            summary["dataset_row_cleared"] = True
        if dataset_version:
            await conn.execute(
                text(
                    """
                    INSERT INTO reference_datasets (
                        dataset, version, source_sha256, row_count, market_count,
                        submarket_count, state_count, licensed_for_external,
                        licence_note, loaded_by
                    ) VALUES (
                        :dataset, :version, :sha, :rows, :markets,
                        :submarkets, :states, :licensed, :note, :by
                    )
                    """
                ),
                {
                    "dataset": DATASET_NAME,
                    "version": dataset_version,
                    "sha": parsed.source_sha256 or "",
                    "rows": len(parsed.rows),
                    "markets": parsed.market_count,
                    "submarkets": parsed.submarket_count,
                    "states": parsed.state_count,
                    "licensed": bool(licensed_external),
                    "note": licence_note,
                    "by": loaded_by,
                },
            )
            summary["dataset_row_written"] = True
    summary["applied"] = True
    return summary


async def _column_exists(conn, table: str, column: str) -> bool:
    if conn.dialect.name == "sqlite":
        rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
        return any(str(r[1]).lower() == column for r in rows)
    row = (
        await conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        )
    ).first()
    return row is not None


async def report_orphans(engine, parsed: ParseResult) -> list[dict[str, Any]]:
    """Deals whose stored ``str_market_id`` / ``submarket_id`` is not in the
    file about to be loaded. Read-only; the deal keeps its keys and names (the
    UI then says "no longer in the loaded list"). Lists ids, the tenant and
    the market / submarket names only — no other deal data.

    The read spans every tenant on purpose (an operator's list across the
    platform); its ``tenant_id IS NOT NULL`` predicate keeps the tenant
    listener satisfied the way the admin scans do.
    """
    market_keys = {r.str_market_id for r in parsed.rows}
    sub_keys = {r.submarket_id for r in parsed.rows if r.submarket_id}
    async with engine.connect() as conn:
        if not await _table_exists(conn, "deals"):
            return []
        has_sub = await _column_exists(conn, "deals", "submarket_id")
        sub_col = "submarket_id" if has_sub else "NULL AS submarket_id"
        rows = (
            await conn.execute(
                text(
                    f"SELECT id, tenant_id, str_market_id, {sub_col}, market, submarket "
                    "FROM deals WHERE tenant_id IS NOT NULL "
                    f"AND (str_market_id IS NOT NULL{' OR submarket_id IS NOT NULL' if has_sub else ''}) "
                    "ORDER BY created_at"
                )
            )
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        m = r._mapping
        mk = m.get("str_market_id")
        sk = m.get("submarket_id")
        mk_in = mk is None or mk in market_keys
        sk_in = sk is None or sk in sub_keys
        if mk_in and sk_in:
            continue
        out.append({
            "deal_id": str(m["id"]),
            "tenant_id": str(m["tenant_id"]),
            "market_key": mk,
            "market_key_in_list": mk_in,
            "submarket_key": sk,
            "submarket_key_in_list": sk_in,
            "market": m.get("market"),
            "submarket": m.get("submarket"),
        })
    return out


def _print(summary: dict[str, Any], parsed: ParseResult, commit: bool) -> None:
    print(
        f"file: {summary['csv_rows']} rows · {summary['csv_markets']} markets · "
        f"{summary['csv_submarkets']} submarkets · {summary['csv_states']} states"
    )
    if parsed.source_sha256:
        print(f"source sha256: {parsed.source_sha256}")
    if summary.get("dataset_version"):
        print(
            f"dataset version: {summary['dataset_version']} · licensed for external "
            f"workspaces: {'yes' if summary['licensed_for_external'] else 'NO'}"
        )
    if parsed.ignored_columns:
        print(f"ignored extra columns: {parsed.ignored_columns}")
    print(
        f"str_markets now: {summary['table_rows']} rows"
        + ("" if summary["table_exists"] else " (table absent — created on --commit)")
    )
    print(
        f"change: +{summary['would_add']} new · -{summary['would_remove']} removed · "
        f"{summary['unchanged_keys']} kept"
    )
    if parsed.problems:
        print(f"{len(parsed.problems)} problem(s) — nothing will be written:")
        for p in parsed.problems:
            print(f"  {p}")
    if summary["applied"]:
        print(
            "applied: str_markets replaced"
            + (
                " · reference_datasets row written" if summary.get("dataset_row_written")
                else " · reference_datasets row cleared (no --version)" if summary.get("dataset_row_cleared")
                else ""
            )
        )
    elif commit:
        print("NOT applied")
    else:
        print("dry run — re-run with --commit to apply")


def _print_orphans(orphans: list[dict[str, Any]]) -> None:
    if not orphans:
        print("orphans: none — every stored key is in the file")
        return
    print(f"orphans: {len(orphans)} deal(s) hold a key the file no longer carries (names stay):")
    for o in orphans:
        parts = []
        if not o["market_key_in_list"]:
            parts.append(f"market key {o['market_key']!r} ({o['market']!r})")
        if not o["submarket_key_in_list"]:
            parts.append(f"submarket key {o['submarket_key']!r} ({o['submarket']!r})")
        print(f"  deal {o['deal_id']} tenant {o['tenant_id']}: {'; '.join(parts)}")


async def _main(args: argparse.Namespace) -> int:
    from app.config import get_settings
    from app.database import get_engine

    settings = get_settings()
    print(f"database: {settings.async_database_url.split('@')[-1]}")
    path = Path(args.path)
    parsed = parse_costar_geography(path) if args.format == "costar-geography" else parse_csv(path)
    engine = get_engine()
    if args.report_orphans and not parsed.problems:
        _print_orphans(await report_orphans(engine, parsed))
    summary = await plan_and_apply(
        engine, parsed, commit=args.commit,
        dataset_version=args.version,
        licensed_external=args.licensed_external,
        licence_note=args.licence_note,
        loaded_by=getpass.getuser() if args.commit else None,
    )
    _print(summary, parsed, args.commit)
    if parsed.problems:
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("path", help="the definition file (CSV, or .xlsx for costar-geography)")
    parser.add_argument(
        "--format", choices=("str-csv", "costar-geography"), default="str-csv",
        help="str-csv: STR's CSV with vendor ids (default); costar-geography: the CoStar export (keys minted)",
    )
    parser.add_argument(
        "--version", default=None,
        help="dataset version label written to reference_datasets (required for costar-geography)",
    )
    parser.add_argument(
        "--licensed-external", action="store_true",
        help="record that CoStar confirmed the licence (external workspaces are served only with "
             "MARKET_TAXONOMY_SERVE_EXTERNAL on as well)",
    )
    parser.add_argument("--licence-note", default=None, help="free-text licence note stored with the dataset row")
    parser.add_argument(
        "--report-orphans", action="store_true",
        help="list deals whose stored keys are not in this file (read-only)",
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="Replace str_markets with the file (default: dry run)",
    )
    args = parser.parse_args()
    if args.format == "costar-geography" and not args.version:
        parser.error("--version <label> is required with --format costar-geography")
    if (args.licensed_external or args.licence_note) and not args.version:
        parser.error("--licensed-external / --licence-note need --version <label> (the dataset row carries them)")
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
