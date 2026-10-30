'use client';

// Hidden diagnostic page for production debugging.
// Gated behind NEXT_PUBLIC_SHOW_DIAG === 'true' — when the flag is unset
// (default / production) the page renders a 404 so prospects never see it.
// When enabled, /diag shows worker URL, /health response, /deals count,
// last model calls, and runs a one-shot smoke test (deal → upload → extract).
// Not linked from the main UI — reachable by URL only.

import { notFound } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { api, isWorkerConnected, workerUrl, type AgentCostsResponse } from '@/lib/api';

const DIAG_ENABLED = process.env.NEXT_PUBLIC_SHOW_DIAG === 'true';

/** Body of this app's own GET /api/version (src/app/api/version/route.ts). */
interface WebVersion {
  sha: string | null;
  env: string | null;
}

interface Diag {
  workerUrl: string;
  connected: boolean;
  health: {
    status?: string;
    version?: string;
    db?: string;
    db_migrations_dsn?: 'owner' | 'same';
    build_sha?: string | null;
    build_time?: string | null;
  } | null;
  healthError: string | null;
  dealsCount: number | null;
  dealsError: string | null;
  buildTime: string;
  loading: boolean;
}

// What the "Recent model calls" section shows on a 401 (a page file may only
// export the page itself, so this stays module-local).
const DIAG_COSTS_SIGN_IN_MESSAGE =
  'sign in to see this — /observability/agent-costs needs a signed-in session (HTTP 401) and shows your workspace’s calls only';

type SmokeStep = 'idle' | 'creating' | 'uploading' | 'extracting' | 'polling' | 'done' | 'error';

interface SmokeState {
  step: SmokeStep;
  dealId?: string;
  docId?: string;
  status?: string;
  error?: string;
  startedAt?: number;
  finishedAt?: number;
}

export default function DiagPage() {
  if (!DIAG_ENABLED) {
    notFound();
  }
  return <DiagPageInner />;
}

function DiagPageInner() {
  const [diag, setDiag] = useState<Diag>({
    workerUrl: workerUrl(),
    connected: isWorkerConnected(),
    health: null,
    healthError: null,
    dealsCount: null,
    dealsError: null,
    buildTime: new Date().toISOString(),
    loading: true,
  });
  const [webVersion, setWebVersion] = useState<WebVersion | null>(null);
  const [webVersionError, setWebVersionError] = useState<string | null>(null);
  const [costs, setCosts] = useState<AgentCostsResponse | null>(null);
  const [costsError, setCostsError] = useState<string | null>(null);
  const [smoke, setSmoke] = useState<SmokeState>({ step: 'idle' });
  const cancelRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    async function run() {
      let health: Diag['health'] = null;
      let healthError: string | null = null;
      let dealsCount: number | null = null;
      let dealsError: string | null = null;
      try {
        health = await api.health();
      } catch (e) {
        healthError = e instanceof Error ? e.message : String(e);
      }
      try {
        const rows = await api.deals.list();
        dealsCount = rows.length;
      } catch (e) {
        dealsError = e instanceof Error ? e.message : String(e);
      }
      if (cancelled) return;
      setDiag((d) => ({ ...d, health, healthError, dealsCount, dealsError, loading: false }));

      // Best-effort read of recent agent calls, through the AUTHED client —
      // the endpoint needs a signed-in session and scopes to the caller's
      // tenant, so a raw fetch 401s in production (review finding). A 401
      // is shown as "sign in to see this", never a bare HTTP code.
      try {
        const json = await api.observability.agentCosts(1);
        if (!cancelled) setCosts(json);
      } catch (e) {
        if (cancelled) return;
        const status = (e as { status?: unknown } | null)?.status;
        if (status === 401) setCostsError(DIAG_COSTS_SIGN_IN_MESSAGE);
        else setCostsError(`observability endpoint not reachable: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
    // Same-origin: which web bundle is serving (VERCEL_GIT_COMMIT_SHA / VERCEL_ENV
    // read at request time by the route handler). Independent of the worker.
    async function readWebVersion() {
      try {
        const res = await fetch('/api/version', { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = (await res.json()) as WebVersion;
        if (!cancelled) setWebVersion({ sha: json.sha ?? null, env: json.env ?? null });
      } catch (e) {
        if (!cancelled) setWebVersionError(e instanceof Error ? e.message : String(e));
      }
    }
    void readWebVersion();
    if (isWorkerConnected()) {
      void run();
    } else {
      setDiag((d) => ({ ...d, loading: false }));
    }
    return () => {
      cancelled = true;
    };
  }, []);

  async function runSmokeTest() {
    if (smoke.step !== 'idle' && smoke.step !== 'done' && smoke.step !== 'error') return;
    cancelRef.current = false;
    const startedAt = Date.now();
    setSmoke({ step: 'creating', startedAt });

    try {
      const deal = await api.deals.create({
        name: `Smoke Test ${new Date().toISOString().slice(11, 19)}`,
        city: 'Miami, FL',
        keys: 132,
        service: 'Full-Service',
      });
      if (cancelRef.current) return;
      setSmoke((s) => ({ ...s, step: 'uploading', dealId: deal.id }));

      // Generate a tiny PDF-ish blob (one-page placeholder). The worker only
      // needs *something* uploaded — content doesn't have to be a real PDF for
      // the upload + status-poll path to register. The worker's parser will
      // mark it FAILED if invalid, which is also useful diagnostic signal.
      const placeholder = new File(
        [new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x34])],
        'smoke.pdf',
        { type: 'application/pdf' },
      );
      const docs = await api.documents.upload(deal.id, [placeholder]);
      if (cancelRef.current) return;
      const doc = docs[0];
      setSmoke((s) => ({ ...s, step: 'extracting', docId: doc.id }));

      await api.documents.extract(deal.id, doc.id);
      if (cancelRef.current) return;
      setSmoke((s) => ({ ...s, step: 'polling' }));

      // Poll status up to ~60s
      let final = 'UNKNOWN';
      for (let i = 0; i < 20; i += 1) {
        if (cancelRef.current) return;
        await new Promise((r) => setTimeout(r, 3000));
        try {
          const list = await api.documents.list(deal.id);
          const d = list[0];
          if (d) {
            final = d.status;
            setSmoke((s) => ({ ...s, status: d.status }));
            if (d.status === 'EXTRACTED' || d.status === 'FAILED') break;
          }
        } catch {
          // keep polling
        }
      }
      setSmoke((s) => ({ ...s, step: 'done', status: final, finishedAt: Date.now() }));
    } catch (e) {
      setSmoke((s) => ({
        ...s,
        step: 'error',
        error: e instanceof Error ? e.message : String(e),
        finishedAt: Date.now(),
      }));
    }
  }

  function resetSmoke() {
    cancelRef.current = true;
    setSmoke({ step: 'idle' });
  }

  const smokeBusy = ['creating', 'uploading', 'extracting', 'polling'].includes(smoke.step);
  const smokeElapsed =
    smoke.startedAt && (smoke.finishedAt ?? Date.now()) > smoke.startedAt
      ? ((smoke.finishedAt ?? Date.now()) - smoke.startedAt) / 1000
      : 0;

  return (
    <main style={{ fontFamily: 'monospace', padding: 24, lineHeight: 1.6 }}>
      <h1 style={{ fontSize: 18, marginBottom: 16 }}>Fondok web · diagnostics</h1>

      <Section title="Environment">
        <Kv k="NEXT_PUBLIC_WORKER_URL" v={diag.workerUrl || '(unset)'} />
        <Kv k="isWorkerConnected" v={String(diag.connected)} />
        <Kv k="page rendered at" v={diag.buildTime} />
      </Section>

      <Section title="Web build (GET /api/version)">
        {webVersionError ? (
          <pre style={{ color: '#b00' }} data-testid="diag-web-version-error">{webVersionError}</pre>
        ) : !webVersion ? (
          <div>loading…</div>
        ) : (
          <>
            <Kv k="sha" v={webVersion.sha ?? '—'} />
            <Kv k="env" v={webVersion.env ?? '—'} />
          </>
        )}
      </Section>

      <Section title="Worker /health">
        {diag.loading ? <div>loading…</div> : diag.healthError ? (
          <pre style={{ color: '#b00' }}>{diag.healthError}</pre>
        ) : (
          <>
            <Kv k="status" v={String(diag.health?.status ?? '?')} />
            <Kv k="version" v={String(diag.health?.version ?? '?')} />
            <Kv k="db" v={String(diag.health?.db ?? '?')} />
            <Kv k="db_migrations_dsn" v={String(diag.health?.db_migrations_dsn ?? '?')} />
            <Kv k="build_sha" v={diag.health?.build_sha ?? '—'} />
            <Kv k="build_time" v={diag.health?.build_time ?? '—'} />
          </>
        )}
      </Section>

      <Section title="Worker /deals">
        {diag.loading ? <div>loading…</div> : diag.dealsError ? (
          <pre style={{ color: '#b00' }}>{diag.dealsError}</pre>
        ) : (
          <Kv k="count" v={String(diag.dealsCount)} />
        )}
      </Section>

      <Section title="Recent model calls (last 24h)">
        {costsError ? (
          <div style={{ color: '#999' }} data-testid="diag-costs-error">{costsError}</div>
        ) : !costs ? (
          <div>loading…</div>
        ) : (
          <>
            <Kv k="samples" v={String(costs.samples ?? 0)} />
            <Kv k="total_cost_usd" v={String(costs.total_cost_usd ?? 0)} />
            <Kv k="window" v={String(costs.window ?? '?')} />
            {costs.by_agent && costs.by_agent.length > 0 && (
              <table style={{ marginTop: 8, fontSize: 11, borderCollapse: 'collapse', width: '100%', maxWidth: 720 }}>
                <thead>
                  <tr style={{ textAlign: 'left', color: '#666' }}>
                    <th style={th}>agent</th>
                    <th style={th}>model</th>
                    <th style={th}>cost</th>
                    <th style={th}>in</th>
                    <th style={th}>out</th>
                    <th style={th}>cache</th>
                  </tr>
                </thead>
                <tbody>
                  {costs.by_agent.slice(0, 10).map((c, i) => (
                    <tr key={i} style={{ borderTop: '1px solid #eee' }}>
                      <td style={td}>{String(c.agent_id ?? c.agent_name ?? '?')}</td>
                      <td style={td}>{String(c.model ?? '?')}</td>
                      <td style={td}>${Number(c.total_cost_usd ?? 0).toFixed(4)}</td>
                      <td style={td}>{c.input_tokens ?? 0}</td>
                      <td style={td}>{c.output_tokens ?? 0}</td>
                      <td style={td}>{c.cache_read_tokens ?? 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </Section>

      <Section title="Smoke test">
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12 }}>
          <button
            onClick={runSmokeTest}
            disabled={!diag.connected || smokeBusy}
            style={btn(!diag.connected || smokeBusy)}
          >
            {smokeBusy ? 'Running…' : 'Run smoke test'}
          </button>
          {(smoke.step === 'done' || smoke.step === 'error') && (
            <button onClick={resetSmoke} style={btn(false)}>Reset</button>
          )}
          {smokeElapsed > 0 && (
            <span style={{ fontSize: 11, color: '#666' }}>
              elapsed: {smokeElapsed.toFixed(1)}s
            </span>
          )}
        </div>
        {smoke.step !== 'idle' && (
          <div style={{ fontSize: 12 }}>
            <Kv k="step" v={smoke.step} />
            {smoke.dealId && <Kv k="deal_id" v={smoke.dealId} />}
            {smoke.docId && <Kv k="doc_id" v={smoke.docId} />}
            {smoke.status && <Kv k="doc status" v={smoke.status} />}
            {smoke.error && (
              <div style={{ color: '#b00', marginTop: 6 }}>error: {smoke.error}</div>
            )}
          </div>
        )}
        {smoke.step === 'idle' && (
          <div style={{ fontSize: 11, color: '#999' }}>
            Creates a fresh deal, uploads a placeholder PDF, triggers extraction, and polls until
            EXTRACTED or FAILED (max ~60s). Costs ~$0.10 if a real Anthropic call fires.
          </div>
        )}
      </Section>

      <p style={{ color: '#999', fontSize: 11, marginTop: 24 }}>
        This page is not linked from the main UI. Use it to verify worker reachability from the
        browser, observe recent model calls, and run a one-shot end-to-end smoke test.
      </p>
    </main>
  );
}

const th: React.CSSProperties = { padding: '4px 8px', fontWeight: 600 };
const td: React.CSSProperties = { padding: '4px 8px', fontFamily: 'monospace' };
const btn = (disabled: boolean): React.CSSProperties => ({
  fontFamily: 'inherit',
  fontSize: 12,
  padding: '6px 12px',
  borderRadius: 4,
  border: '1px solid ' + (disabled ? '#ddd' : '#3b82f6'),
  background: disabled ? '#f5f5f5' : '#3b82f6',
  color: disabled ? '#999' : '#fff',
  cursor: disabled ? 'not-allowed' : 'pointer',
});

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginBottom: 24 }}>
      <h2 style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, borderBottom: '1px solid #ddd', paddingBottom: 4 }}>{title}</h2>
      {children}
    </section>
  );
}

function Kv({ k, v }: { k: string; v: string }) {
  return (
    <div style={{ display: 'flex', gap: 12, fontSize: 12 }}>
      <div style={{ color: '#666', minWidth: 200 }}>{k}</div>
      <div style={{ wordBreak: 'break-all' }}>{v}</div>
    </div>
  );
}
