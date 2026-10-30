'use client';
/**
 * Methodology slots (FON-120) — the blocks the markdown cannot carry as
 * prose because the facts live in code. Keyed by the same names the worker's
 * `app/methodology/slots.py` renders to text for Atlas, so the page and the
 * copilot show the same facts from the same generated sources:
 *
 *   reason-code-table              §7 client table — label + client explanation, code hidden
 *   reason-code-table-engineering  §7 engineering table — code, label, explanation, glyph
 *   concept-registry-table         §8 (engineering) — the generated concept registry
 *   mgmt-fee-guidance              §2 — directional guidance list by operating model
 *   mgmt-fee-disclaimer (inline)   §2 — the disclaimer sentence
 *   registry-version / registry-count (inline) — §8 intro figures
 *
 * `methodologyLevels.test.ts` asserts every slot token in the package has a
 * renderer here; the worker's loader refuses a slot name neither side knows.
 */
import React from 'react';
import { REASONS, REASON_CODES, type ReasonCode } from '@/lib/ontology/reasons.generated';
import { CONCEPTS, CONCEPT_IDS, REGISTRY_VERSION } from '@/lib/ontology/concepts.generated';
import { clientReasonExplanation } from '@/lib/reasonCopy';
import {
  MGMT_FEE_GUIDANCE_BY_MODEL,
  MGMT_FEE_GUIDANCE_DISCLAIMER,
  OPERATING_MODEL_LABELS,
  OPERATING_MODEL_ORDER,
} from '@/lib/methodology/managementFeeGuidance.generated';

// Section 8 — rows come from the GENERATED registry module, never literals.
// Critic cross-field checks (group "synthetic") are computed inputs with no
// document line, so they are left off the reference table.
const REGISTRY_ROWS = CONCEPT_IDS.map((id) => CONCEPTS[id]).filter((c) => c.group !== 'synthetic');

/**
 * Name the reason code beside a refusal sentence (engineering level only).
 * The label and the hover explanation come from the generated module, never
 * from a literal here, so a rename in the vocabulary reaches the page.
 */
export function ReasonTag({ code }: { code: string }) {
  const meta = REASONS[code as ReasonCode];
  if (!meta) return <span data-unknown-reason={code}>{code}</span>;
  return (
    <span
      data-reason-tag={code}
      title={meta.explanation}
      className="inline-flex items-baseline gap-1 whitespace-nowrap align-baseline"
    >
      <code className="text-[11.5px] text-ink-700">{code}</code>
      <span className="text-[11px] text-ink-500">({meta.label})</span>
    </span>
  );
}

function ReasonCodeTable() {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12.5px]" data-testid="reason-code-table">
        <thead>
          <tr className="text-ink-500 text-[10.5px] border-b border-border bg-ink-300/5">
            <th className="text-left font-medium px-5 py-2">Shown</th>
            <th className="text-left font-medium px-3 py-2">Meaning</th>
            <th className="text-left font-medium px-3 py-2">What it tells you</th>
          </tr>
        </thead>
        <tbody>
          {REASON_CODES.map((code: ReasonCode) => {
            const meta = REASONS[code];
            return (
              <tr key={code} data-reason-code={code} className="border-b border-border last:border-b-0 align-top">
                <td className="px-5 py-2.5 text-ink-900 tabular-nums">{meta.ui}</td>
                <td className="px-3 py-2.5 font-medium text-ink-900">{meta.label}</td>
                <td className="px-3 py-2.5 text-ink-500 leading-relaxed">{clientReasonExplanation(code)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ReasonCodeTableEngineering() {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12.5px]" data-testid="reason-code-table">
        <thead>
          <tr className="text-ink-500 text-[10.5px] border-b border-border bg-ink-300/5">
            <th className="text-left font-medium px-5 py-2">Shown</th>
            <th className="text-left font-medium px-3 py-2">Code</th>
            <th className="text-left font-medium px-3 py-2">Meaning</th>
            <th className="text-left font-medium px-3 py-2">What it tells you</th>
          </tr>
        </thead>
        <tbody>
          {(Object.keys(REASONS) as ReasonCode[]).map((code) => {
            const meta = REASONS[code];
            return (
              <tr key={code} data-reason-code={code} className="border-b border-border last:border-b-0 align-top">
                <td className="px-5 py-2.5 text-ink-900 tabular-nums">{meta.ui}</td>
                <td className="px-3 py-2.5">
                  <code className="text-[11.5px] text-ink-700">{code}</code>
                </td>
                <td className="px-3 py-2.5 font-medium text-ink-900">{meta.label}</td>
                <td className="px-3 py-2.5 text-ink-500 leading-relaxed">{meta.explanation}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ConceptRegistryTable() {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px]">
        <thead>
          <tr className="text-ink-500 text-[11px] uppercase tracking-wide">
            <th className="text-left font-medium px-2 py-1.5">Concept</th>
            <th className="text-left font-medium px-2 py-1.5">USALI line</th>
            <th className="text-left font-medium px-2 py-1.5">Unit</th>
            <th className="text-left font-medium px-2 py-1.5">Period</th>
            <th className="text-left font-medium px-2 py-1.5">Engines</th>
          </tr>
        </thead>
        <tbody>
          {REGISTRY_ROWS.map((c) => (
            <tr key={c.id} className="border-t border-border/50 align-top">
              <td className="px-2 py-1.5 whitespace-nowrap">
                <span className="font-medium text-ink-900">{c.label}</span>{' '}
                <code className="text-[11px] text-ink-500">{c.id}</code>
              </td>
              <td className="px-2 py-1.5 text-ink-600 whitespace-nowrap">{c.usali?.line ?? '—'}</td>
              <td className="px-2 py-1.5 text-ink-600">{c.unit}</td>
              <td className="px-2 py-1.5 text-ink-600">{c.period}</td>
              <td className="px-2 py-1.5 text-ink-600">{c.engines.length ? c.engines.join(', ') : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MgmtFeeGuidance() {
  return (
    <ul className="list-disc pl-5 space-y-1 text-[12px] text-ink-700 leading-relaxed">
      {OPERATING_MODEL_ORDER.map((id) => (
        <li key={id}>
          <span className="font-medium text-ink-900">{OPERATING_MODEL_LABELS[id]}.</span>{' '}
          {MGMT_FEE_GUIDANCE_BY_MODEL[id]}
        </li>
      ))}
    </ul>
  );
}

export const BLOCK_SLOTS: Readonly<Record<string, () => React.ReactElement>> = {
  'reason-code-table': ReasonCodeTable,
  'reason-code-table-engineering': ReasonCodeTableEngineering,
  'concept-registry-table': ConceptRegistryTable,
  'mgmt-fee-guidance': MgmtFeeGuidance,
};

export const INLINE_SLOTS: Readonly<Record<string, () => string>> = {
  'mgmt-fee-disclaimer': () => MGMT_FEE_GUIDANCE_DISCLAIMER,
  'registry-version': () => String(REGISTRY_VERSION),
  'registry-count': () => String(REGISTRY_ROWS.length),
};

export const SLOTS = { block: BLOCK_SLOTS, inline: INLINE_SLOTS } as const;
