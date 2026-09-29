"""Join-coverage test: how many of OUR Spotify chart tracks exist in an external audio-feature dataset.

Only the id column is read (DuckDB remote range reads on the HuggingFace parquet) - nothing big is
downloaded. Results are printed and saved to docs/results/join_<source>.json.

Usage (from the repo root, via the logger):
    bash run_logs/run.sh join_ozefe "python3 scripts/join_test.py --source ozefe"
    --source ozefe | gildas | maharshi | all
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
RESULTS = ROOT / "docs" / "results"
TMP = ROOT / "data" / "tmp"

# Our chart data (Silver). First existing path wins. aws-backup = newest (to 2026-09-17).
SILVER_CANDIDATES = [
    "/Users/uttkarshtyagi/job/projects/streamPulse/aws-backup-2026-09-19/streampulse-lake-dev-data/silver/song_charts/**/*.parquet",
    "/Users/uttkarshtyagi/job/projects/streamPulse/.silver_local/**/*.parquet",
]

HF = "https://huggingface.co/datasets"
# name -> reader SQL, id column, optional "usable rows" filter, note
SOURCES = {
    "gildas": {
        "reader": f"read_parquet('{HF}/GildasLeDrogoff/spotify-huge-track-analysis-dataset/resolve/main/spotify-huge-audio-features.parquet')",
        "id": "track_id",
        "usable": None,
        "note": "56M rows, CC-BY-NC-4.0, card says only 'derived from Spotify' (source unclear).",
    },
    "ozefe": {
        "reader": "read_parquet('hf://datasets/ozefe/spotify_audio_features/data/*.parquet')",
        "glob": "hf://datasets/ozefe/spotify_audio_features/data/*.parquet",
        "id": "id",
        "usable": "null_response = 0",
        "note": "256M rows, license 'other' (spotify-developer-terms); card says raw data = Anna's Archive Spotify scrape.",
    },
    "maharshi": {
        "reader": f"read_csv('{HF}/maharshipandya/spotify-tracks-dataset/resolve/main/dataset.csv')",
        "id": "track_id",
        "usable": None,
        "note": "114k rows, bsd, small.",
    },
}


def our_tracks(con: duckdb.DuckDBPyConnection) -> Path:
    """Materialise one row per track we have (small parquet) so later tests are instant."""
    out = PROCESSED / "our_tracks.parquet"
    if out.exists():
        return out
    PROCESSED.mkdir(parents=True, exist_ok=True)
    for pattern in SILVER_CANDIDATES:
        try:
            con.execute(f"""
                COPY (
                  SELECT replace(uri,'spotify:track:','') AS track_id, count(*) AS chart_rows,
                         min(year) AS first_year, max(year) AS last_year, sum(streams) AS streams
                  FROM read_parquet('{pattern}', hive_partitioning=1) GROUP BY 1
                ) TO '{out}' (FORMAT parquet)""")
            print(f"built {out}")
            return out
        except Exception as exc:  # try the next candidate
            print(f"silver candidate failed: {str(exc)[:160]}")
    raise SystemExit("no Silver parquet found - fix SILVER_CANDIDATES")


def run(name: str, con: duckdb.DuckDBPyConnection, ours: Path, per_file: bool = False) -> dict:
    spec = SOURCES[name]
    rd, col = spec["reader"], spec["id"]
    t = time.time()
    con.execute(f"CREATE OR REPLACE TABLE ours AS SELECT * FROM read_parquet('{ours}')")
    tot = con.execute("SELECT count(*), sum(chart_rows), sum(streams) FROM ours").fetchone()
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {rd}").fetchall()]
    print(f"[{name}] columns: {cols}", flush=True)
    where = f"AND ({spec['usable']})" if spec["usable"] else ""
    if per_file and spec.get("glob"):
        # one parquet file at a time, with a progress line after each (visible progress, partial results)
        files = [r[0] for r in con.execute(f"SELECT file FROM glob('{spec['glob']}') ORDER BY file").fetchall()]
        print(f"[{name}] {len(files)} files", flush=True)
        con.execute("CREATE OR REPLACE TABLE hit (track_id VARCHAR)")
        for i, f in enumerate(files, 1):
            tf = time.time()
            con.execute(f"""
                INSERT INTO hit
                SELECT DISTINCT {col} FROM read_parquet('{f}')
                WHERE {col} IN (SELECT track_id FROM ours) {where}
                  AND {col} NOT IN (SELECT track_id FROM hit)""")
            n = con.execute("SELECT count(*) FROM hit").fetchone()[0]
            print(f"[{name}] file {i}/{len(files)} done in {time.time() - tf:.0f}s | matched so far {n} | {f.split('/')[-1]}", flush=True)
    else:
        con.execute(f"""
            CREATE OR REPLACE TABLE hit AS
            SELECT DISTINCT {col} AS track_id FROM {rd}
            WHERE {col} IN (SELECT track_id FROM ours) {where}""")
    m = con.execute("""
        SELECT count(*), sum(o.chart_rows), sum(o.streams)
        FROM ours o JOIN hit h USING (track_id)""").fetchone()
    by_year = con.execute("""
        SELECT o.last_year, count(*) AS tracks, count(h.track_id) AS matched
        FROM ours o LEFT JOIN hit h USING (track_id) GROUP BY 1 ORDER BY 1""").fetchall()
    res = {
        "source": name, "note": spec["note"], "usable_filter": spec["usable"],
        "our_tracks": tot[0], "matched_tracks": m[0], "matched_pct_tracks": round(100 * m[0] / tot[0], 1),
        "matched_pct_chart_rows": round(100 * m[1] / tot[1], 1), "matched_pct_streams": round(100 * m[2] / tot[2], 1),
        "by_last_year_pct": {int(y): round(100 * k / n) for y, n, k in by_year},
        "columns": cols, "seconds": round(time.time() - t),
    }
    print(json.dumps(res, indent=2, default=str), flush=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"join_{name}.json").write_text(json.dumps(res, indent=2, default=str))
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, choices=[*SOURCES, "all"])
    ap.add_argument("--per-file", action="store_true", help="read multi-file sources one file at a time with progress lines")
    a = ap.parse_args()
    TMP.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit='2GB'; SET threads=2; SET temp_directory='{TMP}'")
    ours = our_tracks(con)
    for name in (SOURCES if a.source == "all" else [a.source]):
        try:
            run(name, con, ours, a.per_file)
        except Exception as exc:
            print(f"[{name}] FAILED: {str(exc)[:500]}", flush=True)


if __name__ == "__main__":
    main()
