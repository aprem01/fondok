"""Concept registry — loader, validator and deterministic resolver.

``concepts.yaml`` is validated at import time (a broken registry raises
``RegistryError`` here; ``main.py`` catches it and ``/health`` reports
``ontology_invalid``). The public surface is:

    resolve(fields, concept, *, doc_type=None, want="annual", basis=None,
            tenant_aliases=None, allow_token_match=False) -> Resolution
    resolve_many(fields, concepts, **kw) -> dict[str, Resolution]
    concept_for_path(field_name, *, doc_type=None) -> (concept, basis, scope) | None
    identities() -> list[Identity]
    registry_version() -> int

Resolution order (deterministic — the tier is the first sort key):

    1. exact full path among the document type's aliases (its own doc type,
       the families it belongs to, then ``tenant_aliases``);
    2. exact full path among the ``"*"`` aliases;
    3. exact full path among ANOTHER document type's aliases (cross-doc: the
       concept is still certain, but a basis written under that other doc
       type's key is not applied — the basis is derived from the path and
       this document instead);
    4. unit-suffix-stripped full path (``_usd`` / ``_pct`` / ``_percent`` /
       ``_ratio`` / ``_amount`` — the strip the web ``findField`` applies)
       against any alias;
    5. tail match — the field's last segment, unit-stripped, against a bare
       (undotted) alias, unit-stripped. A field whose full path is an exact
       alias of ANOTHER concept (under any doc type) never tail-matches —
       this is the F&B revenue-vs-expense misplacement the web comments
       describe;
    6. token match (``usali_scorer._token_match_candidates``), only when
       ``allow_token_match=True``.

Within a tier: registry (alias) order, then first seen. Scope and basis are
FILTERS, never preferences — alias order in the YAML is what puts an annual
line ahead of a generic one. Paths in a subordinate namespace (``.monthly.``,
``.q1.``, ``.page5.`` ...) are excluded unless ``want`` IS that scope; a
document's own ``period_type`` line (or the doc type's default) gives the
scope of every path that carries no period hint; a ``.budget.`` /
``.forecast.`` / ``.plan.`` / ``.adjusted.`` namespace gives a basis, not a
slice. A month-name segment marks a monthly slice only — never WHICH month.
``reason`` is set iff ``value is None``: ``no_source`` | ``period_mismatch``
| ``unit_unknown`` | ``basis_excluded``.
"""

from __future__ import annotations

import ast
import logging
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

logger = logging.getLogger(__name__)

# ─────────────────────────────── vocabulary ───────────────────────────────

#: ``weekly`` — STAR weekly reports (eval corpus, 2026-09-10).
Scope = Literal["annual", "ttm", "ytd", "quarterly", "monthly", "weekly", "unknown"]
#: ``budget`` (owner capex plan / budget columns), ``plan`` (business-plan goals,
#: forecast blocks, projections), ``adjusted`` (seller-adjusted T-12 sheets) —
#: none of them is an actual, a broker claim, history or market data.
Basis = Literal[
    "actual", "broker", "om_history", "market", "budget", "plan", "adjusted", "unknown"
]
Unit = Literal[
    "usd", "pct", "ratio", "count", "keys", "date", "text", "index",
    "usd_per_key", "usd_per_occupied_room", "years",
]
Sign = Literal["positive", "expense_positive", "signed"]
Period = Literal["flow", "average", "point"]
SourceKind = Literal["grounded", "assumption", "calculated", "override", "refusal"]
ImpactBasis = Literal["noi", "revenue", "expense", "other"]
Reason = Literal["no_source", "period_mismatch", "unit_unknown", "basis_excluded"]

#: The 16 refusal codes shared with ``fondok_schemas.reasons`` and the web.
REASON_CODES: tuple[str, ...] = (
    "no_document", "no_source", "unit_unknown", "period_mismatch", "basis_mismatch",
    "basis_excluded", "awaiting_analyst", "needs_review", "pin_active", "str_unavailable",
    "not_knowable_as_of", "as_of_unknown", "stale_run", "engine_skipped", "inconclusive",
    "not_applicable",
)

#: Engines a concept may name: the engine_runner / provenance names plus the
#: historical, STR and comps engines under ``app/engines/``.
_EXTRA_ENGINES: frozenset[str] = frozenset(
    {"historical_baseline", "historical_variance", "str_forecast", "comp_sales"}
)

_NUMERIC_UNITS: frozenset[str] = frozenset(
    {"usd", "pct", "ratio", "count", "keys", "index", "usd_per_key",
     "usd_per_occupied_room", "years"}
)

_UNIT_SUFFIX_RE = re.compile(r"_?(usd|pct|percent|ratio|amount)$")
_YEAR_SEG_RE = re.compile(r"^(19|20)\d{2}$")
_PAGE_SEG_RE = re.compile(r"^page\d+$")
_MONTH_SEG_RE = re.compile(
    r"^(jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|jun(e)?|jul(y)?|aug(ust)?|"
    r"sep(tember)?|oct(ober)?|nov(ember)?|dec(ember)?)(_?\d{2,4})?$"
)
_PLACEHOLDERS: dict[str, str] = {
    "{year}": r"(?:19|20)\d{2}",
    "{n}": r"\d+",
    "{yyyy_mm}": r"[^.]+",
}
#: Subordinate namespaces that name a period give that scope; the rest are
#: slices that are never a period total under any ``want``.
_NAMESPACE_SCOPE: dict[str, Scope] = {
    "monthly": "monthly", "per_month": "monthly",
    "quarterly": "quarterly", "q1": "quarterly", "q2": "quarterly",
    "q3": "quarterly", "q4": "quarterly",
    "ytd": "ytd",
    "weekly": "weekly",
}
#: Namespaces that mark a BASIS rather than a period slice: a ``.budget.`` /
#: ``.forecast.`` / ``.plan.`` / ``.adjusted.`` block is served only when that
#: basis is asked for (or no basis filter is given) — it is never excluded as
#: a period mismatch.
_NAMESPACE_BASIS: dict[str, Basis] = {
    "budget": "budget", "forecast": "plan", "plan": "plan", "adjusted": "adjusted",
}
#: Path segments that mean "trailing twelve" by construction. NOTE:
#: ``ttm_summary_per_om`` is deliberately absent — on the live OM that block is
#: the "Year Ended Dec 31, 2024" column (actuals through Nov + a December
#: forecast); "ttm" there is the extractor's label, not a period guarantee.
#: Its scope comes from the alias (OM: annual) or the document.
_TTM_SEGMENTS: frozenset[str] = frozenset({"ttm", "ttm_performance", "trailing_twelve", "t12"})
_ALLOWED_SCOPES: dict[str, tuple[str, ...]] = {
    "annual": ("annual", "ttm", "unknown"),
    "ttm": ("ttm", "annual", "unknown"),
    "ytd": ("ytd",),
    "quarterly": ("quarterly",),
    "monthly": ("monthly",),
    "weekly": ("weekly",),
    "unknown": ("annual", "ttm", "ytd", "quarterly", "monthly", "weekly", "unknown"),
}
_DOC_DEFAULT_BASIS: dict[str, Basis] = {
    "T12": "actual", "PNL": "actual", "PNL_MONTHLY": "actual", "PNL_YTD": "actual",
    "OM": "broker",
    "STR": "market", "STR_TREND": "market", "STR_SEGMENTATION": "market",
    "CBRE_HORIZONS": "market", "PNL_BENCHMARK": "market", "PORTFOLIO_PNL": "market",
    "MARKET_STUDY": "market",
}
_DOC_DEFAULT_SCOPE: dict[str, Scope] = {
    "T12": "ttm", "PNL": "annual", "PNL_MONTHLY": "monthly", "PNL_YTD": "ytd",
}


class RegistryError(ValueError):
    """The registry failed validation — raised at load / import."""


# ─────────────────────────────── models ───────────────────────────────────


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Alias(_Strict):
    path: str
    basis: Basis | None = None
    scope: Scope | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_string(cls, v: Any) -> Any:
        # An alias may be written as a bare path string or {path, basis, scope}.
        return {"path": v} if isinstance(v, str) else v


class UsaliRef(_Strict):
    line: str
    section: str
    edition: int = 11


class FieldCatalogBinding(_Strict):
    namespace: str
    key: str
    percentage_key: bool = False


class VarianceConceptBinding(_Strict):
    key: str
    label: str
    impact_basis: ImpactBasis


class WorksheetBinding(_Strict):
    row: str
    hist_key: str | None = None
    meta_key: str | None = None
    review_key: str | None = None
    override_key: str | None = None
    y1_src: Literal["expense", "fb", "revenue"] | None = None
    y1_read: list[str] = Field(default_factory=list)
    fmt: Literal["currency", "pct", "dollar"] = "currency"


class ScorerVariant(_Strict):
    basis: Basis | None = None
    scope: Scope | None = None


class Bindings(_Strict):
    field_catalog: FieldCatalogBinding | None = None
    critic_key: str | None = None
    variance_rule: str | None = None
    variance_concept: VarianceConceptBinding | None = None
    actuals_attr: str | None = None
    scorer_key: str | None = None
    scorer_synonyms: list[str] = Field(default_factory=list)
    scorer_variants: dict[str, ScorerVariant] = Field(default_factory=dict)
    recognizer: str | None = None
    worksheet: WorksheetBinding | None = None
    #: Dotted engine-output path this concept is displayed from, e.g.
    #: ``expense.years[].noi_institutional``. Documentation only — nothing
    #: reads it at runtime; it exists so the two NOI bases cannot drift from
    #: the fields that carry them (FON-59 #1 / FON-67 #2).
    engine_field: str | None = None

    def scorer_identifiers(self) -> set[str]:
        out: set[str] = set(self.scorer_synonyms) | set(self.scorer_variants)
        if self.scorer_key:
            out.add(self.scorer_key)
        return out


class Concept(_Strict):
    id: str = ""
    label: str
    short: str
    group: str
    usali: UsaliRef | None = None
    unit: Unit
    sign: Sign
    period: Period
    #: ``null`` only for point-in-time facts (``period: point``) — a ``note``
    #: must then say why no accounting period applies.
    default_scope: Scope | None
    note: str | None = None
    identity: str | None = None
    identity_optional_terms: list[str] = Field(default_factory=list)
    identity_tolerance: float = 0.005
    usali_rules: list[str] = Field(default_factory=list)
    engines: list[str] = Field(default_factory=list)
    bindings: Bindings = Field(default_factory=Bindings)
    aliases: dict[str, list[Alias]]
    as_of: str | None = None

    def is_numeric(self) -> bool:
        return self.unit in _NUMERIC_UNITS


class Source(_Strict):
    label: str
    badge: str
    kind: SourceKind
    doc_types: list[str] = Field(default_factory=list)
    explanation: str
    reason: str | None = None


class ReasonMeta(_Strict):
    label: str
    ui: str
    explanation: str


class Registry(_Strict):
    version: int
    doc_types: list[str]
    families: dict[str, list[str]] = Field(default_factory=dict)
    period_types: dict[str, int]
    subordinate_namespaces: list[str]
    concepts: dict[str, Concept]
    sources: dict[str, Source]
    reasons: dict[str, ReasonMeta]

    # ── derived (built by the loader; excluded from dumps) ──
    _alias_index: dict[str, dict[str, tuple[str, Alias]]] = {}
    _pattern_index: dict[str, list[tuple[re.Pattern[str], str, Alias]]] = {}
    _subordinate: frozenset[str] = frozenset()

    def alias_keys_for(self, doc_type: str | None) -> list[str]:
        """Alias-map keys that apply to ``doc_type``: itself, then its families."""
        if not doc_type:
            return []
        keys = [doc_type]
        keys.extend(fam for fam, members in self.families.items() if doc_type in members)
        return keys

    def doc_types_for_key(self, key: str) -> list[str]:
        if key == "*":
            return list(self.doc_types)
        if key in self.families:
            return list(self.families[key])
        return [key]

    def dump(self) -> dict[str, Any]:
        """JSON-ready dict (what ``GET /ontology/concepts`` and the codegen emit)."""
        data = self.model_dump(mode="json")
        for cid, concept in data["concepts"].items():
            concept["id"] = cid
        return data


@dataclass(frozen=True)
class Identity:
    concept: str
    expression: str
    terms: tuple[str, ...]
    optional_terms: tuple[str, ...]
    tolerance: float


@dataclass(frozen=True)
class Candidate:
    field_name: str
    value: Any
    tier: int
    scope: str
    basis: str
    alias: str | None
    excluded: str | None


@dataclass(frozen=True)
class Resolution:
    concept: str
    value: Any
    field_name: str | None
    source_page: int | None
    unit: str | None
    confidence: float | None
    reviewed: str | None
    doc_type: str | None
    scope: str
    basis: str
    reason: Reason | None
    candidates: tuple[Candidate, ...]


# ─────────────────────────────── path helpers ─────────────────────────────


def _strip_unit(s: str) -> str:
    return _UNIT_SUFFIX_RE.sub("", s)


def _tail(lname: str) -> str:
    return lname.rsplit(".", 1)[-1]


def _is_pattern(path: str) -> bool:
    return "{" in path


def _compile_pattern(path: str) -> re.Pattern[str]:
    out = ""
    i = 0
    while i < len(path):
        matched = False
        for ph, rx in _PLACEHOLDERS.items():
            if path.startswith(ph, i):
                out += rx
                i += len(ph)
                matched = True
                break
        if not matched:
            out += re.escape(path[i])
            i += 1
    return re.compile(f"^{out}$")


def _has_year_segment(lname: str) -> bool:
    return any(_YEAR_SEG_RE.match(seg) for seg in lname.split(".")[:-1])


def is_om_historical_year(path: str) -> bool:
    """Mirror of ``agents.variance.is_om_historical_year`` (kept import-free)."""
    lower = path.lower()
    if lower.startswith(("historical_performance.", "historical.")):
        return True
    return _has_year_segment(lower)


def is_market_segment(path: str) -> bool:
    """Mirror of ``agents.variance.is_market_segment``."""
    lower = path.lower()
    return ".segment." in lower or lower.startswith(("segment.", "market.", "comp_set.", "compset."))


def _subordinate_scope(lname: str, subordinate: frozenset[str]) -> tuple[bool, Scope | None]:
    """``(is_slice, scope)`` — a slice with ``scope=None`` is never a period total.

    A month-name segment says only "this is a monthly slice" — never WHICH
    month: the live extractor has labelled the T-12's Jan-Mar 2025 columns
    ``monthly.jan_2024`` … (eval corpus, 2026-09-10), so the month label is
    not trusted for period attribution. Basis namespaces (``.budget.`` …)
    are not slices — see ``_NAMESPACE_BASIS``.
    """
    segs = lname.split(".")
    for seg in segs[:-1]:
        if seg in _NAMESPACE_BASIS:
            continue
        if seg in subordinate:
            return True, _NAMESPACE_SCOPE.get(seg)
        if _PAGE_SEG_RE.match(seg):
            return True, None
        if _MONTH_SEG_RE.match(seg):
            return True, "monthly"
    return False, None


def _namespace_basis(lname: str) -> Basis | None:
    for seg in lname.split(".")[:-1]:
        b = _NAMESPACE_BASIS.get(seg)
        if b is not None:
            return b
    return None


def _path_scope_hint(lname: str) -> Scope | None:
    """``ttm`` / ``annual`` when the path itself says so, else ``None``.

    A four-digit-year segment (``p_and_l_usali.2021.gop_usd``) is an annual
    column whichever document it sits in.
    """
    segs = lname.split(".")
    if any(seg in _TTM_SEGMENTS for seg in segs) or "_ttm" in lname or "trailing" in lname:
        return "ttm"
    if "annual" in segs or _has_year_segment(lname):
        return "annual"
    return None


def _coerce_number(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("$", "")
        pct = s.endswith("%")
        if pct:
            s = s[:-1]
        try:
            f = float(s)
        except ValueError:
            return None
        if not math.isfinite(f):
            return None
        return f / 100.0 if pct else f
    return None


# ─────────────────────────────── loading ──────────────────────────────────


_REGISTRY_PATH = Path(__file__).parent / "concepts.yaml"


def _formula_identifiers(formula: str) -> set[str]:
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError:
        return set()
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} - {"abs", "sum", "min", "max"}


def _known_engines() -> frozenset[str]:
    from fondok_schemas.provenance import _ENGINE_NAMES

    return frozenset(_ENGINE_NAMES) | _EXTRA_ENGINES


def _router_doc_types() -> frozenset[str]:
    from fondok_schemas.document import DocType

    return frozenset(dt.value for dt in DocType)


def _validate(reg: Registry) -> None:
    errors: list[str] = []

    # doc types ↔ router enum; families reference known doc types.
    router = _router_doc_types()
    for dt in reg.doc_types:
        if dt not in router:
            errors.append(f"doc_types: {dt!r} is not a router DocType")
    for fam, members in reg.families.items():
        if fam in reg.doc_types:
            errors.append(f"families: {fam!r} collides with a doc type")
        for m in members:
            if m not in reg.doc_types:
                errors.append(f"families[{fam}]: {m!r} is not in doc_types")
    valid_keys = set(reg.doc_types) | set(reg.families) | {"*"}

    # reasons: exactly the shared 16 codes.
    if set(reg.reasons) != set(REASON_CODES):
        missing = sorted(set(REASON_CODES) - set(reg.reasons))
        extra = sorted(set(reg.reasons) - set(REASON_CODES))
        errors.append(f"reasons: expected the 16 shared codes; missing={missing} extra={extra}")
    for sid, src in reg.sources.items():
        if src.reason is not None and src.reason not in reg.reasons:
            errors.append(f"sources[{sid}].reason {src.reason!r} is not a reason code")
        for dt in src.doc_types:
            if dt not in reg.doc_types:
                errors.append(f"sources[{sid}].doc_types: unknown doc type {dt!r}")

    # USALI rules — every listed id exists, and the listed set is exactly the
    # rules whose formula names one of the concept's scorer identifiers.
    from ..usali_rules import load_usali_rules

    rules = load_usali_rules()
    rule_terms = {r.rule_id: _formula_identifiers(r.formula_or_check) for r in rules}
    engines = _known_engines()

    seen_alias: dict[tuple[str, str], str] = {}
    seen_scorer: dict[str, str] = {}
    for cid, c in reg.concepts.items():
        c.id = cid
        if not c.label.strip():
            errors.append(f"{cid}: label is empty")
        if c.default_scope is None and (c.period != "point" or not (c.note or "").strip()):
            errors.append(f"{cid}: default_scope null is allowed only for a point fact with a note")
        for rid in c.usali_rules:
            if rid not in rule_terms:
                errors.append(f"{cid}: unknown usali_rules id {rid!r}")
        idents = c.bindings.scorer_identifiers()
        expected = {rid for rid, terms in rule_terms.items() if terms & idents}
        listed = set(c.usali_rules)
        if rule_terms and expected != listed:
            errors.append(
                f"{cid}: usali_rules must equal the rules naming {sorted(idents)}; "
                f"missing={sorted(expected - listed)} extra={sorted(listed - expected)}"
            )
        for ident in idents:
            prior = seen_scorer.get(ident)
            if prior is not None and prior != cid:
                errors.append(f"{cid}: scorer identifier {ident!r} already belongs to {prior!r}")
            seen_scorer[ident] = cid
        for eng in c.engines:
            if eng not in engines:
                errors.append(f"{cid}: unknown engine {eng!r}")
        if c.as_of is not None and c.as_of not in reg.concepts:
            errors.append(f"{cid}: as_of {c.as_of!r} is not a concept")
        if c.identity:
            errors.extend(_validate_identity(cid, c, reg))
        elif c.identity_optional_terms:
            errors.append(f"{cid}: identity_optional_terms without an identity")
        if c.bindings.variance_rule and c.bindings.variance_rule not in rule_terms:
            errors.append(f"{cid}: variance_rule {c.bindings.variance_rule!r} is not a rule id")
        if not any(c.aliases.values()):
            errors.append(f"{cid}: at least one alias is required")
        for key, aliases in c.aliases.items():
            if key not in valid_keys:
                errors.append(f"{cid}: unknown alias doc type {key!r}")
                continue
            for a in aliases:
                lpath = a.path.strip().lower()
                if not lpath:
                    errors.append(f"{cid}: empty alias under {key!r}")
                    continue
                for dt in reg.doc_types_for_key(key) + (["*"] if key == "*" else []):
                    prior = seen_alias.get((dt, lpath))
                    if prior is not None and prior != cid:
                        errors.append(
                            f"{cid}: alias {a.path!r} ({key}) already maps to {prior!r} for {dt}"
                        )
                    seen_alias.setdefault((dt, lpath), cid)

    if errors:
        raise RegistryError("concept registry invalid:\n  - " + "\n  - ".join(errors))


_ALLOWED_IDENTITY_OPS: tuple[type[ast.AST], ...] = (ast.Add, ast.Sub, ast.Mult, ast.Div)


def _validate_identity(cid: str, c: Concept, reg: Registry) -> list[str]:
    errs: list[str] = []
    try:
        tree = ast.parse(c.identity or "", mode="eval")
    except SyntaxError as exc:
        return [f"{cid}: identity does not parse ({exc})"]
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load)):
            continue
        if isinstance(node, ast.BinOp):
            if not isinstance(node.op, _ALLOWED_IDENTITY_OPS):
                errs.append(f"{cid}: identity operator {type(node.op).__name__} not allowed")
            continue
        if isinstance(node, _ALLOWED_IDENTITY_OPS):
            continue
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            continue
        if isinstance(node, (ast.USub, ast.UAdd)):
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            continue
        if isinstance(node, ast.Name):
            if node.id not in reg.concepts:
                errs.append(f"{cid}: identity references unknown concept {node.id!r}")
            elif node.id == cid:
                errs.append(f"{cid}: identity references itself")
            continue
        errs.append(f"{cid}: identity node {type(node).__name__} not allowed")
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for t in c.identity_optional_terms:
        if t not in names:
            errs.append(f"{cid}: identity_optional_terms {t!r} not in identity")
    return errs


def _build_indexes(reg: Registry) -> None:
    exact: dict[str, dict[str, tuple[str, Alias]]] = {}
    patterns: dict[str, list[tuple[re.Pattern[str], str, Alias]]] = {}
    for cid, c in reg.concepts.items():
        for key, aliases in c.aliases.items():
            for a in aliases:
                lpath = a.path.strip().lower()
                if _is_pattern(lpath):
                    patterns.setdefault(key, []).append((_compile_pattern(lpath), cid, a))
                else:
                    exact.setdefault(key, {}).setdefault(lpath, (cid, a))
    reg._alias_index = exact
    reg._pattern_index = patterns
    reg._subordinate = frozenset(s.lower() for s in reg.subordinate_namespaces)


def load_registry(path: str | Path | None = None) -> Registry:
    """Load + validate a registry YAML. Raises ``RegistryError`` on any defect."""
    p = Path(path) if path is not None else _REGISTRY_PATH
    if not p.exists():
        raise RegistryError(f"concept registry not found at {p}")
    with p.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise RegistryError("concept registry: top level must be a mapping")
    try:
        reg = Registry.model_validate(raw)
    except ValidationError as exc:
        raise RegistryError(f"concept registry invalid:\n{exc}") from exc
    _validate(reg)
    _build_indexes(reg)
    return reg


_REGISTRY: Registry = load_registry()


def get_registry() -> Registry:
    return _REGISTRY


def registry_version() -> int:
    return _REGISTRY.version


# ─────────────────────────────── resolution ───────────────────────────────


@dataclass(frozen=True)
class _Field:
    name: str
    lname: str
    value: Any
    unit: str | None
    source_page: int | None
    confidence: float | None
    reviewed: str | None
    order: int


def _as_fields(fields: Any) -> list[_Field]:
    out: list[_Field] = []
    if fields is None:
        return out
    if isinstance(fields, Mapping):
        for i, (k, v) in enumerate(fields.items()):
            name = str(k).strip()
            if name:
                out.append(_Field(name, name.lower(), v, None, None, None, None, i))
        return out
    for i, f in enumerate(fields):
        if isinstance(f, _Field):  # already normalised (resolve_many → resolve)
            out.append(f)
            continue
        if isinstance(f, Mapping):
            get = f.get
        else:
            def get(k: str, _f: Any = f) -> Any:
                return getattr(_f, k, None)
        name = str(get("field_name") or "").strip()
        if not name:
            continue
        sp = get("source_page")
        conf = get("confidence")
        out.append(
            _Field(
                name, name.lower(), get("value"), get("unit"),
                int(sp) if isinstance(sp, int) and not isinstance(sp, bool) else None,
                float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None,
                get("reviewed"), i,
            )
        )
    return out


def _doc_scope(fields: Sequence[_Field], doc_type: str | None, reg: Registry) -> Scope:
    for f in fields:
        if f.lname.endswith("period_type") and isinstance(f.value, str) and f.value.strip():
            rank = reg.period_types.get(f.value.strip().lower())
            if rank is None:
                break
            if rank == 0:
                return "annual"
            if rank == 1:
                return "ttm"
            if rank == 5:
                return "ytd"
            if rank == 7:
                return "quarterly"
            return "monthly"
    return _DOC_DEFAULT_SCOPE.get(doc_type or "", "unknown")


def _basis_for(lname: str, doc_type: str | None, alias_basis: Basis | None) -> Basis:
    if alias_basis:
        return alias_basis
    if lname.startswith(("broker_proforma.", "broker.")):
        return "broker"
    ns_basis = _namespace_basis(lname)
    if ns_basis is not None:
        return ns_basis
    default = _DOC_DEFAULT_BASIS.get(doc_type or "", "unknown")
    if is_market_segment(lname):
        return "market"
    if is_om_historical_year(lname):
        return "om_history" if default == "broker" else default
    return default


#: Resolver tiers (the first sort key). See the module docstring.
TIER_EXACT_DOC = 1      # exact path, the document type's own / family / tenant aliases
TIER_EXACT_ANY = 2      # exact path, "*" aliases
TIER_EXACT_CROSS = 3    # exact path listed under ANOTHER document type
TIER_STRIPPED = 4       # unit-suffix-stripped full path
TIER_TAIL = 5           # field tail vs bare alias (guarded)
TIER_TOKEN = 6          # opt-in token match


def _ordered_keys(reg: Registry, doc_type: str | None) -> list[tuple[str, int]]:
    """Alias-map keys in resolution order with their tier."""
    own = reg.alias_keys_for(doc_type)
    keys: list[tuple[str, int]] = [(k, TIER_EXACT_DOC) for k in own]
    keys.append(("*", TIER_EXACT_ANY))
    seen = {k for k, _t in keys}
    keys.extend((k, TIER_EXACT_CROSS) for k in reg.doc_types if k not in seen)
    keys.extend((k, TIER_EXACT_CROSS) for k in reg.families if k not in seen)
    return keys


def _match_aliases(
    reg: Registry, concept: Concept, doc_type: str | None,
    tenant_aliases: Mapping[str, Sequence[Any]] | None,
) -> tuple[list[tuple[str, Alias, int]], list[tuple[re.Pattern[str], Alias, int]]]:
    """Ordered ``(lpath, alias, tier)`` exact entries and pattern entries."""
    exact: list[tuple[str, Alias, int]] = []
    patterns: list[tuple[re.Pattern[str], Alias, int]] = []

    def _add(aliases: Iterable[Alias], tier: int) -> None:
        for a in aliases:
            lp = a.path.strip().lower()
            if _is_pattern(lp):
                patterns.append((_compile_pattern(lp), a, tier))
            else:
                exact.append((lp, a, tier))

    ordered = _ordered_keys(reg, doc_type)
    for key, tier in ordered:
        if tier == TIER_EXACT_DOC:
            _add(concept.aliases.get(key, ()), tier)
    # Tenant aliases sit at the end of tier 1 — after the registry's own
    # doc-type aliases, ahead of the "*" bucket.
    if tenant_aliases:
        extra = tenant_aliases.get(concept.id) or ()
        _add(
            (Alias(path=str(e)) if not isinstance(e, Mapping) else Alias(**e) for e in extra),
            TIER_EXACT_DOC,
        )
    for key, tier in ordered:
        if tier != TIER_EXACT_DOC:
            _add(concept.aliases.get(key, ()), tier)
    return exact, patterns


def _owner_of_exact(
    reg: Registry, lname: str, doc_type: str | None
) -> tuple[str, Alias, int] | None:
    """``(concept, alias, tier)`` for ``lname`` as an EXACT alias under ANY
    key — the document's own keys first, then ``*``, then every other doc type."""
    for key, tier in _ordered_keys(reg, doc_type):
        hit = reg._alias_index.get(key, {}).get(lname)
        if hit:
            return hit[0], hit[1], tier
        for rx, cid, a in reg._pattern_index.get(key, ()):
            if rx.match(lname):
                return cid, a, tier
    return None


def _alias_basis(alias: Alias | None, tier: int) -> Basis | None:
    """An alias's explicit basis applies only under its own doc-type key (or
    ``*``); matched cross-doc (tier 3) it is another document's knowledge, so
    the basis is derived from the path + this document instead. ``scope`` is
    a path fact and is honoured at every tier."""
    if alias is None or tier == TIER_EXACT_CROSS:
        return None
    return alias.basis


def _candidates_for(
    reg: Registry, concept: Concept, fields: Sequence[_Field], doc_type: str | None,
    tenant_aliases: Mapping[str, Sequence[Any]] | None, allow_token_match: bool,
) -> list[tuple[_Field, int, int, Alias | None]]:
    """``(field, tier, alias_order, alias)`` for every field that matches."""
    exact, patterns = _match_aliases(reg, concept, doc_type, tenant_aliases)
    out: list[tuple[_Field, int, int, Alias | None]] = []
    matched: set[int] = set()

    # Tiers 1-3: exact full path (and wildcard patterns) in alias order.
    for order, (lp, a, tier) in enumerate(exact):
        for f in fields:
            if f.order in matched:
                continue
            if f.lname == lp:
                out.append((f, tier, order, a))
                matched.add(f.order)
    base = len(exact)
    for order, (rx, a, tier) in enumerate(patterns):
        for f in fields:
            if f.order in matched:
                continue
            if rx.match(f.lname):
                out.append((f, tier, base + order, a))
                matched.add(f.order)
    base += len(patterns)

    # Tier 4: unit-stripped full path.
    stripped = [(_strip_unit(lp), a) for lp, a, _t in exact]
    for f in fields:
        if f.order in matched:
            continue
        fs = _strip_unit(f.lname)
        for order, (sp, a) in enumerate(stripped):
            if fs == sp:
                out.append((f, TIER_STRIPPED, base + order, a))
                matched.add(f.order)
                break
    base += len(stripped)

    # Tier 5: field tail vs bare alias, both unit-stripped — never for a field
    # whose exact path belongs to another concept under any doc type.
    bare = [(_strip_unit(lp), a) for lp, a, _t in exact if "." not in lp]
    if bare:
        for f in fields:
            if f.order in matched:
                continue
            ft = _strip_unit(_tail(f.lname))
            for order, (bp, a) in enumerate(bare):
                if ft == bp:
                    owner = _owner_of_exact(reg, f.lname, doc_type)
                    if owner is not None and owner[0] != concept.id:
                        break
                    out.append((f, TIER_TAIL, base + order, a))
                    matched.add(f.order)
                    break
    base += len(bare)

    # Tier 6: token match (opt-in) — the scorer's v3 resolver over a flat view.
    if allow_token_match and concept.bindings.scorer_key:
        from ..services.usali_scorer import _token_match_candidates

        flat = {f.name: f.value for f in fields if f.order not in matched and f.value is not None}
        cands = _token_match_candidates(concept.bindings.scorer_key, flat)
        cands.sort(key=lambda c: (c[0], c[1]))
        by_name = {f.name: f for f in fields}
        for order, (_score, _ln, key, _val) in enumerate(cands):
            f = by_name.get(key)
            if f is not None and f.order not in matched:
                out.append((f, TIER_TOKEN, base + order, None))
                matched.add(f.order)
    return out


def resolve(
    fields: Any,
    concept: str,
    *,
    doc_type: str | None = None,
    want: Scope = "annual",
    basis: Basis | None = None,
    tenant_aliases: Mapping[str, Sequence[Any]] | None = None,
    allow_token_match: bool = False,
) -> Resolution:
    """Resolve one concept on an extraction payload. See the module docstring."""
    reg = _REGISTRY
    c = reg.concepts.get(concept)
    if c is None:
        raise KeyError(f"unknown concept {concept!r}")
    dt = (doc_type or "").strip().upper() or None
    flds = _as_fields(fields)
    doc_scope = _doc_scope(flds, dt, reg)
    allowed = _ALLOWED_SCOPES[want]

    # Sort key: tier, then registry (alias) order, then first seen. Scope and
    # basis are FILTERS, never a preference — alias order in the YAML is what
    # puts an annual line ahead of a generic one.
    ranked: list[tuple[tuple[int, int, int], _Field, Alias | None, Scope, Basis, str | None]] = []
    for f, tier, order, alias in _candidates_for(reg, c, flds, dt, tenant_aliases, allow_token_match):
        is_slice, slice_scope = _subordinate_scope(f.lname, reg._subordinate)
        if alias is not None and alias.scope is not None:
            scope: Scope = alias.scope
        elif is_slice:
            scope = slice_scope or "unknown"
        else:
            scope = _path_scope_hint(f.lname) or doc_scope
        b = _basis_for(f.lname, dt, _alias_basis(alias, tier))
        excluded: str | None = None
        if is_slice and slice_scope is None:
            # A page, daily, MTD / QTD, day-of-week or prior-year slice is
            # never a period total under any ``want``.
            excluded = "period_mismatch"
        elif (is_slice and want != slice_scope) or scope not in allowed:
            excluded = "period_mismatch"
        elif basis is not None and b != basis:
            excluded = "basis_excluded"
        elif f.value is None or (c.is_numeric() and _coerce_number(f.value) is None):
            excluded = "unit_unknown" if f.value is not None else "no_source"
        key = (tier, order, f.order)
        ranked.append((key, f, alias, scope, b, excluded))
    ranked.sort(key=lambda r: r[0])

    candidates = tuple(
        Candidate(f.name, f.value, k[0], s, b, a.path if a else None, ex)
        for k, f, a, s, b, ex in ranked
    )
    for _k, f, _a, s, b, ex in ranked:
        if ex is None:
            value = _coerce_number(f.value) if c.is_numeric() else f.value
            return Resolution(
                concept=concept, value=value, field_name=f.name, source_page=f.source_page,
                unit=f.unit or c.unit, confidence=f.confidence, reviewed=f.reviewed,
                doc_type=dt, scope=s, basis=b, reason=None, candidates=candidates,
            )

    reason: Reason = "no_source"
    exclusions = {r[5] for r in ranked if r[5]}
    for pick in ("basis_excluded", "period_mismatch", "unit_unknown"):
        if pick in exclusions:
            reason = pick  # type: ignore[assignment]
            break
    return Resolution(
        concept=concept, value=None, field_name=None, source_page=None, unit=c.unit,
        confidence=None, reviewed=None, doc_type=dt, scope="unknown",
        basis=basis or "unknown", reason=reason, candidates=candidates,
    )


def resolve_many(fields: Any, concepts: Iterable[str], **kw: Any) -> dict[str, Resolution]:
    flds = _as_fields(fields)
    return {cid: resolve(flds, cid, **kw) for cid in concepts}


def concept_for_path(field_name: str, *, doc_type: str | None = None) -> tuple[str, Basis, Scope] | None:
    """``(concept, basis, scope)`` for a raw extraction path, or ``None``.

    Subordinate slices ARE classified here (``p_and_l_usali.monthly.apr_2024.gop``
    → ``("gop", "actual", "monthly")``) — the caller decides admissibility.
    A path with no period hint reports scope ``unknown``.
    """
    reg = _REGISTRY
    lname = field_name.strip().lower()
    if not lname:
        return None
    dt = (doc_type or "").strip().upper() or None
    keys = [k for k, _t in _ordered_keys(reg, dt)]

    own = set(reg.alias_keys_for(dt)) | {"*"}
    hit = _owner_of_exact(reg, lname, dt)  # tiers 1-3: exact / pattern, any key
    if hit is None:  # tier 4: unit-stripped full path
        fs = _strip_unit(lname)
        for key in keys:
            for lp, (cid, a) in reg._alias_index.get(key, {}).items():
                if _strip_unit(lp) == fs:
                    hit = (cid, a, TIER_STRIPPED if key in own else TIER_EXACT_CROSS)
                    break
            if hit:
                break
    if hit is None:  # tier 5: tail vs bare alias (no exact owner exists by now)
        ft = _strip_unit(_tail(lname))
        for key in keys:
            for lp, (cid, a) in reg._alias_index.get(key, {}).items():
                if "." not in lp and _strip_unit(lp) == ft:
                    hit = (cid, a, TIER_TAIL if key in own else TIER_EXACT_CROSS)
                    break
            if hit:
                break
    if hit is None:
        return None
    cid, alias, tier = hit
    is_slice, slice_scope = _subordinate_scope(lname, reg._subordinate)
    if alias.scope is not None:
        scope: Scope = alias.scope
    elif is_slice:
        scope = slice_scope or "unknown"
    else:
        scope = _path_scope_hint(lname) or "unknown"
    return cid, _basis_for(lname, dt, _alias_basis(alias, tier)), scope


def identities() -> list[Identity]:
    out: list[Identity] = []
    for cid, c in _REGISTRY.concepts.items():
        if not c.identity:
            continue
        tree = ast.parse(c.identity, mode="eval")
        terms = tuple(dict.fromkeys(n.id for n in ast.walk(tree) if isinstance(n, ast.Name)))
        out.append(
            Identity(
                concept=cid, expression=c.identity, terms=terms,
                optional_terms=tuple(c.identity_optional_terms), tolerance=c.identity_tolerance,
            )
        )
    return out


def tail_collisions(reg: Registry | None = None) -> list[tuple[str, str, str, str]]:
    """``(bare_alias, concept, dotted_alias, other_concept)`` where a bare alias
    equals the unit-stripped tail of another concept's dotted alias. Not an
    error (tier 4 refuses those fields) — surfaced for DRIFT_NOTES."""
    reg = reg or _REGISTRY
    tails: dict[str, list[tuple[str, str]]] = {}
    for cid, c in reg.concepts.items():
        for aliases in c.aliases.values():
            for a in aliases:
                lp = a.path.lower()
                if "." in lp and not _is_pattern(lp):
                    tails.setdefault(_strip_unit(_tail(lp)), []).append((cid, a.path))
    out: list[tuple[str, str, str, str]] = []
    for cid, c in reg.concepts.items():
        for aliases in c.aliases.values():
            for a in aliases:
                lp = a.path.lower()
                if "." in lp:
                    continue
                for other, dotted in tails.get(_strip_unit(lp), ()):
                    if other != cid:
                        out.append((a.path, cid, dotted, other))
    return out


__all__ = [
    "REASON_CODES",
    "Alias",
    "Basis",
    "Candidate",
    "Concept",
    "Identity",
    "Registry",
    "RegistryError",
    "Resolution",
    "Scope",
    "concept_for_path",
    "get_registry",
    "identities",
    "is_market_segment",
    "is_om_historical_year",
    "load_registry",
    "registry_version",
    "resolve",
    "resolve_many",
    "tail_collisions",
]
