import { Pencil, Link2 } from 'lucide-react';

export default function EngineLegend() {
  return (
    <div className="flex items-center gap-4 mb-4 text-[11px] text-ink-500">
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
    </div>
  );
}
