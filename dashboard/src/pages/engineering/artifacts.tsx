import type { ColumnDef } from "@tanstack/react-table";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/api";
import type { ArtifactKind, ArtifactRow } from "@/api/types";
import { ColumnMeta, DataTable, SortableHeader } from "@/components/data-table";
import { ErrorNotice } from "@/components/empty-state";
import { Filterbar } from "@/components/filterbar";
import { KpiCard, KpiRow } from "@/components/kpi-card";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { fmtInt, fmtPct, fmtTokens, fmtUsd } from "@/lib/format";
import { useFilters, windowParams } from "@/lib/use-filters";
import { useQuery } from "@/lib/use-query";

const KINDS: ArtifactKind[] = ["skill", "hook", "agent", "command", "instructions", "rule", "plugin"];
const KIND_ITEMS = [{ value: "all", label: "All kinds" }, ...KINDS.map((k) => ({ value: k, label: k }))];

export default function ArtifactsPage() {
  const f = useFilters();
  const navigate = useNavigate();
  const [kind, setKind] = useState<string>("all");
  const [q, setQ] = useState("");
  const [neverOnly, setNeverOnly] = useState(false);
  const w = windowParams({ range: f.range, compare: "none" });
  const { data, error, loading } = useQuery(
    () =>
      api.analytics.artifacts({
        ...w,
        kind: kind === "all" ? undefined : (kind as ArtifactKind),
        q: q || undefined,
        never_invoked: neverOnly ? true : undefined,
        order: "invocations",
        limit: 200,
      }),
    [f.range.from, f.range.to, kind, q, neverOnly],
  );

  const rows = data?.artifacts ?? [];
  const partial = data != null && data.total > rows.length;
  /* Summed over the rows returned (capped at the page size); the hint says so when truncated. */
  const totals = useMemo(() => {
    const never = rows.filter((r) => r.never_invoked).length;
    const alwaysOn = rows.reduce((s, r) => s + (r.always_on_tokens_total ?? 0), 0);
    const cost = rows.reduce((s, r) => s + (r.cost_usd ?? 0), 0);
    return { never, alwaysOn, cost };
  }, [rows]);

  const columns = useMemo<ColumnDef<ArtifactRow, unknown>[]>(
    () => [
      {
        accessorKey: "name",
        header: ({ column }) => <SortableHeader column={column}>Artifact</SortableHeader>,
        cell: ({ row }) => (
          <div className="min-w-0">
            <div className="flex items-center gap-2 font-medium">
              <span className="truncate">{row.original.name}</span>
              {row.original.never_invoked && (
                <Badge variant="outline" className="text-status-warn">
                  never invoked
                </Badge>
              )}
            </div>
            <div className="truncate text-xs text-muted-foreground">
              {row.original.owner_repo}
              {row.original.path ? ` · ${row.original.path}` : ""}
            </div>
          </div>
        ),
      },
      { accessorKey: "kind", header: "Kind", cell: ({ getValue }) => <Badge variant="secondary">{String(getValue())}</Badge> },
      {
        accessorKey: "estimated_tokens_always_on",
        header: ({ column }) => <SortableHeader column={column}>Always-on tokens</SortableHeader>,
        cell: ({ row }) => (
          <span title={`${fmtInt(row.original.always_on_tokens_total)} across ${fmtInt(row.original.sessions_loaded)} sessions`}>
            {fmtTokens(row.original.estimated_tokens_always_on)}
          </span>
        ),
        meta: { align: "right" } satisfies ColumnMeta,
      },
      {
        accessorKey: "invocations",
        header: ({ column }) => <SortableHeader column={column}>Invocations</SortableHeader>,
        cell: ({ getValue }) => fmtInt(getValue() as number),
        meta: { align: "right" } satisfies ColumnMeta,
      },
      {
        id: "outcomes",
        accessorFn: (r) => r.outcomes,
        header: ({ column }) => <SortableHeader column={column}>Outcomes</SortableHeader>,
        cell: ({ row }) => <OutcomeCell r={row.original} />,
        meta: { align: "right" } satisfies ColumnMeta,
      },
      {
        accessorKey: "correction_rate",
        header: ({ column }) => <SortableHeader column={column}>Correction rate</SortableHeader>,
        cell: ({ getValue }) => fmtPct(getValue() as number | null),
        meta: { align: "right" } satisfies ColumnMeta,
      },
      {
        accessorKey: "cost_per_invocation",
        header: ({ column }) => <SortableHeader column={column}>Cost / invocation</SortableHeader>,
        cell: ({ row }) => (
          <span title={`${fmtUsd(row.original.cost_usd)} total`}>{fmtUsd(row.original.cost_per_invocation)}</span>
        ),
        meta: { align: "right" } satisfies ColumnMeta,
      },
      {
        accessorKey: "version_count",
        header: ({ column }) => <SortableHeader column={column}>Versions</SortableHeader>,
        cell: ({ row }) => (
          <span>
            {fmtInt(row.original.version_count)}
            {!row.original.synced && <span className="ml-1 text-xs text-muted-foreground">(not synced)</span>}
          </span>
        ),
        meta: { align: "right" } satisfies ColumnMeta,
      },
    ],
    [],
  );

  return (
    <>
      <PageHeader title="Artifact registry" description="Skills, agents, hooks, commands and instruction files seen by the collectors, with attribution for the selected period.">
        <Filterbar range={f.range} onRange={f.setRange} />
      </PageHeader>

      <KpiRow>
        <KpiCard label="Artifacts" value={data?.total} loading={loading} hint={`${rows.length} shown`} />
        <KpiCard label="Never invoked" value={totals.never} loading={loading} hint={partial ? `in the ${rows.length} rows shown` : "loaded in sessions, zero invocations"} />
        <KpiCard label="Always-on tokens" value={totals.alwaysOn} format={fmtTokens} loading={loading} hint={partial ? `in the ${rows.length} rows shown` : "sum over sessions that loaded them"} />
        <KpiCard label="Attributed cost" value={totals.cost} format={fmtUsd} loading={loading} hint={partial ? `in the ${rows.length} rows shown` : "API cost by skill / agent name"} />
      </KpiRow>

      <div className="flex flex-wrap items-center gap-2">
        <Input placeholder="Search name, path, repo" value={q} onChange={(e) => setQ(e.target.value)} className="w-64" aria-label="Search artifacts" />
        <Select value={kind} onValueChange={(v) => v && setKind(v)} items={KIND_ITEMS}>
          <SelectTrigger className="w-40" aria-label="Kind">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {KIND_ITEMS.map((i) => (
              <SelectItem key={i.value} value={i.value}>
                {i.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="flex items-center gap-2">
          <Switch id="never" checked={neverOnly} onCheckedChange={(v) => setNeverOnly(Boolean(v))} />
          <Label htmlFor="never">Never invoked only</Label>
        </div>
      </div>

      {error && <ErrorNotice error={error} what="artifacts" />}
      <DataTable
        columns={columns}
        data={rows}
        getRowId={(r) => r.artifact_id}
        onRowClick={(r) => navigate(`/engineering/artifacts/${r.artifact_id}${f.search}`)}
        loading={loading}
        empty={loading ? "Loading…" : "No artifacts match."}
      />
    </>
  );
}

function OutcomeCell({ r }: { r: ArtifactRow }) {
  if (!r.outcomes) return <span className="text-muted-foreground">—</span>;
  return (
    <span className="inline-flex items-center gap-1.5 tabular-nums" title={`${r.success} success · ${r.user_corrected} corrected · ${r.failure} failure`}>
      <span className="text-status-good">{r.success}</span>/<span className="text-status-warn">{r.user_corrected}</span>/
      <span className="text-status-crit">{r.failure}</span>
    </span>
  );
}
