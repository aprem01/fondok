-- FON-93 — Postgres row-level security for every tenant-scoped table.
--
-- NOT applied at worker startup. Apply by hand, in a transaction, after
-- reading docs/WORKSPACE_ISOLATION.md ("Rollout order"). Idempotent: every
-- statement is guarded, so re-running it is a no-op.
--
-- Contract
-- --------
-- * The worker publishes the caller's tenant with
--   ``SELECT set_config('app.tenant_id', '<uuid>', true)`` at the start of
--   each transaction (apps/worker/app/database.py, behind the
--   DB_SET_TENANT_GUC setting). ``true`` = transaction-local, so the value
--   can never leak to the next request on a pooled connection.
-- * Every policy is
--   ``USING tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid``
--   for reads/updates/deletes and the same expression as ``WITH CHECK`` for
--   writes. ``current_setting(name, true)`` returns NULL only while the
--   placeholder has NEVER been set on the session; once any transaction on
--   a pooled connection has run ``set_config(..., true)`` the placeholder
--   exists and every later transaction that publishes nothing reads the
--   EMPTY STRING instead — and ``''::uuid`` raises ``invalid input syntax
--   for type uuid`` (verified on Postgres 16, 2026-10-15). ``NULLIF`` folds
--   that back to NULL; NULL = anything is NULL, which Postgres treats as
--   false — so a connection that published no tenant sees ZERO rows and can
--   write none, instead of 500ing. Fail closed, not fail loud.
-- * FORCE ROW LEVEL SECURITY makes the policy apply to the table OWNER too.
--   Even so, the worker must connect as a NON-owner role (``fondok_app``)
--   that has no BYPASSRLS — a superuser or BYPASSRLS role ignores RLS
--   regardless of FORCE. The owner role stays for migrations only.
-- * ``model_calls.tenant_id`` is nullable (system jobs). It is NOT
--   covered here; the application scopes it (api/observability.py,
--   api/admin_cost.py). Covering it would require a decision on NULL rows.
-- * ``tenants`` (the registry) is read by the auth layer BEFORE a tenant
--   is known, so it is not RLS-protected; it holds no customer data.
--
-- Background jobs: the digest scheduler, the batch poller and the
-- extraction watchdog have no request context. With DB_SET_TENANT_GUC on
-- they iterate tenants (the registry, else DISTINCT tenant_id read via
-- DATABASE_URL_MIGRATIONS) and publish each tenant's GUC before its scan
-- — apps/worker/app/services/tenant_scan.py; verified against a real
-- Postgres by tests/test_rls_postgres.py (FONDOK_TEST_POSTGRES_URL).
-- Startup DDL runs through DATABASE_URL_MIGRATIONS (the owner) when set.

BEGIN;

-- The app role the worker will connect as. Created only if missing; the
-- password is set out-of-band (``ALTER ROLE fondok_app PASSWORD '…'``).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fondok_app') THEN
        CREATE ROLE fondok_app LOGIN NOBYPASSRLS;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO fondok_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO fondok_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO fondok_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO fondok_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO fondok_app;

-- One policy per tenant-scoped table. ``current_setting(.., true)`` =
-- missing_ok, so an unset GUC yields NULL (→ no rows) instead of an error.
DO $$
DECLARE
    t text;
    tenant_tables text[] := ARRAY[
        'deals',
        'documents',
        'template_mappings',
        'extraction_results',
        'document_chunks',
        'audit_log',
        'memo_edits',
        'verification_reports',
        'critic_findings',
        'critic_reports',
        'engine_outputs',
        'due_diligence_questions',
        'broker_questions',
        'broker_qa_pairs',
        'scenarios',
        'portfolio_library',
        'saved_pipeline_views',
        'pipeline_digest_schedules',
        'pending_batches',
        'lineage_records',
        'cell_comments'
    ];
BEGIN
    FOREACH t IN ARRAY tenant_tables LOOP
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.tables
             WHERE table_schema = 'public' AND table_name = t
        ) THEN
            RAISE NOTICE 'rls: table % does not exist yet — skipped', t;
            CONTINUE;
        END IF;

        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);

        -- Drop-and-recreate (not IF NOT EXISTS) so re-running the file
        -- after a policy-expression change upgrades the policy in place;
        -- the whole file runs in one transaction, so there is no window
        -- with the table unprotected.
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON public.%I', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON public.%I '
            'FOR ALL '
            'USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid) '
            'WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)',
            t
        );
    END LOOP;
END
$$;

COMMIT;

-- Verification (run as fondok_app, all in ONE session):
--   SELECT count(*) FROM deals;                                  -- 0 (no GUC)
--   BEGIN; SELECT set_config('app.tenant_id', '<tenant uuid>', true);
--   SELECT count(*) FROM deals;                                  -- that tenant's deals
--   COMMIT;
--   SELECT current_setting('app.tenant_id', true);               -- '' (placeholder persists)
--   SELECT count(*) FROM deals;                                  -- 0 again — NOT an error
--
-- Rollback (owner role):
--   ALTER TABLE <t> NO FORCE ROW LEVEL SECURITY;
--   ALTER TABLE <t> DISABLE ROW LEVEL SECURITY;
--   DROP POLICY IF EXISTS tenant_isolation ON <t>;
