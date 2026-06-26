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

interface RunSummary {
  run_id: string;
  lead_email?: string;
  final_tier?: string;
  final_route?: string;
  steps?: number;
  started_at?: string;
  event_count?: number;
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

const EVENT_STYLE: Record<string, { bg: string; label: string }> = {
  run_start: { bg: "bg-zinc-100 text-zinc-600", label: "start" },
  llm_call: { bg: "bg-indigo-50 text-indigo-700", label: "llm" },
  tool_call: { bg: "bg-emerald-50 text-emerald-700", label: "tool" },
  tool_response: { bg: "bg-emerald-50 text-emerald-700", label: "result" },
  run_end: { bg: "bg-zinc-100 text-zinc-600", label: "end" },
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

function friendlyEvent(e: { event_type: string; agent: string; payload: Record<string, unknown> }): string {
  if (e.event_type === "run_start") return "Triage started";
  if (e.event_type === "run_end") {
    const tier = e.payload?.final_tier as string;
    return tier ? `Completed: ${tier}` : "Triage complete";
  }
  if (e.event_type === "tool_call") {
    const tool = e.payload?.tool as string;
    return tool || "Tool call";
  }
  if (e.event_type === "llm_call") {
    if (e.agent?.startsWith("tool.")) return `LLM (${e.agent.replace("tool.", "")})`;
    return "Agent reasoning";
  }
  return e.event_type;
}

/* ── Skeleton components ─────────────────────────────────────────────────── */

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
      <div className="space-y-3 mt-6">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="flex gap-3">
            <div className="skeleton h-5 w-5 rounded-full shrink-0 mt-0.5" />
            <div className="flex-1 space-y-1.5">
              <div className="skeleton h-4 w-32" />
              <div className="skeleton h-3 w-48" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Main component ──────────────────────────────────────────────────────── */

export default function OpsDashboard() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [, setRuns] = useState<RunSummary[]>([]);
  const [selectedRun, setSelectedRun] = useState<RunDetail | null>(null);
  const [selectedEmail, setSelectedEmail] = useState("");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [expandedEvents, setExpandedEvents] = useState<Set<number>>(new Set());

  const fetchData = useCallback(async () => {
    try {
      const [l, r] = await Promise.all([
        apiGet<Lead[]>("/leads"),
        apiGet<RunSummary[]>("/runs"),
      ]);
      setLeads(l);
      setRuns(r);
    } catch {
      /* API may be down */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 2000);
    return () => clearInterval(id);
  }, [fetchData]);

  async function selectLead(lead: Lead) {
    setSelectedEmail(lead.email);
    setSelectedRun(null);
    setExpandedEvents(new Set());
    if (lead.run_id) {
      setDetailLoading(true);
      try {
        const detail = await apiGet<RunDetail>(`/runs/${lead.run_id}`);
        setSelectedRun(detail);
      } catch {
        setSelectedRun(null);
      } finally {
        setDetailLoading(false);
      }
    }
  }

  function toggleEvent(i: number) {
    setExpandedEvents((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  }

  const tierCounts = leads.reduce<Record<string, number>>((acc, l) => {
    const t = l.tier || "unknown";
    acc[t] = (acc[t] || 0) + 1;
    return acc;
  }, {});

  const selectedLead = leads.find((l) => l.email === selectedEmail);

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
        </div>
        <Link
          href="/"
          className="text-xs font-medium text-zinc-500 hover:text-zinc-900 transition-colors"
        >
          Lead Form
        </Link>
      </header>

      {/* Metric strip */}
      <div className="border-b border-zinc-200 bg-white px-6 py-2.5 flex items-center gap-5 text-xs shrink-0 overflow-x-auto">
        <div className="flex items-center gap-1.5">
          <span className="text-zinc-500">Total</span>
          <span className="font-semibold text-zinc-900 font-mono">{leads.length}</span>
        </div>
        <div className="w-px h-4 bg-zinc-200" />
        {(["hot", "warm", "cold", "disqualified"] as const).map((t) => (
          <div key={t} className="flex items-center gap-1.5">
            <span className={`w-1.5 h-1.5 rounded-full ${TIER_DOT[t]}`} />
            <span className="text-zinc-500 capitalize">{t}</span>
            <span className="font-semibold text-zinc-900 font-mono">{tierCounts[t] || 0}</span>
          </div>
        ))}
      </div>

      {/* Main */}
      <div className="flex flex-1 overflow-hidden">
        {/* Lead list */}
        <div className="w-80 lg:w-96 border-r border-zinc-200 bg-white overflow-y-auto shrink-0">
          {loading && (
            <>
              <LeadSkeleton />
              <LeadSkeleton />
              <LeadSkeleton />
            </>
          )}
          {!loading && leads.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-center px-6">
              <div className="w-10 h-10 rounded-full bg-zinc-100 flex items-center justify-center mb-3">
                <svg className="w-5 h-5 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M18 18.72a9.094 9.094 0 003.741-.479 3 3 0 00-4.682-2.72m.94 3.198l.001.031c0 .225-.012.447-.037.666A11.944 11.944 0 0112 21c-2.17 0-4.207-.576-5.963-1.584A6.062 6.062 0 016 18.719m12 0a5.971 5.971 0 00-.941-3.197m0 0A5.995 5.995 0 0012 12.75a5.995 5.995 0 00-5.058 2.772m0 0a3 3 0 00-4.681 2.72 8.986 8.986 0 003.74.477m.94-3.197a5.971 5.971 0 00-.94 3.197M15 6.75a3 3 0 11-6 0 3 3 0 016 0zm6 3a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0zm-13.5 0a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0z" />
                </svg>
              </div>
              <p className="text-sm font-medium text-zinc-700 mb-1">No leads yet</p>
              <p className="text-xs text-zinc-400">
                Submit a lead from the{" "}
                <Link href="/" className="text-indigo-600 hover:text-indigo-700">
                  form
                </Link>{" "}
                to see it here.
              </p>
            </div>
          )}
          {leads.map((lead) => (
            <button
              key={lead.email}
              onClick={() => selectLead(lead)}
              className={`w-full text-left px-5 py-3.5 border-b border-zinc-100 transition-colors ${
                selectedEmail === lead.email
                  ? "bg-indigo-50/50"
                  : "hover:bg-zinc-50"
              }`}
            >
              <div className="flex items-center justify-between mb-0.5">
                <span className="text-sm font-medium text-zinc-900 truncate pr-2">
                  {lead.name || lead.email}
                </span>
                {lead.tier && (
                  <span
                    className={`text-[10px] px-2 py-0.5 rounded-lg font-semibold shrink-0 ${
                      TIER_BADGE[lead.tier] || TIER_BADGE.cold
                    }`}
                  >
                    {lead.tier}
                  </span>
                )}
              </div>
              <div className="text-xs text-zinc-400 truncate">{lead.email}</div>
              <div className="flex items-center gap-2 mt-1 text-[10px] text-zinc-400">
                {lead.company && <span className="truncate">{lead.company}</span>}
                {lead.score !== undefined && (
                  <span className="font-mono">{lead.score} pts</span>
                )}
                {lead.last_activity_at && <span>{timeAgo(lead.last_activity_at)}</span>}
              </div>
            </button>
          ))}
        </div>

        {/* Detail panel */}
        <div className="flex-1 overflow-y-auto bg-zinc-50/50">
          {!selectedEmail && !detailLoading && (
            <div className="flex flex-col items-center justify-center h-full text-center px-6">
              <div className="w-10 h-10 rounded-full bg-zinc-100 flex items-center justify-center mb-3">
                <svg className="w-5 h-5 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 9.776c.112-.017.227-.026.344-.026h15.812c.117 0 .232.009.344.026m-16.5 0a2.25 2.25 0 00-1.883 2.542l.857 6a2.25 2.25 0 002.227 1.932H19.05a2.25 2.25 0 002.227-1.932l.857-6a2.25 2.25 0 00-1.883-2.542m-16.5 0V6A2.25 2.25 0 016 3.75h3.879a1.5 1.5 0 011.06.44l2.122 2.12a1.5 1.5 0 001.06.44H18A2.25 2.25 0 0120.25 9v.776" />
                </svg>
              </div>
              <p className="text-sm text-zinc-500">Select a lead to view its triage trace</p>
            </div>
          )}

          {detailLoading && <DetailSkeleton />}

          {selectedRun && selectedLead && (
            <div className="p-6 max-w-3xl">
              {/* Lead header */}
              <div className="mb-6">
                <div className="flex items-start justify-between mb-2">
                  <div>
                    <h2 className="text-lg font-semibold tracking-tight text-zinc-900">
                      {selectedLead.name || selectedEmail}
                    </h2>
                    <p className="text-xs text-zinc-400 mt-0.5">{selectedEmail}</p>
                  </div>
                  {selectedLead.tier && (
                    <span
                      className={`text-xs px-2.5 py-1 rounded-lg font-semibold ${
                        TIER_BADGE[selectedLead.tier] || ""
                      }`}
                    >
                      {selectedLead.tier} / {ROUTE_LABEL[selectedLead.route || ""] || selectedLead.route}
                    </span>
                  )}
                </div>

                {/* Attribute pills */}
                <div className="flex flex-wrap gap-2 mt-3">
                  {selectedLead.score !== undefined && (
                    <span className="inline-flex items-center gap-1 text-xs bg-zinc-100 text-zinc-600 px-2 py-1 rounded-lg font-mono">
                      {selectedLead.score} pts
                    </span>
                  )}
                  {selectedLead.industry && selectedLead.industry !== "unknown" && (
                    <span className="text-xs bg-zinc-100 text-zinc-600 px-2 py-1 rounded-lg">
                      {selectedLead.industry}
                    </span>
                  )}
                  {selectedLead.seniority && selectedLead.seniority !== "unknown" && (
                    <span className="text-xs bg-zinc-100 text-zinc-600 px-2 py-1 rounded-lg">
                      {selectedLead.seniority}
                    </span>
                  )}
                  {selectedLead.company && (
                    <span className="text-xs bg-zinc-100 text-zinc-600 px-2 py-1 rounded-lg">
                      {selectedLead.company}
                    </span>
                  )}
                </div>

                {/* Stats row */}
                <div className="flex items-center gap-4 mt-4 text-[10px] text-zinc-400 font-mono">
                  <span>{selectedRun.event_count} events</span>
                  <span>{selectedRun.stats.llm_call_count} LLM calls</span>
                  <span>{selectedRun.stats.total_duration_ms}ms</span>
                  <span>
                    {selectedRun.stats.total_input_tokens + selectedRun.stats.total_output_tokens}{" "}
                    tokens
                  </span>
                  <span>${selectedRun.stats.estimated_cost_usd.toFixed(4)}</span>
                </div>

                {/* Deep links */}
                <div className="flex items-center gap-2 mt-3">
                  <span className="text-[10px] text-zinc-400 font-mono">
                    {selectedRun.run_id.slice(0, 8)}
                  </span>
                  <a
                    href={`https://app.hubspot.com/contacts/search?query=${encodeURIComponent(selectedEmail)}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[10px] font-medium text-indigo-600 hover:text-indigo-700 transition-colors"
                  >
                    HubSpot
                  </a>
                  <a
                    href={`https://us.cloud.langfuse.com/traces?search=${selectedRun.run_id.slice(0, 8)}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[10px] font-medium text-indigo-600 hover:text-indigo-700 transition-colors"
                  >
                    Langfuse
                  </a>
                </div>
              </div>

              {/* RAO Timeline */}
              <div>
                <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-4">
                  Triage trace
                </h3>
                <div className="relative">
                  {/* Timeline line */}
                  <div className="absolute left-[9px] top-3 bottom-3 w-px bg-zinc-200" />

                  <div className="space-y-0">
                    {selectedRun.events
                      .filter(
                        (e) =>
                          e.event_type === "run_start" ||
                          e.event_type === "llm_call" ||
                          e.event_type === "tool_call" ||
                          e.event_type === "tool_response" ||
                          e.event_type === "run_end"
                      )
                      .map((e, i) => {
                        const style = EVENT_STYLE[e.event_type] || EVENT_STYLE.run_start;
                        const isExpanded = expandedEvents.has(i);
                        const isCompleted = e.event_type === "run_end";

                        return (
                          <div key={i} className="relative flex gap-3 pb-4">
                            {/* Dot */}
                            <div
                              className={`relative z-10 w-[19px] h-[19px] rounded-full border-2 flex items-center justify-center shrink-0 mt-0.5 ${
                                isCompleted
                                  ? "border-emerald-500 bg-emerald-50"
                                  : e.event_type === "run_start"
                                  ? "border-zinc-300 bg-white"
                                  : "border-indigo-400 bg-indigo-50"
                              }`}
                            >
                              {isCompleted && (
                                <svg className="w-2.5 h-2.5 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                                </svg>
                              )}
                            </div>

                            {/* Content */}
                            <div className="flex-1 min-w-0">
                              <button
                                onClick={() => toggleEvent(i)}
                                className="flex items-center gap-2 w-full text-left group"
                              >
                                <span
                                  className={`text-[10px] px-1.5 py-0.5 rounded font-mono font-medium ${style.bg}`}
                                >
                                  {style.label}
                                </span>
                                <span className="text-sm text-zinc-700 truncate">
                                  {friendlyEvent(e)}
                                </span>
                                {e.duration_ms != null && e.duration_ms > 0 && (
                                  <span className="text-[10px] text-zinc-400 font-mono ml-auto shrink-0">
                                    {e.duration_ms}ms
                                  </span>
                                )}
                                <svg
                                  className={`w-3 h-3 text-zinc-400 transition-transform shrink-0 ${
                                    isExpanded ? "rotate-90" : ""
                                  }`}
                                  fill="none"
                                  viewBox="0 0 24 24"
                                  stroke="currentColor"
                                  strokeWidth={2}
                                >
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                                </svg>
                              </button>

                              {isExpanded && (
                                <pre className="mt-2 text-[11px] text-zinc-500 font-mono bg-white border border-zinc-200 rounded-lg p-3 whitespace-pre-wrap break-words max-h-48 overflow-y-auto leading-relaxed">
                                  {JSON.stringify(e.payload, null, 2)}
                                </pre>
                              )}
                            </div>
                          </div>
                        );
                      })}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
