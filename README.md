# StreamPulse

A Django web app with a Spotify streaming-analytics dashboard and a RAG (Retrieval-Augmented
Generation) chatbot that answers natural-language questions grounded in real data — not
hallucinated numbers.

Built independently on top of a Spotify Gold data lake (S3, Hive-partitioned Parquet) from a
CDAC PGCP-BDA team capstone project I contributed to on architecture and pipeline design. The
Django app, the Postgres/pgvector RAG pipeline, the chatbot, and the AWS EC2 deployment are my
own end-to-end build.

## What it does

- **Analytics dashboard** — Spotify streaming data by artist, country, and label, backed by
  Postgres tables loaded from the S3 Gold layer (Parquet, Hive-partitioned by year).
- **RAG chatbot** — ask questions like *"How did Kendrick Lamar perform in 2024?"* or
  *"Compare India and Brazil"* and get answers grounded in the actual Gold data, with a
  confidence gate that refuses to answer (rather than guess) when nothing relevant is found.

## Tech stack

| Layer | Choice |
|---|---|
| Data lake (source) | AWS S3, Parquet, Hive-partitioned by year |
| Database | PostgreSQL 17 + `pgvector` (`ivfflat` approximate nearest-neighbor index) |
| Embeddings | `all-MiniLM-L6-v2` (SentenceTransformers, local, free, 384-dim) |
| LLM | Groq (`llama-3.3-70b-versatile`) → Gemini → local Ollama (`llama3.2:3b`) fallback chain |
| Backend | Django 6 + Django REST Framework |
| Cache | Redis (chatbot response cache + per-provider usage tracking, fails open if unreachable) |
| Deployment | AWS EC2 (`t3.small`, Ubuntu 22.04, `ap-south-1`), Nginx + Gunicorn, systemd |

## How the RAG pipeline works

```
S3 Gold layer (Parquet)
      │  scripts/load_gold_to_postgres.py — boto3 + pyarrow → psycopg2
      ▼
Postgres — 5 Gold tables (artist/country/label/dashboard/monthly)
      │  scripts/build_gold_chunks.py — aggregates to yearly grain, turns each row
      │  into an English sentence, embeds it with all-MiniLM-L6-v2
      ▼
Postgres — gold_chunks (source_table, source_key, chunk_text, embedding vector(384))
      │  pgvector ivfflat index for fast nearest-neighbor search
      ▼
User question → embedded the same way → top-k nearest chunks retrieved
      │  routed one of three ways: exact SQL for aggregate questions ("which is highest"),
      │  per-entity scoped retrieval for comparisons, or single-entity vector search
      ▼
Confidence gate (skip the LLM entirely if nothing relevant enough was found)
      │
      ▼
Prompt = retrieved chunks as context + question → Groq/Gemini/Ollama → grounded answer
```

## Engineering notes worth knowing

- **Confidence gate.** Out-of-scope questions sometimes scored deceptively close in vector
  space, producing confidently-wrong answers. A distance threshold now makes the bot say
  "I don't have data to answer that" instead of guessing.
- **SQL router for aggregates.** Questions like "which country has the most streams" can't be
  answered from 5 similar-looking chunks — those are keyword-routed to a direct SQL query
  against the Gold tables instead of the RAG path.
- **Per-entity scoped retrieval for comparisons.** A single shared top-k search let one entity's
  chunks crowd out the other's when comparing two countries/artists — retrieval is now done
  separately per entity and merged.
- **LLM fallback chain.** Groq first (fast, generous free tier), Gemini second (added after
  Groq's 100K-tokens/day cap got exhausted repeatedly), local Ollama last (free, no API key,
  always available as a last resort).
- Tested against a fixed set of 15 questions before/after every fix: 7/15 flipped from wrong to
  correct, 0 regressions.

## Local setup

```bash
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DJANGO_SECRET_KEY, GOLD_DATABASE_URL, and an LLM key (optional)
python manage.py migrate
python manage.py runserver
```

Requires Postgres 17 with the `pgvector` extension (`schema.sql` has the full DDL, including
`gold_chunks`). Without a `GROQ_API_KEY`/`GEMINI_API_KEY` set, the chatbot falls back to a local
Ollama server (`ollama pull llama3.2:3b`).

## Project layout

```
apps/
  core/          landing pages
  gold_data/     dashboard views over the Gold tables
  chatbot/       RAG pipeline (rag.py), caching (cache.py), chat API
  api/           REST API (DRF)
  documentation/ in-app docs
  contact/       contact form
  team/          team page
scripts/         S3 → Postgres ETL, gold_chunks builder, baseline probe
config/          Django settings (dev/prod split)
schema.sql       full Postgres schema, including gold_chunks (pgvector)
```

## Deployment

Previously deployed on AWS EC2 (`t3.small`, `ap-south-1`, IP-only, no domain/TLS). See
`STREAMPULSE_FULL_FLOW.md` for the full deployment log, including every real issue hit
(Python version mismatch, disk space, Postgres version mismatch, `t3.micro` CPU-credit
exhaustion, static file permissions, `ivfflat` recall drift after index rebuilds) and how each
was fixed.
