"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { apiGet } from "@/lib/api";

/* ── Types ──────────────────────────────────────────────────────────────── */

interface Lead {
  email: string;
  name?: string;
  company?: string;
  tier?: string;
  score?: number;
  route?: string;
  run_id?: string;
  industry?: string;
  seniority?: string;
  last_activity?: string;
  last_activity_at?: string;
}

interface RunDetail {
  run_id: string;
  event_count: number;
  stats: {
    total_input_tokens: number;
    total_output_tokens: number;
    total_duration_ms: number;
    llm_call_count: number;
    estimated_cost_usd: number;
  };
  events: Array<{
    event_type: string;
    agent: string;
    payload: Record<string, unknown>;
    created_at: string;
    input_tokens?: number;
    output_tokens?: number;
    duration_ms?: number;
    error?: string | null;
  }>;
  triage_result?: TriageResultData | null;
}

interface TriageResultData {
  enrichment?: {
    industry?: string;
    company_size?: string;
    seniority?: string;
    is_business_email?: boolean;
    confidence?: number;
    [k: string]: unknown;
  } | null;
  score?: {
    points?: number;
    rule_points?: number;
    llm_adjustment?: number;
    llm_reason?: string;
    reason?: string;
    tier?: string;
    route?: string;
  } | null;
  outreach?: {
    subject?: string;
    body?: string;
    status?: string;
  } | null;
  provider_used?: string;
}

interface AppConfig {
  provider: string;
  model: string;
  crm_backend: string;
  langfuse_enabled: boolean;
  langfuse_host: string;
  daily_cap: number;
  used_today: number;
  remaining: number;
}

/* ── Design tokens ──────────────────────────────────────────────────────── */

const TIER_DOT: Record<string, string> = {
  hot: "bg-red-500",
  warm: "bg-amber-500",
  cold: "bg-blue-400",
  disqualified: "bg-zinc-400",
};

const TIER_BADGE: Record<string, string> = {
  hot: "bg-red-50 text-red-700",
  warm: "bg-amber-50 text-amber-700",
  cold: "bg-blue-50 text-blue-700",
  disqualified: "bg-zinc-100 text-zinc-500",
};

const ROUTE_LABEL: Record<string, string> = {
  ae_immediate: "AE immediate",
  sdr_nurture: "SDR nurture",
  marketing_nurture: "Marketing nurture",
  drop: "Drop",
};

/* ── Helpers ─────────────────────────────────────────────────────────────── */

function timeAgo(iso: string): string {
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

// Group flat events into RAO phases
interface Phase {
  name: string;
  tool: string;
  events: RunDetail["events"];
}

function groupIntoPhases(events: RunDetail["events"]): Phase[] {
  const phases: Phase[] = [];
  let current: Phase | null = null;

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
      const names: Record<string, string> = {
        crm_lookup: "CRM Lookup",
        enrich_lead: "Enrichment",
        score_lead: "Scoring",
        draft_outreach: "Draft Outreach",
      };
      current = { name: names[tool] || tool, tool, events: [e] };
      phases.push(current);
      continue;
    }
    // llm_call, tool_response nest under current phase
    if (current) {
      current.events.push(e);
    } else {
      // orphan llm_call (agent reasoning before first tool)
      phases.push({ name: "Agent Reasoning", tool: "", events: [e] });
    }
  }
  return phases;
}

// (triage_result is now served directly on the /runs/{id} response)

/* ── Skeleton ────────────────────────────────────────────────────────────── */

function LeadSkeleton() {
  return (
    <div className="px-5 py-4 border-b border-zinc-100">
      <div className="flex items-center justify-between mb-2">
        <div className="skeleton h-4 w-36" />
        <div className="skeleton h-5 w-14 rounded-lg" />
      </div>
      <div className="skeleton h-3 w-44 mb-1.5" />
      <div className="skeleton h-3 w-28" />
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="p-6 space-y-4">
      <div className="skeleton h-6 w-52 mb-2" />
      <div className="flex gap-3">
        <div className="skeleton h-5 w-20 rounded-lg" />
        <div className="skeleton h-5 w-16" />
        <div className="skeleton h-5 w-24" />
      </div>
      <div className="skeleton h-24 w-full rounded-lg mt-4" />
      <div className="skeleton h-16 w-full rounded-lg" />
      <div className="space-y-3 mt-4">
        {[1, 2, 3].map((i) => (
          <div key={i} className="skeleton h-10 w-full rounded-lg" />
        ))}
      </div>
    </div>
  );
}

/* ── Main ────────────────────────────────────────────────────────────────── */

export default function OpsDashboard() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [selectedRun, setSelectedRun] = useState<RunDetail | null>(null);
  const [selectedEmail, setSelectedEmail] = useState("");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [expandedPhases, setExpandedPhases] = useState<Set<number>>(new Set());
  const [tierFilter, setTierFilter] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [copied, setCopied] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [l, c] = await Promise.all([
        apiGet<Lead[]>("/leads"),
        apiGet<AppConfig>("/config"),
      ]);
      setLeads(l);
      setConfig(c);
    } catch { /* API may be down */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 2000);
    return () => clearInterval(id);
  }, [fetchData]);

  async function selectLead(lead: Lead) {
    setSelectedEmail(lead.email);
    setSelectedRun(null);
    setExpandedPhases(new Set());
    setCopied(false);
    if (lead.run_id) {
      setDetailLoading(true);
      try {
        const detail = await apiGet<RunDetail>(`/runs/${lead.run_id}`);
        setSelectedRun(detail);
      } catch { setSelectedRun(null); }
      finally { setDetailLoading(false); }
    }
    // No run_id — detail panel shows "no trace" state (handled in render)
  }

  function togglePhase(i: number) {
    setExpandedPhases((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  }

  async function copyDraft(text: string) {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  // Filters
  const filtered = leads.filter((l) => {
    if (tierFilter && l.tier !== tierFilter) return false;
    if (search) {
      const q = search.toLowerCase();
      return (l.name?.toLowerCase().includes(q) || l.email.toLowerCase().includes(q)) ?? false;
    }
    return true;
  });

  const tierCounts = leads.reduce<Record<string, number>>((acc, l) => {
    acc[l.tier || "unknown"] = (acc[l.tier || "unknown"] || 0) + 1;
    return acc;
  }, {});

  const selectedLead = leads.find((l) => l.email === selectedEmail);
  const phases = selectedRun ? groupIntoPhases(selectedRun.events) : [];
  const tr = selectedRun?.triage_result;
  const hasRealMetrics = selectedRun ? selectedRun.stats.total_duration_ms > 0 : false;

  return (
    <div className="flex flex-col min-h-[100dvh] h-[100dvh]">
      {/* Header */}
      <header className="border-b border-zinc-200 bg-white px-6 py-3.5 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2.5">
          <Link href="/" className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-lg bg-zinc-900 flex items-center justify-center">
              <span className="text-white font-bold text-xs">A</span>
            </div>
          </Link>
          <span className="font-semibold text-sm text-zinc-900">Ops</span>
          <span className="relative flex h-2 w-2 ml-1">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
          </span>
          {config && (
            <span className="text-[10px] text-zinc-400 font-mono ml-2">
              {config.provider}/{config.model}
            </span>
          )}
        </div>
        <Link href="/" className="text-xs font-medium text-zinc-500 hover:text-zinc-900 transition-colors">
          Lead Form
        </Link>
      </header>

      {/* Metric strip with filters */}
      <div className="border-b border-zinc-200 bg-white px-6 py-2.5 flex items-center gap-3 text-xs shrink-0 overflow-x-auto">
        <button
          onClick={() => setTierFilter(null)}
          className={`flex items-center gap-1.5 px-2 py-1 rounded-lg transition-colors ${!tierFilter ? "bg-zinc-900 text-white" : "text-zinc-500 hover:bg-zinc-100"}`}
        >
          All <span className="font-mono">{leads.length}</span>
        </button>
        <div className="w-px h-4 bg-zinc-200" />
        {(["hot", "warm", "cold", "disqualified"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTierFilter(tierFilter === t ? null : t)}
            className={`flex items-center gap-1.5 px-2 py-1 rounded-lg transition-colors ${tierFilter === t ? "bg-zinc-900 text-white" : "text-zinc-500 hover:bg-zinc-100"}`}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${tierFilter === t ? "bg-white" : TIER_DOT[t]}`} />
            <span className="capitalize">{t}</span>
            <span className="font-mono">{tierCounts[t] || 0}</span>
          </button>
        ))}
        <div className="ml-auto">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search..."
            className="w-36 px-2.5 py-1 text-xs bg-zinc-50 border border-zinc-200 rounded-lg focus:outline-none focus:ring-1 focus:ring-indigo-600/20 placeholder:text-zinc-400"
          />
        </div>
      </div>

      {/* Main */}
      <div className="flex flex-1 overflow-hidden">
        {/* Lead list */}
        <div className="w-80 lg:w-96 border-r border-zinc-200 bg-white overflow-y-auto shrink-0">
          {loading && <><LeadSkeleton /><LeadSkeleton /><LeadSkeleton /></>}
          {!loading && filtered.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-center px-6">
              <p className="text-sm text-zinc-500 mb-1">{leads.length === 0 ? "No leads yet" : "No matches"}</p>
              <p className="text-xs text-zinc-400">
                {leads.length === 0 ? (
                  <>Submit a lead from the <Link href="/" className="text-indigo-600">form</Link>.</>
                ) : "Try a different filter or search."}
              </p>
            </div>
          )}
          {filtered.map((lead) => (
            <button
              key={lead.email}
              onClick={() => selectLead(lead)}
              className={`w-full text-left px-5 py-3.5 border-b border-zinc-100 transition-colors ${selectedEmail === lead.email ? "bg-indigo-50/50" : "hover:bg-zinc-50"}`}
            >
              <div className="flex items-center justify-between mb-0.5">
                <span className="text-sm font-medium text-zinc-900 truncate pr-2">{lead.name || lead.email}</span>
                <div className="flex items-center gap-2 shrink-0">
                  {lead.score !== undefined && (
                    <span className="text-[10px] font-mono text-zinc-400">{lead.score}</span>
                  )}
                  {lead.tier && (
                    <span className={`text-[10px] px-2 py-0.5 rounded-lg font-semibold ${TIER_BADGE[lead.tier] || TIER_BADGE.cold}`}>
                      {lead.tier}
                    </span>
                  )}
                </div>
              </div>
              <div className="text-xs text-zinc-400 truncate">{lead.email}</div>
              {lead.company && <div className="text-[10px] text-zinc-400 mt-0.5 truncate">{lead.company}</div>}
            </button>
          ))}
        </div>

        {/* Detail panel */}
        <div className="flex-1 overflow-y-auto bg-zinc-50/50">
          {!selectedEmail && !detailLoading && (
            <div className="flex flex-col items-center justify-center h-full text-center px-6">
              <p className="text-sm text-zinc-500">Select a lead to view its triage trace</p>
            </div>
          )}

          {detailLoading && <DetailSkeleton />}

          {selectedEmail && !detailLoading && !selectedRun && selectedLead && (
            <div className="flex flex-col items-center justify-center h-full text-center px-6">
              <p className="text-sm font-medium text-zinc-700 mb-1">{selectedLead.name || selectedEmail}</p>
              <p className="text-xs text-zinc-400 mb-3">{selectedLead.tier && `${selectedLead.tier} / ${selectedLead.route || ""}`}</p>
              <p className="text-xs text-zinc-400">No triage trace found for this contact.</p>
              <p className="text-[10px] text-zinc-300 mt-1">The contact exists in CRM but has no linked run in the trace store.</p>
            </div>
          )}

          {selectedRun && selectedLead && (
            <div className="p-6 max-w-3xl space-y-5">
              {/* Lead header */}
              <div>
                <div className="flex items-start justify-between">
                  <div>
                    <h2 className="text-lg font-semibold tracking-tight text-zinc-900">{selectedLead.name || selectedEmail}</h2>
                    <p className="text-xs text-zinc-400 mt-0.5">{selectedEmail}</p>
                  </div>
                  {selectedLead.tier && (
                    <span className={`text-xs px-2.5 py-1 rounded-lg font-semibold ${TIER_BADGE[selectedLead.tier] || ""}`}>
                      {selectedLead.tier} / {ROUTE_LABEL[selectedLead.route || ""] || selectedLead.route}
                    </span>
                  )}
                </div>

                {/* Stats + deep links */}
                <div className="flex items-center gap-3 mt-3 text-[10px] text-zinc-400 font-mono">
                  <span>{selectedRun.event_count} events</span>
                  {hasRealMetrics && (
                    <>
                      <span>{selectedRun.stats.llm_call_count} LLM calls</span>
                      <span>{selectedRun.stats.total_duration_ms}ms</span>
                      <span>{selectedRun.stats.total_input_tokens + selectedRun.stats.total_output_tokens} tokens</span>
                      <span>${selectedRun.stats.estimated_cost_usd.toFixed(4)}</span>
                    </>
                  )}
                  {tr?.provider_used && (
                    <span className={tr.provider_used === "openai" ? "text-emerald-500" : "text-zinc-400"}>
                      {tr.provider_used}
                    </span>
                  )}
                  <span className="text-zinc-300">|</span>
                  <span>{selectedRun.run_id.slice(0, 8)}</span>
                  {config?.langfuse_enabled && config.langfuse_host && (
                    <a
                      href={`${config.langfuse_host}/traces?search=triage-${selectedRun.run_id.slice(0, 8)}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-indigo-600 hover:text-indigo-700 font-sans font-medium"
                    >
                      Langfuse
                    </a>
                  )}
                  {config?.crm_backend === "hubspot" ? (
                    <a
                      href={`https://app.hubspot.com/contacts/search?query=${encodeURIComponent(selectedEmail)}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-indigo-600 hover:text-indigo-700 font-sans font-medium"
                    >
                      HubSpot
                    </a>
                  ) : (
                    <span className="text-zinc-300 font-sans cursor-default" title="HubSpot backend not active">HubSpot</span>
                  )}
                </div>
              </div>

              {/* Draft outreach card */}
              {tr?.outreach?.subject && (
                <div className="bg-white border border-zinc-200 rounded-lg p-4">
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="text-xs font-semibold text-zinc-700">Draft Outreach</h3>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-600 font-medium">draft</span>
                      <button
                        onClick={() => copyDraft(tr.outreach?.body || "")}
                        className="text-[10px] font-medium text-indigo-600 hover:text-indigo-700 transition-colors"
                      >
                        {copied ? "Copied" : "Copy"}
                      </button>
                    </div>
                  </div>
                  <p className="text-sm font-medium text-zinc-800 mb-1">{tr.outreach.subject}</p>
                  <p className="text-xs text-zinc-500 leading-relaxed whitespace-pre-wrap">{tr.outreach.body}</p>
                </div>
              )}

              {/* Score + Enrichment cards */}
              <div className="grid grid-cols-2 gap-3">
                {tr?.score && (
                  <div className="bg-white border border-zinc-200 rounded-lg p-4">
                    <h3 className="text-xs font-semibold text-zinc-700 mb-2">Score</h3>
                    <div className="flex items-baseline gap-2 mb-2">
                      <span className="text-2xl font-bold text-zinc-900 font-mono">{tr.score.points ?? tr.score.rule_points}</span>
                      <span className="text-xs text-zinc-400">/ 100</span>
                    </div>
                    <div className="space-y-1 text-[11px] text-zinc-500">
                      {tr.score.rule_points !== undefined && (
                        <div className="flex justify-between">
                          <span>Rules</span>
                          <span className="font-mono">{tr.score.rule_points}</span>
                        </div>
                      )}
                      {tr.score.llm_adjustment !== undefined && tr.score.llm_adjustment !== 0 && (
                        <div className="flex justify-between">
                          <span>LLM adjustment</span>
                          <span className="font-mono">{tr.score.llm_adjustment > 0 ? "+" : ""}{tr.score.llm_adjustment}</span>
                        </div>
                      )}
                    </div>
                    {tr.score.llm_reason && (
                      <p className="text-[10px] text-zinc-400 mt-2 leading-relaxed">{tr.score.llm_reason}</p>
                    )}
                  </div>
                )}

                {tr?.enrichment && (
                  <div className="bg-white border border-zinc-200 rounded-lg p-4">
                    <h3 className="text-xs font-semibold text-zinc-700 mb-2">Enrichment</h3>
                    <div className="space-y-1.5 text-[11px]">
                      {([
                        ["Industry", tr.enrichment.industry],
                        ["Size", tr.enrichment.company_size],
                        ["Seniority", tr.enrichment.seniority],
                        ["Business email", tr.enrichment.is_business_email ? "Yes" : "No"],
                        ["Confidence", tr.enrichment.confidence],
                      ] as [string, unknown][]).filter(([, v]) => v !== undefined && v !== "unknown").map(([k, v]) => (
                        <div key={k as string} className="flex justify-between">
                          <span className="text-zinc-500">{k as string}</span>
                          <span className="text-zinc-700 font-medium">{String(v)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {/* Remaining runs indicator */}
              {config && config.remaining > 0 && (
                <p className="text-[10px] text-zinc-400 text-right">
                  {config.remaining} real runs left today
                </p>
              )}

              {/* Phased RAO Timeline */}
              <div>
                <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-3">Triage trace</h3>
                <div className="space-y-1">
                  {phases.map((phase, pi) => {
                    const isExpanded = expandedPhases.has(pi);
                    const isEnd = phase.name === "Complete";
                    const isStart = phase.name === "Start";
                    const phaseDuration = phase.events.reduce((s, e) => s + (e.duration_ms || 0), 0);

                    return (
                      <div key={pi} className="bg-white border border-zinc-200 rounded-lg overflow-hidden">
                        <button
                          onClick={() => togglePhase(pi)}
                          className="w-full flex items-center gap-2.5 px-3.5 py-2.5 text-left hover:bg-zinc-50 transition-colors"
                        >
                          <div className={`w-2 h-2 rounded-full shrink-0 ${isEnd ? "bg-emerald-500" : isStart ? "bg-zinc-300" : "bg-indigo-500"}`} />
                          <span className="text-sm font-medium text-zinc-800 flex-1">{phase.name}</span>
                          {phaseDuration > 0 && (
                            <span className="text-[10px] text-zinc-400 font-mono">{phaseDuration}ms</span>
                          )}
                          <span className="text-[10px] text-zinc-400">{phase.events.length} step{phase.events.length !== 1 ? "s" : ""}</span>
                          <svg className={`w-3 h-3 text-zinc-400 transition-transform ${isExpanded ? "rotate-90" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                          </svg>
                        </button>
                        {isExpanded && (
                          <div className="border-t border-zinc-100 px-3.5 py-2 space-y-1.5">
                            {phase.events.map((e, ei) => {
                              const typeColors: Record<string, string> = {
                                llm_call: "bg-indigo-50 text-indigo-700",
                                tool_call: "bg-emerald-50 text-emerald-700",
                                tool_response: "bg-zinc-100 text-zinc-600",
                                run_start: "bg-zinc-100 text-zinc-600",
                                run_end: "bg-emerald-50 text-emerald-700",
                              };
                              return (
                                <div key={ei} className="text-[11px]">
                                  <div className="flex items-center gap-2">
                                    <span className={`px-1.5 py-0.5 rounded font-mono font-medium ${typeColors[e.event_type] || "bg-zinc-100 text-zinc-600"}`}>
                                      {e.event_type.replace("_", " ")}
                                    </span>
                                    <span className="text-zinc-500">{e.agent}</span>
                                    {e.duration_ms != null && e.duration_ms > 0 && (
                                      <span className="text-zinc-400 font-mono ml-auto">{e.duration_ms}ms</span>
                                    )}
                                    {e.input_tokens != null && e.input_tokens > 0 && (
                                      <span className="text-zinc-400 font-mono">{e.input_tokens}+{e.output_tokens}tok</span>
                                    )}
                                  </div>
                                  <pre className="mt-1 text-[10px] text-zinc-400 font-mono whitespace-pre-wrap break-words max-h-32 overflow-y-auto leading-relaxed">
                                    {JSON.stringify(e.payload, null, 2).slice(0, 600)}
                                  </pre>
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
