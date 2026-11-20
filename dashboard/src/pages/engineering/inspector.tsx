import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "@/api";
import type { EventStatus, Sensitive, TelemetryEvent } from "@/api/types";
import { EmptyState, ErrorNotice } from "@/components/empty-state";
import { Filterbar } from "@/components/filterbar";
import { PageHeader } from "@/components/page-header";
import { SensitiveText } from "@/components/redacted";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fmtDateTime, fmtInt, fmtMs, shortId } from "@/lib/format";
import { toApiParams } from "@/lib/period";
import { useFilters } from "@/lib/use-filters";
import { useQuery } from "@/lib/use-query";

const PAGE_SIZE = 50;
const STATUS_ITEMS = [
  { value: "all", label: "All outcomes" },
  { value: "success", label: "Success" },
  { value: "user_corrected", label: "User corrected" },
  { value: "failure", label: "Failure" },
];

/** Events inspector (the pre-Phase-4 Search view): raw skill outcomes with feedback and
 * interaction text, honouring server-side redaction. Drill target of the failures page. */
export default function InspectorPage() {
  const f = useFilters();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "all";
  const errorCode = params.get("error_code") ?? "";
  const session = params.get("session") ?? "";
  const q = params.get("q") ?? "";
  const [draftQ, setDraftQ] = useState(q);
  const offset = Number(params.get("offset") ?? 0) || 0;
  const [open, setOpen] = useState<string | null>(null);

  const set = (patch: Record<string, string | null>) =>
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        for (const [k, v] of Object.entries(patch)) {
          if (v) p.set(k, v);
          else p.delete(k);
        }
        return p;
      },
      { replace: true },
    );

  const { data, error, loading } = useQuery(
    () =>
      api.reports.events(
        {
          ...toApiParams(f.range),
          skill_name: f.skill ?? undefined,
          status: status === "all" ? "" : (status as EventStatus),
          session_id: session || undefined,
          upn: f.user ?? undefined,
          q: q || undefined,
          error_code: errorCode || undefined,
        },
        PAGE_SIZE,
        offset,
      ),
    [f.range.from, f.range.to, f.skill, f.user, status, session, q, errorCode, offset],
  );

  const events = data?.events ?? [];
  const skills = [...new Set(data?.events.map((e) => e.skill_name) ?? [])].sort();

  return (
    <>
      <PageHeader title="Events inspector" description="Skill outcome events with their feedback and captured interaction text. Leadership sees redaction markers in place of text.">
        <Filterbar
          range={f.range}
          onRange={f.setRange}
          dimensions={[{ key: "skill", label: "Skill", options: f.skill && !skills.includes(f.skill) ? [f.skill, ...skills] : skills, value: f.skill }]}
          onDimension={(key, value) => {
            f.update({ [key]: value });
            set({ offset: null });
          }}
        />
      </PageHeader>

      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          set({ q: draftQ || null, offset: null });
        }}
      >
        <Select value={status} onValueChange={(v) => v && set({ status: v === "all" ? null : v, offset: null })} items={STATUS_ITEMS}>
          <SelectTrigger className="w-40" aria-label="Outcome">
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
        <Input className="w-64" placeholder="Search feedback & interaction text" value={draftQ} onChange={(e) => setDraftQ(e.target.value)} aria-label="Search text" />
        <Input className="w-72 font-mono text-xs" placeholder="Session id" value={session} onChange={(e) => set({ session: e.target.value || null, offset: null })} aria-label="Session id" />
        <Button type="submit" variant="outline">
          Search
        </Button>
        {errorCode && (
          <Badge variant="outline" className="gap-1">
            error_code = <code>{errorCode}</code>
            <button type="button" className="ml-1 text-muted-foreground hover:text-foreground" onClick={() => set({ error_code: null, offset: null })} aria-label="Clear error code filter">
              ×
            </button>
          </Badge>
        )}
      </form>

      {error && <ErrorNotice error={error} what="events" />}

      <Card>
        <CardContent className={loading ? "opacity-60 transition-opacity" : "transition-opacity"}>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>Skill</TableHead>
                <TableHead>Outcome</TableHead>
                <TableHead>Error</TableHead>
                <TableHead>Model</TableHead>
                <TableHead className="text-right">Latency</TableHead>
                <TableHead>Session</TableHead>
                <TableHead>Feedback</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {events.length === 0 && (
                <TableRow>
                  <TableCell colSpan={8} className="h-24 text-center text-muted-foreground">
                    {loading ? "Loading…" : "No events match."}
                  </TableCell>
                </TableRow>
              )}
              {events.map((e) => (
                <EventRow key={e.event_id} e={e} open={open === e.event_id} onToggle={() => setOpen(open === e.event_id ? null : e.event_id)} />
              ))}
            </TableBody>
          </Table>
          {data && data.total > PAGE_SIZE && (
            <div className="flex items-center justify-between pt-3 text-sm text-muted-foreground">
              <span>
                {fmtInt(offset + 1)}–{fmtInt(Math.min(offset + PAGE_SIZE, data.total))} of {fmtInt(data.total)}
              </span>
              <div className="flex gap-1">
                <Button variant="outline" size="icon" disabled={offset === 0} onClick={() => set({ offset: String(Math.max(0, offset - PAGE_SIZE)) || null })} aria-label="Previous page">
                  <ChevronLeftIcon />
                </Button>
                <Button variant="outline" size="icon" disabled={offset + PAGE_SIZE >= data.total} onClick={() => set({ offset: String(offset + PAGE_SIZE) })} aria-label="Next page">
                  <ChevronRightIcon />
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </>
  );
}

export function StatusBadge({ status }: { status: EventStatus }) {
  const cls = status === "success" ? "text-status-good" : status === "failure" ? "text-status-crit" : "text-status-warn";
  const label = status === "user_corrected" ? "user corrected" : status;
  return (
    <Badge variant="outline" className={cls}>
      {label}
    </Badge>
  );
}

function EventRow({ e, open, onToggle }: { e: TelemetryEvent; open: boolean; onToggle: () => void }) {
  return (
    <>
      <TableRow className="cursor-pointer" onClick={onToggle} tabIndex={0} onKeyDown={(k) => (k.key === "Enter" || k.key === " ") && (k.preventDefault(), onToggle())} aria-expanded={open}>
        <TableCell className="whitespace-nowrap text-xs">{fmtDateTime(e.client_ts)}</TableCell>
        <TableCell className="font-medium">{e.skill_name}</TableCell>
        <TableCell>
          <StatusBadge status={e.status} />
        </TableCell>
        <TableCell>{e.error_code ? <code className="text-xs">{e.error_code}</code> : <span className="text-muted-foreground">—</span>}</TableCell>
        <TableCell className="text-xs">{e.model ?? "—"}</TableCell>
        <TableCell className="text-right tabular-nums">{fmtMs(e.latency_ms)}</TableCell>
        <TableCell>
          <Link className="font-mono text-xs underline-offset-2 hover:underline" to={`/engineering/sessions/${e.session_id}`} onClick={(k) => k.stopPropagation()}>
            {shortId(e.session_id)}
          </Link>
        </TableCell>
        <TableCell className="max-w-[20rem] truncate text-xs">
          <SensitiveText value={e.feedback?.notes} clamp={80} />
        </TableCell>
      </TableRow>
      {open && (
        <TableRow className="bg-muted/30 hover:bg-muted/30">
          <TableCell colSpan={8}>
            <EmptyStateOrDetail e={e} />
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

function EmptyStateOrDetail({ e }: { e: TelemetryEvent }) {
  const sections: { label: string; value: Sensitive | undefined }[] = [
    { label: "Notes", value: e.feedback?.notes },
    { label: "User corrections", value: e.feedback?.user_corrections },
    { label: "Suggested patch", value: e.feedback?.suggested_patch },
    { label: "User prompt", value: e.interaction?.user_prompt },
    { label: "Assistant response", value: e.interaction?.assistant_response },
  ];
  const present = sections.filter((s) => s.value != null);
  if (present.length === 0) return <EmptyState compact title="No feedback or interaction text on this event." />;
  return (
    <dl className="grid gap-3 py-1 text-sm md:grid-cols-2">
      {present.map((s) => (
        <div key={s.label} className="min-w-0">
          <dt className="text-xs font-medium text-muted-foreground">{s.label}</dt>
          <dd className="mt-0.5 max-h-64 overflow-auto rounded border bg-background p-2 font-mono text-xs">
            <SensitiveText value={s.value} />
          </dd>
        </div>
      ))}
      <div className="text-xs text-muted-foreground md:col-span-2">
        event {e.event_id} · user {e.auth_upn} · version {e.skill_version ?? "—"} · received {fmtDateTime(e.received_at)}
      </div>
    </dl>
  );
}
