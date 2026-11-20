import type { ColumnDef } from "@tanstack/react-table";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/api";
import type { SessionListRow, SessionOrder, SessionsResponse } from "@/api/types";
import { ColumnMeta, DataTable } from "@/components/data-table";
import { ErrorNotice } from "@/components/empty-state";
import { Filterbar } from "@/components/filterbar";
import { KpiCard, KpiRow } from "@/components/kpi-card";
import { PageHeader } from "@/components/page-header";
import { SensitiveText } from "@/components/redacted";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { fmtDateTime, fmtInt, fmtTokens, fmtUsd, shortId } from "@/lib/format";
import { useFilters, windowParams } from "@/lib/use-filters";
import { useQuery } from "@/lib/use-query";

const PAGE_SIZE = 50;

const ORDER_ITEMS: { value: SessionOrder; label: string }[] = [
  { value: "started", label: "Newest first" },
  { value: "cost", label: "Costliest" },
  { value: "turns", label: "Most turns" },
  { value: "corrections", label: "Most corrections" },
  { value: "tokens", label: "Most tokens" },
];

/** Paginated session list from `GET /v1/analytics/sessions` (ordering and paging are server-side). */
export default function SessionsPage() {
  const f = useFilters();
  const navigate = useNavigate();
  const [jump, setJump] = useState("");
  const [order, setOrder] = useState<SessionOrder>("started");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const w = windowParams({ range: f.range, compare: "none" });
  const { data, error, loading } = useQuery<SessionsResponse>(
    () => api.analytics.sessions({ from: w.from, to: w.to, order, q: q.trim() || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    [f.range.from, f.range.to, order, q, page],
  );
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  const columns = useMemo<ColumnDef<SessionListRow, unknown>[]>(
    () => [
      { accessorKey: "session_id", header: "Session", cell: ({ getValue }) => <code className="text-xs">{shortId(String(getValue()), 13)}</code> },
      { accessorKey: "started_at", header: "Started", cell: ({ getValue }) => <span className="whitespace-nowrap text-xs">{fmtDateTime(getValue() as string | null)}</span> },
      { accessorKey: "project_key", header: "Project", cell: ({ getValue }) => <span className="text-xs">{(getValue() as string | null) ?? "—"}</span> },
      { id: "title", header: "Title", cell: ({ row }) => <SensitiveText className="text-xs" value={row.original.title_redacted} clamp={60} /> },
      { accessorKey: "turns", header: "Turns", cell: ({ getValue }) => fmtInt(getValue() as number), meta: { align: "right" } satisfies ColumnMeta },
      { id: "tools", header: "Tool calls", cell: ({ row }) => `${fmtInt(row.original.tool_calls)}${row.original.tool_failures ? ` (${row.original.tool_failures} failed)` : ""}`, meta: { align: "right" } satisfies ColumnMeta },
      { accessorKey: "compactions", header: "Compactions", cell: ({ getValue }) => fmtInt(getValue() as number), meta: { align: "right" } satisfies ColumnMeta },
      { accessorKey: "tokens", header: "Tokens", cell: ({ getValue }) => fmtTokens(getValue() as number | null), meta: { align: "right" } satisfies ColumnMeta },
      { accessorKey: "cost_usd", header: "Cost", cell: ({ getValue }) => fmtUsd(getValue() as number | null), meta: { align: "right" } satisfies ColumnMeta },
      { id: "outcomes", header: "Outcomes", cell: ({ row }) => (row.original.outcomes ? `${row.original.outcomes} (${row.original.user_corrected} corrected, ${row.original.failures} failed)` : "—"), meta: { align: "right" } satisfies ColumnMeta },
    ],
    [],
  );

  return (
    <>
      <PageHeader title="Sessions" description="Every session started in the selected period, with turns, tool calls, compactions, cost and skill outcomes. Open any session for its full timeline.">
        <Filterbar
          range={f.range}
          onRange={(r) => {
            setPage(0);
            f.setRange(r);
          }}
        />
      </PageHeader>

      <KpiRow>
        <KpiCard label="Sessions" value={data?.totals.sessions} loading={loading} />
        <KpiCard label="Mean session cost" value={data?.totals.mean_session_cost_usd ?? null} format={fmtUsd} loading={loading} />
        <KpiCard label="Turns" value={data?.totals.turns} loading={loading} />
        <KpiCard label="Corrected outcomes" value={data?.totals.user_corrected} loading={loading} hint={`of ${fmtInt(data?.totals.outcomes)} outcomes`} />
      </KpiRow>

      <div className="flex flex-wrap items-center gap-2">
        <Input
          className="w-64 text-xs"
          placeholder="Filter by session id prefix or project"
          value={q}
          onChange={(e) => {
            setPage(0);
            setQ(e.target.value);
          }}
          aria-label="Filter sessions"
        />
        <Select
          value={order}
          onValueChange={(v) => {
            if (v) {
              setPage(0);
              setOrder(v as SessionOrder);
            }
          }}
          items={ORDER_ITEMS}
        >
          <SelectTrigger className="w-44" aria-label="Order">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ORDER_ITEMS.map((i) => (
              <SelectItem key={i.value} value={i.value}>
                {i.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <form
          className="flex items-center gap-2 sm:ml-auto"
          onSubmit={(e) => {
            e.preventDefault();
            if (jump.trim()) navigate(`/engineering/sessions/${encodeURIComponent(jump.trim())}`);
          }}
        >
          <Input className="w-72 font-mono text-xs" placeholder="Open a session by id (UUID)" value={jump} onChange={(e) => setJump(e.target.value)} aria-label="Session id" />
          <Button type="submit" variant="outline">
            Open
          </Button>
        </form>
      </div>

      {error && <ErrorNotice error={error} what="sessions" />}
      <DataTable columns={columns} data={data?.sessions ?? []} getRowId={(r) => r.session_id} onRowClick={(r) => navigate(`/engineering/sessions/${r.session_id}`)} loading={loading} empty={loading ? "Loading…" : "No sessions in this period."} />
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
        <span data-testid="sessions-range">{data ? `${fmtInt(data.total === 0 ? 0 : data.offset + 1)}–${fmtInt(data.offset + data.sessions.length)} of ${fmtInt(data.total)}` : "—"}</span>
        <span className="flex items-center gap-2">
          <Button variant="outline" size="sm" disabled={page === 0 || loading} onClick={() => setPage((p) => p - 1)}>
            Previous
          </Button>
          <span>
            Page {page + 1} of {pages}
          </span>
          <Button variant="outline" size="sm" disabled={page + 1 >= pages || loading} onClick={() => setPage((p) => p + 1)}>
            Next
          </Button>
        </span>
      </div>
    </>
  );
}
