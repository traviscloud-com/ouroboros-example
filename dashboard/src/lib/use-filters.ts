import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import type { Bucket } from "@/api/types";
import { DAY_RE, DayRange, lastNDays } from "./period";

export const DEFAULT_DAYS_KEY = "ouroboros-default-days"; // /settings/general preference
const DEFAULT_DAYS = 30;

export type Compare = "previous" | "none";

export interface Filters {
  range: DayRange;
  compare: Compare;
  bucket: Bucket;
  skill: string | null;
  agent: string | null;
  user: string | null;
}

export function defaultDays(): number {
  try {
    const n = Number(localStorage.getItem(DEFAULT_DAYS_KEY));
    if ([7, 14, 30, 90].includes(n)) return n;
  } catch {
    /* storage unavailable */
  }
  return DEFAULT_DAYS;
}

/** Filter state lives in the URL (D4): `?from&to&compare&bucket&skill&agent&user`.
 * Edits use `replace: true` so the back button steps between pages, not tweaks. */
export function useFilters() {
  const [params, setParams] = useSearchParams();
  const from = params.get("from");
  const to = params.get("to");
  const compareParam = params.get("compare");
  const bucketParam = params.get("bucket");
  const skill = params.get("skill");
  const agent = params.get("agent");
  const user = params.get("user");

  const filters = useMemo<Filters>(() => {
    const range: DayRange =
      from && to && DAY_RE.test(from) && DAY_RE.test(to) && from <= to ? { from, to } : lastNDays(defaultDays());
    return {
      range,
      compare: compareParam === "none" ? "none" : "previous",
      bucket: bucketParam === "week" ? "week" : "day",
      skill: skill || null,
      agent: agent || null,
      user: user || null,
    };
  }, [from, to, compareParam, bucketParam, skill, agent, user]);

  const update = useCallback(
    (patch: Partial<{ range: DayRange; compare: Compare; bucket: Bucket; skill: string | null; agent: string | null; user: string | null }>) =>
      setParams(
        (prev) => {
          const p = new URLSearchParams(prev);
          if (patch.range) {
            p.set("from", patch.range.from);
            p.set("to", patch.range.to);
          }
          const setOrDelete = (key: string, value: string | null | undefined, dflt: string) => {
            if (value === undefined) return;
            if (!value || value === dflt) p.delete(key);
            else p.set(key, value);
          };
          setOrDelete("compare", patch.compare, "previous");
          setOrDelete("bucket", patch.bucket, "day");
          setOrDelete("skill", patch.skill, "");
          setOrDelete("agent", patch.agent, "");
          setOrDelete("user", patch.user, "");
          return p;
        },
        { replace: true },
      ),
    [setParams],
  );

  const setRange = useCallback((range: DayRange) => update({ range }), [update]);

  /** Query-string fragment that carries the current filters to another page. */
  const search = useMemo(() => {
    const p = new URLSearchParams();
    p.set("from", filters.range.from);
    p.set("to", filters.range.to);
    if (filters.compare !== "previous") p.set("compare", filters.compare);
    if (filters.bucket !== "day") p.set("bucket", filters.bucket);
    if (filters.skill) p.set("skill", filters.skill);
    if (filters.agent) p.set("agent", filters.agent);
    if (filters.user) p.set("user", filters.user);
    return `?${p}`;
  }, [filters]);

  return { ...filters, filters, setRange, update, search };
}

/** Analytics query params from the filters (`compare_to` only when comparing). */
export function windowParams(f: Pick<Filters, "range" | "compare">) {
  return { from: f.range.from, to: f.range.to, compare_to: f.compare === "previous" ? ("previous" as const) : undefined };
}
