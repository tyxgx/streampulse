# pipeline/ — daily incremental lake refresh (DuckDB)

Replaces the Glue-based Silver/Gold jobs with one DuckDB script that runs on a free GitHub Actions
runner every day. No EC2, no Glue; the only AWS cost is S3 storage.

```
Kaggle charts_songs_daily.csv  ──►  Silver (clean + features)  ──►  Gold (7 tables)  ──►  S3
   (whole file, no delta)            year=/month= parquet           year= parquet
```

## How the incremental load works
- Kaggle only serves the whole ~10 GB CSV, so the job downloads it, then keeps rows with
  `date >= max(date in Silver) - 7 days`. The 7-day lookback catches late or corrected rows
  (the file is ordered by country, so a strict `> max(date)` would miss them).
- Only the months those rows touch are rebuilt in Silver; Gold is rebuilt for the same months and only
  the affected `year=` partitions are rewritten. `country_performance`, `monthly_trends` and
  `track_catalog` are small and rewritten whole, because month-over-month `growth_percentage`
  needs the previous month.
- `monthly_trends` leaves out the still-in-progress month (the Glue job hardcoded 2026-05 for this).
- Empty lake => full-history bootstrap (same code path).

## Correctness
`tests/` checks that (a) an incremental run gives **exactly** the same tables as a from-scratch run,
(b) re-running is idempotent, (c) late-arriving rows are picked up, (d) unmapped/`Global` markets are
dropped, (e) the partial month is excluded from `monthly_trends`. Output schemas match the existing
Gold tables column-for-column and type-for-type.

Silver/Gold rules are a port of `data-lake/glue_jobs/*.py`. `artist_performance` and `track_catalog` were
not in those scripts (they came from the team's Gold layer), so they are re-derived here:
`track_count` = distinct tracks, `hit_track_count` = chart rows in the top 50, `best_rank` = min rank.

```
cd pipeline && python -m pytest -q tests
python refresh.py --csv /path/charts_songs_daily.csv --lake ./lake
```

## One-time setup for the workflow (`.github/workflows/daily-refresh.yml`)
Repo **Settings → Secrets and variables → Actions**:

| Kind | Name | Value |
|---|---|---|
| Variable | `LAKE_BUCKET` | `streampulse-lake-dev-data` |
| Variable | `AWS_REGION` | `ap-south-1` |
| Secret | `KAGGLE_USERNAME`, `KAGGLE_KEY` | Kaggle API credentials |
| Secret | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | a dedicated IAM user limited to the lake bucket |

Least-privilege policy for that IAM user (`s3:ListBucket` on the bucket, get/put/delete on `gold/*`,
`silver/*`, `state/*`). Then run the workflow once by hand (**Actions → daily-refresh → Run workflow**)
for the full bootstrap; after that the 03:30 UTC schedule takes over.
