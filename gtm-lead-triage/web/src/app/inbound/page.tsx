"use client";

import { useState, useRef, useCallback } from "react";
import { Nav } from "@/components/nav";
import { apiPost } from "@/lib/api";
import { invalidateLeads } from "@/lib/leads-store";

/* -- Types ---------------------------------------------------------------- */

type Channel = "form" | "email" | "chat" | "clay";

interface TriageResponse {
  run_id: string;
  final_tier: string | null;
  final_route: string | null;
}

const TIER_STYLE: Record<string, { bg: string; label: string }> = {
  hot: { bg: "bg-red-100 text-red-700", label: "Hot" },
  warm: { bg: "bg-amber-100 text-amber-700", label: "Warm" },
  cold: { bg: "bg-blue-100 text-blue-700", label: "Cold" },
  disqualified: { bg: "bg-stone-100 text-stone-500", label: "Disqualified" },
};

const ROUTE_MSG: Record<string, { heading: string; detail: string }> = {
  ae_immediate: { heading: "Routed to your Account Executive", detail: "Expect a response within the hour." },
  sdr_nurture: { heading: "Added to our nurture sequence", detail: "An SDR will follow up shortly." },
  marketing_nurture: { heading: "We'll keep you in the loop", detail: "You'll receive tailored content as we learn more." },
  drop: { heading: "Thanks for reaching out", detail: "We'll be in touch if there's a fit." },
  skip: { heading: "Thanks for reaching out", detail: "We'll be in touch if there's a fit." },
};

/* -- Presets -------------------------------------------------------------- */

interface FormData { name: string; email: string; company: string; message: string }

const FORM_PRESETS: { label: string; tier: string; data: FormData }[] = [
  { label: "Hot buyer", tier: "hot", data: { name: "Marcus Hale", email: "marcus.hale@datadoghq.com", company: "Datadog", message: "Budget approved, need a demo this quarter for 40 PMs." } },
  { label: "Warm evaluator", tier: "warm", data: { name: "Dana Okafor", email: "dana.okafor@notion.so", company: "Notion", message: "Drowning in feature requests. Looking to tie feedback to roadmap decisions." } },
  { label: "Cold browser", tier: "cold", data: { name: "Alex Kumar", email: "alex.kumar@gmail.com", company: "", message: "Just browsing. Saw your site." } },
  { label: "Spam", tier: "disqualified", data: { name: "SEO King", email: "promo_king@gmail.com", company: "", message: "Buy cheap SEO backlinks! Visit our site now!" } },
  { label: "Opt-out", tier: "disqualified", data: { name: "Maria Garcia", email: "maria.garcia@hubspot.com", company: "HubSpot", message: "Please remove me from your mailing list." } },
];

const EMAIL_PRESETS: { label: string; data: string }[] = [
  { label: "VP inquiry (Stripe)", data: "From: Julia Martinez <j.martinez@stripe.com>\nSubject: Feedback tooling\n\nVP of Product at Stripe. Evaluating tools to centralize customer feedback. Can we talk?" },
  { label: "Demo request (Vercel)", data: "From: Leah Brooks <leah.brooks@vercel.com>\nSubject: Demo request\n\nHead of Product at Vercel. Need to schedule a demo for our team. Budget approved for Q3." },
  { label: "Cold request", data: "From: info@university.edu\nSubject: Info request\n\nSend me some info about your product." },
  { label: "Opt-out email", data: "From: maria@hubspot.com\nSubject: Unsubscribe\n\nPlease remove me from your mailing list immediately." },
];

const CHAT_PRESETS: { label: string; data: string }[] = [
  { label: "Interest (Figma)", data: "Visitor: Hi, I'm Sarah Chen. My email is sarah.chen@figma.com.\nVisitor: We're a design tools company, interested in your feedback tool.\nAgent: Happy to help!" },
  { label: "Evaluating (Amplitude)", data: "Visitor: Hey, I'm Tom Park. Email is tom.park@amplitude.com.\nVisitor: I'm evaluating feedback platforms for our product team.\nVisitor: Can you walk me through how it handles Slack integration?" },
  { label: "Browsing", data: "Visitor: Hi, my email is curious@gmail.com.\nVisitor: Just browsing, what does your tool do?" },
  { label: "Opt-out", data: "Visitor: Please stop contacting me. My email is remove@example.com" },
];

const CLAY_PRESETS: { label: string; data: Record<string, string> }[] = [
  { label: "Notion row", data: { Email: "dana.okafor@notion.so", "Full Name": "Dana Okafor", Company: "Notion", "Job Title": "Head of Product", Notes: "Looking for feedback tooling." } },
  { label: "Datadog row", data: { Email: "marcus.hale@datadoghq.com", "Full Name": "Marcus Hale", Company: "Datadog", "Job Title": "VP of Sales", Notes: "Budget approved, need demo for 40 PMs." } },
  { label: "Minimal row", data: { email: "minimal@example.com" } },
];

const TIER_COLOR: Record<string, string> = {
  hot: "border-red-200 text-red-700 hover:bg-red-50",
  warm: "border-amber-200 text-amber-700 hover:bg-amber-50",
  cold: "border-blue-200 text-blue-700 hover:bg-blue-50",
  disqualified: "border-stone-200 text-stone-500 hover:bg-stone-50",
};

/* -- Component ------------------------------------------------------------ */

export default function InboundPage() {
  const [channel, setChannel] = useState<Channel>("form");
  const [loading, setLoading] = useState(false);
  const [loadingMsg, setLoadingMsg] = useState("Triaging...");
  const [error, setError] = useState("");
  const [result, setResult] = useState<TriageResponse | null>(null);

  const [form, setForm] = useState<FormData>({ name: "", email: "", company: "", message: "" });
  const [rawEmail, setRawEmail] = useState("");
  const [transcript, setTranscript] = useState("");
  const [clayJson, setClayJson] = useState("{}");

  const formRef = useRef(form);
  const rawEmailRef = useRef(rawEmail);
  const transcriptRef = useRef(transcript);
  const clayJsonRef = useRef(clayJson);
  const channelRef = useRef(channel);
  formRef.current = form;
  rawEmailRef.current = rawEmail;
  transcriptRef.current = transcript;
  clayJsonRef.current = clayJson;
  channelRef.current = channel;

  const submit = useCallback(async () => {
    setLoading(true);
    setLoadingMsg("Triaging...");
    setError("");
    setResult(null);
    const t1 = setTimeout(() => setLoadingMsg("Analyzing lead..."), 3000);
    const t2 = setTimeout(() => setLoadingMsg("Still working..."), 12000);
    try {
      let resp: TriageResponse;
      const ch = channelRef.current;
      if (ch === "form") {
        const f = formRef.current;
        resp = await apiPost<TriageResponse>("/triage", { email: f.email, name: f.name, company: f.company, message: f.message, source: "web_form" });
      } else if (ch === "email") {
        resp = await apiPost<TriageResponse>("/intake/email", { raw_email: rawEmailRef.current });
      } else if (ch === "chat") {
        resp = await apiPost<TriageResponse>("/intake/chat", { transcript: transcriptRef.current });
      } else {
        resp = await apiPost<TriageResponse>("/webhooks/clay", { row: JSON.parse(clayJsonRef.current) });
      }
      setResult(resp);
      // Invalidate shared leads cache so Outbound picks up the new lead
      invalidateLeads();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach the server.");
    } finally {
      clearTimeout(t1);
      clearTimeout(t2);
      setLoading(false);
    }
  }, []);

  function switchChannel(ch: Channel) { setChannel(ch); channelRef.current = ch; setResult(null); setError(""); }
  function fillForm(data: FormData) { setForm(data); formRef.current = data; }
  function fillEmail(raw: string) { setRawEmail(raw); rawEmailRef.current = raw; }
  function fillChat(t: string) { setTranscript(t); transcriptRef.current = t; }
  function fillClay(row: Record<string, string>) { const j = JSON.stringify(row, null, 2); setClayJson(j); clayJsonRef.current = j; }

  function reset() { setResult(null); setError(""); setForm({ name: "", email: "", company: "", message: "" }); setRawEmail(""); setTranscript(""); setClayJson("{}"); }

  const inputCls = "w-full px-3.5 py-2.5 bg-[var(--surface)] border border-stone-200 rounded-lg text-sm text-stone-900 placeholder:text-stone-400 focus:outline-none focus:ring-2 focus:ring-indigo-600/20 focus:border-indigo-600 transition-colors";

  return (
    <div className="flex flex-col min-h-[100dvh]">
      <Nav />
      <main className="flex-1 flex items-center justify-center px-4 py-10">
        <div className="w-full max-w-md">
          {!result ? (
            <>
              <h1 className="text-2xl font-semibold tracking-tight text-stone-900 mb-1">Get in touch</h1>
              <p className="text-sm text-stone-500 leading-relaxed mb-5 max-w-[50ch]">Tell us about your needs and we'll route you to the right team.</p>

              {/* Channel tabs */}
              <div className="flex gap-1 mb-4">
                {(["form", "email", "chat", "clay"] as Channel[]).map((ch) => (
                  <button key={ch} onClick={() => switchChannel(ch)}
                    className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${channel === ch ? "bg-stone-900 text-white" : "text-stone-500 hover:bg-stone-100"}`}>
                    {ch === "form" ? "Form" : ch === "email" ? "Email" : ch === "chat" ? "Chat" : "Clay"}
                  </button>
                ))}
              </div>

              {/* Presets */}
              <div className="mb-5">
                <p className="text-[10px] font-medium text-stone-400 uppercase tracking-wide mb-2">Try an example</p>
                <div className="flex flex-wrap gap-1.5">
                  {channel === "form" && FORM_PRESETS.map((p) => (
                    <button key={p.label} onClick={() => fillForm(p.data)} disabled={loading}
                      className={`text-[11px] font-medium px-2.5 py-1 rounded-lg border transition-colors disabled:opacity-50 ${TIER_COLOR[p.tier]}`}>{p.label}</button>
                  ))}
                  {channel === "email" && EMAIL_PRESETS.map((p) => (
                    <button key={p.label} onClick={() => fillEmail(p.data)} disabled={loading}
                      className="text-[11px] font-medium px-2.5 py-1 rounded-lg border border-stone-200 text-stone-600 hover:bg-stone-50 disabled:opacity-50 transition-colors">{p.label}</button>
                  ))}
                  {channel === "chat" && CHAT_PRESETS.map((p) => (
                    <button key={p.label} onClick={() => fillChat(p.data)} disabled={loading}
                      className="text-[11px] font-medium px-2.5 py-1 rounded-lg border border-stone-200 text-stone-600 hover:bg-stone-50 disabled:opacity-50 transition-colors">{p.label}</button>
                  ))}
                  {channel === "clay" && CLAY_PRESETS.map((p) => (
                    <button key={p.label} onClick={() => fillClay(p.data)} disabled={loading}
                      className="text-[11px] font-medium px-2.5 py-1 rounded-lg border border-stone-200 text-stone-600 hover:bg-stone-50 disabled:opacity-50 transition-colors">{p.label}</button>
                  ))}
                </div>
              </div>

              {/* Channel-specific fields */}
              <div className="space-y-4">
                {channel === "form" && (
                  <>
                    <div><label className="block text-xs font-medium text-stone-700 mb-1.5">Name</label><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={inputCls} /></div>
                    <div><label className="block text-xs font-medium text-stone-700 mb-1.5">Work email</label><input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className={inputCls} /></div>
                    <div><label className="block text-xs font-medium text-stone-700 mb-1.5">Company <span className="text-stone-400 font-normal">(optional)</span></label><input value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} className={inputCls} /></div>
                    <div><label className="block text-xs font-medium text-stone-700 mb-1.5">Message</label><textarea rows={3} value={form.message} onChange={(e) => setForm({ ...form, message: e.target.value })} className={`${inputCls} resize-none`} /></div>
                  </>
                )}
                {channel === "email" && (
                  <div><label className="block text-xs font-medium text-stone-700 mb-1.5">Raw email</label><textarea rows={8} value={rawEmail} onChange={(e) => setRawEmail(e.target.value)} className={`${inputCls} resize-none font-mono text-xs`} /></div>
                )}
                {channel === "chat" && (
                  <div><label className="block text-xs font-medium text-stone-700 mb-1.5">Chat transcript</label><textarea rows={6} value={transcript} onChange={(e) => setTranscript(e.target.value)} className={`${inputCls} resize-none font-mono text-xs`} /></div>
                )}
                {channel === "clay" && (
                  <div>
                    <label className="block text-xs font-medium text-stone-700 mb-1.5">Clay webhook row (JSON)</label>
                    <textarea rows={8} value={clayJson} onChange={(e) => setClayJson(e.target.value)} className={`${inputCls} resize-none font-mono text-xs`} />
                  </div>
                )}
              </div>

              {error && <div className="mt-4 rounded-lg bg-red-50 border border-red-200 px-3.5 py-2.5 text-sm text-red-700">{error}</div>}

              <button onClick={submit} disabled={loading}
                className="mt-5 w-full py-2.5 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 active:scale-[0.98] disabled:opacity-50 disabled:pointer-events-none transition-all">
                {loading ? (
                  <span className="flex items-center justify-center gap-2">
                    <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    {loadingMsg}
                  </span>
                ) : "Submit"}
              </button>
            </>
          ) : (
            <div className="text-center">
              <div className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-semibold mb-5 ${TIER_STYLE[result.final_tier || "cold"]?.bg || TIER_STYLE.cold.bg}`}>
                {TIER_STYLE[result.final_tier || "cold"]?.label || result.final_tier}
              </div>
              <h2 className="text-xl font-semibold tracking-tight text-stone-900 mb-2">
                {ROUTE_MSG[result.final_route || ""]?.heading || "Request received"}
              </h2>
              <p className="text-sm text-stone-500 leading-relaxed mb-8 max-w-[45ch] mx-auto">
                {ROUTE_MSG[result.final_route || ""]?.detail || "Your request has been routed."}
              </p>
              <button onClick={reset} className="text-xs font-medium text-indigo-600 hover:text-indigo-700 transition-colors">
                Submit another lead
              </button>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
