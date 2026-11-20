import type { ColumnDef } from "@tanstack/react-table";
import { PlayIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { api, isApiError } from "@/api";
import type { LoopStatus, Proposal, ProposalsResponse } from "@/api/types";
import { ColumnMeta, DataTable, SortableHeader } from "@/components/data-table";
import { EmptyState, ErrorNotice } from "@/components/empty-state";
import { atLeast, useMe } from "@/components/me-provider";
import { PageHeader } from "@/components/page-header";
import { SensitiveText } from "@/components/redacted";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { fmtDateTime, fmtInt, fmtPct, shortId } from "@/lib/format";
import { useQuery } from "@/lib/use-query";
import { ProposalStatusBadge, RiskBadge, VerdictBadge } from "./badges";

const STATUS_ITEMS = [
  { value: "all", label: "All statuses" },
  { value: "proposed", label: "Proposed" },
  { value: "approved", label: "Approved" },
  { value: "pr_opened", label: "PR opened" },
  { value: "applied", label: "Applied" },
  { value: "rejected", label: "Rejected" },
  { value: "failed", label: "Failed" },
  { value: "superseded", label: "Superseded" },
];

export function NoProposalsState({ status }: { status: LoopStatus | null }) {
  const disabled = status?.analyst.status === "disabled";
  return (
    <EmptyState title="No proposals yet.">
      {disabled
        ? `The analyst is disabled (${status?.analyst.reason}). Once it is configured, evidence-backed proposals to trim always-on tokens and reduce correction rates appear here for human approval.`
        : "When the analyst runs, evidence-backed proposals to trim always-on tokens and reduce correction rates appear here for human approval (design D14)."}
    </EmptyState>
  );
}

/** Analyst and GitHub availability, plus the admin-only manual trigger. */
export function LoopStatusBar({ status, onRan }: { status: LoopStatus | null; onRan?: () => void }) {
  const { me } = useMe();
  const [busy, setBusy] = useState(false);
  if (!status) return null;
  const a = status.analyst;
  const last = a.last_runs[0];
  const run = async () => {
    setBusy(true);
    try {
      await api.proposals.run();
      toast.success("Analyst run started", { description: `Up to ${a.max_artifacts} artifacts with ${a.model}; new proposals appear when it finishes.` });
      onRan?.();
    } catch (e) {
      toast.error("Analyst run not started", { description: isApiError(e) ? e.detail : String(e) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card>
      <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm" data-testid="loop-status">
        <span className="flex items-center gap-2">
          Analyst
          <Badge variant={a.status === "enabled" ? "default" : "outline"}>{a.status}</Badge>
          <span className="text-muted-foreground">
            {a.status === "disabled" ? a.reason : `${a.model} · ${a.schedule ? `schedule ${a.schedule}` : "manual only"}${a.sees_sensitive ? " · sees verbatim corrections" : ""}`}
          </span>
        </span>
        <span className="flex items-center gap-2">
          PR creation
          <Badge variant={status.github.status === "enabled" ? "default" : "outline"}>{status.github.status === "enabled" ? `GitHub ${status.github.mode}` : "not configured"}</Badge>
        </span>
        {last && (
          <span className="text-muted-foreground">
            Last run {fmtDateTime(last.started_at)} · {last.status} · {fmtInt(last.proposals_created)} proposed, {fmtInt(last.outputs_discarded)} discarded · {fmtInt(last.input_tokens + last.cache_read_tokens)} in / {fmtInt(last.output_tokens)} out tokens
          </span>
        )}
        {atLeast(me?.role, "admin") && (
          <Button className="ml-auto" size="sm" variant="outline" disabled={busy || a.status !== "enabled"} title={a.status !== "enabled" ? (a.reason ?? undefined) : undefined} onClick={run}>
            <PlayIcon /> Run analyst
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

export default function ProposalsPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState("all");
  const loop = useQuery<LoopStatus | null>(() => api.proposals.status().catch(() => null), []);
  const { data, error, loading } = useQuery<ProposalsResponse>(() => api.proposals.list({ status: status === "all" ? undefined : status, limit: 200 }), [status]);

  const columns = useMemo<ColumnDef<Proposal, unknown>[]>(
    () => [
      { accessorKey: "created_at", header: ({ column }) => <SortableHeader column={column}>Created</SortableHeader>, cell: ({ getValue }) => <span className="whitespace-nowrap text-xs">{fmtDateTime(getValue() as string)}</span> },
      { id: "target", accessorFn: (p) => p.target_artifact_name ?? p.target_artifact_id, header: "Target artifact", cell: ({ row }) => <span className="font-medium">{row.original.target_artifact_name ?? shortId(row.original.target_artifact_id)}</span> },
      { id: "rationale", header: "Rationale", cell: ({ row }) => <SensitiveText className="line-clamp-2 max-w-md text-xs" value={row.original.rationale} clamp={220} /> },
      {
        id: "effect",
        header: "Expected effect",
        cell: ({ row }) => {
          const e = row.original.expected_effect;
          if (!e) return "—";
          return (
            <span className="text-xs tabular-nums">
              {e.tokens_saved != null && `${fmtInt(e.tokens_saved)} tokens`}
              {e.tokens_saved != null && e.correction_rate_delta != null && " · "}
              {e.correction_rate_delta != null && `${fmtPct(e.correction_rate_delta, 1)} corrections`}
            </span>
          );
        },
      },
      { accessorKey: "risk_class", header: ({ column }) => <SortableHeader column={column}>Risk</SortableHeader>, cell: ({ getValue }) => <RiskBadge risk={getValue() as Proposal["risk_class"]} /> },
      {
        accessorKey: "status",
        header: ({ column }) => <SortableHeader column={column}>Status</SortableHeader>,
        cell: ({ row }) => (
          <span className="flex flex-col items-start gap-1">
            <span className="flex items-center gap-1">
              <ProposalStatusBadge status={row.original.status} />
              {row.original.outcome && <VerdictBadge verdict={row.original.outcome.verdict} />}
            </span>
            {row.original.status_note && <span className="text-xs text-muted-foreground">{row.original.status_note}</span>}
          </span>
        ),
      },
      { accessorKey: "created_by", header: "By", cell: ({ getValue }) => String(getValue()), meta: { className: "text-xs" } satisfies ColumnMeta },
    ],
    [],
  );

  return (
    <>
      <PageHeader title="Improvement proposals" description="Analyst-generated diffs against skills, agents, hooks and instruction files, with evidence; a human approves, a PR applies, outcomes are measured after the after-window.">
        <Select value={status} onValueChange={(v) => v && setStatus(v)} items={STATUS_ITEMS}>
          <SelectTrigger className="w-40" aria-label="Status">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATUS_ITEMS.map((i) => (
              <SelectItem key={i.value} value={i.value}>
                {i.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </PageHeader>
      <LoopStatusBar status={loop.data} onRan={loop.refetch} />
      {error && <ErrorNotice error={error} what="proposals" />}
      {!loading && !error && data && data.total === 0 && status === "all" ? (
        <NoProposalsState status={loop.data} />
      ) : (
        <DataTable columns={columns} data={data?.proposals ?? []} getRowId={(p) => p.proposal_id} onRowClick={(p) => navigate(`/proposals/${p.proposal_id}`)} loading={loading} empty={loading ? "Loading…" : "No proposals match."} initialSorting={[{ id: "created_at", desc: true }]} />
      )}
    </>
  );
}
