// Revenue engine — computes rooms, F&B, and other revenue across the hold period.
import { Assumptions } from './types';

export interface RevenueLine {
  year: number;
  roomsRevenue: number;
  fbRevenue: number;
  otherRevenue: number;
  totalRevenue: number;
}

export function projectRevenue(a: Assumptions): RevenueLine[] {
  const lines: RevenueLine[] = [];
  // Y1 baseline (post-PIP)
  const y1Rooms = a.y1Adr * a.y1Occupancy * 365 * a.keys;
  const y1Fb = a.y1FbRevenue;
  const y1Other = a.y1OtherRevenue;

  // Project through holdYears + 1 so we have terminal NOI for exit valuation.
  const totalYears = a.holdYears + 1;
  for (let y = 1; y <= totalYears; y++) {
    const growthFactor = Math.pow(1 + a.revparGrowth, y - 1);
    const rooms = y1Rooms * growthFactor;
    const fb = y1Fb * growthFactor;
    const other = y1Other * growthFactor;
    lines.push({
      year: y,
      roomsRevenue: rooms,
      fbRevenue: fb,
      otherRevenue: other,
      totalRevenue: rooms + fb + other,
    });
  }
  return lines;
}
