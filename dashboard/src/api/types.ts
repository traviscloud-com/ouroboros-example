/** Response shapes of the reporting service (reporting/app/routes.py,
 * analytics.py, queries.py, export.py, auth.py). Keep in sync by reading the
 * server code, not by guessing. */

// --- redaction (design D12) ---------------------------------------------------------

/** What a `sensitive` text field becomes for the leadership role. */
export interface RedactionMarker {
  redacted: true;
  length: number;
}

/** A sensitive text field: verbatim for engineer+/admin, a marker for leadership. */
export type Sensitive = string | RedactionMarker | null;

// --- identity -------------------------------------------------------------------------

export type Role = "leadership" | "engineer" | "admin";

export interface Me {
  email: string;
  role: Role;
  landing: string;
  via: "gateway" | "bootstrap" | "token";
  /** Improvement-loop availability (Phase 5); details at `/v1/proposals/status`. */
  features?: { analyst: "enabled" | "disabled"; pr_creation: "app" | "token" | "not_configured" };
}

export interface PrincipalRow {
  email: string;
  role: Role;
  created_at: string;
  created_by: string;
}

export interface UsersResponse {
  users: PrincipalRow[];
  bootstrap_admins: string[];
  roles: Role[];
}

// --- analytics envelope (queries.envelope) -----------------------------------------------

export type Bucket = "day" | "week";
export type CompareTo = "previous" | null;

export interface WindowMeta {
  from: string;
  to: string;
  days: number;
}

export interface Delta {
  abs: number;
  pct: number | null;
}

export type Deltas = Record<string, Delta>;

/** `{bucket, current{...window, series, totals, ...}, previous, deltas, ...meta}` */
export interface Envelope<C extends object, Meta extends object = object> {
  bucket: string | null;
  current: WindowMeta & C;
  previous: (WindowMeta & C) | null;
  deltas: Deltas | null;
}

export type Series<Row> = { series: Row[] };

// --- adoption ---------------------------------------------------------------------------------

export interface AdoptionRow {
  bucket: string;
  name: string;
  kind: "skill" | "agent";
  users: number;
  sessions: number;
  turns: number;
  invocations: number;
}

export interface AdoptionTop {
  name: string;
  kind: "skill" | "agent";
  invocations: number;
  turns: number;
  peak_dau: number;
}

export interface AdoptionTotals {
  users: number;
  sessions: number;
  turns: number;
  invocations: number;
  names: number;
}

export type AdoptionBody = Series<AdoptionRow> & { top: AdoptionTop[]; totals: AdoptionTotals };
export type AdoptionResponse = Envelope<AdoptionBody>;

// --- outcomes --------------------------------------------------------------------------------

export interface OutcomeRow {
  bucket: string;
  skill_name: string;
  success: number;
  failure: number;
  user_corrected: number;
  total: number;
  correction_rate: number | null;
}

export interface ErrorCodeRow {
  skill_name: string;
  error_code: string;
  errors: number;
}

export interface OutcomeTotals {
  success: number;
  failure: number;
  user_corrected: number;
  total: number;
  correction_rate: number | null;
  success_rate: number | null;
  errors: number;
  tool_calls: number;
  tool_failures: number;
}

/** Tool call success / failure counts in the window (`tool_calls`, not attributed to skills). */
export interface ToolOutcomeRow {
  tool_name: string;
  mcp_server: string | null;
  calls: number;
  success: number;
  failure: number;
  interrupted: number;
  unknown: number;
  failure_rate: number | null;
}

export type OutcomesBody = Series<OutcomeRow> & { error_codes: ErrorCodeRow[]; tools: ToolOutcomeRow[]; totals: OutcomeTotals };
export type OutcomesResponse = Envelope<OutcomesBody>;

// --- latency -------------------------------------------------------------------------------------

export interface LatencyRow {
  bucket: string;
  kind: "skill" | "tool";
  name: string;
  samples: number;
  p50: number | null;
  p95: number | null;
  p99: number | null;
}

export interface LatencyByKind {
  kind: "skill" | "tool";
  samples: number;
  p50: number | null;
  p95: number | null;
  p99: number | null;
}

export interface LatencyTotals {
  samples: number;
  [key: string]: number | null; // skill_p50, tool_p95, ...
}

export type LatencyBody = Series<LatencyRow> & { by_kind: LatencyByKind[]; totals: LatencyTotals };
export type LatencyResponse = Envelope<LatencyBody> & { approximate: boolean };

// --- cost ---------------------------------------------------------------------------------------------

export type CostGroup = "none" | "model" | "skill" | "agent";

export interface CostRow {
  bucket: string;
  group: string | null;
  input_tokens: number;
  output_tokens: number;
  cache_read: number;
  cache_create: number;
  cost_usd: number;
  api_requests: number;
  session_rows: number;
}

export interface CostSession {
  session_id: string;
  first_at: string;
  last_at: string;
  cost_usd: number;
  api_requests: number;
  input_tokens: number;
  output_tokens: number;
  cache_read: number;
  cache_create: number;
}

export interface CostTotals {
  input_tokens: number;
  output_tokens: number;
  cache_read: number;
  cache_create: number;
  cost_usd: number;
  api_requests: number;
  sessions: number;
  mean_session_cost_usd: number;
}

export type CostBody = Series<CostRow> & { top_sessions: CostSession[]; totals: CostTotals };
export type CostResponse = Envelope<CostBody> & { group_by: CostGroup };

// --- context bloat ------------------------------------------------------------------------------

export interface BloatRow {
  bucket: string;
  project_key: string;
  sessions: number;
  turns: number;
  always_on_tokens: number | null;
  loaded_artifacts: number | null;
  compactions: number;
  mean_pre_tokens: number | null;
  bloat_index: number | null;
}

export interface BloatTotals {
  sessions: number;
  turns: number;
  always_on_tokens: number | null;
  loaded_artifacts: number | null;
  compactions: number;
  mean_pre_tokens: number | null;
  bloat_index: number | null;
  projects: number;
}

export type BloatBody = Series<BloatRow> & { totals: BloatTotals };
export type BloatResponse = Envelope<BloatBody> & { approximate: boolean; formula: string };

// --- sentiment -------------------------------------------------------------------------------------

export interface SentimentRow {
  bucket: string;
  skill_name: string;
  event_count: number;
  text_count: number;
  text_coverage: number | null;
  text_sentiment: number | null;
  behavioral_sentiment: number | null;
  sentiment: number | null;
  computed_at: string | null;
}

export interface SentimentTotals {
  event_count: number;
  text_coverage: number | null;
  sentiment: number | null;
  text_sentiment: number | null;
}

export type SentimentBody = Series<SentimentRow> & { totals: SentimentTotals };
export type SentimentResponse = Envelope<SentimentBody>;

// --- themes ---------------------------------------------------------------------------------------------

export interface ThemeRow {
  week?: string;
  theme: string;
  count: number;
  /** Absent for leadership (routes.themes strips it). */
  example_event_ids?: string[];
}

export type ThemesBody = Series<ThemeRow> & { top: ThemeRow[]; totals: { themes: number; weeks: number } };
export type ThemesResponse = Envelope<ThemesBody>;

// --- artifacts (design D10) ----------------------------------------------------------------------

export type ArtifactKind = "skill" | "hook" | "agent" | "command" | "instructions" | "rule" | "plugin";

export interface ArtifactRow {
  artifact_id: string;
  kind: ArtifactKind;
  name: string;
  owner_repo: string | null;
  path: string | null;
  source: string | null;
  plugin_id_hash: string | null;
  first_seen: string;
  last_seen: string;
  invocations: number;
  success: number;
  failure: number;
  user_corrected: number;
  outcomes: number;
  correction_rate: number | null;
  cost_usd: number;
  tokens: number;
  api_requests: number;
  cost_per_invocation: number | null;
  tokens_per_invocation: number | null;
  sessions_loaded: number;
  never_invoked: boolean;
  always_on_tokens_total: number | null;
  latest_version_id: string | null;
  latest_content_hash: string | null;
  estimated_tokens_always_on: number | null;
  estimated_tokens_body: number | null;
  latest_seen_from: string | null;
  latest_seen_until: string | null;
  latest_commit_sha: string | null;
  synced: boolean;
  version_count: number;
}

export interface ArtifactsResponse extends WindowMeta {
  artifacts: ArtifactRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface ArtifactVersion {
  version_id: string;
  content_hash: string;
  frontmatter: Record<string, unknown> | null;
  estimated_tokens_always_on: number | null;
  estimated_tokens_body: number | null;
  seen_from: string;
  seen_until: string | null;
  commit_sha: string | null;
  synced: boolean;
  content_length: number | null;
}

export interface ArtifactSeriesRow {
  bucket: string;
  invocations: number;
  sessions: number;
  users: number;
}

export type ArtifactDetailResponse = Envelope<{ artifact: ArtifactRow; series: ArtifactSeriesRow[] }> & {
  versions: ArtifactVersion[];
};

// --- session timeline ------------------------------------------------------------------------------

export interface SessionRow {
  session_id: string;
  started_at: string | null;
  ended_at: string | null;
  source: string | null;
  model_initial: string | null;
  agent_type: string | null;
  cwd_hash: string | null;
  project_key: string | null;
  permission_mode: string | null;
  effort: string | null;
  title_redacted: Sensitive;
  end_reason: string | null;
  context_tokens_at_start: number | null;
  client_version: string | null;
  auth_upn: string | null;
  received_at: string;
}

export interface ToolCallRow {
  tool_use_id: string;
  prompt_id: string | null;
  agent_id: string | null;
  tool_name: string;
  mcp_server: string | null;
  started_at: string | null;
  duration_ms: number | null;
  success: boolean | null;
  is_interrupt: boolean | null;
  input_bytes: number | null;
  output_bytes: number | null;
  error_class: string | null;
  decision: string | null;
  input_content: Sensitive;
  output_content: Sensitive;
}

export type EventStatus = "success" | "failure" | "user_corrected";

export interface Feedback {
  notes: Sensitive;
  user_corrections?: Sensitive;
  suggested_patch?: Sensitive;
}

export interface Interaction {
  user_prompt?: Sensitive;
  assistant_response?: Sensitive;
}

export interface OutcomeEventRow {
  event_id: string;
  prompt_id: string | null;
  turn_id: string | null;
  artifact_id: string | null;
  client_ts: string;
  skill_name: string;
  skill_version: string | null;
  status: EventStatus;
  model: string | null;
  latency_ms: number | null;
  error_code: string | null;
  feedback: Feedback | null;
  interaction: Interaction | null;
}

export interface CompactionRow {
  id: number;
  prompt_id: string | null;
  trigger: string | null;
  pre_tokens: number | null;
  post_tokens: number | null;
  at: string;
  custom_instructions_length: number | null;
  summary_length: number | null;
  summary_text: Sensitive;
}

export interface SkillActivationRow {
  id: number;
  prompt_id: string | null;
  artifact_id: string | null;
  skill_name: string;
  skill_source: string | null;
  invocation_trigger: string | null;
  at: string;
}

export interface SubagentRunRow {
  agent_id: string;
  prompt_id: string | null;
  agent_type: string | null;
  started_at: string | null;
  stopped_at: string | null;
  total_tokens: number | null;
  total_tool_uses: number | null;
  last_message_length: number | null;
}

export interface ModelSwitchRow {
  id: number;
  prompt_id: string | null;
  phase: string | null;
  from_model: string | null;
  to_model: string | null;
  context_tokens: number | null;
  at: string;
}

export interface TurnErrorRow {
  id: number;
  prompt_id: string | null;
  kind: string | null;
  model: string | null;
  error_code: string | null;
  status_code: number | null;
  error_class: string | null;
  message: Sensitive;
  duration_ms: number | null;
  at: string;
}

export interface InstructionLoadRow {
  id: number;
  prompt_id: string | null;
  artifact_id: string | null;
  file_path: string | null;
  content_hash: string | null;
  memory_type: string | null;
  load_reason: string | null;
  trigger_file_path: string | null;
  parent_file_path: string | null;
  at: string;
}

export interface TurnUsage {
  api_requests: number;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  cache_read: number;
  cache_create: number;
  first_at: string | null;
  last_at: string | null;
}

export interface TurnChildren {
  tool_calls: ToolCallRow[];
  outcomes: OutcomeEventRow[];
  compactions: CompactionRow[];
  skill_activations: SkillActivationRow[];
  subagent_runs: SubagentRunRow[];
  model_switches: ModelSwitchRow[];
  errors: TurnErrorRow[];
  instructions_loaded: InstructionLoadRow[];
}

export interface TurnRow extends TurnChildren {
  turn_id: string;
  prompt_id: string | null;
  seq: number | null;
  submitted_at: string | null;
  stopped_at: string | null;
  prompt_length: number | null;
  prompt_text: Sensitive;
  response_length: number | null;
  response_text: Sensitive;
  model: string | null;
  expansion_type: string | null;
  command_name: string | null;
  command_source: string | null;
  usage: TurnUsage | null;
}

export interface TimelineTotals {
  turns: number;
  tool_calls: number;
  outcomes: number;
  compactions: number;
  subagent_runs: number;
  errors: number;
  cost_usd: number;
  api_requests: number;
  transcript_lines: number;
  transcript_lines_with_content: number;
}

export interface TimelineResponse {
  session: SessionRow;
  turns: TurnRow[];
  unattributed: Partial<TurnChildren> & { usage?: (TurnUsage & { prompt_id: string | null })[] };
  totals: TimelineTotals;
}

// --- legacy /v1/reports/* ---------------------------------------------------------------------

export interface TelemetryEvent extends OutcomeEventRow {
  session_id: string;
  received_at: string;
  auth_oid: string;
  auth_upn: string;
}

export interface EventsPage {
  events: TelemetryEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface EventFilters {
  skill_name?: string;
  status?: EventStatus | "";
  session_id?: string;
  upn?: string;
  from?: string;
  to?: string;
  q?: string;
  error_code?: string;
}

export interface SkillAggregate {
  skill_name: string;
  total: number;
  success_count: number;
  failure_count: number;
  user_corrected_count: number;
  success_rate: number;
  failure_rate: number;
  user_corrected_rate: number;
}

export interface ReportsOverview {
  totals: { events: number; sessions: number; users: number; skills: number };
  status_breakdown: Partial<Record<EventStatus, number>>;
  activity: { bucket: string; count: number }[];
}

export interface SentimentRollup {
  sentiment: number;
  event_count: number;
  text_coverage: number;
  user_corrected_count: number;
  failure_count: number;
}

export interface SentimentReport {
  overall: { sentiment: number | null; event_count: number; text_coverage: number };
  skills: (SentimentRollup & { skill_name: string })[];
  sessions: (SentimentRollup & { session_id: string })[];
  trend: { bucket: string; sentiment: number; event_count: number }[];
}

// --- export ------------------------------------------------------------------------------------------

export const EXPORT_DATASETS = [
  "sessions",
  "turns",
  "tool_calls",
  "usage_facts",
  "events",
  "artifact_versions",
] as const;
export type ExportDataset = (typeof EXPORT_DATASETS)[number];
export type ExportFormat = "ndjson" | "parquet";

// --- improvement loop (design D14, Phase 5: reporting/app/proposals.py) ---------------------

export type ProposalStatus = "proposed" | "approved" | "rejected" | "pr_opened" | "applied" | "failed" | "superseded";
export const PROPOSAL_STATUSES: ProposalStatus[] = ["proposed", "approved", "rejected", "pr_opened", "applied", "failed", "superseded"];
export type RiskClass = "low" | "medium" | "high";
export type OutcomeVerdict = "improved" | "neutral" | "worse" | "insufficient_data";

export interface ExpectedEffect {
  tokens_saved?: number | null;
  correction_rate_delta?: number | null;
  summary?: string | null;
  [key: string]: unknown;
}

/** Metric snapshot shared by the evidence pack and the outcome windows. */
export type ArtifactMetrics = Record<string, number | boolean | null>;

export interface ProposalEvidence {
  window?: { from: string; to: string; days: number };
  metrics?: ArtifactMetrics;
  examples?: { event_id: string; at: string | null; status: string; error_code: string | null }[];
  sees_sensitive?: boolean;
  model?: string;
  risk_raised_from?: RiskClass | null;
  dropped_evidence_ids?: number;
}

export interface ProposalAuditEntry {
  at: string;
  actor_email: string;
  actor_role: Role | null;
  action: string;
  details: Record<string, unknown>;
}

export interface Proposal {
  proposal_id: string;
  target_artifact_id: string;
  target_artifact_name: string | null;
  target_artifact_kind: ArtifactKind | null;
  target_owner_repo: string | null;
  target_path: string | null;
  base_version_id: string | null;
  /** Redacted for leadership when the analyst saw verbatim user text. */
  rationale: Sensitive;
  expected_effect: ExpectedEffect | null;
  evidence_event_ids: string[];
  evidence: ProposalEvidence | null;
  diff: string;
  risk_class: RiskClass;
  status: ProposalStatus;
  /** e.g. "approved, PR creation not configured" or "failed: <reason>". */
  status_note: string | null;
  created_by: "human" | "loop";
  created_at: string;
  updated_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  pr_url: string | null;
  pr_repo: string | null;
  pr_number: number | null;
  pr_branch: string | null;
  merge_sha: string | null;
  applied_at: string | null;
  applied_version_id: string | null;
  failure_reason: string | null;
  analyst_run_id: string | null;
  outcome: ProposalOutcome | null;
  /** Detail route only. */
  audit?: ProposalAuditEntry[];
}

/** A half-open `tstzrange` (`[from, to)`), ISO datetimes. */
export interface TimeRange {
  from: string | null;
  to: string | null;
  days: number | null;
  bounds: "[)";
}

export interface ProposalOutcome {
  proposal_id: string;
  before_window: TimeRange;
  after_window: TimeRange;
  metrics_before: ArtifactMetrics;
  metrics_after: ArtifactMetrics;
  verdict: OutcomeVerdict;
  verdict_detail: { improved: string[]; regressed: string[]; min_sample: number; sample_before: number; sample_after: number } | null;
  computed_at: string;
}

export interface ProposalsResponse {
  proposals: Proposal[];
  total: number;
  limit: number;
  offset: number;
}

export interface AnalystRun {
  run_id: string;
  started_at: string;
  finished_at: string | null;
  trigger: "schedule" | "manual";
  actor_email: string | null;
  model: string;
  status: "running" | "succeeded" | "failed";
  artifacts_considered: number;
  proposals_created: number;
  outputs_discarded: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  error: string | null;
}

export interface LoopStatus {
  analyst: {
    status: "enabled" | "disabled";
    reason: string | null;
    model: string;
    schedule: string;
    schedule_seconds: number | null;
    max_artifacts: number;
    sees_sensitive: boolean;
    window_days: number;
    last_runs: AnalystRun[];
  };
  github: { status: "enabled" | "disabled"; mode: "app" | "token" | "disabled"; reason: string | null; webhook: "enabled" | "disabled"; poll_seconds: number };
  outcomes: { window_days: number; min_sample: number; relative_threshold: number; rate_points: number };
  auto_approve: string;
}

export interface ProposalSummary {
  by_status: Record<ProposalStatus, number>;
  verdicts: Record<OutcomeVerdict, number>;
  recent_outcomes: { proposal_id: string; verdict: OutcomeVerdict; computed_at: string; target_artifact_name: string; risk_class: RiskClass }[];
}

// --- sessions list (/v1/analytics/sessions) ------------------------------------------------------

export type SessionOrder = "started" | "cost" | "turns" | "corrections" | "tokens";

export interface SessionListRow {
  session_id: string;
  started_at: string | null;
  ended_at: string | null;
  source: string | null;
  model_initial: string | null;
  agent_type: string | null;
  project_key: string | null;
  end_reason: string | null;
  title_redacted: Sensitive;
  auth_upn: string;
  turns: number;
  tool_calls: number;
  tool_failures: number;
  compactions: number;
  cost_usd: number | null;
  api_requests: number | null;
  tokens: number | null;
  outcomes: number;
  user_corrected: number;
  failures: number;
}

export interface SessionsResponse extends WindowMeta {
  sessions: SessionListRow[];
  total: number;
  limit: number;
  offset: number;
  order: SessionOrder;
  totals: { sessions: number; cost_usd: number; mean_session_cost_usd: number | null; turns: number; outcomes: number; user_corrected: number };
}
