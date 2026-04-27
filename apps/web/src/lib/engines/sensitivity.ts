// Sensitivity engine — generates 5×5 matrices by flexing two assumptions.
import { Assumptions, SensitivityMatrix, SensitivityCell } from './types';
import { runModel } from './model';

type AssumptionKey = keyof Pick<
  Assumptions,
  'exitCapRate' | 'revparGrowth' | 'ltv' | 'interestRate' | 'holdYears'
>;

type Metric = 'leveredIrr' | 'equityMultiple' | 'cashOnCash';

export interface SensitivityConfig {
  rowKey: AssumptionKey;
  rowValues: number[];
  colKey: AssumptionKey;
  colValues: number[];
  metric: Metric;
  rowLabel: string;
  colLabel: string;
}

export function buildSensitivity(
  base: Assumptions,
  cfg: SensitivityConfig,
): SensitivityMatrix {
  const cells: SensitivityCell[][] = [];
  let baseRow = -1;
  let baseCol = -1;
  // Find closest row/col to base
  for (let i = 0; i < cfg.rowValues.length; i++) {
    if (Math.abs(cfg.rowValues[i] - (base[cfg.rowKey] as number)) < 1e-6) baseRow = i;
  }
  for (let j = 0; j < cfg.colValues.length; j++) {
    if (Math.abs(cfg.colValues[j] - (base[cfg.colKey] as number)) < 1e-6) baseCol = j;
  }
  if (baseRow < 0) baseRow = Math.floor(cfg.rowValues.length / 2);
  if (baseCol < 0) baseCol = Math.floor(cfg.colValues.length / 2);

  for (let i = 0; i < cfg.rowValues.length; i++) {
    const row: SensitivityCell[] = [];
    for (let j = 0; j < cfg.colValues.length; j++) {
      const flexed: Assumptions = {
        ...base,
        [cfg.rowKey]: cfg.rowValues[i],
        [cfg.colKey]: cfg.colValues[j],
      };
      // holdYears must be int
      if (cfg.rowKey === 'holdYears') (flexed as Assumptions).holdYears = Math.round(cfg.rowValues[i]);
      if (cfg.colKey === 'holdYears') (flexed as Assumptions).holdYears = Math.round(cfg.colValues[j]);

      const out = runModel(flexed);
      const value = out[cfg.metric];
      row.push({
        value: isFinite(value) ? value : 0,
        rowVal: cfg.rowValues[i],
        colVal: cfg.colValues[j],
        isBase: i === baseRow && j === baseCol,
      });
    }
    cells.push(row);
  }

  return {
    rowLabel: cfg.rowLabel,
    colLabel: cfg.colLabel,
    rows: cfg.rowValues,
    cols: cfg.colValues,
    cells,
    unit: cfg.metric === 'equityMultiple' ? 'multiple' : 'pct',
    baseRow,
    baseCol,
  };
}

/** Default suite of sensitivity matrices for the Returns tab. */
export function defaultSensitivities(base: Assumptions): SensitivityMatrix[] {
  return [
    buildSensitivity(base, {
      rowKey: 'exitCapRate',
      rowValues: [0.060, 0.065, 0.070, 0.075, 0.080],
      colKey: 'revparGrowth',
      colValues: [0.020, 0.030, 0.040, 0.050, 0.060],
      metric: 'leveredIrr',
      rowLabel: 'Exit Cap',
      colLabel: 'RevPAR Growth',
    }),
    buildSensitivity(base, {
      rowKey: 'ltv',
      rowValues: [0.55, 0.60, 0.65, 0.70, 0.75],
      colKey: 'holdYears',
      colValues: [3, 4, 5, 6, 7],
      metric: 'equityMultiple',
      rowLabel: 'LTV',
      colLabel: 'Hold',
    }),
    buildSensitivity(base, {
      rowKey: 'exitCapRate',
      rowValues: [0.060, 0.065, 0.070, 0.075, 0.080],
      colKey: 'holdYears',
      colValues: [3, 4, 5, 6, 7],
      metric: 'cashOnCash',
      rowLabel: 'Cap Rate',
      colLabel: 'Hold',
    }),
  ];
}
