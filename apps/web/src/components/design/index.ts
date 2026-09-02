/**
 * Fondok canonical design-system layer.
 *
 * Shared, presentational, props-driven components + tokens extracted EXACTLY
 * from the vendored canonical design (`design/canonical/*.dc.html`, FON-72).
 * Every tab is meant to be rebuilt from these — see `README.md` for the
 * component → `.dc.html` source map and the token list.
 *
 * Not yet wired into any tab (design-system-only phase).
 */

export * from './tokens';
export { ProvenanceDot, StateBadge } from './ProvenanceDot';
export type { ProvenanceDotProps, StateBadgeProps } from './ProvenanceDot';
export { DataKey } from './DataKey';
export type { DataKeyProps } from './DataKey';
export { KpiTile } from './KpiTile';
export type { KpiTileProps } from './KpiTile';
export { SectionCard } from './SectionCard';
export type { SectionCardProps } from './SectionCard';
export { SubTabNav } from './SubTabNav';
export type { SubTabItem, SubTabNavProps } from './SubTabNav';
export { StatementTable, denseValueColor } from './StatementTable';
export type { StatementTableProps, StatementRow, StatementCell } from './StatementTable';
export { FieldValue, fieldKindFromState } from './FieldValue';
export type { FieldValueProps, FieldKind } from './FieldValue';
export { WhereThisCameFrom } from './WhereThisCameFrom';
export type {
  WhereThisCameFromProps,
  ProvInput,
  ProvSource,
  ProvOverride,
  ProvAction,
} from './WhereThisCameFrom';
