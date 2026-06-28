"use client";

import { useCallback, useEffect, useState } from "react";
import { Nav } from "@/components/nav";
import { apiGet, apiPost, apiDelete } from "@/lib/api";
import { Lead, TIER_BADGE, TIER_DOT, CHANNEL_DOT, DraftVariant, factLabel } from "@/lib/tokens";

/* -- Types ---------------------------------------------------------------- */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
interface OutboundResult { run_id: string; lead_email: string; final_tier: string | null; trace_path: string; enrichment?: Record<string, any> | null; score?: { points?: number; reason_codes?: string[] } | null; outreach?: { drafts?: DraftVariant[] } | null; }

interface Modal { type: "delete" | "campaign"; email: string; name: string; company: string; domain: string; }

interface Account { domain: string; company: string; contacts: Lead[]; hottestTier: string; hasDemand: boolean; }

type DraftStatus = "none" | "loading" | "ready";
type SortMode = "tier" | "recent";

const TIER_ORDER: Record<string, number> = { hot: 0, warm: 1, cold: 2, disqualified: 3 };

/* -- Component ------------------------------------------------------------ */

export default function OutboundPage() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [loading, setLoading] = useState(true);
  const [results, setResults] = useState<Record<string, OutboundResult>>({});
  const [campaigns, setCampaigns] = useState<Record<string, Record<string, unknown>>>({});
  const [draftStatus, setDraftStatus] = useState<Record<string, DraftStatus>>({});
  const [selectedDomain, setSelectedDomain] = useState<string | null>(null);
  const [selectedContact, setSelectedContact] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [modal, setModal] = useState<Modal | null>(null);
  const [campaignLoading, setCampaignLoading] = useState(false);
  const [modalCampaign, setModalCampaign] = useState({ name: "", icp_keywords: "", target_persona: "Head of Product", value_prop: "centralize scattered customer feedback and tie it to roadmap decisions" });
  const [sortMode, setSortMode] = useState<SortMode>("tier");

  const defaultCampaign = { name: "Productboard ICP", value_prop: "centralize scattered customer feedback and tie it to roadmap decisions", icp_keywords: ["product management", "saas", "customer feedback"], target_persona: "Head of Product" };

  const fetchData = useCallback(async () => {
    try {
      const leadsList = await apiGet<Lead[]>("/leads");
      setLeads(leadsList);
      const existing: Record<string, OutboundResult> = {};
      const existingCampaigns: Record<string, Record<string, unknown>> = {};
      const statuses: Record<string, DraftStatus> = {};

      // Load per-lead outbound email drafts
      for (const lead of leadsList) {
        try {
          const data = await apiGet<{ results: Array<Record<string, unknown>> }>(`/outbound/by-lead/${encodeURIComponent(lead.email)}`);
          for (const r of data.results) {
            if (r.run_type !== "outbound_campaign" && r.run_id) {
              existing[lead.email] = r as unknown as OutboundResult;
              statuses[lead.email] = "ready";
            }
          }
          if (!statuses[lead.email]) statuses[lead.email] = "none";
        } catch { statuses[lead.email] = "none"; }
      }

      // Load domain-keyed campaigns
      const domains = new Set(leadsList.map(l => l.email.includes("@") ? l.email.split("@")[1] : "").filter(Boolean));
      for (const domain of domains) {
        try {
          const data = await apiGet<{ domain: string; campaign: Record<string, unknown> | null }>(`/outbound/campaign/${encodeURIComponent(domain)}`);
          if (data.campaign) existingCampaigns[domain] = data.campaign;
        } catch { /* */ }
      }

      setResults(existing);
      setCampaigns(existingCampaigns);
      setDraftStatus(statuses);
    } catch { /* */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Group leads by company domain
  const accounts: Account[] = (() => {
    const byDomain: Record<string, Lead[]> = {};
    for (const lead of leads) {
      const domain = lead.email.includes("@") ? lead.email.split("@")[1] : lead.email;
      if (!byDomain[domain]) byDomain[domain] = [];
      byDomain[domain].push(lead);
    }
    return Object.entries(byDomain).map(([domain, contacts]) => {
      const tiers = contacts.map(c => c.tier || "cold");
      const hottestTier = tiers.sort((a, b) => (TIER_ORDER[a] ?? 9) - (TIER_ORDER[b] ?? 9))[0] || "cold";
      const hasDemand = !!campaigns[domain];
      const company = contacts.find(c => c.company)?.company || domain;
      return { domain, company, contacts, hottestTier, hasDemand };
    }).sort((a, b) => {
      if (sortMode === "tier") return (TIER_ORDER[a.hottestTier] ?? 9) - (TIER_ORDER[b.hottestTier] ?? 9);
      return 0;
    });
  })();

  async function draftCandidate(email: string) {
    if (draftStatus[email] === "ready" || draftStatus[email] === "loading") return;
    setDraftStatus(prev => ({ ...prev, [email]: "loading" }));
    try {
      const r = await apiPost<OutboundResult>("/outbound/from-lead", { email, campaign: defaultCampaign });
      setResults(prev => ({ ...prev, [email]: r }));
      setDraftStatus(prev => ({ ...prev, [email]: "ready" }));
    } catch { setDraftStatus(prev => ({ ...prev, [email]: "none" })); }
  }

  function selectContact(email: string) {
    setSelectedContact(email);
    if (draftStatus[email] === "none") draftCandidate(email);
  }

  function openCampaignModal(account: Account) {
    const keywords = [account.company.toLowerCase(), "saas", "product management"].filter(Boolean).join(", ");
    setModalCampaign({ name: `${account.company} ICP`, icp_keywords: keywords, target_persona: "Head of Product", value_prop: defaultCampaign.value_prop });
    setModal({ type: "campaign", email: account.contacts[0]?.email || "", name: "", company: account.company, domain: account.domain });
  }

  function openDeleteModal(lead: Lead) {
    const domain = lead.email.includes("@") ? lead.email.split("@")[1] : "";
    setModal({ type: "delete", email: lead.email, name: lead.name || "", company: lead.company || "", domain });
  }

  async function handleDelete() {
    if (!modal || modal.type !== "delete") return;
    try {
      await apiDelete(`/contacts/${encodeURIComponent(modal.email)}`);
      setLeads(prev => prev.filter(l => l.email !== modal.email));
      setResults(prev => { const n = { ...prev }; delete n[modal.email]; return n; });
      if (selectedContact === modal.email) setSelectedContact(null);
    } catch { /* */ }
    setModal(null);
  }

  async function handleCampaign() {
    if (!modal || modal.type !== "campaign") return;
    setCampaignLoading(true);
    try {
      const result = await apiPost<Record<string, unknown>>("/outbound/campaign-for-company", {
        domain: modal.domain, company: modal.company,
        campaign: { name: modalCampaign.name, value_prop: modalCampaign.value_prop, icp_keywords: modalCampaign.icp_keywords.split(",").map(s => s.trim()).filter(Boolean), target_persona: modalCampaign.target_persona },
        apollo_keyword_tags: modalCampaign.icp_keywords.split(",").map(s => s.trim()).filter(Boolean).slice(0, 3), apollo_limit: 3,
      });
      setCampaigns(prev => ({ ...prev, [modal.domain]: result }));
    } catch { /* */ }
    setCampaignLoading(false);
    setModal(null);
  }

  async function copyText(text: string, id: string) { await navigator.clipboard.writeText(text); setCopied(id); setTimeout(() => setCopied(null), 2000); }

  const selAccount = selectedDomain ? accounts.find(a => a.domain === selectedDomain) : null;
  const sel = selectedContact ? results[selectedContact] : null;
  const selLead = selectedContact ? leads.find(l => l.email === selectedContact) : null;
  const selStatus = selectedContact ? (draftStatus[selectedContact] || "none") : "none";
  const brief = sel?.enrichment;
  const drafts = sel?.outreach?.drafts;
  const inputCls = "w-full px-2.5 py-1.5 text-xs bg-[var(--surface)] border border-stone-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-400";
  const labelCls = "block text-[10px] font-medium text-stone-500 mb-0.5";

  return (
    <div className="flex flex-col min-h-[100dvh] h-[100dvh]">
      <Nav />

      {/* Modal */}
      {modal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-stone-900/40">
          <div className="bg-[var(--surface)] rounded-xl border border-stone-200 shadow-lg p-5 w-full max-w-sm mx-4">
            {modal.type === "delete" ? (
              <>
                <h3 className="text-sm font-semibold text-stone-900 mb-2">Delete contact</h3>
                <p className="text-xs text-stone-500 mb-4">Remove <span className="font-medium text-stone-700">{modal.name || modal.email}</span> and all their trace data?</p>
                <div className="flex gap-2 justify-end">
                  <button onClick={() => setModal(null)} className="px-3 py-1.5 text-xs font-medium text-stone-600 bg-stone-100 rounded-lg hover:bg-stone-200 transition-colors">Cancel</button>
                  <button onClick={handleDelete} className="px-3 py-1.5 text-xs font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 transition-colors">Delete</button>
                </div>
              </>
            ) : (
              <>
                <h3 className="text-sm font-semibold text-stone-900 mb-1">Launch campaign for {modal.company}</h3>
                <p className="text-xs text-stone-500 mb-3">Find similar companies via Apollo and draft outreach.</p>
                <div className="space-y-2 mb-4">
                  <div><label className={labelCls}>Campaign name</label><input className={inputCls} value={modalCampaign.name} onChange={e => setModalCampaign({ ...modalCampaign, name: e.target.value })} /></div>
                  <div><label className={labelCls}>ICP keywords</label><input className={inputCls} value={modalCampaign.icp_keywords} onChange={e => setModalCampaign({ ...modalCampaign, icp_keywords: e.target.value })} /></div>
                  <div><label className={labelCls}>Target persona</label><input className={inputCls} value={modalCampaign.target_persona} onChange={e => setModalCampaign({ ...modalCampaign, target_persona: e.target.value })} /></div>
                </div>
                <div className="flex gap-2 justify-end">
                  <button onClick={() => setModal(null)} className="px-3 py-1.5 text-xs font-medium text-stone-600 bg-stone-100 rounded-lg hover:bg-stone-200 transition-colors">Cancel</button>
                  <button onClick={handleCampaign} disabled={campaignLoading} className="px-3 py-1.5 text-xs font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors">{campaignLoading ? "Launching..." : "Launch campaign"}</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      <div className="flex-1 flex overflow-hidden">
        {/* Left: account list */}
        <div className="w-72 border-r border-stone-200 bg-[var(--surface)] flex flex-col shrink-0">
          <div className="px-3 py-2.5 border-b border-stone-200 flex items-center justify-between">
            <h3 className="text-[10px] font-semibold text-stone-400 uppercase tracking-wide">Accounts</h3>
            <select value={sortMode} onChange={e => setSortMode(e.target.value as SortMode)} className="text-[9px] bg-stone-50 border border-stone-200 rounded px-1.5 py-0.5 text-stone-500">
              <option value="tier">Hot first</option>
              <option value="recent">Recent</option>
            </select>
          </div>

          <div className="flex-1 overflow-y-auto">
            {loading && <div className="p-3 space-y-1">{[1, 2, 3].map(i => <div key={i} className="skeleton h-10 rounded-lg" />)}</div>}
            {!loading && accounts.length === 0 && <p className="p-3 text-xs text-stone-400">No leads yet. Submit some in the Inbound tab.</p>}

            {accounts.map(account => {
              const isActive = selectedDomain === account.domain;
              return (
                <button key={account.domain} onClick={() => { setSelectedDomain(account.domain); setSelectedContact(null); }}
                  className={`w-full text-left px-3 py-2 border-b border-stone-100 transition-colors ${isActive ? "bg-indigo-50/50 border-l-2 border-l-indigo-500" : "hover:bg-stone-50"}`}>
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-stone-900 truncate pr-2">{account.company}</span>
                    <div className="flex items-center gap-1.5 shrink-0">
                      <span className="text-[9px] font-mono text-stone-400">{account.contacts.length}</span>
                      <span className={`w-2 h-2 rounded-full ${TIER_DOT[account.hottestTier] || "bg-stone-400"}`} />
                      {account.hasDemand && <span className="text-[8px] text-rose-600 font-medium">demand</span>}
                    </div>
                  </div>
                  <div className="text-[10px] text-stone-400 truncate">{account.domain}</div>
                </button>
              );
            })}
          </div>

          {/* Legend */}
          <div className="px-3 py-2 border-t border-stone-200">
            <div className="flex flex-wrap gap-x-3 gap-y-1 mb-1.5">
              {(["hot", "warm", "cold", "disqualified"] as const).map(t => (
                <div key={t} className="flex items-center gap-1">
                  <span className={`w-2 h-2 rounded-full ${TIER_DOT[t]}`} />
                  <span className="text-[9px] text-stone-500 capitalize">{t}</span>
                </div>
              ))}
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              {[["web_form", "Form"], ["email", "Email"], ["chat", "Chat"], ["clay", "Clay"]].map(([key, label]) => (
                <div key={key} className="flex items-center gap-1">
                  <span className={`w-1.5 h-1.5 rounded-full ${CHANNEL_DOT[key]}`} />
                  <span className="text-[9px] text-stone-400">{label}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Right: detail */}
        <div className="flex-1 overflow-y-auto p-5" style={{ background: "var(--background)" }}>
          {!selectedDomain && (
            <div className="flex items-center justify-center h-full">
              <p className="text-sm text-stone-400">Select an account to view contacts and outreach</p>
            </div>
          )}

          {selAccount && (
            <div className="space-y-4">
              {/* Account header */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <h2 className="text-sm font-semibold text-stone-900">{selAccount.company}</h2>
                  <span className={`w-2 h-2 rounded-full ${TIER_DOT[selAccount.hottestTier]}`} />
                  <span className="text-[10px] text-stone-400">{selAccount.domain}</span>
                </div>
                <button onClick={() => openCampaignModal(selAccount)}
                  className="px-4 py-2 text-xs font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 active:scale-[0.98] transition-all" data-testid="outbound-actions">
                  {campaigns[selAccount.domain] ? "Update campaign" : "Launch campaign"}
                </button>
              </div>

              {/* Campaign section - shows real targets with fit + drafts */}
              {campaigns[selAccount.domain] && (
                <div className="bg-[var(--surface)] border border-stone-200 rounded-lg p-3" data-testid="campaign-section">
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="text-[10px] font-semibold text-stone-400 uppercase tracking-wide">Campaign: {String(campaigns[selAccount.domain].campaign_name || "")}</h3>
                    <div className="flex items-center gap-2">
                      {campaigns[selAccount.domain].total_drafts !== undefined && (
                        <span className="text-[9px] text-stone-400 font-mono">{String(campaigns[selAccount.domain].total_drafts)} drafts</span>
                      )}
                      <span className="text-[9px] text-emerald-600 bg-emerald-50 px-1.5 py-0.5 rounded font-medium">{String(campaigns[selAccount.domain].status || "launched")}</span>
                    </div>
                  </div>
                  {Array.isArray(campaigns[selAccount.domain].targets) && (
                    <div className="space-y-2">
                      {(campaigns[selAccount.domain].targets as Array<Record<string, unknown>>).map((t, i) => (
                        <div key={i} className="border border-stone-100 rounded-lg p-2">
                          <div className="flex items-center justify-between mb-1">
                            <div className="flex items-center gap-2">
                              <span className="text-xs font-medium text-stone-800">{String(t.company)}</span>
                              {t.domain ? <span className="text-[9px] text-stone-400">{String(t.domain)}</span> : null}
                            </div>
                            <div className="flex items-center gap-1.5">
                              {t.fit_points !== undefined && <span className="text-[9px] font-mono text-stone-400">{String(t.fit_points)}</span>}
                              {t.fit_tier ? <span className={`text-[9px] px-1.5 py-0.5 rounded font-semibold ${TIER_BADGE[String(t.fit_tier)] || "bg-stone-100 text-stone-500"}`}>{String(t.fit_tier)}</span> : null}
                            </div>
                          </div>
                          {t.industry ? <span className="text-[9px] text-stone-400">{String(t.industry)}</span> : null}
                          {Array.isArray(t.drafts) && (t.drafts as Array<Record<string, string>>).length > 0 && (
                            <div className="mt-1.5 space-y-1">
                              {(t.drafts as Array<Record<string, string>>).map((d, di) => (
                                <div key={di} className="bg-stone-50 rounded p-1.5 text-[10px]">
                                  <p className="font-medium text-stone-700 mb-0.5">{d.subject}</p>
                                  <p className="text-stone-500 line-clamp-2">{d.body?.slice(0, 120)}...</p>
                                </div>
                              ))}
                            </div>
                          )}
                          {(!Array.isArray(t.drafts) || (t.drafts as unknown[]).length === 0) ? (
                            t.fit_tier ? <p className="text-[9px] text-stone-400 mt-1">No draft ({String(t.fit_tier)} fit)</p> : null
                          ) : null}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Contact dropdown */}
              <div className="bg-[var(--surface)] border border-stone-200 rounded-lg p-3">
                <h3 className="text-[10px] font-semibold text-stone-400 uppercase tracking-wide mb-2">Contacts ({selAccount.contacts.length})</h3>
                <div className="space-y-1">
                  {selAccount.contacts.map(contact => {
                    const isContactActive = selectedContact === contact.email;
                    const status = draftStatus[contact.email] || "none";
                    const ch = contact.source || "web_form";
                    return (
                      <div key={contact.email} className={`flex items-center justify-between px-2 py-1.5 rounded-lg cursor-pointer transition-colors ${isContactActive ? "bg-indigo-50 border border-indigo-200" : "hover:bg-stone-50 border border-transparent"}`}
                        onClick={() => selectContact(contact.email)}>
                        <div className="flex items-center gap-2 min-w-0">
                          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${CHANNEL_DOT[ch] || "bg-stone-400"}`} />
                          <span className="text-xs text-stone-800 truncate">{contact.name || contact.email}</span>
                          {contact.tier && <span className={`text-[9px] px-1.5 py-0.5 rounded font-semibold ${TIER_BADGE[contact.tier] || ""}`}>{contact.tier}</span>}
                        </div>
                        <div className="flex items-center gap-1.5 shrink-0">
                          {contact.score !== undefined && <span className="text-[9px] font-mono text-stone-400">{contact.score}</span>}
                          {status === "ready" && results[contact.email]?.outreach?.drafts && (results[contact.email].outreach?.drafts?.length ?? 0) > 0 && <span className="text-[8px] text-emerald-600">drafted</span>}
                          {status === "ready" && (!results[contact.email]?.outreach?.drafts || (results[contact.email].outreach?.drafts?.length ?? 0) === 0) && <span className="text-[8px] text-stone-400">no draft ({results[contact.email]?.final_tier || "cold"})</span>}
                          {status === "loading" && <span className="w-2.5 h-2.5 border border-indigo-400 border-t-transparent rounded-full animate-spin" />}
                          <button onClick={(e) => { e.stopPropagation(); openDeleteModal(contact); }} className="text-[9px] text-red-400 hover:text-red-600 ml-1">delete</button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Selected contact's draft */}
              {selectedContact && selStatus === "loading" && (
                <div className="space-y-3">
                  <div className="flex items-center gap-3"><div className="w-4 h-4 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" /><p className="text-xs text-stone-500">Drafting outreach for {selLead?.company || selectedContact.split("@")[1]}...</p></div>
                  <div className="skeleton h-24 rounded-lg" />
                </div>
              )}

              {sel && selLead && (
                <div className="space-y-3">
                  {/* Brief + Score */}
                  <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
                    {brief && (
                      <div className="lg:col-span-2 bg-[var(--surface)] border border-stone-200 rounded-lg p-3">
                        <h3 className="text-[10px] font-semibold text-stone-400 uppercase tracking-wide mb-2">Brief</h3>
                        {brief.what_they_do && <p className="text-xs text-stone-600 leading-relaxed mb-2">{String(brief.what_they_do).slice(0, 180)}</p>}
                        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] mb-2">
                          {brief.industry && <span className="text-stone-500">Industry: <span className="font-medium text-stone-700">{brief.industry}</span></span>}
                          {brief.size && <span className="text-stone-500">Size: <span className="font-medium text-stone-700">{brief.size}</span></span>}
                        </div>
                        {Array.isArray(brief.recent_signals) && brief.recent_signals.length > 0 && (
                          <div className="space-y-0.5 mb-2">
                            {(brief.recent_signals as Array<Record<string, string>>).slice(0, 3).map((sig, i) => (
                              <div key={i} className="flex items-center gap-1.5 text-[10px]">
                                <span className={`px-1 py-0.5 rounded font-medium ${sig.kind === "funding" ? "bg-emerald-50 text-emerald-700" : sig.kind === "launch" ? "bg-blue-50 text-blue-700" : sig.kind === "demand" ? "bg-rose-50 text-rose-700" : "bg-stone-100 text-stone-600"}`}>{sig.kind}</span>
                                <span className="text-stone-500 truncate">{sig.text}</span>
                              </div>
                            ))}
                          </div>
                        )}
                        {Array.isArray(brief.tech_stack) && brief.tech_stack.length > 0 && (
                          <div className="flex flex-wrap gap-1">
                            {(brief.tech_stack as string[]).slice(0, 6).map((t, ti) => <span key={`${t}-${ti}`} className="text-[9px] px-1.5 py-0.5 rounded bg-stone-100 text-stone-500 font-mono">{t}</span>)}
                          </div>
                        )}
                      </div>
                    )}
                    {sel.score && (
                      <div className="bg-[var(--surface)] border border-stone-200 rounded-lg p-3">
                        <h3 className="text-[10px] font-semibold text-stone-400 uppercase tracking-wide mb-2">Fit</h3>
                        <div className="flex items-baseline gap-1.5 mb-2">
                          <span className="text-2xl font-bold text-stone-900 font-mono">{sel.score.points}</span>
                          <span className="text-[10px] text-stone-400">/ 100</span>
                        </div>
                        {sel.score.reason_codes?.map((c, i) => <div key={i} className="text-[9px] font-mono text-stone-500">{c}</div>)}
                      </div>
                    )}
                  </div>

                  {/* Drafts */}
                  {drafts && drafts.length > 0 && (
                    <div>
                      <div className="flex items-center justify-between mb-2">
                        <h3 className="text-[10px] font-semibold text-stone-400 uppercase tracking-wide">Drafts for {selLead.name || selLead.email}</h3>
                        <span className="text-[9px] text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded font-medium">never-send, review only</span>
                      </div>
                      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                        {drafts.map(d => (
                          <div key={d.variant} className="bg-[var(--surface)] border border-stone-200 rounded-lg p-3">
                            <div className="flex items-center justify-between mb-1">
                              <span className="text-[10px] font-semibold text-stone-500">Variant {d.variant}</span>
                              <button onClick={() => copyText(d.body, d.variant)} className="text-[9px] text-indigo-600 hover:text-indigo-700">{copied === d.variant ? "Copied" : "Copy"}</button>
                            </div>
                            <p className="text-xs font-medium text-stone-800 mb-1">{d.subject}</p>
                            <p className="text-[11px] text-stone-500 leading-relaxed whitespace-pre-wrap">{d.body}</p>
                            {d.grounded_on.length > 0 && (
                              <div className="flex flex-wrap gap-1 mt-2 pt-1.5 border-t border-stone-100">
                                {d.grounded_on.map((g, gi) => <span key={`${g}-${gi}`} className="text-[9px] px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-700">{factLabel(g)}</span>)}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
