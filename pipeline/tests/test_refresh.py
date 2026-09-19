"""Incremental refresh must equal a from-scratch run, and be idempotent."""
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import refresh  # noqa: E402

HEADER = ("date,country,rank,uri,artist_names,track_name,label,peak_rank,previous_rank,days_on_chart,"
          "streams,consecutive_days,entry_status,peak_date,entry_rank,entry_date,release_date,artist_uris")
TABLES = ["silver/song_charts", "gold/kpi_song", "gold/kpi_artist", "gold/artist_performance",
          "gold/label_performance_enhanced", "gold/country_performance", "gold/monthly_trends",
          "gold/track_catalog"]


def rows(days):
    out = []
    for d in days:
        for cc in ("in", "us", "xx", "global"):  # xx = unmapped market, global = dropped
            for rank, (uri, artists, uris) in enumerate([
                ("t1", "A|B", "ua|ub"), ("t2", "C", "uc"), ("t3", "A", "ua")], 1):
                out.append(f'{d},{cc},{rank},"{uri}","{artists}","Song {uri}","some label",{rank},-1,5,'
                           f'{1000 * (4 - rank)},3,NEW_ENTRY,{d},{rank},{d},2019-01-01,"{uris}"')
    return out


def write_csv(path, days):
    Path(path).write_text("\n".join([HEADER] + rows(days)) + "\n")


def snapshot(lake, table):
    if not any(Path(lake, table).rglob("*.parquet")):
        return []  # e.g. monthly_trends while only a partial month exists
    con = duckdb.connect()
    return sorted(con.execute(
        f"SELECT * FROM read_parquet('{lake}/{table}/**/*.parquet', hive_partitioning=1)").fetchall(),
        key=str)


def test_incremental_matches_scratch(tmp_path):
    early = ["2026-01-30", "2026-01-31"]
    late = early + ["2026-02-01", "2026-02-02"]
    write_csv(tmp_path / "early.csv", early)
    write_csv(tmp_path / "late.csv", late)

    inc, scratch = tmp_path / "inc", tmp_path / "scratch"
    refresh.run(str(tmp_path / "early.csv"), inc, memory_limit="1GB")
    res = refresh.run(str(tmp_path / "late.csv"), inc, memory_limit="1GB")
    refresh.run(str(tmp_path / "late.csv"), scratch, memory_limit="1GB")

    assert res["watermark"] == "2026-02-02"
    for t in TABLES:
        assert snapshot(inc, t) == snapshot(scratch, t), t


def test_rerun_is_idempotent(tmp_path):
    write_csv(tmp_path / "d.csv", ["2026-03-31", "2026-04-01"])
    lake = tmp_path / "lake"
    refresh.run(str(tmp_path / "d.csv"), lake, memory_limit="1GB")
    before = {t: snapshot(lake, t) for t in TABLES}
    refresh.run(str(tmp_path / "d.csv"), lake, memory_limit="1GB")
    assert {t: snapshot(lake, t) for t in TABLES} == before


def test_cleaning_rules(tmp_path):
    write_csv(tmp_path / "d.csv", ["2026-03-01"])
    lake = tmp_path / "lake"
    refresh.run(str(tmp_path / "d.csv"), lake, memory_limit="1GB")
    countries = {r[0] for r in duckdb.connect().execute(
        f"SELECT DISTINCT country_name FROM read_parquet('{lake}/silver/song_charts/**/*.parquet')").fetchall()}
    assert countries == {"India", "United States"}  # unmapped + Global rows dropped


def test_partial_month_excluded_from_monthly_trends(tmp_path):
    write_csv(tmp_path / "d.csv", ["2026-03-30", "2026-03-31", "2026-04-01"])  # April in progress
    lake = tmp_path / "lake"
    refresh.run(str(tmp_path / "d.csv"), lake, memory_limit="1GB")
    months = {r[0] for r in duckdb.connect().execute(
        f"SELECT DISTINCT month FROM read_parquet('{lake}/gold/monthly_trends/**/*.parquet', hive_partitioning=1)"
    ).fetchall()}
    assert months == {3}


def test_late_arriving_rows_within_lookback_are_picked_up(tmp_path):
    days = ["2026-03-10", "2026-03-11", "2026-03-12"]
    full = rows(days)
    # First snapshot is missing the India rows for 03-11 (a late-arriving country-day).
    partial = [r for r in full if not (r.startswith("2026-03-11,in,"))]
    Path(tmp_path / "a.csv").write_text("\n".join([HEADER] + partial) + "\n")
    Path(tmp_path / "b.csv").write_text("\n".join([HEADER] + full) + "\n")

    lake, scratch = tmp_path / "lake", tmp_path / "scratch"
    refresh.run(str(tmp_path / "a.csv"), lake, memory_limit="1GB")
    refresh.run(str(tmp_path / "b.csv"), lake, memory_limit="1GB")
    refresh.run(str(tmp_path / "b.csv"), scratch, memory_limit="1GB")
    for t in TABLES:
        assert snapshot(lake, t) == snapshot(scratch, t), t


def test_bootstrap_and_incremental_across_a_year_boundary(tmp_path):
    early = ["2024-12-30", "2024-12-31", "2025-01-01"]
    late = early + ["2025-01-02", "2026-01-15"]  # gap year + new year
    write_csv(tmp_path / "a.csv", early)
    write_csv(tmp_path / "b.csv", late)
    inc, scratch = tmp_path / "inc", tmp_path / "scratch"
    refresh.run(str(tmp_path / "a.csv"), inc, memory_limit="1GB")
    refresh.run(str(tmp_path / "b.csv"), inc, memory_limit="1GB")
    refresh.run(str(tmp_path / "b.csv"), scratch, memory_limit="1GB")
    for t in TABLES:
        assert snapshot(inc, t) == snapshot(scratch, t), t
