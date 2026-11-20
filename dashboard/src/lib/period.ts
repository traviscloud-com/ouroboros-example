/** Date-range helpers. Ranges are inclusive UTC calendar days ("YYYY-MM-DD"). */
export interface DayRange {
  from: string;
  to: string;
}

const DAY_MS = 86_400_000;
export const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

export const isoDay = (d: Date): string => d.toISOString().slice(0, 10);
export const utcDay = (day: string): Date => new Date(`${day}T00:00:00Z`);

export function addDays(day: string, n: number): string {
  const d = utcDay(day);
  d.setUTCDate(d.getUTCDate() + n);
  return isoDay(d);
}

export function rangeDays(r: DayRange): number {
  return Math.round((utcDay(r.to).getTime() - utcDay(r.from).getTime()) / DAY_MS) + 1;
}

/** The range of equal length ending the day before `r` starts. */
export function previousRange(r: DayRange): DayRange {
  const n = rangeDays(r);
  return { from: addDays(r.from, -n), to: addDays(r.from, -1) };
}

export function lastNDays(n: number, today: Date = new Date()): DayRange {
  const to = isoDay(today);
  return { from: addDays(to, -(n - 1)), to };
}

export const sameRange = (a: DayRange, b: DayRange) => a.from === b.from && a.to === b.to;

/** `/v1/reports/*` filter on `client_ts >= from AND client_ts <= to`. */
export function toApiParams(r: DayRange): { from: string; to: string } {
  return { from: `${r.from}T00:00:00Z`, to: `${r.to}T23:59:59.999Z` };
}

/** Every day of the range, oldest first. */
export function eachDay(r: DayRange): string[] {
  const out: string[] = [];
  for (let d = r.from; d <= r.to; d = addDays(d, 1)) out.push(d);
  return out;
}

/** Monday-start weeks (Postgres `date_trunc('week')`) covering the range. */
export function eachWeek(r: DayRange): string[] {
  const out: string[] = [];
  const start = utcDay(r.from);
  const dow = (start.getUTCDay() + 6) % 7; // Monday = 0
  start.setUTCDate(start.getUTCDate() - dow);
  for (let d = isoDay(start); d <= r.to; d = addDays(d, 7)) out.push(d);
  return out;
}

/** Bucket key of an ISO timestamp or day string, as a UTC day. */
export const bucketDay = (bucket: string): string => (DAY_RE.test(bucket) ? bucket : isoDay(new Date(bucket)));

/** The API omits empty buckets; fill every day (or week) of the range so two periods align by index. */
export function fillBuckets<T extends { bucket: string }>(
  r: DayRange,
  rows: T[],
  bucket: "day" | "week",
  pick: (row: T) => number,
): { day: string; value: number }[] {
  const byDay = new Map<string, number>();
  for (const row of rows) {
    const key = bucketDay(row.bucket);
    byDay.set(key, (byDay.get(key) ?? 0) + pick(row));
  }
  return (bucket === "week" ? eachWeek(r) : eachDay(r)).map((day) => ({ day, value: byDay.get(day) ?? 0 }));
}

export function fillDays(r: DayRange, buckets: { bucket: string; count: number }[]): { day: string; count: number }[] {
  return fillBuckets(r, buckets, "day", (b) => b.count).map((d) => ({ day: d.day, count: d.value }));
}

/** Relative change; null when there is no baseline to compare against. */
export function pctChange(current: number, previous: number): number | null {
  if (!previous) return current ? null : 0;
  return (current - previous) / previous;
}

export const fmtDay = (day: string) =>
  utcDay(day).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
export const fmtRange = (r: DayRange) => `${fmtDay(r.from)} – ${fmtDay(r.to)}`;
