import { Activity } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import EngineHeader from './EngineHeader';

export default function EnginePlaceholder({
  name, desc, outputs, dependsOn,
}: { name: string; desc: string; outputs: string[]; dependsOn: string | null }) {
  return (
    <div>
      <EngineHeader name={name} desc={desc} outputs={outputs} dependsOn={dependsOn} />
      <Card className="p-16 text-center">
        <div className="w-12 h-12 rounded-lg bg-ink-300/20 flex items-center justify-center mx-auto mb-4">
          <Activity size={20} className="text-ink-400" />
        </div>
        <h3 className="text-[15px] font-semibold text-ink-900">No Model Output</h3>
        <p className="text-[12.5px] text-ink-500 mt-1">Run the model to populate {name.toLowerCase()} results.</p>
      </Card>
    </div>
  );
}
