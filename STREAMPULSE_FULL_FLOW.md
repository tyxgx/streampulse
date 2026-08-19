# StreamPulse — Full Project Flow (Gold Data → RAG Chatbot → AWS Deployment)

This is a complete technical walkthrough of a project called StreamPulse: a Django web app with
a Spotify-analytics dashboard and a RAG (Retrieval-Augmented Generation) chatbot that answers
questions using real data. Use this document to understand the whole system end-to-end, or to
replicate a similar system yourself.

---

## 1. What the project is

- A Django app with two main features:
  1. A dashboard showing Spotify streaming analytics (artists, countries, labels)
  2. A chatbot that answers natural-language questions about that data, grounded in real numbers
     (not hallucinated) using RAG
- Data source: a "Gold layer" of processed analytics data sitting in AWS S3 as Parquet files
  (this came from an upstream big-data pipeline — raw Spotify data → cleaned/aggregated "Gold"
  tables, produced by a separate part of the team)
- Deployed live on an AWS EC2 instance

---

## 2. Tech stack

| Layer | Choice |
|---|---|
| Data lake (source) | AWS S3, Parquet files, Hive-partitioned by year |
| Database | PostgreSQL 17 |
| Vector search | `pgvector` Postgres extension, `ivfflat` approximate index |
| Embedding model | `all-MiniLM-L6-v2` (SentenceTransformers, runs locally, free, 384-dim output) |
| LLM | Groq API (`llama-3.3-70b-versatile`), falls back to local Ollama (`llama3.2:3b`) if no API key |
| Backend framework | Django 6 + Django REST Framework |
| Hosting | AWS EC2 `t3.small`, Ubuntu 22.04, Nginx (reverse proxy) + Gunicorn (app server) |

---

## 3. Data flow, end to end

```
AWS S3 (Parquet files)
      │  aws s3 sync  (manual one-time command, copies files to local disk)
      ▼
Local disk copy (.gold_local/ folder)
      │  scripts/load_gold_to_postgres.py reads via pyarrow, inserts via psycopg2
      ▼
PostgreSQL — 5 "Gold" tables:
   artist_performance, country_performance, label_performance,
   dashboard_summary, monthly_trends
      │  scripts/build_gold_chunks.py:
      │    1. aggregates monthly rows → yearly rows per entity (groupby entity+year)
      │    2. turns each yearly row into one English sentence ("chunk")
      │    3. embeds each sentence with all-MiniLM-L6-v2 → 384 numbers (a vector)
      ▼
PostgreSQL — gold_chunks table (same database, new table):
   source_table TEXT, source_key TEXT, chunk_text TEXT, embedding vector(384)
      │  pgvector's ivfflat index makes nearest-neighbor search fast
      ▼
User asks chatbot a question
      │  apps/chatbot/rag.py: get_rag_reply(question)
      │    1. embed the question the same way (MiniLM → 384 numbers)
      │    2. search gold_chunks for the closest-matching rows (pgvector `<->` operator)
      │    3. build a prompt: "Context: <retrieved chunks>\n\nQuestion: <user question>"
      │    4. send prompt to Groq (or Ollama fallback) → get an answer
      ▼
Answer shown in chatbot UI, grounded in real retrieved data
```

**Key idea of RAG**: instead of asking the LLM the question directly (which risks it making up
numbers), you first search your own database for the most relevant real facts, hand those facts
to the LLM as context, and tell it to answer *only* from that context. This is what "grounds" the
answer in real data.

---

## 4. How a sentence becomes a vector (embeddings, concretely)

Example: `"In India during 2023, Spotify recorded 450000 total streams."`

1. **Tokenize** — the sentence is split into subword pieces (not whole words):
   `['in', 'india', 'during', '202', '##3', ',', 'spot', '##ify', 'recorded', '450', '##00', '##0', 'total', 'streams', '.']`
2. **Pass through a transformer neural network** (`all-MiniLM-L6-v2`, a small BERT-like model) —
   each token's meaning gets refined using the context of every other token (self-attention).
3. **Pool into one fixed-size vector** — all token representations are averaged/pooled into a
   single vector of exactly **384 numbers**, regardless of sentence length:
   `[0.00301953, -0.09033745, 0.00358492, -0.0181797, -0.02456461, ...]`

Code (`apps/chatbot/rag.py`):
```python
from sentence_transformers import SentenceTransformer
_model = SentenceTransformer('all-MiniLM-L6-v2')

def embed_query(text):
    return _model.encode([text])[0].tolist()
```

**Why this matters**: two sentences with similar *meaning* end up with vectors that are close
together geometrically. So a user's question about "India" gets a vector close to the vector of
the chunk describing India's data — that's what makes the search work. This is the same function
used both to embed the ~215,000 data chunks once at build time, and to embed the user's question
at query time.

---

## 5. What `gold_chunks` actually looks like (concrete example rows)

It's a normal Postgres table — 4 columns, each row has both the human-readable text AND its
vector, side by side:

| source_table | source_key | chunk_text | embedding |
|---|---|---|---|
| country_performance | India\|2023 | "In India during 2023, Spotify recorded 450,000 total streams (avg 3.2% market share...)" | [0.003, -0.090, 0.003, ...] (384 numbers) |
| artist_performance | Kendrick Lamar\|2024 | "In 2024, artist Kendrick Lamar had 12M total streams..." | [0.011, -0.045, 0.077, ...] (384 numbers) |

Schema (`schema.sql`):
```sql
CREATE TABLE gold_chunks (
    source_table  TEXT,
    source_key    TEXT,
    chunk_text    TEXT,
    embedding     vector(384)          -- pgvector's special column type
);

CREATE INDEX idx_gold_chunks_embedding
  ON gold_chunks USING ivfflat (embedding vector_l2_ops) WITH (lists = 216);
```

No separate vector database (no Pinecone/Weaviate/Chroma) — `pgvector` is a Postgres *extension*,
so vectors live in the same Postgres database as everything else, queried with normal SQL plus one
special operator `<->` (distance between two vectors).

Search query used at runtime:
```sql
SELECT chunk_text FROM gold_chunks
ORDER BY embedding <-> %s::vector   -- %s = the user question's embedding
LIMIT 5
```
This returns the 5 rows whose vector is closest (most similar in meaning) to the question.

Total chunks: ~215,725 (151,264 artist-year rows + 63,789 label-year rows + 672 country-year rows).

---

## 6. The RAG pipeline logic (`apps/chatbot/rag.py`, the core file)

`get_rag_reply(question)` tries 3 paths in order:

**Path 1 — SQL router** (for "highest", "how many", "grew" style questions)
Vector search can only fetch similar-looking chunks — it can't compute a true MAX/COUNT/growth
across the whole dataset. So questions matching trigger keywords get routed to a real SQL query
against the Gold tables directly, giving an exact/deterministic answer instead of an LLM guess.

**Path 2 — Multi-entity comparison** (e.g. "Compare India and Brazil")
If 2+ known country names are detected in the question, retrieval is done *separately per
country* and merged — otherwise a single shared top-5 search lets one country's chunks crowd out
the other's.

**Path 3 — Normal single-entity search**
1. Embed the question
2. Classify which table it's about (keyword/entity-name match) to scope the search
3. Retrieve top-5 nearest chunks
4. **Confidence gate**: if no table could be matched AND even the closest chunk is too far away
   (distance > 1.10), skip the LLM entirely and reply "I don't have data to answer that" — this
   prevents confidently-wrong answers on out-of-scope questions
5. Otherwise, build a prompt with the retrieved chunks as context and call the LLM

**LLM call** — if `GROQ_API_KEY` env var is set, use Groq's cloud API (fast); otherwise fall back
to a local Ollama server (`llama3.2:3b`, free but slower).

---

## 7. Problems found and fixed (via testing, not just code review)

| Problem | Fix |
|---|---|
| Out-of-scope questions sometimes scored *closer* than correct answers, so the LLM would confidently answer wrong | Added a distance-based confidence gate before calling the LLM |
| Comparing two countries sometimes returned all context for one country, none for the other | Per-entity scoped retrieval, merged results |
| "Which is highest" / "how many" can't be answered from 5 similar chunks | Added a keyword-triggered SQL router for aggregate questions |
| A chunk-building function was silently dropping two computed fields | Added the fields back, re-embedded just those chunks |
| No index on the embedding column — every search was a full table scan | Built an `ivfflat` index, then tuned the `probes` parameter for full recall |
| — | Wired up Groq as the LLM provider, kept Ollama as a fallback |

Tested on the same 15 fixed questions before/after every change: 7/15 flipped from wrong to
correct, 0 regressions.

---

## 8. AWS deployment

```
Internet ──▶ Nginx :80 ──▶ Gunicorn :8000 ──▶ Django ──▶ Postgres 17 + pgvector :5432
                                                    │
                                                    └──▶ Groq API (external, over the internet)
```

- EC2 instance, `t3.small`, Ubuntu 22.04, ap-south-1 (Mumbai) region
- Postgres installed directly on the EC2 instance (same machine as Django — not RDS)
- Data moved from local dev to the server with `pg_dump` (local) → `pg_restore` (server)
- App runs as a `systemd` service running Gunicorn; Nginx reverse-proxies to it and also serves
  static files directly
- No domain name / TLS — IP-only deployment, so Django's `SECURE_SSL_REDIRECT` had to stay off

**Real issues hit during deployment (useful if replicating this):**

| Issue | Fix |
|---|---|
| Django 6 needs Python ≥3.12, Ubuntu 22.04 ships Python 3.10 | Installed Python 3.12 via the deadsnakes PPA |
| Ran out of disk space installing PyTorch/sentence-transformers | Resized EBS volume 8GB → 20GB, grew the filesystem |
| `pg_restore` failed — dump was from Postgres 17, server had Postgres 14 | Added the official PGDG apt repo, installed Postgres 17 to match |
| `t3.micro` ran out of CPU credits mid-setup, instance became fully unresponsive | Resized instance to `t3.small` |
| Static files returned 403 (Nginx couldn't read them) | Fixed home directory / staticfiles folder permissions |
| `ivfflat` index gives different internal clustering every time it's rebuilt — a `probes` value tuned on one build didn't give full recall after a rebuild on the server | Re-swept the `probes` value directly against the live server index, verified against an exact brute-force search |

---

## 9. Project file layout (for reference)

```
apps/
  core/          landing pages
  dashboard/     KPI + chart UI (reads from Gold tables)
  gold_data/     Django models/queries for the 5 Gold tables (read-only, managed=False)
  chatbot/       the RAG pipeline + chat UI
    rag.py       <- the core RAG logic (embedding, retrieval, SQL router, LLM calls)
    services.py  <- thin wrapper: calls rag.py, falls back to a canned reply on error
    api.py       <- DRF endpoint: POST /api/v1/chatbot/messages/
  api/           versioned DRF API root
scripts/
  load_gold_to_postgres.py   S3 Parquet (local copy) → Postgres Gold tables
  build_gold_chunks.py       Gold tables → yearly chunks → embeddings → gold_chunks table
  rag_baseline_probe.py      test harness — runs a fixed question set end-to-end
schema.sql        full Postgres schema (all 5 Gold tables + gold_chunks + indexes)
config/settings/  Django settings — DATABASES has two entries: 'default' (sqlite,
                   for Django admin/auth) and 'gold' (Postgres, the real Gold data)
```

---

## 10. Two separate databases inside one Django app

`config/settings/base.py`:
```python
DATABASES = {
    'default': ...,   # sqlite — only for Django's own admin/auth tables, unrelated to Gold data
    'gold': env.db('GOLD_DATABASE_URL', ...)   # Postgres — the real analytics + RAG data
}
```
Code that queries Gold data explicitly says `.objects.using('gold')` (or in `rag.py`,
`connections['gold'].cursor()`) to point at the right database.

---

## How to explain this in one paragraph (for a quick verbal summary)

"We have Spotify analytics data sitting in S3 as Parquet files. We load it into 5 Postgres
tables. To let a chatbot answer questions about it, we generate one summary sentence per
entity-year (artist/country/label), turn each sentence into a 384-number vector using a local
embedding model, and store both the sentence and its vector in a Postgres table called
gold_chunks — using the pgvector extension so we don't need a separate vector database. When a
user asks a question, we embed the question the same way, find the closest-matching stored
sentences using vector search, hand those as context to an LLM (Groq, cloud-hosted
llama-3.3-70b), and the LLM answers using only that real data — so it can't make numbers up. Pure
aggregate questions like 'which country has the highest streams' get routed to a direct SQL query
instead, since vector search alone can't compute a true maximum. The whole thing is deployed on a
single AWS EC2 instance running Postgres, Django, and Nginx together."
