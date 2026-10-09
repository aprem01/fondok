"""R-067 — map a P&L benchmark report onto the model's expense categories.

A CBRE Benchmarker / "Trends in the Hotel Industry" / HotStats report for the
property's submarket and positioning publishes, for every USALI line, the
comp-set ("peer") Total $, Ratio-to-Revenue, $PAR and $POR. The PNL_BENCHMARK
extraction emits those as ``pnl_benchmark.peer.<line>.(total_usd|ratio_pct|
par_usd|por_usd)`` (see ``agents/extraction_schemas/pnl_benchmark.md``).

This module translates those rows into the canonical expense categories the
Future P&L renders (Rooms / F&B / Other Operated departmental expense, the
five undistributed lines, management fee, and the fixed charges), each with
the SAME ratio basis the model's "% Rev" cell uses:

* departmental expense → % of that department's revenue (USALI departmental
  ratio), and
* everything else → % of total revenue.

Read-only and deterministic: nothing here feeds the engines. (The engine's
existing benchmark use — ``engine_runner._load_pnl_benchmark_overrides`` —
is untouched.) Every number traces to an extracted field; a category with no
benchmark value is omitted rather than filled, so the UI never shows a
benchmark the report did not publish.

Ratio precedence (most to least reliable):

1. ``computed_from_totals`` — line ``total_usd`` ÷ its basis revenue's
   ``total_usd`` (both from the report; unambiguous units).
2. ``computed_from_par`` — line ``par_usd`` ÷ basis revenue ``par_usd``
   (same basis, per available room).
3. ``reported`` — the printed ``ratio_pct`` (percent units, e.g. ``24.6``).
4. ``legacy_summary`` — the older peer-set summary aliases
   (``pnl_benchmark.a_and_g_pct`` …) when the report was extracted before
   the per-line breakdown existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

RatioBasis = Literal["department_revenue", "total_revenue"]
RatioSource = Literal[
    "computed_from_totals", "computed_from_par", "reported", "legacy_summary"
]

#: The comp-set column. ``subject`` is the property's own actuals as the
#: benchmark vendor saw them — not a benchmark, so it is never used here.
BENCHMARK_COLUMN = "peer"


@dataclass(frozen=True)
class _CategorySpec:
    key: str
    label: str
    line: str
    basis: RatioBasis
    # Revenue slug the ratio divides by (department or total revenue).
    revenue_line: str
    # Legacy summary alias (fraction 0..1) + whether it is a margin
    # (profit %) that must be flipped to a cost ratio.
    legacy_field: str | None = None
    legacy_is_margin: bool = False


# Order = the Future P&L's row order. ``key`` matches the worker expense
# engine's year keys (``dept_expenses.rooms`` → ``rooms`` …) so the web can
# line a benchmark up with the model's own ratio for the same row.
CATEGORY_SPECS: tuple[_CategorySpec, ...] = (
    _CategorySpec("rooms", "Rooms Expense", "rooms_dept_expense",
                  "department_revenue", "rooms_revenue",
                  legacy_field="pnl_benchmark.rooms_dept_pct"),
    _CategorySpec("food_beverage", "Food & Beverage Expense", "fb_dept_expense",
                  "department_revenue", "fb_revenue",
                  legacy_field="pnl_benchmark.fb_dept_margin", legacy_is_margin=True),
    _CategorySpec("other_operated", "Other Operated Departments Expense",
                  "other_operated_expense", "department_revenue",
                  "other_operated_revenue"),
    _CategorySpec("administrative_general", "Administrative & General", "a_and_g",
                  "total_revenue", "total_revenue",
                  legacy_field="pnl_benchmark.a_and_g_pct"),
    _CategorySpec("information_telecom", "Information & Telecom Systems", "it",
                  "total_revenue", "total_revenue"),
    _CategorySpec("sales_marketing", "Sales & Marketing", "sales_marketing",
                  "total_revenue", "total_revenue",
                  legacy_field="pnl_benchmark.sales_marketing_pct"),
    _CategorySpec("property_operations", "Property Operation & Maintenance",
                  "maintenance", "total_revenue", "total_revenue"),
    _CategorySpec("utilities", "Utilities", "utilities",
                  "total_revenue", "total_revenue",
                  legacy_field="pnl_benchmark.utilities_pct"),
    _CategorySpec("gop", "Gross Operating Profit", "gop",
                  "total_revenue", "total_revenue",
                  legacy_field="pnl_benchmark.gop_margin"),
    _CategorySpec("mgmt_fee", "Management Fees", "mgmt_fee",
                  "total_revenue", "total_revenue"),
    _CategorySpec("property_taxes", "Property Taxes", "property_taxes",
                  "total_revenue", "total_revenue",
                  legacy_field="pnl_benchmark.property_taxes_pct"),
    _CategorySpec("insurance", "Insurance", "insurance",
                  "total_revenue", "total_revenue",
                  legacy_field="pnl_benchmark.insurance_pct"),
    _CategorySpec("rent", "Rent", "rent", "total_revenue", "total_revenue"),
)


def _num(value: Any) -> float | None:
    """Coerce an extracted value to float; ``None`` for blanks / text."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("$", "").replace("%", "")
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = "-" + cleaned[1:-1]
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _ratio(numer: float | None, denom: float | None) -> float | None:
    if numer is None or denom is None or denom <= 0:
        return None
    return numer / denom


def map_benchmark_to_categories(flat: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate flattened PNL_BENCHMARK fields into expense categories.

    ``flat`` maps lower-cased ``field_name`` → extracted value (the shape
    ``api/documents._bucket_pnl`` builds). Returns one dict per category
    the report actually covers, in Future P&L row order::

        {"key", "label", "benchmark_line", "ratio", "ratio_basis",
         "ratio_source", "par_usd", "por_usd"}

    ``ratio`` is a fraction (0.246 = 24.6%) or ``None`` when only PAR / POR
    were published. Categories with no value at all are omitted.
    """
    def field(line: str, metric: str) -> float | None:
        return _num(flat.get(f"pnl_benchmark.{BENCHMARK_COLUMN}.{line}.{metric}"))

    out: list[dict[str, Any]] = []
    for spec in CATEGORY_SPECS:
        total = field(spec.line, "total_usd")
        par = field(spec.line, "par_usd")
        por = field(spec.line, "por_usd")
        printed = field(spec.line, "ratio_pct")

        ratio: float | None = None
        source: RatioSource | None = None
        computed = _ratio(total, field(spec.revenue_line, "total_usd"))
        if computed is not None:
            ratio, source = computed, "computed_from_totals"
        else:
            computed = _ratio(par, field(spec.revenue_line, "par_usd"))
            if computed is not None:
                ratio, source = computed, "computed_from_par"
            elif printed is not None:
                # The schema pins ``ratio_pct`` to percent units ("24.6").
                ratio, source = printed / 100.0, "reported"
            elif spec.legacy_field is not None:
                legacy = _num(flat.get(spec.legacy_field))
                if legacy is not None:
                    # Legacy aliases are decimals 0..1; a stray percent is
                    # normalized the same way the engine's loader does.
                    if legacy > 1.0:
                        legacy = legacy / 100.0
                    if spec.legacy_is_margin:
                        legacy = 1.0 - legacy
                    ratio, source = legacy, "legacy_summary"

        if ratio is None and par is None and por is None:
            continue
        out.append(
            {
                "key": spec.key,
                "label": spec.label,
                "benchmark_line": spec.line,
                "ratio": ratio,
                "ratio_basis": spec.basis,
                "ratio_source": source,
                "par_usd": par,
                "por_usd": por,
            }
        )
    return out


__all__ = ["BENCHMARK_COLUMN", "CATEGORY_SPECS", "map_benchmark_to_categories"]
