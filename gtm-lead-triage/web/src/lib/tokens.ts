/* Shared design tokens and types for all views */

export const TIER_DOT: Record<string, string> = {
  hot: "bg-red-500",
  warm: "bg-amber-500",
  cold: "bg-blue-400",
  disqualified: "bg-stone-400",
};

export const TIER_BADGE: Record<string, string> = {
  hot: "bg-red-50 text-red-700",
  warm: "bg-amber-50 text-amber-700",
  cold: "bg-blue-50 text-blue-700",
  disqualified: "bg-stone-100 text-stone-500",
};

export const ROUTE_LABEL: Record<string, string> = {
  ae_immediate: "AE immediate",
  sdr_nurture: "SDR nurture",
  marketing_nurture: "Marketing nurture",
  drop: "Drop",
  skip: "Skip",
};

/* Channel color-coding (intake source) */
export const CHANNEL_DOT: Record<string, string> = {
  web_form: "bg-stone-400",
  inbound_form: "bg-stone-400",
  email: "bg-sky-500",
  chat: "bg-violet-500",
  clay: "bg-amber-500",
};

export const CHANNEL_LABEL: Record<string, string> = {
  web_form: "Form",
  inbound_form: "Form",
  email: "Email",
  chat: "Chat",
  clay: "Clay",
};

export function channelFromEmail(email: string, source?: string): string {
  if (source && CHANNEL_LABEL[source]) return source;
  return "web_form";
}

export const SOURCE_CHIP: Record<string, string> = {
  pdl: "bg-violet-50 text-violet-700",
  apollo: "bg-sky-50 text-sky-700",
  website: "bg-emerald-50 text-emerald-700",
  search: "bg-amber-50 text-amber-700",
  productboard: "bg-rose-50 text-rose-700",
  extraction: "bg-stone-100 text-stone-600",
  fixture: "bg-stone-50 text-stone-500",
};

export function sourceChipClass(source: string): string {
  if (source.startsWith("search:")) return SOURCE_CHIP.search;
  if (source.startsWith("productboard:")) return SOURCE_CHIP.productboard;
  return SOURCE_CHIP[source] || "bg-stone-100 text-stone-600";
}

/* Human-readable labels for grounded_on fact IDs */
const FACT_LABELS: Record<string, string> = {
  wtd: "company description",
  industry: "industry",
  size: "company size",
  tech: "tech stack",
  demand: "product demand",
};

export function factLabel(factId: string): string {
  if (FACT_LABELS[factId]) return FACT_LABELS[factId];
  if (factId.startsWith("signal_")) return `signal ${parseInt(factId.split("_")[1]) + 1}`;
  if (factId.startsWith("problem_")) return `challenge ${parseInt(factId.split("_")[1]) + 1}`;
  return factId;
}

export function sourceLabel(source: string): string {
  if (source.startsWith("search:")) return "search";
  if (source.startsWith("productboard:")) return "productboard";
  if (source.startsWith("clay:")) return "clay";
  return source;
}

/* ── Shared types ────────────────────────────────────────────────────────── */

export interface Lead {
  email: string;
  name?: string;
  company?: string;
  tier?: string;
  score?: number;
  route?: string;
  run_id?: string;
  industry?: string;
  seniority?: string;
  source?: string;
}

export interface RunDetail {
  run_id: string;
  event_count: number;
  stats: {
    total_input_tokens: number;
    total_output_tokens: number;
    total_duration_ms: number;
    llm_call_count: number;
    estimated_cost_usd: number;
  };
  events: RunEvent[];
  triage_result?: TriageResultData | null;
}

export interface RunEvent {
  event_type: string;
  agent: string;
  payload: Record<string, unknown>;
  created_at: string;
  input_tokens?: number;
  output_tokens?: number;
  duration_ms?: number;
  error?: string | null;
}

export interface TriageResultData {
  enrichment?: Record<string, unknown> | null;
  score?: {
    points?: number;
    rule_points?: number;
    llm_adjustment?: number;
    llm_reason?: string;
    reason?: string;
    tier?: string;
    route?: string;
    reason_codes?: string[];
  } | null;
  outreach?: {
    subject?: string;
    body?: string;
    status?: string;
    drafts?: DraftVariant[];
  } | null;
  provider_used?: string;
  trace_path?: string;
  source?: string;
  parsed_lead?: Record<string, string>;
  extraction_confidence?: number;
  field_sources?: Record<string, string>;
}

export interface DraftVariant {
  subject: string;
  body: string;
  variant: string;
  grounded_on: string[];
  status: string;
}

export interface AppConfig {
  provider: string;
  model: string;
  crm_backend: string;
  langfuse_enabled: boolean;
  langfuse_host: string;
  daily_cap: number;
  used_today: number;
  remaining: number;
}

/* ── Helpers ─────────────────────────────────────────────────────────────── */

export interface Phase {
  name: string;
  tool: string;
  events: RunEvent[];
}

export function groupIntoPhases(events: RunEvent[]): Phase[] {
  const phases: Phase[] = [];
  let current: Phase | null = null;

  const toolNames: Record<string, string> = {
    crm_lookup: "CRM Lookup",
    enrich_lead: "Enrichment",
    score_lead: "Scoring",
    draft_outreach: "Draft Outreach",
    research_company: "Company Research",
    fit_score: "ICP Fit Score",
    draft_outbound: "Outbound Drafts",
  };

  for (const e of events) {
    if (e.event_type === "run_start") {
      phases.push({ name: "Start", tool: "", events: [e] });
      continue;
    }
    if (e.event_type === "run_end") {
      phases.push({ name: "Complete", tool: "", events: [e] });
      continue;
    }
    if (e.event_type === "tool_call") {
      const tool = (e.payload?.tool as string) || "unknown";
      current = { name: toolNames[tool] || tool, tool, events: [e] };
      phases.push(current);
      continue;
    }
    if (current) {
      current.events.push(e);
    } else {
      phases.push({ name: "Agent Reasoning", tool: "", events: [e] });
    }
  }
  return phases;
}
