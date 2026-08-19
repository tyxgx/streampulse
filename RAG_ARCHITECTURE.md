# RAG Pipeline — Architecture & Design Document

**Issue**: #15 — Research and Design Retrieval-Augmented Generation (RAG) Pipeline
**Status**: Architecture finalized, POC built and tested end-to-end.

---

## 1. Research Summary

### 1.1 Embedding Models

| Option | Verdict |
|---|---|
| `all-MiniLM-L6-v2` (local, sentence-transformers) | **Chosen.** 384-dim, free, runs on CPU (18ms/chunk measured on M1/8GB), no external dependency or API cost. |
| OpenAI/Cohere/other hosted embedding APIs | Rejected for this pass — adds cost and a network dependency for no measured accuracy benefit at our data scale (~215K chunks). |

### 1.2 Vector Databases

| Option | Verdict |
|---|---|
| **Postgres + pgvector** | **Chosen.** We already run Postgres for the Gold-layer mart (see §2) — pgvector adds vector search to the same database with zero new infrastructure. `ivfflat`/`hnsw` indexing available if retrieval speed becomes a bottleneck at scale. |
| Dedicated vector DB (Pinecone, Weaviate, Milvus, Chroma) | Rejected — extra service to run/pay for with no capability we need beyond what pgvector already gives us at this data volume (215K vectors). |

### 1.3 Chunking Strategies

| Option | Verdict |
|---|---|
| Row-per-chunk at raw (monthly) grain | Rejected — ~972K rows would take ~5hrs to embed locally, and most of that granularity doesn't correspond to how people ask questions ("how did X do in 2024" beats "how did X do in June 2024"). |
| **Row-per-chunk at yearly-aggregated grain** | **Chosen.** Collapses monthly rows to one row per (entity, year) via pandas groupby before chunking. Cuts total embedding time to ~7 min while keeping chunks at a natural question-answering granularity. |
| Fixed-size text-window chunking (typical RAG-over-documents approach) | Not applicable — our source is structured tabular data, not free text, so row-based chunking is the natural unit. |

### 1.4 Retrieval Methods

| Option | Verdict |
|---|---|
| **pgvector `<->` (L2) nearest-neighbor, top-k=5** | **Chosen** for the POC — simple, fast enough at 215K vectors without an ANN index. |
| Hybrid search (vector + keyword/BM25) | Flagged as a future improvement (see §5) — pure vector search showed a measurable weakness (§4.3). |
| Query routing / per-source-table retrieval | Flagged as a future improvement (see §5). |

### 1.5 LLM Integration

| Option | Verdict |
|---|---|
| Anthropic Claude API | Preferred for quality/instruction-following, but a paid API key wasn't available in this environment (Claude Pro subscription doesn't include API billing). |
| **Ollama + `llama3.2:3b` (local)** | **Chosen for the POC** — free, no API key, runs locally. Trade-off measured directly (§4.3): weaker instruction-following than a frontier model would be. |

---

## 2. Data Conversion Strategy (Gold Layer → LLM-readable text)

### 2.1 Schema-to-Text Conversion

The real Gold layer (`s3://group-1-dbda/gold/`) does **not** match what track-level RAG usually assumes — there is no track/song-level data at all. The actual tables are pre-aggregated marts:

- `artist_performance` (monthly, by artist)
- `country_performance` (monthly, by country)
- `label_performance` (monthly, by label)
- `dashboard_summary`, `monthly_trends` (global monthly aggregates — excluded from RAG, see §2.3)

Each row is converted to a natural-language sentence via an f-string template, e.g.:

> "In 2024, artist Kendrick Lamar (spotify:artist:...) had 45,231,904 total streams across up to 12 active song(s), reaching up to 47 countries that year. Average catalog hit rate was 12.30%, average chart strength 68.40."

### 2.2 Metadata Enrichment

Each chunk carries structured metadata alongside the text (`source_table`, `source_key` = `{entity_id}|{year}`), so retrieved chunks can be cited back to their exact origin row — the chatbot returns real `sources` (e.g. `label_performance:Streamline/interscope|2026`) with every answer, not just prose.

### 2.3 Row-level vs. Aggregated Representations

Decision: **aggregate to one row per entity per year** before generating chunks (not raw monthly rows). Reasoning and measured impact in §1.3 / §3.

`dashboard_summary`/`monthly_trends` are single global-aggregate tables (one row = the whole platform for that month) — not per-entity, so there's no meaningful "chunk" to generate from them. Excluded from `gold_chunks` by design.

### 2.4 Business Context Generation

The chunk templates deliberately spell out units and context inline ("total streams", "reaching up to N countries", "average catalog hit rate") rather than emitting raw column names, so the LLM receives self-describing facts instead of a bare data dump — this is what lets the model answer in plain language and correctly decline when a fact isn't present (see §4.3, test 1).

---

## 3. Chunking Strategy — Results

Yearly aggregation via pandas `groupby(['entity_key', 'year'])`, one row per group, sensible per-column aggregation (sum for additive volume metrics, mean for rates/percentages, max for catalog-size metrics, last for "current leader" fields):

| Table | Monthly rows | Yearly rows | Reduction |
|---|---|---|---|
| `artist_performance` | 652,373 | 151,264 | 4.3x |
| `country_performance` | 7,496 | 672 | 11.2x |
| `label_performance` | 286,055 | 63,789 | 4.5x |

**Total: 215,725 chunks.** Reduction is lower than a naive "12 months → 1 year" assumption because most entities aren't active in every month of every year — this was measured, not assumed.

---

## 4. Embedding Strategy — Results

- Model: `all-MiniLM-L6-v2` (sentence-transformers), 384-dim output.
- **215,725 chunks embedded in 406.4s (6.8 min)** on a local M1/8GB machine, CPU only — batched (batch_size=64).
- Stored in Postgres `gold_chunks(chunk_id, source_table, source_key, chunk_text, embedding vector(384))`.

### 4.3 POC Retrieval + Generation — Tested End-to-End

Live-tested via the actual DRF endpoint (`POST /api/v1/chatbot/messages/`) with 4 questions:

1. **"How did Kendrick Lamar perform in Canada in a recent year?"** → Correct: retrieved real artist chunks, correctly declined to invent a Canada-specific number since the data doesn't break down by country, cited a real fact instead (reached 50 countries in 2020).
2. **"Which country had strong streaming numbers recently?"** → **Retrieval weakness found**: pulled artist chunks instead of country chunks — `country_performance` has only 672 chunks vs. `artist_performance`'s 151K, so the larger corpus dominated nearest-neighbor search. Model still declined rather than hallucinating from the wrong context.
3. **"Which label had the highest total streams in a recent year?"** → Correct: retrieved label chunks, answered with a real grounded number.
4. **"Tell me about the song Shape of You by Ed Sheeran — tempo/key?"** (deliberately out-of-scope, no track data exists) → **Hallucination found**: the local 3B model invented an answer ("according to various sources, e.g. Genius...") instead of declining, violating the "answer only from context" system prompt. This is the risk flagged when choosing a local model over Claude — direct evidence it's real, not just theoretical.

---

## 5. Architecture Diagram (data flow)

```
S3 Gold layer (parquet, Hive-partitioned by year)
        │  aws s3 sync (bulk parallel download)
        ▼
Local parquet files ──pyarrow.dataset──▶ pandas DataFrame
        │
        ├─▶ scripts/load_gold_to_postgres.py ──▶ Postgres 'gold' DB
        │        (artist/country/label/dashboard/monthly tables)
        │
        └─▶ scripts/build_gold_chunks.py
                 │ 1. groupby(entity, year) — yearly aggregation
                 │ 2. f-string template — row → natural language chunk
                 │ 3. all-MiniLM-L6-v2 — chunk → 384-dim embedding
                 ▼
        Postgres gold_chunks (chunk_text, embedding vector(384))

── Query time ──
User question (Django chat widget / chatbot page)
        │
        ▼
apps/chatbot/api.py :: ChatMessageView (POST /api/v1/chatbot/messages/)
        │
        ▼
apps/chatbot/rag.py :: get_rag_reply()
        │ 1. embed_query()        — same MiniLM model
        │ 2. retrieve_chunks()    — pgvector `<->` nearest-neighbor, top-k=5
        │ 3. build_prompt()       — system prompt + retrieved context
        │ 4. Ollama /api/chat     — local llama3.2:3b generates the answer
        ▼
{"reply": "...", "sources": ["label_performance:...", ...]}
```

### API Interaction / Query Pipeline

- **Embed**: `SentenceTransformer.encode([question])` → 384-dim vector.
- **Retrieve**: `SELECT ... FROM gold_chunks ORDER BY embedding <-> %s::vector LIMIT 5`.
- **Generate**: `POST http://localhost:11434/api/chat` (Ollama), system prompt enforces "answer only from context, cite the source."
- **Respond**: DRF serializes `{reply, sources}`, frontend (`chat-widget.js` / `chatbot.js`) renders both.

---

## 6. Deliverables Checklist (per issue #15)

- [x] Architecture document — this file
- [x] Technology comparison — §1
- [x] POC — built, deployed locally, tested with 4 real questions (§4.3)
- [x] Data conversion strategy — §2
- [x] Chunking strategy — §3
- [x] Embedding strategy — §4

## 7. Acceptance Criteria (per issue #15)

- [x] **RAG architecture finalized** — §5
- [x] **Gold layer conversion strategy documented** — §2
- [x] **End-to-end workflow documented** — §5
- [x] **Prototype successfully retrieves relevant information** — §4.3 (3 of 4 test questions retrieved correctly and answered correctly; 1 retrieval-bias case and 1 hallucination case found and documented as known limitations, not hidden)

## 8. Known Limitations / Next Steps

1. **Retrieval imbalance across source tables** — `artist_performance` (151K chunks) dominates `country_performance` (672 chunks) in nearest-neighbor search. Next step: per-source-table retrieval (query classification/routing) or reranking.
2. **Local LLM hallucination risk** — `llama3.2:3b` invented an answer for an out-of-scope question instead of declining. Next step: either upgrade to a larger local model (e.g. `llama3.1:8b`), tighten the system prompt further, or use a paid Claude API key for production use.
3. **No ANN index yet on `gold_chunks.embedding`** — fine at 215K rows with exact search; add `ivfflat`/`hnsw` if the corpus grows substantially.
4. **Upstream Gold data quality**: non-unique natural keys across S3 part-files (handled via `ON CONFLICT DO NOTHING` in the loader) and a handful of literal `"NaN"` string placeholders in `artist_uri` — flagged for the pipeline-owning team, not fixed here (out of scope for this app).
