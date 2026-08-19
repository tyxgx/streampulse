"""
Loads the Gold layer into Postgres tables defined in schema.sql.
Truncate-and-reload per table (static one-time load, not incremental).

Reads from a local copy of s3://spotify-lake-dev-data/gold/ (see
GOLD_LOCAL_DIR) via pyarrow.dataset, which reads an entire Hive-partitioned
table in one pass and auto-derives the partition columns (year, and month
for the two-level-partitioned tables) from the year=YYYY/[month=M/] folder
structure — much faster than looping over individual S3 GetObject calls per
part-file.

To populate the local copy:
    aws s3 sync s3://spotify-lake-dev-data/gold/ /path/to/.gold_local/

Usage:
    python scripts/load_gold_to_postgres.py --dry-run
    python scripts/load_gold_to_postgres.py   # requires PG* env vars set

Env vars (real run only):
    PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD
    GOLD_LOCAL_DIR (default: <repo>/.gold_local/gold)
"""
import argparse
import os
from pathlib import Path

import pyarrow.dataset as ds

GOLD_LOCAL_DIR = Path(
    os.environ.get("GOLD_LOCAL_DIR", Path(__file__).resolve().parent.parent / ".gold_local")
)

# (subdir, postgres table, partition-levels, primary-key-columns)
# partition-levels: 1 = S3 partitioned by year=YYYY/ only (month is an
# ordinary column in the parquet); 2 = partitioned by year=YYYY/month=M/
# (both year and month are derived from the path, no month column in the
# parquet itself).
#
# PK columns are used for an ON CONFLICT DO NOTHING upsert: the real Gold
# data has duplicate (key) rows across part-files (upstream reprocessing
# splits the same group across files without merging) — first-seen wins.
TABLES = [
    ("country_performance", "country_performance", 1, ["country_name", "year_month"]),
    ("kpi_artist", "kpi_artist", 2, ["country_name", "artist_uri", "year_month"]),
    ("kpi_song", "kpi_song", 2, ["country_name", "uri", "year_month"]),
    ("label_performance_enhanced", "label_performance_enhanced", 1, ["standardized_label", "country_name", "year_month"]),
    ("monthly_trends", "monthly_trends", 1, ["country_name", "year_month"]),
    ("artist_performance", "artist_performance", 1, ["country_name", "artist_uri", "year_month"]),
    ("track_catalog", "track_catalog", 0, ["uri"]),
]

BATCH_SIZE = 20_000


def read_table_df(subdir, partition_levels):
    path = GOLD_LOCAL_DIR / subdir
    # partition_levels 0 = unpartitioned (track_catalog): no year=/month=
    # folders, no year/year_month columns to derive.
    partitioning = "hive" if partition_levels > 0 else None
    dataset = ds.dataset(str(path), format="parquet", partitioning=partitioning)
    df = dataset.to_table().to_pandas()
    if partition_levels == 0:
        return df
    df["year"] = df["year"].astype(int)
    if partition_levels == 2:
        df["month"] = df["month"].astype(int)
    df["year_month"] = df["year"].astype(str) + "-" + df["month"].astype(int).astype(str).str.zfill(2)
    return df


def load(dry_run: bool):
    conn = None
    if not dry_run:
        import psycopg2
        from psycopg2.extras import execute_values

        conn = psycopg2.connect(
            host=os.environ["PGHOST"],
            port=os.environ.get("PGPORT", 5432),
            dbname=os.environ["PGDATABASE"],
            user=os.environ["PGUSER"],
            password=os.environ["PGPASSWORD"],
        )

    for subdir, table_name, partition_levels, pk_cols in TABLES:
        print(f"\n=== {table_name} ({subdir}) ===")
        df = read_table_df(subdir, partition_levels)
        print(f"  {len(df):,} row(s) read from local parquet dataset")
        print(f"  columns: {list(df.columns)}")

        if dry_run:
            print(f"  sample rows:\n{df.head(3).to_string(index=False)}")
            continue

        cur = conn.cursor()
        cur.execute(f"TRUNCATE TABLE {table_name}")
        conflict_clause = f"ON CONFLICT ({','.join(pk_cols)}) DO NOTHING"
        cols = list(df.columns)
        col_list = ",".join(cols)
        rows = [tuple(r) for r in df.itertuples(index=False, name=None)]

        # execute_values pages internally (default page_size=100), so
        # cur.rowcount after the call only reflects the LAST page — not the
        # cumulative total. Count the table directly instead.
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            execute_values(
                cur,
                f"INSERT INTO {table_name} ({col_list}) VALUES %s {conflict_clause}",
                batch,
                page_size=BATCH_SIZE,
            )
        conn.commit()
        cur.execute(f"SELECT count(*) FROM {table_name}")
        inserted = cur.fetchone()[0]
        skipped_dupes = len(rows) - inserted
        print(f"  processed {len(rows):,} rows, {skipped_dupes:,} duplicate-key "
              f"rows skipped, {inserted:,} loaded into {table_name}")

    if conn:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    load(dry_run=args.dry_run)
