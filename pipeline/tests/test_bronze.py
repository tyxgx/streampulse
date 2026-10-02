"""Bronze guarantees: raw and append-only, corrections become new versions, Silver/Gold are replayable from it."""
import shutil
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import refresh  # noqa: E402
from test_refresh import HEADER, TABLES, rows, snapshot, write_csv  # noqa: E402


def bronze(lake, select="*", tail=""):
    return duckdb.connect().execute(
        f"SELECT {select} FROM read_parquet('{lake}/bronze/charts/**/*.parquet', hive_partitioning=1) {tail}").fetchall()


def bronze_files(lake):
    return sorted(p.name for p in Path(lake, "bronze/charts").rglob("*.parquet"))


def test_bronze_keeps_raw_rows_and_lineage(tmp_path):
    write_csv(tmp_path / "d.csv", ["2026-03-01"])
    lake = tmp_path / "lake"
    res = refresh.run(str(tmp_path / "d.csv"), lake, memory_limit="1GB", ingest_date="2026-03-02")
    # Silver drops the unmapped 'xx' market and 'global'; Bronze must still hold every source row
    assert res["bronze_rows_appended"] == 12 and res["bronze_bootstrap"] is True
    markets = {r[0] for r in bronze(lake, "DISTINCT country")}
    assert markets == {"in", "us", "xx", "global"}
    row = duckdb.connect().execute(
        f"SELECT _ingest_date::VARCHAR, _source_file, _row_hash IS NOT NULL FROM read_parquet("
        f"'{lake}/bronze/charts/**/*.parquet', hive_partitioning=1) LIMIT 1").fetchone()
    assert row == ("2026-03-02", "d.csv", True)


def test_unchanged_rerun_appends_nothing_and_never_rewrites_files(tmp_path):
    write_csv(tmp_path / "d.csv", ["2026-03-01", "2026-03-02"])
    lake = tmp_path / "lake"
    refresh.run(str(tmp_path / "d.csv"), lake, memory_limit="1GB", ingest_date="2026-03-03")
    files = bronze_files(lake)
    mtimes = {p: p.stat().st_mtime_ns for p in Path(lake, "bronze/charts").rglob("*.parquet")}
    res = refresh.run(str(tmp_path / "d.csv"), lake, memory_limit="1GB", ingest_date="2026-03-04")
    assert res["status"] == "up_to_date"
    assert bronze_files(lake) == files
    assert {p: p.stat().st_mtime_ns for p in Path(lake, "bronze/charts").rglob("*.parquet")} == mtimes


def test_correction_is_a_new_version_and_silver_takes_the_latest(tmp_path):
    day = "2026-03-10"
    lines = rows([day])
    write_csv(tmp_path / "a.csv", [day])
    lake = tmp_path / "lake"
    refresh.run(str(tmp_path / "a.csv"), lake, memory_limit="1GB", ingest_date="2026-03-11")

    # Kaggle later corrects one row: India, rank 1, streams 3000 -> 9999
    fixed = [l.replace(",3000,", ",9999,") if l.startswith(f"{day},in,1,") else l for l in lines]
    (tmp_path / "b.csv").write_text("\n".join([HEADER] + fixed) + "\n")
    res = refresh.run(str(tmp_path / "b.csv"), lake, memory_limit="1GB", ingest_date="2026-03-12")

    assert res["bronze_rows_appended"] == 1
    versions = bronze(lake, "streams, _ingest_date::VARCHAR", "WHERE country='in' AND rank=1 ORDER BY _ingest_date")
    assert versions == [(3000, "2026-03-11"), (9999, "2026-03-12")]     # both versions kept in Bronze
    silver = duckdb.connect().execute(
        f"SELECT streams FROM read_parquet('{lake}/silver/song_charts/**/*.parquet') "
        "WHERE market='in' AND rank=1").fetchall()
    assert silver == [(9999,)]                                           # Silver has exactly the latest


def test_silver_and_gold_are_replayable_from_bronze(tmp_path):
    write_csv(tmp_path / "d.csv", ["2026-01-30", "2026-01-31", "2026-02-01", "2026-02-02"])
    lake = tmp_path / "lake"
    refresh.run(str(tmp_path / "d.csv"), lake, memory_limit="1GB", ingest_date="2026-02-03")
    before = {t: snapshot(lake, t) for t in TABLES}

    shutil.rmtree(lake / "silver")
    shutil.rmtree(lake / "gold")
    res = refresh.run(str(tmp_path / "d.csv"), lake, memory_limit="1GB", rebuild_silver=True, ingest_date="2026-02-04")

    assert res["status"] == "refreshed" and res["bronze_rows_appended"] == 0
    assert {t: snapshot(lake, t) for t in TABLES} == before
