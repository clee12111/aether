"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiPost, warmup } from "@/lib/api";

interface TriageResult {
  run_id: string;
  final_tier: string;
  final_route: string;
  score?: { points: number; reason: string; rule_points?: number; llm_adjustment?: number };
  outreach?: { subject: string; body: string; status: string };
  enrichment?: Record<string, unknown>;
}

const TIER_STYLE: Record<string, { bg: string; label: string }> = {
  hot: { bg: "bg-red-100 text-red-700", label: "Hot" },
  warm: { bg: "bg-amber-100 text-amber-700", label: "Warm" },
  cold: { bg: "bg-blue-100 text-blue-700", label: "Cold" },
  disqualified: { bg: "bg-zinc-100 text-zinc-500", label: "Disqualified" },
};

const ROUTE_LABELS: Record<string, { heading: string; detail: string }> = {
  ae_immediate: {
    heading: "Routed to your Account Executive",
    detail: "Expect a response within the hour.",
  },
  sdr_nurture: {
    heading: "Added to our nurture sequence",
    detail: "An SDR will follow up shortly with relevant resources.",
  },
  marketing_nurture: {
    heading: "We'll keep you in the loop",
    detail: "You'll receive tailored content as we learn more about your needs.",
  },
  drop: {
    heading: "Thanks for reaching out",
    detail: "We'll be in touch if there's a fit.",
  },
};

const PRESETS = [
  {
    label: "Hot buyer",
    tier: "hot" as const,
    base: {
      name: "Julia Martinez, VP of Sales",
      email: "julia.martinez@stripe.com",
      company: "Stripe",
      message: "We have budget approved and need to schedule a demo for our sales team this week. Urgent priority.",
    },
  },
  {
    label: "Warm evaluator",
    tier: "warm" as const,
    base: {
      name: "Mark Chen, Product Manager",
      email: "mark.chen@datadog.com",
      company: "Datadog",
      message: "Exploring tools for our Q3 roadmap. Can you send pricing info?",
    },
  },
  {
    label: "Cold browser",
    tier: "cold" as const,
    base: {
      name: "Alex Kumar",
      email: "alex.kumar@notion.so",
      company: "Notion",
      message: "Just browsing. Saw your site.",
    },
  },
  {
    label: "Spam",
    tier: "disqualified" as const,
    base: {
      name: "SEO King",
      email: "promo_king@gmail.com",
      company: "",
      message: "Buy cheap SEO backlinks! Best price guaranteed! Visit our site now!",
    },
  },
  {
    label: "Opt-out",
    tier: "disqualified" as const,
    base: {
      name: "Maria Garcia",
      email: "maria.garcia@hubspot.com",
      company: "HubSpot",
      message: "Please remove me from your mailing list. Unsubscribe.",
    },
  },
];

const PRESET_TIER_COLOR: Record<string, string> = {
  hot: "border-red-200 text-red-700 hover:bg-red-50",
  warm: "border-amber-200 text-amber-700 hover:bg-amber-50",
  cold: "border-blue-200 text-blue-700 hover:bg-blue-50",
  disqualified: "border-zinc-200 text-zinc-500 hover:bg-zinc-50",
};

export default function LeadForm() {
  const [form, setForm] = useState({ name: "", email: "", company: "", message: "" });
  const [result, setResult] = useState<TriageResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMsg, setLoadingMsg] = useState("Triaging...");
  const [error, setError] = useState("");

  // Warmup: wake Render backend on page load so it's ready when user submits
  useEffect(() => { warmup(); }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setLoadingMsg("Triaging...");
    setError("");
    setResult(null);

    // Progressive loading messages for slow paths (LLM ~10s, cold start ~30s)
    const t1 = setTimeout(() => setLoadingMsg("Analyzing lead — this can take ~15s..."), 3_000);
    const t2 = setTimeout(() => setLoadingMsg("Still working — backend may be waking from cold start..."), 15_000);

    try {
      const res = await apiPost<TriageResult>("/triage", form);
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach the server. Is the API running?");
    } finally {
      clearTimeout(t1);
      clearTimeout(t2);
      setLoading(false);
    }
  }

  const inputClass =
    "w-full px-3.5 py-2.5 bg-white border border-zinc-200 rounded-lg text-sm text-zinc-900 placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-indigo-600/20 focus:border-indigo-600 transition-colors";

  return (
    <div className="flex flex-col min-h-[100dvh]">
      <header className="border-b border-zinc-200 bg-white px-6 py-3.5 flex items-center justify-between shrink-0">
        <Link href="/" className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg bg-zinc-900 flex items-center justify-center">
            <span className="text-white font-bold text-xs">A</span>
          </div>
          <span className="font-semibold text-sm text-zinc-900">Aether GTM</span>
        </Link>
        <Link href="/ops" className="text-xs font-medium text-zinc-500 hover:text-zinc-900 transition-colors">
          Ops Dashboard
        </Link>
      </header>

      <main className="flex-1 flex items-center justify-center px-4 py-12">
        <div className="w-full max-w-md">
          {!result ? (
            <>
              <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 mb-1">
                Get in touch
              </h1>
              <p className="text-sm text-zinc-500 leading-relaxed mb-6 max-w-[50ch]">
                Tell us about your needs and we&apos;ll route you to the right team.
              </p>

              {/* Presets */}
              <div className="mb-6">
                <p className="text-[10px] font-medium text-zinc-400 uppercase tracking-wide mb-2">Try an example</p>
                <div className="flex flex-wrap gap-1.5">
                  {PRESETS.map((p) => (
                    <button
                      key={p.label}
                      type="button"
                      onClick={() => setForm({ ...p.base })}
                      className={`text-[11px] font-medium px-2.5 py-1 rounded-lg border transition-colors ${PRESET_TIER_COLOR[p.tier]}`}
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
              </div>

              <form onSubmit={handleSubmit} className="space-y-5">
                <div>
                  <label className="block text-xs font-medium text-zinc-700 mb-1.5">Name</label>
                  <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={inputClass} placeholder="Julia Martinez, VP of Sales" />
                </div>
                <div>
                  <label className="block text-xs font-medium text-zinc-700 mb-1.5">Work email</label>
                  <input required type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className={inputClass} placeholder="julia@acmecorp.com" />
                </div>
                <div>
                  <label className="block text-xs font-medium text-zinc-700 mb-1.5">Company <span className="text-zinc-400 font-normal">(optional)</span></label>
                  <input value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} className={inputClass} placeholder="Acme Corp" />
                </div>
                <div>
                  <label className="block text-xs font-medium text-zinc-700 mb-1.5">Message</label>
                  <textarea required rows={3} value={form.message} onChange={(e) => setForm({ ...form, message: e.target.value })} className={`${inputClass} resize-none`} placeholder="We'd like to schedule a demo for our team..." />
                </div>

                {error && (
                  <div className="rounded-lg bg-red-50 border border-red-200 px-3.5 py-2.5 text-sm text-red-700">{error}</div>
                )}

                <button
                  type="submit"
                  disabled={loading}
                  className="w-full py-2.5 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 active:scale-[0.98] disabled:opacity-50 disabled:pointer-events-none transition-all"
                >
                  {loading ? (
                    <span className="flex items-center justify-center gap-2">
                      <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                      {loadingMsg}
                    </span>
                  ) : (
                    "Submit"
                  )}
                </button>
              </form>
            </>
          ) : (
            <div className="text-center">
              <div className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-semibold mb-5 ${TIER_STYLE[result.final_tier]?.bg || TIER_STYLE.cold.bg}`}>
                {TIER_STYLE[result.final_tier]?.label || result.final_tier}
                {result.score && <span className="opacity-60 font-mono">{result.score.points} pts</span>}
              </div>

              <h2 className="text-xl font-semibold tracking-tight text-zinc-900 mb-2">
                {ROUTE_LABELS[result.final_route]?.heading || "Request received"}
              </h2>
              <p className="text-sm text-zinc-500 leading-relaxed mb-8 max-w-[45ch] mx-auto">
                {ROUTE_LABELS[result.final_route]?.detail || "Your request has been routed to the appropriate team."}
              </p>

              {result.score?.reason && (
                <p className="text-xs text-zinc-400 font-mono mb-8 max-w-[50ch] mx-auto leading-relaxed">
                  {result.score.reason}
                </p>
              )}

              <button
                onClick={() => { setResult(null); setForm({ name: "", email: "", company: "", message: "" }); }}
                className="text-xs font-medium text-indigo-600 hover:text-indigo-700 transition-colors"
              >
                Submit another lead
              </button>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
