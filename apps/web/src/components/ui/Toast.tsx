'use client';

/**
 * Lightweight toast system — no external dep.
 *
 * Wrap the app in <ToastProvider> once (see AppShell). Components anywhere
 * underneath can call `useToast()` to push a transient notification:
 *
 *   const { toast } = useToast();
 *   toast('Deal created', { type: 'success' });
 *
 * The provider keeps the active stack capped at MAX_VISIBLE so a runaway
 * caller can't paper over the page. Newer toasts push older ones off the
 * bottom; users can also dismiss manually.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { CheckCircle2, XCircle, Info, X } from 'lucide-react';
import { cn } from '@/lib/format';

export type ToastType = 'success' | 'error' | 'info';

export interface ToastOptions {
  type?: ToastType;
  /** Auto-dismiss timeout in ms; default 4000. */
  duration?: number;
}

interface ToastRecord {
  id: number;
  message: string;
  type: ToastType;
  duration: number;
}

interface ToastContextValue {
  toast: (message: string, opts?: ToastOptions) => number;
  dismiss: (id: number) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const MAX_VISIBLE = 4;
const DEFAULT_DURATION_MS = 4000;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);
  const idCounter = useRef(0);
  const timers = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());

  const dismiss = useCallback((id: number) => {
    const t = timers.current.get(id);
    if (t) {
      clearTimeout(t);
      timers.current.delete(id);
    }
    setToasts((prev) => prev.filter((x) => x.id !== id));
  }, []);

  const toast = useCallback(
    (message: string, opts: ToastOptions = {}) => {
      const id = ++idCounter.current;
      const record: ToastRecord = {
        id,
        message,
        type: opts.type ?? 'info',
        duration: opts.duration ?? DEFAULT_DURATION_MS,
      };
      setToasts((prev) => {
        // Drop the oldest when we'd exceed the visible cap.
        const next = [...prev, record];
        if (next.length > MAX_VISIBLE) {
          const dropped = next.slice(0, next.length - MAX_VISIBLE);
          dropped.forEach((d) => {
            const t = timers.current.get(d.id);
            if (t) {
              clearTimeout(t);
              timers.current.delete(d.id);
            }
          });
          return next.slice(-MAX_VISIBLE);
        }
        return next;
      });
      if (record.duration > 0) {
        const handle = setTimeout(() => dismiss(id), record.duration);
        timers.current.set(id, handle);
      }
      return id;
    },
    [dismiss],
  );

  // Drain timers on unmount so we don't leak if the provider tears down.
  useEffect(() => {
    const map = timers.current;
    return () => {
      map.forEach((t) => clearTimeout(t));
      map.clear();
    };
  }, []);

  const value = useMemo<ToastContextValue>(
    () => ({ toast, dismiss }),
    [toast, dismiss],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    // Make calls outside the provider survive — useful during SSR / tests.
    return {
      toast: () => -1,
      dismiss: () => {},
    };
  }
  return ctx;
}

function ToastViewport({
  toasts,
  onDismiss,
}: {
  toasts: ToastRecord[];
  onDismiss: (id: number) => void;
}) {
  if (toasts.length === 0) return null;
  return (
    <div
      aria-live="polite"
      aria-atomic="false"
      className="fixed bottom-4 right-4 z-[60] flex flex-col gap-2 pointer-events-none w-full max-w-[380px]"
    >
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={() => onDismiss(t.id)} />
      ))}
    </div>
  );
}

const styleByType: Record<
  ToastType,
  { border: string; bg: string; iconColor: string; icon: typeof CheckCircle2 }
> = {
  success: {
    border: 'border-success-500/40',
    bg: 'bg-success-50',
    iconColor: 'text-success-700',
    icon: CheckCircle2,
  },
  error: {
    border: 'border-danger-500/40',
    bg: 'bg-danger-50',
    iconColor: 'text-danger-700',
    icon: XCircle,
  },
  info: {
    border: 'border-brand-500/40',
    bg: 'bg-brand-50',
    iconColor: 'text-brand-700',
    icon: Info,
  },
};

function ToastItem({
  toast,
  onDismiss,
}: {
  toast: ToastRecord;
  onDismiss: () => void;
}) {
  const [entered, setEntered] = useState(false);

  useEffect(() => {
    // Trigger slide-in on next paint; keeps SSR-safe.
    const r = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(r);
  }, []);

  const style = styleByType[toast.type];
  const Icon = style.icon;

  return (
    <div
      role="status"
      className={cn(
        'pointer-events-auto rounded-md border shadow-card text-[12.5px]',
        'flex items-start gap-2.5 px-3.5 py-2.5',
        'transition-all duration-200 ease-out',
        style.border,
        style.bg,
        entered
          ? 'translate-x-0 opacity-100'
          : 'translate-x-4 opacity-0',
      )}
    >
      <Icon size={15} className={cn('mt-0.5 flex-shrink-0', style.iconColor)} />
      <div className="flex-1 text-ink-900 leading-snug break-words">
        {toast.message}
      </div>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss notification"
        className="p-0.5 -m-0.5 rounded text-ink-500 hover:text-ink-900 hover:bg-ink-300/30 transition-colors flex-shrink-0"
      >
        <X size={13} />
      </button>
    </div>
  );
}
