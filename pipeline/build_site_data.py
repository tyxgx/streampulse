"""
Build the pre-aggregated JSON the static StreamPulse dashboard reads.

Reads the Silver daily chart rows (lake/silver/song_charts) and writes small JSON
files (site_data/) that the browser fetches straight from S3 - no server per
visitor. Heavy aggregation happens once here (in the daily GitHub Actions run);
the page only draws charts.

    python build_site_data.py --lake ./lake --out ./site_data

Numbers are "charted streams": streams of tracks that appear on a country's
daily top-200 chart, summed over charts. They are not total Spotify streams.
"""
import argparse
import datetime as dt
import json
import logging
import time
from pathlib import Path

import duckdb

log = logging.getLogger("site_data")

TOP_ARTISTS_PAGES = 300
TOP_TRACKS_PAGES = 500
TOP_N = 20


def dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), default=str))


def rows(con, sql, *params):
    cur = con.execute(sql, list(params))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def slug(uri: str) -> str:
    return uri.split(":")[-1]


def build_intermediates(con, silver_glob):
    """A few small tables aggregated once from the 40M+ daily rows."""
    log.info("aggregating silver -> intermediates")
    con.execute(f"""
        CREATE OR REPLACE VIEW s AS
        SELECT date, rank, uri, streams, artist_names, market, country_name,
               track_name, standardized_label AS label, entry_date, entry_status,
               hit_category
        FROM read_parquet('{silver_glob}', hive_partitioning=1)
        WHERE streams IS NOT NULL AND country_name IS NOT NULL
    """)
    con.execute("""
        CREATE OR REPLACE TABLE daily_country AS
        SELECT date, market, country_name, sum(streams)::BIGINT AS streams,
               count(*) AS n, count(DISTINCT uri) AS tracks
        FROM s GROUP BY ALL
    """)
    con.execute("""
        CREATE OR REPLACE TABLE track_all AS
        SELECT uri, any_value(track_name) AS track_name,
               any_value(artist_names) AS artist_names, any_value(label) AS label,
               sum(streams)::BIGINT AS streams, count(DISTINCT date) AS days,
               min(rank) AS peak_rank, count(DISTINCT market) AS markets,
               min(date) AS first_seen, max(date) AS last_seen
        FROM s GROUP BY uri
    """)
    con.execute("""
        CREATE OR REPLACE TABLE track_month AS
        SELECT uri, strftime(date_trunc('month', date), '%Y-%m') AS ym,
               sum(streams)::BIGINT AS streams
        FROM s GROUP BY ALL
    """)
    con.execute("""
        CREATE OR REPLACE TABLE track_country AS
        SELECT uri, market, country_name, sum(streams)::BIGINT AS streams,
               min(rank) AS peak_rank
        FROM s GROUP BY ALL
    """)
    con.execute("""
        CREATE OR REPLACE TABLE track_day AS
        SELECT uri, date, sum(streams)::BIGINT AS streams, min(rank) AS best_rank,
               count(DISTINCT market) AS markets
        FROM s WHERE date >= (SELECT max(date) FROM s) - INTERVAL 210 DAY
        GROUP BY ALL
    """)
    # artist credit split: every credited artist gets the track's streams
    con.execute("""
        CREATE OR REPLACE TABLE track_artist AS
        SELECT uri, trim(a) AS artist FROM (
            SELECT uri, unnest(string_split(artist_names, '|')) AS a FROM track_all
        ) WHERE trim(a) <> ''
    """)
    con.execute("""
        CREATE OR REPLACE TABLE artist_all AS
        SELECT ta.artist, sum(t.streams)::BIGINT AS streams,
               count(DISTINCT ta.uri) AS tracks, min(t.peak_rank) AS peak_rank,
               max(t.last_seen) AS last_seen, min(t.first_seen) AS first_seen,
               count(*) FILTER (WHERE t.peak_rank <= 10) AS top10_tracks
        FROM track_artist ta JOIN track_all t USING (uri) GROUP BY ta.artist
    """)


def window_dates(con):
    (mx,) = con.execute("SELECT max(date) FROM daily_country").fetchone()
    (mn,) = con.execute("SELECT min(date) FROM daily_country").fetchone()
    return mn, mx


def build_meta(con, out, mn, mx):
    d = rows(con, """
        SELECT sum(streams)::BIGINT AS streams, sum(n)::BIGINT AS chart_rows,
               count(DISTINCT market) AS markets FROM daily_country""")[0]
    d.update(
        tracks=con.execute("SELECT count(*) FROM track_all").fetchone()[0],
        artists=con.execute("SELECT count(*) FROM artist_all").fetchone()[0],
        first_date=mn, last_date=mx,
        generated_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    )
    dump(out / "meta.json", d)
    return d


def build_overview(con, out, mx):
    monthly = rows(con, """
        SELECT strftime(date_trunc('month', date), '%Y-%m') AS ym,
               sum(streams)::BIGINT AS streams, count(DISTINCT market) AS markets
        FROM daily_country
        WHERE date < date_trunc('month', (SELECT max(date) FROM daily_country))
        GROUP BY 1 ORDER BY 1""")
    daily = rows(con, """
        SELECT date, sum(streams)::BIGINT AS streams FROM daily_country
        WHERE date > (SELECT max(date) FROM daily_country) - INTERVAL 120 DAY
        GROUP BY 1 ORDER BY 1""")
    top_tracks = rows(con, """
        SELECT t.uri, t.track_name, t.artist_names, sum(d.streams)::BIGINT AS streams
        FROM track_day d JOIN track_all t USING (uri)
        WHERE d.date > ? - INTERVAL 30 DAY GROUP BY ALL ORDER BY streams DESC LIMIT 15""", mx)
    top_artists = rows(con, """
        SELECT ta.artist, sum(d.streams)::BIGINT AS streams
        FROM track_day d JOIN track_artist ta USING (uri)
        WHERE d.date > ? - INTERVAL 30 DAY GROUP BY 1 ORDER BY 2 DESC LIMIT 15""", mx)
    for r in top_tracks:
        r["id"] = slug(r.pop("uri"))
    dump(out / "overview.json", dict(monthly=monthly, daily=daily,
                                     top_tracks=top_tracks, top_artists=top_artists))


def build_countries(con, out, mx):
    summary = rows(con, """
        WITH last30 AS (
            SELECT market, sum(streams) AS s FROM daily_country
            WHERE date > ?::DATE - INTERVAL 30 DAY GROUP BY 1),
        prev30 AS (
            SELECT market, sum(streams) AS s FROM daily_country
            WHERE date > ?::DATE - INTERVAL 60 DAY AND date <= ?::DATE - INTERVAL 30 DAY
            GROUP BY 1),
        yr AS (
            SELECT market, any_value(country_name) AS country_name,
                   sum(streams)::BIGINT AS total, max(date) AS last_date
            FROM daily_country GROUP BY 1)
        SELECT yr.market, yr.country_name, yr.total, yr.last_date, l.s::BIGINT AS last30,
               round(100.0 * (l.s - p.s) / nullif(p.s, 0), 1) AS mom_pct
        FROM yr LEFT JOIN last30 l USING (market) LEFT JOIN prev30 p USING (market)
        ORDER BY last30 DESC NULLS LAST""", mx, mx, mx)
    for c in summary:
        m = c["market"]
        c["monthly"] = rows(con, """
            SELECT strftime(date_trunc('month', date), '%Y-%m') AS ym,
                   sum(streams)::BIGINT AS streams FROM daily_country
            WHERE market = ? AND date < date_trunc('month', ?::DATE)
            GROUP BY 1 ORDER BY 1""", m, mx)
        c["top_tracks"] = rows(con, """
            SELECT t.uri, t.track_name, t.artist_names, tc.streams
            FROM track_country tc JOIN track_all t USING (uri)
            WHERE tc.market = ? ORDER BY tc.streams DESC LIMIT ?""", m, TOP_N)
        for r in c["top_tracks"]:
            r["id"] = slug(r.pop("uri"))
        c["top_artists"] = rows(con, """
            SELECT ta.artist, sum(tc.streams)::BIGINT AS streams
            FROM track_country tc JOIN track_artist ta USING (uri)
            WHERE tc.market = ? GROUP BY 1 ORDER BY 2 DESC LIMIT ?""", m, TOP_N)
        c["recent_top_tracks"] = rows(con, """
            SELECT t.uri, t.track_name, t.artist_names, sum(s.streams)::BIGINT AS streams
            FROM s JOIN track_all t USING (uri)
            WHERE s.market = ? AND s.date > ?::DATE - INTERVAL 30 DAY
            GROUP BY ALL ORDER BY streams DESC LIMIT ?""", m, mx, TOP_N)
        for r in c["recent_top_tracks"]:
            r["id"] = slug(r.pop("uri"))
        dump(out / "country" / f"{m}.json", c)
    # index file is light: no series
    dump(out / "countries.json",
         [{k: v for k, v in c.items() if k in ("market", "country_name", "total", "last30", "mom_pct", "last_date")}
          for c in summary])


def build_artists(con, out, mx):
    top = rows(con, """
        SELECT artist, streams, tracks, peak_rank, top10_tracks, first_seen, last_seen
        FROM artist_all ORDER BY streams DESC LIMIT ?""", TOP_ARTISTS_PAGES)
    for i, a in enumerate(top):
        name = a["artist"]
        a["monthly"] = rows(con, """
            SELECT tm.ym, sum(tm.streams)::BIGINT AS streams
            FROM track_month tm JOIN track_artist ta USING (uri)
            WHERE ta.artist = ? GROUP BY 1 ORDER BY 1""", name)
        a["tracks_top"] = rows(con, """
            SELECT t.uri, t.track_name, t.streams, t.peak_rank, t.markets
            FROM track_all t JOIN track_artist ta USING (uri)
            WHERE ta.artist = ? ORDER BY t.streams DESC LIMIT 15""", name)
        for r in a["tracks_top"]:
            r["id"] = slug(r.pop("uri"))
        a["countries"] = rows(con, """
            SELECT tc.country_name, tc.market, sum(tc.streams)::BIGINT AS streams
            FROM track_country tc JOIN track_artist ta USING (uri)
            WHERE ta.artist = ? GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15""", name)
        a["id"] = i + 1
        dump(out / "artist" / f"{a['id']}.json", a)
    dump(out / "artists.json",
         [{k: a[k] for k in ("id", "artist", "streams", "tracks", "peak_rank", "top10_tracks")}
          for a in top])


def build_tracks(con, out, mx):
    top = rows(con, """
        SELECT uri, track_name, artist_names, label, streams, days, peak_rank, markets,
               first_seen, last_seen
        FROM track_all ORDER BY streams DESC LIMIT ?""", TOP_TRACKS_PAGES)
    for t in top:
        uri = t["uri"]
        t["id"] = slug(uri)
        t["daily"] = rows(con, """
            SELECT date, streams, best_rank, markets FROM track_day
            WHERE uri = ? ORDER BY date""", uri)
        t["monthly"] = rows(con, "SELECT ym, streams FROM track_month WHERE uri = ? ORDER BY ym", uri)
        t["countries"] = rows(con, """
            SELECT country_name, market, streams, peak_rank FROM track_country
            WHERE uri = ? ORDER BY streams DESC LIMIT 15""", uri)
        del t["uri"]
        dump(out / "track" / f"{t['id']}.json", t)
    dump(out / "tracks.json",
         [{k: t[k] for k in ("id", "track_name", "artist_names", "streams", "peak_rank", "markets")}
          for t in top])


def build_trending(con, out, mx):
    base = """
        WITH w AS (
            SELECT uri,
                   sum(streams) FILTER (WHERE date > ?::DATE - INTERVAL 7 DAY) AS cur,
                   sum(streams) FILTER (WHERE date <= ?::DATE - INTERVAL 7 DAY
                                          AND date > ?::DATE - INTERVAL 14 DAY) AS prev,
                   max(markets) FILTER (WHERE date > ?::DATE - INTERVAL 7 DAY) AS markets
            FROM track_day GROUP BY uri)
        SELECT t.uri, t.track_name, t.artist_names, t.first_seen,
               w.cur::BIGINT AS cur, coalesce(w.prev, 0)::BIGINT AS prev, w.markets
        FROM w JOIN track_all t USING (uri)"""
    args = (mx, mx, mx, mx)
    climbers = rows(con, base + """
        WHERE w.prev > 200000 AND w.cur > w.prev
        ORDER BY (w.cur - w.prev) / w.prev DESC LIMIT 25""", *args)
    new = rows(con, base + """
        WHERE t.first_seen > ?::DATE - INTERVAL 14 DAY AND w.cur IS NOT NULL
        ORDER BY w.cur DESC LIMIT 25""", *args, mx)
    fallers = rows(con, base + """
        WHERE w.prev > 500000 AND w.cur < w.prev
        ORDER BY (w.cur - w.prev) / w.prev ASC LIMIT 15""", *args)
    for lst in (climbers, new, fallers):
        for r in lst:
            r["id"] = slug(r.pop("uri"))
            r["change_pct"] = round(100.0 * (r["cur"] - r["prev"]) / r["prev"], 1) if r["prev"] else None
    dump(out / "trending.json", dict(climbers=climbers, new_entries=new, fallers=fallers,
                                     as_of=mx))


def build_labels(con, out, mx):
    labels = rows(con, """
        SELECT label, sum(streams)::BIGINT AS streams, count(*) AS tracks
        FROM track_all WHERE label IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 25""")
    by_year = rows(con, """
        SELECT substr(tm.ym, 1, 4) AS year, t.label, sum(tm.streams)::BIGINT AS streams
        FROM track_month tm JOIN track_all t USING (uri)
        WHERE t.label IN (SELECT label FROM track_all WHERE label IS NOT NULL
                          GROUP BY 1 ORDER BY sum(streams) DESC LIMIT 10)
        GROUP BY 1, 2 ORDER BY 1, 3 DESC""")
    dump(out / "labels.json", dict(labels=labels, by_year=by_year))


def build_seasonality(con, out, mx):
    wd = rows(con, """
        SELECT dayofweek(date) AS dow, avg(day_total)::BIGINT AS avg_streams FROM (
            SELECT date, sum(streams) AS day_total FROM daily_country
            WHERE date > ?::DATE - INTERVAL 365 DAY GROUP BY 1) GROUP BY 1 ORDER BY 1""", mx)
    base = sum(r["avg_streams"] for r in wd) / len(wd)
    for r in wd:
        r["index"] = round(100.0 * r["avg_streams"] / base, 1)
    mo = rows(con, """
        SELECT month(date) AS month, avg(day_total)::BIGINT AS avg_daily FROM (
            SELECT date, sum(streams) AS day_total FROM daily_country
            WHERE date >= '2019-01-01' AND date < date_trunc('month', ?::DATE)
            GROUP BY 1) GROUP BY 1 ORDER BY 1""", mx)
    base = sum(r["avg_daily"] for r in mo) / len(mo)
    for r in mo:
        r["index"] = round(100.0 * r["avg_daily"] / base, 1)
    dump(out / "seasonality.json", dict(weekday=wd, month=mo))


def build_health(con, out, mn, mx):
    per_day = rows(con, """
        SELECT date, sum(n)::BIGINT AS chart_rows, count(DISTINCT market) AS markets,
               sum(streams)::BIGINT AS streams FROM daily_country
        WHERE date > ?::DATE - INTERVAL 45 DAY GROUP BY 1 ORDER BY 1""", mx)
    nulls = rows(con, """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE track_name IS NULL OR track_name = 'Unknown Track') AS unknown_track,
               count(*) FILTER (WHERE artist_names IS NULL) AS no_artist,
               count(*) FILTER (WHERE rank IS NULL) AS no_rank
        FROM s WHERE date > ?::DATE - INTERVAL 45 DAY""", mx)[0]
    stale = rows(con, """
        SELECT country_name, max(date) AS last_date FROM daily_country
        GROUP BY 1 HAVING max(date) < ?::DATE - INTERVAL 3 DAY ORDER BY 2""", mx)
    today = dt.datetime.now(dt.timezone.utc).date()
    lag = (today - mx).days
    expected = 200 * max(r["markets"] for r in per_day)
    checks = [
        dict(name="Data freshness", ok=lag <= 3, detail=f"latest chart date {mx}, {lag} day(s) behind"),
        dict(name="Markets on latest day", ok=per_day[-1]["markets"] >= 60,
             detail=f"{per_day[-1]['markets']} markets"),
        dict(name="Latest-day row count", ok=per_day[-1]["chart_rows"] >= 0.9 * expected,
             detail=f"{per_day[-1]['chart_rows']} rows vs ~{expected} expected"),
        dict(name="Unknown track share", ok=nulls["unknown_track"] / nulls["total"] < 0.02,
             detail=f"{100.0 * nulls['unknown_track'] / nulls['total']:.2f}% of last 45 days"),
        dict(name="Missing rank/artist", ok=nulls["no_rank"] == 0 and nulls["no_artist"] == 0,
             detail=f"{nulls['no_rank']} no rank, {nulls['no_artist']} no artist"),
    ]
    dump(out / "health.json", dict(as_of=mx, first_date=mn, per_day=per_day,
                                   nulls=nulls, checks=checks, stale_markets=stale,
                                   all_ok=all(c["ok"] for c in checks)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--db", default=":memory:")
    ap.add_argument("--memory-limit", default="3GB")
    ap.add_argument("--threads", type=int, default=2)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(args.db)
    con.execute(f"SET memory_limit='{args.memory_limit}'; SET threads={args.threads}")
    t0 = time.time()
    build_intermediates(con, f"{args.lake}/silver/song_charts/**/*.parquet")
    mn, mx = window_dates(con)
    log.info("date range %s .. %s (%.0fs)", mn, mx, time.time() - t0)
    meta = build_meta(con, out, mn, mx)
    for fn in (build_overview, build_countries, build_artists, build_tracks,
               build_trending, build_labels, build_seasonality):
        fn(con, out, mx)
        log.info("%s done (%.0fs)", fn.__name__, time.time() - t0)
    build_health(con, out, mn, mx)
    log.info("all done in %.0fs: %s", time.time() - t0, meta)


if __name__ == "__main__":
    main()
