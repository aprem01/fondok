"""Two-firm isolation check against a running Fondok worker — FON-93 Phase 3.

Proves, over HTTP, that one institution cannot read or touch another's
deal: for every deal-scoped route the worker mounts, firm B is sent at
firm A's real deal (and document) and must get 404 or 403 — never a 2xx
with a body. A request with no credentials must get 401 on a deployed
worker. ``/admin/tenants``, ``/observability/*`` and ``/settings/tenant``
are checked against the caller's registry ``kind`` (internal sees the
whole registry and the cross-tenant observability view; external sees
only its own row and gets 403 on ``?all=1``).

Credentials — pick one:

* **JWT mode** (a deployed worker): ``FIRM_A_JWT`` and ``FIRM_B_JWT`` in
  the environment, each a Clerk session token for a member of that
  firm's organization (DevTools → Network → any worker call →
  ``Authorization`` header, or Clerk's ``getToken()``). Tokens expire in
  about a minute, so mint them right before running.
* **Header mode** (local only, ``ALLOW_TENANT_HEADER_WITHOUT_JWT=1``):
  ``--tenant-a <uuid> --tenant-b <uuid>`` (or ``TENANT_A`` / ``TENANT_B``
  env). Never available on a deployed worker — whether the JWT is required
  is PROBED first (an anonymous ``GET /deals`` must be 401; the public
  ``/health`` no longer reports the auth posture, ``/admin/health`` does,
  internal tenants only) and the unauthenticated expectation is derived
  from it.

Routes come from the worker code (``app.main``, the same enumeration
``tests/test_unauthenticated_routes_guard.py`` uses) so a route that
ships tomorrow is probed tomorrow; ``--routes openapi`` reads them from
the running worker's ``/openapi.json`` instead when the local checkout
might not match the deploy.

**It writes nothing by default.** Only GET routes are probed against firm
A's real deal; mutating routes are listed as SKIPPED. ``--allow-writes``
creates one scratch deal per firm (``POST /deals``), probes every
mutating route against firm A's *scratch* deal as firm B, and hard-deletes
both scratch deals at the end (falls back to archive when the credential
is not an admin). A leak on a mutating route therefore damages only a
scratch row, never a real deal.

Verdicts per route: ``PASS`` (stranger refused, owner served), ``LEAK``
(stranger or anonymous got a 2xx), ``INCONCLUSIVE`` (the handler was not
reached — 422/400/405 — or the owner is refused too, so the probe proves
nothing), ``ERROR`` (5xx or a credential the worker rejected), ``SKIP``.
Exit status: 0 clean, 1 on any LEAK or ERROR (``--strict`` also fails on
INCONCLUSIVE), 2 on a usage / discovery problem.

Usage (from ``apps/worker``)::

    # production worker, two real firms, read-only
    FIRM_A_JWT=eyJ… FIRM_B_JWT=eyJ… uv run python scripts/isolation_check.py \\
        --base-url https://fondok-worker-production.up.railway.app

    # same, plus mutating routes on scratch deals
    … --allow-writes

    # local worker started with ALLOW_TENANT_HEADER_WITHOUT_JWT=1
    uv run python scripts/isolation_check.py --base-url http://localhost:8001 \\
        --tenant-a 11111111-1111-1111-1111-1111aaaaaaaa \\
        --tenant-b 22222222-2222-2222-2222-2222bbbbbbbb --allow-writes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

# ─────────────────────────── routes ───────────────────────────

# Path parameters that are not a deal or a document id. Any value works:
# the route must refuse firm B on the DEAL before it looks at these.
_SUB_ID_PLACEHOLDERS: dict[str, str] = {
    "name": "revenue",  # engine name — /deals/{deal_id}/engines/{name}
    "section_id": "executive_summary",
}

# Required query parameters by name. ``q`` is the FON-93 lesson: without
# it ``GET /deals/{id}/search`` 422s before the handler and proves nothing.
_QUERY_PLACEHOLDERS: dict[str, Any] = {
    "q": "probe",
    "base": str(uuid.uuid4()),
}

# Minimal bodies that reach the tenant check for routes whose generated
# body would be rejected on semantics before the deal lookup.
BODY_HINTS: dict[tuple[str, str], dict[str, Any]] = {
    ("POST", "/deals/{deal_id}/gate1"): {"decision": "approve"},
    ("POST", "/deals/{deal_id}/gate2"): {"recommendation": "go"},
    ("POST", "/deals/{deal_id}/transition"): {"next_state": "VALIDATING"},
    # list fields with min_length=1 — the generated placeholder is [].
    ("POST", "/deals/{deal_id}/imports/historicals/apply"): {
        "changes": [{"cell_id": "probe:cell", "new_value": 1.0}]
    },
    ("POST", "/deals/{deal_id}/imports/projections/apply"): {
        "changes": [{"key": "probe", "new_value": 1.0}]
    },
    ("POST", "/deals/{deal_id}/scenarios/compare"): {"scenario_ids": [str(uuid.uuid4())]},
    ("POST", "/deals/{deal_id}/ask"): {"question": "What is the going-in cap rate?"},
    ("PATCH", "/deals/{deal_id}"): {"name": "Isolation probe rename"},
    # ``cell_key`` must match CELL_KEY_RE (api/cell_comments.py).
    ("POST", "/deals/{deal_id}/comments"): {"cell_key": "proj:revenue.years[0].adr", "body": "probe"},
    ("POST", "/deals/{deal_id}/comments/resolve"): {"cell_key": "proj:revenue.years[0].adr"},
    # ReclassifyBody: at least one field, else 422 "nothing to update".
    ("PATCH", "/deals/{deal_id}/documents/{doc_id}/classification"): {"doc_type": "T12"},
    ("POST", "/deals/{deal_id}/scenarios"): {"name": "probe"},
}

# Routes whose 2xx for a stranger is NOT a leak, each with the reason and a
# predicate over the parsed JSON body that must hold for the row to pass.
# Adding here is a security review, not a test fix.
_ACCEPTED_2XX: dict[tuple[str, str], tuple[str, Any]] = {
    ("GET", "/deals/{deal_id}/audit"): (
        "documented: a foreign deal id returns the EMPTY feed (api/audit.py — "
        "'both shapes leak the same info'); accepted only when total=0, entries=[]",
        lambda body: isinstance(body, dict) and body.get("total") == 0 and body.get("entries") == [],
    ),
}

# Routes that compute nothing and read no table, so a stranger's 2xx is a
# placeholder rather than a leak. Empty since 2026-10-17: the legacy
# ``/model/*`` stubs were deleted (nothing in apps/web called them). Adding
# here is a security review, not a test fix.
_STUB_ROUTES: dict[tuple[str, str], str] = {}

# Routes never probed as the owner: a 200 streams or computes for a long
# time, and the stranger-side 404 is the load-bearing assertion anyway.
_HEAVY_OWNER_ROUTES: tuple[str, ...] = (
    "/memo/stream",
    "/export/",
    "/exports/",
    "/engines/compare/xlsx",
)

# Multipart uploads cannot be expressed as JSON; covered by the upload and
# P&L round-trip tests (both gate on the deal's tenant before reading the file).
_NEVER_PROBED: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/deals/{deal_id}/documents/upload"),
        ("POST", "/deals/{deal_id}/imports/historicals"),
        ("POST", "/deals/{deal_id}/imports/projections"),
    }
)

_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class Route:
    method: str
    path: str  # template, e.g. /deals/{deal_id}/documents/{doc_id}
    has_body: bool
    required_query: tuple[str, ...]
    body_model: Any = None  # pydantic model class when enumerated locally

    @property
    def deal_scoped(self) -> bool:
        return "{deal_id}" in self.path

    @property
    def mutating(self) -> bool:
        return self.method in _MUTATING

    @property
    def needs_document(self) -> bool:
        return "{doc_id}" in self.path

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)


def _is_required(param: Any) -> bool:
    fi = getattr(param, "field_info", None)
    try:
        return bool(fi.is_required())
    except Exception:  # pydantic v1 shape
        return bool(getattr(param, "required", False))


def routes_from_app(app: Any) -> list[Route]:
    """Every ``APIRoute`` the FastAPI app mounts, one entry per method."""
    from fastapi.routing import APIRoute

    out: list[Route] = []
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        required = tuple(p.name for p in r.dependant.query_params if _is_required(p))
        body_model = None
        if r.body_field is not None:
            # FastAPI's pydantic-v2 ``ModelField`` keeps the model on
            # ``field_info.annotation``; ``type_`` is None there.
            fi = getattr(r.body_field, "field_info", None)
            body_model = (
                getattr(fi, "annotation", None)
                or getattr(r.body_field, "type_", None)
                or getattr(r.body_field, "annotation", None)
            )
        for m in sorted(r.methods or ()):
            if m in {"HEAD", "OPTIONS"}:
                continue
            out.append(Route(m, r.path, r.body_field is not None, required, body_model))
    return sorted(out, key=lambda x: (x.path, x.method))


def routes_from_local_code() -> list[Route]:
    """Import ``app.main`` (the worker checkout this script sits in)."""
    # Importing the app must never create a database file beside the
    # script; the engine is lazy but the DSN is read at import.
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    from app.main import app

    return routes_from_app(app)


def routes_from_openapi(spec: dict[str, Any]) -> list[Route]:
    out: list[Route] = []
    for path, ops in (spec.get("paths") or {}).items():
        for method, op in ops.items():
            m = method.upper()
            if m in {"HEAD", "OPTIONS"} or not isinstance(op, dict):
                continue
            required = tuple(
                p["name"]
                for p in op.get("parameters") or []
                if p.get("in") == "query" and p.get("required")
            )
            out.append(Route(m, path, "requestBody" in op, required, None))
    return sorted(out, key=lambda x: (x.path, x.method))


# ─────────────────────────── bodies ───────────────────────────


def _placeholder_for(annotation: Any) -> Any:
    """A value of roughly the right shape for one required field."""
    import typing

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Union or str(origin) == "<class 'types.UnionType'>":
        non_none = [a for a in args if a is not type(None)]
        return _placeholder_for(non_none[0]) if non_none else None
    if origin in (list, tuple, set):
        return []
    if origin is dict:
        return {}
    if origin is typing.Literal:
        return args[0]
    if annotation is bool:
        return False
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    if annotation is str:
        return "probe"
    if annotation is uuid.UUID:
        return str(uuid.uuid4())
    try:
        import enum

        if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
            return next(iter(annotation)).value
    except Exception:
        pass
    fields = getattr(annotation, "model_fields", None)
    if isinstance(fields, dict):
        return minimal_body_from_model(annotation)
    return "probe"


def minimal_body_from_model(model: Any) -> dict[str, Any]:
    fields = getattr(model, "model_fields", None)
    if not isinstance(fields, dict):
        return {}
    body: dict[str, Any] = {}
    for name, f in fields.items():
        try:
            required = f.is_required()
        except Exception:
            required = False
        if required:
            body[name] = _placeholder_for(f.annotation)
    return body


def body_for(route: Route) -> dict[str, Any] | None:
    if not route.has_body:
        return None
    hint = BODY_HINTS.get(route.key)
    if hint is not None:
        return hint
    if route.body_model is not None:
        return minimal_body_from_model(route.body_model)
    return {}


# ─────────────────────────── credentials ───────────────────────────


@dataclass
class Firm:
    label: str
    headers: dict[str, str]
    tenant_id: str | None = None
    kind: str | None = None  # registry kind, None when unregistered
    deals: list[dict[str, Any]] = field(default_factory=list)
    deal_id: str | None = None
    deal_name: str | None = None
    doc_id: str | None = None
    scratch_deal_id: str | None = None


def firms_from_args(args: argparse.Namespace) -> tuple[Firm, Firm, str]:
    jwt_a = (os.environ.get("FIRM_A_JWT") or "").strip()
    jwt_b = (os.environ.get("FIRM_B_JWT") or "").strip()
    if jwt_a and jwt_b:
        return (
            Firm("firm A", {"Authorization": f"Bearer {jwt_a}"}),
            Firm("firm B", {"Authorization": f"Bearer {jwt_b}"}),
            "jwt",
        )
    ta = (args.tenant_a or os.environ.get("TENANT_A") or "").strip()
    tb = (args.tenant_b or os.environ.get("TENANT_B") or "").strip()
    if ta and tb:
        for v in (ta, tb):
            try:
                uuid.UUID(v)
            except ValueError as exc:
                raise SystemExit(f"error: {v!r} is not a tenant UUID") from exc
        if ta == tb:
            raise SystemExit("error: --tenant-a and --tenant-b must differ")
        return Firm("firm A", {"X-Tenant-Id": ta}), Firm("firm B", {"X-Tenant-Id": tb}), "header"
    raise SystemExit(
        "error: give FIRM_A_JWT + FIRM_B_JWT (a deployed worker) or "
        "--tenant-a/--tenant-b (local header mode)"
    )


# ─────────────────────────── probing ───────────────────────────


@dataclass
class Probe:
    status: int | None
    body_bytes: int
    detail: str = ""
    body_text: str = ""  # up to the first 512 bytes, for the accepted-2xx predicates

    def json(self) -> Any:
        try:
            return json.loads(self.body_text)
        except ValueError:
            return None


async def probe(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None,
    json_body: Any,
    timeout_s: float,
) -> Probe:
    """Send one request, read the status and at most 512 bytes of body,
    then close — so an SSE stream or an export cannot hang the run."""
    try:
        async with client.stream(
            method,
            url,
            headers=headers,
            params=params,
            json=json_body,
            timeout=timeout_s,
        ) as resp:
            got = 0
            snippet = b""
            async for chunk in resp.aiter_bytes():
                got += len(chunk)
                if len(snippet) < 512:
                    snippet += chunk[: 512 - len(snippet)]
                if got >= 512:
                    break
            text = snippet.decode("utf-8", "replace")
            return Probe(resp.status_code, got, text[:200], text)
    except httpx.TimeoutException:
        return Probe(None, 0, "timeout")
    except httpx.HTTPError as exc:
        return Probe(None, 0, f"{type(exc).__name__}: {exc}")


async def get_json(
    client: httpx.AsyncClient, path: str, headers: dict[str, str], timeout_s: float
) -> tuple[int | None, Any]:
    try:
        r = await client.get(path, headers=headers, timeout=timeout_s)
    except httpx.HTTPError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:200]


def fill_path(route: Route, firm: Firm, *, deal_id: str) -> str | None:
    """Substitute every path parameter; ``None`` when the route needs a
    document and the firm has none."""
    path = route.path
    if route.needs_document:
        if not firm.doc_id:
            return None
        path = path.replace("{doc_id}", firm.doc_id)
    path = path.replace("{deal_id}", deal_id)

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        return _SUB_ID_PLACEHOLDERS.get(name, str(uuid.uuid4()))

    return re.sub(r"{(\w+)}", sub, path)


def params_for(route: Route) -> dict[str, Any] | None:
    if not route.required_query:
        return None
    return {q: _QUERY_PLACEHOLDERS.get(q, "probe") for q in route.required_query}


# ─────────────────────────── results ───────────────────────────


@dataclass
class Result:
    check: str  # route label or named check
    verdict: str  # PASS | LEAK | INCONCLUSIVE | ERROR | SKIP
    stranger: str = ""
    anonymous: str = ""
    owner: str = ""
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "verdict": self.verdict,
            "stranger": self.stranger,
            "anonymous": self.anonymous,
            "owner": self.owner,
            "note": self.note,
        }


def _fmt(p: Probe | None) -> str:
    if p is None:
        return "-"
    if p.status is None:
        return p.detail or "n/a"
    return str(p.status)


def _refused(status: int | None) -> bool:
    return status in (403, 404)


def _served(status: int | None) -> bool:
    return status is not None and 200 <= status < 300


def _handler_not_reached(status: int | None) -> bool:
    return status in (400, 405, 422)


# ─────────────────────────── the run ───────────────────────────


@dataclass
class RunOptions:
    allow_writes: bool = False
    strict: bool = False
    timeout_s: float = 30.0
    only_deal_id: str | None = None


class Checker:
    def __init__(
        self,
        client: httpx.AsyncClient,
        firm_a: Firm,
        firm_b: Firm,
        mode: str,
        routes: list[Route],
        options: RunOptions,
        *,
        out=sys.stdout,
    ) -> None:
        self.client = client
        self.a = firm_a
        self.b = firm_b
        self.mode = mode
        self.routes = routes
        self.opt = options
        self.out = out
        self.results: list[Result] = []
        self.jwt_required: bool | None = None
        self.health: dict[str, Any] = {}

    # ── helpers ──
    def say(self, msg: str = "") -> None:
        print(msg, file=self.out)

    def add(self, r: Result) -> None:
        self.results.append(r)

    def anonymous_expectation(self, status: int | None, *, deal_scoped: bool = True) -> tuple[bool, str]:
        """Is this anonymous status acceptable? Returns (ok, label).

        With the JWT required (every deployed worker) the only right answer
        is 401. With the header path open (local only) a request with no
        header is served as ``DEFAULT_TENANT_ID``: on a deal-scoped route
        that tenant does not own the deal, so 404/403 is correct; on a
        tenant-level route (``/settings/tenant``, ``/admin/tenants``,
        ``/observability/*``) it is served its OWN view, so a 2xx there is
        the documented local behaviour, not a leak — the row is marked and
        the run says to repeat in JWT mode.
        """
        if self.jwt_required:
            if _served(status):
                return False, "LEAK"
            return status == 401, "401 expected"
        if deal_scoped:
            if _served(status):
                return False, "LEAK"
            return _refused(status) or status == 401, "404/403 (header path open)"
        if _served(status):
            return True, "served DEFAULT_TENANT_ID's own view (header path open — must be 401 on a deployment)"
        return status in (401, 403, 404), "refused (header path open)"

    # ── discovery ──
    async def discover(self) -> bool:
        t = self.opt.timeout_s
        st, health = await get_json(self.client, "/health", {}, t)
        if st != 200 or not isinstance(health, dict):
            self.say(f"error: GET /health → {st} {str(health)[:200]}")
            return False
        self.health = health
        # The auth posture is no longer on the public /health (2026-10-17 —
        # it is operator detail on /admin/health, internal tenants only), so
        # the JWT requirement is observed, not read: an anonymous GET /deals
        # is 401 on every deployed worker and served (as DEFAULT_TENANT_ID)
        # only when the local header path is open.
        anon_status, _anon_body = await get_json(self.client, "/deals", {}, t)
        self.jwt_required = anon_status == 401
        self.say(
            f"worker {health.get('version')}  db={health.get('db')}  "
            f"anonymous GET /deals → {anon_status}  jwt_required={self.jwt_required}"
        )
        if self.mode == "header" and self.jwt_required:
            self.say(
                "error: header mode needs ALLOW_TENANT_HEADER_WITHOUT_JWT=1 on the "
                "worker (anonymous GET /deals is 401). Use FIRM_A_JWT/FIRM_B_JWT."
            )
            return False
        if self.mode == "jwt" and not self.jwt_required:
            self.add(
                Result(
                    "anonymous GET /deals",
                    "LEAK",
                    note=(
                        f"served {anon_status} with no credentials — the header tenant "
                        "path is OPEN on this worker, never on a deployment"
                    ),
                )
            )

        for firm in (self.a, self.b):
            st, body = await get_json(self.client, "/settings/tenant", firm.headers, t)
            if st != 200 or not isinstance(body, dict):
                self.say(f"error: {firm.label} GET /settings/tenant → {st} {str(body)[:200]}")
                self.add(Result(f"{firm.label} credential", "ERROR", note=f"/settings/tenant → {st}"))
                return False
            firm.tenant_id = str(body.get("tenant_id"))
            reg = body.get("registry") or {}
            firm.kind = reg.get("kind") if isinstance(reg, dict) else None
            st, deals = await get_json(self.client, "/deals", firm.headers, t)
            if st != 200 or not isinstance(deals, list):
                self.say(f"error: {firm.label} GET /deals → {st} {str(deals)[:200]}")
                self.add(Result(f"{firm.label} deals", "ERROR", note=f"/deals → {st}"))
                return False
            firm.deals = [d for d in deals if isinstance(d, dict)]
            self.say(
                f"{firm.label}: tenant={firm.tenant_id} kind={firm.kind or 'unregistered'} "
                f"deals={len(firm.deals)}"
            )

        if self.a.tenant_id == self.b.tenant_id:
            self.say("error: both credentials resolve to the SAME tenant — pick two firms")
            self.add(Result("distinct tenants", "ERROR", note="same tenant_id"))
            return False
        self.add(Result("distinct tenants", "PASS", note=f"{self.a.tenant_id} ≠ {self.b.tenant_id}"))

        # Deal lists must be disjoint, and every row must carry the caller's tenant.
        ids_a = {str(d.get("id")) for d in self.a.deals}
        ids_b = {str(d.get("id")) for d in self.b.deals}
        overlap = ids_a & ids_b
        foreign = [
            (firm.label, str(d.get("id")))
            for firm in (self.a, self.b)
            for d in firm.deals
            if str(d.get("tenant_id")) != firm.tenant_id
        ]
        if overlap or foreign:
            self.add(
                Result(
                    "GET /deals lists are disjoint",
                    "LEAK",
                    note=f"shared ids={sorted(overlap)[:5]} foreign rows={foreign[:5]}",
                )
            )
        else:
            self.add(Result("GET /deals lists are disjoint", "PASS", note=f"A={len(ids_a)} B={len(ids_b)}"))

        # Firm A's probe deal.
        if self.opt.only_deal_id:
            if self.opt.only_deal_id not in ids_a:
                self.say(f"error: --deal-id {self.opt.only_deal_id} is not in firm A's list")
                return False
            self.a.deal_id = self.opt.only_deal_id
        elif self.a.deals:
            self.a.deal_id = str(self.a.deals[0]["id"])
        elif self.opt.allow_writes:
            self.a.deal_id = None  # the scratch deal will stand in
        else:
            self.say("error: firm A has no deals to probe — pass --allow-writes to create a scratch deal")
            return False
        if self.a.deal_id:
            self.a.deal_name = next(
                (str(d.get("name")) for d in self.a.deals if str(d.get("id")) == self.a.deal_id), None
            )
            st, docs = await get_json(
                self.client, f"/deals/{self.a.deal_id}/documents", self.a.headers, t
            )
            if st == 200 and isinstance(docs, list) and docs:
                self.a.doc_id = str(docs[0].get("id"))
            self.say(
                f"probe deal: {self.a.deal_id} ({self.a.deal_name!r}) "
                f"document={self.a.doc_id or 'none — per-document routes will be SKIPPED'}"
            )
        return True

    # ── scratch deals ──
    async def create_scratch(self) -> bool:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for firm in (self.a, self.b):
            body = {"name": f"isolation-check scratch {firm.label} {stamp}", "city": "Probe, XX", "keys": 10}
            try:
                r = await self.client.post("/deals", json=body, headers=firm.headers, timeout=self.opt.timeout_s)
            except httpx.HTTPError as exc:
                self.add(Result(f"scratch deal {firm.label}", "ERROR", note=str(exc)))
                return False
            if r.status_code != 201:
                self.add(Result(f"scratch deal {firm.label}", "ERROR", note=f"POST /deals → {r.status_code} {r.text[:120]}"))
                return False
            firm.scratch_deal_id = str(r.json()["id"])
            self.say(f"scratch deal for {firm.label}: {firm.scratch_deal_id}")
        if not self.a.deal_id:
            self.a.deal_id = self.a.scratch_deal_id
            self.a.deal_name = "scratch"
        # The freshest cross-check: B must not see A's brand-new deal.
        p = await probe(
            self.client, "GET", f"/deals/{self.a.scratch_deal_id}", headers=self.b.headers,
            params=None, json_body=None, timeout_s=self.opt.timeout_s,
        )
        self.add(
            Result(
                "B reads A's scratch deal", "PASS" if _refused(p.status) else "LEAK", stranger=_fmt(p)
            )
        )
        return True

    async def cleanup_scratch(self) -> None:
        for firm in (self.a, self.b):
            if not firm.scratch_deal_id:
                continue
            did = firm.scratch_deal_id
            try:
                r = await self.client.delete(f"/deals/{did}/hard", headers=firm.headers, timeout=self.opt.timeout_s)
                how = "hard-deleted"
                if r.status_code == 403:
                    r = await self.client.delete(f"/deals/{did}", headers=firm.headers, timeout=self.opt.timeout_s)
                    how = "archived (credential is not an admin — delete it from the UI)"
                ok = 200 <= r.status_code < 300
            except httpx.HTTPError as exc:
                ok, how = False, str(exc)
            self.add(
                Result(
                    f"cleanup scratch {firm.label}",
                    "PASS" if ok else "ERROR",
                    note=f"{did} {how}" if ok else f"{did} NOT removed: {how}",
                )
            )

    # ── route probes ──
    async def probe_routes(self) -> None:
        t = self.opt.timeout_s
        for route in self.routes:
            if not route.deal_scoped or route.key in _NEVER_PROBED:
                continue
            label = f"{route.method} {route.path}"
            if route.key in _STUB_ROUTES:
                self.add(Result(label, "SKIP", note=_STUB_ROUTES[route.key]))
                continue
            if route.mutating and not self.opt.allow_writes:
                self.add(Result(label, "SKIP", note="mutating — pass --allow-writes (probed on a scratch deal)"))
                continue
            target = self.a.deal_id
            if route.mutating:
                target = self.a.scratch_deal_id or self.a.deal_id
            assert target is not None
            path = fill_path(route, self.a, deal_id=target)
            if path is None:
                self.add(Result(label, "SKIP", note="firm A has no document on the probe deal"))
                continue
            params = params_for(route)
            body = body_for(route)

            stranger = await probe(
                self.client, route.method, path, headers=self.b.headers, params=params, json_body=body, timeout_s=t
            )
            anonymous = await probe(
                self.client, route.method, path, headers={}, params=params, json_body=body, timeout_s=t
            )
            owner: Probe | None = None
            if route.method == "GET" and not any(h in route.path for h in _HEAVY_OWNER_ROUTES):
                owner = await probe(
                    self.client, route.method, path, headers=self.a.headers, params=params, json_body=body, timeout_s=t
                )

            notes: list[str] = []
            verdict = "PASS"
            accepted = _ACCEPTED_2XX.get(route.key)
            if _served(stranger.status) and accepted is not None and accepted[1](stranger.json()):
                notes.append(f"stranger {stranger.status} accepted: {accepted[0]}")
            elif _served(stranger.status):
                verdict = "LEAK"
                notes.append(f"stranger got {stranger.status} with {stranger.body_bytes}B body")
            elif stranger.status is None or stranger.status >= 500:
                verdict = "ERROR"
                notes.append(f"stranger: {stranger.detail or stranger.status}")
            elif stranger.status == 401:
                verdict = "ERROR"
                notes.append("stranger credential rejected (401) — token expired?")
            elif _handler_not_reached(stranger.status):
                verdict = "INCONCLUSIVE"
                notes.append(f"stranger {stranger.status}: handler not reached ({stranger.detail[:80]})")
            elif not _refused(stranger.status):
                verdict = "INCONCLUSIVE"
                notes.append(f"stranger {stranger.status} is neither 2xx nor 403/404")

            anon_ok, anon_label = self.anonymous_expectation(anonymous.status)
            if _served(anonymous.status) and accepted is not None and accepted[1](anonymous.json()):
                notes.append(f"anonymous {anonymous.status} accepted (same empty shape)")
            elif _served(anonymous.status):
                verdict = "LEAK"
                notes.append(f"ANONYMOUS got {anonymous.status}")
            elif not anon_ok and verdict == "PASS":
                if anonymous.status is None or anonymous.status >= 500:
                    verdict = "ERROR"
                else:
                    verdict = "INCONCLUSIVE"
                notes.append(f"anonymous {anonymous.status}, {anon_label}")

            if owner is not None and verdict == "PASS":
                if _refused(owner.status) or owner.status == 401:
                    verdict = "INCONCLUSIVE"
                    notes.append(f"owner also refused ({owner.status}) — route may be dead for this deal")
                elif owner.status is None or owner.status >= 500:
                    notes.append(f"owner {owner.detail or owner.status} (not a leak)")

            self.add(
                Result(
                    label,
                    verdict,
                    stranger=_fmt(stranger),
                    anonymous=_fmt(anonymous),
                    owner=_fmt(owner),
                    note="; ".join(notes),
                )
            )

    # ── kind-specific surfaces ──
    async def probe_kind_surfaces(self) -> None:
        t = self.opt.timeout_s
        a, b = self.a, self.b

        # /settings/tenant — already fetched; anonymous must be refused.
        anon = await probe(self.client, "GET", "/settings/tenant", headers={}, params=None, json_body=None, timeout_s=t)
        ok, lbl = self.anonymous_expectation(anon.status, deal_scoped=False)
        self.add(
            Result(
                "GET /settings/tenant",
                "PASS" if ok else ("LEAK" if _served(anon.status) else "INCONCLUSIVE"),
                owner="200", anonymous=_fmt(anon),
                note=f"A kind={a.kind or 'unregistered'} B kind={b.kind or 'unregistered'}; anonymous {lbl}",
            )
        )

        # /admin/tenants — internal sees all; external sees only itself.
        for me, other in ((a, b), (b, a)):
            st, body = await get_json(self.client, "/admin/tenants", me.headers, t)
            label = f"GET /admin/tenants as {me.label} ({me.kind or 'unregistered'})"
            if st == 403:
                self.add(Result(label, "PASS", owner="403", note="role is not admin — registry not shown"))
                continue
            if st != 200 or not isinstance(body, dict):
                self.add(Result(label, "ERROR", owner=str(st), note=str(body)[:120]))
                continue
            rows = body.get("tenants") or []
            ids = {str(r.get("id")) for r in rows if isinstance(r, dict)}
            scope = body.get("scope")
            if me.kind == "internal":
                verdict = "PASS" if scope == "all" else "INCONCLUSIVE"
                note = f"scope={scope} rows={len(rows)}"
            else:
                leaked = other.tenant_id in ids
                verdict = "LEAK" if leaked else ("PASS" if scope == "own" and ids <= {me.tenant_id} else "INCONCLUSIVE")
                note = f"scope={scope} rows={len(rows)}" + (" — contains the OTHER firm's tenant" if leaked else "")
            self.add(Result(label, verdict, owner="200", note=note))
        anon = await probe(self.client, "GET", "/admin/tenants", headers={}, params=None, json_body=None, timeout_s=t)
        ok, lbl = self.anonymous_expectation(anon.status, deal_scoped=False)
        self.add(Result("GET /admin/tenants anonymous", "PASS" if ok else ("LEAK" if _served(anon.status) else "INCONCLUSIVE"), anonymous=_fmt(anon), note=lbl))

        # /observability — scoped view for everyone; ?all=1 only internal (JWT).
        for ep in ("/observability/cache-stats", "/observability/agent-costs"):
            for me in (a, b):
                scoped = await probe(self.client, "GET", ep, headers=me.headers, params=None, json_body=None, timeout_s=t)
                allv = await probe(self.client, "GET", ep, headers=me.headers, params={"all": "1"}, json_body=None, timeout_s=t)
                label = f"GET {ep} as {me.label} ({me.kind or 'unregistered'})"
                if self.mode == "header":
                    # The header path is a trusted operator by design (require_role).
                    verdict = "PASS" if _served(scoped.status) else "INCONCLUSIVE"
                    note = f"scoped={scoped.status} all=1→{allv.status} (header path = trusted caller; cross-tenant view allowed)"
                elif me.kind == "internal":
                    verdict = "PASS" if _served(scoped.status) and _served(allv.status) else "INCONCLUSIVE"
                    note = f"scoped={scoped.status} all=1→{allv.status} (internal: both expected 200)"
                else:
                    if _served(allv.status):
                        verdict, note = "LEAK", f"external tenant got the cross-tenant view (all=1 → {allv.status})"
                    elif allv.status == 403 and (_served(scoped.status) or scoped.status == 403):
                        verdict, note = "PASS", f"scoped={scoped.status} all=1→403"
                    else:
                        verdict, note = "INCONCLUSIVE", f"scoped={scoped.status} all=1→{allv.status}"
                self.add(Result(label, verdict, owner=_fmt(scoped), note=note))
            anon = await probe(self.client, "GET", ep, headers={}, params=None, json_body=None, timeout_s=t)
            ok, lbl = self.anonymous_expectation(anon.status, deal_scoped=False)
            self.add(Result(f"GET {ep} anonymous", "PASS" if ok else ("LEAK" if _served(anon.status) else "INCONCLUSIVE"), anonymous=_fmt(anon), note=lbl))

    # ── report ──
    def report(self) -> int:
        counts: dict[str, int] = {}
        for r in self.results:
            counts[r.verdict] = counts.get(r.verdict, 0) + 1
        w = max((len(r.check) for r in self.results), default=20)
        self.say()
        self.say(f"{'check':{w}s}  {'verdict':12s} {'B':>5s} {'anon':>5s} {'A':>5s}  note")
        self.say("-" * (w + 40))
        order = {"LEAK": 0, "ERROR": 1, "INCONCLUSIVE": 2, "PASS": 3, "SKIP": 4}
        for r in sorted(self.results, key=lambda x: (order.get(x.verdict, 9), x.check)):
            self.say(f"{r.check:{w}s}  {r.verdict:12s} {r.stranger or '-':>5s} {r.anonymous or '-':>5s} {r.owner or '-':>5s}  {r.note}")
        self.say()
        probed = sum(1 for r in self.results if r.check.split(" ", 1)[0] in _MUTATING | {"GET"} and "{deal_id}" in r.check)
        self.say(
            f"deal-scoped routes: {probed}  |  "
            + "  ".join(f"{k}={counts.get(k, 0)}" for k in ("PASS", "LEAK", "ERROR", "INCONCLUSIVE", "SKIP"))
        )
        if self.mode == "header":
            self.say("mode: header (local only). Run again in JWT mode against the deployed worker before Phase 3.")
        leaks = counts.get("LEAK", 0) + counts.get("ERROR", 0)
        if leaks:
            self.say(f"RESULT: FAIL — {leaks} leak/error row(s) above")
            return 1
        if self.opt.strict and counts.get("INCONCLUSIVE", 0):
            self.say(f"RESULT: FAIL (--strict) — {counts['INCONCLUSIVE']} inconclusive row(s)")
            return 1
        self.say("RESULT: PASS — no cross-firm leak observed")
        return 0

    async def run(self) -> int:
        if not await self.discover():
            self.report()
            return 2
        try:
            if self.opt.allow_writes and not await self.create_scratch():
                return 2
            await self.probe_routes()
            await self.probe_kind_surfaces()
        finally:
            if self.opt.allow_writes:
                await self.cleanup_scratch()
        return self.report()


# ─────────────────────────── entry points ───────────────────────────


async def run_checks(
    client: httpx.AsyncClient,
    firm_a: Firm,
    firm_b: Firm,
    *,
    mode: str,
    routes: list[Route],
    options: RunOptions | None = None,
    out=sys.stdout,
) -> tuple[int, list[Result]]:
    """Library entry point (the tests drive it over an ASGI transport)."""
    checker = Checker(client, firm_a, firm_b, mode, routes, options or RunOptions(), out=out)
    code = await checker.run()
    return code, checker.results


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default=os.environ.get("WORKER_URL", "http://localhost:8001"))
    p.add_argument("--tenant-a", help="header mode: firm A tenant UUID (or TENANT_A)")
    p.add_argument("--tenant-b", help="header mode: firm B tenant UUID (or TENANT_B)")
    p.add_argument("--deal-id", help="probe this deal of firm A instead of its first")
    p.add_argument("--routes", choices=("local", "openapi"), default="local",
                   help="enumerate routes from this checkout's app.main (default) or the worker's /openapi.json")
    p.add_argument("--allow-writes", action="store_true",
                   help="create a scratch deal per firm, probe mutating routes on it, clean up")
    p.add_argument("--strict", action="store_true", help="fail on INCONCLUSIVE rows too")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--json", dest="json_out", help="also write the result rows to this file")
    return p


async def _amain(args: argparse.Namespace) -> int:
    firm_a, firm_b, mode = firms_from_args(args)
    base = args.base_url.rstrip("/")
    async with httpx.AsyncClient(base_url=base) as client:
        if args.routes == "openapi":
            st, spec = await get_json(client, "/openapi.json", {}, args.timeout)
            if st != 200 or not isinstance(spec, dict):
                print(f"error: GET /openapi.json → {st}", file=sys.stderr)
                return 2
            routes = routes_from_openapi(spec)
        else:
            routes = routes_from_local_code()
        print(f"isolation check → {base}  mode={mode}  routes={len(routes)} "
              f"(deal-scoped {sum(1 for r in routes if r.deal_scoped)})  writes={'ON' if args.allow_writes else 'off'}")
        code, results = await run_checks(
            client, firm_a, firm_b, mode=mode, routes=routes,
            options=RunOptions(allow_writes=args.allow_writes, strict=args.strict,
                               timeout_s=args.timeout, only_deal_id=args.deal_id),
        )
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump({"base_url": base, "mode": mode, "exit": code, "results": [r.as_dict() for r in results]}, fh, indent=2)
    return code


def main() -> None:
    args = build_parser().parse_args()
    sys.exit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
