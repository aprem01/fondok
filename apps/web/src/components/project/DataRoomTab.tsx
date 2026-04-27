'use client';
import { useState } from 'react';
import {
  UploadCloud, FolderOpen, Info, FileText, MoreHorizontal, FileSpreadsheet,
  CheckCircle2, Loader2, Circle,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge, StatusBadge } from '@/components/ui/Badge';
import { documentChecklist, engines, kimptonDocuments } from '@/lib/mockData';

export default function DataRoomTab({ projectId }: { projectId: number }) {
  const [selectedDoc, setSelectedDoc] = useState<string | null>(null);
  const isFullDoc = projectId === 7; // Kimpton Angler has docs
  const docs = isFullDoc ? kimptonDocuments : [];
  const checklist = documentChecklist.map((d, i) => ({
    name: d, complete: isFullDoc && i < 4,
  }));

  const completeCount = checklist.filter(d => d.complete).length;
  const extracted = docs.filter(d => d.status === 'Extracted').length;
  const processing = docs.filter(d => d.status === 'Processing').length;

  return (
    <div className="space-y-5">
      <Card className="p-5">
        <div className="flex items-start gap-3">
          <FolderOpen size={20} className="text-brand-500 mt-0.5" />
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <h2 className="text-[15px] font-semibold text-ink-900">Data Room</h2>
              <Info size={13} className="text-ink-400" />
            </div>
            <p className="text-[12.5px] text-ink-500 mt-1">
              Upload and manage deal documents for AI-powered extraction and underwriting automation.
              {' '}({extracted} of {checklist.length} extracted)
            </p>
          </div>
        </div>
      </Card>

      <Card className="p-5">
        <div className="flex items-center gap-4">
          <div className="w-14 h-14 rounded-lg bg-brand-50 flex items-center justify-center flex-shrink-0">
            <UploadCloud size={24} className="text-brand-500" />
          </div>
          <div className="flex-1">
            <h3 className="text-[14px] font-semibold text-ink-900">Upload Documents</h3>
            <p className="text-[12px] text-ink-500 mt-0.5">Drag and drop OM, T12, STR reports · AI auto-extracts key data</p>
          </div>
          <Button variant="primary" size="sm">Choose Files</Button>
          <Button variant="secondary" size="sm">Browse Templates</Button>
        </div>
      </Card>

      <div className="grid grid-cols-3 gap-5">
        {/* Document Checklist */}
        <Card className="col-span-2 p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-[14px] font-semibold text-ink-900">Document Checklist</h3>
            <span className="text-[12px] text-ink-500 tabular-nums">{completeCount}/{checklist.length}</span>
          </div>
          <div className="mb-4">
            <div className="flex justify-between text-[11px] text-ink-500 mb-1">
              <span>Underwriting Ready</span>
              <span>{Math.round((completeCount / checklist.length) * 100)}%</span>
            </div>
            <div className="h-1.5 bg-ink-300/30 rounded-full overflow-hidden">
              <div className="h-full bg-success-500" style={{ width: `${(completeCount / checklist.length) * 100}%` }} />
            </div>
          </div>
          <div className="space-y-2">
            {checklist.map(d => (
              <div key={d.name} className="flex items-center gap-3 py-1.5">
                {d.complete
                  ? <CheckCircle2 size={15} className="text-success-500 flex-shrink-0" />
                  : <Circle size={15} className="text-ink-300 flex-shrink-0" />}
                <span className={`text-[12.5px] flex-1 ${d.complete ? 'text-ink-900' : 'text-ink-500'}`}>{d.name}</span>
                <Badge tone="red">REQ</Badge>
              </div>
            ))}
          </div>
        </Card>

        {/* Engine Status */}
        <Card className="p-5">
          <h3 className="text-[14px] font-semibold text-ink-900 mb-4">Engine Status</h3>
          <div className="space-y-3.5">
            {engines.map(e => (
              <div key={e.id}>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[12px] text-ink-700 font-medium">{e.label}</span>
                  <span className="text-[11px] text-ink-500 tabular-nums">{e.progress}%</span>
                </div>
                <div className="h-1.5 bg-ink-300/30 rounded-full overflow-hidden">
                  <div className="h-full bg-brand-500" style={{ width: `${e.progress}%` }} />
                </div>
              </div>
            ))}
          </div>
          <div className="text-[11px] text-ink-500 mt-4 pt-4 border-t border-border">
            Upload more documents to increase confidence
          </div>
        </Card>
      </div>

      {docs.length > 0 && (
        <Card className="p-5">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <h3 className="text-[14px] font-semibold text-ink-900">Documents ({docs.length})</h3>
              <Badge tone="green">{extracted} Extracted</Badge>
              {processing > 0 && <Badge tone="blue">{processing} Processing</Badge>}
            </div>
          </div>

          <div className="grid grid-cols-3 gap-5">
            <div className="col-span-2 space-y-2">
              {docs.map(d => (
                <button key={d.name} onClick={() => setSelectedDoc(d.name)}
                  className={`w-full text-left p-3 rounded-md border transition-colors ${
                    selectedDoc === d.name ? 'bg-brand-50 border-brand-500' : 'border-border hover:bg-ink-300/10'
                  }`}>
                  <div className="flex items-start gap-3">
                    <div className="w-9 h-9 rounded bg-ink-300/30 flex items-center justify-center flex-shrink-0">
                      {d.name.endsWith('.xlsx') ? <FileSpreadsheet size={16} className="text-success-700" /> : <FileText size={16} className="text-ink-700" />}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <div className="text-[12.5px] font-medium text-ink-900 truncate">{d.name}</div>
                        <StatusBadge value={d.status} />
                        <Badge tone="gray">{d.type}</Badge>
                      </div>
                      <div className="text-[11px] text-ink-500 mt-1">{d.size} · {d.date}</div>
                      {d.status === 'Extracted' && (
                        <div className="flex items-center gap-3 mt-2">
                          <div className="text-[11px] text-ink-700">
                            <span className="text-brand-700 font-medium">{d.fields}</span> fields extracted
                            {' · '}<span className="font-medium">{d.confidence}%</span> confidence
                          </div>
                          {d.populates.length > 0 && (
                            <div className="flex gap-1">
                              {d.populates.map(p => <Badge key={p} tone="blue">{p}</Badge>)}
                            </div>
                          )}
                        </div>
                      )}
                      {d.status === 'Processing' && (
                        <div className="flex items-center gap-1.5 mt-2 text-[11px] text-brand-700">
                          <Loader2 size={11} className="animate-spin" /> Extracting...
                        </div>
                      )}
                    </div>
                    <button onClick={e => { e.stopPropagation(); }} className="p-1 hover:bg-ink-300/20 rounded">
                      <MoreHorizontal size={14} className="text-ink-400" />
                    </button>
                  </div>
                </button>
              ))}
            </div>

            <Card className="p-4 bg-ink-300/5">
              <h4 className="text-[12px] font-semibold text-ink-900 mb-2">Extracted Data</h4>
              {selectedDoc ? (
                <div>
                  <div className="text-[11px] text-ink-500 mb-3 truncate">{selectedDoc}</div>
                  {(() => {
                    const doc = docs.find(d => d.name === selectedDoc);
                    if (!doc || doc.status !== 'Extracted') {
                      return <div className="text-[11.5px] text-ink-500">Document still processing...</div>;
                    }
                    return (
                      <div className="space-y-2 text-[11.5px]">
                        <DataRow label="ADR" value="$385" confidence={96} />
                        <DataRow label="Occupancy" value="76.2%" confidence={94} />
                        <DataRow label="RevPAR" value="$293" confidence={97} />
                        <DataRow label="NOI (T-12)" value="$4.28M" confidence={92} />
                        <DataRow label="Gross Revenue" value="$15.08M" confidence={95} />
                        <DataRow label="Operating Expenses" value="$9.32M" confidence={89} />
                      </div>
                    );
                  })()}
                </div>
              ) : (
                <div className="text-center py-8">
                  <div className="text-[11.5px] text-ink-500">Select a document to view extracted data</div>
                </div>
              )}
            </Card>
          </div>
        </Card>
      )}
    </div>
  );
}

function DataRow({ label, value, confidence }: { label: string; value: string; confidence: number }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-border last:border-0">
      <span className="text-ink-500">{label}</span>
      <div className="flex items-center gap-2">
        <span className="font-medium tabular-nums text-ink-900">{value}</span>
        <span className="text-ink-400 text-[10px]">{confidence}%</span>
      </div>
    </div>
  );
}
