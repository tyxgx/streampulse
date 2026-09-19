"""
Incremental Bronze -> Silver -> Gold refresh for the StreamPulse lake, in DuckDB.

Kaggle serves the whole charts_songs_daily.csv on every download (there is no
delta). This job reads that CSV, keeps only rows at/after the Silver
watermark (max date already in Silver), rebuilds only the months those rows
touch, and rewrites only the Gold partitions for those months. First run
(empty lake) processes the full history.

Lake layout (local dir, synced to/from S3 by the workflow):
    silver/song_charts/year=Y/month=M/*.parquet
    gold/<table>/year=Y/*.parquet
    state/last_run.json

The Silver rules are a line-for-line port of data-lake/glue_jobs/
silver_song_charts.py and the Gold rules of gold_layer_etl.py, so output is
comparable to what the Glue jobs produced.
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


# ---------------------------------------------------------------- Silver

def silver_select(csv_path, min_date):
    """Bronze CSV -> cleaned + feature-engineered Silver rows (silver_song_charts.py)."""
    date_filter = f"WHERE date >= DATE {q(min_date)}" if min_date else ""
    return f"""
    WITH raw AS (
        SELECT * FROM read_csv({q(csv_path)}, header=true, sample_size=200000,
                               types={{'date':'DATE','peak_date':'DATE','entry_date':'DATE','release_date':'DATE'}},
                               ignore_errors=true)
        {date_filter}
    ),
    deduped AS (
        SELECT * FROM raw
        QUALIFY row_number() OVER (PARTITION BY date, country, uri ORDER BY rank) = 1
    ),
    mapped AS (
        SELECT d.*, d.country AS market,
               {map_sql(COUNTRY_MAPPING, 'd.country')} AS country_name
        FROM deduped d
    ),
    cleaned AS (
        SELECT * EXCLUDE (artist_names, track_name, label),
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


def get_watermark(con, lake, lookback_days=0):
    """Latest date in Silver minus a lookback window (None if Silver is empty).

    Kaggle rows can arrive late or get corrected, and the file is ordered by
    country, so a strict "> max(date)" would silently miss them. Re-reading the
    last few days is cheap because affected months are rebuilt idempotently.
    """
    if not has_parquet(lake, "silver/song_charts"):
        return None
    row = con.execute(
        f"SELECT max(date) FROM read_parquet({q(parquet_glob(lake, 'silver/song_charts'))}, "
        "hive_partitioning=1)").fetchone()
    if not row or not row[0]:
        return None
    return (row[0] - dt.timedelta(days=lookback_days)).isoformat()


def build_silver(con, lake, csv_path, watermark):
    """Write Silver for every month touched by rows >= watermark. Returns affected (year, month) list."""
    silver_dir = Path(lake, "silver/song_charts")
    select = silver_select(csv_path, watermark)

    if watermark is None:
        log.info("Silver empty -> full-history load")
        silver_dir.mkdir(parents=True, exist_ok=True)
        con.execute(f"COPY ({select}) TO {q(silver_dir)} "
                    "(FORMAT parquet, PARTITION_BY (year, month), COMPRESSION zstd, OVERWRITE_OR_IGNORE)")
        return con.execute(
            f"SELECT DISTINCT year, month FROM read_parquet({q(parquet_glob(lake, 'silver/song_charts'))}, "
            "hive_partitioning=1) ORDER BY 1, 2").fetchall()

    log.info("Incremental load: rows with date >= %s", watermark)
    con.execute(f"CREATE OR REPLACE TABLE new_rows AS {select}")
    n = con.execute("SELECT count(*) FROM new_rows").fetchone()[0]
    if n == 0:
        log.info("No rows at/after watermark - Silver already up to date")
        return []
    affected = con.execute("SELECT DISTINCT year, month FROM new_rows ORDER BY 1, 2").fetchall()
    log.info("%d new rows across months %s", n, affected)

    # Keep the already-processed rows of the affected months (before the
    # watermark day, which is re-read so re-runs and late corrections are idempotent).
    con.execute(f"""
        CREATE OR REPLACE TABLE keep AS
        SELECT * FROM read_parquet({q(parquet_glob(lake, 'silver/song_charts'))}, hive_partitioning=1, union_by_name=true)
        WHERE date < DATE {q(watermark)} AND (year, month) IN (SELECT year, month FROM new_rows)
    """)
    for y, m in affected:
        shutil.rmtree(silver_dir / f"year={y}" / f"month={m}", ignore_errors=True)
    con.execute(f"""
        COPY (SELECT * FROM keep UNION ALL BY NAME SELECT * FROM new_rows)
        TO {q(silver_dir)} (FORMAT parquet, PARTITION_BY (year, month), COMPRESSION zstd, OVERWRITE_OR_IGNORE)
    """)
    return affected


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


def run(csv_path, lake, memory_limit="5GB", temp_dir=None, exclude_partial_month=True, lookback_days=7):
    t0 = time.time()
    lake = str(lake)
    con = connect(memory_limit, temp_dir or f"{lake}/.duck_tmp")
    latest_before = get_watermark(con, lake)
    watermark = get_watermark(con, lake, lookback_days)
    affected = build_silver(con, lake, csv_path, watermark)
    if not affected:
        return {"status": "up_to_date", "watermark": latest_before}

    silver_view(con, lake, affected)
    gold_new_tables(con, partial_month(con, lake) if exclude_partial_month else None)
    for t in PARTITIONED_GOLD:
        write_partitioned(con, lake, t, affected)
    for t in WHOLE_GOLD:
        write_whole(con, lake, t, affected, growth=t != "track_catalog")

    new_wm = get_watermark(con, lake)
    summary = {
        "status": "refreshed", "previous_watermark": latest_before, "watermark": new_wm,
        "reprocessed_from": watermark,
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
                   help="re-read this many days before the Silver watermark (late/corrected rows)")
    p.add_argument("--keep-partial-month", action="store_true",
                   help="include the in-progress month in monthly_trends")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    print(json.dumps(run(a.csv, a.lake, a.memory_limit, a.temp_dir, not a.keep_partial_month, a.lookback_days), indent=2))


if __name__ == "__main__":
    main()
