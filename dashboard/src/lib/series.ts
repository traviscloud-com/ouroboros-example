import type { Bucket } from "@/api/types";
import { DayRange, fillBuckets, previousRange } from "./period";

export interface AlignedPoint {
  day: string;
  current: number;
  previous: number | null;
  previousDay: string | null;
}

/** Current and previous window aligned by bucket index (the API omits empty buckets). */
export function alignSeries<T extends { bucket: string }>(
  range: DayRange,
  bucket: Bucket,
  current: T[],
  previous: T[] | null,
  pick: (row: T) => number,
): AlignedPoint[] {
  const cur = fillBuckets(range, current, bucket, pick);
  const prev = previous ? fillBuckets(previousRange(range), previous, bucket, pick) : null;
  return cur.map((c, i) => ({
    day: c.day,
    current: c.value,
    previous: prev ? (prev[i]?.value ?? 0) : null,
    previousDay: prev ? (prev[i]?.day ?? null) : null,
  }));
}

/** Weighted mean per bucket (e.g. sentiment weighted by event_count); null where there is no weight. */
export function meanSeries<T extends { bucket: string }>(
  range: DayRange,
  bucket: Bucket,
  rows: T[],
  value: (row: T) => number | null | undefined,
  weight: (row: T) => number,
): { day: string; value: number | null }[] {
  const sum = fillBuckets(range, rows, bucket, (r) => (value(r) ?? 0) * weight(r));
  const w = fillBuckets(range, rows, bucket, (r) => (value(r) == null ? 0 : weight(r)));
  return sum.map((s, i) => ({ day: s.day, value: w[i].value ? s.value / w[i].value : null }));
}

export interface StackedRow {
  day: string;
  [key: string]: number | string;
}

/** Pivot rows into one object per bucket keyed by `group`; the top `limit` groups by total keep
 * their own key (fixed categorical order), the rest fold into "Other" (dataviz: never cycle hues). */
export function pivotStacked<T extends { bucket: string }>(
  range: DayRange,
  bucket: Bucket,
  rows: T[],
  group: (row: T) => string,
  pick: (row: T) => number,
  limit = 5,
): { data: StackedRow[]; keys: string[] } {
  const totals = new Map<string, number>();
  for (const r of rows) totals.set(group(r), (totals.get(group(r)) ?? 0) + pick(r));
  const ranked = [...totals.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
  const keep = ranked.slice(0, limit);
  const hasOther = ranked.length > limit;
  const keys = hasOther ? [...keep, "Other"] : keep;
  const keyOf = (k: string) => (keep.includes(k) ? k : "Other");
  const byKey = new Map<string, { day: string; value: number }[]>();
  for (const key of keys) {
    byKey.set(
      key,
      fillBuckets(
        range,
        rows.filter((r) => keyOf(group(r)) === key),
        bucket,
        pick,
      ),
    );
  }
  const first = byKey.get(keys[0]);
  const days = first ? first.map((d) => d.day) : fillBuckets(range, [], bucket, () => 0).map((d) => d.day);
  const data = days.map((day, i) => {
    const row: StackedRow = { day };
    for (const key of keys) row[key] = byKey.get(key)?.[i]?.value ?? 0;
    return row;
  });
  return { data, keys };
}

export const CHART_SLOTS = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)"];
export const slotColor = (i: number, key?: string) => (key === "Other" ? "var(--chart-previous)" : CHART_SLOTS[i % CHART_SLOTS.length]);
