import { ArrowLeftIcon } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api, isApiError } from "@/api";
import type { ArtifactDetailResponse, ProposalsResponse } from "@/api/types";
import { ChartCard } from "@/components/chart-card";
import { TimeSeriesChart } from "@/components/charts/time-series";
import { CategoryBar } from "@/components/charts/tremor/CategoryBar";
import { EmptyState, ErrorNotice } from "@/components/empty-state";
import { Filterbar } from "@/components/filterbar";
import { KpiCard, KpiRow } from "@/components/kpi-card";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fmtDateTime, fmtInt, fmtPct, fmtTokens, fmtUsd, shortId } from "@/lib/format";
import { alignSeries } from "@/lib/series";
import { useFilters, windowParams } from "@/lib/use-filters";
import { useQuery } from "@/lib/use-query";
import { ProposalStatusBadge, RiskBadge } from "@/pages/proposals/badges";

export default function ArtifactDetailPage() {
  const { id = "" } = useParams();
  const f = useFilters();
  const w = windowParams(f);
  const { data, error, loading } = useQuery<ArtifactDetailResponse>(() => api.analytics.artifact(id, w), [id, f.range.from, f.range.to, f.compare]);
  const proposals = useQuery<ProposalsResponse | null>(
    () => api.proposals.list({ target_artifact_id: id, limit: 50 }).catch((e) => (isApiError(e, 404) ? null : Promise.reject(e))),
    [id],
  );

  const a = data?.current.artifact;
  const prev = data?.previous?.artifact ?? undefined;
  const comparing = data?.previous != null;

  return (
    <>
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            <Button variant="ghost" size="icon" render={<Link to={`/engineering/artifacts${f.search}`} aria-label="Back to artifacts" />}>
              <ArrowLeftIcon />
            </Button>
            {a?.name ?? "Artifact"}
            {a && <Badge variant="secondary">{a.kind}</Badge>}
            {a?.never_invoked && (
              <Badge variant="outline" className="text-status-warn">
                never invoked
              </Badge>
            )}
          </span>
        }
        description={a ? `${a.owner_repo ?? ""}${a.path ? ` · ${a.path}` : ""} · first seen ${fmtDateTime(a.first_seen)} · last seen ${fmtDateTime(a.last_seen)}` : undefined}
      >
        <Filterbar range={f.range} onRange={f.setRange} compare={f.compare} onCompare={(c) => f.update({ compare: c })} />
      </PageHeader>

      {error && (isApiError(error, 404) ? <EmptyState title="No such artifact." /> : <ErrorNotice error={error} what="artifact" />)}
      {!data && !error && <Skeleton className="h-64" />}
      {data && a && (
        <>
          <KpiRow>
            <KpiCard label="Invocations" value={a.invocations} previous={comparing ? prev?.invocations : undefined} loading={loading} />
            <KpiCard label="Sessions loaded" value={a.sessions_loaded} previous={comparing ? prev?.sessions_loaded : undefined} loading={loading} hint={`${fmtTokens(a.estimated_tokens_always_on)} always-on tokens each`} />
            <KpiCard label="Correction rate" value={a.correction_rate} previous={comparing ? prev?.correction_rate : undefined} format={fmtPct} deltaMode="points" invert loading={loading} hint={`${fmtInt(a.outcomes)} outcomes`} />
            <KpiCard label="Cost / invocation" value={a.cost_per_invocation} previous={comparing ? prev?.cost_per_invocation : undefined} format={fmtUsd} invert loading={loading} hint={`${fmtUsd(a.cost_usd)} · ${fmtTokens(a.tokens)} tokens`} />
          </KpiRow>

          <div className="grid gap-4 lg:grid-cols-3">
            <ChartCard title="Invocations" description="Distinct (session, prompt) invocations per day." className="lg:col-span-2">
              <TimeSeriesChart id="artifact" data={alignSeries(f.range, "day", data.current.series, data.previous?.series ?? null, (r) => r.invocations)} label="Invocations" />
            </ChartCard>
            <ChartCard title="Outcomes" description="Success · user corrected · failure in this period.">
              {a.outcomes ? (
                <>
                  <CategoryBar values={[a.success, a.user_corrected, a.failure]} colors={["var(--status-good)", "var(--status-warn)", "var(--status-crit)"]} />
                  <dl className="mt-4 grid grid-cols-3 gap-2 text-sm">
                    <div>
                      <dt className="text-muted-foreground">Success</dt>
                      <dd className="font-medium tabular-nums">{fmtInt(a.success)}</dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Corrected</dt>
                      <dd className="font-medium tabular-nums">{fmtInt(a.user_corrected)}</dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Failure</dt>
                      <dd className="font-medium tabular-nums">{fmtInt(a.failure)}</dd>
                    </div>
                  </dl>
                </>
              ) : (
                <EmptyState compact title="No outcomes in this period." />
              )}
            </ChartCard>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Version history</CardTitle>
                <CardDescription>Content hashes seen by the sync CLI and the registry endpoint.</CardDescription>
              </CardHeader>
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Version</TableHead>
                      <TableHead>Seen</TableHead>
                      <TableHead className="text-right">Always-on</TableHead>
                      <TableHead className="text-right">Body</TableHead>
                      <TableHead>Commit</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.versions.map((v) => (
                      <TableRow key={v.version_id}>
                        <TableCell className="font-mono text-xs">
                          {shortId(v.content_hash, 10)}
                          {v.version_id === a.latest_version_id && (
                            <Badge variant="outline" className="ml-2">
                              latest
                            </Badge>
                          )}
                        </TableCell>
                        <TableCell className="text-xs">
                          {fmtDateTime(v.seen_from)}
                          {v.seen_until ? ` → ${fmtDateTime(v.seen_until)}` : ""}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{fmtTokens(v.estimated_tokens_always_on)}</TableCell>
                        <TableCell className="text-right tabular-nums">
                          {fmtTokens(v.estimated_tokens_body)}
                          {!v.synced && <span className="ml-1 text-xs text-muted-foreground">(no text)</span>}
                        </TableCell>
                        <TableCell className="font-mono text-xs">{shortId(v.commit_sha, 7)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Proposals</CardTitle>
                <CardDescription>Improvement proposals targeting this artifact.</CardDescription>
              </CardHeader>
              <CardContent>
                {(!proposals.loading && proposals.data === null) || proposals.error ? (
                  <EmptyState compact title="Proposals unavailable." />
                ) : (
                  <ProposalMini rows={proposals.data?.proposals ?? []} />
                )}
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </>
  );
}

function ProposalMini({ rows }: { rows: ProposalsResponse["proposals"] }) {
  if (rows.length === 0) return <EmptyState compact title="No proposals for this artifact." />;
  return (
    <ul className="divide-y">
      {rows.map((p) => (
        <li key={p.proposal_id} className="flex items-center gap-2 py-2 text-sm">
          <Link className="min-w-0 flex-1 truncate underline-offset-2 hover:underline" to={`/proposals/${p.proposal_id}`}>
            {typeof p.rationale === "string" ? p.rationale : (p.expected_effect?.summary ?? `Proposal ${p.proposal_id.slice(0, 8)}`)}
          </Link>
          <RiskBadge risk={p.risk_class} />
          <ProposalStatusBadge status={p.status} />
        </li>
      ))}
    </ul>
  );
}
