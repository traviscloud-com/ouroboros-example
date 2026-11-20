import { ArrowLeftIcon, ExternalLinkIcon } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import { api, isApiError } from "@/api";
import type { ArtifactMetrics, Proposal, TimeRange } from "@/api/types";
import { EmptyState, ErrorNotice } from "@/components/empty-state";
import { atLeast, useMe } from "@/components/me-provider";
import { PageHeader } from "@/components/page-header";
import { SensitiveText } from "@/components/redacted";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { fmtDateTime, fmtInt, fmtNum, fmtPct, shortId } from "@/lib/format";
import { useQuery } from "@/lib/use-query";
import { ProposalStatusBadge, RiskBadge, VerdictBadge } from "./badges";

export default function ProposalDetailPage() {
  const { id = "" } = useParams();
  const { me } = useMe();
  const { data, error, loading, refetch } = useQuery<Proposal | null>(() => api.proposals.get(id).catch((e) => (isApiError(e, 404) ? null : Promise.reject(e))), [id]);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const p = data;
  // Approval: admin for any risk, engineer for `low` only (improvement-loop spec).
  const canApprove = p?.status === "proposed" && (atLeast(me?.role, "admin") || (atLeast(me?.role, "engineer") && p.risk_class === "low"));
  const canReject = p?.status === "proposed" && atLeast(me?.role, "engineer");
  const canOpenPr = p?.status === "approved" && atLeast(me?.role, "admin");
  const approveWhy = !p || p.status !== "proposed" ? "Only proposals in status proposed can be approved." : !atLeast(me?.role, "engineer") ? "Leadership can review but not approve." : "Engineers may approve low-risk proposals only; medium and high risk need an admin.";

  const act = async (fn: () => Promise<Proposal>, done: string) => {
    setBusy(true);
    try {
      const next = await fn();
      if (next.status === "failed") toast.error("PR creation failed", { description: next.failure_reason ?? undefined });
      else toast.success(done, { description: next.pr_url ? `PR opened: ${next.pr_url}` : (next.status_note ?? undefined) });
      setRejectOpen(false);
      refetch();
    } catch (e) {
      toast.error("Action failed", { description: String(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            <Button variant="ghost" size="icon" render={<Link to="/proposals" aria-label="Back to proposals" />}>
              <ArrowLeftIcon />
            </Button>
            Proposal {shortId(id)}
            {p && <RiskBadge risk={p.risk_class} />}
            {p && <ProposalStatusBadge status={p.status} />}
          </span>
        }
        description={p ? `Created ${fmtDateTime(p.created_at)} by ${p.created_by}${p.decided_by ? ` · decided ${fmtDateTime(p.decided_at)} by ${p.decided_by}` : ""}` : undefined}
      >
        {p && (
          <div className="flex items-center gap-2">
            {p.pr_url && (
              <Button variant="outline" render={<a href={p.pr_url} target="_blank" rel="noreferrer noopener" />}>
                <ExternalLinkIcon /> Pull request
              </Button>
            )}
            {canOpenPr && (
              <Button variant="outline" disabled={busy} onClick={() => act(() => api.proposals.openPr(p.proposal_id), "Pull request opened")}>
                Open PR
              </Button>
            )}
            <Button disabled={!canApprove || busy} title={canApprove ? undefined : approveWhy} onClick={() => act(() => api.proposals.approve(p.proposal_id), "Proposal approved")}>
              Approve
            </Button>
            <Button variant="outline" disabled={!canReject || busy} onClick={() => setRejectOpen(true)}>
              Reject
            </Button>
          </div>
        )}
      </PageHeader>

      {error && <ErrorNotice error={error} what="proposal" />}
      {!loading && !error && data === null && <EmptyState title="No such proposal." />}
      {loading && !data && <Skeleton className="h-64" />}
      {p && (
        <>
          {!canApprove && p.status === "proposed" && <p className="text-sm text-muted-foreground">{approveWhy}</p>}
          {(p.status_note || p.decision_reason || p.merge_sha) && (
            <Card size="sm" data-testid="proposal-state">
              <CardContent className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
                {p.status_note && <span className="font-medium">{p.status_note}</span>}
                {p.decision_reason && <span className="text-muted-foreground">Reason: {p.decision_reason}</span>}
                {p.pr_repo && p.pr_number != null && <span className="text-muted-foreground">PR {p.pr_repo}#{p.pr_number} ({p.pr_branch})</span>}
                {p.merge_sha && <span className="font-mono text-xs text-muted-foreground">merged {shortId(p.merge_sha, 10)} · {fmtDateTime(p.applied_at)}</span>}
              </CardContent>
            </Card>
          )}
          <div className="grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>Rationale</CardTitle>
                <CardDescription>
                  Target:{" "}
                  <Link className="underline-offset-2 hover:underline" to={`/engineering/artifacts/${p.target_artifact_id}`}>
                    {p.target_artifact_name ?? shortId(p.target_artifact_id)}
                  </Link>
                  {p.target_artifact_kind && <Badge variant="secondary" className="ml-2">{p.target_artifact_kind}</Badge>}
                  {p.base_version_id && <span className="ml-2 font-mono text-xs">base {shortId(p.base_version_id)}</span>}
                </CardDescription>
              </CardHeader>
              <CardContent className="text-sm">
                <SensitiveText value={p.rationale} />
                {p.evidence?.risk_raised_from && <p className="mt-2 text-xs text-muted-foreground">Risk raised from {p.evidence.risk_raised_from} by the service (artifact kind or diff scope).</p>}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Expected effect</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <Row label="Tokens saved" value={p.expected_effect?.tokens_saved != null ? fmtInt(p.expected_effect.tokens_saved) : "—"} />
                <Row label="Correction-rate delta" value={p.expected_effect?.correction_rate_delta != null ? fmtPct(p.expected_effect.correction_rate_delta, 1) : "—"} />
                {Object.entries(p.expected_effect ?? {})
                  .filter(([k]) => k !== "tokens_saved" && k !== "correction_rate_delta")
                  .map(([k, v]) => (
                    <Row key={k} label={k} value={typeof v === "number" ? fmtNum(v) : String(v)} />
                  ))}
                {p.outcome && (
                  <div className="border-t pt-2">
                    <div className="mb-1 flex items-center gap-2">
                      <span className="font-medium">Measured outcome</span> <VerdictBadge verdict={p.outcome.verdict} />
                    </div>
                    <p className="mb-1 text-xs text-muted-foreground">
                      Before {fmtRange(p.outcome.before_window)} · after {fmtRange(p.outcome.after_window)}
                    </p>
                    {Object.keys(p.outcome.metrics_after).map((k) => (
                      <Row key={k} label={k} value={`${fmtMetric(p.outcome?.metrics_before[k])} → ${fmtMetric(p.outcome?.metrics_after[k])}`} />
                    ))}
                    {p.outcome.verdict_detail && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        Improved: {p.outcome.verdict_detail.improved.join(", ") || "none"} · regressed: {p.outcome.verdict_detail.regressed.join(", ") || "none"} · samples {p.outcome.verdict_detail.sample_before} / {p.outcome.verdict_detail.sample_after} (min {p.outcome.verdict_detail.min_sample})
                      </p>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Diff</CardTitle>
              <CardDescription>Unified diff the PR will contain, exactly as stored.</CardDescription>
            </CardHeader>
            <CardContent>
              <DiffViewer diff={p.diff} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Evidence</CardTitle>
              <CardDescription>{fmtInt(p.evidence_event_ids.length)} outcome events cited by the analyst; open them in the inspector.</CardDescription>
            </CardHeader>
            <CardContent>
              {p.evidence_event_ids.length === 0 ? (
                <EmptyState compact title="No evidence events attached." />
              ) : (
                <ul className="flex flex-wrap gap-2">
                  {p.evidence_event_ids.map((e) => (
                    <li key={e}>
                      <Link className="font-mono text-xs underline-offset-2 hover:underline" to={`/engineering/inspector?q=${encodeURIComponent(e)}`}>
                        {shortId(e, 13)}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          {p.evidence?.metrics && (
            <Card>
              <CardHeader>
                <CardTitle>Evidence metrics</CardTitle>
                <CardDescription>
                  Snapshot the analyst saw{p.evidence.window ? ` for ${fmtDateTime(p.evidence.window.from)} – ${fmtDateTime(p.evidence.window.to)}` : ""}
                  {p.evidence.model ? ` · ${p.evidence.model}` : ""}
                  {p.evidence.sees_sensitive ? " · verbatim corrections" : " · corrections redacted"}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <MetricGrid metrics={p.evidence.metrics} />
              </CardContent>
            </Card>
          )}

          {p.audit && p.audit.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Audit trail</CardTitle>
                <CardDescription>Every transition of this proposal, from audit_log.</CardDescription>
              </CardHeader>
              <CardContent>
                <ol className="space-y-1 text-sm" data-testid="audit-trail">
                  {p.audit.map((a, i) => (
                    <li key={i} className="flex flex-wrap gap-x-3">
                      <span className="whitespace-nowrap text-xs text-muted-foreground">{fmtDateTime(a.at)}</span>
                      <code className="text-xs">{a.action}</code>
                      <span className="text-muted-foreground">{a.actor_email}</span>
                    </li>
                  ))}
                </ol>
              </CardContent>
            </Card>
          )}

          <Dialog open={rejectOpen} onOpenChange={setRejectOpen}>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Reject proposal</DialogTitle>
                <DialogDescription>The decision and your identity are written to the audit log. An optional reason helps the analyst's next run.</DialogDescription>
              </DialogHeader>
              <Textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (optional)" />
              <DialogFooter>
                <Button variant="outline" onClick={() => setRejectOpen(false)}>
                  Cancel
                </Button>
                <Button variant="destructive" disabled={busy} onClick={() => act(() => api.proposals.reject(p.proposal_id, reason || undefined), "Proposal rejected")}>
                  Reject
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </>
      )}
    </>
  );
}

function fmtMetric(v: number | boolean | null | undefined): string {
  if (v == null) return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  return fmtNum(v);
}

function fmtRange(r: TimeRange): string {
  return `${fmtDateTime(r.from)} – ${fmtDateTime(r.to)}`;
}

function MetricGrid({ metrics }: { metrics: ArtifactMetrics }) {
  return (
    <div className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2 lg:grid-cols-3">
      {Object.entries(metrics).map(([k, v]) => (
        <Row key={k} label={k} value={fmtMetric(v)} />
      ))}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium tabular-nums">{value}</span>
    </div>
  );
}

/** Line-coloured unified diff: additions/removals use the reserved status tokens plus the +/- prefix. */
export function DiffViewer({ diff }: { diff: string }) {
  if (!diff.trim()) return <EmptyState compact title="Empty diff." />;
  return (
    <pre className="max-h-[32rem] overflow-auto rounded-md border bg-muted/30 p-3 font-mono text-xs leading-5">
      {diff.split("\n").map((line, i) => {
        const cls = line.startsWith("+++") || line.startsWith("---") ? "font-semibold" : line.startsWith("+") ? "bg-status-good/10 text-status-good" : line.startsWith("-") ? "bg-status-crit/10 text-status-crit" : line.startsWith("@@") ? "text-chart-1" : "";
        return (
          <div key={i} className={cls}>
            {line || " "}
          </div>
        );
      })}
    </pre>
  );
}
