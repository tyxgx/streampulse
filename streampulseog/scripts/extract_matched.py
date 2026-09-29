"""Extract FULL rows (all columns) for our matched tracks from an external audio-feature dataset,
via remote DuckDB reads - no full dataset download.

Unlike join_test.py (which only pulled the id column to measure coverage), this pulls every column
we need and writes a small local parquet: data/processed/<source>_matched.parquet.

Gildas is a single ~56M-row file: expect ~2 min (join_test.py took 117s for id-only; full columns
will be somewhat slower but still small, since track_id IN (...) still prunes to ~166k rows).
Ozefe is 10 x ~1.2GB files and the CDN is heavily throttled (measured ~127KB/s even authenticated
on 2026-09-29) - pulling the id column alone took ~73 min (join_ozefe_v2 log), so pulling ALL
17 columns for ~87% of rows will be much slower, possibly hours. Runs file-by-file and appends
after each file, so a Ctrl+C keeps whatever finished so far (rerun to resume: already-matched
track_ids are skipped in later files).

Usage:
    bash run_logs/run.sh extract_gildas "python3 scripts/extract_matched.py --source gildas"
    bash run_logs/run.sh extract_ozefe "python3 scripts/extract_matched.py --source ozefe --per-file"
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
TMP = ROOT / "data" / "tmp"

HF = "https://huggingface.co/datasets"
# Only numeric/audio columns - we already have track/artist/album names from our own Silver data.
# Gildas is a 4.3 GB file behind the same throttled HF xet-bridge CDN as ozefe; selecting fewer
# columns cuts how much of it a remote columnar read has to pull.
GILDAS_COLS = [
    "track_id", "track_popularity", "album_popularity", "artist_popularity", "artist_followers",
    "duration_ms", "explicit", "tempo", "key", "mode", "danceability", "energy", "loudness",
    "speechiness", "acousticness", "instrumentalness", "liveness", "valence",
]
SOURCES = {
    "gildas": {
        "reader": f"read_parquet('{HF}/GildasLeDrogoff/spotify-huge-track-analysis-dataset/resolve/main/spotify-huge-audio-features.parquet')",
        "id": "track_id",
        "usable": None,
        "cols": GILDAS_COLS,
        "order_by": "track_popularity DESC",  # dedupe: keep most-popular artist credit per track
    },
    "ozefe": {
        "reader": "read_parquet('hf://datasets/ozefe/spotify_audio_features/data/*.parquet')",
        "glob": "hf://datasets/ozefe/spotify_audio_features/data/*.parquet",
        "id": "id",
        "usable": "null_response = 0",
        "order_by": None,  # already deduped at source (one row per track)
    },
}


def our_tracks(con: duckdb.DuckDBPyConnection) -> Path:
    out = PROCESSED / "our_tracks.parquet"
    if not out.exists():
        raise SystemExit(f"{out} missing - run join_test.py first (it builds this file)")
    return out


def extract_single(name: str, con: duckdb.DuckDBPyConnection, spec: dict, out: Path) -> None:
    rd, col = spec["reader"], spec["id"]
    where = f"AND ({spec['usable']})" if spec["usable"] else ""
    select_list = ", ".join(spec["cols"]) if spec.get("cols") else "*"
    if spec["order_by"]:
        con.execute(f"""
            COPY (
              SELECT {select_list} FROM (
                SELECT {select_list}, ROW_NUMBER() OVER (PARTITION BY {col} ORDER BY {spec['order_by']}) AS rn
                FROM {rd}
                WHERE {col} IN (SELECT track_id FROM ours) {where}
              ) WHERE rn = 1
            ) TO '{out}' (FORMAT parquet)""")
    else:
        con.execute(f"""
            COPY (SELECT {select_list} FROM {rd} WHERE {col} IN (SELECT track_id FROM ours) {where})
            TO '{out}' (FORMAT parquet)""")


def extract_per_file(name: str, con: duckdb.DuckDBPyConnection, spec: dict, out: Path) -> None:
    col = spec["id"]
    where = f"AND ({spec['usable']})" if spec["usable"] else ""
    files = [r[0] for r in con.execute(f"SELECT file FROM glob('{spec['glob']}') ORDER BY file").fetchall()]
    print(f"[{name}] {len(files)} files", flush=True)

    # resume support: load whatever we already extracted (from a prior interrupted run)
    if out.exists():
        con.execute(f"CREATE OR REPLACE TABLE matched AS SELECT * FROM read_parquet('{out}')")
        n0 = con.execute("SELECT count(*) FROM matched").fetchone()[0]
        print(f"[{name}] resuming, {n0} rows already extracted", flush=True)
    else:
        con.execute(f"CREATE OR REPLACE TABLE matched AS SELECT * FROM read_parquet(files[0]) LIMIT 0")

    for i, f in enumerate(files, 1):
        tf = time.time()
        con.execute(f"""
            INSERT INTO matched
            SELECT * FROM read_parquet('{f}')
            WHERE {col} IN (SELECT track_id FROM ours) {where}
              AND {col} NOT IN (SELECT {col} FROM matched)""")
        n = con.execute("SELECT count(*) FROM matched").fetchone()[0]
        con.execute(f"COPY matched TO '{out}' (FORMAT parquet)")  # checkpoint after every file
        print(f"[{name}] file {i}/{len(files)} done in {time.time() - tf:.0f}s | rows so far {n} | {f.split('/')[-1]}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, choices=list(SOURCES))
    ap.add_argument("--per-file", action="store_true")
    a = ap.parse_args()
    PROCESSED.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit='2GB'; SET threads=2; SET temp_directory='{TMP}'")
    ours = our_tracks(con)
    con.execute(f"CREATE OR REPLACE TABLE ours AS SELECT track_id FROM read_parquet('{ours}')")
    spec = SOURCES[a.source]
    out = PROCESSED / f"{a.source}_matched.parquet"
    t = time.time()
    if a.per_file and spec.get("glob"):
        extract_per_file(a.source, con, spec, out)
    else:
        extract_single(a.source, con, spec, out)
    n = con.execute(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0]
    print(f"[{a.source}] DONE: {n} rows -> {out} ({time.time() - t:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
