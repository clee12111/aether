# Frontier Research: Length-Adaptive Extraction for Lead Triage

**Date:** 2026-06-26
**Scope:** Is a length-adaptive expand/decompose strategy (borrowed from RAG) real
frontier, real GTM practice, or a clever-but-unused analogy transfer?

---

## 1. Executive Verdict

Length-adaptive extraction is a **valid research-backed architectural pattern** (Adaptive-RAG,
short-text enrichment, atomic claim decomposition all have strong primary literature), but it
is **not current GTM industry practice**—no production lead-triage system we found routes its
extraction strategy by input richness. The GTM industry's actual frontier is
**enrichment-first pipelines** (deterministic API waterfall → LLM reasoning over assembled
context), not RAG-style expansion or decomposition of the lead message itself. The RAG analogy
partially transfers: enrichment-as-context-assembly is real and universal; HyDE-style
inference-before-scoring is dangerous and unused; atomic signal extraction from rich messages
is sound engineering that the industry hasn't formalized but implicitly does. The strongest
move for our system is to **adopt enrichment-as-expansion for thin leads and atomic extraction
for rich leads**, while explicitly rejecting ungrounded inference (HyDE) as an expansion
mechanism—a design the research supports but no one has shipped as a unified adaptive system.

---

## 2. Per-Hypothesis Findings

### H1: SHORT/thin leads → "query expansion" (expand sparse signal before scoring)

**Verdict: SUPPORTED (with critical caveat) — Confidence: HIGH (0.85)**

**What the research says:**

Short-text enrichment is a well-studied NLP problem. Semantic sparsity in short texts degrades
classification accuracy, and enrichment techniques consistently improve it:

- **Conceptual knowledge enrichment** adds related terms/concepts from external knowledge bases
  to expand short text before classification (survey: Qiang et al., 2023).
- **SSE-CoT** (Syntactic and Semantic Enrichment Chain-of-Thought, 2025) uses LLMs to
  decompose short-text classification tasks and address semantic sparsity, showing gains on
  tweet/SMS classification [1].
- **Multi-source graph enrichment** (2025) builds statistical, linguistic, and factual graphs
  to introduce external context for short text [2].
- **Entity-based enrichment** extracts named entities and enriches via knowledge graphs before
  classification.

**What the GTM industry actually does:**

Every major GTM platform performs enrichment on thin leads, but they call it "enrichment" not
"expansion," and it is grounded external data, not LLM inference:

- **Clay** runs a waterfall of 75+ data providers in sequence (Clearbit → ZoomInfo → Apollo →
  etc.) to fill missing firmographic/technographic fields before any LLM touches the lead [3].
- **Default.com** uses an "enrichment-first architecture" that explicitly enriches before
  qualification/routing, calling it their biggest differentiator [4].
- **Cargo** documents their pipeline as: data sources → context assembly → LLM scoring [5].
- **Clearbit** (now HubSpot) returns structured JSON enrichment in <200ms via deterministic
  API calls [6].

**The critical caveat — expansion vs. enrichment:**

The research literature describes two very different things under "expansion":

| Mechanism | Grounded? | GTM usage | Risk |
|-----------|-----------|-----------|------|
| **(a) Enrichment** — adding real external data (firmographics, technographics, web scraping) | Yes | Universal | Low (data is factual) |
| **(b) HyDE / inference** — LLM generates hypothetical context before scoring | No | Not found in any GTM system | High (hallucination contaminates scoring) |

HyDE (Gao et al., 2022) generates a hypothetical document to improve retrieval embedding
similarity [7]. The ARAGOG benchmark (2024) found HyDE did not consistently outperform
vanilla RAG [8]. For scoring decisions, fabricating context before scoring is explicitly
identified as hallucination-prone and unsafe in fact-bound domains [9].

**No GTM vendor uses HyDE-style inference.** Every vendor we examined enforces a hard
boundary between enrichment (real data from APIs) and LLM inference (reasoning over assembled
context). This separation is architectural, not accidental.

**Bottom line for H1:** Expanding thin leads with *grounded external data* (enrichment) is
universal best practice. Expanding thin leads with *LLM-generated hypothetical context*
(HyDE/inference) is a research technique that the GTM industry correctly avoids for scoring.

---

### H2: LONG/rich leads → atomic claim extraction / decomposition

**Verdict: SUPPORTED (research-strong, industry-implicit) — Confidence: MODERATE (0.70)**

**What the research says:**

Atomic fact decomposition is well-established and progressing rapidly:

- **FActScore** (Min et al., 2023): decomposes text into "short statements each containing one
  piece of information," then verifies each against a knowledge source. Limitation: extracts
  everything (including unverifiable claims), no decontextualization [10].
- **SAFE** (Wei et al., 2024, Google DeepMind): decomposes into self-contained atomic facts,
  filters for relevance, verifies via Google Search. Agrees with human fact-checkers 72% of
  the time; wins 76% of disagreements. 20× cheaper than human verification [11].
- **VeriScore** (Song et al., 2024): extracts only *verifiable* claims using a contextualized
  sliding window, avoiding FActScore's over-extraction problem. Published at EMNLP 2024 [12].
- **DnDScore** (2024): adds explicit decontextualization before decomposition—resolves pronouns
  and coreferences that FActScore missed [13].

The progression FActScore → SAFE → VeriScore → DnDScore shows the field converging on:
extract only verifiable/actionable claims, decontextualize them, then process independently.
This maps directly to extracting atomic signals (seniority, intent, fit, use case) from a
rich lead message.

**What the GTM industry actually does:**

No vendor explicitly calls their approach "atomic extraction" or "decomposition," but the
pattern appears implicitly:

- **Vadim's blog** (2026) documents decomposing lead qualification into separate scoring
  dimensions (seniority, role_fit, reachability, propensity) with different methods per
  dimension—rule-based for seniority (title lookup), LLM for role_fit, rule-based for
  reachability, LLM for propensity [14]. This is dimension-level decomposition, not
  claim-level, but it's the closest industry analog.
- **Cargo** assembles context then scores with a structured LLM prompt that emits a 0-30
  score with justification—single-call extraction with structured output, not decomposition
  [5].
- **Clay's Claygent** extracts structured insights from websites/profiles via natural language
  queries—task-level structured extraction, not atomic decomposition [15].

**Disconfirming evidence:**

- Modern LLMs perform well at **zero-shot structured extraction** from free text without
  decomposition. Clinical NLP studies show GPT-class models extract structured fields from
  clinical notes in a single prompt with high accuracy [16]. For lead messages (simpler than
  clinical notes), decomposition may be unnecessary overhead.
- **Error propagation** in multi-step NLP pipelines grows with pipeline depth [17]. Each
  decomposition step is a failure point. For a 2-3 sentence lead, the risk of distorting
  what was plainly stated may exceed the benefit.
- **Structured output constraints** (JSON mode, tool-use) now enforce schema compliance in
  single calls, reducing the multi-step reliability advantage that decomposition once offered
  [18].

**Bottom line for H2:** Atomic decomposition is research-proven and the technique transfers
cleanly to lead signal extraction. But the industry gets adequate results from single-call
structured extraction with schema constraints. The value of decomposition increases with
message length/complexity—for a paragraph-length message, it's marginal; for a multi-paragraph
detailed email with multiple stakeholders and use cases mentioned, it's high-value. The
**length-dependence is real but the threshold is higher than expected**: you probably need
500+ words before decomposition beats single-call extraction.

---

### H3: Does the GTM industry use RAG-style techniques for lead understanding?

**Verdict: NOT SUPPORTED — Confidence: HIGH (0.90)**

**What the GTM industry actually does (the four patterns we found):**

| Pattern | Who | Scoring method | LLM role |
|---------|-----|----------------|----------|
| **1. Enrichment-first → LLM reasoning** | Clay, Cargo, Default | Structured prompt with assembled context | Score/classify over enriched data |
| **2. ML models on conversion data** | MadKudu, 6sense, HubSpot | Trained classifiers (gradient boosting, neural) | Explain scores, not generate them |
| **3. Embedding similarity** | Dextra Labs | Cosine similarity to won-deal embeddings | Feature engineering, not scoring |
| **4. Hybrid rule + LLM** | Vadim's system | Rules for structured fields, LLM for unstructured | Dimensional scoring |

**None of these use RAG-style techniques.** Specifically:

- **No query expansion** on lead messages. The "expansion" that happens is deterministic
  enrichment from APIs—a fundamentally different mechanism.
- **No query decomposition** of lead messages. Scoring is done in a single LLM call over
  assembled context, or by ML models, or by rules.
- **No HyDE** or hypothetical document generation. Every vendor enforces a grounded-data-first
  architecture.
- **No retrieval** from a document corpus. Lead messages are self-contained inputs, not queries
  against a knowledge base.
- **No adaptive routing** by input complexity. All vendors apply the same pipeline regardless
  of lead message length or richness.

The closest analog is **Cargo's context assembly step**, which gathers data from multiple
sources before the LLM call—this resembles RAG's retrieval phase but uses structured API
enrichment, not semantic search over documents.

**Why RAG techniques aren't used:**

1. **No corpus to retrieve from.** RAG exists to bridge the gap between a query and a document
   store. Lead messages are the input text itself, not queries against a corpus. The task is
   extraction, not retrieval [19].
2. **Enrichment solves the sparse-input problem better.** Where RAG would "expand" a short
   query with inferred context, GTM tools add real data from APIs. Grounded enrichment
   dominates ungrounded inference for scoring decisions.
3. **Context windows are large enough.** With 128K+ token context windows, there's no need to
   decompose or compress lead data. You can stuff all enrichment results + the lead message
   into a single call.
4. **Error propagation.** Multi-step decomposition/expansion pipelines multiply failure points.
   For a task where a single LLM call with structured output achieves adequate accuracy, the
   complexity isn't justified.

---

## 3. What the GTM Industry Actually Does Today

### The Canonical Pipeline (synthesized from Clay, Cargo, Default, Vadim)

```
Inbound Lead (form, email, chat)
  │
  ▼
DETERMINISTIC ENRICHMENT (waterfall)
  │  Clearbit/HubSpot → ZoomInfo → Apollo → PDL → ...
  │  Returns: company size, industry, funding, tech stack, title normalization
  │  Latency: <500ms  •  Grounded: 100%
  │
  ▼
OPTIONAL: LLM WEB RESEARCH (Claygent-style)
  │  Scrape company website, summarize value prop, classify ICP tier
  │  Latency: 2-5s  •  Semi-grounded (reads real pages, LLM summarizes)
  │
  ▼
CONTEXT ASSEMBLY
  │  Merge: lead message + enriched firmographics + web research
  │  into a structured prompt with ICP definition injected
  │
  ▼
LLM SCORING (single call, structured output)
  │  Input: assembled context + scoring rubric
  │  Output: structured score (Pydantic/JSON schema enforced)
  │  e.g., {score: 0-30, fit: "A/B/C", intent: "high/medium/low", reason: "..."}
  │
  ▼
DETERMINISTIC ROUTING
  │  Score thresholds → assignment rules → CRM update → Slack alert
```

### Key Industry Observations

1. **Enrichment and LLM inference are architecturally separated.** This is universal across
   every vendor we examined. The enrichment step is deterministic, API-based, and fast. The
   LLM step is downstream, reasoning over assembled context.

2. **Scoring is increasingly hybrid.** MadKudu/6sense use trained ML models; Clay/Cargo use
   LLM-as-scorer; Vadim's system uses rules for some dimensions and LLM for others; Dextra
   Labs uses embedding similarity as ML features. The trend is toward LLM scoring for
   unstructured signals + deterministic scoring for structured signals.

3. **No one differentiates by lead message length.** Every system runs the same pipeline
   regardless of whether the lead submitted "hi" or a 500-word detailed email. This is a
   genuine gap in the industry, not an intentional design choice.

4. **The LLM's role is extraction + classification, not expansion.** The LLM reads assembled
   context and extracts structured signals. It does not generate hypothetical context or
   expand the lead message. This is closer to NER/IE than to RAG.

---

## 4. Concrete Recommendation for Our System

### Should we route extraction strategy by input length? **YES, with constraints.**

The research supports it, the industry hasn't done it (making it genuinely novel), and the
design is clean if we avoid two traps:

**Trap 1: Using HyDE/inference as expansion.** Never generate hypothetical context about a
lead before scoring. Expansion must be grounded enrichment only.

**Trap 2: Over-decomposing short text.** Atomic extraction of a one-liner ("Need help with
compliance") adds pipeline depth with no signal gain. Short text should be enriched, not
decomposed.

### Recommended Design

```
┌─────────────────────────────────────────────────────┐
│                    INBOUND LEAD                      │
│                  (message + email)                    │
└────────────────────┬────────────────────────────────┘
                     │
              ┌──────▼──────┐
              │  CLASSIFIER │  (heuristic: word count / field count)
              │  thin < 50w │
              │  rich ≥ 50w │
              └──┬───────┬──┘
                 │       │
    ┌────────────▼─┐   ┌─▼────────────────┐
    │  THIN PATH   │   │    RICH PATH      │
    │              │   │                   │
    │ 1. Enrich    │   │ 1. Atomic extract │
    │    (waterfall│   │    (decompose msg  │
    │    APIs)     │   │    into signals:   │
    │              │   │    seniority,      │
    │ 2. Single    │   │    intent, fit,    │
    │    extract   │   │    use case, pain  │
    │    (LLM over │   │    point, urgency, │
    │    enriched  │   │    stakeholders)   │
    │    context)  │   │                   │
    │              │   │ 2. Enrich          │
    │              │   │    (waterfall APIs) │
    │              │   │                   │
    │              │   │ 3. Score per-signal │
    │              │   │    (structured)     │
    └──────┬───────┘   └────────┬──────────┘
           │                    │
           └────────┬───────────┘
                    │
             ┌──────▼──────┐
             │ DETERMINISTIC│
             │   SCORING    │
             │  + ROUTING   │
             └─────────────┘
```

### Design Rationale (tied to research)

| Decision | Rationale | Source |
|----------|-----------|--------|
| Enrich thin leads before LLM extraction | Short-text enrichment consistently improves classification (NLP literature); universal GTM practice | [1][2][3][4] |
| Reject HyDE/inference expansion | Hallucination risk contaminates scoring decisions; no GTM vendor uses it; ARAGOG found inconsistent gains | [7][8][9] |
| Atomic extraction for rich leads | FActScore→SAFE→VeriScore progression validates the technique; rich leads contain multiple independent signals that benefit from decomposition | [10][11][12] |
| Length threshold ~50 words | Below this, there's nothing meaningful to decompose; above this, signals start compounding. Clinical NLP shows zero-shot extraction works well on short text [16]; decomposition value increases with complexity | [16][17] |
| Adaptive routing pattern | Adaptive-RAG (Jeong et al., 2024) validates complexity-based strategy routing; our classifier is simpler (length heuristic, not trained) but same principle | [20] |
| Single-call extraction for thin path | Context-stuffing works well for small inputs with few extraction targets [18]; avoids error propagation | [17][18] |

### Addressing the expansion-vs-grounding tension

The tension resolves cleanly once you separate two meanings of "expansion":

- **Grounded expansion (enrichment):** Add real data from external APIs. This is what the
  thin path does. It's the GTM industry standard, it's deterministic, and it's safe for
  scoring. ✓
- **Ungrounded expansion (HyDE/inference):** Generate hypothetical context with an LLM before
  scoring. This is a retrieval optimization technique that does not transfer to scoring. The
  risk is that fabricated context biases the score. ✗

Our system should treat enrichment as **context assembly** (giving the LLM more real signal
to reason over), never as **signal generation** (asking the LLM to infer what might be true
before scoring what is true).

### What makes this genuinely novel

No GTM vendor we found:
1. Routes extraction strategy by input richness (all use one-size-fits-all pipelines)
2. Performs atomic signal decomposition on rich lead messages
3. Explicitly architecturally separates the thin-lead and rich-lead paths

The research ingredients exist (Adaptive-RAG routing + FActScore-style decomposition +
short-text enrichment), but their combination for lead triage is unshipped. This is a
genuine frontier gap, not a rediscovery of existing practice.

---

## 5. Where the RAG Analogy Breaks

| RAG Assumption | Lead Triage Reality | Consequence |
|---------------|---------------------|-------------|
| There's a document corpus to retrieve from | There's no corpus; the lead message IS the input | "Retrieval" is replaced by "enrichment" (external API data, not semantic search) |
| The query is a question to be answered | The lead is a message to be classified | The task is extraction/classification, not question-answering |
| Query expansion improves recall | "Expanding" a lead with inferred context risks hallucination | Only grounded enrichment (real API data) is safe for scoring |
| Decomposition helps multi-hop reasoning | Lead signals are independent attributes, not reasoning chains | Decomposition works but for different reasons (signal independence, not reasoning depth) |
| HyDE bridges query-document vocabulary gap | There's no document to retrieve; HyDE generates fiction | HyDE is the wrong tool entirely—it solves an embedding similarity problem that doesn't exist here |
| Relevance feedback improves retrieval | There's no retrieval to improve | The feedback loop is eval-driven (score accuracy vs. conversion outcomes), not retrieval-driven |
| Adaptive RAG routes by query complexity | Input richness ≠ query complexity | Our routing is simpler: word count heuristic, not a trained complexity classifier. But the principle (spend compute proportionally to input difficulty) transfers |

### The one place the analogy DOES transfer cleanly

**Context assembly = retrieval.** In RAG, you retrieve relevant documents and inject them
into the LLM's context. In lead triage, you enrich with external data and inject it into
the LLM's context. The mechanism is different (API calls vs. vector search) but the function
is identical: give the LLM more relevant information to reason over. This is why the
enrichment-first architecture is universal in GTM—it's the RAG retrieval step, just
implemented differently.

**Atomic decomposition = extractive sub-questions.** In RAG, you decompose a complex query
into sub-queries to retrieve better. In lead triage, you decompose a complex message into
atomic signals to extract better. Again, different mechanism (no retrieval involved) but
same principle: break complex inputs into tractable units.

---

## 6. Sources

### Primary Sources (papers, official docs)

1. **SSE-CoT short-text enrichment** — CoT-Driven Framework for Short Text Classification
   (2025). [arxiv.org/pdf/2401.03158](https://arxiv.org/pdf/2401.03158)
2. **Multi-source graph enrichment for short text** (2025).
   [arxiv.org/abs/2501.09214](https://arxiv.org/abs/2501.09214)
3. **Clay waterfall enrichment** — Vanderbuild GTM Bible (2026).
   [vanderbuild.co/blog/the-gtm-architects-bible-mastering-waterfall-enrichment-in-clay](https://vanderbuild.co/blog/the-gtm-architects-bible-mastering-waterfall-enrichment-in-clay)
4. **Default.com enrichment-first architecture** — Default lead routing guide.
   [default.com/post/lead-routing](https://www.default.com/post/lead-routing)
5. **Cargo LLM scoring pipeline** — Cargo engineering blog.
   [getcargo.ai/blog/llm-powered-lead-scoring-beyond-traditional-models](https://www.getcargo.ai/blog/llm-powered-lead-scoring-beyond-traditional-models)
6. **Clearbit enrichment API** — Clearbit/HubSpot docs.
   [help.clearbit.com/hc/en-us/sections/360002035034](https://help.clearbit.com/hc/en-us/sections/360002035034--Enrichment-API)
7. **HyDE** — Gao et al., "Precise Zero-Shot Dense Retrieval without Relevance Labels"
   (2022). [arxiv.org/abs/2212.10496](https://arxiv.org/abs/2212.10496)
8. **ARAGOG benchmark** — HyDE inconsistency (2024).
   [arxiv.org/pdf/2404.01037](https://arxiv.org/pdf/2404.01037)
9. **HyDE hallucination risk in scoring** — Advanced retrieval strategies survey.
   [arxiv.org/html/2507.16754v1](https://arxiv.org/html/2507.16754v1)
10. **FActScore** — Min et al., "FActScore: Fine-grained Atomic Evaluation of Factual
    Precision in Long Form Text Generation" (2023, EMNLP).
    [arxiv.org/abs/2305.14251](https://arxiv.org/abs/2305.14251)
11. **SAFE** — Wei et al., Google DeepMind, "Long-form factuality in large language models"
    (2024). [arxiv.org/abs/2403.18802](https://arxiv.org/abs/2403.18802)
12. **VeriScore** — Song et al., "Evaluating the factuality of verifiable claims in long-form
    text generation" (2024, EMNLP).
    [arxiv.org/abs/2406.19276](https://arxiv.org/abs/2406.19276)
13. **DnDScore** — Decontextualization and Decomposition for Factuality Verification (2024).
    [arxiv.org/pdf/2412.13175](https://arxiv.org/pdf/2412.13175)
14. **Vadim's LLM lead scoring** — Multi-dimensional lead qualification architecture (2026).
    [vadim.blog/llm-lead-conversion-propensity-scoring-for-b2b-lead-prioritization](https://vadim.blog/llm-lead-conversion-propensity-scoring-for-b2b-lead-prioritization)
15. **Clay Claygent** — Clay University Claygent lesson.
    [university.clay.com/lessons/enriching-with-claygent](https://university.clay.com/lessons/enriching-with-claygent)
16. **Zero-shot LLM extraction on clinical text** — ChatIE (2023).
    [arxiv.org/pdf/2302.10205](https://arxiv.org/pdf/2302.10205)
17. **Error propagation in NLP pipelines** — "When It's All Piling Up" (CEUR-WS).
    [ceur-ws.org/Vol-1386/piling_up.pdf](https://ceur-ws.org/Vol-1386/piling_up.pdf)
18. **Structured output via constrained decoding** — LLM schemas guide (Simon Willison, 2025).
    [simonwillison.net/2025/Feb/28/llm-schemas](https://simonwillison.net/2025/Feb/28/llm-schemas/)
19. **RAG scoped to knowledge-intensive tasks** — Lewis et al., "Retrieval-Augmented
    Generation for Knowledge-Intensive NLP Tasks" (2020).
    [arxiv.org/abs/2005.11401](https://arxiv.org/abs/2005.11401)
20. **Adaptive-RAG** — Jeong et al., "Adaptive-RAG: Learning to Adapt Retrieval-Augmented
    Large Language Models through Question Complexity" (2024, NAACL).
    [aclanthology.org/2024.naacl-long.389](https://aclanthology.org/2024.naacl-long.389/)

### Secondary Sources (vendor reviews, guides, blog posts)

- Clay enrichment guide — Databar.ai (2026).
  [databar.ai/blog/article/clay-lead-enrichment-complete-2025-guide-top-alternatives](https://databar.ai/blog/article/clay-lead-enrichment-complete-2025-guide-top-alternatives)
- MadKudu lead grade scoring — MadKudu help docs.
  [help.madkudu.com/docs/lead-grade-scoring](https://help.madkudu.com/docs/lead-grade-scoring)
- HubSpot AI lead scoring — Aptitude 8 blog (2025).
  [aptitude8.com/blog/hubspot-ai-lead-scoring-prospecting-agent](https://aptitude8.com/blog/hubspot-ai-lead-scoring-prospecting-agent)
- 6sense intent model — 6sense support docs.
  [support.6sense.com/docs/intent-model](https://support.6sense.com/docs/intent-model)
- 6sense technical architecture — Prospeo explainer (2026).
  [prospeo.io/s/how-does-6sense-work](https://prospeo.io/s/how-does-6sense-work)
- Apollo ML platform — Apollo tech blog.
  [apollo.io/tech-blog/building-apollos-data-machine-learning-platform](https://www.apollo.io/tech-blog/building-apollos-data-machine-learning-platform)
- LLM embeddings for lead scoring — Dextra Labs (2026).
  [medium.com/@dextra_labs/...lead-scoring](https://medium.com/@dextra_labs/llm-embeddings-are-underrated-heres-how-we-use-them-for-lead-scoring-7e5488699325)
- Building a lead scoring agent — Rani Urbis (2026).
  [medium.com/@raniurbis/how-to-build-a-proper-lead-scoring-agent](https://medium.com/@raniurbis/how-to-build-a-proper-lead-scoring-agent-2f083d2d56e3)
- Qualified Piper AI SDR — Qualified newsroom (2025).
  [qualified.com/newsroom/qualified-unveils-piper-2025](https://www.qualified.com/newsroom/qualified-unveils-piper-2025)
- Default 2.0 announcement — Default blog.
  [default.com/post/introducing-default-2-0](https://www.default.com/post/introducing-default-2-0)
- Query expansion survey — "Query Expansion in the Age of Pre-trained and Large Language
  Models" (2024). [arxiv.org/pdf/2509.07794](https://arxiv.org/pdf/2509.07794)
- Adaptive RAG routing benchmark — RAGRouter-Bench (2025).
  [arxiv.org/pdf/2604.03455](https://arxiv.org/pdf/2604.03455)
- LLM routing convergence — "Doing More with Less" routing survey (2025).
  [arxiv.org/pdf/2502.00409](https://arxiv.org/pdf/2502.00409)
- Multi-agent error propagation — (2025).
  [arxiv.org/html/2603.04474v1](https://arxiv.org/html/2603.04474v1)
- State of AI for GTM 2026 — GTM Strategist.
  [knowledge.gtmstrategist.com/p/the-2026-state-of-ai-for-gtm-workflows](https://knowledge.gtmstrategist.com/p/the-2026-state-of-ai-for-gtm-workflows)
- Cargo GTM Playbook 2026 — Cargo blog.
  [getcargo.ai/blog/gtm-engineering-playbook-2026-autonomous-workflows](https://www.getcargo.ai/blog/gtm-engineering-playbook-2026-autonomous-workflows)
