# Gold Layer — Live Pass Report

Decisions locked in for this pass: **local Postgres**, **yearly embedding grain**, **rename to "StreamPulse"**.

## 1. Postgres Setup Confirmation

- Homebrew's `pgvector` bottle only targets `postgresql@17`/`@18`, not `@16` — the originally-installed `postgresql@16` was uninstalled and replaced with `postgresql@17` (judgment call, see §9).
- `postgresql@17` + `pgvector 0.8.5` installed and running (`brew services start postgresql@17`, confirmed via `pg_isready`).
- `gold` database created, `schema.sql` applied cleanly: `CREATE EXTENSION vector` succeeded, all 5 Gold tables + `gold_chunks` created (`\dt` confirmed 6 relations).

## 2. ETL Load Verification

First attempt failed (`AccessDenied` — expired AWS session token), fixed once you restarted the AWS session. Second attempt hit a real bug: the schema's primary keys (`artist_uri, year_month` etc.) aren't actually unique in the raw Gold data — some `(artist_uri, year_month)` pairs repeat across different S3 part-files. Fixed the loader to use `ON CONFLICT DO NOTHING` (upsert, first-seen wins) rather than crashing.

The sequential per-file S3 loop was also very slow (~10 min and still not done on just the first table) — switched to `aws s3 sync` (bulk parallel download, 5,921 files/64MB in under 2 min) + `pyarrow.dataset` (reads the whole Hive-partitioned table in one pass, auto-derives `year` from the partition folder). Also caught and fixed a bug in duplicate-count *reporting* — `psycopg2.execute_values` pages internally (default 100 rows/statement) so `cur.rowcount` after the call only reflected the last page, making it look like 99.5% of rows were duplicates when they weren't. Fixed to count the table directly.

| Table | Estimated (Step 1) | Actual loaded | Match |
|---|---|---|---|
| `artist_performance` | ~664K | **652,373** | close |
| `country_performance` | ~6.8K | **7,496** | close |
| `label_performance` | ~301K | **286,055** | close |
| `dashboard_summary` | 113 (exact) | **113** | exact |
| `monthly_trends` | 113 (exact) | **113** | exact |

All 5 tables loaded successfully, no unresolved mismatches.

## 3. Yearly Aggregation + Embedding Results

| Table | Monthly rows | Yearly rows | Reduction |
|---|---|---|---|
| `artist_performance` | 652,373 | 151,264 | 4.3x |
| `country_performance` | 7,496 | 672 | 11.2x |
| `label_performance` | 286,055 | 63,789 | 4.5x |

**Reduction is lower than the ~12x originally assumed** — most artists/labels aren't active in all 12 months of every year, so grouping by (entity, year) doesn't collapse a full dozen rows for most entities. Still a large win overall.

- **Total chunks: 215,725**
- **Embedding time: 406.4s (6.8 min)** on `all-MiniLM-L6-v2`, CPU — faster than the ~25 min estimate
- `gold_chunks` final count: 215,725, split exactly across 3 `source_table` values — `dashboard_summary`/`monthly_trends` correctly excluded (confirmed via `GROUP BY source_table`)

## 4. Live Dashboard Endpoint Test

Django dev server started (`localhost:8000`), `python manage.py check` clean. Hit a template bug on first load (see §9), fixed, then confirmed:

```
GET /api/v1/dashboard/kpis/
[
  {"id": "total-streams", "label": "Total Streams (May 2026)", "value": "23.93B", "delta": "-34.1%", "trend": "down", "icon": "bi-soundwave"},
  {"id": "active-artists", "label": "Active Artists", "value": "7,166", "delta": "-9.1%", "trend": "down", "icon": "bi-mic"},
  {"id": "countries-covered", "label": "Countries Covered", "value": "71", "delta": "—", "trend": "up", "icon": "bi-globe"},
  {"id": "catalog-hit-rate", "label": "Catalog Hit Rate", "value": "28.5%", "delta": "—", "trend": "up", "icon": "bi-graph-up-arrow"}
]

GET /api/v1/dashboard/charts/streams-over-time/
type: line, 113 labels (Jan 2017 ... May 2026), real monthly total-stream values

GET /api/v1/dashboard/charts/top-countries/
{"type": "bar", "labels": ["Global","United States","Mexico","Indonesia","India","Brazil","Philippines","Turkey"], ...}
```

Dashboard page itself (`GET /dashboard/`) returns 200, server-rendered KPI cards show the same real values (`23.93B`, `7,166`, `71`, `28.5%`) — confirmed via HTML grep, not just the API.

## 5. Live Chatbot Test

**`ANTHROPIC_API_KEY` was never obtained** (Claude Pro subscription ≠ API billing, and a paid key wasn't set up). Switched to a **free local alternative**: installed Ollama + `llama3.2:3b` (2GB, local, no API key) via Homebrew, rewrote `apps/chatbot/rag.py`'s generation call from the Anthropic SDK to Ollama's local `/api/chat` endpoint. `apps/chatbot/services.py::get_bot_reply()` now calls the real RAG pipeline, with a try/except fallback to the canned demo reply if Ollama is down.

Tested via the live DRF endpoint (`POST /api/v1/chatbot/messages/`), 4 real Q&A pairs:

1. **"How did Kendrick Lamar perform in Canada in a recent year?"**
   → Correctly retrieved 5 real `artist_performance` chunks for Kendrick Lamar (2019-2026), correctly noted the data doesn't break down by country and declined to invent a Canada-specific number, mentioned his real 2020 "reached 50 countries" figure. **Good.**

2. **"Which country had strong streaming numbers recently?"**
   → Retrieval picked 5 `artist_performance` chunks instead of `country_performance` chunks — the question is about countries, but the much larger artist corpus (151K chunks vs. 672 country chunks) dominated the nearest-neighbor search. Model correctly said it couldn't answer from the (wrong) context rather than guessing. **Retrieval bias, not hallucination** — flagged in §9.

3. **"Which label had the highest total streams in a recent year?"**
   → Correctly retrieved `label_performance` chunks, answered "Best in 2026, 25,851,845 streams" — a real grounded number from the data. **Good.**

4. **"Tell me about the song Shape of You by Ed Sheeran — what is its tempo and key?"** (deliberately out-of-scope — no track-level data exists in Gold at all)
   → **Failed the way it was supposed to be tested for**: instead of declining, the model said *"According to various sources (e.g., Genius), the tempo... is around 100 BPM, [key of] C minor"* — invented an answer from its own training data, ignoring the system prompt's "answer ONLY from the provided context" instruction. **This is a real hallucination**, and exactly the risk flagged when Ollama was proposed as the free alternative to Claude — a 3B local model is measurably weaker at instruction-following than Claude would be here.

**Net result: RAG pipeline (retrieval + generation + sources) is fully live and working**, but the small local model isn't fully reliable for strict grounding — 1 of 4 test questions hallucinated instead of declining. Documented, not silently hidden.

## 6. Chat Widget Activation Confirmation

- `{% include 'partials/chat_widget.html' %}` added to `templates/base.html` before `</body>`.
- `.chat-widget*` CSS written in `static/css/main.css` (floating toggle reusing `.icon-chip`'s circular treatment filled with `--color-primary`, panel reusing `.app-card`'s gradient-border shell, `prefers-reduced-motion` handled).
- **Bug caught and fixed**: `chat_widget.html` used `{% static %}` but didn't `{% load static %}` itself — Django's `{% load %}` doesn't propagate across `{% include %}` boundaries, so every page 500'd until fixed.
- Confirmed live: home page and dashboard page both return 200 and contain the widget markup (`grep -c "chat-widget"` → 8 matches on `/dashboard/`).

## 7. Rebrand Confirmation

Replaced in all 4 actual locations (one more than the original 3 identified — `SPECTACULAR_SETTINGS['TITLE']` in `config/settings/base.py` also said "Big Data Pipeline Platform API", caught during the final re-grep):
- `templates/partials/navbar.html` — nav brand
- `templates/partials/footer.html` — footer heading + copyright
- `templates/base.html` — `<title>` suffix + `og:title`
- `config/settings/base.py` — drf-spectacular API docs title

Final repo-wide grep for `DataPipeline Platform` / `Big Data Pipeline Platform` across `*.html`/`*.py`/`*.js`/`*.css`: **zero matches.**

## 8. Dashboard Files Updated

- `apps/gold_data/services.py` (new) — `get_kpis()`/`get_chart_data()`, same output shapes as the dummy `apps.dashboard.services`, sourced from real Postgres.
- `apps/gold_data/api.py` — rewritten from raw-model views to `GoldKPIListView`/`GoldChartDataView` matching the dummy `KPISerializer`/`ChartDataSerializer` contracts exactly.
- `apps/gold_data/serializers.py` — deleted (superseded by reusing `apps.dashboard.serializers`).
- `apps/gold_data/{__init__,apps,migrations/__init__}.py` — added (app was missing these, wouldn't have loaded).
- `apps/api/v1/urls.py` — `dashboard/kpis/` and `dashboard/charts/<chart_key>/` now route to `gold_data` views instead of the dummy ones.
- `apps/dashboard/views.py` — `DashboardView` now calls `gold_data.services.get_kpis()` instead of the dummy `services.get_kpis()`.
- `apps/dashboard/templates/dashboard/dashboard.html` — chart canvases changed from the dummy pipeline-monitoring concepts (`processed-over-time`, `layer-distribution`, `pipeline-status`) to real ones (`streams-over-time`, `top-countries`) — dropped one chart since there are only 2 meaningful real charts, not 3 (see §9).
- `config/settings/base.py` — `INSTALLED_APPS` += `gold_data`, `DATABASES['gold']` added (env-driven via `GOLD_DATABASE_URL`).
- `.env` / `.env.example` — `GOLD_DATABASE_URL` added.

**Kept, not deleted**: `apps/dashboard/services.py`'s dummy KPI/chart data and `apps/dashboard/api.py`'s `KPIListView`/`ChartDataView` — no longer routed, kept as an offline-demo fallback, following the same pattern already established by `apps.chatbot`'s canned-reply stub coexisting with `rag.py`.

## 9. Failures, Judgment Calls, and Flags

1. **postgresql@16 → @17 swap**: pgvector's Homebrew bottle doesn't support @16. Uninstalled @16 (no data had been written to it yet — safe), installed @17. If your team standardizes on a different Postgres version later, note this.
2. **Non-unique "primary keys" in real Gold data**: `schema.sql`'s PKs assume one row per (entity, month) but the source has upstream duplicates. Loader now uses `ON CONFLICT DO NOTHING` — did not attempt to fix the upstream pipeline (out of scope, someone else's team).
3. **~8 rows with literal string `"NaN"` as `artist_uri`** (3 real artist names affected, e.g. "نايزر") — a genuine upstream data-quality issue, tiny blast radius (0.001% of rows, ~3 of 215,725 chunks), not fixed.
4. **Yearly-grain reduction was 4.3-11.2x, not a flat ~12x** as originally assumed — sparse monthly activity per entity, not a bug.
5. **`top-countries` chart bug caught and fixed during testing**: first version returned the same country repeated per-month instead of one bar per country (forgot to aggregate within the year) — fixed with `.values('country_name').annotate(Sum(...))`.
6. **`chat_widget.html` missing `{% load static %}`** — caused a site-wide 500 until fixed (see §6).
7. **Dashboard chart redesign beyond a literal swap**: the dummy dashboard's 3 charts (`processed-over-time`, `layer-distribution`, `pipeline-status`) are pipeline-monitoring concepts (Bronze/Silver/Gold ETL status) that have no equivalent in real Gold data (which is Spotify streaming analytics, not pipeline telemetry). Replaced with 2 real charts (`streams-over-time`, `top-countries`) instead of trying to force a 1:1 swap — flagging this as a content decision, not purely mechanical.
8. **Anthropic API key never materialized** (Pro subscription doesn't cover API billing) — switched to free local Ollama (`llama3.2:3b`) per your call. Trade-off realized in testing: 1 of 4 test questions hallucinated (see §5, test 4) instead of declining — a real limitation of small local models vs. Claude for strict "answer only from context" grounding. If this matters for a real demo, worth revisiting a paid key or a larger local model (e.g. `llama3.1:8b`) later.
9. **Retrieval bias toward the largest chunk table**: `artist_performance` has 151K chunks vs. `country_performance`'s 672, so questions about countries can get swamped by artist-chunk nearest-neighbors (see §5, test 2). Not fixed in this pass — would need either per-source-table retrieval balancing or a query-routing step; flagging as a known limitation.
10. Dev server, Postgres, and Ollama are all running locally for testing — not left running unattended for production use. To stop everything: `pkill -f "manage.py runserver"`, `brew services stop postgresql@17 ollama`.

Nothing was pushed or committed to git in this pass, per your instruction.
