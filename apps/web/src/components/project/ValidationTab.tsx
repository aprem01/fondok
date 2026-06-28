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

import { BrokerQuestionsPanel } from './validation/BrokerQuestionsPanel';
import { CompSetDriftCallout } from './validation/CompSetDriftCallout';
import { CoachMark } from '@/components/help/CoachMark';

/**
 * Validation Tab — Wave 1 reduction (Linear/Stripe pass).
 *
 * Previously rendered four surfaces. Eliminated:
 *   - Header "what does this tab do" Card (taught — coach marks cover it)
 *   - GapChipsStrip (lives on Data Room next to the uploads it acts on)
 *
 * What's left: the two surfaces that are uniquely Validation's job —
 * broker-question generation and STR comp-set drift. CompSetDriftCallout
 * is silent when no drifts, so the tab collapses to a single panel on
 * clean deals (correct).
 */
export default function ValidationTab({ dealId }: { dealId: string }) {
  return (
    <div className="space-y-5">
      {/* Broker Questions — the marquee panel. */}
      <CoachMark
        anchorId="validation-broker-questions"
        viewKey="validation"
        order={0}
        title="Auto-generated broker questions"
        body="Fondok writes broker-ready questions from year-over-year variances in the financials. Mark them sent when you've emailed the broker, then paste the reply to close the loop — the engine re-ingests the answer."
        side="bottom"
        learnMoreHref="/methodology#extraction"
      >
        <BrokerQuestionsPanel dealId={dealId} />
      </CoachMark>

      {/* Comp-set drift side-note — silent when no drifts. */}
      <CompSetDriftCallout dealId={dealId} />
    </div>
  );
}
