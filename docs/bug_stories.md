# Bug Stories

### Story 1. The UTF-8 BOM that broke pydantic-settings

**Symptom.** `pydantic-settings` threw a validation error saying `ANTHROPIC_API_KEY` was missing. But it was right there in `.env`. I could `cat` the file and see the line. Copy-paste from the terminal into a Python script worked fine. The file existed, the key was in it, and pydantic refused to read it.

**What I thought first.** I spent time checking whether the `.env` path was wrong, whether pydantic's `env_file` setting was case-sensitive, whether some other `.env` file was shadowing it. I even tried hardcoding the key into `Settings` as a default to rule out pydantic entirely. None of this explained it.

**The investigation.** I hex-dumped the file. The first three bytes were `EF BB BF`. That's a UTF-8 byte-order mark. The file had been created in Notepad, which silently inserts a BOM. That meant pydantic-settings was parsing the first variable name as `\ufeffANTHROPIC_API_KEY`, which doesn't match the field name `anthropic_api_key` after case-normalization. Every other variable loaded fine because only the first line carries the BOM prefix.

**The real cause.** Notepad's invisible BOM corrupted the first variable name in the `.env` file. `cat` doesn't show it. Most text editors don't show it. But the parser sees it.

**The fix.** Deleted the file and recreated it in VS Code, which saves as UTF-8 without a BOM by default.

**The lesson.** When an error contradicts what you can see in the terminal, suspect encoding before logic. `cat` lies about BOMs; `xxd` doesn't.

---

### Story 2. Stale Chroma index masking retrieval improvements

**Symptom.** I was iterating on the query classifier in `_classify_query`, fixing regex patterns that should have improved how the retriever distinguished data queries from policy queries. I'd make a fix, run the eval suite, and the numbers wouldn't move. Same 20/25, every time.

**What I thought first.** I assumed my regex changes weren't firing. I added debug prints to `_classify_query`, confirmed the new patterns matched, confirmed the classification was changing. The classifier was working. The numbers still didn't move.

**The investigation.** I started logging the actual chunks coming back from retrieval and compared them against what I expected. The chunks were stale. They had content from an older chunking strategy with different row boundaries and header formatting. My retriever code was running correctly against the wrong data.

**The real cause.** Chroma persists its index to disk. When I changed the chunking logic in the ingestion layer, the old embeddings stayed in `chroma_db/`. My retriever was searching against chunks that no longer matched the current ingestion output. There was a cache between my chunking code and the retrieval results, and I'd forgotten to clear it.

**The fix.** Deleted the `chroma_db/` directory and re-indexed. Precision jumped to 24/25 on the next run. Added `chroma_db/` to `.gitignore` and made a mental note: after any change to ingestion or embedding, nuke and rebuild.

**The lesson.** Cached state is the silent killer of iterative improvement. If your metrics aren't responding to code changes, verify the changes actually reached the runtime before blaming your logic.

---

### Story 3. Planner generating window functions in SQL WHERE clauses

**Symptom.** The executor would fail intermittently on certain e2e test cases with a DuckDB `BinderError`. The error message was about a column reference not being valid in a WHERE clause. It didn't happen on every run. Sometimes the planner would generate slightly different SQL that avoided the issue.

**What I thought first.** I suspected a DuckDB version quirk, or maybe bad column types in the demo CSV. I checked the data, confirmed the columns existed, ran the planner's SQL manually in DuckDB and got the same error. So the SQL itself was broken, not the data.

**The investigation.** I looked at the generated SQL across multiple failing runs. The pattern was consistent: the planner was writing things like `WHERE SUM(ownership_pct) > 100` or `WHERE ROW_NUMBER() OVER (...) = 1`. These are syntactically plausible. They look like SQL. But aggregate and window functions aren't legal in WHERE clauses. You need HAVING for aggregates, or a CTE/subquery for window functions. The planner was generating SQL that would pass a syntax highlighter but fail a query planner.

**The real cause.** Claude generates SQL from its training distribution, which includes plenty of examples where aggregates appear *near* WHERE clauses in tutorials and Stack Overflow answers. Without an explicit constraint, the model produces the pattern it's seen most often, not the one that's semantically correct.

**The fix.** Two layers. First, I added a rule to the planner's system prompt: "never use window functions inside a WHERE clause; use a CTE or subquery first." Second, I added a fallback in `RunSQLTool` that detects this specific `BinderError` pattern and auto-rewrites the query as a CTE. The prompt catches most cases at plan time; the tool-level fallback catches the rest at execution time.

**The lesson.** LLMs hallucinate. Prompt constraints reduce the frequency. Runtime guardrails catch what slips through. I needed both to get reliable SQL.

---

### Story 4. Planner hallucinating CSV column names

**Symptom.** Several e2e test cases were failing because the executor's SQL referenced columns that didn't exist. The planner would generate SQL like `SELECT ownership_pct FROM accounts` when the actual column was `ownership_percentage`, or invent columns entirely, like `allocation_ratio` or `expected_share`, that weren't in the CSV at all.

**What I thought first.** I assumed the planner needed better few-shot examples showing the exact column names. I tried adding more examples to the system prompt with the real column names spelled out. It helped for the cases that matched the examples, but novel goals still produced invented columns.

**The investigation.** I traced what information the planner actually received. It got the goal, the retrieved context chunks, and the system prompt. The context chunks contained CSV data, but as prose-formatted text with row data, not a clean schema listing. The planner was inferring column names from the goal description and the chunk content, essentially guessing what columns *should* exist for a fund capital accounts table. Sometimes it guessed right. Often it didn't.

**The real cause.** The planner had no structured schema information. It was generating SQL against an imagined schema derived from the goal's natural language, not from the actual data. This isn't a prompt engineering problem. It's an input problem. The model can't use information it doesn't have.

**The fix.** Added `_build_schema_block()` to the planner module. It reads the actual file paths and uses pandas to extract column names, then injects them into the user prompt as a structured block: "AVAILABLE FILES AND SCHEMAS: fund_capital_accounts.csv — Columns: partner_name, ownership_pct, distributions, ..." with an explicit instruction: "Do NOT invent table names, file names, or column names."

**The lesson.** The model can't use information it doesn't have. Schema grounding fixed what prompt engineering couldn't.

---

### Story 5. Silent pass, empty output

**Symptom.** Every e2e test case passed. The Critic returned "pass" verdicts with high confidence. But every report file written to `data/uploads/` was `{}`. An empty JSON object. The pipeline was reporting success while delivering nothing.

**What I thought first.** I assumed WriteReportTool was broken -- maybe a path issue, or the JSON serialization was silently swallowing an error. I read the tool code and it looked correct: it received `args["results"]`, serialized it, wrote it to disk. Simple.

**The investigation.** I queried the trace store for a recent run and looked at the exact args passed to `write_report`. The `results` key was missing entirely. The executor injects upstream step outputs under the key `prior_results`, but WriteReportTool was looking for `results`. Since `args.get("results", {})` returns `{}` when the key is absent, the tool wrote an empty dict to disk without raising any error. No crash, no warning, no indication that anything was wrong.

**The real cause.** A key mismatch between the executor and the tool. The executor passes upstream data as `prior_results`. WriteReportTool read `args.get("results", {})`. The key never matched, so every report was silently empty. The Critic validated against the in-memory executor state, which contained real results. The tests validated verdicts and flag counts, also from in-memory state. Nobody checked the file on disk.

**The fix.** One line: `results = args.get("results") or args.get("prior_results", {})`. Two characters of substance -- the `or` fallback.

**The lesson.** Validate the actual deliverable, not the steps that produced it. Tests pass when they find what they measure. If you don't measure the artifact the user actually receives, your tests are telling you something other than "it works."
