'use client';
// The bar a Fondok staff admin sees while reading a firm under support access:
// which firm, that it is read-only, how long is left, and a way out. It also
// ends the mode in this tab when the grant expires or the worker no longer
// recognises it (ended elsewhere), and reloads so no firm data stays on screen.

import { useCallback, useEffect, useState } from 'react';
import { Eye } from 'lucide-react';
import { api } from '@/lib/api';
import {
  clearSupportSession,
  getSupportSession,
  onSupportSessionChange,
  type SupportSession,
} from '@/lib/supportAccess';

const LEAVE_TO = '/admin/platform';

function remaining(expiresAt: string): string {
  const ms = new Date(expiresAt).getTime() - Date.now();
  if (ms <= 0) return 'ending';
  const min = Math.ceil(ms / 60000);
  if (min < 60) return `${min} min left`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  if (h >= 48) return `${Math.ceil(h / 24)} days left`;
  return m ? `${h} h ${m} min left` : `${h} h left`;
}

export default function SupportBanner() {
  const [session, setSession] = useState<SupportSession | null>(null);
  const [, tick] = useState(0);
  const [ending, setEnding] = useState(false);

  useEffect(() => {
    setSession(getSupportSession());
    return onSupportSessionChange(() => setSession(getSupportSession()));
  }, []);

  const leave = useCallback(() => {
    clearSupportSession();
    window.location.assign(LEAVE_TO);
  }, []);

  // Countdown + expiry, and a periodic check that the grant is still live.
  useEffect(() => {
    if (!session) return;
    const clock = window.setInterval(() => {
      tick((n) => n + 1);
      if (new Date(session.expiresAt).getTime() <= Date.now()) leave();
    }, 30_000);
    const check = window.setInterval(() => {
      api.admin.support
        .list()
        .then((r) => {
          if (!r.grants.some((g) => g.id === session.grantId)) leave();
        })
        .catch(() => {
          /* transient: the worker still refuses requests once the grant ends */
        });
    }, 60_000);
    return () => {
      window.clearInterval(clock);
      window.clearInterval(check);
    };
  }, [session, leave]);

  if (!session) return null;

  const end = async () => {
    setEnding(true);
    try {
      await api.admin.support.end(session.grantId);
    } catch {
      /* the grant still expires on its own; leave either way */
    }
    leave();
  };

  return (
    <div
      role="status"
      className="sticky top-0 z-30 flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-warn-500/40 bg-warn-50 px-5 py-2 text-[12.5px] text-warn-700"
    >
      <Eye size={14} aria-hidden="true" />
      <span>
        <strong className="font-semibold">Support access: {session.firm}</strong> · read-only · {remaining(session.expiresAt)} ·
        every page you open is recorded in {session.firm}&apos;s activity log
      </span>
      <button
        type="button"
        onClick={() => void end()}
        disabled={ending}
        className="ml-auto rounded-md border border-warn-500/50 bg-white px-2.5 py-1 font-medium text-warn-700 hover:bg-warn-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-warn-500 disabled:opacity-60"
      >
        {ending ? 'Ending…' : 'End support access'}
      </button>
    </div>
  );
}
