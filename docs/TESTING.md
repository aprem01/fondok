# Testing — how to run the gates, and the flake log

This page is the record of every intermittent test failure we have chased to a
root cause, what the cause turned out to be, and what was changed. Add to it
whenever a test flakes: a flake that is only re-run is a flake that comes back.

## Running the gates

From the repo root:

```sh
# Worker (FastAPI + engines). Run from apps/worker; the private TMPDIR keeps
# the sqlite fixtures of this run away from every other run on the machine.
cd apps/worker && mkdir -p .tmp_fx && \
  TMPDIR=$PWD/.tmp_fx .venv/bin/python -m pytest -q -m "not live_llm and not slow" \
    -p no:cacheprovider -o pythonpath="../../packages/schemas-py ."
# afterwards, from the repo root:
git checkout evals/test-results.xml      # tests/test_observability.py regenerates it
rm -rf apps/worker/.tmp_fx

# Kimpton golden — its OWN shell invocation (see "golden then pytest" below).
# The fixture is apps/worker/tests/fixtures/kimpton_seed.py, injected through
# run_all_engines(assumptions=...); production has no Kimpton defaults and no
# demo deal id (2026-10-30).
cd apps/worker && .venv/bin/python ../../scripts/compare_golden_baseline.py
# must print "Headline fields changed: 0"

# Web
cd apps/web && npx tsc --noEmit -p . && npx vitest run --poolOptions.threads.maxThreads=3
```

Suite size on 2026-10-17: 2,544 passed / 6 skipped / 2 xfailed / 15 deselected
(`live_llm`, `slow`), about six minutes on a laptop.

### Postgres RLS integration test (opt-in)

`apps/worker/tests/test_rls_postgres.py` proves the FON-93 row-level-security
setup against a **real Postgres** as the non-owner `fondok_app` role: it runs
the startup migrations through `DATABASE_URL_MIGRATIONS` (owner), applies
`migrations/rls/001_enable_rls.sql`, seeds two tenants, and checks that with
no GUC the app role sees 0 rows, with the GUC only that tenant's rows across
`deals` / `documents` / `extraction_results` / `engine_outputs`, that a
cross-tenant write is refused, and that the digest scheduler, batch poller
and extraction watchdog each find both tenants' rows by iterating tenants.
It is skipped (9 tests, cleanly) unless `FONDOK_TEST_POSTGRES_URL` is set.

The database is **modified** (role `fondok_app` created with password
`fondok_app_test`, tables truncated) — point it at a throwaway only:

```sh
docker run -d --name fondok-rls-pg -e POSTGRES_USER=owner -e POSTGRES_PASSWORD=owner \
  -e POSTGRES_DB=fondok_rls -p 54329:5432 postgres:16
cd apps/worker && mkdir -p .tmp_fx && \
  FONDOK_TEST_POSTGRES_URL=postgresql://owner:owner@127.0.0.1:54329/fondok_rls \
  TMPDIR=$PWD/.tmp_fx .venv/bin/python -m pytest -q -p no:cacheprovider \
    -o pythonpath="../../packages/schemas-py ." tests/test_rls_postgres.py
docker rm -f fondok-rls-pg
```

Plain `postgres:16` has no pgvector, so the `pgvector.enable_extension` and
`document_chunks.*` migrations log a failure and are skipped — expected; the
test does not touch `document_chunks`. Use the `pgvector/pgvector:pg16` image
for a full schema. The same variable also enables
`tests/test_tenant_registry.py::test_factory_sessions_publish_the_guc_on_postgres`.

**Local Postgres only.** Pointed at a remote database (e.g. the Railway public
proxy, even a throwaway database on that server) the module hangs after the
first few tests — the module-scoped engine is left `idle in transaction`
across the ~60 ms round-trips and the next fixture never acquires its
connection. Seen 2026-10-17 while proving the production RLS switch; the
local-docker run is unaffected. For a remote proof, run the same checks as a
standalone script (one connection per step, explicit commits) — that is how
the production switch was verified — and keep the pytest module for the
docker loop above.

### Shell gotcha that produces "sqlite errors everywhere"

`TMPDIR=$PWD/.tmp_fx cmd1 && cmd2` applies `TMPDIR` to **cmd1 only**; `cmd2`
runs with the default temp directory. Verified:

```
$ TMPDIR=/only/first sh -c 'echo $TMPDIR' && sh -c 'echo ${TMPDIR:-<unset>}'
/only/first
/var/folders/…/T/
```

Until 2026-10-17 that mattered a lot (see the "two suites, one TMPDIR" entry):
the pytest half of such a chain shared the system temp with whatever other
agent or terminal was running the suite. `tests/conftest.py` now gives every
pytest process a private temp directory regardless of how `TMPDIR` was passed,
so the `.tmp_fx` convention is belt-and-braces rather than load-bearing. Still
write `export TMPDIR=…` or repeat the prefix on each command.

## How the worker suite is wired (what you need to know before touching env)

* **One sqlite engine per process.** `app.database.get_engine()` is a module
  singleton created on first use (the session-scoped `setup_test_database`
  fixture) from `get_settings().async_database_url`. `run_startup_migrations()`
  goes through the same `get_engine()`, so every module migrates and uses the
  same file. Which file: the one in the settings cache at that moment (below).
* **`get_settings()` is `lru_cache(maxsize=1)` and is populated during
  collection.** `app/services/extractor_throttle.py` reads
  `get_settings().EXTRACTOR_MAX_CHUNK_CONCURRENCY` at import, and the first
  test module to import `app.api.documents` triggers it. ~115 modules set
  `DATABASE_URL` / `EVALS_MOCK` / `DOCUMENT_STORAGE_ROOT` at module level; only
  the alphabetically-earlier ones have run when the cache fills. Measured with
  a collection-finish probe on 2026-10-17: cached `DATABASE_URL` →
  `fondok-tests-activity-feed.db`, `EVALS_MOCK=None`,
  `EXTRACTION_WATCHDOG_ENABLED=True`, `DOCUMENT_STORAGE_ROOT=/tmp/fondok`;
  final environment → `fondok-tests-year-mismatch.db`, `EVALS_MOCK=true`,
  watchdog disabled, a per-module storage root. Any `get_settings.cache_clear()`
  flipped every later module to the final environment.
* **Fixed-name temp files.** Modules pin
  `Path(tempfile.gettempdir()) / "fondok-tests-<module>.db"` (and
  `-storage` directories) and `unlink` them at import.
* **Per-module isolation (since 2026-10-17, `tests/conftest.py`):** an autouse
  module-scoped fixture clears the settings cache at module start and restores
  a snapshot of `os.environ` (and clears the cache again) at module end. Every
  module therefore sees the same `Settings` — the fully-collected environment —
  and in-body `os.environ` writes cannot leak past the module. The engine is
  deliberately not reset. Cost: one `Settings()` per module; the full suite ran
  in 358.9 s before the change and within noise of that after it (times below).
* **Per-process temp directory (since 2026-10-17, `tests/conftest.py`):**
  conftest is imported before any test module, so it creates
  `fondok-pytest-*/` under the caller's `TMPDIR`, exports it as `TMPDIR` for
  child processes (`evals/run.py`, `scripts/compare_golden_baseline.py`) and
  sets `tempfile.tempdir` for the process. Removed in `pytest_sessionfinish`
  unless `FONDOK_TEST_KEEP_TMP=1`.

Rules of thumb:

1. Set env for a test with `monkeypatch.setenv` **and** call
   `get_settings.cache_clear()` if a `Settings` field is involved; otherwise the
   cached object simply ignores you. Never write `os.environ[...]` in a test
   body without restoring it.
2. A new module that needs its own database copies the existing header
   (`_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-<name>.db"`, pin
   `DATABASE_URL`, then import `app`). It will not actually get its own engine
   — see above — but the migrations are idempotent, so it works.
3. Never put `asyncio.sleep(<small number>)` between a publisher and a
   subscriber to "let it attach". Use the explicit hook (memo entry below).

## Flake log

### 2026-10-17 — `test_document_download_url.py::test_signed_route_403_on_tampered_token`

*Symptom.* 403 expected, 200 served; "intermittent under load, passes alone".

*Real cause.* Not load, not ordering, not the signing key. The test built the
tampered token as `("0" if token[-1] != "0" else "1") + token[1:]` — it
inspected the **last** hex digit but replaced the **first**. Whenever the first
digit already equalled the replacement, the "tampered" token was the real token
and the route was right to serve it. Probability 1/16 per run; a 100k-token
simulation gave 6.44 %. The signing key is a process-wide `secrets.token_bytes`
generated once (`app/api/documents.py::_download_signing_key`), the TTL check
allows 50 s of slack, and the module already clears the settings cache after
each test, so none of the suspected mechanisms applied.

*Fix.* Flip the digit that was inspected (`token[:-1] + …`) and assert
`flipped != token`. Assertions unchanged.

### 2026-10-17 — `test_memo_generation.py` SSE tests (`sleep(0.05)` / `sleep(0.02)`)

*Symptom.* `event: error` / `event: done` missing; the stream waits for its
300 s timeout; shows up when the host is slow.

*Real cause.* `InProcessMemoBroadcast.publish` delivers only to queues that are
registered at that instant. The fake analyst task is spawned on `POST
/memo/generate`, and the subscriber attaches during the later `GET
/memo/stream` after a tenant check that hops to the aiosqlite thread. A nap of
20–50 ms was the only thing ordering the two.

*Fix.* `InProcessMemoBroadcast` gained a no-op hook `_subscriber_attached(deal_id)`
called right after a queue is registered (production behaviour unchanged). The
tests install a subclass that sets an `asyncio.Event` per channel and the fake
analysts `await bc.wait_for_subscriber(channel)` instead of sleeping. The
heartbeat test keeps its 0.6 s idle period — that idle time *is* the behaviour
under test — but only after the subscriber is known to be attached.

### 2026-10-17 — `test_extraction_watchdog.py::test_watchdog_start_is_a_noop_when_disabled`

*Symptom.* `start_watchdog()` returned `True` although the module sets
`EXTRACTION_WATCHDOG_ENABLED=False` at import.

*Real cause.* The settings-cache-at-collection mechanism described above: the
cached `Settings` was built before this module's `os.environ` line ran, so the
watchdog looked enabled unless an earlier module had happened to clear the
cache. The test now pins the flag and clears the cache itself; the per-module
fixture makes that the rule for the whole suite.

*Suite audit.* 29 `get_settings.cache_clear()` calls across 10 modules; 135
module-level `DATABASE_URL` writes; ~30 in-body `os.environ` writes in 13
modules, several without restore (`test_documents.py` pops `EVALS_MOCK` six
times; `test_critic.py`, `test_prompt_caching.py`, `test_tenant_registry.py`
write and restore by hand). App code also reads `os.environ` directly in 15
places (`EVALS_MOCK` in `api/documents.py`, `EXTRACTOR_USE_DYNAMIC_SCHEMAS`,
`ALLOW_TEST_INGEST`, LLM provider overrides in `llm.py`), which is why env
leaks mattered even where settings were not consulted.

### 2026-10-17 — "golden then pytest in one shell: 12 failures + 96 sqlite errors"

*What was tried.* `compare_golden_baseline.py` followed by the full suite in one
shell with one `TMPDIR` (`scratchpad/repro_chain.sh` shape): **2,544 passed,
358.9 s** — the chain does not reproduce. Environment leakage is impossible
here: the script is a child process, and `os.environ` only flows downward.

*What does reproduce the error shape.* Two pytest processes sharing one
`TMPDIR` — which is exactly what the prefix-assignment gotcha above produces
when another suite is running anywhere on the machine (other agents, another
terminal). Each process unlinks and re-creates the same fixed-name sqlite files
at import and then writes to them concurrently. Measured with four modules per
process, started 4 s apart:

| | process A | process B |
|---|---|---|
| before | 1 failed, 17 passed, **19 errors**; 5,910 `sqlite3` lines ("database disk image is malformed", "disk I/O error") | 1 failed, 36 passed |
| after (private per-process TMPDIR) | 37 passed | 37 passed |

*Fixes.* (1) `tests/conftest.py` private per-process temp directory, above.
(2) `scripts/compare_golden_baseline.py` is now side-effect free for anything
that follows: it uses a `TemporaryDirectory` that it removes on exit (before,
every run — including the one `tests/test_run_compare.py` spawns inside the
suite — left a `fondok-golden-*` directory in `TMPDIR`), disposes the aiosqlite
engine before the directory goes, and refuses to run (exit 2) if the engine is
bound to anything other than its throwaway file, so it can never touch
`apps/worker/fondok.db`. The script's header documents the contract.

### Known load flakes still being watched

Reported by the gate as "rerun alone and say so": `test_document_download_url`
tampered-token (now explained above), a `test_memo_generation` SSE test (now
explained above), `test_extraction_watchdog` disabled-start (now explained
above). If any of these appears again after 2026-10-17 it is a new cause —
record it here rather than re-running.

## Stability evidence for the 2026-10-17 changes

Two back-to-back full worker runs after the changes, each with the gate
command: see the commit message for the counts and wall times. The golden
comparison run in its own shell reported `Headline fields changed: 0`.
