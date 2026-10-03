# RAG Pipeline — Architecture and Flow (Final)

## 1. Overview

StreamPulse includes a Retrieval-Augmented Generation (RAG) chatbot that answers natural-language
questions about the platform's Gold-layer analytics data. Instead of asking a large language model
(LLM) to answer from its own training knowledge — which risks fabricated numbers — the system
first retrieves real, relevant facts from its own database and instructs the LLM to answer strictly
from that retrieved context. Purely computational questions (maximum, count, growth) are answered
by direct SQL queries instead of the LLM, guaranteeing an exact result where vector search cannot.

---

## 2. System Architecture

```
┌──────────────┐     ┌────────────────────┐     ┌──────────────────────────────┐
│   AWS S3      │────▶│  PostgreSQL 17       │────▶│   Django (EC2, Nginx +         │
│  Gold layer   │     │  + pgvector          │     │   Gunicorn)                     │
│  (Parquet)    │     │                      │     │                                  │
└──────────────┘     │  ├─ artist_performance │    │  ┌───────────┐  ┌─────────────┐ │
                      │  ├─ country_performance│    │  │ Dashboard  │  │  RAG Chatbot │ │
                      │  ├─ label_performance  │    │  └───────────┘  └──────┬──────┘ │
                      │  ├─ dashboard_summary  │    └────────────────────────┼────────┘
                      │  ├─ monthly_trends     │                             │
                      │  └─ gold_chunks (RAG)  │                             ▼
                      └──────────────────────┘                       Groq LLM API
                                                                   (llama-3.3-70b-versatile)
                                                                   falls back to local Ollama
```

**Layers:**
- **Data lake** — AWS S3, Parquet files, Hive-partitioned by year.
- **Database** — PostgreSQL 17, single database, two roles: (a) the Gold-layer analytics mart
  (5 tables) used directly by the dashboard, and (b) `gold_chunks`, a RAG-specific table built
  from that same mart, holding text + vector embeddings side by side via the `pgvector` extension.
  No separate vector database is used.
- **Application** — Django 6 + Django REST Framework, deployed on a single AWS EC2 instance
  (Nginx reverse proxy → Gunicorn → Django).
- **LLM** — Groq's hosted API (`llama-3.3-70b-versatile`), used for generation; falls back
  automatically to a local Ollama model (`llama3.2:3b`) if no Groq API key is configured.

---

## 3. Data Conversion — Gold Tables to Retrievable Chunks

The Gold layer is pre-aggregated tabular data (not free text), so each row is converted into a
retrievable unit ("chunk") in two stages:

**Stage 1 — Yearly aggregation.** Monthly rows are grouped by `(entity, year)` using `pandas`,
with an aggregation function chosen per column's meaning (sum for additive volume metrics such as
total streams, mean for rate/percentage metrics, max for catalog-size metrics, last-value for
"current leader" fields such as top artist/label). This keeps chunks at the granularity people
actually ask questions at ("how did X do in 2024") rather than at raw monthly grain.

| Source table | Monthly rows | Yearly rows (chunks) |
|---|---|---|
| `artist_performance` | 652,373 | 151,264 |
| `country_performance` | 7,496 | 672 |
| `label_performance` | 286,055 | 63,789 |
| **Total** | | **215,725 chunks** |

`dashboard_summary` and `monthly_trends` are excluded — they are single global-aggregate tables
(one row = the whole platform for that month), not per-entity data, so there is no meaningful
per-entity chunk to generate from them.

**Stage 2 — Text + embedding generation.** Each yearly row is converted to a natural-language
sentence via a fixed template, e.g.:

> "In India during 2023, Spotify recorded 450,000 total streams (avg 3.2% market share, avg 5.1%
> growth), with up to 12,000 active song(s) and an average catalog hit rate of 41.20%. Up to 3,200
> active artists and 480 active labels were represented that year. Most recent top artist was
> [Artist], top label was [Label]."

Each sentence is embedded with `all-MiniLM-L6-v2` (SentenceTransformers, local, free, 384-dimensional
output) and stored — together with the original text and a source identifier
(`source_table`, `source_key` = `"{entity}|{year}"`) — as one row in `gold_chunks`. This lets every
retrieved fact be traced back to its exact origin row, so the chatbot can cite real sources with
every answer, not just prose.

215,725 chunks were embedded in ~6.8 minutes on CPU (batched, batch size 64).

```
Gold tables (monthly)
      │  groupby(entity, year) — sum / mean / max / last per column
      ▼
Yearly rows
      │  fixed template — row → natural-language sentence
      ▼
Chunk text  ──▶  all-MiniLM-L6-v2  ──▶  384-dim vector  ──▶  gold_chunks (pgvector)
```

---

## 4. Retrieval Infrastructure

`gold_chunks` is an ordinary PostgreSQL table with one addition: a `vector(384)` column, enabled by
the `pgvector` extension. An `ivfflat` approximate-nearest-neighbor index is built on this column
(216 lists) so similarity search does not require a full 215K-row scan on every query.

```sql
CREATE TABLE gold_chunks (
    source_table  TEXT,
    source_key    TEXT,
    chunk_text    TEXT,
    embedding     vector(384)
);

CREATE INDEX idx_gold_chunks_embedding
  ON gold_chunks USING ivfflat (embedding vector_l2_ops) WITH (lists = 216);
```

Retrieval query:
```sql
SELECT source_table, source_key, chunk_text
FROM gold_chunks
ORDER BY embedding <-> :question_embedding
LIMIT 5;
```
`<->` computes L2 (Euclidean) distance between two vectors — the 5 chunks with the smallest
distance to the question's own embedding are returned as the most semantically relevant matches.

---

## 5. Query-Time RAG Pipeline

```
                              User question
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │  Keyword match?                 │
                    │  "highest / most / how many /   │
                    │   grew / growth"                │
                    └───────┬─────────────────┬───────┘
                        yes │                 │ no
                            ▼                 ▼
                ┌───────────────────┐   ┌─────────────────────────┐
                │   SQL router        │   │  2+ known countries       │
                │   (parameterized     │   │  named in question?       │
                │   query on Gold      │   └──────┬─────────────┬────┘
                │   tables — exact,    │      yes  │             │ no
                │   deterministic      │           ▼             ▼
                │   result, no LLM     │  ┌──────────────────┐ ┌─────────────────┐
                │   guessing)          │  │ Per-country        │ │ embed_query()     │
                └─────────┬───────────┘  │ scoped retrieval    │ │ classify_query()  │
                          │              │ (one query per        │ │ retrieve_chunks() │
                          │              │  country, merged)     │ └────────┬─────────┘
                          │              └─────────┬────────┘             │
                          │                        │             ┌───────┴────────┐
                          │                        │             │ No table match  │
                          │                        │             │ AND distance     │
                          │                        │             │ too far?         │
                          │                        │             └───┬─────────┬───┘
                          │                        │              yes│         │no
                          │                        │                 ▼         │
                          │                        │          "I don't have    │
                          │                        │           data for that"  │
                          │                        │          (no LLM call)    │
                          │                        │                          │
                          └────────────┬───────────┴──────────────────────────┘
                                       ▼
                          build_prompt(question, context)
                                       │
                                       ▼
                            Groq API (llama-3.3-70b)
                          — falls back to local Ollama
                            if no Groq key configured
                                       │
                                       ▼
                       {"reply": "...", "sources": [...]}
                                       │
                                       ▼
                     Markdown-rendered reply + source chips
                          shown in the chat widget
```

### Stage-by-stage explanation

1. **SQL router** — Vector similarity search can only return the top-k chunks that look most
   similar to the question text; it cannot compute a true MAX, COUNT, or year-over-year delta
   across rows it never retrieves together. Questions matching trigger keywords ("highest",
   "how many", "grew") are routed directly to a parameterized SQL query against the real Gold
   tables, giving an exact, deterministic answer instead of an LLM guess assembled from a handful
   of similar-looking chunks. `COUNT` results are returned directly, without an LLM call;
   `superlative`/`growth` results are handed to the LLM only to phrase the answer in natural
   language, not to compute it.

2. **Multi-entity comparison detection** — If two or more known country names are detected in the
   question (e.g. "Compare India and Brazil"), retrieval is run once per country and the results
   are merged, rather than a single shared top-5 search — otherwise the country whose data is
   semantically "closer" to the question crowds out the other entirely.

3. **Standard retrieval path** — For all other questions:
   - The question is embedded with the same MiniLM model used at chunk-build time.
   - `classify_query()` checks whether the question names a real country or matches a
     table-specific keyword, and if so scopes the search to that table only — this prevents a
     small table (`country_performance`, 672 chunks) from being drowned out by a much larger one
     (`artist_performance`, 151K chunks) in a shared nearest-neighbor search.
   - The top-5 nearest chunks are retrieved via the `<->` operator.

4. **Confidence gate** — If no table could be matched *and* even the single closest chunk exceeds a
   calibrated distance threshold, the pipeline returns "I don't have data to answer that" without
   calling the LLM at all. This threshold was set by measuring real distances: a confirmed-correct,
   name-routed match sits around 0.86–1.04; a confirmed out-of-scope query sits around 1.16–1.17.
   This prevents the model from confidently answering questions the data cannot actually support.

5. **Prompt construction and generation** — Retrieved chunks (or SQL results) are assembled into a
   context block and combined with the user's question into a single prompt. The system prompt
   instructs the model to answer only from the supplied context, never invent numbers, cite the
   entity and time period for every fact, and format the answer in markdown (bold for key numbers/
   entities, bullet lists for multi-fact answers, a markdown table when comparing multiple entities
   across the same metrics). The prompt is sent to Groq's `llama-3.3-70b-versatile` model over its
   OpenAI-compatible chat endpoint; if no Groq API key is configured, the same prompt is sent to a
   local Ollama server instead, with no other code path changing.

6. **Response and rendering** — The API returns `{"reply": "...", "sources": [...]}`. The frontend
   renders the reply through a markdown parser (sanitized before insertion into the page) so bold
   text, bullet lists, headings, and tables display correctly instead of as raw `**`/`-`/`#`
   characters, and renders each source as a small chip beneath the reply so the answer's origin
   data is visible, not just the prose.

---

## 6. Technology Stack

| Layer | Choice | Reasoning |
|---|---|---|
| Data lake | AWS S3, Parquet | Already the pipeline's Gold-layer output format |
| Database | PostgreSQL 17 | Single database serves both the analytics dashboard and RAG retrieval — no extra infrastructure |
| Vector search | `pgvector` extension, `ivfflat` index | Adds vector similarity search to the existing Postgres instance instead of running a separate vector database |
| Embedding model | `all-MiniLM-L6-v2` (SentenceTransformers) | Runs locally on CPU, free, no external API dependency, sufficient accuracy at this data scale |
| LLM | Groq `llama-3.3-70b-versatile` (cloud), Ollama `llama3.2:3b` (local fallback) | Groq gives fast, higher-quality answers; the fallback keeps the system fully functional with zero API cost when no key is configured |
| Backend | Django 6 + Django REST Framework | Single POST endpoint (`/api/v1/chatbot/messages/`) between frontend and RAG pipeline |
| Hosting | AWS EC2 (`t3.small`), Nginx + Gunicorn | Single-instance deployment, Postgres and Django co-located |

---

## 7. Why This Design — Key Decisions

- **Retrieval-augmented, not fine-tuned or purely prompted** — grounding every answer in
  retrieved real data (or a real SQL result) is what allows the system to say "I don't have data
  for that" instead of fabricating a plausible-sounding number, which is the central risk in any
  chatbot answering questions about specific proprietary data.
- **SQL router as a first-class path, not an afterthought** — aggregate questions (maximum, count,
  growth) have exactly one correct answer that a top-k similarity search structurally cannot
  produce; routing these to real SQL removes an entire category of possible wrong answers rather
  than trying to prompt-engineer around it.
- **pgvector over a dedicated vector database** — at 215,725 vectors, a dedicated vector database
  (Pinecone, Weaviate, Chroma) would add a new service to run, pay for, and keep in sync, with no
  retrieval capability pgvector doesn't already provide at this scale.
- **Confidence gating scoped narrowly, not globally** — a global distance threshold was tested and
  found to suppress correct answers to informally-phrased but valid questions; gating only
  activates when there is no other signal of relevance (no table/entity match at all), so it
  targets genuinely out-of-scope questions specifically rather than penalizing phrasing style.
- **Groq with a local fallback, not Groq-only** — keeps the system fully operable in a local/dev
  environment or if the API key/quota is unavailable, at the cost of slower responses in that mode
  — an explicit, deliberate trade-off rather than a hard dependency on a paid external service.

---

## 8. Known Limitations

- Artist-level source citations show the underlying Spotify URI rather than a display name (the
  chunk key for `artist_performance` is the URI, not the artist's name) — accurate but not
  human-friendly in the sources list.
- Multi-entity comparison scoping (per-entity retrieval) currently covers countries only; a
  similarly-phrased multi-artist comparison question does not get the same crowding-out
  protection, since no exact artist-name list is maintained the way the country list is.
- The `ivfflat` index's internal clustering is randomized at build time — a `probes` value tuned
  for full recall on one index build is not guaranteed to transfer to a rebuilt index; the value
  in production was re-verified directly against the live index rather than assumed.
