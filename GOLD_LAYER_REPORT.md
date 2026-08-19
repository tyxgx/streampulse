# Gold Layer → Dashboard + RAG Chatbot — Report

Scope: website only (dashboard + chatbot reading real Gold data). Tableau/Athena untouched. No billable AWS resources provisioned.

## 1. Real Gold Layer Inventory

`s3://group-1-dbda/gold/` — **5 tables, not 6, and none of the originally designed names/grain exist.**

| Real table | Files | Est. rows | Partitioned | Matches original design? |
|---|---|---|---|---|
| `artist_performance` | 2000 | ~664K (estimated) | by `year=YYYY/` | Closest to `artist_summary`, but monthly grain |
| `country_performance` | 1919 | ~6.8K (estimated) | by `year=YYYY/` | Closest to `country_summary` |
| `label_performance` | 2000 | ~301K (estimated) | by `year=YYYY/` | **New — not in original design** |
| `dashboard_summary` | 1 | 113 (exact) | no | New, aggregate-of-aggregates |
| `monthly_trends` | 1 | 113 (exact) | no | Closest to `daily_timeseries`, but **monthly**, not daily |

**`track_summary`, `track_similarity`, `cluster_definitions` do not exist.** There is no track-level grain anywhere in Gold — everything is pre-aggregated to artist/country/label/month. This kills any RAG idea centered on individual tracks or similarity/clustering; grounding has to work at artist/country/label/month level instead.

Row counts for the 3 partitioned tables are **estimated** from byte-size sampling (not exact — would require reading all ~5,900 file footers, skipped to keep this pass fast). `dashboard_summary`/`monthly_trends` counts are exact (single file each).

`year` is **not a column** in `artist_performance`, `country_performance`, `label_performance` — it only exists as the S3 partition path (`year=2024/`). The ETL script derives it from the key.

## 2. Postgres Hosting Options

**Neither AWS CLI, Postgres, nor pgvector was installed on this machine** at the start of this session — awscli + credentials are now set up (you ran `aws configure`). Postgres/pgvector are still not installed, pending your choice below.

| Option | Steps from here | Cost |
|---|---|---|
| **Local Postgres** (recommended for dev) | `brew install postgresql@16 pgvector`, `brew services start postgresql@16`, `createdb gold`, `psql gold -f schema.sql` | Free |
| **AWS RDS Postgres** | Provision RDS instance (needs your AWS console access — not run here, it's billable), enable `pgvector` extension (supported on RDS PG 15+), apply `schema.sql`, expose to Django via `DATABASE_URL_GOLD` | **Billable — flagged, not provisioned** |

`pgvector` 0.8.5 is available and compatible via Homebrew alongside `postgresql@16`.

`schema.sql` (repo root) has `CREATE TABLE` for all 5 real Gold tables plus `gold_chunks` (RAG store, `vector(384)` — dimension confirmed in Step 4).

## 3. ETL Script

`scripts/load_gold_to_postgres.py` — reads each Gold table from S3 (boto3 + pyarrow), derives `year` from the partition path where needed, truncate-and-reload into Postgres (psycopg2).

Ran `--dry-run` against the real S3 data — output confirmed:
- All 5 tables found, file counts match Step 1
- Columns match `schema.sql` exactly
- Sample rows print correctly (e.g. artist `Kendrick Lamar`/`Ed Sheeran` in `country_performance`, real streaming numbers in `dashboard_summary`)

## 4. Embedding Feasibility

Chunk templates written for `artist_performance`, `country_performance`, `label_performance` (the 3 tables with meaningful per-entity narrative — `dashboard_summary`/`monthly_trends` are single aggregate rows, not really "chunk" material).

**Local test** (`all-MiniLM-L6-v2`, CPU, this M1/8GB machine):
- 100 chunks embedded in **1.82s** (18.2 ms/chunk)
- **Embedding dimension: 384** → `schema.sql`'s `gold_chunks.embedding` is `vector(384)`

**Extrapolated to full dataset (~972K rows if every artist/country/label-month row becomes one chunk): ~294 minutes (~5 hours).** That is *not* a quick one-time job at full grain — it needs either:
- **Reduced grain** (e.g. one chunk per artist *per year* instead of per month → ~12x fewer chunks, ~25 min), or
- **Batched/checkpointed processing** if you want full monthly grain.

Recommendation: **local embedding stays free and is fast enough per-chunk (18ms) — the only issue is total chunk count.** Reduce grain before embedding rather than reaching for a paid API. API-based embedding is only worth considering if you specifically want full monthly-grain chunks without a multi-hour local batch job — your call.

## 5. Django Integration

Inspected existing conventions: `services.py` (dummy data, single integration point) → `api.py` (DRF `APIView` + `@extend_schema`) → registered in `apps/api/v1/urls.py`. Dashboard JS fetches from these endpoints; chatbot JS posts `{message}` and expects `{reply}` back.

**New `apps/gold_data` app** (skeleton, created):
- `models.py` — `managed=False` models for all 5 Gold tables, pointed at a `gold` DB alias. Chose `managed=False` over Django migrations because `schema.sql` + the truncate-and-reload ETL script are the actual source of truth — Django shouldn't try to own/migrate tables that get wiped and reloaded by a batch job.
- `serializers.py`, `api.py` — `LatestKPIView` and `MonthlyTrendsView`, response shapes deliberately mirror the existing dummy `KPISerializer`/dashboard chart contract so `dashboard.js` doesn't need to change.
- **Not yet wired into `apps/api/v1/urls.py`** or `INSTALLED_APPS`/`DATABASES['gold']` — needs the Postgres decision (Step 2) first.

**RAG chatbot** (`apps/chatbot/rag.py`, new file):
- `embed_query()` (same MiniLM model), `retrieve_chunks()` (pgvector `<->` nearest-neighbor against `gold_chunks`), `build_prompt()`, `get_rag_reply()` (calls Claude via the `anthropic` SDK with a "cite your source" system prompt).
- **Not yet swapped into `apps/chatbot/services.py::get_bot_reply()`** — that stays on the canned demo stub until you're ready to flip it (one-line change once Postgres + `ANTHROPIC_API_KEY` exist).
- Added optional `sources` field to `ChatMessageResponseSerializer`.

**Floating chat widget** (new files, dark-theme-matched, **not yet included in `base.html`**):
- `templates/partials/chat_widget.html` — floating `.icon-chip` toggle button + `.app-card` panel
- `static/js/chat-widget.js` — posts to the same `/api/v1/chatbot/messages/` endpoint as the full chatbot page
- To activate: add `{% include 'partials/chat_widget.html' %}` before `</body>` in `templates/base.html`, plus a few CSS rules for `.chat-widget*` classes in `main.css` (not written yet — flagging as follow-up, not urgent)

**Dashboard files that would need to swap from dummy → real data** (once `gold_data` is wired):
- `apps/api/v1/urls.py` — swap `KPIListView`/`ChartDataView` imports for `gold_data.api` views (or add alongside)
- `apps/dashboard/services.py` — either delete once `gold_data` fully replaces it, or keep as offline-demo fallback

## 6. Branding Audit

"DataPipeline Platform" appears in exactly 3 files:
- `templates/partials/navbar.html` (nav brand)
- `templates/partials/footer.html` (footer heading + copyright line)
- `templates/base.html` (`<title>` suffix: "Big Data Pipeline Platform")

**Name alternatives** (Spotify-analytics-flavored):
1. **Wavelength** — evokes audio waveform + data trends, short, brandable
2. **StreamPulse** — "stream" (Spotify) + "pulse" (live analytics/heartbeat)
3. **Cadence Analytics** — musical term (rhythm/tempo) doubling as "regular data cadence"
4. **Resonance** — music term, implies impact/reach (matches "reach X countries" metric)
5. **Chartline** — pun on Billboard "charts" + data "line" charts

No rename performed — pick one and I'll do the find-and-replace across the 3 files.

## 7. Decisions Pending Your Go-Ahead

1. **Postgres hosting**: local (free, `brew install postgresql@16 pgvector`) vs. RDS (billable, needs manual provisioning in AWS console — not done here). Local is the natural fit until the team is ready to share a real DB.
2. **Embedding grain**: full monthly-grain chunks (~972K rows, ~5hr local batch) vs. reduced yearly-grain chunks (~25 min local). Recommend reduced grain.
3. **Branding name**: pick one of the 5 above (or none — leave as "DataPipeline Platform").

Nothing above 1–3 has been executed or finalized.
