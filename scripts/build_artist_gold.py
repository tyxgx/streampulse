"""
Builds two new Gold tables from the Silver layer (s3://spotify-lake-dev-data/silver/song_charts/)
that the existing Gold tables don't have: real artist-level metrics, and a uri -> track_name
lookup. Neither exists in the current Gold layer -- kpi_artist is a bare country x artist x month
presence table with no metric columns, and kpi_song has no artist link or track name (see
schema.sql's comments). Silver has both: artist_names/artist_uris (pipe '|'-delimited for
collabs, verified pipe-count-aligned) and a real streams count per (country, track, day).

Design choice: a collab track's streams are attributed in FULL to each credited artist (not
split) -- standard practice for this kind of chart analytics, matches how a track can
legitimately count toward multiple artists' totals.

Silver is Hive-partitioned year=YYYY/month=M/, one file per month (~113 files, ~47M rows total).
That's too much to hold in one pandas DataFrame on a laptop (a single month-file with only the
needed columns already runs ~90MB in pandas; all 113 at once would be 10GB+). So each file is
read, exploded, and aggregated down to its own (country, artist, month) grain immediately, and
only the much smaller aggregated result is kept in memory across files.

To populate the local Silver copy:
    aws s3 sync s3://spotify-lake-dev-data/silver/song_charts/ /path/to/.silver_local/

Usage:
    python scripts/build_artist_gold.py --dry-run              # process 1 file, sanity check
    python scripts/build_artist_gold.py                        # process all files, write output

Env vars:
    SILVER_LOCAL_DIR (default: <repo>/.silver_local)
    ARTIST_GOLD_OUTPUT_DIR (default: <repo>/.gold_artist_output)
"""
import argparse
import os
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parent.parent
SILVER_LOCAL_DIR = Path(os.environ.get("SILVER_LOCAL_DIR", REPO_ROOT / ".silver_local"))
OUTPUT_DIR = Path(os.environ.get("ARTIST_GOLD_OUTPUT_DIR", REPO_ROOT / ".gold_artist_output"))

NEEDED_COLUMNS = [
    "date", "country_name", "uri", "track_name",
    "artist_names", "artist_uris", "streams", "rank", "hit_category",
]

_HIT_CATEGORIES = {"Major Hit", "Global Hit"}


def _explode_artists(df):
    """One input row per (country, track, day) with pipe-delimited artist_names/artist_uris
    becomes one output row per contributing artist. artist_uris is the reliable delimiter
    signal (always `spotify:artist:...`, unambiguous); artist_names is split the same way
    UNLESS that produces a different element count than artist_uris -- which happens for a
    small number of rows where a single artist's own name legitimately contains a literal
    '|' (e.g. a bilingual name like "Nizr | نايزر"), not a multi-artist delimiter. For those
    rows the whole artist_names string is kept as one name repeated per uri, rather than
    mis-splitting it (a ~0.003% edge case, not worth a more elaborate parse)."""
    uris = df["artist_uris"].str.split("|")
    names = df["artist_names"].str.split("|")

    mismatched = names.str.len() != uris.str.len()
    if mismatched.any():
        names = names.copy()
        names[mismatched] = [
            [artist_names] * len(uri_list)
            for artist_names, uri_list in zip(
                df.loc[mismatched, "artist_names"], uris[mismatched]
            )
        ]

    exploded = df.assign(artist_name=names, artist_uri=uris).explode(
        ["artist_name", "artist_uri"], ignore_index=True
    )
    return exploded


def process_file(path):
    """Reads one Silver month-file, explodes multi-artist rows, and returns:
    (artist_agg_df, track_name_pairs) where artist_agg_df is already aggregated to
    (country_name, artist_uri, artist_name, year, month) grain -- the raw exploded frame is
    never kept beyond this function."""
    table = pq.read_table(path, columns=NEEDED_COLUMNS)
    df = table.to_pandas()

    df["year"] = df["date"].str.slice(0, 4).astype(int)
    df["month"] = df["date"].str.slice(5, 7).astype(int)

    track_name_pairs = df[["uri", "track_name"]].drop_duplicates(subset="uri")

    exploded = _explode_artists(df)
    exploded["is_hit"] = exploded["hit_category"].isin(_HIT_CATEGORIES)

    agg = exploded.groupby(
        ["country_name", "artist_uri", "artist_name", "year", "month"], as_index=False
    ).agg(
        total_streams=("streams", "sum"),
        track_count=("uri", "nunique"),
        hit_track_count=("is_hit", "sum"),
        best_rank=("rank", "min"),
    )
    return agg, track_name_pairs


def find_silver_files(limit=None):
    files = sorted(SILVER_LOCAL_DIR.glob("year=*/month=*/*.parquet"))
    if limit:
        files = files[:limit]
    return files


def build(dry_run: bool):
    files = find_silver_files(limit=1 if dry_run else None)
    if not files:
        raise SystemExit(f"No Silver parquet files found under {SILVER_LOCAL_DIR}")

    print(f"Processing {len(files)} Silver month-file(s) from {SILVER_LOCAL_DIR}")

    artist_aggs = []
    track_names = {}  # uri -> track_name, first-seen wins

    for i, path in enumerate(files, 1):
        agg, track_pairs = process_file(path)
        artist_aggs.append(agg)
        for uri, name in zip(track_pairs["uri"], track_pairs["track_name"]):
            track_names.setdefault(uri, name)
        print(f"  [{i}/{len(files)}] {path.relative_to(SILVER_LOCAL_DIR)}: "
              f"{len(agg):,} artist-month rows, {len(track_pairs):,} tracks seen "
              f"({len(track_names):,} unique so far)")

    print("\nMerging per-file aggregates...")
    artist_performance = pd.concat(artist_aggs, ignore_index=True)
    # Re-aggregate in case of any cross-file overlap (shouldn't happen -- month partitions
    # don't overlap -- but cheap insurance against a bad assumption).
    artist_performance = artist_performance.groupby(
        ["country_name", "artist_uri", "artist_name", "year", "month"], as_index=False
    ).agg(
        total_streams=("total_streams", "sum"),
        track_count=("track_count", "sum"),
        hit_track_count=("hit_track_count", "sum"),
        best_rank=("best_rank", "min"),
    )
    artist_performance["year_month"] = (
        artist_performance["year"].astype(str) + "-"
        + artist_performance["month"].astype(str).str.zfill(2)
    )

    track_catalog = pd.DataFrame(
        {"uri": list(track_names.keys()), "track_name": list(track_names.values())}
    )

    print(f"\nartist_performance: {len(artist_performance):,} rows "
          f"({artist_performance['artist_uri'].nunique():,} distinct artists)")
    print(f"track_catalog: {len(track_catalog):,} rows")

    if dry_run:
        print("\n--dry-run: not writing output. Sample rows:")
        print(artist_performance.sort_values("total_streams", ascending=False).head(10)
              .to_string(index=False))
        print(track_catalog.head(5).to_string(index=False))
        return

    print(f"\nWriting output to {OUTPUT_DIR}")
    artist_table = pa.Table.from_pandas(artist_performance, preserve_index=False)
    ds.write_dataset(
        artist_table, OUTPUT_DIR / "artist_performance", format="parquet",
        partitioning=["year"], partitioning_flavor="hive",
        existing_data_behavior="overwrite_or_ignore",
    )
    track_table = pa.Table.from_pandas(track_catalog, preserve_index=False)
    (OUTPUT_DIR / "track_catalog").mkdir(parents=True, exist_ok=True)
    pq.write_table(track_table, OUTPUT_DIR / "track_catalog" / "part-0.parquet")
    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                         help="process only 1 Silver file, print sample output, write nothing")
    args = parser.parse_args()
    build(dry_run=args.dry_run)
