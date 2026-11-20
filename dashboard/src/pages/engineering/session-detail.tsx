import { ArrowLeftIcon, ChevronDownIcon, ChevronRightIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, isApiError } from "@/api";
import type { TimelineResponse, TurnRow } from "@/api/types";
import { Tracker } from "@/components/charts/tremor/Tracker";
import { EmptyState, ErrorNotice } from "@/components/empty-state";
import { KpiCard, KpiRow } from "@/components/kpi-card";
import { PageHeader } from "@/components/page-header";
import { SensitiveText } from "@/components/redacted";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { fmtDateTime, fmtDuration, fmtInt, fmtMs, fmtTime, fmtTokens, fmtUsd, shortId } from "@/lib/format";
import { useQuery } from "@/lib/use-query";
import { StatusBadge } from "./inspector";

type Kind = "turn" | "tool" | "outcome" | "compaction" | "skill" | "subagent" | "model" | "error" | "instructions";

interface Item {
  at: string | null;
  kind: Kind;
  title: React.ReactNode;
  detail?: React.ReactNode;
  tone?: "good" | "warn" | "crit";
}

const KIND_LABEL: Record<Kind, string> = {
  turn: "Turn",
  tool: "Tool call",
  outcome: "Outcome",
  compaction: "Compaction",
  skill: "Skill activated",
  subagent: "Subagent",
  model: "Model switch",
  error: "Error",
  instructions: "Instructions loaded",
};

export default function SessionDetailPage() {
  const { id = "" } = useParams();
  const { data, error } = useQuery<TimelineResponse>(() => api.analytics.sessionTimeline(id), [id]);

  const tracker = useMemo(
    () =>
      data?.turns.map((t) => {
        const failed = t.outcomes.some((o) => o.status === "failure") || t.errors.length > 0;
        const corrected = t.outcomes.some((o) => o.status === "user_corrected");
        return {
          key: t.turn_id,
          color: failed ? "var(--status-crit)" : corrected ? "var(--status-warn)" : "var(--status-good)",
          tooltip: `Turn ${t.seq ?? "?"} · ${t.tool_calls.length} tool calls${failed ? " · failed" : corrected ? " · corrected" : ""}`,
        };
      }) ?? [],
    [data],
  );

  return (
    <>
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            <Button variant="ghost" size="icon" render={<Link to="/engineering/sessions" aria-label="Back to sessions" />}>
              <ArrowLeftIcon />
            </Button>
            Session <code className="text-base">{shortId(id, 13)}</code>
          </span>
        }
        description={
          data ? (
            <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span>Title: <SensitiveText value={data.session.title_redacted} clamp={80} /></span>
              <span>{fmtDateTime(data.session.started_at)} → {fmtDateTime(data.session.ended_at)} ({fmtDuration(data.session.started_at, data.session.ended_at)})</span>
              {data.session.project_key && <Badge variant="secondary">{data.session.project_key}</Badge>}
              {data.session.model_initial && <Badge variant="outline">{data.session.model_initial}</Badge>}
              {data.session.end_reason && <Badge variant="outline">ended: {data.session.end_reason}</Badge>}
              <span className="text-xs">{data.session.auth_upn}</span>
            </span>
          ) : undefined
        }
      />

      {error && (isApiError(error, 404) ? <EmptyState title="No such session." /> : <ErrorNotice error={error} what="session" />)}
      {!data && !error && <Skeleton className="h-64" />}
      {data && (
        <>
          <KpiRow>
            <KpiCard label="Turns" value={data.totals.turns} hint={`${fmtInt(data.totals.tool_calls)} tool calls`} />
            <KpiCard label="Cost" value={data.totals.cost_usd} format={fmtUsd} hint={`${fmtInt(data.totals.api_requests)} API requests`} />
            <KpiCard label="Outcomes" value={data.totals.outcomes} hint={`${fmtInt(data.totals.errors)} errors · ${fmtInt(data.totals.compactions)} compactions`} />
            <KpiCard label="Subagents" value={data.totals.subagent_runs} hint={`${fmtInt(data.totals.transcript_lines)} transcript lines (${fmtInt(data.totals.transcript_lines_with_content)} with content)`} />
          </KpiRow>

          {tracker.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Turn health</CardTitle>
                <CardDescription>One block per turn: green ok, amber user-corrected, red failed or errored. Hover for detail.</CardDescription>
              </CardHeader>
              <CardContent>
                <Tracker data={tracker} hoverEffect />
              </CardContent>
            </Card>
          )}

          <Tabs defaultValue="timeline">
            <Card>
              <CardHeader>
                <CardTitle>Timeline</CardTitle>
                <CardDescription>Turns with their tool calls, outcomes, subagents, compactions, instruction loads and model switches in chronological order. Text fields honour server-side redaction.</CardDescription>
                <TabsList className="mt-2 w-fit">
                  <TabsTrigger value="timeline">Timeline</TabsTrigger>
                  <TabsTrigger value="inspector">Prompt & context inspector</TabsTrigger>
                </TabsList>
              </CardHeader>
              <CardContent>
                <TabsContent value="timeline">
                  <Timeline data={data} />
                </TabsContent>
                <TabsContent value="inspector">
                  <Inspector data={data} />
                </TabsContent>
              </CardContent>
            </Card>
          </Tabs>
        </>
      )}
    </>
  );
}

function itemsFor(t: Partial<TurnRow>): Item[] {
  const items: Item[] = [];
  for (const c of t.tool_calls ?? [])
    items.push({
      at: c.started_at,
      kind: "tool",
      tone: c.success === false ? "crit" : undefined,
      title: (
        <>
          <span className="font-medium">{c.tool_name}</span>
          {c.mcp_server && <span className="text-muted-foreground"> via {c.mcp_server}</span>}
          {c.agent_id && <Badge variant="outline" className="ml-2">subagent {shortId(c.agent_id, 12)}</Badge>}
          <span className="ml-2 text-xs text-muted-foreground">{fmtMs(c.duration_ms)}{c.success === false ? ` · failed${c.error_class ? ` (${c.error_class})` : ""}` : ""}{c.decision ? ` · ${c.decision}` : ""}</span>
        </>
      ),
      detail:
        c.input_content != null || c.output_content != null ? (
          <div className="grid gap-2 md:grid-cols-2">
            <Pre label="Input" value={c.input_content} />
            <Pre label="Output" value={c.output_content} />
          </div>
        ) : undefined,
    });
  for (const o of t.outcomes ?? [])
    items.push({
      at: o.client_ts,
      kind: "outcome",
      tone: o.status === "failure" ? "crit" : o.status === "user_corrected" ? "warn" : "good",
      title: (
        <>
          <span className="font-medium">{o.skill_name}</span> <StatusBadge status={o.status} />
          {o.error_code && <code className="ml-2 text-xs">{o.error_code}</code>}
          <span className="ml-2 text-xs text-muted-foreground">{fmtMs(o.latency_ms)}</span>
        </>
      ),
      detail: o.feedback?.user_corrections != null || o.feedback?.notes != null ? (
        <div className="grid gap-2 md:grid-cols-2">
          <Pre label="Notes" value={o.feedback?.notes} />
          <Pre label="User corrections" value={o.feedback?.user_corrections} />
        </div>
      ) : undefined,
    });
  for (const c of t.compactions ?? [])
    items.push({
      at: c.at,
      kind: "compaction",
      tone: "warn",
      title: (
        <>
          <span className="font-medium">{c.trigger ?? "compaction"}</span>
          <span className="ml-2 text-xs text-muted-foreground">{fmtTokens(c.pre_tokens)} → {fmtTokens(c.post_tokens)} tokens{c.summary_length != null ? ` · summary ${fmtInt(c.summary_length)} chars` : ""}</span>
        </>
      ),
      detail: c.summary_text != null ? <Pre label="Summary" value={c.summary_text} /> : undefined,
    });
  for (const s of t.skill_activations ?? [])
    items.push({ at: s.at, kind: "skill", title: <><span className="font-medium">{s.skill_name}</span><span className="ml-2 text-xs text-muted-foreground">{s.skill_source ?? ""}{s.invocation_trigger ? ` · ${s.invocation_trigger}` : ""}</span></> });
  for (const s of t.subagent_runs ?? [])
    items.push({
      at: s.started_at,
      kind: "subagent",
      title: (
        <>
          <span className="font-medium">{s.agent_type ?? "agent"}</span> <code className="text-xs">{shortId(s.agent_id, 12)}</code>
          <span className="ml-2 text-xs text-muted-foreground">{fmtDuration(s.started_at, s.stopped_at)} · {fmtTokens(s.total_tokens)} tokens · {fmtInt(s.total_tool_uses)} tool uses</span>
        </>
      ),
    });
  for (const m of t.model_switches ?? [])
    items.push({ at: m.at, kind: "model", title: <><span className="font-medium">{m.from_model ?? "?"} → {m.to_model ?? "?"}</span><span className="ml-2 text-xs text-muted-foreground">{m.phase ?? ""}{m.context_tokens != null ? ` · ${fmtTokens(m.context_tokens)} tokens in context` : ""}</span></> });
  for (const e of t.errors ?? [])
    items.push({
      at: e.at,
      kind: "error",
      tone: "crit",
      title: <><span className="font-medium">{e.kind ?? "error"}</span>{e.error_code && <code className="ml-2 text-xs">{e.error_code}</code>}{e.status_code != null && <span className="ml-2 text-xs text-muted-foreground">HTTP {e.status_code}</span>}</>,
      detail: e.message != null ? <Pre label="Message" value={e.message} /> : undefined,
    });
  for (const i of t.instructions_loaded ?? [])
    items.push({ at: i.at, kind: "instructions", title: <><span className="font-medium">{i.file_path ?? shortId(i.artifact_id)}</span><span className="ml-2 text-xs text-muted-foreground">{i.memory_type ?? ""}{i.load_reason ? ` · ${i.load_reason}` : ""}</span></> });
  return items.sort((a, b) => (a.at ?? "").localeCompare(b.at ?? ""));
}

function Timeline({ data }: { data: TimelineResponse }) {
  const [open, setOpen] = useState<Set<string>>(() => new Set(data.turns.slice(0, 3).map((t) => t.turn_id)));
  const unattributed = itemsFor(data.unattributed as Partial<TurnRow>);
  if (data.turns.length === 0 && unattributed.length === 0) return <EmptyState compact title="No turns recorded for this session." />;
  const toggle = (id: string) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  return (
    <ol className="relative border-l pl-5">
      {data.turns.map((t) => {
        const items = itemsFor(t);
        const isOpen = open.has(t.turn_id);
        return (
          <li key={t.turn_id} className="relative mb-4">
            <span className="absolute -left-[27px] top-1.5 size-3 rounded-full border-2 border-background" style={{ background: t.errors.length || t.outcomes.some((o) => o.status === "failure") ? "var(--status-crit)" : "var(--chart-1)" }} aria-hidden />
            <button type="button" className="flex w-full items-start gap-2 text-left" onClick={() => toggle(t.turn_id)} aria-expanded={isOpen}>
              {isOpen ? <ChevronDownIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" /> : <ChevronRightIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />}
              <span className="min-w-0 flex-1">
                <span className="text-sm font-medium">Turn {t.seq ?? "?"}</span>
                {t.command_name && <Badge variant="secondary" className="ml-2">{t.command_name}</Badge>}
                {t.model && <Badge variant="outline" className="ml-2">{t.model}</Badge>}
                <span className="ml-2 text-xs text-muted-foreground">
                  {fmtTime(t.submitted_at)} · {fmtDuration(t.submitted_at, t.stopped_at)} · prompt {fmtInt(t.prompt_length)} chars · response {fmtInt(t.response_length)} chars
                  {t.usage && ` · ${fmtUsd(t.usage.cost_usd)} / ${fmtInt(t.usage.api_requests)} req`}
                  {items.length > 0 && ` · ${items.length} events`}
                </span>
              </span>
            </button>
            {isOpen && (
              <div className="mt-2 space-y-2 pl-6">
                <Pre label="Prompt" value={t.prompt_text} clamp={600} />
                {items.map((it, i) => (
                  <ItemRow key={i} item={it} />
                ))}
                <Pre label="Response" value={t.response_text} clamp={600} />
              </div>
            )}
          </li>
        );
      })}
      {unattributed.length > 0 && (
        <li className="relative mb-4">
          <span className="absolute -left-[27px] top-1.5 size-3 rounded-full border-2 border-background bg-muted-foreground" aria-hidden />
          <p className="text-sm font-medium">Session-level events</p>
          <p className="text-xs text-muted-foreground">Not attributed to a turn (session start, hooks outside a prompt).</p>
          <div className="mt-2 space-y-2 pl-6">
            {unattributed.map((it, i) => (
              <ItemRow key={i} item={it} />
            ))}
          </div>
        </li>
      )}
    </ol>
  );
}

function ItemRow({ item }: { item: Item }) {
  const [open, setOpen] = useState(false);
  const toneClass = item.tone === "crit" ? "text-status-crit" : item.tone === "warn" ? "text-status-warn" : item.tone === "good" ? "text-status-good" : "text-muted-foreground";
  return (
    <div className="rounded-md border px-3 py-1.5 text-sm">
      <div className="flex items-start gap-2">
        <span className={`w-28 shrink-0 text-xs font-medium ${toneClass}`}>{KIND_LABEL[item.kind]}</span>
        <span className="w-20 shrink-0 text-xs text-muted-foreground">{fmtTime(item.at)}</span>
        <span className="min-w-0 flex-1">{item.title}</span>
        {item.detail && (
          <button type="button" className="text-xs text-muted-foreground underline-offset-2 hover:underline" onClick={() => setOpen(!open)} aria-expanded={open}>
            {open ? "hide" : "detail"}
          </button>
        )}
      </div>
      {open && item.detail && <div className="mt-2">{item.detail}</div>}
    </div>
  );
}

function Pre({ label, value, clamp }: { label: string; value: TurnRow["prompt_text"] | undefined; clamp?: number }) {
  if (value === undefined) return null;
  return (
    <div className="min-w-0">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div className="mt-0.5 max-h-72 overflow-auto rounded border bg-muted/30 p-2 font-mono text-xs">
        <SensitiveText value={value} clamp={clamp} empty="(none)" />
      </div>
    </div>
  );
}

/** Prompt / context inspector: what the model saw per turn (instructions loaded, compactions,
 * context tokens) next to the prompt and response text, redaction-aware. */
function Inspector({ data }: { data: TimelineResponse }) {
  const loads = [...(data.unattributed.instructions_loaded ?? []), ...data.turns.flatMap((t) => t.instructions_loaded)];
  return (
    <div className="space-y-4">
      <div className="grid gap-3 text-sm md:grid-cols-3">
        <div className="rounded-md border p-3">
          <div className="text-xs text-muted-foreground">Context at start</div>
          <div className="text-lg font-semibold">{fmtTokens(data.session.context_tokens_at_start)} tokens</div>
        </div>
        <div className="rounded-md border p-3">
          <div className="text-xs text-muted-foreground">Instruction files loaded</div>
          <div className="text-lg font-semibold">{fmtInt(loads.length)}</div>
        </div>
        <div className="rounded-md border p-3">
          <div className="text-xs text-muted-foreground">Compactions</div>
          <div className="text-lg font-semibold">{fmtInt(data.totals.compactions)}</div>
        </div>
      </div>
      {loads.length > 0 && (
        <div>
          <h3 className="mb-1 text-sm font-medium">Instructions loaded</h3>
          <ul className="divide-y rounded-md border text-xs">
            {loads.map((l) => (
              <li key={l.id} className="flex flex-wrap items-center gap-2 px-3 py-1.5">
                <span className="font-mono">{l.file_path ?? shortId(l.artifact_id)}</span>
                <span className="text-muted-foreground">{l.memory_type ?? ""}{l.load_reason ? ` · ${l.load_reason}` : ""}{l.trigger_file_path ? ` · triggered by ${l.trigger_file_path}` : ""}</span>
                {l.artifact_id && (
                  <Link className="ml-auto underline-offset-2 hover:underline" to={`/engineering/artifacts/${l.artifact_id}`}>
                    artifact →
                  </Link>
                )}
                <span className="text-muted-foreground">{fmtTime(l.at)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="space-y-3">
        {data.turns.map((t) => (
          <div key={t.turn_id} className="rounded-md border p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium">Turn {t.seq ?? "?"}</span>
              {t.model && <Badge variant="outline">{t.model}</Badge>}
              {t.expansion_type && <Badge variant="secondary">{t.expansion_type}</Badge>}
              <span className="text-xs text-muted-foreground">{fmtTime(t.submitted_at)}</span>
              {t.usage && <span className="ml-auto text-xs text-muted-foreground">in {fmtTokens(t.usage.input_tokens)} · out {fmtTokens(t.usage.output_tokens)} · cache read {fmtTokens(t.usage.cache_read)} · cache write {fmtTokens(t.usage.cache_create)}</span>}
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              <Pre label={`Prompt (${fmtInt(t.prompt_length)} chars)`} value={t.prompt_text} />
              <Pre label={`Response (${fmtInt(t.response_length)} chars)`} value={t.response_text} />
            </div>
          </div>
        ))}
        {data.turns.length === 0 && <EmptyState compact title="No turns recorded." />}
      </div>
    </div>
  );
}
