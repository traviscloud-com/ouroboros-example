import { Label, Pie, PieChart } from "recharts";
import { Link } from "react-router-dom";
import { api } from "@/api";
import type { AdoptionResponse, BloatResponse, CostResponse, OutcomesResponse, ProposalSummary, SentimentResponse, ThemesResponse } from "@/api/types";
import { ChartCard } from "@/components/chart-card";
import { StackedBars } from "@/components/charts/stacked-bars";
import { TimeSeriesChart } from "@/components/charts/time-series";
import { BarList } from "@/components/charts/tremor/BarList";
import { EmptyState, ErrorNotice } from "@/components/empty-state";
import { Filterbar } from "@/components/filterbar";
import { KpiCard, KpiCardSkeleton, KpiRow } from "@/components/kpi-card";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { ChartConfig, ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { VerdictBadge } from "@/pages/proposals/badges";
import { fmtInt, fmtNum, fmtPct, fmtScore, fmtTokens, fmtUsd } from "@/lib/format";
import { fillBuckets, fmtDay } from "@/lib/period";
import { alignSeries, meanSeries, pivotStacked } from "@/lib/series";
import { useFilters, windowParams, type Filters } from "@/lib/use-filters";
import { useQuery } from "@/lib/use-query";

interface Data {
  adoption: AdoptionResponse;
  outcomes: OutcomesResponse;
  costByModel: CostResponse;
  costBySkill: CostResponse;
  bloat: BloatResponse;
  sentiment: SentimentResponse;
  themes: ThemesResponse;
}

/* Status colours are reserved tokens (never reused as series colours). */
const statusConfig = {
  count: { label: "Outcomes" },
  success: { label: "Success", color: "var(--status-good)" },
  user_corrected: { label: "User corrected", color: "var(--status-warn)" },
  failure: { label: "Failure", color: "var(--status-crit)" },
} satisfies ChartConfig;

const STATUS_COLORS = { success: "var(--status-good)", user_corrected: "var(--status-warn)", failure: "var(--status-crit)" };
const STATUS_LABELS = { success: "Success", user_corrected: "User corrected", failure: "Failure" };

export default function OverviewPage() {
  const f = useFilters();
  const { range, compare, bucket, skill, update } = f;
  const w = windowParams(f);
  const { data, error, loading } = useQuery<Data>(
    async () => {
      const [adoption, outcomes, costByModel, costBySkill, bloat, sentiment, themes] = await Promise.all([
        api.analytics.adoption({ ...w, bucket, name: skill ?? undefined }),
        api.analytics.outcomes({ ...w, bucket, skill_name: skill ?? undefined }),
        api.analytics.cost({ ...w, bucket, group_by: "model", skill_name: skill ?? undefined }),
        api.analytics.cost({ ...w, bucket, group_by: "skill", skill_name: skill ?? undefined }),
        api.analytics.contextBloat({ ...w, bucket }),
        api.analytics.sentiment({ ...w, bucket, skill_name: skill ?? undefined }),
        api.analytics.themes({ ...w, top: 10 }),
      ]);
      return { adoption, outcomes, costByModel, costBySkill, bloat, sentiment, themes };
    },
    [range.from, range.to, compare, bucket, skill],
  );

  const skills = data ? [...new Set(data.adoption.current.top.filter((t) => t.kind === "skill").map((t) => t.name))].sort() : [];

  return (
    <>
      <PageHeader
        title="Overview"
        description="Adoption, outcomes, cost and context health for the selected period, compared with the period of equal length before it. Aggregates only - no prompt or correction text is shown here."
      >
        <Filterbar
          range={range}
          onRange={f.setRange}
          compare={compare}
          onCompare={(c) => update({ compare: c })}
          bucket={bucket}
          onBucket={(b) => update({ bucket: b })}
          dimensions={[{ key: "skill", label: "Skill", options: skill && !skills.includes(skill) ? [skill, ...skills] : skills, value: skill }]}
          onDimension={(key, value) => update({ [key]: value })}
        />
      </PageHeader>

      {error && <ErrorNotice error={error} what="overview" />}
      {data ? <OverviewContent data={data} filters={f.filters} loading={loading} /> : !error && <Loading />}
    </>
  );
}

function Loading() {
  return (
    <>
      <KpiRow>
        {Array.from({ length: 4 }, (_, i) => (
          <KpiCardSkeleton key={i} />
        ))}
      </KpiRow>
      <KpiRow>
        {Array.from({ length: 4 }, (_, i) => (
          <KpiCardSkeleton key={i} />
        ))}
      </KpiRow>
      <Skeleton className="h-[320px] w-full" />
    </>
  );
}

export function OverviewContent({ data, filters, loading }: { data: Data; filters: Filters; loading: boolean }) {
  const { range, bucket } = filters;
  const { adoption, outcomes, costByModel, costBySkill, bloat, sentiment, themes } = data;
  const comparing = adoption.previous !== null;
  const prev = <T,>(v: T | undefined): T | undefined | null => (comparing ? v : undefined);

  const invocations = alignSeries(range, bucket, adoption.current.series, adoption.previous?.series ?? null, (r) => r.invocations);
  const outcomeSeries = (["success", "user_corrected", "failure"] as const).map((status) =>
    fillBuckets(range, outcomes.current.series, bucket, (x) => x[status]),
  );
  const outcomeData = outcomeSeries[0].map((d, i) => ({
    day: d.day,
    success: outcomeSeries[0][i].value,
    user_corrected: outcomeSeries[1][i].value,
    failure: outcomeSeries[2][i].value,
  }));
  const statusData = (["success", "user_corrected", "failure"] as const)
    .map((status) => ({ status, count: outcomes.current.totals[status], fill: STATUS_COLORS[status] }))
    .filter((d) => d.count > 0);
  const costModel = pivotStacked(range, bucket, costByModel.current.series, (r) => r.group ?? "unknown", (r) => r.cost_usd);
  const costSkills = new Map<string, number>();
  let unattributedCost = 0;
  for (const r of costBySkill.current.series) {
    if (r.group == null) unattributedCost += r.cost_usd;
    else costSkills.set(r.group, (costSkills.get(r.group) ?? 0) + r.cost_usd);
  }
  const sentimentTrend = meanSeries(range, bucket, sentiment.current.series, (r) => r.sentiment, (r) => r.event_count);
  const sentimentPrev = sentiment.previous
    ? meanSeries({ from: sentiment.previous.from, to: sentiment.previous.to }, bucket, sentiment.previous.series, (r) => r.sentiment, (r) => r.event_count)
    : null;
  const sentimentPoints = sentimentTrend.map((p, i) => ({
    day: p.day,
    current: p.value ?? (null as unknown as number),
    previous: sentimentPrev ? sentimentPrev[i]?.value ?? null : null,
    previousDay: sentimentPrev ? sentimentPrev[i]?.day ?? null : null,
  }));
  const bloatTrend = meanSeries(range, bucket, bloat.current.series, (r) => r.bloat_index, (r) => r.sessions || 1);
  const bloatPrev = bloat.previous
    ? meanSeries({ from: bloat.previous.from, to: bloat.previous.to }, bucket, bloat.previous.series, (r) => r.bloat_index, (r) => r.sessions || 1)
    : null;
  const bloatPoints = bloatTrend.map((p, i) => ({
    day: p.day,
    current: p.value ?? (null as unknown as number),
    previous: bloatPrev ? bloatPrev[i]?.value ?? null : null,
    previousDay: bloatPrev ? bloatPrev[i]?.day ?? null : null,
  }));
  const topNames = adoption.current.top.slice(0, 8);
  const topThemes = themes.current.top.slice(0, 8);

  return (
    <>
      <KpiRow>
        <KpiCard label="Active users" value={adoption.current.totals.users} previous={prev(adoption.previous?.totals.users)} loading={loading} />
        <KpiCard label="Sessions" value={adoption.current.totals.sessions} previous={prev(adoption.previous?.totals.sessions)} loading={loading} />
        <KpiCard label="Turns" value={adoption.current.totals.turns} previous={prev(adoption.previous?.totals.turns)} loading={loading} />
        <KpiCard label="Skill & agent invocations" value={adoption.current.totals.invocations} previous={prev(adoption.previous?.totals.invocations)} loading={loading} />
      </KpiRow>
      <KpiRow>
        <KpiCard label="Success rate" value={outcomes.current.totals.success_rate} previous={prev(outcomes.previous?.totals.success_rate)} format={fmtPct} deltaMode="points" loading={loading} hint={`${fmtInt(outcomes.current.totals.total)} outcomes recorded`} />
        <KpiCard label="Correction rate" value={outcomes.current.totals.correction_rate} previous={prev(outcomes.previous?.totals.correction_rate)} format={fmtPct} deltaMode="points" invert loading={loading} hint={`${fmtInt(outcomes.current.totals.user_corrected)} user-corrected outcomes`} />
        <KpiCard label="Cost" value={costByModel.current.totals.cost_usd} previous={prev(costByModel.previous?.totals.cost_usd)} format={fmtUsd} invert loading={loading} hint={`${fmtTokens(costByModel.current.totals.input_tokens + costByModel.current.totals.output_tokens)} tokens · ${fmtUsd(costByModel.current.totals.mean_session_cost_usd)} per session`} />
        <KpiCard
          label="Context-bloat index"
          value={bloat.current.totals.bloat_index}
          previous={prev(bloat.previous?.totals.bloat_index)}
          format={(n) => fmtNum(n, 2)}
          invert
          loading={loading}
          hint={`${fmtTokens(bloat.current.totals.always_on_tokens)} always-on tokens · ${fmtInt(bloat.current.totals.compactions)} compactions`}
        />
      </KpiRow>

      <div className="grid gap-4 lg:grid-cols-3">
        <ChartCard title="Adoption" description="Skill and agent invocations per bucket; the dashed line is the previous period aligned by bucket." className="lg:col-span-2">
          <TimeSeriesChart id="adoption" data={invocations} label="Invocations" />
        </ChartCard>
        <ChartCard title="Top skills & agents" description="Invocations in this period.">
          {topNames.length === 0 ? (
            <EmptyState compact title="No invocations in this period." />
          ) : (
            <BarList data={topNames.map((t) => ({ name: `${t.name}${t.kind === "agent" ? " (agent)" : ""}`, value: t.invocations }))} valueFormatter={fmtInt} sortOrder="none" />
          )}
        </ChartCard>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Tabs defaultValue="chart" className="lg:col-span-2">
          <ChartCard
            title="Outcomes over time"
            description="Skill outcomes per bucket, split by status (reserved status colours)."
            action={
              <TabsList>
                <TabsTrigger value="chart">Chart</TabsTrigger>
                <TabsTrigger value="table">Table</TabsTrigger>
              </TabsList>
            }
          >
            <TabsContent value="chart">
              {outcomes.current.totals.total === 0 ? (
                <EmptyState compact title="No outcomes in this period." />
              ) : (
                <StackedBars data={outcomeData} keys={["success", "user_corrected", "failure"]} colors={STATUS_COLORS} labels={STATUS_LABELS} />
              )}
            </TabsContent>
            <TabsContent value="table">
              <OutcomesTable outcomes={outcomes} />
            </TabsContent>
          </ChartCard>
        </Tabs>
        <ChartCard title="Outcome mix" description="Share of outcomes by status in this period.">
          {statusData.length === 0 ? (
            <EmptyState compact title="No outcomes in this period." />
          ) : (
            <ChartContainer config={statusConfig} className="mx-auto aspect-square max-h-[240px]">
              <PieChart>
                <ChartTooltip cursor={false} content={<ChartTooltipContent nameKey="status" hideLabel />} />
                <Pie data={statusData} dataKey="count" nameKey="status" innerRadius={56} strokeWidth={2} stroke="var(--background)">
                  <Label
                    content={({ viewBox }) => {
                      if (viewBox && "cx" in viewBox && "cy" in viewBox) {
                        return (
                          <text x={viewBox.cx} y={viewBox.cy} textAnchor="middle" dominantBaseline="middle">
                            <tspan x={viewBox.cx} y={viewBox.cy} className="fill-foreground text-2xl font-semibold">
                              {fmtPct(outcomes.current.totals.success_rate)}
                            </tspan>
                            <tspan x={viewBox.cx} y={(viewBox.cy ?? 0) + 22} className="fill-muted-foreground text-xs">
                              success
                            </tspan>
                          </text>
                        );
                      }
                      return null;
                    }}
                  />
                </Pie>
                <ChartLegend content={<ChartLegendContent nameKey="status" className="flex-wrap" />} />
              </PieChart>
            </ChartContainer>
          )}
        </ChartCard>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Tabs defaultValue="chart" className="lg:col-span-2">
          <ChartCard
            title="Cost by model"
            description="API cost per bucket by model (top 5, the rest as Other)."
            action={
              <TabsList>
                <TabsTrigger value="chart">Chart</TabsTrigger>
                <TabsTrigger value="table">Table</TabsTrigger>
              </TabsList>
            }
          >
            {costModel.keys.length === 0 ? (
              <EmptyState compact title="No usage recorded in this period." />
            ) : (
              <>
                <TabsContent value="chart">
                  <StackedBars data={costModel.data} keys={costModel.keys} format={fmtUsd} />
                </TabsContent>
                <TabsContent value="table">
                  <CostByModelTable cost={costByModel} />
                </TabsContent>
              </>
            )}
          </ChartCard>
        </Tabs>
        <ChartCard title="Cost by skill" description="API cost attributed to skills in this period.">
          {costSkills.size === 0 ? (
            <EmptyState compact title="No skill-attributed cost in this period." />
          ) : (
            <BarList data={[...costSkills.entries()].map(([name, value]) => ({ name, value })).slice(0, 8)} valueFormatter={fmtUsd} />
          )}
          {unattributedCost > 0 && (
            <p className="mt-3 text-xs text-muted-foreground">{fmtUsd(unattributedCost)} of API cost had no skill attached.</p>
          )}
        </ChartCard>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <ChartCard
          title="Sentiment"
          description="Blended sentiment of skill outcomes (text + behavioural), −1 to +1, weighted by outcome count."
          action={<Badge variant="outline">{fmtScore(sentiment.current.totals.sentiment)} overall</Badge>}
        >
          {sentiment.current.totals.event_count === 0 ? (
            <EmptyState compact title="No sentiment yet.">The scheduler scores outcomes every refresh.</EmptyState>
          ) : (
            <TimeSeriesChart id="sentiment" data={sentimentPoints} label="Sentiment" color="var(--chart-3)" domain={[-1, 1]} format={fmtScore} line height={220} />
          )}
        </ChartCard>
        <ChartCard
          title="Context-bloat index"
          description="Always-on tokens, compactions per 100 turns and pre-compaction context, combined (heuristic)."
          action={
            <Tooltip>
              <TooltipTrigger render={<Badge variant="outline" className="cursor-help" />}>formula</TooltipTrigger>
              <TooltipContent className="max-w-xs">{bloat.formula}</TooltipContent>
            </Tooltip>
          }
        >
          {bloat.current.totals.sessions === 0 ? (
            <EmptyState compact title="No sessions in this period." />
          ) : (
            <TimeSeriesChart id="bloat" data={bloatPoints} label="Bloat index" color="var(--chart-2)" format={(n) => fmtNum(n, 2)} line height={220} />
          )}
        </ChartCard>
        <ChartCard title="Top correction themes" description="Most frequent themes in negative user corrections (weekly clustering).">
          {topThemes.length === 0 ? (
            <EmptyState compact title="No correction themes in this period." />
          ) : (
            <BarList
              data={topThemes.map((t) => ({ name: t.theme, value: t.count, href: undefined }))}
              valueFormatter={fmtInt}
              sortOrder="none"
            />
          )}
          <p className="mt-3 text-xs text-muted-foreground">
            Engineers can open examples from the{" "}
            <Link className="underline" to={`/engineering/failures?from=${range.from}&to=${range.to}`}>
              failures page
            </Link>
            .
          </p>
        </ChartCard>
        <ImprovementLoopCard />
      </div>
      <p className="text-xs text-muted-foreground">
        {bucket === "week" && "Weekly percentiles and bloat components are session-weighted means of daily values (approximate). "}
        Buckets are UTC days on event time; {fmtDay(range.from)} – {fmtDay(range.to)}.
      </p>
    </>
  );
}

function OutcomesTable({ outcomes }: { outcomes: OutcomesResponse }) {
  const bySkill = new Map<string, { success: number; user_corrected: number; failure: number; total: number }>();
  for (const r of outcomes.current.series) {
    const row = bySkill.get(r.skill_name) ?? { success: 0, user_corrected: 0, failure: 0, total: 0 };
    row.success += r.success;
    row.user_corrected += r.user_corrected;
    row.failure += r.failure;
    row.total += r.total;
    bySkill.set(r.skill_name, row);
  }
  const rows = [...bySkill.entries()].sort((a, b) => b[1].total - a[1].total);
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Skill</TableHead>
          <TableHead className="text-right">Outcomes</TableHead>
          <TableHead className="text-right">Success</TableHead>
          <TableHead className="text-right">Corrected</TableHead>
          <TableHead className="text-right">Failure</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.length === 0 && (
          <TableRow>
            <TableCell colSpan={5} className="text-center text-muted-foreground">
              No outcomes in this period.
            </TableCell>
          </TableRow>
        )}
        {rows.map(([name, s]) => (
          <TableRow key={name}>
            <TableCell className="font-medium">{name}</TableCell>
            <TableCell className="text-right tabular-nums">{fmtInt(s.total)}</TableCell>
            <TableCell className="text-right tabular-nums">
              {fmtInt(s.success)} <Badge variant="outline">{fmtPct(s.total ? s.success / s.total : 0)}</Badge>
            </TableCell>
            <TableCell className="text-right tabular-nums">
              {fmtInt(s.user_corrected)} <Badge variant="outline">{fmtPct(s.total ? s.user_corrected / s.total : 0)}</Badge>
            </TableCell>
            <TableCell className="text-right tabular-nums">
              {fmtInt(s.failure)} <Badge variant="outline">{fmtPct(s.total ? s.failure / s.total : 0)}</Badge>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** Table view of cost by model (dataviz relief: categorical slots 3-5 are below 3:1 on the light surface). */
function CostByModelTable({ cost }: { cost: CostResponse }) {
  const byModel = new Map<string, { cost: number; input: number; output: number }>();
  for (const r of cost.current.series) {
    const key = r.group ?? "unknown";
    const row = byModel.get(key) ?? { cost: 0, input: 0, output: 0 };
    row.cost += r.cost_usd;
    row.input += r.input_tokens;
    row.output += r.output_tokens;
    byModel.set(key, row);
  }
  const rows = [...byModel.entries()].sort((a, b) => b[1].cost - a[1].cost);
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Model</TableHead>
          <TableHead className="text-right">Cost</TableHead>
          <TableHead className="text-right">Input tokens</TableHead>
          <TableHead className="text-right">Output tokens</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map(([name, r]) => (
          <TableRow key={name}>
            <TableCell className="font-medium">{name}</TableCell>
            <TableCell className="text-right tabular-nums">{fmtUsd(r.cost)}</TableCell>
            <TableCell className="text-right tabular-nums">{fmtTokens(r.input)}</TableCell>
            <TableCell className="text-right tabular-nums">{fmtTokens(r.output)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** Improvement loop (design D14): proposal pipeline and measured outcomes. Independent of the
 * period filter - outcomes are measured over their own before/after windows. */
function ImprovementLoopCard() {
  const { data, error } = useQuery<ProposalSummary | null>(() => api.proposals.summary().catch(() => null), []);
  const s = data?.by_status;
  const v = data?.verdicts;
  const measured = v ? v.improved + v.neutral + v.worse + v.insufficient_data : 0;
  return (
    <ChartCard title="Improvement loop" description="Analyst proposals by stage and the measured verdict of applied ones.">
      {error || !data || !s || !v ? (
        <EmptyState compact title="Improvement loop unavailable." />
      ) : (
        <div className="space-y-3 text-sm" data-testid="loop-card">
          <div className="grid grid-cols-3 gap-2 text-center">
            {[
              ["Awaiting review", s.proposed],
              ["Approved / PR open", s.approved + s.pr_opened],
              ["Applied", s.applied],
            ].map(([label, n]) => (
              <div key={label as string} className="rounded-md border p-2">
                <div className="text-lg font-semibold tabular-nums">{fmtInt(n as number)}</div>
                <div className="text-xs text-muted-foreground">{label}</div>
              </div>
            ))}
          </div>
          <p className="text-muted-foreground">
            {measured === 0
              ? "No outcomes measured yet."
              : `${fmtInt(v.improved)} improved · ${fmtInt(v.neutral)} neutral · ${fmtInt(v.worse)} worse · ${fmtInt(v.insufficient_data)} insufficient data`}
          </p>
          {data.recent_outcomes.length > 0 && (
            <ul className="space-y-1">
              {data.recent_outcomes.map((o) => (
                <li key={o.proposal_id} className="flex items-center justify-between gap-2">
                  <Link className="truncate underline-offset-2 hover:underline" to={`/proposals/${o.proposal_id}`}>
                    {o.target_artifact_name}
                  </Link>
                  <VerdictBadge verdict={o.verdict} />
                </li>
              ))}
            </ul>
          )}
          <Link className="text-xs underline" to="/proposals">
            All proposals
          </Link>
        </div>
      )}
    </ChartCard>
  );
}
