/** Typed client for the reporting service. One module per server router; see
 * `types.ts` for the shapes (read from reporting/app, never guessed). */
import { del, get, post, query } from "./client";
import type {
  AdoptionResponse,
  ArtifactDetailResponse,
  ArtifactKind,
  ArtifactsResponse,
  BloatResponse,
  Bucket,
  CompareTo,
  CostGroup,
  CostResponse,
  EventFilters,
  EventsPage,
  ExportDataset,
  ExportFormat,
  LatencyResponse,
  LoopStatus,
  Me,
  OutcomesResponse,
  Proposal,
  ProposalsResponse,
  ProposalSummary,
  ReportsOverview,
  Role,
  SentimentReport,
  SentimentResponse,
  SessionOrder,
  SessionsResponse,
  SkillAggregate,
  ThemesResponse,
  TimelineResponse,
  UsersResponse,
} from "./types";

export * from "./client";
export * from "./types";

/** Event-time window every analytics route accepts (`from`/`to` are UTC days). */
export interface WindowParams {
  from: string;
  to: string;
  compare_to?: CompareTo;
}

export interface SeriesParams extends WindowParams {
  bucket?: Bucket;
}

export const api = {
  me: () => get<Me>("/v1/me"),

  analytics: {
    adoption: (p: SeriesParams & { kind?: "skill" | "agent"; name?: string }) =>
      get<AdoptionResponse>("/v1/analytics/adoption", p),
    outcomes: (p: SeriesParams & { skill_name?: string }) => get<OutcomesResponse>("/v1/analytics/outcomes", p),
    latency: (p: SeriesParams & { kind?: "skill" | "tool"; name?: string }) =>
      get<LatencyResponse>("/v1/analytics/latency", p),
    cost: (p: SeriesParams & { group_by?: CostGroup; model?: string; skill_name?: string; agent_name?: string }) =>
      get<CostResponse>("/v1/analytics/cost", p),
    contextBloat: (p: SeriesParams & { project_key?: string }) =>
      get<BloatResponse>("/v1/analytics/context-bloat", p),
    sentiment: (p: SeriesParams & { skill_name?: string }) => get<SentimentResponse>("/v1/analytics/sentiment", p),
    themes: (p: WindowParams & { top?: number }) => get<ThemesResponse>("/v1/analytics/themes", p),
    sessions: (p: WindowParams & { project_key?: string; upn?: string; q?: string; order?: SessionOrder; limit?: number; offset?: number }) =>
      get<SessionsResponse>("/v1/analytics/sessions", p),
    sessionTimeline: (sessionId: string) =>
      get<TimelineResponse>(`/v1/analytics/sessions/${encodeURIComponent(sessionId)}/timeline`),
    artifacts: (
      p: WindowParams & {
        kind?: ArtifactKind;
        q?: string;
        never_invoked?: boolean;
        order?: "invocations" | "name" | "cost" | "always_on" | "last_seen";
        limit?: number;
        offset?: number;
      },
    ) => get<ArtifactsResponse>("/v1/analytics/artifacts", p),
    artifact: (artifactId: string, p: WindowParams) =>
      get<ArtifactDetailResponse>(`/v1/analytics/artifacts/${encodeURIComponent(artifactId)}`, p),
  },

  /** Legacy routes; `from`/`to` are ISO datetimes on `client_ts`. */
  reports: {
    events: (filters: EventFilters, limit: number, offset: number) =>
      get<EventsPage>("/v1/reports/events", { ...filters, limit, offset }),
    skills: (p: { from?: string; to?: string } = {}) =>
      get<{ skills: SkillAggregate[] }>("/v1/reports/skills", p).then((r) => r.skills),
    overview: (p: { from?: string; to?: string; bucket?: "hour" | "day" | "week" } = {}) =>
      get<ReportsOverview>("/v1/reports/overview", { bucket: "day", ...p }),
    sentiment: (p: { from?: string; to?: string } = {}) => get<SentimentReport>("/v1/reports/sentiment", p),
  },

  export: {
    /** Browser download URL; the server streams the file with Content-Disposition. */
    url: (dataset: ExportDataset, p: { from: string; to: string; format: ExportFormat; redact?: boolean }) =>
      `/v1/export/${dataset}${query({ ...p, redact: p.redact ? 1 : undefined })}`,
  },

  settings: {
    users: () => get<UsersResponse>("/v1/settings/users"),
    addUser: (email: string, role: Role) =>
      post<{ email: string; role: Role; created_at: string; created_by: string }>("/v1/settings/users", {
        email,
        role,
      }),
    removeUser: (email: string) => del(`/v1/settings/users/${encodeURIComponent(email)}`),
  },

  /** Improvement loop (design D14, reporting/app/proposals.py). */
  proposals: {
    list: (p: { status?: string; risk_class?: string; target_artifact_id?: string; limit?: number; offset?: number } = {}) =>
      get<ProposalsResponse>("/v1/proposals", p),
    get: (id: string) => get<Proposal>(`/v1/proposals/${encodeURIComponent(id)}`),
    status: () => get<LoopStatus>("/v1/proposals/status"),
    summary: () => get<ProposalSummary>("/v1/proposals/summary"),
    /** Admin; 503 while the analyst is disabled, 409 while a run is in progress, 202 when started. */
    run: () => post<{ status: string }>("/v1/proposals/run"),
    approve: (id: string) => post<Proposal>(`/v1/proposals/${encodeURIComponent(id)}/approve`),
    openPr: (id: string) => post<Proposal>(`/v1/proposals/${encodeURIComponent(id)}/open-pr`),
    reject: (id: string, reason?: string) =>
      post<Proposal>(`/v1/proposals/${encodeURIComponent(id)}/reject`, reason ? { reason } : undefined),
  },
};
