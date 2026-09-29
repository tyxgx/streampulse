import duckdb, time

con = duckdb.connect()
con.execute("SET memory_limit='2GB'; SET threads=2")
S = "/Users/uttkarshtyagi/job/projects/streamPulse/aws-backup-2026-09-19/streampulse-lake-dev-data/silver/song_charts/**/*.parquet"

t = time.time()
con.execute(f"""
CREATE TABLE ours AS
SELECT replace(uri,'spotify:track:','') AS track_id, count(*) AS chart_rows,
       min(year) AS first_year, max(year) AS last_year, sum(streams) AS streams
FROM read_parquet('{S}', hive_partitioning=1) GROUP BY 1
""")
tot = con.execute("SELECT count(*), sum(chart_rows), sum(streams) FROM ours").fetchone()
print("ours: tracks %d, chart_rows %d" % (tot[0], tot[1]), "(%.0fs)" % (time.time() - t), flush=True)

SOURCES = {
    "maharshipandya 114k (bsd)": "read_csv('https://huggingface.co/datasets/maharshipandya/spotify-tracks-dataset/resolve/main/dataset.csv')|track_id",
    "Gildas 56M (cc-by-nc)": "read_parquet('https://huggingface.co/datasets/GildasLeDrogoff/spotify-huge-track-analysis-dataset/resolve/main/spotify-huge-audio-features.parquet')|track_id",
}
for name, spec in SOURCES.items():
    rd, col = spec.split("|")
    t = time.time()
    try:
        con.execute(f"CREATE OR REPLACE TABLE hit AS SELECT DISTINCT {col} AS track_id FROM {rd} WHERE {col} IN (SELECT track_id FROM ours)")
        r = con.execute("""
            SELECT count(*) AS tracks_matched, sum(o.chart_rows) AS rows_matched, sum(o.streams) AS streams_matched
            FROM ours o JOIN hit h USING (track_id)""").fetchone()
        print("%s: matched tracks %d / %d (%.1f%%) | chart rows %.1f%% | streams %.1f%% (%.0fs)" % (
            name, r[0], tot[0], 100 * r[0] / tot[0], 100 * r[1] / tot[1], 100 * r[2] / tot[2], time.time() - t), flush=True)
        # coverage by last year the track appeared (recent tracks are the risk)
        rows = con.execute("""
            SELECT o.last_year, count(*) AS tracks, count(h.track_id) AS matched
            FROM ours o LEFT JOIN hit h USING (track_id) GROUP BY 1 ORDER BY 1""").fetchall()
        print("   by last_year:", [(y, n, "%.0f%%" % (100 * m / n)) for y, n, m in rows], flush=True)
    except Exception as e:
        print(name, "FAIL", str(e)[:300], flush=True)
