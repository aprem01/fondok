'use client';
/**
 * Settings → Workspace → "Data policies" (FON-116 / FON-96).
 *
 * An operator surface for INTERNAL admins only: every registered workspace
 * with its CoStar licensing policy and a switch that calls
 * ``PATCH /admin/tenants/{id}/data_policies``. The card hides itself for
 * everyone else and on a worker that predates the route — it feature-detects
 * from the registry list (``scope === 'all'`` and ``data_policies`` present
 * on every row), so an older worker or an external admin never sees a
 * control that would 403.
 *
 * Copy never says CoStar IS licensed: the two states are "offered" and
 * "withheld (licence pending)".
 */
import { useEffect, useState } from 'react';
import { Loader2, ShieldCheck } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { useToast } from '@/components/ui/Toast';
import { cn } from '@/lib/format';
import {
  api,
  isWorkerConnected,
  userMessage,
  type DataPolicyChoice,
  type RegisteredTenant,
} from '@/lib/api';
import { useCurrentRole } from '@/lib/auth';

export const COSTAR_POLICY_LABEL: Record<DataPolicyChoice, string> = {
  allow: 'offered',
  withhold: 'withheld (licence pending)',
};

export default function DataPoliciesCard() {
  const role = useCurrentRole();
  const { toast } = useToast();
  const eligible = role === 'org:admin' && isWorkerConnected();
  const [rows, setRows] = useState<RegisteredTenant[] | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    if (!eligible) {
      setRows(null);
      return;
    }
    let cancelled = false;
    api.admin.tenants
      .list()
      .then((res) => {
        if (cancelled) return;
        // Feature detection: only an internal tenant's admin is served every
        // row, and only a worker with FON-116 puts ``data_policies`` on them.
        const supported = res.scope === 'all' && res.tenants.every((t) => t.data_policies != null);
        setRows(supported ? res.tenants : null);
      })
      .catch(() => {
        // 403 (not internal), 404 (older worker) or a network failure: no card.
        if (!cancelled) setRows(null);
      });
    return () => {
      cancelled = true;
    };
  }, [eligible]);

  if (!eligible || !rows || rows.length === 0) return null;

  const onToggle = async (tenant: RegisteredTenant, next: DataPolicyChoice) => {
    setBusyId(tenant.id);
    try {
      const res = await api.admin.tenants.setDataPolicies(tenant.id, { costar_derived_defaults: next });
      setRows((prev) =>
        prev ? prev.map((r) => (r.id === tenant.id ? { ...r, data_policies: res.data_policies } : r)) : prev,
      );
      toast(`${tenant.name}: CoStar-derived defaults ${COSTAR_POLICY_LABEL[next]}`, { type: 'success' });
    } catch (err) {
      toast(`Couldn't change the data policy for ${tenant.name} — ${userMessage(err)}`, { type: 'error' });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Card className="p-5" data-testid="settings-data-policies">
      <div className="flex items-center gap-2 mb-1">
        <ShieldCheck size={14} className="text-brand-500" aria-hidden="true" />
        <h3 className="text-[14px] font-semibold text-ink-900">Data policies</h3>
      </div>
      <p className="text-[12px] text-ink-500 mb-4 leading-relaxed">
        Whether each registered workspace may be offered market defaults derived from CoStar reports.
        Withhold a workspace until its licensing is confirmed; a withheld default is shown as not applied
        with its reason, and the workspace&apos;s own uploaded figures are never affected.
      </p>
      <ul className="divide-y divide-border">
        {rows.map((t) => {
          const choice: DataPolicyChoice = t.data_policies?.costar_derived_defaults ?? 'allow';
          const offered = choice === 'allow';
          const busy = busyId === t.id;
          return (
            <li
              key={t.id}
              className="flex items-center justify-between gap-4 py-3"
              data-testid={`data-policy-row-${t.id}`}
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <div className="text-[13px] font-medium text-ink-900 truncate">{t.name}</div>
                  <Badge tone={t.kind === 'internal' ? 'blue' : 'gray'}>
                    {t.kind === 'internal' ? 'Internal' : 'External'}
                  </Badge>
                  {t.status !== 'active' && <Badge tone="amber">{t.status.replace('_', ' ')}</Badge>}
                  {t.data_policies_error && (
                    <span title={t.data_policies_error} data-testid={`data-policy-invalid-${t.id}`}>
                      <Badge tone="red">stored row invalid — withheld</Badge>
                    </span>
                  )}
                </div>
                <div className="text-[12px] text-ink-500 mt-0.5">
                  CoStar-derived defaults:{' '}
                  <span
                    className={cn('font-medium', offered ? 'text-ink-700' : 'text-warn-700')}
                    data-testid={`data-policy-state-${t.id}`}
                  >
                    {COSTAR_POLICY_LABEL[choice]}
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                {busy && <Loader2 size={12} className="animate-spin text-ink-500" aria-hidden="true" />}
                <button
                  type="button"
                  role="switch"
                  aria-checked={offered}
                  aria-label={`CoStar-derived defaults for ${t.name}`}
                  disabled={busy}
                  onClick={() => void onToggle(t, offered ? 'withhold' : 'allow')}
                  className={cn(
                    'w-10 h-5 rounded-full transition-colors relative disabled:opacity-60',
                    offered ? 'bg-brand-500' : 'bg-ink-300',
                  )}
                >
                  <span
                    className={cn(
                      'absolute top-0.5 w-4 h-4 bg-white rounded-full transition-transform',
                      offered ? 'translate-x-5' : 'translate-x-0.5',
                    )}
                  />
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
