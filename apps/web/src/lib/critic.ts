/**
 * Critic finding shape — the Critic agent reads the broker pro forma, the T-12
 * and the market context and surfaces cross-field issues a per-field variance
 * pass would miss. Each finding grounds in a USALI rule_id (or a MULTI_FIELD_*
 * rule). Severity ordered CRITICAL → WARN → INFO.
 *
 * TYPES ONLY — the live findings come from GET /deals/{id}/critic
 * (AnalysisTab maps the worker JSON onto this shape). Nothing ships a fixture.
 */
export type CriticSeverity = 'CRITICAL' | 'WARN' | 'INFO';

export interface CriticFinding {
  id: string;
  ruleId: string;
  title: string;
  narrative: string;
  severity: CriticSeverity;
  citedFields: string[];
  citedPages: number[];
  citedDocumentId?: string;
  citedDocumentName?: string;
  impactEstimateUsd?: number;
}
