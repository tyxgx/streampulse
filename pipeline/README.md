# pipeline/ - medallion lake refresh (DuckDB)

Replaces the Glue-based jobs with one DuckDB script that runs on a free GitHub Actions runner every day.
No EC2, no Glue; the only AWS cost is S3 storage.

```
Kaggle charts_songs_daily.csv  --->  BRONZE (raw, append-only)  --->  SILVER (clean, features)  --->  GOLD (7 tables)  --->  serving JSON / Parquet
   (whole file, no delta)             year=/month= parquet              rebuilt from Bronze              year= parquet           build_site_data.py
```

## How the incremental load works
- **Bronze.** Kaggle only serves the whole ~10 GB CSV. The job reads it and takes rows with `date >= (latest Bronze date) - 7 days`
  (the file is ordered by country, so a strict `>` would miss late or corrected rows). Each row gets `_row_hash = hash(whole row)`,
  `_ingest_date` and `_source_file`; rows whose `(date, country, uri, _row_hash)` already exist in Bronze are dropped. What remains is new or
  corrected data, appended as new files (`ingest_<date>_<uuid>.parquet`). Nothing in Bronze is ever rewritten. An unchanged day appends
  nothing and the run ends with `status: up_to_date`.
- **Silver.** The months that received new Bronze rows are rebuilt from Bronze: latest ingest of each `(date, country, uri)` wins, unmapped
  markets and `Global` are dropped, names are trimmed, and `hit_category`, `chart_strength_score` and `standardized_label` are added.
  Silver is a pure function of Bronze, so it is idempotent and can be rebuilt completely with `--rebuild-silver`.
- **Gold.** The same months are recomputed from Silver and only the affected `year=` partitions are rewritten. `country_performance`,
  `monthly_trends` and `track_catalog` are small and rewritten whole, because month-over-month `growth_percentage` needs the previous month.
  `monthly_trends` leaves out a month that is still in progress.
- **Empty Bronze** means a full-history bootstrap, one year at a time to keep memory low (the first run, 2026-10-02: 44,620,907 rows, 28 minutes in CI).

```
python refresh.py --csv charts_songs_daily.csv --lake ./lake                    # daily
python refresh.py --csv charts_songs_daily.csv --lake ./lake --rebuild-silver   # replay Silver and Gold from Bronze
python build_site_data.py --lake ./lake --out ./site_data --chat-out ./chat_data
```

Limits: a correction that arrives more than 7 days after the day it corrects is not noticed (raise `--lookback-days`); the CSV is read with
`ignore_errors=true`, so a malformed line is skipped before Bronze; column types are inferred from a sample of the file.

## Correctness
`tests/` (11) checks that (a) an incremental run gives **exactly** the same tables as a from-scratch run,
(b) re-running is idempotent, (c) late-arriving rows are picked up, (d) unmapped/`Global` markets are
dropped from Silver but kept in Bronze, (e) the partial month is excluded from `monthly_trends`,
(f) Bronze never rewrites files and an unchanged run appends nothing, (g) a corrected row becomes a new Bronze version and Silver takes the latest,
(h) deleting Silver and Gold and replaying from Bronze reproduces them exactly, (i) track names resolve deterministically in the serving layer. Output schemas match the existing
Gold tables column-for-column and type-for-type.

Silver/Gold rules are a port of `data-lake/glue_jobs/*.py`. `artist_performance` and `track_catalog` were
not in those scripts (they came from the team's Gold layer), so they are re-derived here:
`track_count` = distinct tracks, `hit_track_count` = chart rows in the top 50, `best_rank` = min rank.

```
cd pipeline && python -m pytest -q tests
python refresh.py --csv /path/charts_songs_daily.csv --lake ./lake
```

## Setup for the workflow (`.github/workflows/daily-refresh.yml`)
AWS access uses GitHub OIDC; there are no AWS keys to store. `infra/terraform` creates the lake and site buckets and the role
`streampulse-github-pipeline`. The only repository secrets are the Kaggle credentials:

| Kind | Name | Value |
|---|---|---|
| Secret | `KAGGLE_USERNAME`, `KAGGLE_KEY` | Kaggle API credentials |

Bucket names and region are set in the workflow `env`. Run the workflow once by hand (**Actions → daily-refresh → Run workflow**); after that the
03:30 UTC schedule takes over. The first run on an empty Bronze is a full-history bootstrap.

After the Silver/Gold refresh the same job runs `build_site_data.py` (dashboard JSON plus the chatbot Parquet) and publishes to S3.
See [../docs/OPERATIONS.md](../docs/OPERATIONS.md) for the runbook and [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) for the data flow.

`duckdb` is pinned to 1.5.5 in `requirements.txt` because 1.5.6 fails the tests with an internal optimizer error.
