"""
Medallion refresh for the StreamPulse lake, in DuckDB.

    Kaggle CSV --> BRONZE (raw, append-only, lineage columns)
                     --> SILVER (cleaned, deduplicated, features; a pure function of Bronze)
                           --> GOLD (monthly aggregates)

Kaggle only serves the whole charts_songs_daily.csv (no delta). Each run reads it and
appends to Bronze only the rows that are NEW or CHANGED since what Bronze already holds
(row-hash anti-join over a lookback window), so Bronze never rewrites history and stays small.
Silver for the affected months is then rebuilt from Bronze (latest version of each row wins),
and Gold for the same months from Silver. Because Silver depends only on Bronze, it can be
rebuilt from scratch at any time (--rebuild-silver) without touching Kaggle.

Lake layout (local dir, synced to/from S3 by the workflow):
    bronze/charts/year=Y/month=M/ingest_<date>_<uuid>.parquet   (immutable, append-only)
    silver/song_charts/year=Y/month=M/*.parquet
    gold/<table>/year=Y/*.parquet
    state/last_run.json

The Silver rules are a line-for-line port of data-lake/glue_jobs/silver_song_charts.py and the
Gold rules of gold_layer_etl.py, so output is comparable to what the Glue jobs produced.
"""
import argparse
import datetime as dt
import json
import logging
import shutil
import time
from pathlib import Path

import duckdb

from country_map import COUNTRY_MAPPING

log = logging.getLogger("refresh")

HIT_CATEGORIES = "('Global Hit', 'Major Hit')"

# Ported from gold_layer_etl.create_label_performance_enhanced.
LABEL_MAPPING = {
    "Taylor Swift": "Republic Records",
    "Lord Huron": "Republic Records",
    "Shubh": "Warner Music India",
    "Diljit Dosanjh": "Warner Music India",
    "Central Cee": "Columbia",
    "Arizona Zervas": "Columbia",
    "Olivia Rodrigo Ps": "Geffen",
    "The Weeknd/Lyric": "Xo / Republic Records",
    "Ap Dhillon": "Republic Records",
    "Karan Aujla": "Rehaan Records",
    "Ritviz": "Sony Music Entertainment India Pvt. Ltd.",
}

# Tables rewritten by year partition; the two small ones are rewritten whole
# because growth_percentage needs every prior month.
PARTITIONED_GOLD = (
    "kpi_song", "kpi_artist", "artist_performance", "label_performance_enhanced",
)
WHOLE_GOLD = ("country_performance", "monthly_trends", "track_catalog")


def q(s):
    return "'" + str(s).replace("'", "''") + "'"


def connect(memory_limit, temp_dir):
    con = duckdb.connect()
    con.execute(f"SET memory_limit={q(memory_limit)}")
    Path(temp_dir).mkdir(parents=True, exist_ok=True)
    con.execute(f"SET temp_directory={q(temp_dir)}")
    con.execute("SET preserve_insertion_order=false")
    return con


def parquet_glob(lake, sub):
    return f"{lake}/{sub}/**/*.parquet"


def has_parquet(lake, sub):
    return any(Path(lake, sub).rglob("*.parquet"))


def initcap_sql(expr):
    # Spark initcap: uppercase first letter of each space-separated word.
    return (f"array_to_string(list_transform(string_split(lower(trim({expr})), ' '), "
            f"w -> upper(w[1:1]) || w[2:]), ' ')")


def map_sql(mapping, key_expr):
    body = " ".join(f"WHEN {q(k)} THEN {q(v)}" for k, v in mapping.items())
    return f"CASE {key_expr} {body} END"


# ---------------------------------------------------------------- Bronze

BRONZE = "bronze/charts"
CSV_TYPES = "{'date':'DATE','peak_date':'DATE','entry_date':'DATE','release_date':'DATE'}"


def csv_source(csv_path):
    return (f"read_csv({q(csv_path)}, header=true, sample_size=200000, types={CSV_TYPES}, ignore_errors=true)")


def bronze_glob(lake):
    return parquet_glob(lake, BRONZE)


def bronze_max_date(con, lake):
    """Latest chart date already in Bronze (None if Bronze is empty)."""
    if not has_parquet(lake, BRONZE):
        return None
    row = con.execute(f"SELECT max(date) FROM read_parquet({q(bronze_glob(lake))}, hive_partitioning=1)").fetchone()
    return row[0] if row and row[0] else None


def _write_bronze(con, lake, table, ingest_date):
    """Append a temp table of new Bronze rows as immutable, uniquely named files."""
    out = Path(lake, BRONZE)
    out.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM {table}) TO {q(out)} (FORMAT parquet, PARTITION_BY (year, month), "
                f"COMPRESSION zstd, APPEND, FILENAME_PATTERN {q('ingest_' + ingest_date + '_{uuid}')})")


def _candidate_sql(csv_path, ingest_date, lo=None, hi=None):
    """Raw CSV rows + lineage columns. Values are kept exactly as read; nothing is cleaned or filtered here."""
    conds = []
    if lo:
        conds.append(f"date >= DATE {q(lo)}")
    if hi:
        conds.append(f"date < DATE {q(hi)}")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    return f"""
        SELECT r.*, hash(r) AS _row_hash, DATE {q(ingest_date)} AS _ingest_date,
               {q(Path(csv_path).name)} AS _source_file,
               year(r.date)::INTEGER AS year, month(r.date)::INTEGER AS month
        FROM {csv_source(csv_path)} AS r {where}"""


def build_bronze(con, lake, csv_path, ingest_date, lookback_days):
    """Append new/changed CSV rows to Bronze. Returns (affected (year, month) list, rows appended)."""
    prev = bronze_max_date(con, lake)
    if prev is None:
        log.info("Bronze empty -> full-history load, one year at a time")
        lo, hi = con.execute(f"SELECT min(date), max(date) FROM {csv_source(csv_path)}").fetchone()
        total = 0
        for year in range(lo.year, hi.year + 1):
            t = time.time()
            con.execute(f"CREATE OR REPLACE TABLE new_bronze AS {_candidate_sql(csv_path, ingest_date, f'{year}-01-01', f'{year + 1}-01-01')}")
            n = con.execute("SELECT count(*) FROM new_bronze").fetchone()[0]
            if n:
                _write_bronze(con, lake, "new_bronze", ingest_date)
            total += n
            log.info("bronze %d: %d rows in %.0fs", year, n, time.time() - t)
        affected = con.execute(f"SELECT DISTINCT year::INTEGER, month::INTEGER FROM read_parquet({q(bronze_glob(lake))}, "
                               "hive_partitioning=1) ORDER BY 1, 2").fetchall()
        return affected, total

    wm = (prev - dt.timedelta(days=lookback_days)).isoformat()
    log.info("Incremental Bronze: rows with date >= %s, keeping only new or changed ones", wm)
    con.execute(f"""
        CREATE OR REPLACE TABLE new_bronze AS
        SELECT c.* FROM ({_candidate_sql(csv_path, ingest_date, wm)}) c
        WHERE NOT EXISTS (
            SELECT 1 FROM read_parquet({q(bronze_glob(lake))}, hive_partitioning=1, union_by_name=true) b
            WHERE b.date >= DATE {q(wm)} AND b.date = c.date AND b.country = c.country
              AND b.uri = c.uri AND b._row_hash = c._row_hash)
        QUALIFY row_number() OVER (PARTITION BY c.date, c.country, c.uri, c._row_hash) = 1""")
    n = con.execute("SELECT count(*) FROM new_bronze").fetchone()[0]
    if n == 0:
        log.info("No new or changed rows - Bronze already up to date")
        return [], 0
    affected = con.execute("SELECT DISTINCT year, month FROM new_bronze ORDER BY 1, 2").fetchall()
    _write_bronze(con, lake, "new_bronze", ingest_date)
    log.info("appended %d rows to Bronze across months %s", n, affected)
    return affected, n


# ---------------------------------------------------------------- Silver

def silver_select(lake, months):
    """Bronze -> cleaned + feature-engineered Silver rows (silver_song_charts.py).

    months: list of (year, month) to rebuild. The latest ingest of a row wins, so a corrected
    Kaggle value replaces the older one in Silver while Bronze keeps both."""
    keys = ", ".join(f"({y},{m})" for y, m in months)
    return f"""
    WITH raw AS (
        SELECT * EXCLUDE (year, month)
        FROM read_parquet({q(bronze_glob(lake))}, hive_partitioning=1, union_by_name=true)
        WHERE (year, month) IN ({keys})
    ),
    deduped AS (
        SELECT * FROM raw
        QUALIFY row_number() OVER (PARTITION BY date, country, uri ORDER BY _ingest_date DESC, rank) = 1
    ),
    mapped AS (
        SELECT d.*, d.country AS market,
               {map_sql(COUNTRY_MAPPING, 'd.country')} AS country_name
        FROM deduped d
    ),
    cleaned AS (
        SELECT * EXCLUDE (artist_names, track_name, label, _row_hash, _ingest_date, _source_file),
               trim(coalesce(artist_names, 'Unknown Artist')) AS artist_names,
               trim(coalesce(track_name, 'Unknown Track'))    AS track_name,
               trim(coalesce(label, 'Independent'))           AS label
        FROM mapped
        WHERE country_name IS NOT NULL AND country_name != 'Global'
    )
    SELECT * EXCLUDE (country),
           year(date)::INTEGER AS year, month(date)::INTEGER AS month, quarter(date)::INTEGER AS quarter,
           CASE WHEN rank <= 10 THEN 'Global Hit' WHEN rank <= 50 THEN 'Major Hit'
                WHEN rank <= 100 THEN 'Popular Track' ELSE 'Charting Track' END AS hit_category,
           {initcap_sql('label')} AS standardized_label,
           round((201 - rank) * 0.4 + (201 - peak_rank) * 0.3
                 + days_on_chart * 0.2 + consecutive_days * 0.1, 2) AS chart_strength_score
    FROM cleaned
    """


def build_silver(con, lake, months):
    """(Re)build Silver for the given months from Bronze. Idempotent: Silver is a pure function of Bronze."""
    silver_dir = Path(lake, "silver/song_charts")
    silver_dir.mkdir(parents=True, exist_ok=True)
    # one year per pass keeps the working set small when many months are rebuilt
    for year in sorted({y for y, _ in months}):
        batch = [(y, m) for y, m in months if y == year]
        t = time.time()
        for y, m in batch:
            shutil.rmtree(silver_dir / f"year={y}" / f"month={m}", ignore_errors=True)
        con.execute(f"COPY ({silver_select(lake, batch)}) TO {q(silver_dir)} "
                    "(FORMAT parquet, PARTITION_BY (year, month), COMPRESSION zstd, OVERWRITE_OR_IGNORE)")
        log.info("silver %d (%d months) done in %.0fs", year, len(batch), time.time() - t)


# ----------------------------------------------------------------- Gold

def silver_view(con, lake, affected):
    keys = ", ".join(f"({y},{m})" for y, m in affected)
    con.execute(f"""
        CREATE OR REPLACE VIEW s AS
        SELECT * EXCLUDE (year, month), year::BIGINT AS year, month::INTEGER AS month
        FROM read_parquet({q(parquet_glob(lake, 'silver/song_charts'))}, hive_partitioning=1, union_by_name=true)
        WHERE (year, month) IN ({keys})
    """)


def gold_new_tables(con, exclude_partial_month_key):
    """Recompute every Gold table for the affected months into new_* temp tables."""
    con.execute(f"""
        CREATE OR REPLACE TABLE new_kpi_song AS
        SELECT year, month::BIGINT AS month, country_name, uri, standardized_label,
               sum(streams)::BIGINT AS total_streams,
               max(CASE WHEN hit_category IN {HIT_CATEGORIES} THEN 1 ELSE 0 END)::INTEGER AS is_hit
        FROM s GROUP BY ALL""")

    con.execute("""
        CREATE OR REPLACE TABLE new_kpi_artist AS
        SELECT DISTINCT year, month::BIGINT AS month, country_name, trim(u) AS artist_uri
        FROM (SELECT year, month, country_name, unnest(string_split(artist_uris, '|')) AS u FROM s)
        WHERE u IS NOT NULL AND trim(u) != ''""")

    # (name, uri) pairs, one row per credited artist per chart row.
    con.execute("""
        CREATE OR REPLACE TABLE artist_rows AS
        SELECT year, month, country_name, streams, rank, uri, hit_category,
               trim(names[i]) AS artist_name, trim(uris[i]) AS artist_uri
        FROM (SELECT *, string_split(artist_names, '|') AS names, string_split(artist_uris, '|') AS uris FROM s),
             unnest(range(1, greatest(len(names), len(uris)) + 1)) AS t(i)""")

    con.execute(f"""
        CREATE OR REPLACE TABLE new_artist_performance AS
        SELECT country_name, artist_uri, artist_name, month::BIGINT AS month,
               sum(streams)::BIGINT AS total_streams,
               count(DISTINCT uri)::BIGINT AS track_count,
               count(*) FILTER (WHERE hit_category IN {HIT_CATEGORIES})::BIGINT AS hit_track_count,
               min(rank)::BIGINT AS best_rank,
               printf('%04d-%02d', year, month) AS year_month, year
        FROM artist_rows WHERE artist_uri IS NOT NULL AND artist_uri != ''
        GROUP BY country_name, artist_uri, artist_name, year, month""")

    label_case = map_sql(LABEL_MAPPING, "standardized_label")
    con.execute(f"""
        CREATE OR REPLACE TABLE new_label_performance_enhanced AS
        SELECT year, month, country_name, coalesce({label_case}, standardized_label) AS standardized_label,
               sum(streams)::BIGINT AS total_streams,
               count(DISTINCT uri)::BIGINT AS active_songs,
               count(DISTINCT artist_names)::BIGINT AS active_artists
        FROM s GROUP BY ALL""")

    con.execute("""
        CREATE OR REPLACE TABLE new_track_catalog AS
        SELECT uri, track_name FROM s
        QUALIFY row_number() OVER (PARTITION BY uri ORDER BY date DESC) = 1""")

    # country_performance / monthly_trends base rows (growth is added after merging with history).
    base_agg = f"""
        SELECT year, month, country_name,
               round(sum(streams), 0)::BIGINT AS total_streams,
               count(DISTINCT uri)::BIGINT AS active_songs,
               count(DISTINCT CASE WHEN hit_category IN {HIT_CATEGORIES} THEN uri END)::BIGINT AS hit_songs,
               round(avg(chart_strength_score), 2) AS avg_chart_strength
        FROM s GROUP BY ALL"""
    con.execute(f"""
        CREATE OR REPLACE TABLE cp_base AS
        SELECT b.*, a.active_artists FROM ({base_agg}) b
        LEFT JOIN (SELECT year, month, country_name, count(DISTINCT artist_uri)::BIGINT AS active_artists
                   FROM (SELECT year, month, country_name, trim(unnest(string_split(artist_uris, '|'))) AS artist_uri FROM s)
                   WHERE artist_uri IS NOT NULL AND artist_uri != '' GROUP BY ALL) a
        USING (year, month, country_name)""")
    con.execute("""
        CREATE OR REPLACE TABLE new_country_performance AS
        SELECT cp.year, cp.month, cp.country_name, cp.total_streams, cp.active_songs, cp.hit_songs,
               cp.avg_chart_strength, cp.active_artists,
               ts.top_song_name, ta.top_artist_name
        FROM cp_base cp
        LEFT JOIN (SELECT year, month, country_name, track_name AS top_song_name FROM (
                       SELECT year, month, country_name, track_name, sum(streams) AS st FROM s
                       WHERE track_name != 'Unknown Track' GROUP BY ALL)
                   QUALIFY row_number() OVER (PARTITION BY year, month, country_name ORDER BY st DESC, track_name ASC) = 1) ts
               USING (year, month, country_name)
        LEFT JOIN (SELECT year, month, country_name, artist_name AS top_artist_name FROM (
                       SELECT year, month, country_name, artist_name, sum(streams) AS st FROM artist_rows
                       WHERE artist_name != 'Unknown Artist' GROUP BY ALL)
                   QUALIFY row_number() OVER (PARTITION BY year, month, country_name ORDER BY st DESC, artist_name ASC) = 1) ta
               USING (year, month, country_name)""")

    # monthly_trends excludes the still-in-progress month (was hardcoded to 2026-05 in the Glue job).
    excl = ""
    if exclude_partial_month_key:
        y, m = exclude_partial_month_key
        excl = f"WHERE NOT (year = {y} AND month = {m})"
    con.execute(f"""
        CREATE OR REPLACE TABLE new_monthly_trends AS
        SELECT cp.year, cp.month, cp.country_name, cp.total_streams, cp.active_songs,
               l.active_labels, cp.hit_songs, cp.avg_chart_strength, cp.active_artists
        FROM cp_base cp
        JOIN (SELECT year, month, country_name, count(DISTINCT standardized_label)::BIGINT AS active_labels
              FROM s GROUP BY ALL) l USING (year, month, country_name)
        {excl.replace('year', 'cp.year').replace('month', 'cp.month')}""")


def write_partitioned(con, lake, table, affected):
    """Merge new_<table> into existing year partitions and rewrite only the affected years."""
    gold_dir = Path(lake, "gold", table)
    years = sorted({y for y, _ in affected})
    keys = ", ".join(f"({y},{m})" for y, m in affected)
    existing = ""
    if has_parquet(lake, f"gold/{table}"):
        existing = (f"SELECT * FROM read_parquet({q(parquet_glob(lake, f'gold/{table}'))}, hive_partitioning=1, "
                    f"union_by_name=true) WHERE year IN ({', '.join(map(str, years))}) "
                    f"AND (year, month) NOT IN ({keys}) UNION ALL BY NAME ")
    con.execute(f"CREATE OR REPLACE TABLE merged AS {existing} SELECT * FROM new_{table}")
    for y in years:
        shutil.rmtree(gold_dir / f"year={y}", ignore_errors=True)
    gold_dir.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY merged TO {q(gold_dir)} (FORMAT parquet, PARTITION_BY (year), COMPRESSION zstd, OVERWRITE_OR_IGNORE)")


def write_whole(con, lake, table, affected, growth):
    """Merge into the full table, recompute month-over-month growth, rewrite everything."""
    gold_dir = Path(lake, "gold", table)
    keys = ", ".join(f"({y},{m})" for y, m in affected)
    have = has_parquet(lake, f"gold/{table}")
    if table == "track_catalog":
        # Latest name per uri: new rows win over history.
        old = (f"UNION ALL SELECT uri, track_name, 1 FROM read_parquet({q(parquet_glob(lake, f'gold/{table}'))})"
               if have else "")
        con.execute(f"""CREATE OR REPLACE TABLE merged AS
            SELECT uri, track_name FROM (SELECT uri, track_name, 0 AS pri FROM new_track_catalog {old})
            QUALIFY row_number() OVER (PARTITION BY uri ORDER BY pri) = 1""")
    else:
        old = (f"SELECT * FROM read_parquet({q(parquet_glob(lake, f'gold/{table}'))}, hive_partitioning=1, "
               f"union_by_name=true) WHERE (year, month) NOT IN ({keys}) UNION ALL BY NAME ") if have else ""
        con.execute(f"CREATE OR REPLACE TABLE merged_raw AS {old} SELECT * FROM new_{table}")
        # Drop stale/derived cols so they're recomputed consistently.
        con.execute("ALTER TABLE merged_raw DROP COLUMN IF EXISTS growth_percentage")
        con.execute("ALTER TABLE merged_raw DROP COLUMN IF EXISTS monthly_total_streams")
        extra_total = ""
        if table == "country_performance":
            extra_total = ", sum(total_streams) OVER (PARTITION BY year, month)::BIGINT AS monthly_total_streams"
        growth_expr = ("round((total_streams - lag(total_streams) OVER w) * 100.0 / "
                       "nullif(lag(total_streams) OVER w, 0), 2)")
        con.execute(f"""CREATE OR REPLACE TABLE merged AS
            SELECT *, {growth_expr} AS growth_percentage {extra_total}
            FROM merged_raw WINDOW w AS (PARTITION BY country_name ORDER BY year, month)""")
    if gold_dir.exists():
        shutil.rmtree(gold_dir)
    gold_dir.mkdir(parents=True)
    if table == "track_catalog":
        con.execute(f"COPY merged TO {q(gold_dir / 'data.parquet')} (FORMAT parquet, COMPRESSION zstd)")
    else:
        con.execute(f"COPY merged TO {q(gold_dir)} (FORMAT parquet, PARTITION_BY (year), COMPRESSION zstd, OVERWRITE_OR_IGNORE)")


def partial_month(con, lake):
    """(year, month) of the latest month in Silver if it is still in progress, else None."""
    d = con.execute(f"SELECT max(date) FROM read_parquet({q(parquet_glob(lake, 'silver/song_charts'))}, "
                    "hive_partitioning=1)").fetchone()[0]
    nxt = (d + dt.timedelta(days=1))
    return (d.year, d.month) if nxt.month == d.month else None


def run(csv_path, lake, memory_limit="5GB", temp_dir=None, exclude_partial_month=True, lookback_days=7,
        rebuild_silver=False, ingest_date=None):
    t0 = time.time()
    lake = str(lake)
    ingest_date = ingest_date or dt.datetime.now(dt.timezone.utc).date().isoformat()
    con = connect(memory_limit, temp_dir or f"{lake}/.duck_tmp")
    previous = bronze_max_date(con, lake)
    bootstrap = previous is None

    affected, appended = build_bronze(con, lake, csv_path, ingest_date, lookback_days)
    if rebuild_silver and not bootstrap:
        # replay: rebuild every month Bronze has, without touching Kaggle data
        affected = con.execute(f"SELECT DISTINCT year::INTEGER, month::INTEGER FROM read_parquet({q(bronze_glob(lake))}, "
                               "hive_partitioning=1) ORDER BY 1, 2").fetchall()
    if not affected:
        return {"status": "up_to_date", "watermark": previous.isoformat() if previous else None}

    build_silver(con, lake, affected)

    # One year of months per pass keeps the unnest/aggregate working set small
    # (the full-history bootstrap would otherwise materialise ~65M artist rows).
    # Chronological order matters: growth_percentage and track_catalog are
    # recomputed against everything already written.
    partial = partial_month(con, lake) if exclude_partial_month else None
    for year in sorted({y for y, _ in affected}):
        batch = [(y, m) for y, m in affected if y == year]
        log.info("gold %d: months %s", year, [m for _, m in batch])
        silver_view(con, lake, batch)
        gold_new_tables(con, partial)
        for t in PARTITIONED_GOLD:
            write_partitioned(con, lake, t, batch)
        for t in WHOLE_GOLD:
            write_whole(con, lake, t, batch, growth=t != "track_catalog")

    new_wm = bronze_max_date(con, lake)
    summary = {
        "status": "refreshed", "previous_watermark": previous.isoformat() if previous else None,
        "watermark": new_wm.isoformat(),
        "bronze_rows_appended": appended, "bronze_bootstrap": bootstrap, "silver_rebuilt_from_bronze": True,
        "affected_months": [f"{y}-{m:02d}" for y, m in affected],
        "seconds": round(time.time() - t0, 1),
        "run_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    Path(lake, "state").mkdir(parents=True, exist_ok=True)
    Path(lake, "state", "last_run.json").write_text(json.dumps(summary, indent=2))
    Path(lake, "state", "affected_partitions.txt").write_text(
        "".join(f"year={y}/month={m}\n" for y, m in affected))
    shutil.rmtree(f"{lake}/.duck_tmp", ignore_errors=True)
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", required=True, help="path to charts_songs_daily.csv")
    p.add_argument("--lake", required=True, help="local lake directory")
    p.add_argument("--memory-limit", default="5GB")
    p.add_argument("--temp-dir")
    p.add_argument("--lookback-days", type=int, default=7,
                   help="re-read this many days before the Bronze watermark (late/corrected rows)")
    p.add_argument("--rebuild-silver", action="store_true",
                   help="replay: rebuild all of Silver and Gold from Bronze (no new Kaggle rows needed)")
    p.add_argument("--keep-partial-month", action="store_true",
                   help="include the in-progress month in monthly_trends")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    print(json.dumps(run(a.csv, a.lake, a.memory_limit, a.temp_dir, not a.keep_partial_month, a.lookback_days,
                         a.rebuild_silver), indent=2))


if __name__ == "__main__":
    main()
