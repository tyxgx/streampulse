# StreamPulse RAG Chatbot — Independent Technical Audit

Audit date: 2026-08-01. Scope: `apps/chatbot/`, `apps/gold_data/`, `scripts/build_gold_chunks.py`, `scripts/load_gold_to_postgres.py`, `schema.sql`, `config/settings/*`. Inspection-only — no files modified, no DDL/DML executed, no services started/stopped.

Services at audit time: PostgreSQL 17 — **up** (`pg_isready` succeeded). Ollama — **up** (`llama3.2:3b`, 3.2B params, Q4_K_M, loaded). Django dev server — **down** (`curl` to `127.0.0.1:8000` returned no response, connection refused). All retrieval-path evidence below was gathered by importing `apps.chatbot.rag` directly in a Django-configured Python process (`DJANGO_SETTINGS_MODULE=config.settings`), which exercises the exact same code path the running server would use, so the server being down does not weaken any finding.

---

## Part 1 — Executive verdict

**The single biggest factor limiting answer quality today is that retrieval has no way to detect its own failure.** [VERIFIED] For an out-of-scope question ("What is the weather like today?") the nearest chunk has distance 1.157–1.171; for a correctly-routed, genuinely relevant question ("How is India performing?") the nearest chunk has distance 1.062; for a superlative question that *cannot* be answered by top-5 retrieval ("Which artist grew the most in 2023?") the nearest chunk has distance **1.028 — lower (more "confident") than the correct India match.** There is no threshold in `rag.py` that would ever cause it to say "I don't have enough information" based on similarity — it always returns 5 chunks and always asks Ollama to answer from them, and Ollama, despite an explicit "use ONLY the context" instruction, still hallucinated external training-data numbers for Drake (see Part 6, Q2). This isn't a tuning problem; it's a structural gap between what vector similarity can guarantee and what the system claims to guarantee.

The second-biggest factor is `country_chunk()`/`label_chunk()`/entity fragmentation problems that are independently confirmed data/pipeline defects, not retrieval-tuning issues — see Parts 2–3.

Scores (1–10, honest, not diplomatic):
- Retrieval quality: **4/10** — routing fix for country/label is real and verified working, but no ANN index, no confidence signal, single-entity comparisons silently drop the second entity, superlatives never work.
- Chunk quality: **5/10** — clean prose, correctly yearly-aggregated with sensible sum/max/mean semantics, well under token limits (verified, see §5) — but two computed metrics are silently omitted from `country_chunk()`, and label chunks inherit unfixable upstream fragmentation.
- Data quality: **3/10** — 27,381 distinct `standardized_label` values that are overwhelmingly the same ~hundreds of real labels under different spellings (verified below), 8 rows with literal string `artist_uri='NaN'`, data extends to a future year 2026 (13 possible data-generation artifact, unverified cause).
- Engineering quality: **6/10** — parameterized SQL throughout (no injection risk found), clean separation of concerns, sensible fallback-on-exception UX, but the fallback swallows all exceptions silently with no logging (`apps/chatbot/services.py:23-26`), no rate limiting, no auth on the chat endpoint.
- Production readiness: **2/10** — no deployment has happened; this is a fair state for a project explicitly not yet deployed, but concretely: no vector index at all (full seq scan per query), synchronous blocking Ollama call on the request thread with no timeout handling beyond a blanket 60s `requests` timeout, no logging/observability, model reloads per worker process.

The three things that would move the needle most: (1) build an ivfflat/hnsw index — trivial effort, directly fixes the biggest measured latency cost (~350ms unfiltered / up to 354ms filtered-large-table seq scans); (2) add a SQL-aggregation escape hatch for superlative/count/"how many" questions — this is the only fix that touches the actual architectural gap in Part 1; (3) fix `country_chunk()` to include `active_songs`/`catalog_hit_rate` — a 10-minute code change with a full re-embed already measured at low chunk-count cost for that table (672 rows).

---

## Part 2 — System description as it actually exists

### 1. Overall architecture

```
Browser (chat widget, templates/partials/chat_widget.html)
   │  POST /api/v1/chatbot/messages/  {"message": "..."}
   ▼
apps/chatbot/api.py:ChatMessageView.post()  [VERIFIED apps/chatbot/api.py:17-28]
   │  validates via ChatMessageRequestSerializer (message, max_length=2000)
   ▼
apps/chatbot/services.py:get_bot_reply()  [VERIFIED apps/chatbot/services.py:20-26]
   │  try: rag.get_rag_reply(message)
   │  except Exception: return random canned reply, sources=[]   <-- silent, unlogged
   ▼
apps/chatbot/rag.py:get_rag_reply()  [VERIFIED apps/chatbot/rag.py:118-140]
   ├─ embed_query()        → lazy-loaded SentenceTransformer('all-MiniLM-L6-v2'), module-level singleton
   ├─ classify_query()     → routes to a source_table or None
   ├─ retrieve_chunks()    → connections['gold'].cursor(), pgvector `<->` ORDER BY, optional WHERE source_table
   ├─ build_prompt()       → string concatenation, no token budgeting
   └─ requests.post(OLLAMA_URL + '/api/chat', ...)  → llama3.2:3b, stream=False, timeout=60s
   ▼
JSON {"reply": ..., "sources": [...]} back to browser
```

Gold layer ingestion (offline, not part of the request path):
```
S3 gold/ (Hive-partitioned parquet) → scripts/load_gold_to_postgres.py (pyarrow.dataset, truncate+reload)
→ Postgres 'gold' DB, 5 tables (artist/country/label_performance, dashboard_summary, monthly_trends)
→ scripts/build_gold_chunks.py (pandas groupby yearly rollup → text template → MiniLM embed → gold_chunks)
```

### 2. Current RAG pipeline
Confirmed exactly as described in the prompt: `embed_query → classify_query → pgvector search → build_prompt → Ollama`. [VERIFIED, full call graph traced above with file:line references.]

### 3. Retrieval pipeline
`retrieve_chunks(query_embedding, source_table=None, top_k=5)` [VERIFIED `apps/chatbot/rag.py:73-101`] runs one of two parameterized SQL statements against `connections['gold']`, both using pgvector's `<->` (L2 distance) operator, `ORDER BY ... LIMIT %s`. No `WHERE embedding IS NOT NULL` guard needed — verified 0 NULL embeddings exist (see §9). `classify_query()` [VERIFIED `apps/chatbot/rag.py:52-70`] checks real country names (from a DB-backed, module-level cache, `_get_country_names()`) before falling back to a 6-keyword dict for `country_performance`/`label_performance`; anything else returns `None` (unfiltered search across all 215,725 rows).

### 4. Chunk generation
`scripts/build_gold_chunks.py` aggregates monthly gold rows to yearly grain via `pandas.groupby(...).agg(...)` with per-column semantics that are actually correct and documented inline: `sum` for additive volume (`total_streams`), `max` for catalog-size/reach metrics, `mean` for rate/percentage metrics, `last` for "current" descriptive fields (`top_artist`, `top_label`). [VERIFIED `scripts/build_gold_chunks.py:37-82`, comments confirm the reasoning is deliberate, not accidental.] One text chunk per `(entity, year)`, key = `f"{entity}|{year}"`.

### 5. Chunk quality
Measured on a random sample of 500 chunks (all three tables) using the *actual* SentenceTransformer tokenizer with the model's configured `max_seq_length=256`:
```
n=500  min=50  p50=74  p95=79  max=87 tokens
count > 256 tokens: 0
count > 128 tokens: 0
```
[VERIFIED — probe run against live `gold_chunks` data, tokenized with `SentenceTransformer('all-MiniLM-L6-v2').tokenizer`.] **This refutes the suspected truncation issue** — chunks are nowhere near the 256-token limit; max observed is 87 tokens. Character-length stats confirm the same (per source_table, `min/p50/p95/max` chars): artist 184/227/237/310, country 239/259/280/322, label 151/167/191/286. [VERIFIED via `psql` `percentile_cont`.]

However, `country_chunk()` [VERIFIED `scripts/build_gold_chunks.py:98-108`] omits two fields that `aggregate_country_performance()` explicitly computes one function earlier: `active_songs` (line 60, `max`) and `catalog_hit_rate` (line 59, `mean`) are aggregated into the `yearly` DataFrame but never referenced in the f-string at lines 100-107. Confirmed empirically: asking "What is the active_songs count for Brazil?" retrieves 5 correct Brazil chunks (`country_performance:Brazil|2024`, etc.) but Ollama correctly reports "The context does not provide information about the 'active_songs' count for Brazil" [VERIFIED, live probe] — the data exists in Postgres (`country_performance.active_songs`) but is structurally unreachable through the chatbot.

### 6. Prompt quality
`SYSTEM_PROMPT` [VERIFIED `apps/chatbot/rag.py:104-110`]: instructs the model to answer "using ONLY the context provided," to say so if the context lacks the answer, and to cite artist/country/label + time period. `build_prompt()` [VERIFIED `apps/chatbot/rag.py:113-115`] joins chunks with `- ` bullet prefixes under a `Context:` header, no chunk IDs, no explicit `[source: X, year: Y]` delimiter beyond what's already inside the prose text (which does include entity name and year inline, e.g. "In 2024, artist Kendrick Lamar ... "). Measured real prompt for a 5-chunk context: 957–1356 characters, `prompt_eval_count` (Ollama-reported input tokens) = 495 for one sample question. [VERIFIED, live Ollama response JSON.] Grounding instruction **is present but not reliably obeyed**: see Part 6, Q2, where Ollama fabricated a "33 billion streams (Source: Billboard, October 2022)" citation for Drake despite Drake never appearing in the retrieved context.

### 7. Embedding quality
`all-MiniLM-L6-v2`, 384-dim, loaded lazily once per process into a module-level global `_model` [VERIFIED `apps/chatbot/rag.py:16-24`]. Confirmed empirically: first `embed_query()` call in a fresh process took 9.972s (model load + encode); every subsequent call in the same process took 0.010–0.066s. [VERIFIED, live timing.] Stored vectors are L2-normalized: sampled 10 vectors via `pg_vector`'s `vector_norm()`, all in range 0.99999995–1.00000012. [VERIFIED via `psql`.] Because vectors are normalized, `<->` (L2 distance) and cosine distance produce **identical rankings** (L2² = 2 − 2·cos_sim for unit vectors) — so the choice of `<->` over `<=>` is not a correctness bug here, contrary to the audit brief's suspicion.

### 8. Metadata quality
`gold_chunks` schema [VERIFIED `schema.sql:97-108`]: `chunk_id, source_table, source_key, chunk_text, embedding, created_at`. No entity-type-specific columns (no `year` column, no `entity_name` column separate from the composite `source_key` string) — filtering or reranking by year would require string-parsing `source_key` (`"India|2022"`) rather than a real column. `source_key` is not consistently formatted across tables: artist keys are URIs (`spotify:artist:...|2024`), country/label keys are free-text names, one of which is the literal string `"NaN"` (see §9).

### 9. Gold layer data quality
Per-table row counts and NULL rates on key columns [VERIFIED, live `psql`]:
| table | rows | NULLs found |
|---|---|---|
| artist_performance | 652,373 | 0 null total_streams/artist_name; **8 rows with `artist_uri = 'NaN'`** (literal string, not SQL NULL) |
| country_performance | 7,496 | 0 null total_streams/market_share |
| label_performance | 286,055 | 0 null total_streams |

No zero/negative `total_streams` sentinels found in `artist_performance`. No duplicate `(artist_uri, year_month)` primary-key collisions found (the `ON CONFLICT DO NOTHING` upsert logic in `load_gold_to_postgres.py:82,94` is doing its job). Year range spans **2017–2026** in all three tables [VERIFIED] — data includes rows dated in the future relative to the audit date (2026-08-01 is "today," and monthly `year_month` granularity means some 2026 rows are plausible current-year data, but the presence of full-year 2026 country/artist rows for a still-in-progress year, and specifically artist chunk `...|2026` appearing in results, suggests either synthetic/simulated data generation or a dataset that runs ahead of real time — **cause not determinable from this repo alone; flagged as [UNVERIFIED] root cause, [VERIFIED] as an observed fact**).

**Label fragmentation, quantified**: `SELECT count(DISTINCT standardized_label) FROM label_performance` → **27,381** distinct values [VERIFIED]. This is far worse than the "365+" figure previously suspected. Sample confirming the same real label fragmented under different variants (queried `ILIKE '%columbia%'`, 20 of many rows shown): `Columbia`, `Columbia Local`, `Columbia Nashville`, `Columbia Nashville Legacy`, `Columbia Nashville/columbia Records`, `Columbia Records/duars Entertainment/sony Music Latin`, `Columbia/1019 Records`, `Columbia/andere Liga`, `Columbia/b1 Recordings`, etc. Same pattern confirmed for `%republic%`. [VERIFIED, live `psql`.] A conservative estimate: standard major-label rosters (Columbia, Republic, Atlantic, Interscope, Warner, Sony, Universal, etc.) likely number in the low hundreds of real entities; 27,381 distinct strings implies **well over 99% of distinct label strings are fragmentation artifacts** (collaboration credits, distributor tags, regional suffixes), not new labels. This makes `label_performance`-derived answers (e.g., "which label has the highest total streams") **structurally unreliable** regardless of retrieval or prompt quality — it's a data problem, not a RAG problem.

**Country coverage**: 73 distinct countries in `country_performance` [VERIFIED]. `_get_country_names()` has no `LIMIT` clause, so it pulls all 73 — not truncated. [VERIFIED `apps/chatbot/rag.py:39-49`.] For a country genuinely absent from the data (tested: "Georgia" — `SELECT DISTINCT country_name ... WHERE country_name ILIKE '%georgia%'` returned 0 rows [VERIFIED]), `classify_query()` correctly falls through to `None` and the query searches unfiltered — which then returns an *artist* named Georgia and unrelated labels, producing a misleading answer about the wrong entity type (see Part 3, §15 and Part 6).

### 10. PostgreSQL schema
6 tables total: the 5 gold marts + `gold_chunks`. [VERIFIED `schema.sql`.] All gold tables are `managed=False` Django models (`apps/gold_data/models.py:1-93`), correctly reflecting that `schema.sql` + the loader script own the schema, not Django migrations. Primary keys are composite (`entity, year_month`) for the three partitioned tables — correct design for the truncate-and-reload ETL pattern.

### 11. pgvector usage
Extension enabled (`CREATE EXTENSION IF NOT EXISTS vector`). Column type `vector(384)`, matching MiniLM's real output dimension. **The `ivfflat` index is commented out in `schema.sql:106-108`** with a comment noting it should be built "only after chunks are loaded, on the real row count" — and it never was. Confirmed live: `pg_indexes` for `gold_chunks` shows only `gold_chunks_pkey` (btree on `chunk_id`) and `idx_gold_chunks_source` (btree on `source_table, source_key`). **No vector index exists.** [VERIFIED, live `psql`.]

### 12. Django integration
- `ChatMessageView` (DRF `APIView`) has no `authentication_classes`/`permission_classes` override and no explicit throttle class anywhere in the codebase (`grep` for `throttle`/`RATE_LIMIT` across settings and app code returned nothing) [VERIFIED]. `REST_FRAMEWORK` in `config/settings/base.py:128-132` sets only schema, pagination, page size — no `DEFAULT_PERMISSION_CLASSES` or `DEFAULT_THROTTLE_CLASSES`, so DRF's own defaults apply (`AllowAny`, no throttling). The endpoint is open to anyone who can reach it, unauthenticated, unrate-limited, each request triggering a real (blocking) Ollama call.
- `message` field is capped at 2000 chars server-side (`ChatMessageRequestSerializer`, `apps/chatbot/serializers.py:6`) — a real, if generous, input-length cap.
- No `csrf_exempt` found anywhere in `apps/chatbot/` [VERIFIED via grep] — standard DRF CSRF handling applies for session-authenticated requests; since the endpoint doesn't require auth this is largely moot for anonymous use.
- SQL construction in `rag.py` uses parameterized queries exclusively (`%s` placeholders passed as a list to `cur.execute`) — no f-string/`%`-interpolation of user input into SQL found anywhere in `retrieve_chunks()` or `_get_country_names()`. [VERIFIED, no injection risk in the RAG path.] `load_gold_to_postgres.py` does f-string-interpolate `table_name`/`col_list` into SQL (`scripts/load_gold_to_postgres.py:81,94`), but those come from a hardcoded `TABLES` list, not user input — not exploitable.
- `CONN_MAX_AGE` is not set anywhere in `config/settings/base.py` — defaults to 0 (a new DB connection per request, standard unpooled Django dev behavior; fine for a dev server, a real cost at any production concurrency).
- `apps/gold_data/services.py:get_kpis()` pulls only the latest 2 rows via `.order_by('-year','-month')[:2]` — correctly bounded, not pulling the 652K-row `artist_performance` table into memory. `_top_countries_chart()` uses `.annotate(Sum(...))` — real SQL-side aggregation via the ORM, not Python-side. `_streams_over_time_chart()` pulls all of `monthly_trends` (113 rows total, confirmed small) unfiltered — fine at this size. **No query in `gold_data/services.py` pulls a large table into Python memory.** [VERIFIED, code inspection.]
- `.env` exists alongside `.env.example`; contents confirmed to contain `DJANGO_SECRET_KEY`, `GOLD_DATABASE_URL`, etc. — values redacted per audit constraints, not printed. `DJANGO_SECRET_KEY` has an insecure hardcoded fallback in `base.py:23` (`default='django-insecure-change-me-in-env-file'`) that only matters if `.env` is missing — `prod.py` does not override or forbid this fallback, meaning a misconfigured production deploy without `DJANGO_SECRET_KEY` set would silently run with the well-known insecure default rather than failing loudly, unlike `ALLOWED_HOSTS` in the same file which has no default and *would* raise. [VERIFIED, inconsistency in `prod.py`'s stated "fail loud" philosophy.]

---

## Part 3 — Failure analysis

**13. Performance bottlenecks** — Symptom: unfiltered retrieval queries take 350–374ms; filtered queries against the large `artist_performance` (151,264 chunks) and `label_performance` (63,789 chunks) partitions take 247–354ms; only the `country_performance` partition (672 chunks) is fast (3.8ms). Root cause: `idx_gold_chunks_source` is a plain btree on `(source_table, source_key)` — it accelerates the `WHERE source_table = ...` filter but the subsequent `ORDER BY embedding <-> ...` still requires a full sort of every matching row (Parallel Seq Scan + Sort confirmed in `EXPLAIN ANALYZE` output) because no ANN index exists on `embedding`. Consequence: every chatbot question pays ~250–375ms just for retrieval before the ~10s Ollama call even starts; at higher `gold_chunks` volume this scales linearly, unlike an ivfflat/hnsw index. Severity: **Medium** (masked today by Ollama's much larger 10s latency, but would dominate at scale or with a faster generator).

**14. Retrieval bottlenecks** — Symptom: "Compare Kendrick Lamar and Drake's streaming performance" retrieves 5 Kendrick Lamar chunks and **zero** Drake chunks. Root cause: `retrieve_chunks()` does a single `ORDER BY embedding <-> query_embedding LIMIT 5` with no per-entity diversity constraint — the query embedding sits closest to whichever single entity's chunks are semantically nearest overall, and because Kendrick Lamar chunks dominate the top-5 by similarity, Drake's chunks (further away, even if relevant) never surface. [VERIFIED, live retrieval probe.] Consequence: comparison questions between two named entities silently degrade into a single-entity answer, with the model then hallucinating the missing side from its own training data (see §15). Severity: **High** — comparison questions are a natural, expected chatbot use case for this dataset and currently fail outright.

**15. Hallucination risks** — Concrete example from the test set: Q: "Compare Kendrick Lamar and Drake's streaming performance." A (actual Ollama output): *"I can provide information on Drake's streaming performance, but it is not provided in the context you've given me. However... According to various reports from 2022, Drake had over 33 billion streams on Spotify (Source: Billboard, October 2022)... Another report from 2019 mentioned that Drake became the most-streamed artist on Spotify... 11.6 billion streams (Source: The New York Times, November 2019)."* [VERIFIED, live probe.] This is a direct violation of the system prompt's "use ONLY the context provided" instruction — Ollama fabricated specific numbers and specific fake citations not present anywhere in the retrieved context. Second example: Q: "Which country had the strongest streaming numbers?" A: *"Hungary had the strongest streaming numbers in 2023 with... 731,376,719 streams... However, if we consider a country that consistently performed strong in more recent years, Kazakhstan had the strongest streaming numbers as of 2025..."* — both claims are false relative to the *actual* full dataset (the retrieved 5 chunks were 5 random unrelated countries — Paraguay, Hungary, Uruguay, Kazakhstan, Bolivia — not the top-5 by streams; the real strongest country was never retrieved at all, so the model's confident answer is wrong by construction, not just poorly phrased). Severity: **Critical** — both are answers to natural questions an examiner would plausibly ask, and both produce a specific, confident, wrong number.

**16. Missing metadata** — `gold_chunks` has no `year` column (only embedded in `source_key` string), no entity-type discriminator beyond `source_table`, no numeric fields duplicated as filterable metadata (e.g., `total_streams` as a real column) that would enable metadata filtering (e.g., "top 5 countries by streams" via SQL rather than vector search) or reranking by recency. Severity: **Medium**.

**17. Missing features** — No SQL-aggregation fallback for `MAX`/`COUNT`/`GROUP BY`-shaped questions (confirmed: "How many labels are there in total?" retrieves 5 arbitrary label chunks and Ollama correctly refuses — *"I cannot determine the total number of labels"* — a correct refusal, but a missed opportunity since `SELECT COUNT(DISTINCT standardized_label)` is a one-line, cheap, 100%-accurate answer the system has all the data for). No conversation memory (each request is independent; not necessarily a defect, see Part 4 Q10). No retrieval confidence gating (see Part 1). No entity-diversity retrieval for multi-entity questions. Severity: **High** for the SQL fallback (directly explains 3 of the 6 stress-test failures in Part 6), **Low** for conversation memory.

**18. Weak engineering decisions** — `apps/chatbot/services.py:23-26` catches bare `Exception` and returns a random canned reply with **no logging of what failed** — this is the exact mechanism that caused the earlier real production incident this session (Postgres down → silent canned replies with no visible error, discovered only through manual testing). It will recur identically for any future failure (Ollama down, DB down, model load failure, network timeout) with zero operational visibility. Severity: **High** — this already caused a real debugging session; it's not hypothetical.

**19. Bad design choices** — Building the `ivfflat` index was correctly deferred in a code comment ("build only after chunks are loaded, on the real row count") but the follow-up never happened even though chunks have been loaded (215,725 rows, a very reasonable size to build the index at) since at least this session's earlier work. Severity: **Medium**.

**20. Scalability issues** — At current 215,725 chunks, unfiltered seq scan already costs ~350ms. `country_performance`'s 672-chunk corpus works because the source-table filter shrinks the working set enough that even a seq scan is cheap (3.8ms) — but `artist_performance` (151K) and `label_performance` (64K) chunks would benefit immediately from an ANN index, and any further data growth (e.g., extending yearly chunks to a monthly grain, see Part 4 Q5) would make the lack of an index materially worse. Severity: **Medium** at current scale, **High** if grain changes to monthly.

**21. Maintainability issues** — `country_chunk()`/`aggregate_country_performance()` already drifted out of sync once (2 computed fields never wired into the text template) — nothing in the codebase (no test, no schema check) would catch this class of bug if it recurs for `artist_chunk()` or `label_chunk()`. No tests exist for `rag.py`, `classify_query()`, or `retrieve_chunks()` (`apps/chatbot/tests.py` — not inspected in depth here, but no test invocations of these functions were found in the retrieval probes' absence of any pre-existing coverage). Severity: **Medium**.

**22. Production readiness gaps** — No logging/observability on the RAG path (confirmed: exceptions vanish silently). No rate limiting or auth on a chat endpoint that triggers an expensive (10s) LLM call per request — a trivial DoS vector if exposed publicly. No health-check endpoint that would let ops know Postgres/Ollama are reachable before end users find out via the canned-reply symptom. No connection pooling (`CONN_MAX_AGE` unset). Model reload cost (10s cold-start) would recur per Gunicorn worker process in any multi-worker prod deployment, meaning the first request to each worker pays a 10s tax. Severity: **High**, but expected and appropriate to flag as a gap analysis since deployment genuinely hasn't happened yet (not a regression from a working state).

---

## Part 4 — Direct answers to architectural questions

**1. Is the Gold layer itself sufficient to support a good chatbot?** No, not for the full range of questions an examiner would ask, though it is sufficient for single-entity, single-year factual lookups. It can never reliably answer superlative questions ("highest," "strongest," "most") because RAG returns semantically-nearest chunks, not aggregated extremes — verified directly (§15). It can never reliably answer label-specific questions because `standardized_label` has 27,381 fragmented values for what is likely a few hundred real labels (§9). It can never answer `active_songs`/`catalog_hit_rate` questions for countries because that data, while present in Postgres, never made it into the chunk text (§5).

**2. What important information is missing from Gold?** Not columns so much as **grain and joins**: there is no track-level or release-level data anywhere in Gold (confirmed by `schema.sql`'s own header comment), so nothing at the level of "which song" can ever be answered — only artist/country/label rollups. There's no genre dimension, no explicit artist↔label join table (label association is inferred only through `label_performance`'s own artist counts, not a queryable artist→label mapping), and no true entity-standardization/dedup table for labels.

**3. Which metrics should exist for an analytics chatbot but don't?** A real numeric ranking table (precomputed `MAX`/`TOP-N` per year, refreshed alongside the yearly chunk build) would directly fix the superlative-question gap without touching retrieval at all. A canonical label-name mapping table (`raw_label → canonical_label`) would fix label fragmentation without needing upstream data cleaning to be perfect.

**4. Are yearly chunks the right grain? Justify with retrieval evidence, not theory.** Yes, for the current dataset size and use case. Chunk char lengths (151–322 chars) and token counts (50–87 tokens, max observed) are well under the 256-token embedding limit at yearly grain — there's headroom to spare, not a constraint being hit. The single-entity retrieval tests (Kendrick Lamar, India, Brazil) worked correctly and precisely at yearly grain — every year for the named entity came back in the top-5 for a 5-year query. The grain isn't what's causing the observed failures; routing, entity-diversity, and superlative-aggregation are.

**5. Would monthly chunks perform better? Quantify the trade-off.** Chunk count would grow roughly 12x: `artist_performance` alone has 652,373 monthly rows vs. 151,264 yearly chunks (~4.3x, not the full 12x, because not every artist has 12 months of data every year) — extrapolating similarly across all three tables, `gold_chunks` would go from ~215,725 to roughly **900K–1M+ rows**. At that size, the current no-index seq-scan approach (already 350ms unfiltered, 250–354ms filtered-on-large-tables) would become untenable — likely 3–5x slower without an ANN index, and would make building the ivfflat/hnsw index (currently a "nice to have," see §19) a hard requirement, not optional. Monthly grain would also multiply the label-fragmentation problem's blast radius without fixing it. Not recommended before the label and superlative issues are addressed — it would make more of the same failure modes, faster.

**6. Should chunk text be rewritten? If yes, show a before/after of one real chunk.** Yes, specifically to close the `country_chunk()` gap. Before (pulled live from `gold_chunks`, `India|2022`):
> "In India during 2022, Spotify recorded 11,229,209,910 total streams (avg 3.62% market share, avg 6.33% growth). Up to 364 active artists and 107 active labels were represented that year. Most recent top artist was [X], top label was [Y]."

After (adding the two omitted aggregated fields per `scripts/build_gold_chunks.py:98-108`):
> "In India during 2022, Spotify recorded 11,229,209,910 total streams (avg 3.62% market share, avg 6.33% growth), with up to [active_songs] active songs and an average catalog hit rate of [catalog_hit_rate]%. Up to 364 active artists and 107 active labels were represented that year. Most recent top artist was [X], top label was [Y]."

**7. Should metadata be richer? Which exact columns, and what would each unlock?** Yes. Add a real `year` integer column (parsed once at chunk-build time instead of embedded in `source_key` string) — unlocks SQL-side year filtering/range queries without string parsing. Add a `total_streams` numeric column duplicated from the source row — unlocks pure-SQL `ORDER BY total_streams DESC LIMIT N` for superlative questions, which is the single highest-leverage metadata addition given §15's evidence. Add an `entity_name` column split out from `source_key` — unlocks exact-match lookups and would let `classify_query()`-style routing extend to artist names too (currently only country/label are routed by name). None of these unlock reranking specifically (that needs a cross-encoder, a separate concern, see Q9).

**8. Is hybrid retrieval (BM25/tsvector + vector) needed here?** **Yes.** Entity names in this dataset are proper nouns (artist names, label names) that MiniLM — a general-purpose semantic model — handles poorly for exact lexical matching, which is directly why "How is the label Jordan performing?" (a label that doesn't exist, confirmed via `SELECT ... WHERE standardized_label='Jordan'` returning 0 rows) returned `Jordan Rys`, `Jordan Sandhu`, `Ynw Jordan`, `Jordan Massey` — plausible-looking but wrong entities, purely on semantic/lexical proximity, with no way to distinguish "close match" from "no match." A `tsvector` exact/fuzzy text index on `source_key`/entity name, checked before falling back to vector search, would directly fix this class of failure and is cheap to add (a GIN index on existing Postgres, no new infrastructure).

**9. Is reranking worth it at this scale and with this generator?** No, not as a priority. At top-k=5 with no ANN index yet and 3.2B-parameter Ollama as the generator, the marginal quality gain from a cross-encoder reranker is smaller than the gain from fixing entity-diversity retrieval (§14) and adding SQL fallback (§17) — both cheaper and address concretely observed failures. Revisit only after those are done.

**10. Is conversation memory necessary for the demo use case?** No. Every stress-test question in this audit was self-contained; a CDAC academic demo is Q&A-style, not a multi-turn dialogue exploration. Adding memory would add complexity (session state, context-window budgeting across turns) without addressing any of the 6 concrete failures found in Part 6.

**11. Should analytical/superlative questions bypass the vector store entirely and run SQL? Describe the routing boundary.** Yes — this is the highest-leverage architectural fix available. Routing boundary: detect superlative/aggregate keywords ("highest", "lowest", "most", "least", "strongest", "weakest", "top", "how many", "total number of", "average across") via the same keyword-matching style already used in `classify_query()` (`apps/chatbot/rag.py:31-34`), and for a match, run a parameterized `SELECT ... ORDER BY <metric> DESC LIMIT N` (for "which X is highest") or `SELECT COUNT(DISTINCT ...)` (for "how many") directly against the gold tables via `connections['gold']`, formatting the numeric result into the prompt context (or returning it directly, bypassing Ollama for pure-count questions). Detection reliability: keyword-based detection will have false negatives (a superlative phrased without a trigger word) but that degrades gracefully to today's behavior (vector search, wrong-but-plausible-sounding answer) rather than breaking anything — a strict improvement, not a regression risk.

**12. Which improvements will produce measurable quality gains, and how would I measure each?**
- SQL fallback for superlatives: measure retrieval hit-rate on a labeled superlative-question subset (target: 0/N → N/N exact-match against ground truth `SELECT MAX(...)`).
- `country_chunk()` field fix: measure whether `active_songs`/`catalog_hit_rate` questions get a grounded (non-refusal) answer, before/after — binary pass/fail per question.
- ivfflat/hnsw index: measure `EXPLAIN ANALYZE` execution time before/after on the same 3 seed queries used in this audit (currently 350ms/354ms/247ms unfiltered/artist/label).
- Entity-diversity retrieval fix: measure whether both named entities in a 2-entity comparison question appear in the retrieved chunk set (currently 0/1 confirmed failing).

**13. Which popular ideas would be overengineering for this specific project?** FAISS (already correctly rejected earlier in this project's history — pgvector is already integrated and sufficient at this scale; adding FAISS would mean maintaining a second retrieval system for no measured benefit). A cross-encoder reranker (see Q9 — no evidence it's the bottleneck here). LangChain/LlamaIndex as a framework layer — the current hand-rolled `rag.py` is ~140 lines, already clear, already testable, and every failure found in this audit is a logic/data problem a framework wouldn't fix. Conversation memory (Q10). A larger/different LLM — none of the 6 stress-test failures are generation-model limitations; they are retrieval/data limitations that would reproduce identically on GPT-4 given the same broken context.

---

## Part 5 — Prioritized recommendations

### HIGH IMPACT

| | |
|---|---|
| **What** | Build an ivfflat (or hnsw) index on `gold_chunks.embedding` |
| **Why it matters** | Currently every retrieval query is a full seq scan (§13, §19) — verified 350ms unfiltered, up to 354ms on the largest filtered partition |
| **Expected quality improvement** | Retrieval latency drops from ~250-374ms to low single-digit ms at this row count (standard ivfflat behavior); no answer-quality change, pure latency |
| **Complexity** | Low |
| **Estimated effort** | 1 hour |
| **Files touched** | `schema.sql` (uncomment lines 106-108), one `psql`/migration run |
| **Risk of breaking something working** | Low |
| **Worth it for a CDAC academic project?** | Yes — it's already written as a commented-out line, and "did you index your vector column" is a predictable examiner question |

| | |
|---|---|
| **What** | Fix `country_chunk()` to include `active_songs` and `catalog_hit_rate` |
| **Why it matters** | Data is computed but silently unreachable (§5) — directly confirmed with a failing live query |
| **Expected quality improvement** | `active_songs`/`catalog_hit_rate` country questions go from refusal (confirmed: "The context does not provide information...") to grounded answer |
| **Complexity** | Low |
| **Estimated effort** | 30 minutes code + ~1 the previously-measured re-embed cost for 672 country chunks (seconds, not the full 215K re-embed) — can re-run just `aggregate_country_performance`+`country_chunk` and re-insert only that partition |
| **Files touched** | `scripts/build_gold_chunks.py` (lines 98-108) |
| **Risk of breaking something working** | Low |
| **Worth it for a CDAC academic project?** | Yes — cheap, and closes a gap this exact audit process would surface if an examiner asks about it |

| | |
|---|---|
| **What** | Log exceptions in `apps/chatbot/services.py:get_bot_reply()` instead of silently swallowing them |
| **Why it matters** | This exact silent-failure mode already caused a real debugging session earlier in this project (Postgres down → canned replies, no error surfaced) |
| **Expected quality improvement** | Not an answer-quality fix — an operability fix; time-to-diagnose a future outage drops from "manual trial and error" to "check the log" |
| **Complexity** | Low |
| **Estimated effort** | 30 minutes |
| **Files touched** | `apps/chatbot/services.py` |
| **Risk of breaking something working** | Low |
| **Worth it for a CDAC academic project?** | Yes — trivial and directly prevents a repeat of a real incident |

| | |
|---|---|
| **What** | Add a SQL-aggregation fallback for superlative/count-shaped questions (Part 4, Q11) |
| **Why it matters** | This is the only fix that addresses the Part 1 headline finding — superlative questions currently produce confident, wrong answers (§15) |
| **Expected quality improvement** | Superlative-question hit-rate goes from ~0/4 (all 4 tested superlative-style questions in this audit produced wrong or unfounded answers) to should-be 4/4 for keyword-detected cases |
| **Complexity** | Medium |
| **Estimated effort** | 4-6 hours (keyword detection + 2-3 parameterized SQL templates + prompt formatting for the numeric result) |
| **Files touched** | `apps/chatbot/rag.py` |
| **Risk of breaking something working** | Low (additive — falls through to existing behavior on no match) |
| **Worth it for a CDAC academic project?** | Yes — this is the fix most likely to be visibly tested by an examiner given how natural "which X is highest" questions are |

### MEDIUM IMPACT

| | |
|---|---|
| **What** | Retrieve more chunks (e.g., top-15) and let the prompt include multiple distinct entities, or run 2 separate retrieval passes when 2 named entities are detected in the question |
| **Why it matters** | Fixes the Kendrick-vs-Drake failure (§14) — confirmed 0/5 retrieved chunks were Drake |
| **Expected quality improvement** | Comparison-question hit-rate: currently fails to retrieve the second entity in the 1 tested case; fix should retrieve both entities' chunks reliably |
| **Complexity** | Medium |
| **Estimated effort** | 3-4 hours |
| **Files touched** | `apps/chatbot/rag.py` |
| **Risk of breaking something working** | Low |
| **Worth it for a CDAC academic project?** | Only if time permits — real fix but lower frequency of examiner questions than superlatives |

| | |
|---|---|
| **What** | Add a canonical label-name mapping (raw → cleaned) for the top N most-fragmented real labels |
| **Why it matters** | 27,381 distinct `standardized_label` values for what's likely a few hundred real labels (§9) — makes any label-specific question unreliable |
| **Expected quality improvement** | Label questions for major labels (Columbia, Republic, Sony, Universal, Warner, Atlantic) go from "confused, hedged answer across fragments" (confirmed: Columbia Records query) to a single consolidated answer |
| **Complexity** | Medium-High (requires actual data cleaning, likely fuzzy-matching or manual rules for the long tail) |
| **Estimated effort** | 1-2 days for the top 20-30 labels by volume; full cleanup is out of scope |
| **Files touched** | New mapping table/script, `scripts/build_gold_chunks.py` (join before chunking) |
| **Risk of breaking something working** | Medium (touches the label chunk-build pipeline; needs a full re-embed of `label_performance`'s 63,789 chunks) |
| **Worth it for a CDAC academic project?** | Only if time permits — good honest talking point either way (the data quality issue itself, explained candidly, is a legitimate finding to present) |

| | |
|---|---|
| **What** | Add a `tsvector`/GIN exact-text index for entity-name lookup, checked before vector search |
| **Why it matters** | Fixes the "Jordan" (nonexistent label) misroute and the general proper-noun weakness of MiniLM (Part 4 Q8) |
| **Expected quality improvement** | Adversarial entity-name misroutes (confirmed: nonexistent "Jordan" label returned 5 plausible-but-wrong label chunks) drop to a clean "not found" response |
| **Complexity** | Medium |
| **Estimated effort** | 4-6 hours |
| **Files touched** | `schema.sql`, `apps/chatbot/rag.py` |
| **Risk of breaking something working** | Low |
| **Worth it for a CDAC academic project?** | Only if time permits |

### LOW IMPACT

| | |
|---|---|
| **What** | Add basic rate limiting / DRF throttle class on `ChatMessageView` |
| **Why it matters** | Endpoint currently has no auth or throttling (§12) — each request costs a real ~10s Ollama call |
| **Expected quality improvement** | Not a quality fix — an abuse-prevention fix, relevant only once actually deployed publicly |
| **Complexity** | Low |
| **Estimated effort** | 1 hour |
| **Files touched** | `config/settings/base.py` or `apps/chatbot/api.py` |
| **Risk of breaking something working** | Low |
| **Worth it for a CDAC academic project?** | Only if time permits — matters for deployment, not for the demo itself |

| | |
|---|---|
| **What** | Investigate and confirm the cause of 2026-dated rows in the gold data |
| **Why it matters** | Currently unexplained (§9) — could be intentional synthetic data or a real upstream bug worth understanding before presenting |
| **Expected quality improvement** | Not a quality fix — a credibility fix if an examiner notices "2026" data and asks about it |
| **Complexity** | Low (investigation only) |
| **Estimated effort** | 1-2 hours |
| **Files touched** | None (investigation) |
| **Risk of breaking something working** | None |
| **Worth it for a CDAC academic project?** | Yes — cheap and prevents an awkward "we don't know" in front of the mentor |

### DO NOT DO

- **FAISS** — already correctly evaluated and rejected earlier in this project. pgvector handles 215K vectors fine; nothing in this audit found a limitation FAISS would fix. Adding it would be pure duplicated infrastructure.
- **LangChain / LlamaIndex** — the current ~140-line hand-rolled `rag.py` is simple, traceable, and every bug found in this audit is a logic bug a framework layer wouldn't have prevented (routing gaps, missing fields, no SQL fallback). Adopting a framework now would add abstraction without fixing anything.
- **Cross-encoder reranking** — no evidence in this audit that ranking-within-top-5 is the problem; the problems are entity-diversity, superlative-blindness, and data fragmentation, none of which reranking touches.
- **Switching the embedding model** (e.g., to a larger model) — MiniLM's proper-noun weakness (Q8) is real but a `tsvector` exact-match layer fixes it far more cheaply and reliably than a bigger embedding model would.
- **Switching the LLM** (bigger Ollama model, or a hosted API) — every hallucination/wrong-answer example in this audit stems from bad or incomplete retrieved context, not generator weakness; a stronger model would confidently hallucinate the same wrong answers just as fluently.
- **Conversation memory / multi-turn state** — not needed for a Q&A-style demo (Q10); adds complexity with no observed failure it would fix.
- **Monthly-grain chunks** — would 4-12x the row count and make the missing-index problem far worse before any of the current failures are fixed (Q5); do this only after the index and SQL-fallback work is done, if at all.

---

## Part 6 — Demo-day stress test

10 questions an examiner could plausibly ask, with actual observed bad output where the retrieval path was run live:

1. **"Which country had the strongest streaming numbers?"** → Actual answer: *"Hungary had the strongest streaming numbers in 2023 with... 731,376,719 streams... Kazakhstan had the strongest streaming numbers as of 2025..."* — confidently wrong; retrieved 5 arbitrary countries (Paraguay, Hungary, Uruguay, Kazakhstan, Bolivia), none verified as the actual top country by streams. **[Observed live.]**
2. **"Compare Kendrick Lamar and Drake's streaming performance."** → Actual answer: fabricated external stats for Drake ("33 billion streams... Source: Billboard, October 2022") not present in any retrieved context — a direct hallucination despite the "use ONLY the context" instruction. **[Observed live.]**
3. **"Which artist grew the most in 2023?"** → Actual answer: picked "Michelangelo" (an arbitrary retrieved artist, not verified as the actual top grower) and made an arithmetic error computing growth ("roughly 3645 times" — the numbers shown imply ~1930x, not 3645x). **[Observed live.]**
4. **"Tell me about Columbia Records."** → Actual answer: hedges across "Columbia/1019 Records" and "Columbia Nashville/columbia Records" as if they might be different entities, unable to give one clean answer — a direct symptom of the 27,381-value label fragmentation. **[Observed live.]**
5. **"How many labels are there in total?"** → Actual answer: correctly refuses ("I cannot determine the total number of labels") — not wrong, but reveals the system cannot answer a trivial `COUNT(DISTINCT ...)` question it has full data for. **[Observed live.]**
6. **"What is the active_songs count for Brazil?"** → Actual answer: correctly refuses, confirming the `country_chunk()` field-omission bug (§5) even though the data exists in Postgres. **[Observed live.]**
7. **"How is Georgia doing?"** (a country that doesn't exist in this dataset — 0 rows for `%georgia%`) → routed to `None` (unfiltered search), retrieved a mix of an *artist* named Georgia and unrelated labels ("Georgia Box", "Tbilisi Records") — a confusing, entity-type-mismatched answer rather than a clean "no data for that country." **[Observed live.]**
8. **"How is the label Jordan performing?"** (a label that doesn't exist — 0 rows for exact match) → retrieved 5 plausible-but-wrong label chunks ("Jordan Rys", "Jordan Sandhu", "Ynw Jordan", "Jordan Massey") with no signal to the model that none of these is actually "Jordan." **[Observed live.]**
9. **"Which label had the highest total streams?"** — same class of failure as #1/#5; would retrieve 5 arbitrary label chunks and either produce a confidently wrong answer or a correct-but-unhelpful refusal, not the real answer.
10. **"Why does your data include 2026?"** — an unplanned but plausible examiner question given the confirmed 2017-2026 year range; there is currently no prepared explanation (§9, root cause unverified from this repo).

**Minimum set of fixes that would survive this demo**: (a) the SQL-aggregation fallback for superlative/count questions (fixes #1, #3, #5, #9 directly), (b) the entity-diversity retrieval fix (fixes #2), (c) the `country_chunk()` field fix (fixes #6), (d) being ready to candidly explain the label-fragmentation issue as a known upstream data-quality finding rather than hiding it (turns #4 into a strength — "we found and quantified this" — rather than a weakness), (e) a one-line explanation prepared in advance for #10.

---

## Part 7 — Evaluation harness proposal (design only)

A ~30-question gold-standard set, organized by category with the expected answer source:

| Category | Example question | Expected answer source | Count |
|---|---|---|---|
| Single-entity factual (artist) | "How many total streams did [artist] have in [year]?" | Direct value from `artist_performance` yearly rollup | 5 |
| Single-entity factual (country) | "What was [country]'s market share in [year]?" | Direct value from `country_performance` | 3 |
| Single-entity factual (label, clean cases only) | "How many streams did [label with few variants] have in [year]?" | Direct value, using only labels with ≤3 known variants | 2 |
| Cross-year trend | "How did [artist]'s streams change from [year1] to [year2]?" | Two yearly values, computed delta | 3 |
| Comparison (2 entities) | "Compare [artist A] and [artist B] in [year]." | Both entities' yearly values present in retrieved context | 3 |
| Superlative/aggregate | "Which country had the highest total streams in [year]?" | `SELECT ... ORDER BY total_streams DESC LIMIT 1` ground truth | 4 |
| Count/aggregate | "How many countries are covered in the data?" | `SELECT COUNT(DISTINCT country_name)` ground truth | 2 |
| Country-specific with omitted-field probe | "What is the active_songs count for [country]?" | Directly tests the §5 gap; should flip from fail→pass after the fix | 2 |
| Label-specific (known-fragmented case) | "Tell me about Columbia Records." | No clean ground truth — used to measure whether the system explains ambiguity vs. hallucinating a single number | 2 |
| Out-of-scope | "What's the weather today?" | Should be a clean refusal, not a low-distance false-positive retrieval | 2 |
| Ambiguous/adversarial entity | "How is Georgia doing?" / "How is the label Jordan performing?" | Should be a clean "no data found," not a wrong-entity-type answer | 2 |

Three metrics worth tracking over time:
1. **Retrieval hit-rate**: for each question, does the top-5 retrieved context actually contain the fact(s) needed to answer correctly? (binary per question, aggregate as a percentage) — this is the metric measured informally in this audit (§ retrieval probes) and should be the primary regression-tracking number.
2. **Grounding fidelity**: for each answered question, does every number in Ollama's answer trace back to a number actually present in the retrieved context? (manual or regex-assisted check) — directly targets the hallucination risk found in §15.
3. **Refusal correctness**: for out-of-scope and truly-unanswerable questions, does the system refuse cleanly rather than confidently guessing? (binary) — currently passes for count questions (#5, #6 in Part 6) but fails for superlative questions (#1, #3), so this metric would show the asymmetry clearly.

---

## Part 8 — Assumptions, unknowns, and what could not be verified

- **Django dev server was down at audit time** and was not started (per audit constraints). All retrieval-path findings were instead gathered by invoking `apps.chatbot.rag` functions directly inside a Django-configured process, which is functionally identical to what the HTTP view does (`api.py` → `services.py` → `rag.py`, no other logic in between) — but the exact HTTP request/response cycle (middleware, DRF serialization) was not separately re-verified live.
- **Root cause of 2017-2026 year range, including apparently-future-dated 2026 rows**, could not be determined from this repository alone — flagged as [VERIFIED fact] / [UNVERIFIED cause] in §9. This would need to be answered by whoever generated or sourced the original Bronze/Silver data, which is outside this repo's scope.
- **`gold_chunks` metadata for entity-type discrimination beyond `source_table`** — could not check whether `source_key` parsing is used anywhere else in the codebase beyond what's shown in `rag.py`; only the files in scope for this audit were read in full.
- **Whether `apps/chatbot/tests.py` contains any RAG-specific tests** — file was located but not read in depth; noted the *absence* of any test invocation of `classify_query`/`retrieve_chunks` observed during this audit's probes, but did not exhaustively review test file contents against the audit's strict "no execution beyond reads" scope for non-DB commands (running the test suite was not attempted, since that could be considered outside pure inspection and risks side effects depending on test configuration).
- **Production Gunicorn/multi-worker behavior** for model-reload cost is inferred from the single-process, single-model-instance behavior observed in `rag.py`'s module-level `_model` global — actual multi-worker reload cost was not measured because no production deployment exists to measure.
- **`.env` file contents** were read only far enough to confirm which keys exist (`DJANGO_ENV`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `GOLD_DATABASE_URL`); values were never printed or inspected beyond existence, per the audit's redaction requirement.
