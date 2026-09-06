export const COMPANION_MAX_METER_ROWS = 6;
export const COMPANION_MAX_CONTROL_ROWS = 6;

const COMPANION_WIDTH = 340;
const CONTROL_HEADER_HEIGHT = 31;
const CONTROL_ROW_HEIGHT = 48;
const METER_HEADER_HEIGHT = 34;
const METER_ROW_HEIGHT = 42;
const METER_FOOTER_HEIGHT = 19;
const METER_DETAIL_CHROME_HEIGHT = 70;
const METER_STANDALONE_CHROME_HEIGHT = 32;
const METER_STANDALONE_IDLE_HEIGHT = 134;
const SINGLE_SECTION_BORDER_HEIGHT = 2;
const JOINED_SECTION_BORDER_HEIGHT = 3;

export interface CompanionLayout {
  meterRows: number;
  controlRows: number;
  meterHiddenRows: number;
  controlHiddenRows: number;
  panelSize: { width: number; height: number };
}

export type CompanionPlacement = "auto" | "above" | "right";
export type CompanionSide = "above" | "right" | "left" | "below";

export interface CompanionRectangle {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface CompanionPlacementResult {
  x: number;
  y: number;
  side: CompanionSide;
}

function scaled(value: number, scale: number): number {
  const factor = Math.max(0.9, Math.min(1.6, scale));
  return Math.round(value * factor);
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}

export function companionChromeHeight(
  scale: number,
  detailActive: boolean,
  standalone: boolean,
): number {
  return scaled(
    (detailActive ? METER_DETAIL_CHROME_HEIGHT : 0) +
      (standalone ? METER_STANDALONE_CHROME_HEIGHT : 0),
    scale,
  );
}

export function decoratedCompanionSize(
  panelSize: { width: number; height: number },
  scale: number,
  detailActive: boolean,
  standalone: boolean,
  idle: boolean,
): { width: number; height: number } {
  const decoratedHeight = panelSize.height + companionChromeHeight(scale, detailActive, standalone);
  return {
    width: panelSize.width,
    height: standalone && idle
      ? Math.max(decoratedHeight, scaled(METER_STANDALONE_IDLE_HEIGHT, scale))
      : decoratedHeight,
  };
}

export function availableCompanionHeight(
  anchor: CompanionRectangle,
  panelWidth: number,
  workArea: CompanionRectangle,
  gap: number,
): number {
  const spaceRight = workArea.x + workArea.width - (anchor.x + anchor.width);
  const spaceLeft = anchor.x - workArea.x;
  if (Math.max(spaceRight, spaceLeft) >= panelWidth + gap) {
    return Math.max(1, workArea.height - gap * 2);
  }
  const spaceAbove = anchor.y - workArea.y;
  const spaceBelow = workArea.y + workArea.height - (anchor.y + anchor.height);
  return Math.max(1, Math.max(spaceAbove, spaceBelow) - gap);
}

export function placeCompanionSurface(
  anchor: CompanionRectangle,
  panel: { width: number; height: number },
  workArea: CompanionRectangle,
  gap: number,
  placement: CompanionPlacement,
): CompanionPlacementResult {
  const spaceAbove = anchor.y - workArea.y;
  const spaceBelow = workArea.y + workArea.height - (anchor.y + anchor.height);
  const spaceRight = workArea.x + workArea.width - (anchor.x + anchor.width);
  const spaceLeft = anchor.x - workArea.x;
  const fits = {
    above: spaceAbove >= panel.height + gap,
    below: spaceBelow >= panel.height + gap,
    right: spaceRight >= panel.width + gap,
    left: spaceLeft >= panel.width + gap,
  };

  let side: CompanionSide;
  if (placement === "above") {
    side = fits.above ? "above" : fits.below ? "below"
      : fits.right ? "right" : fits.left ? "left" : spaceAbove >= spaceBelow ? "above" : "below";
  } else if (placement === "right") {
    side = fits.right ? "right" : fits.left ? "left"
      : fits.above ? "above" : fits.below ? "below" : spaceRight >= spaceLeft ? "right" : "left";
  } else {
    side = fits.above ? "above" : fits.right ? "right"
      : fits.left ? "left" : "below";
  }

  let x = anchor.x + Math.round((anchor.width - panel.width) / 2);
  let y = anchor.y - panel.height - gap;
  if (side === "right") {
    x = anchor.x + anchor.width + gap;
    y = anchor.y + Math.round((anchor.height - panel.height) / 2);
  } else if (side === "left") {
    x = anchor.x - panel.width - gap;
    y = anchor.y + Math.round((anchor.height - panel.height) / 2);
  } else if (side === "below") {
    y = anchor.y + anchor.height + gap;
  }

  return {
    x: clamp(x, workArea.x, Math.max(workArea.x, workArea.x + workArea.width - panel.width)),
    y: clamp(y, workArea.y, Math.max(workArea.y, workArea.y + workArea.height - panel.height)),
    side,
  };
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
  meterChromeHeight = 0,
): CompanionLayout {
  const maximumMeterRows = Math.min(COMPANION_MAX_METER_ROWS, Math.max(0, Math.floor(meterTotal)));
  const maximumControlRows = Math.min(COMPANION_MAX_CONTROL_ROWS, Math.max(0, Math.floor(controlTotal)));
  const needsBoth = maximumMeterRows > 0 && maximumControlRows > 0;
  let best: CompanionLayout | null = null;

  for (let meterRows = 0; meterRows <= maximumMeterRows; meterRows += 1) {
    for (let controlRows = 0; controlRows <= maximumControlRows; controlRows += 1) {
      if (meterRows + controlRows === 0) continue;
      if (needsBoth && (meterRows === 0 || controlRows === 0)) continue;
      const meterHiddenRows = Math.max(0, Math.floor(meterTotal) - meterRows);
      const controlHiddenRows = Math.max(0, Math.floor(controlTotal) - controlRows);
      const panelSize = companionSurfaceSize(controlRows, meterRows, scale, meterHiddenRows > 0);
      if (panelSize.height + (meterRows > 0 ? meterChromeHeight : 0) > maxPanelHeight) continue;
      const candidate = {
        meterRows, controlRows, meterHiddenRows, controlHiddenRows, panelSize,
      };
      // Live control timers are time-sensitive. Additional damage contributors
      // can be paged; they must not evict mez/lull rows as a new fight fills up.
      if (!best || controlRows > best.controlRows ||
        (controlRows === best.controlRows && meterRows > best.meterRows)) {
        best = candidate;
      }
    }
  }

  if (best) return best;
  if (needsBoth) {
    // If both sections cannot fit, retain control rather than silently hiding
    // every timer behind the damage meter. Meter-only chrome no longer applies.
    const controlsOnly = boundedCompanionLayout(0, controlTotal, scale, maxPanelHeight);
    return { ...controlsOnly, meterHiddenRows: Math.max(0, Math.floor(meterTotal)) };
  }
  // Keep a deterministic minimum for unusual virtual displays where even one
  // priority row cannot fit the requested slot.
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
