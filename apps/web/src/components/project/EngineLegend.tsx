'use client';
import { Pencil, Link2, SlidersHorizontal } from 'lucide-react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';

export default function EngineLegend() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const rawId = (params?.id as string | undefined) ?? '';
  const currentTab = searchParams.get('tab') ?? '';

  // Hint is shown on every engine tab EXCEPT the Returns tab itself —
  // that's where the Live Assumptions sliders actually live. Sam couldn't
  // find them on his re-test because he was looking on P&L / Per-Key;
  // the affordance wasn't surfaced anywhere except on Returns.
  const showSliderHint = rawId && currentTab !== 'returns';

  const goToReturns = () => {
    router.push(`/projects/${rawId}?tab=returns`, { scroll: false });
  };

  return (
    <div className="flex flex-wrap items-center gap-4 mb-4 text-[11px] text-ink-500">
      <span className="flex items-center gap-1.5">
        <Pencil size={11} className="text-warn-500" /> Editable
      </span>
      <span className="text-ink-300">|</span>
      <span className="flex items-center gap-1.5">
        <Link2 size={11} className="text-success-500" /> Linked
      </span>
      <span className="text-ink-300">|</span>
      <span className="flex items-center gap-1.5">
        <span className="w-2 h-2 bg-ink-300 rounded" /> Read-Only
      </span>
      {showSliderHint && (
        <>
          <span className="text-ink-300 ml-auto" aria-hidden>|</span>
          <button
            type="button"
            onClick={goToReturns}
            className="inline-flex items-center gap-1.5 text-brand-700 hover:text-brand-500 hover:underline"
            title="Drag the Live Assumptions sliders on the Returns tab to see IRR / Multiple recompute in real time"
          >
            <SlidersHorizontal size={11} />
            Drag the Live Assumptions sliders on the Returns tab →
          </button>
        </>
      )}
    </div>
  );
}
