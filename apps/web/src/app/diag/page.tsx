'use client';

// Hidden diagnostic page for production debugging.
// Visit /_test to see worker URL, /health response, /deals count, build SHA.
// Not linked from anywhere; reachable by URL only.

import { useEffect, useState } from 'react';
import { api, isWorkerConnected, workerUrl } from '@/lib/api';

interface Diag {
  workerUrl: string;
  connected: boolean;
  health: unknown;
  healthError: string | null;
  dealsCount: number | null;
  dealsError: string | null;
  buildTime: string;
  loading: boolean;
}

export default function TestPage() {
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

  useEffect(() => {
    let cancelled = false;
    async function run() {
      let health: unknown = null;
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
    }
    if (isWorkerConnected()) {
      void run();
    } else {
      setDiag((d) => ({ ...d, loading: false }));
    }
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main style={{ fontFamily: 'monospace', padding: 24, lineHeight: 1.6 }}>
      <h1 style={{ fontSize: 18, marginBottom: 16 }}>Fondok web · diagnostics</h1>

      <Section title="Environment">
        <Kv k="NEXT_PUBLIC_WORKER_URL" v={diag.workerUrl || '(unset)'} />
        <Kv k="isWorkerConnected" v={String(diag.connected)} />
        <Kv k="page rendered at" v={diag.buildTime} />
      </Section>

      <Section title="Worker /health">
        {diag.loading ? <div>loading…</div> : (
          diag.healthError
            ? <pre style={{ color: '#b00' }}>{diag.healthError}</pre>
            : <pre>{JSON.stringify(diag.health, null, 2)}</pre>
        )}
      </Section>

      <Section title="Worker /deals">
        {diag.loading ? <div>loading…</div> : (
          diag.dealsError
            ? <pre style={{ color: '#b00' }}>{diag.dealsError}</pre>
            : <Kv k="count" v={String(diag.dealsCount)} />
        )}
      </Section>

      <p style={{ color: '#999', fontSize: 11, marginTop: 24 }}>
        This page is not linked from the main UI. Use it to verify worker reachability from the browser.
      </p>
    </main>
  );
}

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
      <div>{v}</div>
    </div>
  );
}
