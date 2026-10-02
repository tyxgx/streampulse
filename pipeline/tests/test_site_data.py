"""Serving-layer determinism: a track with several name spellings must resolve to the latest one, every time."""
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_site_data as b  # noqa: E402
import refresh  # noqa: E402
from test_refresh import HEADER, rows  # noqa: E402


def test_track_all_uses_latest_spelling_deterministically(tmp_path):
    lines = rows(["2026-03-10", "2026-03-11"])
    # t1 is credited "A|B" on 03-10 but "A|B|C" on 03-11 (a re-credit)
    lines = [l.replace('"A|B"', '"A|B|C"') if l.startswith("2026-03-11") else l for l in lines]
    (tmp_path / "d.csv").write_text("\n".join([HEADER] + lines) + "\n")
    lake = tmp_path / "lake"
    refresh.run(str(tmp_path / "d.csv"), lake, memory_limit="1GB", ingest_date="2026-03-12")

    results = set()
    for _ in range(3):
        con = duckdb.connect()
        b.build_intermediates(con, f"{lake}/silver/song_charts/**/*.parquet")
        results.add(con.execute("SELECT artist_names FROM track_all WHERE uri = 't1'").fetchone()[0])
    assert results == {"A|B|C"}
