import type { RedactionMarker, Sensitive } from "@/api/types";

export const fmtInt = (n: number | null | undefined) => (n == null ? "—" : Math.round(n).toLocaleString());
export const fmtNum = (n: number | null | undefined, digits = 2) =>
  n == null ? "—" : n.toLocaleString(undefined, { maximumFractionDigits: digits });
export const fmtPct = (x: number | null | undefined, digits?: number) =>
  x == null ? "—" : `${(x * 100).toFixed(digits ?? (x < 0.1 && x > 0 ? 1 : 0))}%`;
export const fmtUsd = (n: number | null | undefined) =>
  n == null
    ? "—"
    : n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: n < 10 ? 2 : 0 });
export const fmtTokens = (n: number | null | undefined) => {
  if (n == null) return "—";
  if (n >= 1e6) return `${(n / 1e6).toFixed(n < 1e7 ? 1 : 0)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(n < 1e4 ? 1 : 0)}k`;
  return Math.round(n).toLocaleString();
};
export const fmtMs = (n: number | null | undefined) => {
  if (n == null) return "—";
  if (n >= 60_000) return `${(n / 60_000).toFixed(1)} min`;
  if (n >= 1000) return `${(n / 1000).toFixed(n < 10_000 ? 2 : 1)} s`;
  return `${Math.round(n)} ms`;
};
export const fmtScore = (x: number | null | undefined) => (x == null ? "—" : `${x >= 0 ? "+" : ""}${x.toFixed(2)}`);
export const fmtDateTime = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";
export const fmtTime = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleTimeString(undefined, { timeStyle: "medium" }) : "—";
export const fmtDuration = (start: string | null | undefined, end: string | null | undefined) =>
  start && end ? fmtMs(new Date(end).getTime() - new Date(start).getTime()) : "—";
export const shortId = (id: string | null | undefined, n = 8) => (id ? id.slice(0, n) : "—");

export const isRedacted = (v: unknown): v is RedactionMarker =>
  typeof v === "object" && v !== null && (v as RedactionMarker).redacted === true;

/** Length of a sensitive field whether verbatim or redacted. */
export const sensitiveLength = (v: Sensitive | undefined): number | null =>
  v == null ? null : isRedacted(v) ? v.length : v.length;
