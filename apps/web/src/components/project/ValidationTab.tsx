'use client';

/**
 * Validation Tab — Wave 1 scaffolding.
 *
 * Eshan's June 2026 ask: strict separation between Onboarding (collect
 * data only) and Validation (surface gaps, USALI deviations, variance
 * flags, broker questions). This tab is where Phase B lives.
 *
 * STATUS: skeleton only. Cards are placeholders. See docs/ROADMAP.md §2
 * for the full implementation plan. As the underlying engines ship
 * (USALI scoring P1.4, gap detection P1.7, variance broker questions
 * P2.2, comp-set drift P4 in current ordering), each card lights up.
 *
 * Cards to land here (in order):
 *   1. Gaps Panel        — sequential + detail-level gaps (P1.7)
 *   2. USALI Deviations  — per-document compliance score callouts (P1.4)
 *   3. Variance Heatmap  — moved from current AnalysisTab sub-tab
 *   4. Broker Questions  — auto-generated YoY variance questions (P2.2)
 *   5. Q&A History       — broker responses + re-ingestion log (P2.3)
 *   6. Critic Findings   — moved from current AnalysisTab sub-tab
 *   7. Comp-Set Drift    — STR comp-set year-over-year diff (P8)
 */

import { Card } from '@/components/ui/Card';
import { AlertTriangle } from 'lucide-react';

export default function ValidationTab({ dealId }: { dealId: string }) {
  return (
    <div className="space-y-4">
      <Card className="p-6">
        <div className="flex items-start gap-3">
          <AlertTriangle size={18} className="text-warn-500 mt-0.5 flex-shrink-0" />
          <div>
            <h2 className="text-[15px] font-semibold text-ink-900 mb-1">
              Validation
            </h2>
            <p className="text-[13px] text-ink-700 leading-relaxed">
              Once onboarding is complete, this tab surfaces every issue
              Fondok found in your data: missing years, USALI deviations,
              suspicious year-over-year swings, and broker questions
              ready to send. Iterate here until the deal is ready for IC.
            </p>
          </div>
        </div>
      </Card>

      {/* Gaps Panel — to be implemented (P1.7) */}
      <Card className="p-5">
        <h3 className="text-[14px] font-semibold text-ink-900 mb-2">
          Document gaps
        </h3>
        <p className="text-[12.5px] text-ink-500">
          Coming soon. Will surface missing years (e.g., "missing 2021
          financials") and detail-level gaps (e.g., "2024 monthly through
          October only"). See ROADMAP §7.
        </p>
      </Card>

      {/* USALI Deviations Panel — P1.4 */}
      <Card className="p-5">
        <h3 className="text-[14px] font-semibold text-ink-900 mb-2">
          USALI compliance
        </h3>
        <p className="text-[12.5px] text-ink-500">
          Coming soon. Per-P&L compliance score (0–100) with specific
          deviation callouts citing USALI sections. See ROADMAP §3.
        </p>
      </Card>

      {/* Broker Questions Panel — P2.2 */}
      <Card className="p-5">
        <h3 className="text-[14px] font-semibold text-ink-900 mb-2">
          Questions for the broker
        </h3>
        <p className="text-[12.5px] text-ink-500">
          Coming soon. Auto-generated when Fondok detects material
          year-over-year swings on departmental lines. Each question
          comes with broker-ready text + the supporting data. See
          ROADMAP §4.
        </p>
      </Card>

      {/* Variance Heatmap — relocated from AnalysisTab in P1.2 */}
      <Card className="p-5">
        <h3 className="text-[14px] font-semibold text-ink-900 mb-2">
          Broker pro-forma vs. T-12
        </h3>
        <p className="text-[12.5px] text-ink-500">
          Variance flags between the broker's pro-forma and your T-12
          actuals will move here from the Analysis tab. See ROADMAP §2.
        </p>
      </Card>
    </div>
  );
}
