'use client';
/**
 * ForecastingTab — Wave 3 W3.3 host for the STR forward-forecast panel.
 *
 * Today the tab hosts a single panel (STRForecastPanel) but the layout
 * leaves room for additional forecasting tools (e.g. CBRE-anchored
 * RevPAR walk, F&B forecast) without re-routing.
 *
 * Data flow: the tab fetches the deal's STR forecast on mount via
 * ``api.deals.strForecast(dealId)``. The panel owns its own scenario-
 * edit popovers and re-fetches when an override saves successfully.
 */
import { useCallback, useEffect, useState } from 'react';
import { api, type STRForecastResponse } from '@/lib/api';
import STRForecastPanel from './STRForecastPanel';

export interface ForecastingTabProps {
  projectId: number | string;
}

export default function ForecastingTab({ projectId }: ForecastingTabProps) {
  const dealId = String(projectId);
  const isMockId = /^\d+$/.test(dealId);

  const [forecast, setForecast] = useState<STRForecastResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (isMockId) return;
    let cancelled = false;
    const ac = new AbortController();
    setLoading(true);
    api.validation
      .strForecast(dealId, ac.signal)
      .then((res) => {
        if (cancelled) return;
        setForecast(res);
        setError(null);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        // 404 / 422 are expected for new deals with no STR Trend uploaded.
        setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [dealId, isMockId]);

  const onScenariosUpdated = useCallback((next: STRForecastResponse) => {
    setForecast(next);
  }, []);

  if (isMockId) {
    return (
      <div className="text-[12.5px] text-ink-500">
        STR Forward Forecast is available on live deals only. Connect this
        project to a worker-backed deal to see the 24-month forecast.
      </div>
    );
  }

  if (loading && !forecast) {
    return <div className="text-[12.5px] text-ink-500">Loading STR forecast…</div>;
  }

  if (error && !forecast) {
    return (
      <div className="text-[12.5px] text-ink-500">
        STR forecast unavailable for this deal. Upload an STR Trend report to
        enable the 24-month forward forecast.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <STRForecastPanel
        forecast={forecast}
        dealId={dealId}
        onScenariosUpdated={onScenariosUpdated}
      />
    </div>
  );
}
