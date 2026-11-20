import type { ColumnDef } from "@tanstack/react-table";
import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { api } from "@/api";
import type { Bucket, ErrorCodeRow, LatencyResponse, LatencyRow, OutcomesResponse } from "@/api/types";
import { ChartCard } from "@/components/chart-card";
import { TimeSeriesChart } from "@/components/charts/time-series";
import { ColumnMeta, DataTable, SortableHeader } from "@/components/data-table";
import { EmptyState, ErrorNotice } from "@/components/empty-state";
import { Filterbar } from "@/components/filterbar";
import { KpiCard, KpiRow } from "@/components/kpi-card";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { ChartConfig, ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { fmtInt, fmtMs, fmtPct } from "@/lib/format";
import { alignSeries } from "@/lib/series";
import { useFilters, windowParams } from "@/lib/use-filters";
import { useQuery } from "@/lib/use-query";

interface Data {
  outcomes: OutcomesResponse;
  latency: LatencyResponse;
}

const errorConfig = { errors: { label: "Errors", color: "var(--status-crit)" } } satisfies ChartConfig;

/** The server's `name` filter applies to skill and tool rows alike, so a skill filter would
 * empty the tools tab: fetch skill latency for the selected skill and tool latency unfiltered. */
async function latencyFor(w: ReturnType<typeof windowParams>, bucket: Bucket, skill: string | null): Promise<LatencyResponse> {
  if (!skill) return api.analytics.latency({ ...w, bucket });
  const [sk, tools] = await Promise.all([
    api.analytics.latency({ ...w, bucket, kind: "skill", name: skill }),
    api.analytics.latency({ ...w, bucket, kind: "tool" }),
  ]);
  const merge = (a: LatencyResponse["current"] | null, b: LatencyResponse["current"] | null) =>
    a && b ? { ...a, series: [...a.series, ...b.series], by_kind: [...a.by_kind, ...b.by_kind], totals: { ...b.totals, ...a.totals } } : null;
  return {
    ...sk,
    current: merge(sk.current, tools.current)!,
    previous: merge(sk.previous, tools.previous),
    deltas: sk.deltas && tools.deltas ? { ...tools.deltas, ...sk.deltas } : null,
    approximate: sk.approximate || tools.approximate,
  };
}

export default function FailuresPage() {
  const f = useFilters();
  const navigate = useNavigate();
  const w = windowParams(f);
  const { data, error, loading } = useQuery<Data>(
    async () => {
      const [outcomes, latency] = await Promise.all([
        api.analytics.outcomes({ ...w, bucket: f.bucket, skill_name: f.skill ?? undefined }),
        latencyFor(w, f.bucket, f.skill),
      ]);
      return { outcomes, latency };
    },
    [f.range.from, f.range.to, f.compare, f.bucket, f.skill],
  );

  const comparing = data?.outcomes.previous != null;
  const skills = useMemo(() => [...new Set(data?.outcomes.current.series.map((r) => r.skill_name) ?? [])].sort(), [data]);

  const byCode = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of data?.outcomes.current.error_codes ?? []) m.set(r.error_code, (m.get(r.error_code) ?? 0) + r.errors);
    return [...m.entries()].map(([code, errors]) => ({ code, errors })).sort((a, b) => b.errors - a.errors).slice(0, 12);
  }, [data]);

  const toolFailures = useMemo<ToolRow[]>(() => {
    // Success / failure counts from tool_calls (outcomes route); p50/p95 from the latency view's tool rows.
    const lat = new Map<string, { samples: number; p50: number; p95: number }>();
    for (const r of data?.latency.current.series ?? []) {
      if (r.kind !== "tool") continue;
      const cur = lat.get(r.name) ?? { samples: 0, p50: 0, p95: 0 };
      cur.p50 = (cur.p50 * cur.samples + (r.p50 ?? 0) * r.samples) / (cur.samples + r.samples || 1);
      cur.p95 = (cur.p95 * cur.samples + (r.p95 ?? 0) * r.samples) / (cur.samples + r.samples || 1);
      cur.samples += r.samples;
      lat.set(r.name, cur);
    }
    const counts = new Map<string, ToolRow>();
    for (const t of data?.outcomes.current.tools ?? []) {
      const cur = counts.get(t.tool_name) ?? { name: t.tool_name, calls: 0, success: 0, failure: 0, interrupted: 0, failure_rate: null, p50: null, p95: null };
      cur.calls += t.calls;
      cur.success += t.success;
      cur.failure += t.failure;
      cur.interrupted += t.interrupted;
      counts.set(t.tool_name, cur);
    }
    for (const [name, l] of lat) if (!counts.has(name)) counts.set(name, { name, calls: l.samples, success: 0, failure: 0, interrupted: 0, failure_rate: null, p50: null, p95: null });
    return [...counts.values()].map((r) => {
      const l = lat.get(r.name);
      const known = r.success + r.failure;
      return { ...r, failure_rate: known ? r.failure / known : null, p50: l?.p50 ?? null, p95: l?.p95 ?? null };
    });
  }, [data]);

  const drill = (code: string) => navigate(`/engineering/inspector?from=${f.range.from}&to=${f.range.to}&status=failure&error_code=${encodeURIComponent(code)}${f.skill ? `&skill=${encodeURIComponent(f.skill)}` : ""}`);

  const errorCols = useMemo<ColumnDef<ErrorCodeRow, unknown>[]>(
    () => [
      { accessorKey: "error_code", header: ({ column }) => <SortableHeader column={column}>Error code</SortableHeader>, cell: ({ getValue }) => <code className="text-xs">{String(getValue())}</code> },
      { accessorKey: "skill_name", header: ({ column }) => <SortableHeader column={column}>Skill</SortableHeader> },
      { accessorKey: "errors", header: ({ column }) => <SortableHeader column={column}>Errors</SortableHeader>, cell: ({ getValue }) => fmtInt(getValue() as number), meta: { align: "right" } satisfies ColumnMeta },
      {
        id: "drill",
        header: "",
        cell: ({ row }) => (
          <Link
            className="text-xs underline-offset-2 hover:underline"
            to={`/engineering/inspector?from=${f.range.from}&to=${f.range.to}&status=failure&error_code=${encodeURIComponent(row.original.error_code)}&skill=${encodeURIComponent(row.original.skill_name)}`}
            onClick={(e) => e.stopPropagation()}
          >
            events →
          </Link>
        ),
      },
    ],
    [f.range.from, f.range.to],
  );

  return (
    <>
      <PageHeader title="Failure analysis" description="Error codes, tool failures and latency percentiles for the selected period; click a bar or row to open the matching events.">
        <Filterbar
          range={f.range}
          onRange={f.setRange}
          compare={f.compare}
          onCompare={(c) => f.update({ compare: c })}
          bucket={f.bucket}
          onBucket={(b) => f.update({ bucket: b })}
          dimensions={[{ key: "skill", label: "Skill", options: f.skill && !skills.includes(f.skill) ? [f.skill, ...skills] : skills, value: f.skill }]}
          onDimension={(key, value) => f.update({ [key]: value })}
        />
      </PageHeader>

      {error && <ErrorNotice error={error} what="failures" />}

      <KpiRow className="xl:grid-cols-5">
        <KpiCard label="Failures" value={data?.outcomes.current.totals.failure} previous={comparing ? data?.outcomes.previous?.totals.failure : undefined} invert loading={loading} hint={`${fmtInt(data?.outcomes.current.totals.errors)} with an error code`} />
        <KpiCard label="Failure rate" value={data && data.outcomes.current.totals.total ? data.outcomes.current.totals.failure / data.outcomes.current.totals.total : null} previous={comparing && data?.outcomes.previous?.totals.total ? data.outcomes.previous.totals.failure / data.outcomes.previous.totals.total : undefined} format={fmtPct} deltaMode="points" invert loading={loading} />
        <KpiCard label="Tool failures" value={data?.outcomes.current.totals.tool_failures} previous={comparing ? data?.outcomes.previous?.totals.tool_failures : undefined} invert loading={loading} hint={`of ${fmtInt(data?.outcomes.current.totals.tool_calls)} tool calls`} />
        <KpiCard label="Skill latency p95" value={data?.latency.current.totals.skill_p95 ?? null} previous={comparing ? data?.latency.previous?.totals.skill_p95 : undefined} format={fmtMs} invert loading={loading} hint={`p50 ${fmtMs(data?.latency.current.totals.skill_p50)}`} />
        <KpiCard label="Tool duration p95" value={data?.latency.current.totals.tool_p95 ?? null} previous={comparing ? data?.latency.previous?.totals.tool_p95 : undefined} format={fmtMs} invert loading={loading} hint={`p50 ${fmtMs(data?.latency.current.totals.tool_p50)} · ${fmtInt(data?.latency.current.totals.samples)} samples`} />
      </KpiRow>

      {data && (
        <>
          <div className="grid gap-4 lg:grid-cols-2">
            <ChartCard title="Failures over time" description="Failed skill outcomes per bucket; dashed = previous period.">
              <TimeSeriesChart id="failures" data={alignSeries(f.range, f.bucket, data.outcomes.current.series, data.outcomes.previous?.series ?? null, (r) => r.failure)} label="Failures" color="var(--status-crit)" />
            </ChartCard>
            <ChartCard title="Error codes" description="Errors by code in this period. Click a bar to open those events.">
              {byCode.length === 0 ? (
                <EmptyState compact title="No error codes in this period." />
              ) : (
                <ChartContainer config={errorConfig} className="aspect-auto w-full" style={{ height: Math.max(180, byCode.length * 32 + 30) }}>
                  <BarChart data={byCode} layout="vertical" accessibilityLayer margin={{ left: 8, right: 24 }}>
                    <CartesianGrid horizontal={false} />
                    <XAxis type="number" tickLine={false} axisLine={false} allowDecimals={false} />
                    <YAxis type="category" dataKey="code" width={150} tickLine={false} axisLine={false} />
                    <ChartTooltip cursor={{ fill: "var(--muted)", opacity: 0.5 }} content={<ChartTooltipContent />} />
                    <Bar dataKey="errors" fill="var(--color-errors)" radius={[0, 4, 4, 0]} maxBarSize={20} className="cursor-pointer" onClick={(d) => d && drill(String((d as { code?: string }).code ?? (d as { payload?: { code: string } }).payload?.code))} />
                  </BarChart>
                </ChartContainer>
              )}
            </ChartCard>
          </div>

          <Tabs defaultValue="errors">
            <ChartCard
              title="Breakdowns"
              description={data.latency.approximate ? "Weekly percentiles are sample-weighted means of daily values (approximate)." : "Error codes per skill, tool failures and latency percentiles."}
              action={
                <TabsList>
                  <TabsTrigger value="errors">Error codes</TabsTrigger>
                  <TabsTrigger value="tools">Tools</TabsTrigger>
                  <TabsTrigger value="latency">Latency</TabsTrigger>
                </TabsList>
              }
            >
              <TabsContent value="errors">
                <DataTable columns={errorCols} data={data.outcomes.current.error_codes} onRowClick={(r) => drill(r.error_code)} loading={loading} empty="No error codes in this period." />
              </TabsContent>
              <TabsContent value="tools">
                <DataTable
                  columns={toolCols}
                  data={toolFailures}
                  loading={loading}
                  empty="No tool calls in this period."
                  initialSorting={[{ id: "failure", desc: true }]}
                />
              </TabsContent>
              <TabsContent value="latency">
                <DataTable columns={latencyCols} data={aggregateLatency(data.latency.current.series)} loading={loading} empty="No latency samples in this period." initialSorting={[{ id: "p95", desc: true }]} />
              </TabsContent>
            </ChartCard>
          </Tabs>
        </>
      )}
    </>
  );
}

interface ToolRow {
  name: string;
  calls: number;
  success: number;
  failure: number;
  interrupted: number;
  failure_rate: number | null;
  p50: number | null;
  p95: number | null;
}

const toolCols: ColumnDef<ToolRow, unknown>[] = [
  { accessorKey: "name", header: ({ column }) => <SortableHeader column={column}>Tool</SortableHeader> },
  { accessorKey: "calls", header: ({ column }) => <SortableHeader column={column}>Calls</SortableHeader>, cell: ({ getValue }) => fmtInt(getValue() as number), meta: { align: "right" } satisfies ColumnMeta },
  { accessorKey: "success", header: ({ column }) => <SortableHeader column={column}>Succeeded</SortableHeader>, cell: ({ getValue }) => fmtInt(getValue() as number), meta: { align: "right" } satisfies ColumnMeta },
  { accessorKey: "failure", header: ({ column }) => <SortableHeader column={column}>Failed</SortableHeader>, cell: ({ getValue }) => fmtInt(getValue() as number), meta: { align: "right" } satisfies ColumnMeta },
  { accessorKey: "failure_rate", header: ({ column }) => <SortableHeader column={column}>Failure rate</SortableHeader>, cell: ({ getValue }) => fmtPct(getValue() as number | null), meta: { align: "right" } satisfies ColumnMeta },
  { accessorKey: "interrupted", header: ({ column }) => <SortableHeader column={column}>Interrupted</SortableHeader>, cell: ({ getValue }) => fmtInt(getValue() as number), meta: { align: "right" } satisfies ColumnMeta },
  { accessorKey: "p50", header: ({ column }) => <SortableHeader column={column}>p50</SortableHeader>, cell: ({ getValue }) => fmtMs(getValue() as number | null), meta: { align: "right" } satisfies ColumnMeta },
  { accessorKey: "p95", header: ({ column }) => <SortableHeader column={column}>p95</SortableHeader>, cell: ({ getValue }) => fmtMs(getValue() as number | null), meta: { align: "right" } satisfies ColumnMeta },
];

interface LatencyAgg {
  kind: string;
  name: string;
  samples: number;
  p50: number;
  p95: number;
  p99: number;
}

function aggregateLatency(rows: LatencyRow[]): LatencyAgg[] {
  const m = new Map<string, LatencyAgg>();
  for (const r of rows) {
    const key = `${r.kind}:${r.name}`;
    const cur = m.get(key) ?? { kind: r.kind, name: r.name, samples: 0, p50: 0, p95: 0, p99: 0 };
    const n = cur.samples + r.samples || 1;
    cur.p50 = (cur.p50 * cur.samples + (r.p50 ?? 0) * r.samples) / n;
    cur.p95 = (cur.p95 * cur.samples + (r.p95 ?? 0) * r.samples) / n;
    cur.p99 = (cur.p99 * cur.samples + (r.p99 ?? 0) * r.samples) / n;
    cur.samples += r.samples;
    m.set(key, cur);
  }
  return [...m.values()];
}

const latencyCols: ColumnDef<LatencyAgg, unknown>[] = [
  { accessorKey: "kind", header: "Kind", cell: ({ getValue }) => <Badge variant="secondary">{String(getValue())}</Badge> },
  { accessorKey: "name", header: ({ column }) => <SortableHeader column={column}>Name</SortableHeader> },
  { accessorKey: "samples", header: ({ column }) => <SortableHeader column={column}>Samples</SortableHeader>, cell: ({ getValue }) => fmtInt(getValue() as number), meta: { align: "right" } satisfies ColumnMeta },
  { accessorKey: "p50", header: ({ column }) => <SortableHeader column={column}>p50</SortableHeader>, cell: ({ getValue }) => fmtMs(getValue() as number), meta: { align: "right" } satisfies ColumnMeta },
  { accessorKey: "p95", header: ({ column }) => <SortableHeader column={column}>p95</SortableHeader>, cell: ({ getValue }) => fmtMs(getValue() as number), meta: { align: "right" } satisfies ColumnMeta },
  { accessorKey: "p99", header: ({ column }) => <SortableHeader column={column}>p99</SortableHeader>, cell: ({ getValue }) => fmtMs(getValue() as number), meta: { align: "right" } satisfies ColumnMeta },
];
