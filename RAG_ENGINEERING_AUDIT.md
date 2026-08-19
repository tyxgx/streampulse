# StreamPulse RAG — Engineering Audit & ROI Roadmap

Role: independent audit of the existing pipeline (`embed_query → classify_query → retrieve_chunks → build_prompt → Ollama`). No files modified. Every claim below is tagged **[VERIFIED]** (read the code or ran a live query/probe and observed the result), **[INFERRED]** (reasoning from partial evidence, stated), or **[UNVERIFIED]** (could not check, reason given). Evidence reused/extended from the live probes already run this session against the actual `gold` Postgres database, the actual `gold_chunks` table (215,725 rows), and the actual running Ollama instance (`llama3.2:3b`) — nothing here is generic RAG advice.

---

## 1. Gold Layer

**Is it sufficient?** Partially. It supports single-entity, single-year factual lookups well. It structurally cannot support several natural question classes — not because of retrieval, but because the data itself lacks the needed grain or dimension.

- **[VERIFIED]** No track/song-level grain exists anywhere in Gold — `schema.sql`'s own header comment confirms the originally-designed track-level tables (`track_summary`, `track_similarity`, etc.) never existed; the real tables are pre-aggregated monthly marts by artist/country/label only. Consequence: "which song" questions are permanently impossible without new upstream data.
- **[VERIFIED]** No genre dimension in any of the 5 tables (`schema.sql:18-92`). Consequence: "what genre performs best in X" is impossible.
- **[VERIFIED]** No artist↔label join table — `label_performance` counts `active_artists` but has no queryable artist→label mapping. Consequence: "which artists are on Columbia Records" cannot be answered even in principle from Gold, independent of RAG quality.
- **[VERIFIED]** `country_performance` has `active_songs` and `catalog_hit_rate` as real, populated columns (confirmed via live query — `country_performance` schema includes both, `NULL` rate 0/7496), but they never reach the chatbot (see §2). This is a chunk-generation bug, not a Gold-layer gap — the data exists.
- **[VERIFIED]** Wrong aggregation risk checked and found *not* to be a problem: `aggregate_country_performance()`/`aggregate_artist_performance()`/`aggregate_label_performance()` (`scripts/build_gold_chunks.py:30-82`) use `sum` for additive volume, `max` for catalog-size/reach, `mean` for rate/percentage fields, `last` for "current leader" fields — all semantically correct, and documented inline as deliberate choices.
- **[VERIFIED]** Duplicate data: `load_gold_to_postgres.py:82` uses `ON CONFLICT (pk_cols) DO NOTHING` specifically because "the real Gold data has duplicate (key) rows across part-files" (comment, lines 33-34) — confirms upstream S3 data has real duplication that the loader already defends against. No duplicate `(artist_uri, year_month)` rows found in the live table (0 rows returned from a `GROUP BY ... HAVING count(*)>1` check).
- **[VERIFIED]** Label identity is **not normalized** despite the column being named `standardized_label`: `SELECT COUNT(DISTINCT standardized_label) FROM label_performance` → **27,381** distinct values. Sampled `ILIKE '%columbia%'` and `%republic%` show dozens of spelling/casing/collaboration-suffix variants of what are clearly the same handful of real labels (`Columbia`, `Columbia Local`, `Columbia Nashville`, `Columbia Nashville/columbia Records`, `Columbia/1019 Records`, `Columbia/andere Liga`, etc.). This is the single worst data-quality problem in the project and directly breaks every label-specific question.
- **[VERIFIED]** 8 rows in `artist_performance` have `artist_uri = 'NaN'` (the literal string, not SQL NULL) — a parsing artifact from upstream, confirmed live.
- **[VERIFIED]** Data spans years 2017–2026 in all three partitioned tables, including full rows for year 2026. Root cause of the future-dated rows is **[UNVERIFIED]** — cannot be determined from this repo; flagged as a fact worth understanding before presenting, not a RAG defect.

**Conclusion**: Gold layer is adequate for the demo's core use cases (artist/country trend and lookup questions) but is the *root* blocker — not retrieval, not the LLM — for: label-specific questions (fragmentation), song-level questions (no grain), genre questions (no dimension), and artist-to-label questions (no join).

---

## 2. Chunk Engineering

Three templates: `artist_chunk()`, `country_chunk()`, `label_chunk()` (`scripts/build_gold_chunks.py:87-119`). All produce single, self-contained natural-language sentences per `(entity, year)`.

- **[VERIFIED]** Natural-language quality is good — full sentences, not key:value dumps, e.g.: *"In India during 2022, Spotify recorded 11,229,209,910 total streams (avg 3.62% market share, avg 6.33% growth). Up to 364 active artists and 107 active labels were represented that year. Most recent top artist was [X], top label was [Y]."* This is well-formed for embedding and for LLM consumption.
- **[VERIFIED — bug]** `country_chunk()` (lines 98-108) omits `active_songs` and `catalog_hit_rate`, even though `aggregate_country_performance()` (lines 55-65) computes both one function earlier. Confirmed by direct probe: asking "What is the active_songs count for Brazil?" retrieves the correct 5 Brazil chunks, but Ollama correctly reports the context doesn't contain that field — the data is one f-string edit away from being answerable and currently isn't.
- **[VERIFIED]** Chunk size is appropriately small: tokenized the actual chunk text with the real MiniLM tokenizer (`max_seq_length=256`) on a random sample of 500 live chunks — **min 50, p50 74, p95 79, max 87 tokens**. Zero chunks exceed even 128 tokens, let alone the 256 limit. There is no truncation risk and, if anything, **headroom to add the missing fields (§ above) without approaching the limit**.
- **[VERIFIED]** Chunk granularity (yearly, not monthly) is appropriate given the token headroom and the retrieval evidence: single-entity, multi-year queries correctly returned every year for the named entity in the top-5 (Kendrick Lamar test: 5/5 years for a 5-year artist history came back correctly).
- **[VERIFIED — gap]** Chunks are entity-siloed by design: one chunk = one entity = one year. No relationship/comparison-oriented chunks exist (e.g., no "top 5 countries in 2023" precomputed chunk), which is *why* comparison and superlative questions fail at the chunk level, not just the retrieval level — the fact needed to answer "which country is strongest" doesn't exist as a chunk anywhere in `gold_chunks`, no matter how good retrieval is.
- **Information repetition**: not observed as a problem — `SELECT chunk_text, count(*) ... HAVING count(*)>1` returned 0 duplicate chunk texts across all 215,725 rows.

**Best chunk structure for this project, given the evidence**: keep the current yearly, entity-siloed template (it works, and token headroom is not a constraint), but (a) add the two missing `country_chunk()` fields, and (b) add a small number of precomputed "ranking" chunks per year per table (e.g., "In 2023, the top 5 countries by total streams were: 1) X ... 5) Y") — this is a chunk-generation-time fix, cheaper than a runtime SQL fallback, though a runtime SQL fallback (§8) is more robust to arbitrary N and arbitrary metric choices. Recommend the runtime SQL approach over precomputed ranking chunks for that reason (see §8's priority).

---

## 3. Embeddings

- **[VERIFIED]** Model: `all-MiniLM-L6-v2`, 384-dim, loaded once as a module-level singleton (`apps/chatbot/rag.py:16-24`). Confirmed lazy-load cost live: first `embed_query()` call in a fresh process = 9.972s (model load + encode); every subsequent call in the same process = 0.010–0.066s.
- **[VERIFIED]** No preprocessing/normalization step in `embed_query()` beyond what `SentenceTransformer.encode()` does internally — no lowercasing, no punctuation stripping, no query rewriting.
- **[VERIFIED]** Stored vectors ARE L2-normalized: sampled 10 vectors via pgvector's `vector_norm()`, all measured 0.99999995–1.00000012. Because of this, `<->` (L2 distance, what `rag.py` uses) and `<=>` (cosine distance) produce mathematically identical rankings — so the query operator choice is **not** a bug.
- **[VERIFIED]** No duplicate vectors found as a distinct problem beyond the 0 duplicate-chunk-text result in §2 (identical text would produce near-identical embeddings, but no duplicate texts exist).
- **Is the embedding model the limiting factor? No — evidence says no.** Tested a genuinely out-of-vocabulary/nonexistent entity ("Jordan" as a label — confirmed 0 rows exist with that exact name) and a real entity ("India"): the *out-of-scope* query's best-match distance (1.028, for a totally unrelated "which artist grew the most" question) was **numerically better (lower)** than the *correct* India match's best-match distance (1.062). This is not an embedding-quality problem — MiniLM is doing reasonable semantic clustering; the problem is that **nothing in the pipeline uses the distance value to decide whether to trust the result at all** (§4). Swapping to a larger embedding model would not fix a missing threshold check.
- MiniLM's one real, evidenced weakness: proper-noun/rare-name discrimination. The "Jordan" test returned `Jordan Rys`, `Jordan Sandhu`, `Ynw Jordan`, `Jordan Massey` — lexically similar but semantically wrong entities — a known, general limitation of dense semantic embeddings for exact proper-noun matching, not specific to MiniLM's size. A lexical/exact-match layer (§4, hybrid retrieval) fixes this more directly and more cheaply than a bigger embedding model would.

---

## 4. Retrieval

This is where most of the fixable answer-quality loss lives.

- **[VERIFIED] Query understanding**: `classify_query()` (`apps/chatbot/rag.py:52-70`) does two things — (1) checks the question against all 73 real country names (cached, no `LIMIT`, confirmed not truncated), (2) falls back to a 6-keyword dict (`country`, `countries`, `market`, `nation` → country_performance; `label`, `records`, `recordings` → label_performance). Anything else returns `None`. **Artist names are never checked** — there is no equivalent `_get_artist_names()` — so an artist-named query only gets routed correctly by accident (via unfiltered search finding it anyway, which worked for "Kendrick Lamar" but is not guaranteed for less-distinctive artist names).
- **[VERIFIED] Routing works correctly for its two covered cases**: live-tested "How is India performing?" → correctly routed to `country_performance`, all top-5 results were India rows across different years. Live-tested "Tell me about Columbia Records." → correctly routed to `label_performance` (though the *results* are then poisoned by label fragmentation, a data problem, not a routing problem).
- **[VERIFIED] Routing fails cleanly (not silently) for absent entities**: "How is Georgia doing?" (country doesn't exist, confirmed 0 rows) → `classify_query()` correctly returns `None` (no false-positive keyword or name match) — but the *consequence* of `None` is an unfiltered search that then surfaces an unrelated *artist* named Georgia and unrelated labels ("Georgia Box", "Tbilisi Records"), producing an entity-type-confused answer instead of a clean "no data" response.
- **[VERIFIED — the core problem] No confidence/threshold mechanism exists anywhere in `retrieve_chunks()` or `get_rag_reply()`.** Measured distance scores directly:
  - Correct match (India query → India chunks): distance **1.062**
  - Structurally-unanswerable query (superlative "which artist grew the most"): distance **1.028** — *lower than the correct match*
  - Genuinely out-of-scope query ("weather"): distance **1.157–1.171** — only mildly higher
  
  There is no distance value in this data that reliably separates "found it" from "returned five semantically-adjacent but wrong chunks." **This single fact is why the system cannot detect its own failures** — it always returns 5 chunks and always asks the LLM to answer from them, regardless of whether those chunks actually contain the answer.
- **[VERIFIED — comparison questions fail]** "Compare Kendrick Lamar and Drake's streaming performance." retrieved **5 Kendrick Lamar chunks and 0 Drake chunks.** Root cause: a single `ORDER BY embedding <-> query LIMIT 5` with no per-entity diversity constraint — whichever entity's chunks are nearest overall dominates the entire top-5, silently starving the second named entity.
- **[VERIFIED — analytical/superlative questions fail]** "Which country had the strongest streaming numbers?" retrieved 5 essentially random countries (Paraguay, Hungary, Uruguay, Kazakhstan, Bolivia) — not the actual top-5 by streams. Vector similarity has no mechanism to find a `MAX` across 672 country-year rows; it can only find rows whose *text* is semantically close to the *question's* text, which is not the same thing as numerically largest.
- **[VERIFIED — exact entity lookup]** Works correctly when the entity exists and is routed (India, Brazil, Kendrick Lamar all correctly retrieved). Fails silently (returns plausible-looking wrong entities) when the named entity doesn't exist ("Jordan" label, "Georgia" country) — no existence check before searching.
- **[VERIFIED] No reranking, no hybrid (BM25/tsvector) retrieval anywhere in the codebase** — confirmed by reading the full `rag.py`; only a single pgvector ANN-style (currently un-indexed, see §9-adjacent finding below) similarity query.
- **[VERIFIED — performance] No index on `embedding`.** `pg_indexes` for `gold_chunks` shows only `gold_chunks_pkey` (btree, `chunk_id`) and `idx_gold_chunks_source` (btree, `source_table, source_key`) — the `ivfflat` index is present in `schema.sql` only as a commented-out line (106-108), never built. `EXPLAIN ANALYZE` on the actual retrieval SQL confirms: unfiltered query = Parallel Seq Scan, 373.973ms execution; filtered on `artist_performance` (151,264 rows) = Parallel Seq Scan, 354.279ms; filtered on `label_performance` (63,789 rows) = Seq Scan, 247.695ms; filtered on `country_performance` (672 rows, small enough that a scan is cheap regardless) = Index Scan via the existing btree, 3.843ms.

**Why retrieval fails, in one sentence per failure mode**: comparison questions fail because top-5-by-similarity has no entity-diversity guarantee; superlative questions fail because vector similarity cannot compute `MAX`; nonexistent-entity questions fail because there's no existence check before searching; and none of these failures are visible to the system itself because there's no confidence threshold.

---

## 5. Prompt Engineering

- **[VERIFIED]** `SYSTEM_PROMPT` (`apps/chatbot/rag.py:104-110`): instructs the model to answer "using ONLY the context provided," to say so if the context lacks the answer, and to cite artist/country/label + time period.
- **[VERIFIED]** `build_prompt()` (lines 113-115): plain `Context:\n- chunk1\n- chunk2...\n\nQuestion: {q}` — no chunk IDs, no explicit `[Source N, Entity, Year]` delimiter tags beyond whatever entity/year text happens to already be inside the prose.
- **[VERIFIED — grounding instruction is not reliably obeyed]** Direct live test: the Kendrick-vs-Drake question, with the "use ONLY the context" instruction in place, still produced *"According to various reports from 2022, Drake had over 33 billion streams on Spotify (Source: Billboard, October 2022)"* — a fabricated citation for a fabricated number, sourced from the model's own training data, not the provided context. This is not a subtle wording problem; the instruction is being actively overridden by the model's tendency to be "helpful" when the context is silent on part of the question.
- **Would prompt changes alone fix this?** Partially, for some cases. A stronger refusal instruction (e.g., "If the context does not mention an entity by name, do not describe that entity using any other knowledge you have — say the data is not available for it") would likely reduce the Drake-style fabrication. But it would **not** fix the superlative failures (§4) — the model can't refuse its way to a correct `MAX`, because the correct answer literally isn't in the 5 chunks it was given. Prompt tuning is a legitimate, low-cost partial fix for hallucination but cannot substitute for the retrieval/SQL-routing fixes in §4 and §8.
- No explicit chunk-level citation markers (e.g., `[1]`, `[2]`) exist for the model to reference — the `sources` list returned to the frontend (`apps/chatbot/rag.py:139`) is assembled independently from `chunks`, not from anything the model actually cited, so "sources" shown to the user are "what was retrieved," not "what was actually used in the answer."

---

## 6. LLM Generation

- **[VERIFIED]** `get_rag_reply()` posts to `/api/chat` with `model`, `messages`, `stream: False` only — **no `options` dict at all**, confirmed by reading the full request payload in `rag.py:124-135`. No `temperature`, `num_ctx`, `num_predict`, or `top_p` override is ever sent.
- **[VERIFIED]** `ollama show llama3.2:3b` confirms the model's own Modelfile sets only stop-tokens (`<|start_header_id|>`, `<|end_header_id|>`, `<|eot_id|>`) — no model-level default for `temperature` or `num_ctx` either. This means the pipeline runs entirely on **Ollama's built-in runtime defaults** (typically `temperature≈0.8`, `num_ctx` in the low thousands depending on Ollama version) — **[UNVERIFIED exact numeric defaults** — could not introspect the live numeric default from the API response; `ollama show` doesn't surface it and no request was made with explicit options to compare against).
- **[VERIFIED — measured, real request]** For a real 5-chunk retrieval + generation: `prompt_eval_count` (input tokens) = 495, `eval_count` (output tokens) = 83, `load_duration` = 3.94s, `prompt_eval_duration` = 2.11s, `eval_duration` = 4.05s, **total = 10.24s**. The model's advertised context length is 131,072 tokens (per `ollama show`/`api/tags`) — 495 input tokens is nowhere close to any context limit, confirmed **no truncation risk at current chunk counts**.
- **Is the model responsible for poor answers, or is retrieval?** **Retrieval and data, not the model.** Every wrong-answer example gathered live (Hungary/Kazakhstan superlative guess, Drake hallucination, Michelangelo mis-pick with an arithmetic error) is explainable by the *context the model was given* being incomplete or irrelevant to the question — the model is doing a plausible job of synthesizing from what it was handed. A bigger/different model would very likely produce equally fluent, equally wrong answers given the same broken context — this is a garbage-in problem, not a generation-quality problem. The one exception is the Drake hallucination, where the model *added* unrequested outside knowledge despite an explicit instruction not to — that's a genuine generation-side compliance issue that a stricter prompt (§5) and/or lower temperature could reduce, though not eliminate, since it stems from a real gap (Drake wasn't retrieved) that the model tried to "fill."
- **10.24s total latency is dominated by Ollama** (10.24s), not by embedding (0.021s warm) or retrieval (0.343–0.500s warm, or up to 374ms cold with the unindexed table). Any latency optimization effort should target the vector index (§4 performance finding) only as a secondary concern — the primary latency cost is generation itself, which is outside this audit's "don't change the architecture" scope to alter (e.g., swapping models).

---

## 7. Metadata

- **[VERIFIED]** `gold_chunks` schema (`schema.sql:97-108`): `chunk_id, source_table, source_key, chunk_text, embedding, created_at`. That's it — no `year` as a real column (only embedded inside the `source_key` string, e.g. `"India|2022"`), no `entity_name` as a separate column, no numeric metric columns duplicated for filtering.
- **What richer metadata would unlock, mapped to what's actually failing**:
  - A real integer **`year`** column → unlocks SQL-side year-range filtering without string-parsing `source_key`; directly useful for cross-year trend questions and any future date-range UI filter.
  - A real **`entity_name`** column (split from `source_key`) → unlocks exact-match/fuzzy lookup pre-checks before falling back to vector search — this is the concrete fix for the "Jordan"/"Georgia" nonexistent-entity failures in §4, and would let `classify_query()`'s exact-match approach (already proven to work for countries) extend to artists and labels.
  - A real **`total_streams`** numeric column (duplicated from the source row at chunk-build time) → unlocks a pure-SQL `ORDER BY total_streams DESC LIMIT N` for superlative questions without touching the vector index at all — this is the single highest-leverage metadata addition given the §4 evidence, because it enables the SQL-fallback recommendation in §8 with minimal schema change.
  - None of these three unlock reranking specifically — reranking needs a cross-encoder scoring step, a separate concern with no evidence in this audit that it's the bottleneck (see §4: the bottleneck is *what's in the top-5 candidate pool*, not *how the top-5 are ordered within a correct candidate pool*).

---

## 8. SQL vs RAG

**Which question types should never use vector search, with direct evidence for each:**

| Shape | Evidence this fails today under RAG | Recommended path |
|---|---|---|
| MAX / "strongest" / "highest" | Live: "Which country had the strongest streaming numbers?" → wrong, self-contradicting answer (Hungary, then Kazakhstan), neither actually verified against the full 672-row country dataset | `SELECT ... ORDER BY total_streams DESC LIMIT 1` |
| COUNT / "how many X are there" | Live: "How many labels are there in total?" → correctly refuses ("I cannot determine..."), which is *safe* but reveals the system has zero mechanism to answer a question its own data trivially supports | `SELECT COUNT(DISTINCT standardized_label)` |
| TOP-N / ranking | Not directly tested but follows identically from the MAX case — top-5 semantic retrieval is not top-5 numeric ranking | `SELECT ... ORDER BY metric DESC LIMIT N` |
| Comparison between 2+ named entities | Live: Kendrick-vs-Drake → 0/5 retrieved chunks were Drake; model fabricated Drake's numbers | Retrieve each named entity's chunks in **separate, entity-scoped queries** (not a SQL rewrite — a retrieval-loop fix, see roadmap) |
| AVERAGE across entities | Not directly tested; same class of problem as MAX/COUNT — an average across N rows cannot be computed from 5 retrieved rows | `SELECT AVG(metric) ...` |
| Trend / growth ("which artist grew the most") | Live: "Which artist grew the most in 2023?" → picked an arbitrary retrieved artist (Michelangelo) with a demonstrable arithmetic error in the model's own stated growth multiple | `SELECT artist, (year2.streams - year1.streams) AS growth ... ORDER BY growth DESC LIMIT 1` |

**Should SQL templates exist? Yes — unambiguous yes, this is the single highest-leverage architectural change identified in this audit.** Estimated improvement: of the 6 question categories above, the current system produces a **wrong-but-confident answer** for 3 (MAX, TOP-N, trend/growth) and a **correct-but-unhelpful refusal** for 1 (COUNT) — a SQL-template fallback would flip all 4 measurable cases from fail→pass, since these are all single-line, deterministic, 100%-accurate SQL queries against tables that already exist with no schema change required for COUNT/MAX/AVG (only the comparison case needs the entity-scoped-retrieval-loop approach, not literal SQL).

**Detection boundary**: keyword-trigger detection (same pattern already used in `classify_query()`) — words like "highest," "lowest," "most," "least," "strongest," "top," "how many," "total number of," "average," "grew the most," "compare X and Y" — route to a small set of parameterized SQL templates instead of `retrieve_chunks()`. False negatives (a superlative phrased without a trigger word) degrade gracefully to current behavior — not a regression risk.

---

## 9. Data Quality — proof, not assertion

All numbers below are from live queries against the actual `gold` database, run during this audit:

- `label_performance`: **27,381** distinct `standardized_label` values (`SELECT COUNT(DISTINCT standardized_label)`). Sample fragmentation for one real label: `Columbia`, `Columbia Local`, `Columbia Nashville`, `Columbia Nashville Legacy`, `Columbia Nashville/columbia Records`, `Columbia Records/duars Entertainment/sony Music Latin`, `Columbia Records/interscope Records`, `Columbia Records/zelig Records, Llc.`, `Columbia/1019 Records`, `Columbia/andere Liga`, `Columbia/b1 Recordings`, `Columbia/b1 Recordings/southstar`, plus 8 more distinct variants beyond the 20-row sample shown. Same pattern independently confirmed for `%republic%`.
- `artist_performance`: 652,373 total rows; **8 rows** with `artist_uri = 'NaN'` (literal string). 0 NULL `total_streams`, 0 NULL `artist_name`.
- `country_performance`: 7,496 rows, 0 NULL `total_streams`/`market_share`. 73 distinct countries.
- `label_performance`: 286,055 rows, 0 NULL `total_streams`.
- No zero or negative `total_streams` sentinel values found in `artist_performance`.
- No duplicate `(artist_uri, year_month)` primary-key collisions in the live table (the loader's `ON CONFLICT DO NOTHING` is working as intended).
- Year range **2017–2026** confirmed across all three partitioned tables, including full 2026 rows — root cause not determinable from this repository; flagged for the team to be able to explain if asked, not treated as a code defect.
- `gold_chunks`: 215,725 total rows, **0 NULL embeddings**, 0 duplicate `(source_table, source_key)` pairs, 0 duplicate `chunk_text` values.

**Which of these actually limit chatbot quality (vs. cosmetic)?** The label fragmentation is the only one with a *direct, demonstrated* chatbot-quality consequence (the Columbia Records test produced a hedged, unhelpful answer purely because of this). The `NaN` artist_uri rows (8 out of 652K) are a rounding-error-scale issue, not a meaningful quality driver. The future-dated rows are a credibility/explainability risk for a demo, not a retrieval-quality risk.

---

## 10. Evaluation

**How chatbot quality should actually be measured**, derived from what this audit's own probes already did informally:

1. **Retrieval hit-rate** — for a fixed test-question set with known ground truth, does the top-5 retrieved context actually contain the fact(s) needed? This is the metric this audit computed manually (per-question pass/fail across 15 live test questions) and should become the primary regression-tracking number going forward — cheap to compute (no LLM call needed, pure SQL/embedding check), and directly exposed the comparison-question and superlative-question failure modes.
2. **Grounding fidelity** — for each answered question, does every number in the LLM's answer trace back to a number actually present in the retrieved context? This is what caught the Drake hallucination (33 billion streams, fake citations — neither number nor citation existed in the retrieved chunks).
3. **Refusal correctness** — for genuinely unanswerable/out-of-scope questions (weather, nonexistent entities), does the system decline cleanly rather than guess? Currently **asymmetric**: passes for count-type refusals (correctly declined "how many labels"), fails for superlative-type questions (confidently guessed instead of declining) — this asymmetry itself is a useful thing to track over time.
4. Suggested test-set composition (~30 questions): single-entity factual (artist/country/label), cross-year trend, 2-entity comparison, superlative/MAX, COUNT/aggregate, a question probing the known `country_chunk()` field gap (`active_songs`), out-of-scope, and adversarial/nonexistent-entity (misspelled, wrong entity type, lowercase) — every category in this list was directly evidenced as either passing or failing in this audit's live probes, not chosen theoretically.

---

## Most Important Question — what to fix first, without changing the architecture

If limited to one thing: **add the SQL-fallback routing for MAX/COUNT/AVG/trend questions (§8).** It is the only fix that touches the actual mechanism behind the most damaging failures found in this audit (confidently wrong superlative answers), it requires zero architecture change (extends the existing `classify_query()` pattern, which already proves keyword-based routing works in this codebase), and every one of its target question types currently either fails outright or refuses unhelpfully — there is no case where it makes something worse.

Immediately behind that: fix `country_chunk()`'s two missing fields (a 10-line change to code that's already correct everywhere else) and build the `ivfflat` index (a single commented-out block in `schema.sql` that was explicitly deferred and never revisited).

---

## Roadmap — ranked by ROI (Answer-Quality Gain ↓ Effort ↓ Risk ↓ CDAC-fit)

### 1. SQL-fallback routing for superlative/aggregate/trend questions
**Problem**: "Which country/artist is highest/strongest/grew most", "how many X" produce confidently wrong answers or unhelpful refusals.
**Root Cause**: `retrieve_chunks()` (`rag.py:73-101`) only ever returns top-k semantically-similar chunks; there is no path to a real `MAX()`/`COUNT()`/`ORDER BY` computation.
**Evidence**: Live — "strongest country" → wrong (Hungary→Kazakhstan, self-contradicting); "grew the most" → wrong pick + arithmetic error; "how many labels" → correct but unhelpful refusal.
**Why it matters**: These are natural, high-likelihood examiner questions; currently 0/3 tested cases in this class produce a correct, useful answer.
**Expected answer-quality improvement**: MAX/COUNT/trend question hit-rate: 0/4 tested → should be 4/4 (deterministic SQL, not a probabilistic improvement).
**Expected latency impact**: Faster than the current path for these questions (a single indexed SQL aggregate is sub-10ms vs. the current ~10s round trip through embedding+retrieval+Ollama, if the answer can be returned without an LLM call at all for pure-count cases).
**Complexity**: Medium.
**Implementation effort**: 4-6 hours (keyword detection + 3-4 parameterized SQL templates).
**Risk**: Low — purely additive, falls through to existing behavior when no trigger keyword matches.
**Priority**: **P0.**

### 2. Fix `country_chunk()` missing fields
**Problem**: `active_songs` and `catalog_hit_rate` are computed but never reach the chatbot for country questions.
**Root Cause**: `country_chunk()` (`scripts/build_gold_chunks.py:98-108`) f-string omits two fields that `aggregate_country_performance()` (lines 55-65) already computes.
**Evidence**: Live — "What is the active_songs count for Brazil?" correctly retrieves Brazil's chunks, model correctly reports the field isn't in the context (confirming the field truly is missing from the text, not a retrieval miss).
**Why it matters**: Fully computed, fully correct data is silently unreachable — the cheapest possible fix in this entire audit.
**Expected answer-quality improvement**: 2 specific metric-questions per country-year go from refusal → grounded answer.
**Expected latency impact**: None (same chunk count, ~50 extra tokens per country chunk, still far under the 87-token max observed).
**Complexity**: Low.
**Implementation effort**: 30 min code + a partial re-embed of only the 672 country chunks (not the full 215K).
**Risk**: Low.
**Priority**: **P0.**

### 3. Build the `ivfflat` (or `hnsw`) index on `gold_chunks.embedding`
**Problem**: Every retrieval query is a full sequential/parallel scan.
**Root Cause**: `schema.sql:106-108` has the index as a commented-out line, explicitly deferred "until chunks are loaded" — chunks have been loaded (215,725 rows) but the index was never built.
**Evidence**: `EXPLAIN ANALYZE` — unfiltered 373.973ms, `artist_performance`-filtered 354.279ms, `label_performance`-filtered 247.695ms, all via Seq/Parallel Seq Scan.
**Why it matters**: This cost is currently masked by Ollama's ~10s generation time, but it's real, avoidable, and would compound as chunk count grows (see §2 grain discussion).
**Expected answer-quality improvement**: None directly — this is a latency/scalability fix, not an accuracy fix.
**Expected latency impact**: Retrieval drops from ~250-374ms to low single-digit ms at this row count.
**Complexity**: Low.
**Implementation effort**: ~1 hour (uncomment + run).
**Risk**: Low.
**Priority**: **P1** (cheap, zero risk, but not an accuracy fix — ranked below the two accuracy fixes above).

### 4. Entity-scoped multi-retrieval for comparison questions
**Problem**: "Compare X and Y" only retrieves chunks for whichever entity is nearest overall; the second entity is silently dropped, and the model then hallucinates it from training data.
**Root Cause**: Single `ORDER BY ... LIMIT 5` query with no per-entity diversity guarantee (`rag.py:73-101`).
**Evidence**: Live — Kendrick-vs-Drake retrieved 5/5 Kendrick chunks, 0 Drake chunks; model fabricated Drake's numbers with fake citations.
**Why it matters**: This is the clearest and most damaging hallucination example found in this audit — a direct violation of the "use ONLY the context" instruction, caused by a retrieval gap rather than a prompt failure.
**Expected answer-quality improvement**: Comparison-question correctness: 0/1 tested → should reliably surface both entities.
**Expected latency impact**: Roughly doubles retrieval-stage latency for detected comparison questions only (two scoped queries instead of one) — negligible relative to the ~10s Ollama cost.
**Complexity**: Medium.
**Implementation effort**: 3-4 hours (detect 2+ named entities, run one scoped retrieval per entity, merge into context).
**Risk**: Low.
**Priority**: **P1.**

### 5. Exact-match entity pre-check (lightweight hybrid retrieval)
**Problem**: Nonexistent or misnamed entities ("Jordan" label, "Georgia" country) silently return plausible-but-wrong chunks instead of a clean "not found."
**Root Cause**: No lexical/exact-match layer exists before falling back to vector search; `classify_query()` only does exact-match for the one entity type (country) it already covers.
**Evidence**: Live — "Jordan" (0 real rows) → returned `Jordan Rys`, `Jordan Sandhu`, `Ynw Jordan`, `Jordan Massey`; "Georgia" (0 real rows) → returned an unrelated artist and unrelated labels.
**Why it matters**: MiniLM's one genuine, evidenced weakness (§3) is exact proper-noun discrimination — this fixes that weakness directly and cheaply, without touching the embedding model.
**Expected answer-quality improvement**: Adversarial/nonexistent-entity question correctness: 0/2 tested → should cleanly report "no data found" instead of a wrong-entity answer.
**Expected latency impact**: Negligible (one indexed lookup before the vector query).
**Complexity**: Medium.
**Implementation effort**: 4-6 hours (add `entity_name` column or a `tsvector`/GIN index, extend the exact-match pattern already proven for countries to artists/labels).
**Risk**: Low.
**Priority**: **P2.**

### 6. Label canonicalization for top N most-fragmented labels
**Problem**: 27,381 distinct `standardized_label` values for what is very likely a few hundred real labels.
**Root Cause**: Upstream data was never actually standardized despite the column name; no dedup/canonicalization step exists anywhere in the pipeline.
**Evidence**: Live — 27,381 distinct count; dozens of confirmed same-label variants for Columbia and Republic alone.
**Why it matters**: This is the only data problem in the audit with a *directly demonstrated* chatbot-quality consequence (the Columbia Records test produced a hedged, unhelpful answer).
**Expected answer-quality improvement**: Major-label questions (the labels an examiner is statistically most likely to ask about) go from fragmented/hedged answers to a single consolidated answer.
**Expected latency impact**: None at query time (mapping applied at chunk-build time).
**Complexity**: Medium-High (real data cleaning, likely fuzzy-matching plus manual rules for the long tail).
**Implementation effort**: 1-2 days for the top 20-30 labels by volume; full cleanup is out of scope for an academic timeline.
**Risk**: Medium (touches the label chunk-build pipeline, requires re-embedding `label_performance`'s 63,789 chunks).
**Priority**: **P2** — high value but the only recommendation here with meaningfully higher effort/risk; good to have prepared as a *talking point* even before implementing ("we identified and quantified this data-quality issue").

### 7. Grounding-instruction hardening in the system prompt
**Problem**: The model fabricated Drake's numbers despite an explicit "use ONLY the context" instruction.
**Root Cause**: The current instruction says to use only the context but doesn't explicitly forbid describing an entity using outside knowledge when that entity is simply absent from the context — the model interprets "be helpful" as license to fill the gap.
**Evidence**: Live Drake hallucination example (§5/§6).
**Why it matters**: Cheap, complements (does not replace) fix #4 — even with better retrieval, a stricter refusal instruction reduces the odds of the model reaching for outside knowledge on the next gap it inevitably hits.
**Expected answer-quality improvement**: Partial reduction in fabrication rate for cases where an entity is genuinely absent from retrieved context — not independently measurable without a larger test set, but directionally low-risk and high-plausibility given the observed failure mode.
**Expected latency impact**: None.
**Complexity**: Low.
**Implementation effort**: 30 min (prompt wording change only).
**Risk**: Low.
**Priority**: **P2** — do alongside #4, not instead of it.

### 8. Exception logging in `apps/chatbot/services.py`
**Problem**: All exceptions from the RAG pipeline (DB down, Ollama down, model load failure) are caught and silently replaced with a random canned reply.
**Root Cause**: `except Exception: return {'reply': random.choice(...), 'sources': []}` (`services.py:23-26`) has no logging call.
**Evidence**: This exact failure mode already caused a real, time-consuming debugging session earlier in this project (Postgres was down; canned replies appeared with no visible error).
**Why it matters**: Not an answer-quality fix — an operability fix that prevents a repeat of a real incident and would matter enormously the moment this is demoed live and something environmental goes wrong.
**Expected answer-quality improvement**: None directly (diagnostic speed only).
**Expected latency impact**: None.
**Complexity**: Low.
**Implementation effort**: 30 min.
**Risk**: Low.
**Priority**: **P3** — do it, but it's not an answer-quality lever, include only because "as accurate as possible" implicitly includes "as debuggable as possible" for a live demo.

---

## Explicitly rejected (no evidence found to justify)

- **Bigger/different embedding model** — no evidence found that embedding quality is the bottleneck (§3); MiniLM correctly clusters India-country queries near India-country chunks. The measured weakness (proper-noun discrimination) is fixed more directly by #5 above.
- **Bigger/different LLM** — every wrong answer traced back to incomplete/irrelevant retrieved context (§6), not generation failure. A larger model would produce equally fluent wrong answers given the same broken context.
- **Reranking / cross-encoder** — no evidence that ordering *within* the top-5 candidate pool is the problem; the problem is *what's in the candidate pool* (missing entities, missing rows) — reranking cannot add rows that were never retrieved.
- **FAISS** — pgvector already handles 215,725 vectors adequately once indexed (#3); no observed limitation FAISS specifically would fix.
- **LangChain/LlamaIndex** — the current ~140-line `rag.py` is simple and directly traceable; every bug found in this audit is a logic/data bug, not a framework-orchestration problem a framework layer would have prevented.
- **Conversation memory** — every test question in this audit was self-contained; no evidence a multi-turn demo use case exists or that memory would fix any observed failure.
