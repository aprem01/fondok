'use client';

/**
 * Document detail — a full-screen view of one document's extracted data.
 *
 * Sam QA 8/21: non-financial documents' "View data" opens this screen instead
 * of the pop-out drawer, for a cleaner review surface. (Financial statements
 * jump to the Financials tab; this page covers everything else — OM, STR,
 * insurance, property tax, room mix, etc.)
 */

import { useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ArrowLeft, Search, FileText, AlertTriangle, Loader2 } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { cn, formatValue } from '@/lib/format';
import { useDocuments } from '@/lib/hooks/useDocuments';
import { humanizeFieldName } from '@/lib/fieldLabels';

const DOC_TYPE_LABEL: Record<string, string> = {
  OM: 'Offering Memorandum', T12: 'T-12', PNL: 'P&L', PNL_MONTHLY: 'Monthly P&L',
  PNL_YTD: 'YTD P&L', PNL_BENCHMARK: 'P&L Benchmark', STR: 'STR Report',
  STR_TREND: 'STR / CoStar Trend', CBRE_HORIZONS: 'CBRE Horizons', INSURANCE: 'Insurance',
  PROPERTY_TAX: 'Property Taxes', ROOM_MIX: 'Room Mix', CAPEX: 'Historical CapEx',
  PROPERTY_INFO: 'Property Info', LEASES: 'Leases & Agreements', SURVEYS: 'Surveys & Reviews',
};

export default function DocumentDetailPage() {
  const params = useParams();
  const router = useRouter();
  const dealId = (params?.id as string) ?? '';
  const docId = (params?.docId as string) ?? '';
  const { documents, extractions, loading } = useDocuments(dealId);
  const [q, setQ] = useState('');

  const doc = documents.find((d) => d.id === docId);
  const ext = extractions[docId];
  const fields = ext?.fields ?? [];

  // Only fields that carry a value are shown; counts follow the visible set.
  const displayable = useMemo(
    () => fields.filter((f) => f.value !== null && f.value !== undefined && f.value !== ''),
    [fields],
  );
  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    if (!query) return displayable;
    return displayable.filter(
      (f) =>
        humanizeFieldName(f.field_name).toLowerCase().includes(query) ||
        f.field_name.toLowerCase().includes(query),
    );
  }, [displayable, q]);

  const lowCount = displayable.filter((f) => (f.confidence ?? 1) < 0.85 && !f.reviewed).length;
  const docType = (doc?.doc_type ?? '').toUpperCase();
  const stillLoading = loading && !doc;

  return (
    <div className="p-8 max-w-[1100px] w-full">
      <button
        type="button"
        onClick={() => router.push(`/projects/${dealId}`, { scroll: false })}
        className="inline-flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-ink-500 hover:text-ink-900 mb-3"
      >
        <ArrowLeft size={13} /> Back to Data Room
      </button>

      <div className="flex items-start justify-between gap-4 mb-5">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <FileText size={18} className="text-ink-400 shrink-0" />
            <h1 className="text-[20px] font-semibold text-ink-900 truncate">{doc?.filename ?? 'Document'}</h1>
          </div>
          <div className="flex flex-wrap items-center gap-2 mt-1.5 text-[12px] text-ink-500">
            {docType && (
              <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-ink-100 text-ink-600">
                {DOC_TYPE_LABEL[docType] ?? docType}
              </span>
            )}
            {doc?.status && <span>{doc.status === 'EXTRACTED' ? 'Extracted' : doc.status}</span>}
            {displayable.length > 0 && <span>· {displayable.length} fields</span>}
            {lowCount > 0 && (
              <span className="inline-flex items-center gap-1 text-danger-700">
                <AlertTriangle size={11} /> {lowCount} to review
              </span>
            )}
          </div>
        </div>
        <div className="relative shrink-0">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-400" aria-hidden="true" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search fields…"
            className="w-56 pl-8 pr-2 py-1.5 text-[12.5px] rounded-md border border-border focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
          />
        </div>
      </div>

      {stillLoading ? (
        <Card className="p-12 text-center text-[13px] text-ink-500">
          <Loader2 size={18} className="animate-spin mx-auto mb-2 text-ink-400" /> Loading document…
        </Card>
      ) : !doc ? (
        <Card className="p-12 text-center text-[13px] text-ink-500">Document not found on this deal.</Card>
      ) : displayable.length === 0 ? (
        <Card className="p-12 text-center text-[13px] text-ink-500">
          No extracted data yet{doc.status && doc.status !== 'EXTRACTED' ? ` — status: ${doc.status}.` : '.'}
        </Card>
      ) : (
        <Card className="p-0 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="bg-ink-900 text-white text-[10px] uppercase tracking-wider">
                  <th className="text-left font-semibold px-5 py-2.5">Field</th>
                  <th className="text-right font-semibold px-5 py-2.5">Value</th>
                  <th className="text-right font-semibold px-5 py-2.5 w-28">Confidence</th>
                  <th className="text-right font-semibold px-5 py-2.5 w-16">Page</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-5 py-8 text-center text-[12.5px] text-ink-500">
                      No fields match “{q}”.
                    </td>
                  </tr>
                ) : (
                  filtered.map((f, i) => {
                    const conf = f.confidence ?? null;
                    const low = conf != null && conf < 0.85 && !f.reviewed;
                    return (
                      <tr key={`${f.field_name}-${i}`} className="border-t border-border hover:bg-ink-100/30">
                        <td className="px-5 py-2 text-ink-800">
                          {humanizeFieldName(f.field_name)}
                          <span className="block text-[10px] text-ink-400 font-mono">{f.field_name}</span>
                        </td>
                        <td className={cn('px-5 py-2 text-right tabular-nums', low ? 'text-danger-700 font-medium' : 'text-ink-900')}>
                          {formatValue(f.value, f.unit, f.field_name)}
                        </td>
                        <td className="px-5 py-2 text-right tabular-nums">
                          {conf != null ? (
                            <span className={conf >= 0.85 ? 'text-success-700' : 'text-danger-700'}>{Math.round(conf * 100)}%</span>
                          ) : (
                            '—'
                          )}
                        </td>
                        <td className="px-5 py-2 text-right tabular-nums text-ink-500">{f.source_page ?? '—'}</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
