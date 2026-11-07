'use client';
// For a firm's admins: is Fondok currently looking at our workspace? Lists the
// live support sessions on this firm (why, until when) and lets an admin end
// one. Hidden when there are none — a quiet default, not an empty card.

import { useCallback, useEffect, useState } from 'react';
import { Eye } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import { api, isWorkerConnected, userMessage } from '@/lib/api';

interface FirmGrant {
  id: string;
  reason: string;
  created_at: string | null;
  expires_at: string;
}

function until(iso: string): string {
  const d = new Date(iso);
  return Number.isFinite(d.getTime()) ? d.toLocaleString() : '—';
}

export default function SupportSessionsCard({ isAdmin }: { isAdmin: boolean }) {
  const { toast } = useToast();
  const [grants, setGrants] = useState<FirmGrant[]>([]);
  const [ending, setEnding] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api.admin.support.onMyFirm();
      setGrants(r.grants);
    } catch {
      setGrants([]);
    }
  }, []);

  useEffect(() => {
    if (!isAdmin || !isWorkerConnected()) return;
    void load();
    const t = window.setInterval(() => void load(), 60_000);
    return () => window.clearInterval(t);
  }, [isAdmin, load]);

  if (!isAdmin || grants.length === 0) return null;

  const end = async (g: FirmGrant) => {
    setEnding(g.id);
    try {
      await api.admin.support.endOnMyFirm(g.id);
      toast('Fondok support access ended.', { type: 'success' });
      await load();
    } catch (e) {
      toast(userMessage(e), { type: 'error' });
    } finally {
      setEnding(null);
    }
  };

  return (
    <Card className="p-5 border-warn-500/40" data-testid="settings-support-sessions">
      <div className="flex items-center gap-2">
        <Eye size={15} className="text-warn-700" aria-hidden="true" />
        <h3 className="text-[14px] font-semibold text-ink-900">Fondok support access</h3>
      </div>
      <p className="mt-1 text-[12.5px] text-ink-500">
        Fondok&apos;s support team currently has read-only access to this workspace. Every page they open is in
        your activity log. You can end it at any time.
      </p>
      <ul className="mt-3 divide-y divide-border">
        {grants.map((g) => (
          <li key={g.id} className="flex flex-wrap items-center gap-3 py-2.5">
            <div className="min-w-0 flex-1">
              <div className="text-[13px] text-ink-900">{g.reason}</div>
              <div className="text-[11.5px] text-ink-500">
                Started {g.created_at ? until(g.created_at) : '—'} · ends {until(g.expires_at)}
              </div>
            </div>
            <Button size="sm" variant="secondary" loading={ending === g.id} onClick={() => void end(g)}>
              End access
            </Button>
          </li>
        ))}
      </ul>
    </Card>
  );
}
