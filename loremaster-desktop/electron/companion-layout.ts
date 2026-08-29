export const COMPANION_MAX_METER_ROWS = 6;
export const COMPANION_MAX_CONTROL_ROWS = 6;

const COMPANION_WIDTH = 340;
const CONTROL_HEADER_HEIGHT = 31;
const CONTROL_ROW_HEIGHT = 48;
const METER_HEADER_HEIGHT = 34;
const METER_ROW_HEIGHT = 42;
const METER_FOOTER_HEIGHT = 19;
const SINGLE_SECTION_BORDER_HEIGHT = 2;
const JOINED_SECTION_BORDER_HEIGHT = 3;

export interface CompanionLayout {
  meterRows: number;
  controlRows: number;
  meterHiddenRows: number;
  controlHiddenRows: number;
  panelSize: { width: number; height: number };
}

function scaled(value: number, scale: number): number {
  const factor = Math.max(0.9, Math.min(1.6, scale));
  return Math.round(value * factor);
}

export function companionSurfaceSize(
  controlRows: number,
  meterRows: number,
  scale: number,
  meterHasOverflow = false,
): { width: number; height: number } {
  const controlHeight = controlRows > 0
    ? CONTROL_HEADER_HEIGHT + controlRows * CONTROL_ROW_HEIGHT
    : 0;
  const meterHeight = meterRows > 0
    ? METER_HEADER_HEIGHT + meterRows * METER_ROW_HEIGHT + (meterHasOverflow ? METER_FOOTER_HEIGHT : 0)
    : 0;
  // Sections size from their children. Account for the outer border as well;
  // the second section drops its shared top border when the two are joined.
  const sectionBorderHeight = controlRows > 0 && meterRows > 0
    ? JOINED_SECTION_BORDER_HEIGHT
    : controlRows > 0 || meterRows > 0 ? SINGLE_SECTION_BORDER_HEIGHT : 0;
  return {
    width: scaled(COMPANION_WIDTH, scale),
    height: Math.max(1, scaled(controlHeight + meterHeight + sectionBorderHeight, scale)),
  };
}

export function boundedCompanionLayout(
  meterTotal: number,
  controlTotal: number,
  scale: number,
  maxPanelHeight: number,
): CompanionLayout {
  const maximumMeterRows = Math.min(COMPANION_MAX_METER_ROWS, Math.max(0, Math.floor(meterTotal)));
  const maximumControlRows = Math.min(COMPANION_MAX_CONTROL_ROWS, Math.max(0, Math.floor(controlTotal)));
  const needsBoth = maximumMeterRows > 0 && maximumControlRows > 0;
  let best: (CompanionLayout & { coverage: number; balance: number; shown: number }) | null = null;

  for (let meterRows = 0; meterRows <= maximumMeterRows; meterRows += 1) {
    for (let controlRows = 0; controlRows <= maximumControlRows; controlRows += 1) {
      if (meterRows + controlRows === 0) continue;
      if (needsBoth && (meterRows === 0 || controlRows === 0)) continue;
      const meterHiddenRows = Math.max(0, Math.floor(meterTotal) - meterRows);
      const controlHiddenRows = Math.max(0, Math.floor(controlTotal) - controlRows);
      const panelSize = companionSurfaceSize(controlRows, meterRows, scale, meterHiddenRows > 0);
      if (panelSize.height > maxPanelHeight) continue;
      const meterCoverage = maximumMeterRows > 0 ? meterRows / maximumMeterRows : 0;
      const controlCoverage = maximumControlRows > 0 ? controlRows / maximumControlRows : 0;
      const coverage = meterCoverage + controlCoverage;
      const balance = needsBoth ? Math.min(meterCoverage, controlCoverage) : coverage;
      const shown = meterRows + controlRows;
      const candidate = {
        meterRows, controlRows, meterHiddenRows, controlHiddenRows, panelSize,
        coverage, balance, shown,
      };
      if (!best || coverage > best.coverage + 1e-9 ||
        (Math.abs(coverage - best.coverage) < 1e-9 && balance > best.balance + 1e-9) ||
        (Math.abs(coverage - best.coverage) < 1e-9 && Math.abs(balance - best.balance) < 1e-9 && shown > best.shown) ||
        (Math.abs(coverage - best.coverage) < 1e-9 && Math.abs(balance - best.balance) < 1e-9 && shown === best.shown && controlRows > best.controlRows)) {
        best = candidate;
      }
    }
  }

  if (best) return best;
  // Every supported Windows work area fits at least one row from both sections.
  // Keep a deterministic fallback for unusual virtual displays or test harnesses.
  const meterRows = maximumMeterRows > 0 ? 1 : 0;
  const controlRows = maximumControlRows > 0 ? 1 : 0;
  const meterHiddenRows = Math.max(0, Math.floor(meterTotal) - meterRows);
  const controlHiddenRows = Math.max(0, Math.floor(controlTotal) - controlRows);
  return {
    meterRows,
    controlRows,
    meterHiddenRows,
    controlHiddenRows,
    panelSize: companionSurfaceSize(controlRows, meterRows, scale, meterHiddenRows > 0),
  };
}
