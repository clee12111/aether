"use client";

import { useCallback, useEffect, useState } from "react";
import { Nav } from "@/components/nav";
import { apiGet } from "@/lib/api";
import { Lead } from "@/lib/tokens";

/* -- Stage data ----------------------------------------------------------- */

const STAGES = [
  { id: "intake", n: 1, title: "Intake", stacks: ["Web form", "Email", "Chat", "Clay"], desc: "Multi-channel lead ingestion. Each channel normalizes raw input into a Signal.", swap: "Add a channel by implementing ChannelAdapter (one file)." },
  { id: "parse", n: 2, title: "Parse", stacks: ["Extraction", "Signal protocol"], desc: "Extract name, email, intent, seniority from raw text. Deterministic heuristics + optional LLM.", swap: "Swap extraction provider via env." },
  { id: "enrich", n: 3, title: "Enrich", stacks: ["PDL", "Apollo", "Brave", "Website", "Productboard"], desc: "Company research: firmographics, funding, tech stack, demand signals.", swap: "Swap PDL for Clearbit by one env var. Each source toggles independently." },
  { id: "score", n: 4, title: "Score", stacks: ["Deterministic rules", "OpenAI nudge"], desc: "ICP fit scoring. Rules own the tier; LLM proposes a bounded +/-10 adjustment.", swap: "Swap OpenAI for Anthropic via GTM_PROVIDER. Rules stay deterministic." },
  { id: "draft", n: 5, title: "Draft", stacks: ["OpenAI", "A/B grounded", "Verifier"], desc: "LLM-composed outreach with grounding verification. Two variants, each citing facts.", swap: "Model via GTM_MODEL. Verifier is deterministic, always runs." },
  { id: "deliver", n: 6, title: "Deliver", stacks: ["HubSpot", "CRM", "Postgres"], desc: "Route to AE/SDR/nurture. Upsert to CRM + record outcome for the feedback loop.", swap: "CRM: HubSpot or SQLite or Postgres via CRM_BACKEND." },
  { id: "observe", n: 7, title: "Observe", stacks: ["Langfuse", "Postgres trace", "Sentry"], desc: "Every step traced. Cost, latency, tokens per run. Optional Langfuse + Sentry.", swap: "All observability no-op without config. Zero-cost when off." },
];

const CHIP_COLORS: Record<string, string> = {
  "Web form": "bg-stone-200 text-stone-700", Email: "bg-sky-100 text-sky-800", Chat: "bg-violet-100 text-violet-800", Clay: "bg-amber-100 text-amber-800",
  PDL: "bg-violet-100 text-violet-800", Apollo: "bg-sky-100 text-sky-800", Brave: "bg-amber-100 text-amber-800", Website: "bg-emerald-100 text-emerald-800", Productboard: "bg-rose-100 text-rose-800",
  "Deterministic rules": "bg-stone-200 text-stone-700", "OpenAI nudge": "bg-emerald-100 text-emerald-800", OpenAI: "bg-emerald-100 text-emerald-800",
  "A/B grounded": "bg-indigo-100 text-indigo-800", Verifier: "bg-stone-200 text-stone-700",
  HubSpot: "bg-amber-100 text-amber-800", CRM: "bg-stone-200 text-stone-700", Postgres: "bg-blue-100 text-blue-800",
  Langfuse: "bg-indigo-100 text-indigo-800", "Postgres trace": "bg-blue-100 text-blue-800", Sentry: "bg-red-100 text-red-800",
  Extraction: "bg-stone-200 text-stone-700", "Signal protocol": "bg-indigo-100 text-indigo-800",
};

/* -- Station positions (top-left of each card) inside 1200x560 viewBox ---- */
const CARD_POS: [number, number, number, number][] = [
  // [x, y, w, h]
  [55, 56, 240, 88],     // 1 Intake
  [405, 56, 240, 88],    // 2 Parse
  [755, 48, 240, 104],   // 3 Enrich (taller for 5 chips)
  [755, 256, 240, 88],   // 4 Score
  [405, 256, 240, 88],   // 5 Draft
  [55, 256, 240, 88],    // 6 Deliver
  [55, 426, 240, 88],    // 7 Observe
];

/* -- Road path (exact spec) ----------------------------------------------- */
const ROAD_D = "M150 100 H850 C1010 100 1010 300 850 300 H150 C20 300 20 470 150 470";
const ACCENT = "#4f46e5";

/* -- Component ------------------------------------------------------------ */

export default function ArchitecturePage() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [runs, setRuns] = useState<Array<Record<string, unknown>>>([]);
  const [active, setActive] = useState<number | null>(null);

  const fetchStats = useCallback(async () => {
    try {
      const [l, r] = await Promise.all([apiGet<Lead[]>("/leads"), apiGet<Array<Record<string, unknown>>>("/runs?limit=200")]);
      setLeads(l); setRuns(r);
    } catch { /* */ }
  }, []);
  useEffect(() => { fetchStats(); }, [fetchStats]);

  const totalLeads = leads.length;
  const totalCompanies = new Set(leads.map(l => l.email.includes("@") ? l.email.split("@")[1] : l.email)).size;
  const totalRuns = runs.length;
  const totalDrafts = leads.filter(l => l.tier === "hot" || l.tier === "warm").length;

  return (
    <div className="flex flex-col min-h-[100dvh]">
      <Nav />

      {/* Live stats strip */}
      <div className="border-b border-stone-200 bg-[var(--surface)] px-6 py-3 shrink-0" data-testid="live-stats">
        <div className="flex items-center gap-8">
          {[["Leads", totalLeads], ["Companies", totalCompanies], ["Drafts", totalDrafts], ["Runs", totalRuns]].map(([label, value]) => (
            <div key={String(label)} className="flex items-baseline gap-2">
              <span className="text-[10px] text-stone-400 uppercase tracking-wide font-medium">{String(label)}</span>
              <span className="text-lg font-bold text-stone-900 font-mono">{String(value)}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Diagram - ~80% of available space */}
      <div className="flex-1 flex flex-col items-center justify-center px-8 py-4 overflow-auto" style={{ background: "var(--background)" }}>
        <svg viewBox="0 0 1200 560" preserveAspectRatio="xMidYMid meet" className="w-[80%] max-h-[75vh]">
          <defs>
            <marker id="arr" viewBox="0 0 12 8" refX="12" refY="4" markerWidth="10" markerHeight="7" orient="auto-start-reverse">
              <polygon points="0 0, 12 4, 0 8" fill={ACCENT} />
            </marker>
            <marker id="arr-rose" viewBox="0 0 12 8" refX="12" refY="4" markerWidth="9" markerHeight="6" orient="auto-start-reverse">
              <polygon points="0 0, 12 4, 0 8" fill="#e11d48" />
            </marker>
          </defs>

          {/* Road bed (thick, faint accent) */}
          <path d={ROAD_D} fill="none" stroke={ACCENT} strokeOpacity="0.12" strokeWidth="24" strokeLinecap="round" />

          {/* Animated flow dashes */}
          <path d={ROAD_D} fill="none" stroke={ACCENT} strokeOpacity="0.5" strokeWidth="3" strokeDasharray="10 16" strokeLinecap="round">
            <animate attributeName="stroke-dashoffset" from="0" to="-52" dur="1.3s" repeatCount="indefinite" />
          </path>

          {/* Productboard loop */}
          {/* PB node at x=1000, y=158 (fully inside 1200 viewBox) */}
          <foreignObject x="1000" y="158" width="170" height="72">
            <div className="bg-rose-50 border-2 border-rose-300 rounded-xl p-2.5 h-full flex flex-col justify-center shadow-sm" data-testid="pb-loop">
              <p className="text-[11px] font-bold text-rose-700">Productboard</p>
              <p className="text-[9px] text-rose-500">Demand signals + write-back</p>
            </div>
          </foreignObject>

          {/* READ arrow: PB -> Enrich */}
          <path d="M1000 180 L900 120" fill="none" stroke="#e11d48" strokeWidth="1.5" markerEnd="url(#arr-rose)" />
          <text x="920" y="140" fill="#e11d48" fontSize="9" fontWeight="600">read: demand</text>

          {/* WRITE-BACK: dashed path Intake -> PB across top */}
          <path d="M295 60 Q650 10 1000 170" fill="none" stroke="#e11d48" strokeWidth="1.5" strokeDasharray="5 4" markerEnd="url(#arr-rose)" />
          <text x="560" y="28" fill="#e11d48" fontSize="9" fontWeight="600">write-back - new requests</text>

          {/* Station cards */}
          {STAGES.map((stage, i) => {
            const [x, y, w, h] = CARD_POS[i];
            const isActive = active === i;
            return (
              <foreignObject key={stage.id} x={x} y={y} width={w} height={h}>
                <button
                  onClick={() => setActive(isActive ? null : i)}
                  className={`w-full h-full rounded-xl p-2.5 text-left transition-all border-2 ${isActive ? "bg-indigo-50 border-indigo-400 shadow-md" : "bg-[var(--surface)] border-stone-200 hover:border-stone-300 hover:shadow-sm"}`}
                  data-testid={`stage-${stage.id}`}
                >
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className={`w-5 h-5 rounded-md flex items-center justify-center text-[9px] font-bold ${isActive ? "bg-indigo-600 text-white" : "bg-stone-200 text-stone-600"}`}>{stage.n}</span>
                    <span className="text-[11px] font-bold text-stone-800">{stage.title}</span>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {stage.stacks.map(s => (
                      <span key={s} className={`text-[8px] px-1.5 py-0.5 rounded-md font-semibold ${CHIP_COLORS[s] || "bg-stone-200 text-stone-700"}`}>{s}</span>
                    ))}
                  </div>
                </button>
              </foreignObject>
            );
          })}
        </svg>

        {/* Popover */}
        {active !== null && (
          <div className="w-full max-w-lg bg-[var(--surface)] border border-stone-200 rounded-xl p-4 shadow-md mt-2">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <span className="w-6 h-6 rounded-lg bg-indigo-600 text-white flex items-center justify-center text-[10px] font-bold">{STAGES[active].n}</span>
                <h3 className="text-sm font-bold text-stone-900">{STAGES[active].title}</h3>
              </div>
              <button onClick={() => setActive(null)} className="text-xs text-stone-400 hover:text-stone-600">Close</button>
            </div>
            <p className="text-xs text-stone-600 leading-relaxed mb-2">{STAGES[active].desc}</p>
            <p className="text-[10px] text-indigo-600 font-mono bg-indigo-50 px-2 py-1 rounded">{STAGES[active].swap}</p>
          </div>
        )}

        {/* Swappable footer */}
        <p className="text-center text-[10px] text-stone-400 leading-relaxed mt-4 max-w-xl">
          Every stop swaps behind one interface: CRM HubSpot / Salesforce - enrichment PDL / Apollo / Clearbit - model OpenAI / Anthropic - store Postgres / SQLite - search Brave / Tavily
        </p>
      </div>
    </div>
  );
}
