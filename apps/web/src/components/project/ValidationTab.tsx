'use client';

/**
 * Validation Tab — Wave 1 live layout (ROADMAP #3 / #4 / #7 / #8).
 *
 * Eshan's June 2026 separation between Onboarding and Validation lives
 * here. The tab mounts three institutional-grade surfaces:
 *
 *   1. GapChipsStrip          — document coverage gaps (#7)
 *   2. BrokerQuestionsPanel   — YoY variance broker questions (#4)
 *   3. CompSetDriftCallout    — STR comp-set drift side-note (#8)
 *
 * Per-document USALI compliance scoring (#3) lives on the Data Room
 * tab next to each document card — see ``DataRoomTab`` for the badge +
 * deviation accordion wiring.
 */

import { ShieldCheck } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { GapChipsStrip } from './validation/GapChipsStrip';
import { BrokerQuestionsPanel } from './validation/BrokerQuestionsPanel';
import { CompSetDriftCallout } from './validation/CompSetDriftCallout';

export default function ValidationTab({ dealId }: { dealId: string }) {
  return (
    <div className="space-y-5">
      {/* Header card — orients reviewers to what Validation surfaces vs.
          what Data Room handles. Mirrors VarianceTab's anchor pattern. */}
      <Card className="p-5 border-l-4 border-l-brand-500">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-md bg-brand-50 flex items-center justify-center flex-shrink-0">
            <ShieldCheck size={16} className="text-brand-700" aria-hidden="true" />
          </div>
          <div className="flex-1">
            <h2 className="text-[15px] font-semibold text-ink-900">
              Validation
            </h2>
            <p className="text-[12.5px] text-ink-700 mt-1 leading-relaxed">
              Every issue Fondok found in the uploaded data — coverage
              gaps, broker-ready year-over-year questions, and STR
              comp-set drift. Resolve the items below before promoting
              the deal to IC.
            </p>
          </div>
        </div>
      </Card>

      {/* Coverage gap chips — also rendered atop the Data Room tab.
          Different mount point, same component. */}
      <GapChipsStrip dealId={dealId} surface="validation" />

      {/* Broker Questions — the marquee panel. */}
      <BrokerQuestionsPanel dealId={dealId} />

      {/* Comp-set drift side-note — silent when no drifts. */}
      <CompSetDriftCallout dealId={dealId} />
    </div>
  );
}
