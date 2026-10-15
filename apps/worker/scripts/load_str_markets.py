"""Load STR's market / submarket definition list into ``str_markets`` — Sam #6.

STR's file is not in hand yet. This loader is the groundwork: when the file
arrives, export it to CSV with exactly these columns (header row required,
order free, case-insensitive):

    str_market_id, market_name, submarket_id, submarket_name, state, country

* one row per submarket (``submarket_id`` + ``submarket_name`` set), and/or
  one market-level row per market (both submarket columns blank);
* ``str_market_id`` and ``market_name`` are required on every row, and one
  ``str_market_id`` must carry one ``market_name`` throughout;
* a ``submarket_name`` needs its ``submarket_id`` (the id is the row's key).

Usage (from ``apps/worker``, against the DB in ``DATABASE_URL``):

    # Dry run (the default) — validates the CSV and prints what would change
    uv run python scripts/load_str_markets.py path/to/str_markets.csv

    # Apply — REPLACES the whole table with the CSV, in one transaction
    uv run python scripts/load_str_markets.py path/to/str_markets.csv --commit

``--commit`` is refused while the CSV has any problem; nothing is written
partially. The values are STR's, copied verbatim (whitespace tidied) — this
script never invents an identifier or a name.

``str_markets`` is NOT tenant-scoped: it is vendor reference data identical
for every workspace, with no ``tenant_id`` and no customer or deal data (see
``app/migrations.py``). A deal's chosen market lives on its tenant-scoped
``deals`` row. A reload never touches ``deals``: a deal keeps the
``str_market_id`` and names it was saved with.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.services.str_markets import CSV_COLUMNS, StrMarketRow, row_key

REQUIRED = ("str_market_id", "market_name")


@dataclass
class ParseResult:
    rows: list[StrMarketRow] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    ignored_columns: list[str] = field(default_factory=list)

    @property
    def market_ids(self) -> set[str]:
        return {r.str_market_id for r in self.rows}

    @property
    def submarket_count(self) -> int:
        return sum(1 for r in self.rows if r.submarket_id)


def _tidy(v: Any) -> str | None:
    if v is None:
        return None
    t = " ".join(str(v).split())
    return t or None


def parse_csv(path: Path) -> ParseResult:
    """Read and validate the CSV. Never raises on bad content — every
    problem is listed (with its line number) so one run shows them all."""
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


async def _table_exists(conn) -> bool:
    if conn.dialect.name == "sqlite":
        sql = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'str_markets'"
    else:
        sql = "SELECT 1 FROM information_schema.tables WHERE table_name = 'str_markets'"
    return (await conn.execute(text(sql))).first() is not None


async def plan_and_apply(engine, parsed: ParseResult, *, commit: bool) -> dict[str, Any]:
    """Compare the CSV with the table; with ``commit`` replace the table.

    Returns a summary. Refuses (``applied`` False) when the CSV has problems.
    Reads and writes only ``str_markets`` (reference data, no tenant_id).
    """
    summary: dict[str, Any] = {
        "csv_rows": len(parsed.rows),
        "csv_markets": len(parsed.market_ids),
        "csv_submarkets": parsed.submarket_count,
        "problems": list(parsed.problems),
        "applied": False,
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
        incoming = {row_key(r.str_market_id, r.submarket_id) for r in parsed.rows}
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
        await conn.execute(text("DELETE FROM str_markets"))
        await conn.execute(
            text(
                """
                INSERT INTO str_markets (
                    id, str_market_id, market_name, submarket_id,
                    submarket_name, state, country
                ) VALUES (
                    :id, :str_market_id, :market_name, :submarket_id,
                    :submarket_name, :state, :country
                )
                """
            ),
            [
                {"id": row_key(r.str_market_id, r.submarket_id), **r.as_dict()}
                for r in parsed.rows
            ],
        )
    summary["applied"] = True
    return summary


def _print(summary: dict[str, Any], parsed: ParseResult, commit: bool) -> None:
    print(
        f"CSV: {summary['csv_rows']} rows · {summary['csv_markets']} markets · "
        f"{summary['csv_submarkets']} submarkets"
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
        print("applied: str_markets replaced")
    elif commit:
        print("NOT applied")
    else:
        print("dry run — re-run with --commit to apply")


async def _main(args: argparse.Namespace) -> int:
    from app.config import get_settings
    from app.database import get_engine

    settings = get_settings()
    print(f"database: {settings.async_database_url.split('@')[-1]}")
    parsed = parse_csv(Path(args.csv))
    summary = await plan_and_apply(get_engine(), parsed, commit=args.commit)
    _print(summary, parsed, args.commit)
    if parsed.problems:
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("csv", help="STR market definition CSV")
    parser.add_argument(
        "--commit", action="store_true",
        help="Replace str_markets with the CSV (default: dry run)",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
