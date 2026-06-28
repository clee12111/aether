"use client";

import { useEffect, useState, useCallback } from "react";
import { Nav } from "@/components/nav";
import { apiGet } from "@/lib/api";
import { RunDetail, TIER_BADGE, TIER_DOT, CHANNEL_DOT, groupIntoPhases } from "@/lib/tokens";

/* -- Types ---------------------------------------------------------------- */

interface RunSummary { run_id: string; lead_email: string; final_tier: string; event_count: number; }

interface JourneyRun {
  run_id: string; motion: string; run_type: string; final_tier: string; trace_path: string;
  event_count: number; stats: RunDetail["stats"]; events: RunDetail["events"];
}

interface JourneyData { email: string; runs: JourneyRun[]; }

interface CompanyGroup { domain: string; company: string; emails: string[]; hottestTier: string; runCount: number; }

type SortMode = "tier" | "recent";
const TIER_ORDER: Record<string, number> = { hot: 0, warm: 1, cold: 2, disqualified: 3, campaign: 4 };

/* -- Pipeline ------------------------------------------------------------- */

const IB = [["channel","Channel"],["parse","Parse"],["enrich","Enrich"],["score","Score"],["draft","Draft"],["route","Route"]];
const OB = [["research","Research"],["fit","Fit Score"],["draft_ob","Draft A/B"],["route_ob","Route"]];

const TRACE_LIT: Record<string, Set<string>> = {
  CLEAN_FULL_PATH: new Set(IB.map(n=>n[0])), SHORT_CIRCUIT_INVALID: new Set(["channel","parse"]),
  SHORT_CIRCUIT_INTENT: new Set(["channel","parse"]), CRM_HIT_SKIP_ENRICH: new Set(["channel","parse","score","draft","route"]),
  LOW_CONFIDENCE_GATE: new Set(IB.map(n=>n[0])), DIG_DEEPER: new Set(IB.map(n=>n[0])),
  OUTBOUND_DRAFTED: new Set(OB.map(n=>n[0])), OUTBOUND_NO_DRAFT: new Set(["research","fit"]),
  OUTBOUND_DISQUALIFIED: new Set(), OUTBOUND_CAMPAIGN: new Set(),
};

function PipelineRow({ label, nodes, lit }: { label: string; nodes: string[][]; lit: Set<string> }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[10px] text-stone-500 w-16 shrink-0 font-medium">{label}</span>
      {nodes.map(([id, name], i) => (
        <div key={id} className="flex items-center">
          <div className={`px-3.5 py-1.5 rounded-lg text-[11px] font-medium ${lit.has(id) ? "bg-indigo-600 text-white" : "bg-stone-100 text-stone-400"}`}>{name}</div>
          {i < nodes.length - 1 && <div className={`w-5 h-px ${lit.has(id) ? "bg-indigo-400" : "bg-stone-200"}`} />}
        </div>
      ))}
    </div>
  );
}

/* -- Trace Column --------------------------------------------------------- */

function TraceCol({ title, run }: { title: string; run: JourneyRun | undefined }) {
  const [exp, setExp] = useState<Set<number>>(new Set());
  if (!run) return (
    <div className="bg-[var(--surface)] border border-stone-200 rounded-lg p-6 flex items-center justify-center min-h-[100px]">
      <p className="text-xs text-stone-400">{title}: not run yet</p>
    </div>
  );

  const phases = groupIntoPhases(run.events);
  const tc: Record<string, string> = { llm_call: "bg-indigo-50 text-indigo-700", tool_call: "bg-emerald-50 text-emerald-700", tool_response: "bg-stone-100 text-stone-600", run_start: "bg-stone-100 text-stone-600", run_end: "bg-emerald-50 text-emerald-700", dig_deeper: "bg-amber-50 text-amber-700" };

  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <h4 className="text-sm font-semibold text-stone-800">{title}</h4>
        {run.final_tier && <span className={`text-[10px] px-2 py-0.5 rounded-lg font-semibold ${TIER_BADGE[run.final_tier] || "bg-stone-100 text-stone-500"}`}>{run.final_tier}</span>}
        <span className="text-[10px] font-mono text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded-lg">{run.trace_path}</span>
      </div>
      <div className="flex flex-wrap gap-x-4 text-[10px] text-stone-400 font-mono mb-3">
        <span>{run.event_count} events</span>
        {run.stats.llm_call_count > 0 && <span>{run.stats.llm_call_count} LLM calls</span>}
        {run.stats.total_duration_ms > 0 && <span>{run.stats.total_duration_ms}ms</span>}
        {(run.stats.total_input_tokens + run.stats.total_output_tokens) > 0 && <span>{run.stats.total_input_tokens + run.stats.total_output_tokens} tokens</span>}
        {run.stats.estimated_cost_usd > 0 && <span>${run.stats.estimated_cost_usd.toFixed(4)}</span>}
      </div>
      <div className="space-y-1">
        {phases.map((p, pi) => {
          const isE = exp.has(pi); const dur = p.events.reduce((s, e) => s + (e.duration_ms || 0), 0);
          return (
            <div key={`${run.run_id}-${pi}`} className="bg-[var(--surface)] border border-stone-200 rounded-lg overflow-hidden">
              <button onClick={() => setExp(prev => { const n = new Set(prev); n.has(pi) ? n.delete(pi) : n.add(pi); return n; })}
                className="w-full flex items-center gap-2.5 px-3 py-2 text-left hover:bg-stone-50 transition-colors">
                <div className={`w-2 h-2 rounded-full shrink-0 ${p.name === "Complete" ? "bg-emerald-500" : p.name === "Start" ? "bg-stone-300" : "bg-indigo-500"}`} />
                <span className="text-xs font-medium text-stone-800 flex-1">{p.name}</span>
                {dur > 0 && <span className="text-[10px] text-stone-400 font-mono">{dur}ms</span>}
                <span className="text-[10px] text-stone-400">{p.events.length}</span>
                <svg className={`w-3 h-3 text-stone-400 transition-transform ${isE ? "rotate-90" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}><path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" /></svg>
              </button>
              {isE && (
                <div className="border-t border-stone-100 px-3 py-2 space-y-1.5">
                  {p.events.map((e, ei) => (
                    <div key={`${run.run_id}-${pi}-${ei}`} className="text-[11px]">
                      <div className="flex items-center gap-2">
                        <span className={`px-1.5 py-0.5 rounded font-mono font-medium ${tc[e.event_type] || "bg-stone-100 text-stone-600"}`}>{e.event_type.replace(/_/g, " ")}</span>
                        <span className="text-stone-500">{e.agent}</span>
                        {e.duration_ms != null && e.duration_ms > 0 && <span className="text-stone-400 font-mono ml-auto">{e.duration_ms}ms</span>}
                        {e.input_tokens != null && e.input_tokens > 0 && <span className="text-stone-400 font-mono">{e.input_tokens}+{e.output_tokens}tok</span>}
                      </div>
                      <pre className="mt-1 text-[10px] text-stone-400 font-mono whitespace-pre-wrap break-words max-h-28 overflow-y-auto leading-relaxed">{JSON.stringify(e.payload, null, 2).slice(0, 600)}</pre>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* -- Component ------------------------------------------------------------ */

export default function TestingPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState("");
  const [selectedDomain, setSelectedDomain] = useState<string | null>(null);
  const [selectedEmail, setSelectedEmail] = useState<string | null>(null);
  const [journey, setJourney] = useState<JourneyData | null>(null);
  const [domainCampaign, setDomainCampaign] = useState<Record<string, unknown> | null>(null);
  const [jLoading, setJLoading] = useState(false);
  const [sortMode, setSortMode] = useState<SortMode>("tier");

  const fetchRuns = useCallback(async () => {
    setLoading(true);
    try {
      setFetchError("");
      setRuns(await apiGet<RunSummary[]>("/runs?limit=50"));
    } catch (err) {
      setFetchError(err instanceof Error ? err.message : "Failed to load runs");
    }
    finally { setLoading(false); }
  }, []);

  // Fetch on every mount (including client-side navigation)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { fetchRuns(); }, []);

  async function loadJourney(email: string) {
    setSelectedEmail(email); setJourney(null); setDomainCampaign(null); setJLoading(true);
    try {
      const j = await apiGet<JourneyData>(`/journey/${encodeURIComponent(email)}`);
      setJourney(j);
      // Also load domain campaign
      const domain = email.includes("@") ? email.split("@")[1] : "";
      if (domain) {
        try {
          const c = await apiGet<{ campaign: Record<string, unknown> | null }>(`/outbound/campaign/${encodeURIComponent(domain)}`);
          setDomainCampaign(c.campaign);
        } catch { /* */ }
      }
    } catch { /* */ }
    finally { setJLoading(false); }
  }

  // Group by company domain
  const companies: CompanyGroup[] = (() => {
    const byDomain: Record<string, { emails: Set<string>; tiers: string[]; count: number }> = {};
    for (const run of runs) {
      if (!run.lead_email) continue;  // skip runs with no email (campaign synthetic)
      const domain = run.lead_email.includes("@") ? run.lead_email.split("@")[1] : run.lead_email;
      if (domain.startsWith("campaign")) continue;  // skip campaign@ synthetic entries
      if (!byDomain[domain]) byDomain[domain] = { emails: new Set(), tiers: [], count: 0 };
      byDomain[domain].emails.add(run.lead_email);
      byDomain[domain].tiers.push(run.final_tier);
      byDomain[domain].count++;
    }
    return Object.entries(byDomain).map(([domain, data]) => {
      const hottestTier = [...data.tiers].sort((a, b) => (TIER_ORDER[a] ?? 9) - (TIER_ORDER[b] ?? 9))[0] || "cold";
      return { domain, company: domain, emails: [...data.emails], hottestTier, runCount: data.count };
    }).sort((a, b) => {
      if (sortMode === "tier") return (TIER_ORDER[a.hottestTier] ?? 9) - (TIER_ORDER[b.hottestTier] ?? 9);
      return 0;
    });
  })();

  const selCompany = selectedDomain ? companies.find(c => c.domain === selectedDomain) : null;

  const inboundRun = journey?.runs.find(r => r.run_type === "inbound" || (!r.run_type && r.motion !== "outbound"));
  const emailRun = journey?.runs.find(r => r.run_type === "outbound_email");
  const campaignRun = journey?.runs.find(r => r.run_type === "outbound_campaign");
  const ibLit = inboundRun ? (TRACE_LIT[inboundRun.trace_path] || new Set<string>()) : new Set<string>();
  const obLit = (emailRun || campaignRun) ? (TRACE_LIT[(emailRun || campaignRun)!.trace_path] || new Set<string>()) : new Set<string>();

  const allRuns = journey?.runs || [];
  const totalEvents = allRuns.reduce((s, r) => s + r.event_count, 0);
  const totalLLM = allRuns.reduce((s, r) => s + (r.stats?.llm_call_count || 0), 0);
  const totalDuration = allRuns.reduce((s, r) => s + (r.stats?.total_duration_ms || 0), 0);
  const totalTokens = allRuns.reduce((s, r) => s + (r.stats?.total_input_tokens || 0) + (r.stats?.total_output_tokens || 0), 0);
  const totalCost = allRuns.reduce((s, r) => s + (r.stats?.estimated_cost_usd || 0), 0);

  return (
    <div className="flex flex-col min-h-[100dvh] h-[100dvh]">
      <Nav />
      {/* Pipeline */}
      <div className="border-b border-stone-200 bg-[var(--surface)] px-6 py-4 shrink-0 space-y-2.5">
        <PipelineRow label="Inbound" nodes={IB} lit={ibLit} />
        <PipelineRow label="Outbound" nodes={OB} lit={obLit} />
      </div>

      <div className="flex-1 flex overflow-hidden">
        {/* Left: company list */}
        <div className="w-64 border-r border-stone-200 bg-[var(--surface)] flex flex-col shrink-0">
          <div className="px-3 py-2.5 flex items-center justify-between border-b border-stone-200">
            <h3 className="text-[10px] font-semibold text-stone-400 uppercase tracking-wide">Companies</h3>
            <div className="flex items-center gap-2">
              <select value={sortMode} onChange={e => setSortMode(e.target.value as SortMode)} className="text-[9px] bg-stone-50 border border-stone-200 rounded px-1 py-0.5 text-stone-500">
                <option value="tier">Hot first</option>
                <option value="recent">Recent</option>
              </select>
              <button onClick={fetchRuns} className="text-[10px] text-indigo-600 hover:text-indigo-700 font-medium">Refresh</button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto">
            {loading && <div className="p-3 space-y-1">{[1, 2, 3].map(i => <div key={i} className="skeleton h-8 rounded-lg" />)}</div>}
            {fetchError && <p className="p-3 text-xs text-red-500">{fetchError}</p>}
            {!loading && !fetchError && companies.length === 0 && <p className="p-3 text-xs text-stone-400">No runs yet. Submit leads in Inbound.</p>}
            {companies.map(co => (
              <button key={co.domain} onClick={() => { setSelectedDomain(co.domain); setSelectedEmail(null); setJourney(null); }}
                className={`w-full text-left px-3 py-2 border-b border-stone-100 transition-colors ${selectedDomain === co.domain ? "bg-indigo-50/50 border-l-2 border-l-indigo-500" : "hover:bg-stone-50"}`}>
                <div className="flex items-center justify-between">
                  <span className="text-[11px] text-stone-700 truncate pr-1 font-medium">{co.domain}</span>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <span className={`w-2 h-2 rounded-full ${TIER_DOT[co.hottestTier] || "bg-stone-400"}`} />
                    <span className="text-[9px] text-stone-400 font-mono">{co.emails.length}</span>
                  </div>
                </div>
              </button>
            ))}
          </div>
          {/* Legends */}
          <div className="px-3 py-2.5 border-t border-stone-200 space-y-2">
            <div>
              <p className="text-[8px] font-semibold text-stone-400 uppercase tracking-wide mb-1">Tier</p>
              <div className="flex flex-wrap gap-x-3 gap-y-1">
                {(["hot", "warm", "cold", "disqualified"] as const).map(t => (
                  <div key={t} className="flex items-center gap-1">
                    <span className={`w-2.5 h-2.5 rounded-full ${TIER_DOT[t]}`} />
                    <span className="text-[10px] text-stone-500 capitalize">{t}</span>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <p className="text-[8px] font-semibold text-stone-400 uppercase tracking-wide mb-1">Channel</p>
              <div className="flex flex-wrap gap-x-3 gap-y-1">
                {[["web_form", "Form"], ["email", "Email"], ["chat", "Chat"], ["clay", "Clay"]].map(([key, label]) => (
                  <div key={key} className="flex items-center gap-1">
                    <span className={`w-2.5 h-2.5 rounded-full ${CHANNEL_DOT[key]}`} />
                    <span className="text-[10px] text-stone-400">{label}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Right: company detail */}
        <div className="flex-1 overflow-y-auto p-5" style={{ background: "var(--background)" }}>
          {!selectedDomain && <div className="flex items-center justify-center h-full"><p className="text-sm text-stone-400">Select a company to inspect its journey</p></div>}

          {selCompany && !selectedEmail && !journey && (
            <div className="max-w-3xl space-y-4">
              <h2 className="text-sm font-semibold text-stone-900">{selCompany.domain}</h2>
              <div className="bg-[var(--surface)] border border-stone-200 rounded-lg p-3">
                <h3 className="text-[10px] font-semibold text-stone-400 uppercase tracking-wide mb-2">Contacts ({selCompany.emails.length})</h3>
                {selCompany.emails.map(email => {
                  const emailRuns = runs.filter(r => r.lead_email === email);
                  const tier = emailRuns[0]?.final_tier;
                  return (
                    <button key={email} onClick={() => { setSelectedEmail(email); loadJourney(email); }}
                      className="w-full text-left px-2 py-1.5 rounded-lg hover:bg-stone-50 flex items-center justify-between transition-colors">
                      <span className="text-xs text-stone-700 truncate">{email}</span>
                      <div className="flex items-center gap-1.5 shrink-0">
                        {tier && <span className={`text-[9px] px-1.5 py-0.5 rounded font-semibold ${TIER_BADGE[tier] || ""}`}>{tier}</span>}
                        <span className="text-[9px] text-stone-400 font-mono">{emailRuns.length} runs</span>
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {jLoading && (
            <div className="grid grid-cols-3 gap-4 max-w-5xl">
              {[1, 2, 3].map(i => <div key={i} className="space-y-2"><div className="skeleton h-6 w-28" /><div className="skeleton h-40 rounded-lg" /></div>)}
            </div>
          )}

          {journey && selectedEmail && (
            <div className="max-w-5xl space-y-5">
              <div className="flex items-center gap-2">
                <button onClick={() => { setSelectedEmail(null); setJourney(null); }} className="text-[10px] text-indigo-600 hover:text-indigo-700">Back to contacts</button>
                <span className="text-stone-300">|</span>
                <span className="text-sm font-semibold text-stone-900">{selectedEmail}</span>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
                <TraceCol title="Inbound" run={inboundRun} />
                <TraceCol title="Outbound Email" run={emailRun} />
                {campaignRun ? (
                  <TraceCol title="Outbound Campaign" run={campaignRun} />
                ) : domainCampaign ? (
                  <div className="bg-[var(--surface)] border border-stone-200 rounded-lg p-4" data-testid="campaign-trace">
                    <div className="flex items-center gap-2 mb-2">
                      <h4 className="text-sm font-semibold text-stone-800">Account Campaign</h4>
                      <span className="text-[9px] text-emerald-600 bg-emerald-50 px-1.5 py-0.5 rounded font-medium">{String(domainCampaign.status || "launched")}</span>
                    </div>
                    <p className="text-xs text-stone-600 mb-2">{String(domainCampaign.campaign_name || "")}</p>
                    {Array.isArray(domainCampaign.targets) && (
                      <div className="space-y-1">
                        {(domainCampaign.targets as Array<Record<string, unknown>>).map((t, i) => (
                          <div key={i} className="flex justify-between text-[10px] py-1 border-b border-stone-100 last:border-0">
                            <span className="text-stone-700 font-medium">{String(t.company)}</span>
                            {t.domain ? <span className="text-stone-400">{String(t.domain)}</span> : null}
                          </div>
                        ))}
                      </div>
                    )}
                    <p className="text-[9px] text-stone-400 mt-2">{String(domainCampaign.targets_processed || 0)} targets via Apollo</p>
                  </div>
                ) : (
                  <div className="bg-[var(--surface)] border border-stone-200 rounded-lg p-6 flex items-center justify-center min-h-[100px]">
                    <p className="text-xs text-stone-400">Outbound Campaign: not run yet</p>
                  </div>
                )}
              </div>

              {allRuns.length > 0 && (
                <div className="bg-[var(--surface)] border border-stone-200 rounded-lg p-4" data-testid="e2e-metrics">
                  <h3 className="text-xs font-semibold text-stone-500 uppercase tracking-wide mb-3">End-to-end totals</h3>
                  <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                    {[["Runs", allRuns.length], ["Events", totalEvents], ["LLM calls", totalLLM], ["Duration", `${totalDuration}ms`], ["Tokens", totalTokens], ["Cost", totalCost > 0 ? `$${totalCost.toFixed(4)}` : "$0"]].map(([label, value]) => (
                      <div key={String(label)}>
                        <div className="text-[10px] text-stone-500">{String(label)}</div>
                        <div className="text-lg font-bold text-stone-900 font-mono">{String(value)}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
