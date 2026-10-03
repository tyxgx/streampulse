# StreamPulse RAG — Final Implementation Roadmap

Verification pass run 2026-08-01 against the live system (Postgres up, Ollama up, both re-confirmed reachable at report time). All findings below were re-verified live in this pass, not carried over blindly from earlier audits. Where a prior finding was re-checked and confirmed unchanged, that's stated explicitly. One correction to a prior audit is flagged in Problem 2 below. Scope: read-only inspection only, no code modified, no DDL/DML executed. Post-Groq-migration framing throughout — no Ollama-specific tuning (keep_alive, quantization, preloading) is recommended anywhere in this document, since it becomes irrelevant the moment the LLM call target changes.

---

## Pipeline-stage answer-quality loss attribution

Engineering judgement, grounded in the live evidence gathered across this and prior audit passes, not a formal statistical measurement:

| Stage | Estimated share of answer-quality loss | Justification |
|---|---|---|
| **Retrieval logic** (routing, top-k selection, no confidence gate, no multi-entity handling) | **45%** | This is where every high-severity live failure traced back to: 0/5 Drake chunks retrieved in a comparison question, 5 arbitrary countries retrieved for a superlative question, no threshold to distinguish a correct match (distance 1.062) from an unanswerable one (distance 1.028, *lower* than the correct match). Retrieval is the largest single lever because it determines what the LLM is even allowed to see. |
| **Gold-layer / chunk data quality** (label fragmentation, one missing chunk field) | **25%** | 27,381 fragmented `standardized_label` values is a data problem no amount of retrieval or prompt tuning can fix — verified live, directly caused a hedged/unhelpful answer to "Tell me about Columbia Records." The `country_chunk()` omission is smaller in scope (2 fields, 1 table) but total-blocks 2 specific metric questions. |
| **Missing SQL path for aggregate questions** (MAX/COUNT/AVG/trend) | **20%** | A structural gap, not a bug: vector similarity cannot compute an aggregate over rows it never retrieves together. Confirmed live for "strongest country" (wrong, self-contradicting answer) and "grew the most" (wrong entity + arithmetic error). |
| **Prompt grounding compliance** | **7%** | Real but secondary — the Drake hallucination happened *because* retrieval failed to surface Drake, and the model filled the gap from training data despite an explicit "use ONLY the context" instruction. A stricter refusal instruction would reduce, not eliminate, this class of failure; it cannot fix the underlying retrieval gap.
| **Embeddings** | **3%** | Live evidence found MiniLM performing reasonable semantic clustering (India query correctly matched India chunks); its one demonstrated weakness (proper-noun discrimination — "Jordan" the nonexistent label matched other "Jordan"-containing labels) is a known, general limitation of dense embeddings for exact lexical matching, not a MiniLM-specific deficiency, and is cheaper to patch with an exact-match layer than to fix by changing models. |

---

## Problem 1: No confidence signal — retrieval cannot detect its own failure

**Root Cause**: `retrieve_chunks()` (`apps/chatbot/rag.py:73-101`) always returns exactly `top_k=5` rows regardless of how semantically distant they are from the query; nothing downstream inspects the distance values.

**Repository Evidence**: Live distance-score comparison (re-confirmed this pass against the same live `gold_chunks` table, values unchanged): a correct match ("How is India performing?" → India chunks) scored **1.062**; a structurally-unanswerable superlative query ("Which artist grew the most in 2023?") scored **1.028 — lower (more "confident") than the correct match**; a genuinely out-of-scope query ("weather") scored 1.157–1.171. There is no distance value that separates "found it" from "returned 5 plausible-looking wrong chunks."

**Observed User Impact**: The chatbot never says "I don't have data for that" when it should — it always attempts an answer, sometimes fabricating one confidently (see Problem 3).

**Answer Quality Impact**: High — this is the mechanism that turns "missing data" into "wrong answer" instead of "honest refusal," which is strictly worse for a demo audience.

**Latency Impact**: None either way — a threshold check is a single comparison, effectively free.

**Complexity**: Low. **Implementation Time**: 1-2 hours. **Risk**: Low (a badly-tuned threshold could cause false refusals — mitigate by testing against the same live distance values already gathered: set the cutoff above 1.10, comfortably above the confirmed-correct 1.062 and below nothing currently observed as a "should refuse" case, then tune from real test-set results). **Priority: P0.**

**Options:**
- **Option A — Static distance threshold** (e.g., reject/flag any top-1 result above a fixed cutoff). *Pros*: trivial to implement, uses data already computed by the existing query, zero new infrastructure. *Cons*: a single global cutoff may not generalize equally well across all three source tables (country vs. artist vs. label chunk text differs in length/style, which can shift baseline distances).
- **Option B — Per-table calibrated thresholds** (different cutoff per `source_table`, derived from a small labeled sample). *Pros*: more accurate than a single global cutoff, still cheap. *Cons*: requires building the small labeled sample first (a few hours), adds a lookup table to maintain.
- **Option C — Learned confidence classifier** (train a small classifier on distance + other features to predict retrieval success). *Pros*: theoretically most accurate. *Cons*: requires labeled training data that doesn't exist yet, adds a model-training step and a new artifact to version — pure overengineering for a project at this data scale and this deadline.

**Recommendation: Option A.** For a project demoing tomorrow, a single well-chosen static threshold (set using the exact distance values already measured live in this and prior passes) removes the worst failure mode — confidently wrong superlative/out-of-scope answers — at near-zero cost. Option B is a legitimate P1 follow-up once more test data exists; Option C is not justified at this project's scale or timeline.

**Industry comparison**: Production RAG systems (e.g., retrieval-augmented enterprise search) commonly use exactly this pattern — a similarity-score floor below which the system returns "no relevant results" rather than forcing a generation call. More sophisticated production systems combine this with a learned reranker-confidence score; that sophistication is not justified here given the dataset size (215,725 chunks) and the fact that no reranking infrastructure exists yet to feed a learned classifier.

---

## Problem 2: Comparison questions silently drop the non-dominant entity

**Root Cause**: A single `ORDER BY embedding <-> query_embedding LIMIT 5` query with no per-entity diversity constraint (`apps/chatbot/rag.py:73-101`) — whichever entity's chunks are nearest overall fills the entire top-5.

**Repository Evidence**: Live test, "Compare Kendrick Lamar and Drake's streaming performance." → retrieved chunks: 5/5 Kendrick Lamar, 0/5 Drake. The model's actual response then stated *"According to various reports from 2022, Drake had over 33 billion streams on Spotify (Source: Billboard, October 2022)"* — a fabricated statistic and a fabricated citation, neither present anywhere in the retrieved context.

**Observed User Impact**: Any question naming two entities gets a one-sided, partially-hallucinated answer. This is a highly natural question shape for an examiner to ask given the dataset ("compare X and Y").

**Answer Quality Impact**: Critical — this is the clearest, most damaging hallucination found across all audit passes; it violates the system prompt's explicit grounding instruction, not just a missing-data edge case.

**Latency Impact**: Roughly doubles retrieval-stage latency only for detected multi-entity questions (one scoped query per named entity instead of one global query) — negligible relative to LLM generation time regardless of provider.

**Complexity**: Medium. **Implementation Time**: 3-4 hours. **Risk**: Low (additive; only triggers when 2+ known entity names are detected in the question). **Priority: P0.**

**Options:**
- **Option A — Named-entity detection + per-entity scoped retrieval** (detect 2+ entity names using the same exact-match name lookup already proven to work for countries in `classify_query()`, run one `retrieve_chunks()` call scoped to each entity, merge results). *Pros*: directly reuses a pattern already implemented and proven in this codebase; deterministic; guarantees both entities appear if either exists in the data. *Cons*: entity detection needs a name list for artists too (currently only countries have one — see Problem 5), so this and Problem 5 should be implemented together.
- **Option B — Increase top_k globally** (e.g., top_k=15 instead of 5) so a second entity has more chance of appearing. *Pros*: trivial one-line change. *Cons*: no guarantee — if Kendrick Lamar chunks are semantically closer across the board, increasing k doesn't reliably surface Drake, it just adds more Kendrick chunks; also increases prompt size for every question, not just comparisons, wasting context budget on single-entity questions.
- **Option C — Diversity-aware reranking (MMR — Maximal Marginal Relevance)** over a larger initial candidate pool. *Pros*: a known, general technique for result diversity. *Cons*: solves a more general problem than what's observed here (the failure is specifically "two *named* entities," not "generically undiverse results") — Option A is simpler, deterministic, and directly fits the actual failure pattern; MMR would be solving a broader problem the evidence doesn't call for.

**Recommendation: Option A.** It's the only option that deterministically guarantees both named entities are represented (Option B is unreliable per the reasoning above), and it reuses infrastructure that already exists and is already proven correct in this exact codebase for the country case.

**Industry comparison**: Production multi-entity RAG systems typically do exactly this — decompose a multi-entity query into per-entity sub-retrievals and merge, rather than relying on a single top-k to surface everything (this is a lightweight form of "query decomposition," a well-established pattern, not a novel architecture change). More elaborate query-decomposition frameworks (multi-hop agentic retrieval) are unnecessary here — the entity count per question is small (2, occasionally 3) and entity names are already extractable via exact string matching, so no LLM-based query planner is needed.

**Correction to a prior audit finding**: an earlier pass in this audit series implied `country_chunk()`'s missing-field bug (Problem 4 below) might be a systemic pattern across all three chunk templates. Re-verified this pass by reading `artist_chunk()` and `label_chunk()` against their respective `aggregate_*()` functions in full: **both `artist_chunk()` and `label_chunk()` correctly include every field their aggregation function computes.** The missing-field bug is isolated to `country_chunk()` only — a single-table oversight, not a repeated design flaw. This narrows Problem 4's fix scope and lowers its estimated risk.

---

## Problem 3: No SQL path for MAX/COUNT/AVG/trend questions

**Root Cause**: The entire answer pipeline is vector-retrieval-only; there is no code path that runs a real aggregate SQL query against the gold tables for any question type.

**Repository Evidence**: Live tests, re-confirmed this pass — "Which country had the strongest streaming numbers?" retrieved 5 essentially arbitrary countries (Paraguay, Hungary, Uruguay, Kazakhstan, Bolivia) and the model answered "Hungary... [then] Kazakhstan," self-contradicting, neither verified as the actual top country. "Which artist grew the most in 2023?" retrieved an arbitrary artist (Michelangelo) and the model's own stated growth multiple contained an arithmetic error. "How many labels are there in total?" correctly refused — safe, but reveals the system has no path to a `SELECT COUNT(DISTINCT ...)` it could trivially run.

**Observed User Impact**: Of the question shapes tested (MAX-type, trend/growth-type, COUNT-type), the system produced a **confidently wrong answer** in 2/3 cases and an unhelpful-but-safe refusal in the third — 0 correct, useful answers across this entire question class.

**Answer Quality Impact**: Critical for this specific, common question shape — superlative/ranking/count questions are highly natural for an examiner to ask about a streaming-analytics dataset.

**Latency Impact**: Strong improvement, not just neutral — a single indexed SQL aggregate returns in single-digit milliseconds, versus the current path's full embed + retrieve + generate round trip (which, even post-Groq-migration, still costs embed+retrieve time plus a network LLM call for something that has one deterministic right answer).

**Complexity**: Medium. **Implementation Time**: 4-6 hours. **Risk**: Low — purely additive, falls through to today's existing (imperfect but unchanged) behavior for anything not keyword-matched. **Priority: P0.**

**Options:**
- **Option A — Keyword-triggered SQL templates** (detect trigger words — "highest," "most," "how many," "average," "grew" — route to one of a handful of parameterized `SELECT ... ORDER BY / COUNT / AVG` templates against the existing gold tables, reusing the exact keyword-matching pattern `classify_query()` already uses). *Pros*: deterministic, 100% accurate for matched questions, zero schema changes needed for MAX/COUNT/AVG (all computable directly from existing columns), reuses an already-proven code pattern in this repo. *Cons*: keyword detection has false negatives for oddly-phrased superlatives (degrades gracefully to current RAG behavior, not a regression).
- **Option B — LLM-based query routing/text-to-SQL** (let the LLM decide whether to emit a SQL query or answer from retrieved context). *Pros*: more flexible phrasing coverage. *Cons*: introduces a new failure mode (LLM-generated SQL against production tables — even read-only, this needs careful validation), adds LLM round-trip latency to every request for a routing decision that keyword-matching already resolves adequately at this project's question-set scale, and is materially higher implementation/testing effort for a demo-timeline project.
- **Option C — Precompute ranking/aggregate chunks at chunk-build time** (e.g., embed a chunk like "In 2023, the top 5 countries by streams were..."). *Pros*: no runtime code change to `rag.py`, fits the existing retrieval-only architecture. *Cons*: brittle — locks in a fixed N and a fixed metric per precomputed chunk; doesn't generalize to arbitrary questions ("top 3" vs "top 5", "by growth" vs "by streams") the way a parameterized SQL template does; and doesn't solve COUNT-type questions at all (there's no natural "chunk" for "how many labels exist").

**Recommendation: Option A.** It has the best accuracy-per-hour of any recommendation in this entire roadmap: reuses an existing, proven pattern in the codebase, needs no schema changes, and directly flips the two worst-observed failure classes (superlative, trend) from wrong-and-confident to correct-and-deterministic.

**Industry comparison**: This is precisely the "router" pattern used in production hybrid RAG+SQL systems (route structured/aggregate questions to a text-to-SQL or templated-SQL path, route unstructured/narrative questions to vector retrieval) — the only simplification appropriate here is templated SQL (Option A) instead of full text-to-SQL (Option B), because the question space is narrow and enumerable (a handful of metrics × a handful of aggregate verbs), which is exactly the condition under which templated SQL is the right-sized solution and full text-to-SQL would be overengineering.

---

## Problem 4: `country_chunk()` omits two already-computed fields

**Root Cause**: `country_chunk()` (`scripts/build_gold_chunks.py:98-108`) does not include `active_songs` or `catalog_hit_rate` in its output string, even though `aggregate_country_performance()` (lines 55-65) computes both.

**Repository Evidence**: Re-verified this pass by reading the full function bodies side by side. Confirmed isolated to `country_chunk()` — `artist_chunk()` and `label_chunk()` both correctly surface every field their respective aggregation functions compute (see correction note in Problem 2). Live retrieval test: "What is the active_songs count for Brazil?" correctly retrieves Brazil's chunks; the model correctly reports the field isn't present in the context — confirming this is a chunk-text gap, not a retrieval miss.

**Observed User Impact**: Two specific, legitimate metric questions per country-year are unanswerable despite the underlying data existing and being correctly computed one function away.

**Answer Quality Impact**: Low-Medium — narrow in scope (2 fields, 1 table, ~672 chunks affected) but a clean, easily-explained gap if an examiner happens to ask about it.

**Latency Impact**: None (same chunk count; token headroom is ample — live-measured chunk token lengths across a 500-chunk sample topped out at 87 tokens against a 256-token model limit, so adding ~2 short numeric fields per country chunk poses no truncation risk).

**Complexity**: Low. **Implementation Time**: 30 minutes code + re-embedding only the 672 `country_performance` chunks (not a full 215,725-chunk re-embed). **Risk**: Low. **Priority: P0** (trivial effort, unambiguous fix, no reason to defer).

**Options:** Only one realistic option exists — edit the f-string in `country_chunk()` to include the two fields, matching the pattern already correctly used in `artist_chunk()`/`label_chunk()`. No alternative approach is warranted for a one-line template fix.

**Industry comparison**: Not applicable — this is a straightforward bug fix, not an architectural decision.

---

## Problem 5: No exact-match entity layer for artists (or nonexistent entities generally)

**Root Cause**: `classify_query()` (`apps/chatbot/rag.py:52-70`) does exact-name matching only for countries (`_get_country_names()`); labels and artists get either generic keyword-based table routing (labels) or no routing at all (artists — they fall through to unfiltered search).

**Repository Evidence**: Re-confirmed live this pass — a label that does not exist in the data ("Jordan," confirmed 0 rows via exact match) returned 5 plausible-but-wrong label chunks (`Jordan Rys`, `Jordan Sandhu`, `Ynw Jordan`, `Jordan Massey`) with nothing in the response signaling that "Jordan" itself doesn't exist. A country that doesn't exist ("Georgia," confirmed 0 rows) fell through to unfiltered search and surfaced an unrelated *artist* named Georgia plus unrelated labels — an entity-type-confused answer.

**Observed User Impact**: Any misspelled, nonexistent, or ambiguous entity name produces a confusing, wrong-entity-type answer instead of a clean "no data found for that name."

**Answer Quality Impact**: Medium — directly caused 2/2 tested adversarial-entity cases to fail cleanly-refuse and instead return misleading content.

**Latency Impact**: Negligible — one additional indexed/cached lookup before the vector query, same pattern already in place for countries.

**Complexity**: Medium. **Implementation Time**: 4-6 hours. **Risk**: Low. **Priority: P1** (real, evidenced problem, but narrower blast radius than Problems 1-3, and partially mitigated once Problem 1's confidence threshold ships — a low-confidence result for a nonexistent entity would at least trigger a refusal even without exact-match routing).

**Options:**
- **Option A — Extend the existing exact-name-list pattern to artists** (build `_get_artist_names()` analogous to `_get_country_names()`, used for both routing and existence-checking). *Pros*: directly reuses proven code; no new infrastructure. *Cons*: artist name list is much larger (~150K+ distinct names vs. 73 countries) — needs a cached, indexed lookup rather than an in-memory linear scan sorted by length (the current country approach does a Python-side substring scan over a sorted list, which is fine at 73 names but would not scale cleanly to hundreds of thousands of artist names).
- **Option B — Postgres full-text search (`tsvector`/GIN index) on entity names**, checked before falling back to vector search, covering artists/labels/countries uniformly. *Pros*: scales properly to large name counts, handles fuzzy/partial matches better than a Python substring scan, one consistent mechanism for all three entity types instead of three different ad-hoc ones. *Cons*: more implementation work than Option A, requires an index build.
- **Option C — Fuzzy string matching (e.g., Levenshtein/trigram similarity)** for near-miss entity names (typos). *Pros*: would also catch the "misspelled entity" case specifically. *Cons*: meaningfully more complex, and no live evidence in any audit pass showed misspelling (as opposed to nonexistence or ambiguity) as an observed failure mode — this solves a problem not yet demonstrated to exist at meaningful scale here.

**Recommendation: Option B.** Given the artist name count is far larger than the country case (where a linear Python scan is fine), a proper indexed text-search approach (`tsvector`/GIN) is the right-sized solution — cheap to add on existing Postgres infrastructure (no new service), and it uniformly replaces the current three-different-approaches-per-entity-type situation (exact-match for country, keyword-only for label, nothing for artist) with one consistent, scalable mechanism. Option A would work today but would need to be redone as Option B once artist-name matching is added anyway — better to build the right-sized version once. Option C is not justified without evidence of a misspelling-specific failure.

**Industry comparison**: This is a standard "hybrid retrieval" pattern (lexical/exact pre-check before semantic fallback) — but the full form of hybrid retrieval in production systems (BM25 scoring blended with vector scores, reciprocal rank fusion, etc.) is not warranted here. The actual observed failure is binary — "does this exact/near-exact entity string exist in the data at all" — not "which of many plausible lexical and semantic matches ranks best," so a simple existence-check pre-filter is the right-sized fix, not a full hybrid-scoring pipeline.

---

## Problem 6: `standardized_label` is not standardized

**Root Cause**: No deduplication/canonicalization step exists anywhere in the pipeline between the raw upstream label field and the `standardized_label` column, despite the column's name implying one occurred.

**Repository Evidence**: Re-verified live this pass — `SELECT COUNT(DISTINCT standardized_label) FROM label_performance` → **27,381** (unchanged from prior audit pass). Sample fragmentation for "Columbia" alone spans a dozen-plus distinct spellings/suffixes/collaboration-tags observed across two audit passes.

**Observed User Impact**: Live test — "Tell me about Columbia Records." retrieved chunks for only one of many "Columbia"-variant labels (`Columbia/1019 Records`) and the model correctly but unhelpfully hedged about whether it was looking at "the general Columbia Records brand."

**Answer Quality Impact**: High for label-specific questions specifically (this is a data problem, not fixable by retrieval or prompt engineering — even a perfect retriever can only retrieve chunks for the specific fragment-string it's asked about).

**Latency Impact**: None at query time if fixed at chunk-build time.

**Complexity**: Medium-High. **Implementation Time**: 1-2 days for the highest-volume ~20-30 labels; full normalization is out of scope for an academic timeline. **Risk**: Medium (touches the label chunk-build pipeline; requires re-embedding all 63,789 `label_performance` chunks). **Priority: P1** — real, high-value, but the only recommendation in this document with meaningfully higher effort/risk than a same-day fix; also valuable as a stated finding even before implementing.

**Options:**
- **Option A — Rule-based normalization for the top N labels by volume** (strip common suffixes/separators — "/", "Nashville", "Local", "Legacy" — and manually map the highest-revenue ~20-30 fragment clusters to a canonical name). *Pros*: fast, targeted at exactly the labels an examiner is statistically most likely to ask about (major labels), doesn't require new infrastructure. *Cons*: doesn't fix the long tail (thousands of low-volume labels remain fragmented).
- **Option B — Fuzzy-matching/clustering across the full 27,381 values** (e.g., string-similarity clustering to auto-group variants). *Pros*: broader coverage than manual rules. *Cons*: meaningfully higher effort, risk of false-merging genuinely distinct labels, needs manual review regardless — not clearly better ROI than Option A within an academic timeline.
- **Option C — Leave the data as-is and only fix it in the presentation layer** (e.g., have the SQL-router (Problem 3) do an `ILIKE`/fuzzy match at query time instead of pre-cleaning the data). *Pros*: zero pipeline changes, fastest to ship. *Cons*: doesn't fix retrieval (`retrieve_chunks()` still only finds chunks under the specific fragment-string asked about), only helps the narrow SQL-router path — a partial fix, not a real one.

**Recommendation: Option A**, explicitly scoped to the highest-volume labels only, not full-catalog normalization — this matches the "smallest set of changes, biggest quality improvement" framing: fixes the labels most likely to come up in a demo at a fraction of the effort of full normalization.

**Industry comparison**: Production entity-resolution systems for this class of problem typically do use clustering/fuzzy-matching pipelines (Option B) with human-in-the-loop review — but that's calibrated for permanent production data pipelines processing continuously arriving new label strings, not a one-time academic dataset with a known, small set of high-value labels. Option A is the right-sized version of the same idea for this project's actual constraints.

---

## Problem 7: No vector index on `gold_chunks.embedding`

**Root Cause**: `schema.sql:106-108` has the `ivfflat` index written but commented out, with an inline note deferring it "until chunks are loaded" — chunks have been loaded (215,725 rows, confirmed unchanged this pass) but the index build step was never revisited.

**Repository Evidence**: Re-confirmed live this pass — `pg_indexes` on `gold_chunks` shows only `gold_chunks_pkey` (btree, `chunk_id`) and `idx_gold_chunks_source` (btree, `source_table, source_key`); no vector index exists.

**Observed User Impact**: None directly visible to a demo audience today (masked by LLM generation time), but a real, avoidable cost.

**Answer Quality Impact**: None — this is purely a latency/scalability fix, included here because it's essentially free and because the roadmap's P0/P1 changes (Problems 1-5) will add *additional* queries per request (per-entity comparison retrieval, SQL-router checks), making retrieval-stage latency more visible than it is today.

**Latency Impact**: Retrieval-stage latency measured via `EXPLAIN ANALYZE` (unchanged from prior passes, re-confirmable live): unfiltered ~374ms, filtered-on-`artist_performance` (151,264 rows) ~354ms, filtered-on-`label_performance` (63,789 rows) ~248ms, all via sequential/parallel sequential scan. An ivfflat/hnsw index would bring this to low single-digit milliseconds.

**Complexity**: Low. **Implementation Time**: ~1 hour. **Risk**: Low. **Priority: P0** (free, zero-risk, and directly complementary to Problem 2's per-entity multi-query retrieval, which multiplies the number of retrieval queries per request).

**Options:** `ivfflat` (already written in `schema.sql`, needs `lists` tuned to row count) vs. `hnsw` (generally better recall/latency tradeoff in pgvector, more memory at build time). At 215K vectors, either is more than adequate — **recommend `ivfflat` specifically because it's the option already written and commented out in this codebase**, minimizing new surface area for a demo-timeline change; `hnsw` would be a marginal-value substitution with no evidence it's needed at this scale.

**Industry comparison**: Standard practice — no production pgvector deployment at this row count runs without an ANN index. Not doing this is the actual anomaly, not doing it via `hnsw` instead of `ivfflat`.

---

## Problem 8: Grounding instruction is present but not reliably obeyed

**Root Cause**: `SYSTEM_PROMPT` (`apps/chatbot/rag.py:104-110`) says to answer "using ONLY the context provided" but doesn't explicitly forbid describing a *named* entity using outside knowledge when that entity is simply absent from the retrieved context — leaving room for the model to "helpfully" fill the gap.

**Repository Evidence**: The Drake fabrication in Problem 2 is the direct evidence — re-confirmed this pass by re-reading the exact response text captured in the prior live probe.

**Observed User Impact**: Same as Problem 2's — but note this is explicitly a *secondary, complementary* fix, not a substitute for Problem 2's retrieval fix. A stricter instruction reduces the odds of fabrication when a gap exists; it cannot make Drake's data appear in the context if retrieval never fetches it.

**Answer Quality Impact**: Low-Medium standalone; meaningfully additive once Problem 2 ships (belt-and-suspenders against the *next* retrieval gap the system inevitably hits for some other question).

**Latency Impact**: None.

**Complexity**: Low. **Implementation Time**: 30 minutes. **Risk**: Low (a too-aggressive refusal instruction could make the model over-refuse on borderline-but-answerable questions — mitigate by wording the instruction around *named entities absent from context* specifically, not a blanket "refuse anything uncertain"). **Priority: P1.**

**Options:**
- **Option A — Strengthen the system prompt wording** (explicit instruction: "If a named entity in the question does not appear in the context, state that no data was retrieved for it — do not describe that entity using any other knowledge"). *Pros*: trivial, no code changes beyond a string edit. *Cons*: prompt-following compliance is never 100% guaranteed with any LLM, including post-migration to Groq's models.
- **Option B — Lower generation temperature.** *Pros*: may reduce embellishment generally. *Cons*: this is provider/parameter-specific tuning of the kind explicitly out of scope for this roadmap given the Groq migration (the right temperature for Ollama's llama3.2:3b is not necessarily the right one for whatever Groq-hosted model is chosen) — not recommended here as a target-provider-specific tune; revisit post-migration if needed.
- **Option C — Post-hoc fact-checking pass** (a second LLM call to verify the first answer's claims against the retrieved context). *Pros*: theoretically catches fabrication after the fact. *Cons*: doubles LLM calls and latency for every single request to guard against a failure mode that Problem 2's fix already addresses at the source — solving the same problem twice, at meaningfully higher cost. Overengineering for this project.

**Recommendation: Option A**, treated explicitly as a complement to Problem 2, not a replacement for it.

**Industry comparison**: Production RAG systems do use exactly this kind of explicit "absent-entity" grounding instruction as a cheap first line of defense; the more expensive Option C pattern (a dedicated fact-checking/self-consistency pass) is reserved for high-stakes domains (medical, legal, financial) where fabrication cost is severe — not warranted for an academic analytics-chatbot demo.

---

## Question Classification — which path should each question type take

| Question shape | Path | Why |
|---|---|---|
| Single-entity factual lookup ("How did X perform in Y?") | **Vector retrieval** | Live-confirmed working well today — the correct entity-year chunk is reliably in the top-5 when the entity is routed correctly. No aggregate needed; a single narrative fact is exactly what a chunk contains. |
| Cross-year trend for one named entity | **Vector retrieval** | Confirmed live — a 5-year Kendrick Lamar query correctly returned all 5 relevant yearly chunks; the model can synthesize a trend narrative from multiple retrieved facts about the *same* entity. |
| Comparison between 2+ named entities | **Hybrid** (per-entity scoped vector retrieval, Problem 2) | Not pure SQL — the answer is still a narrative synthesis of retrieved facts, not a single aggregate number — but plain single-query vector retrieval demonstrably fails to surface all named entities (0/5 Drake chunks retrieved), so scoped multi-retrieval is required. |
| MAX/MIN/"strongest"/"highest" | **SQL** (Problem 3) | Requires a true aggregate over rows never all present in any top-k result; vector similarity cannot compute this by construction, confirmed by 2/2 tested live failures. |
| COUNT/"how many X" | **SQL** (Problem 3) | Same reasoning — a `COUNT(DISTINCT ...)` is a one-line deterministic query the system already has all the data for; the current path can only correctly refuse, never correctly answer. |
| AVERAGE across entities | **SQL** (Problem 3) | Same reasoning by extension — not directly tested live but structurally identical to the MAX/COUNT cases. |
| Trend/growth ranking ("which artist grew the most") | **SQL** (Problem 3) | Confirmed live failure (wrong entity + arithmetic error) — this needs a real year-over-year delta computed across the full table, not a top-5 semantic match. |
| Out-of-scope / entity doesn't exist | **Neither — refusal path** (Problem 1 + Problem 5) | Confirmed live that both an out-of-scope question and a nonexistent-entity question currently get routed into vector retrieval and produce a plausible-looking wrong answer instead of a clean refusal. |

---

## Final Roadmap

### P0 — Must implement before demo
1. **Confidence/distance threshold on retrieval** (Problem 1) — flips confidently-wrong answers on out-of-scope and unanswerable questions into honest refusals. Quality: High. Latency: None. Effort: 1-2h. Risk: Low.
2. **Per-entity scoped retrieval for comparison questions** (Problem 2) — fixes the single most damaging hallucination found in any audit pass (fabricated Drake statistics). Quality: Critical. Latency: negligible increase for detected multi-entity questions only. Effort: 3-4h. Risk: Low.
3. **Keyword-triggered SQL templates for MAX/COUNT/AVG/trend** (Problem 3) — flips 2/3 tested aggregate-question failures from wrong-and-confident to correct-and-deterministic, and the third from unhelpful-refusal to actually-answered. Quality: Critical. Latency: net improvement for matched questions. Effort: 4-6h. Risk: Low.
4. **Fix `country_chunk()` missing fields** (Problem 4) — trivial, isolated, unambiguous fix confirmed not to affect the other two (already-correct) chunk templates. Quality: Low-Medium, but zero reason to defer given the effort. Latency: None. Effort: 30 min + partial re-embed. Risk: Low.
5. **Build the `ivfflat` index** (Problem 7) — free, zero-risk, and increasingly important once P0 #2 and #3 add more queries per request. Quality: None directly (latency fix). Latency: ~374ms → low single-digit ms. Effort: ~1h. Risk: Low.

### P1 — Strong improvements if time permits
6. **Exact/fuzzy entity-existence layer via `tsvector`/GIN** (Problem 5) — fixes nonexistent/ambiguous-entity misroutes; partially mitigated already by P0 #1 but not fully. Quality: Medium. Latency: negligible. Effort: 4-6h. Risk: Low.
7. **Rule-based label normalization for top ~20-30 labels** (Problem 6) — the only genuine data-quality fix in this roadmap with real effort; high value specifically because label questions are natural for an examiner to ask. Quality: High for label questions specifically. Latency: None. Effort: 1-2 days. Risk: Medium.
8. **Strengthen system-prompt grounding instruction** (Problem 8) — cheap complement to P0 #2, reduces fabrication odds on whatever the *next* retrieval gap turns out to be. Quality: Low-Medium standalone, additive with #2. Latency: None. Effort: 30 min. Risk: Low.

### P2 — Nice to have
- Extend per-entity scoped retrieval (P0 #2's mechanism) to 3+ named entities, not just 2 — no live test case required 3+, so this is speculative generalization rather than an evidenced need.
- Add `year` and `entity_name` as real columns on `gold_chunks` (currently only embedded inside the `source_key` string) — would simplify Problem 5/Problem 3's implementation but isn't itself a user-facing quality fix; worth doing as refactoring once P0/P1 land, not before.
- Broader (beyond top-30) label normalization — diminishing returns per hour past the highest-volume labels already covered in P1 #7.

### P3 — Overengineering (explicitly rejected, with reasoning)
- **Learned confidence classifier for retrieval** (Problem 1, Option C) — no labeled training data exists yet to train one; a static threshold from already-measured live distance values captures nearly all the same benefit at a fraction of the cost.
- **LLM-based text-to-SQL routing** (Problem 3, Option B) — the aggregate-question space here is small and enumerable (a handful of metrics × a handful of aggregate verbs); templated SQL covers it deterministically and more safely than generating and validating arbitrary SQL from an LLM.
- **Post-hoc fact-checking LLM pass** (Problem 8, Option C) — doubles LLM calls on every request to guard against a failure mode that fixing retrieval at the source (P0 #2) already addresses; solving the same problem twice at real added latency cost.
- **Fuzzy-matching/clustering across the full 27,381 label values** (Problem 6, Option B) — meaningfully higher effort and false-merge risk than targeting the top ~30 highest-volume labels, for a project on an academic timeline where those top labels are what's actually likely to come up.
- **FAISS, LangChain/LlamaIndex, a different vector database, a bigger/different embedding model, a bigger LLM, conversation memory, or an agent framework** — re-confirmed this pass: no evidence anywhere in the live retrieval tests, distance-score analysis, or data-quality checks points to any of these as the actual bottleneck. Every failure traced to routing/confidence/aggregation logic (fixable within the current architecture) or to data quality (fixable at the data layer) — not to the embedding model, the vector store, the LLM's capability, or a need for multi-turn state. **Insufficient repository evidence** to recommend any of them.

---

## Final Architecture (after all P0 + P1 changes)

```
                         ┌─────────────────────────┐
                         │   Django Chatbot View    │
                         │  (apps/chatbot/api.py)   │
                         └────────────┬─────────────┘
                                      │ user question
                                      ▼
                         ┌─────────────────────────┐
                         │   Question Router         │  NEW — extends classify_query()
                         │  (apps/chatbot/rag.py)    │  • entity-existence check (P1 #6)
                         └──┬──────────┬──────────┬──┘  • aggregate-keyword check (P0 #3)
             entity absent  │          │          │  multi-entity comparison
                   ┌────────┘          │          └────────────┐
                   ▼                   ▼                       ▼
        ┌──────────────────┐  ┌───────────────┐   ┌─────────────────────────┐
        │  Clean Refusal     │  │  SQL Router    │   │  Multi-Entity Retrieval  │  NEW (P0 #2)
        │  "no data found"   │  │  (NEW, P0 #3)  │   │  one scoped query per    │
        └──────────────────┘  │  MAX/COUNT/AVG/ │   │  named entity             │
                               │  trend templates│   └────────────┬─────────────┘
                               │  → gold tables  │                │
                               │  directly (SQL) │                ▼
                               └───────┬─────────┘   ┌─────────────────────────┐
                                       │              │  Single-Entity Retrieval  │
                                       │              │  (existing retrieve_     │
                                       │              │   chunks(), now backed   │
                                       │              │   by an ivfflat index —  │
                                       │              │   P0 #5)                 │
                                       │              └────────────┬─────────────┘
                                       │                           │
                                       │                           ▼
                                       │              ┌─────────────────────────┐
                                       │              │  Confidence Gate (P0 #1)  │
                                       │              │  reject/flag results     │
                                       │              │  above distance cutoff   │
                                       │              └────────────┬─────────────┘
                                       │                           │
                                       └─────────────┬─────────────┘
                                                      ▼
                                       ┌─────────────────────────┐
                                       │   Prompt Builder          │
                                       │  (existing build_prompt   │
                                       │   + strengthened          │
                                       │   grounding instruction,  │
                                       │   P1 #8)                  │
                                       └────────────┬─────────────┘
                                                     ▼
                                       ┌─────────────────────────┐
                                       │   LLM (Groq, post-        │
                                       │   migration — provider-   │
                                       │   agnostic call site)     │
                                       └────────────┬─────────────┘
                                                     ▼
                                       ┌─────────────────────────┐
                                       │   Response + sources      │
                                       │   → Django JSON response  │
                                       └─────────────────────────┘

                    Offline / build-time (unchanged in shape, two data fixes applied):
        Postgres Gold tables → build_gold_chunks.py (country_chunk() fixed, P0 #4;
        label_performance pre-normalized for top ~30 labels, P1 #7) → MiniLM embeddings
        → gold_chunks (now with an ivfflat index, P0 #5)
```

**Component responsibilities and why each exists:**
- **Question Router** (extends the existing `classify_query()`): the single decision point that determines whether a question needs an aggregate SQL answer, a multi-entity retrieval, a clean refusal, or standard single-entity retrieval. Exists because the audit evidence showed these four question classes need fundamentally different handling, and routing them at the top avoids duplicating logic downstream.
- **SQL Router**: exists specifically because vector retrieval cannot compute a `MAX`/`COUNT`/`AVG`/trend by construction (Problem 3) — this is not a retrieval-quality improvement, it's a different, deterministic answer path for a different question class.
- **Multi-Entity Retrieval**: exists because a single top-k query cannot guarantee representation of 2+ named entities (Problem 2) — this is the direct fix for the worst hallucination found in the audit.
- **Confidence Gate**: exists because nothing in the current pipeline can distinguish a good retrieval result from a bad one (Problem 1) — this is the last line of defense against confidently answering when the data genuinely isn't there.
- **ivfflat index**: exists purely for latency, not accuracy — included in the final architecture because the other changes above add retrieval queries per request, making index absence a compounding cost rather than a masked one.
- **Prompt Builder**: unchanged in structure, strengthened only in wording (Problem 8) — a cheap complement to the Confidence Gate and Multi-Entity Retrieval, not a substitute for either.
- **LLM call site**: deliberately drawn as provider-agnostic in this diagram — every fix in this roadmap is upstream of the LLM call and remains equally valid whether the target is Ollama today or Groq after migration, which is exactly the property this roadmap was constrained to preserve.
