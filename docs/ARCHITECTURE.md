# Architecture

StreamPulse has three parts: a **data pipeline** that runs once a day, a **static dashboard** that reads precomputed JSON, and a
**chat assistant** behind a Lambda. Nothing runs when nobody is visiting, except the daily job.

## 1. Components

| Part | Technology | Where it runs |
|---|---|---|
| Ingest and transform | Python, DuckDB 1.5.5 | GitHub Actions (`daily-refresh.yml`), ubuntu-latest |
| Lake | Parquet in S3 (`streampulse-lake-922120357133`, ap-south-1) | AWS S3 |
| Site data builder | `pipeline/build_site_data.py` (DuckDB) | same workflow run |
| Dashboard | Vanilla JS, Chart.js 4, D3 7, TopoJSON | S3 website bucket (`streampulse-site-922120357133`) |
| Assistant API | Python 3.12, LangGraph, OpenAI-compatible SDK, DuckDB | Lambda container, arm64, 1.5 GB, 45 s timeout, Function URL |
| LLMs | Groq (gpt-oss-120b, gpt-oss-20b, optionally qwen) then Gemini (3.1 flash-lite, 3 flash preview) | external APIs, free tiers |
| Rate limiter | DynamoDB on-demand, atomic counters with TTL | AWS |
| Secrets | SSM Parameter Store SecureString under `/streampulse/` | AWS |
| IaC | Terraform (local state) | `infra/terraform/` |
| Build and deploy | GitHub Actions with OIDC (no long-lived AWS keys) | GitHub |

## 2. Data flow

```
Kaggle: charts_songs_daily.csv (~10 GB, whole file every time, no delta)
  │  daily-refresh.yml, cron 03:30 UTC (also manual dispatch)
  ▼
refresh.py
  keeps rows with date >= (max date in Silver) - 7 days      (file is ordered by country, so a strict > would miss late rows)
  rebuilds only the affected months  -> silver/song_charts/year=Y/month=M/*.parquet
  rebuilds Gold for those months     -> gold/<table>/year=Y/*.parquet       (7 tables)
  writes state/last_run.json (watermark) and the list of affected partitions
  ▼
build_site_data.py  (reads the full Silver, ~44M rows, about 2 minutes)
  intermediates: daily_country, track_all, track_month, track_country, track_country_day, track_day, track_artist, artist_all
  ├─ site JSON  (8 MB)  -> s3://site/data/*.json        read by the browser
  └─ chat Parquet (63 MB) -> s3://lake/chat/*.parquet   read by the Lambda; _version.json is written last
  ▼
dashboard files synced to s3://site/   (index.html, app.js, chat.js, style.css, config.js, world.json)
```

The workflow stops early (`status: up_to_date`) when Kaggle has nothing newer than the Silver watermark.

### S3 layout

```
streampulse-lake-922120357133        private, SSE-S3, abort-incomplete-multipart lifecycle
  silver/song_charts/year=/month=/    cleaned daily chart rows (about 1.3 GB)
  gold/<table>/year=/                 monthly aggregates (about 80 MB)
  state/last_run.json                 watermark, last run summary
  chat/*.parquet, chat/_version.json  chatbot tables and a version marker

streampulse-site-922120357133        public read, S3 website hosting, CORS GET/HEAD
  index.html app.js chat.js config.js style.css world.json
  data/meta.json overview.json countries.json artists.json tracks.json trending.json labels.json seasonality.json health.json
  data/country/<market>.json (72)  data/artist/<n>.json (300)  data/track/<id>.json (500)
```

### Data model (what the tools and JSON are built from)

| Table | Grain | Used for |
|---|---|---|
| `daily_country` | date x market | totals, windows, trends, freshness per market |
| `track_all` | track (Spotify URI) | all-time streams, best rank, days charted, first and last seen |
| `track_month` | track x month | monthly trends for tracks and artists |
| `track_country` | track x market | per-market rankings, all time |
| `track_country_day` | track x market x day, last 60 days | per-country recent windows |
| `track_day` | track x day, last ~210 days | global recent windows, movers |
| `track_artist` | track x credited artist | artist rollups (collaborations count for every credited artist) |
| `artist_all` | artist | all-time artist rank and totals |

Gold (monthly `country_performance`, `monthly_trends`, `kpi_*`, `label_performance_enhanced`, `artist_performance`, `track_catalog`) is
produced and kept for compatibility with the original lake design; the dashboard and assistant read the Silver-derived tables above
because they need daily resolution.

## 3. The dashboard

A hash-routed single page app: `#/` (home), `#/overview`, `#/map`, `#/countries`, `#/country/<m>`, `#/artists`, `#/artist/<n>`,
`#/tracks`, `#/track/<id>`, `#/trending`, `#/labels`, `#/seasonality`, `#/health`.

- Pages fetch their JSON, render with template strings (all data escaped), and draw charts with Chart.js. The map is a D3 choropleth
  over TopoJSON (`world.json`, 110 m). Singapore and Hong Kong, too small for that resolution, are drawn as markers.
- Theme is locked to Spotify's palette (`#121212`, `#181818`, `#1DB954`, `#B3B3B3`). Typography is Geist and Geist Mono. Icons are Phosphor Light.
- Motion is limited to scroll reveals, count-up, a drawing chart and button hover. All of it is off under `prefers-reduced-motion`.
- `chat.js` renders the assistant widget only when `config.js` defines `window.SP_API`.
- Data health is a page of its own: the pipeline's checks (freshness, markets on the latest day, row count, "Unknown Track" share,
  missing rank or artist) and the list of markets whose charts stopped.

## 4. The assistant

See [CHATBOT.md](CHATBOT.md). In short: `POST /ask` to a Lambda Function URL. The handler loads the chat Parquet from S3 into `/tmp`
on a cold start (re-checks `_version.json` every 15 minutes), reads the LLM keys from SSM, enforces limits in DynamoDB, runs the agent,
and logs one JSON line per request to CloudWatch.

## 5. Security and abuse controls

- **No long-lived AWS keys in CI.** GitHub OIDC role `streampulse-github-pipeline`. Its trust policy accepts this repository in both the
  classic and the immutable subject format. It can read and write the two buckets, push to one ECR repo, and update one Lambda.
- **Lambda role** can read `s3://lake/chat/*`, read `/streampulse/*` SSM parameters, update one DynamoDB table and write its own logs.
- **Function URL is public** (auth NONE) with CORS restricted to the site origin and `http://localhost:8787`. Protection is in the
  application: 30 questions per IP per hour and 1,500 per day, atomic in DynamoDB; IPs are stored only as a truncated SHA-256.
- **Input handling.** Questions are capped at 600 characters, history at 6 turns, control characters stripped. Injection and secret
  requests are refused before any model call. Tool arguments from the model are whitelisted and type-checked; a tool failure returns a
  message, never a stack trace.
- **Secrets** live in SSM SecureString. They are never in Git, Terraform state, logs or the image.
- **Budget.** A gross-usage budget ($3 per month, credits not netted out) emails at 40 % forecast and 100 % actual.

## 6. Cost model (estimates)

| Item | Expectation |
|---|---|
| GitHub Actions | free (public repo) |
| Lambda | free tier covers demo traffic; each warm question uses about 2 s of 1.5 GB |
| S3 | about 1.5 GB lake plus 7 MB site: cents per month, plus requests |
| ECR | about 220 MB image, last 5 kept: cents per month |
| DynamoDB, CloudWatch, SSM | inside free tier at this scale |
| Groq, Gemini | free tiers (the real limit on throughput) |

The earlier version of this project ran on EC2. Its stopped instance, idle Elastic IP and disk alone cost about $0.28 per day, which is
why it was terminated.
