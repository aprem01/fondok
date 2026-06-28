'use client';
//
// Scheduled pipeline digests (Wave 4 W4.5).
//
// One page at /pipeline-digests for managing recurring Slack / email
// pipeline summaries. List existing schedules + an inline side-panel
// form for create / edit. "Send now" per row fires the dispatch path
// immediately for testing.
//
// Backed by /pipeline-digests on the worker — see
// apps/worker/app/api/pipeline_filters.py.
//
import { useEffect, useMemo, useState } from 'react';
import {
  Calendar, Clock, Mail, MessageSquare, Plus, Send, Trash2, Pencil,
  AlertCircle,
} from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import Modal from '@/components/ui/Modal';
import {
  api,
  isWorkerConnected,
  CreateDigestScheduleBody,
  DigestCadence,
  DigestDelivery,
  DigestScheduleRecord,
  SavedViewRecord,
} from '@/lib/api';

const WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

interface FormState {
  id: string | null;
  name: string;
  cadence: DigestCadence;
  weekday: number | null;
  hour_utc: number;
  delivery: DigestDelivery;
  slack_webhook_url: string;
  email_recipients: string;
  saved_view_id: string | null;
  include_kpi_summary: boolean;
  include_recently_mutated: boolean;
  include_deals_meeting_target: boolean;
  include_full_table: boolean;
  is_active: boolean;
}

const EMPTY_FORM: FormState = {
  id: null,
  name: '',
  cadence: 'daily',
  weekday: 0,
  hour_utc: 13,
  delivery: 'slack',
  slack_webhook_url: '',
  email_recipients: '',
  saved_view_id: null,
  include_kpi_summary: true,
  include_recently_mutated: true,
  include_deals_meeting_target: true,
  include_full_table: false,
  is_active: true,
};

export default function PipelineDigestsPage() {
  const [items, setItems] = useState<DigestScheduleRecord[]>([]);
  const [views, setViews] = useState<SavedViewRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);

  const refresh = async () => {
    if (!isWorkerConnected()) {
      setItems([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [schedules, savedViews] = await Promise.all([
        api.pipelineDigests.list(),
        api.pipelineViews.list().catch(() => [] as SavedViewRecord[]),
      ]);
      setItems(schedules);
      setViews(savedViews);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'failed to load schedules');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const openCreate = () => {
    setForm(EMPTY_FORM);
    setFormOpen(true);
  };

  const openEdit = (s: DigestScheduleRecord) => {
    setForm({
      id: s.id,
      name: s.name,
      cadence: s.cadence,
      weekday: s.weekday ?? 0,
      hour_utc: s.hour_utc,
      delivery: s.delivery,
      slack_webhook_url: s.slack_webhook_url ?? '',
      email_recipients: s.email_recipients.join(', '),
      saved_view_id: s.saved_view_id,
      include_kpi_summary: s.include_kpi_summary,
      include_recently_mutated: s.include_recently_mutated,
      include_deals_meeting_target: s.include_deals_meeting_target,
      include_full_table: s.include_full_table,
      is_active: s.is_active,
    });
    setFormOpen(true);
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    const recipients = form.email_recipients
      .split(/[,\n;]/)
      .map(e => e.trim())
      .filter(Boolean);
    const body: CreateDigestScheduleBody = {
      name: form.name.trim(),
      cadence: form.cadence,
      weekday: form.cadence === 'weekly' ? form.weekday : null,
      hour_utc: form.hour_utc,
      delivery: form.delivery,
      slack_webhook_url: form.slack_webhook_url.trim() || null,
      email_recipients: recipients,
      saved_view_id: form.saved_view_id,
      include_kpi_summary: form.include_kpi_summary,
      include_recently_mutated: form.include_recently_mutated,
      include_deals_meeting_target: form.include_deals_meeting_target,
      include_full_table: form.include_full_table,
      is_active: form.is_active,
    };
    try {
      if (form.id) {
        await api.pipelineDigests.update(form.id, body);
      } else {
        await api.pipelineDigests.create(body);
      }
      setFormOpen(false);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'save failed');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (s: DigestScheduleRecord) => {
    if (!confirm(`Delete digest "${s.name}"?`)) return;
    setBusy(s.id);
    try {
      await api.pipelineDigests.delete(s.id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'delete failed');
    } finally {
      setBusy(null);
    }
  };

  const handleRunNow = async (s: DigestScheduleRecord) => {
    setBusy(s.id);
    setFlash(null);
    try {
      const r = await api.pipelineDigests.runNow(s.id);
      if (r.no_op_reason) {
        setFlash(`No-op: ${r.no_op_reason}`);
      } else {
        const bits: string[] = [];
        if (r.slack_attempted) {
          bits.push(`Slack ${r.slack_succeeded ? 'OK' : r.slack_error ?? 'failed'}`);
        }
        if (r.email_attempted) {
          bits.push(`Email ${r.email_succeeded ? 'OK' : r.email_error ?? 'failed'}`);
        }
        setFlash(
          `Sent ${r.deal_count} deal(s) · ${bits.join(' · ') || 'no channel'}`,
        );
      }
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'send failed');
    } finally {
      setBusy(null);
    }
  };

  const cadenceLabel = (s: DigestScheduleRecord) => {
    const hour = `${String(s.hour_utc).padStart(2, '0')}:00 UTC`;
    if (s.cadence === 'daily') return `Daily @ ${hour}`;
    if (s.cadence === 'monthly') return `Monthly · 1st @ ${hour}`;
    const wd = s.weekday != null ? WEEKDAY_LABELS[s.weekday] : 'Mon';
    return `Weekly · ${wd} @ ${hour}`;
  };

  return (
    <div className="space-y-4 p-6">
      <PageHeader
        title="Pipeline digests"
        subtitle="Recurring Slack / email summaries of pipeline state."
        action={
          <Button variant="primary" onClick={openCreate}>
            <Plus size={14} /> New schedule
          </Button>
        }
      />

      {!isWorkerConnected() && (
        <Card className="border-warning-300 bg-warning-50 p-4 text-sm text-warning-900">
          The worker isn't connected. Set ``NEXT_PUBLIC_WORKER_URL`` to manage digests.
        </Card>
      )}

      {error && (
        <Card className="flex items-center gap-2 border-danger-300 bg-danger-50 p-3 text-sm text-danger-700">
          <AlertCircle size={14} /> {error}
        </Card>
      )}

      {flash && (
        <Card className="border-brand-300 bg-brand-50 p-3 text-sm text-brand-900">
          {flash}
        </Card>
      )}

      <Card className="p-0">
        {loading && (
          <div className="p-6 text-center text-sm text-ink-500">Loading…</div>
        )}
        {!loading && items.length === 0 && (
          <div className="p-10 text-center">
            <div className="mx-auto mb-3 inline-flex h-10 w-10 items-center justify-center rounded-full bg-ink-100 text-ink-500">
              <Calendar size={20} />
            </div>
            <p className="text-sm text-ink-700">
              No schedules yet. Add one to start receiving pipeline summaries.
            </p>
          </div>
        )}
        {!loading && items.length > 0 && (
          <div className="divide-y divide-border">
            {items.map(s => (
              <div
                key={s.id}
                className="flex flex-wrap items-center gap-3 px-4 py-3"
              >
                <div className="min-w-[200px] flex-1">
                  <div className="flex items-center gap-2 text-sm font-semibold text-ink-900">
                    {s.name}
                    {!s.is_active && (
                      <span className="rounded-full bg-ink-100 px-2 py-0.5 text-[10px] uppercase tracking-wide text-ink-500">
                        paused
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-3 text-xs text-ink-500">
                    <span className="inline-flex items-center gap-1">
                      <Clock size={11} /> {cadenceLabel(s)}
                    </span>
                    {(s.delivery === 'slack' || s.delivery === 'both') && (
                      <span className="inline-flex items-center gap-1">
                        <MessageSquare size={11} />
                        {s.slack_webhook_url ? 'Slack webhook' : 'Slack (no URL)'}
                      </span>
                    )}
                    {(s.delivery === 'email' || s.delivery === 'both') && (
                      <span className="inline-flex items-center gap-1">
                        <Mail size={11} /> {s.email_recipients.length} recipient(s)
                      </span>
                    )}
                    {s.saved_view_id && (
                      <span className="inline-flex items-center gap-1">
                        Filter: {views.find(v => v.id === s.saved_view_id)?.name ?? '(view)'}
                      </span>
                    )}
                    {s.last_run_at && (
                      <span>Last run {new Date(s.last_run_at).toLocaleString()}</span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    loading={busy === s.id}
                    onClick={() => void handleRunNow(s)}
                    title="Send now"
                  >
                    <Send size={14} /> Send now
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => openEdit(s)}
                    title="Edit"
                  >
                    <Pencil size={14} />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => void handleDelete(s)}
                    title="Delete"
                  >
                    <Trash2 size={14} />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Modal
        open={formOpen}
        onClose={() => setFormOpen(false)}
        title={form.id ? 'Edit schedule' : 'New pipeline digest'}
        maxWidth="max-w-lg"
      >
        <div className="space-y-4 p-5 text-sm">
          <Field label="Name">
            <input
              type="text"
              value={form.name}
              onChange={e => setForm({ ...form, name: e.target.value })}
              maxLength={120}
              className="w-full rounded-md border border-border bg-white px-3 py-1.5 focus:border-brand-500 focus:outline-none"
              autoFocus
            />
          </Field>

          <div className="grid grid-cols-3 gap-3">
            <Field label="Cadence">
              <select
                value={form.cadence}
                onChange={e =>
                  setForm({ ...form, cadence: e.target.value as DigestCadence })
                }
                className="w-full rounded-md border border-border bg-white px-2 py-1.5"
              >
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
              </select>
            </Field>
            {form.cadence === 'weekly' && (
              <Field label="Weekday">
                <select
                  value={form.weekday ?? 0}
                  onChange={e =>
                    setForm({ ...form, weekday: Number(e.target.value) })
                  }
                  className="w-full rounded-md border border-border bg-white px-2 py-1.5"
                >
                  {WEEKDAY_LABELS.map((wd, i) => (
                    <option key={wd} value={i}>{wd}</option>
                  ))}
                </select>
              </Field>
            )}
            <Field label="Hour (UTC)">
              <input
                type="number"
                min={0}
                max={23}
                value={form.hour_utc}
                onChange={e =>
                  setForm({ ...form, hour_utc: Number(e.target.value) })
                }
                className="w-full rounded-md border border-border bg-white px-3 py-1.5"
              />
            </Field>
          </div>

          <Field label="Delivery">
            <select
              value={form.delivery}
              onChange={e =>
                setForm({ ...form, delivery: e.target.value as DigestDelivery })
              }
              className="w-full rounded-md border border-border bg-white px-3 py-1.5"
            >
              <option value="slack">Slack only</option>
              <option value="email">Email only</option>
              <option value="both">Slack + Email</option>
            </select>
          </Field>

          {(form.delivery === 'slack' || form.delivery === 'both') && (
            <Field label="Slack webhook URL">
              <input
                type="url"
                value={form.slack_webhook_url}
                onChange={e =>
                  setForm({ ...form, slack_webhook_url: e.target.value })
                }
                placeholder="https://hooks.slack.com/services/..."
                className="w-full rounded-md border border-border bg-white px-3 py-1.5"
              />
            </Field>
          )}

          {(form.delivery === 'email' || form.delivery === 'both') && (
            <Field label="Email recipients (comma- or newline-separated)">
              <textarea
                value={form.email_recipients}
                onChange={e =>
                  setForm({ ...form, email_recipients: e.target.value })
                }
                rows={2}
                placeholder="ic@fondok.app, partners@fondok.app"
                className="w-full rounded-md border border-border bg-white px-3 py-1.5"
              />
            </Field>
          )}

          <Field label="Filter (optional)">
            <select
              value={form.saved_view_id ?? ''}
              onChange={e =>
                setForm({ ...form, saved_view_id: e.target.value || null })
              }
              className="w-full rounded-md border border-border bg-white px-3 py-1.5"
            >
              <option value="">All active deals</option>
              {views.map(v => (
                <option key={v.id} value={v.id}>{v.name}</option>
              ))}
            </select>
          </Field>

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-ink-700">Includes</label>
            <Toggle
              label="KPI summary"
              value={form.include_kpi_summary}
              onChange={v => setForm({ ...form, include_kpi_summary: v })}
            />
            <Toggle
              label="Recently mutated deals"
              value={form.include_recently_mutated}
              onChange={v => setForm({ ...form, include_recently_mutated: v })}
            />
            <Toggle
              label="Deals meeting target IRR"
              value={form.include_deals_meeting_target}
              onChange={v =>
                setForm({ ...form, include_deals_meeting_target: v })
              }
            />
            <Toggle
              label="Full pipeline table"
              value={form.include_full_table}
              onChange={v => setForm({ ...form, include_full_table: v })}
            />
            <Toggle
              label="Active"
              value={form.is_active}
              onChange={v => setForm({ ...form, is_active: v })}
            />
          </div>

          {error && (
            <div className="rounded-md border border-danger-300 bg-danger-50 px-3 py-2 text-xs text-danger-700">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setFormOpen(false)}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={saving}
              onClick={() => void handleSave()}
              disabled={!form.name.trim()}
            >
              {form.id ? 'Save changes' : 'Create schedule'}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-ink-700">
        {label}
      </label>
      {children}
    </div>
  );
}

function Toggle({
  label, value, onChange,
}: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-xs text-ink-700">
      <input
        type="checkbox"
        checked={value}
        onChange={e => onChange(e.target.checked)}
        className="h-3.5 w-3.5 rounded border-border text-brand-700 focus:ring-brand-500"
      />
      {label}
    </label>
  );
}
