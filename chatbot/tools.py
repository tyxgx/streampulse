"""
Deterministic tools the StreamPulse chatbot calls. Every number the assistant
states must come from one of these functions, never from the model's memory.

Facts live in compact Parquet files (pipeline/build_site_data.py --chat-out) and
are queried with DuckDB. Each tool returns:

    {"ok": True, "as_of": "2026-09-30", "data": {...}, "notes": [...], "links": [...]}
or  {"ok": False, "error": "...", "suggestions": [...]}

"Streams" always means charted streams: streams of tracks on a market's daily
top-200 chart, not total Spotify streams.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb
from rapidfuzz import fuzz, process

log = logging.getLogger("chatbot.tools")

PERIODS = {"last_7_days": 7, "last_30_days": 30, "last_60_days": 60, "last_year": 365, "all_time": None}
COUNTRY_WINDOW_MAX = 60       # track_country_day keeps 60 days
GLOBAL_TRACK_WINDOW_MAX = 210  # track_day keeps ~210 days
MAX_LIMIT = 15

COUNTRY_ALIASES = {
    "us": "United States", "usa": "United States", "america": "United States", "united states of america": "United States",
    "uk": "United Kingdom", "britain": "United Kingdom", "england": "United Kingdom", "great britain": "United Kingdom",
    "uae": "United Arab Emirates", "emirates": "United Arab Emirates", "korea": "South Korea", "czechia": "Czech Republic",
    "holland": "Netherlands", "russia": "Russia", "hongkong": "Hong Kong",
}


def human(n) -> str:
    if n is None:
        return "n/a"
    a = abs(n)
    if a >= 1e12:
        return f"{n / 1e12:.2f}T"
    if a >= 1e9:
        return f"{n / 1e9:.2f}B"
    if a >= 1e6:
        return f"{n / 1e6:.1f}M"
    if a >= 1e3:
        return f"{n / 1e3:.0f}K"
    return str(int(n))


def _slug(uri: str) -> str:
    return uri.split(":")[-1]


class Facts:
    def __init__(self, data_dir: str | Path):
        d = Path(data_dir)
        self.con = duckdb.connect()
        self.con.execute("SET threads=2; SET memory_limit='1GB'")
        for t in ("daily_country", "track_all", "track_month", "track_country",
                  "track_artist", "artist_all", "track_day", "track_country_day"):
            self.con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{d / (t + '.parquet')}')")
        self.as_of = self.con.execute("SELECT max(date) FROM daily_country").fetchone()[0]
        self.first = self.con.execute("SELECT min(date) FROM daily_country").fetchone()[0]
        self.countries = {m: n for m, n in self.con.execute(
            "SELECT DISTINCT market, country_name FROM daily_country").fetchall()}
        self._cname = {n.lower(): m for m, n in self.countries.items()}
        self.artist_names = [r[0] for r in self.con.execute(
            "SELECT artist FROM artist_all ORDER BY streams DESC LIMIT 20000").fetchall()]
        # dashboard page ids exist only for the top lists
        self._artist_page = {a: i + 1 for i, a in enumerate(self.artist_names[:300])}
        self._track_page = {r[0] for r in self.con.execute(
            "SELECT uri FROM track_all ORDER BY streams DESC LIMIT 500").fetchall()}

    # ---------- helpers ----------
    def q(self, sql, *params):
        cur = self.con.execute(sql, list(params))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def _window(self, period: str):
        if period not in PERIODS:
            raise ValueError(f"period must be one of {sorted(PERIODS)}")
        return PERIODS[period]

    def resolve_country(self, name: str):
        n = re.sub(r"\s+", " ", (name or "").strip().lower())
        if not n:
            return None, []
        n = COUNTRY_ALIASES.get(n, n).lower()
        if n in self._cname:
            return self._cname[n], []
        if n in self.countries:  # market code like "in"
            return n, []
        hits = process.extract(n, list(self._cname), scorer=fuzz.WRatio, limit=3)
        if hits and hits[0][1] >= 88:
            return self._cname[hits[0][0]], []
        return None, [self.countries[self._cname[h[0]]] for h in hits]

    def resolve_artist(self, name: str):
        n = (name or "").strip()
        if not n:
            return None, []
        low = {a.lower(): a for a in self.artist_names}
        if n.lower() in low:
            return low[n.lower()], []
        hits = process.extract(n, self.artist_names, scorer=fuzz.WRatio, limit=3)
        if hits and hits[0][1] >= 90:
            return hits[0][0], []
        return None, [h[0] for h in hits]

    def resolve_track(self, name: str, artist: str | None = None):
        n = (name or "").strip()
        if not n:
            return None, []
        rows = self.q("""SELECT uri, track_name, artist_names, streams FROM track_all
                         WHERE lower(track_name) = lower(?) ORDER BY streams DESC LIMIT 20""", n)
        if artist:
            a = artist.lower()
            narrowed = [r for r in rows if a in r["artist_names"].lower()]
            rows = narrowed or rows
        if rows:
            top = rows[0]
            same = [r for r in rows if r["artist_names"] == top["artist_names"]]
            top = dict(top, uris=[r["uri"] for r in same])
            return top, []
        cand = self.q("SELECT uri, track_name, artist_names, streams FROM track_all ORDER BY streams DESC LIMIT 30000")
        hits = process.extract(n, [c["track_name"] for c in cand], scorer=fuzz.WRatio, limit=3)
        if hits and hits[0][1] >= 92:
            c = cand[hits[0][2]]
            return dict(c, uris=[c["uri"]]), []
        return None, [f"{cand[h[2]]['track_name']} - {cand[h[2]]['artist_names'].replace('|', ', ')}" for h in hits]

    def _ok(self, data, notes=None, links=None):
        return {"ok": True, "as_of": str(self.as_of), "data": data, "notes": notes or [], "links": links or []}

    @staticmethod
    def _err(msg, suggestions=None):
        return {"ok": False, "error": msg, "suggestions": suggestions or []}

    def _artist_link(self, name):
        i = self._artist_page.get(name)
        return {"label": name, "href": f"#/artist/{i}"} if i else None

    def _track_link(self, uri, label):
        return {"label": label, "href": f"#/track/{_slug(uri)}"} if uri in self._track_page else None

    def _since(self, days):
        return f"date > DATE '{self.as_of}' - INTERVAL {int(days)} DAY"

    # ---------- tools ----------
    def data_status(self):
        stale = self.q("""SELECT country_name, max(date)::VARCHAR AS last_date FROM daily_country
                          GROUP BY 1 HAVING max(date) < DATE '%s' - INTERVAL 3 DAY ORDER BY 2""" % self.as_of)
        return self._ok({"first_date": str(self.first), "last_date": str(self.as_of), "markets": len(self.countries),
                         "markets_without_recent_data": stale},
                        links=[{"label": "Data health", "href": "#/health"}])

    def global_overview(self, period: str = "last_30_days"):
        days = self._window(period)
        w = f"WHERE {self._since(days)}" if days else ""
        r = self.q(f"SELECT sum(streams)::BIGINT AS streams, count(DISTINCT market) AS markets FROM daily_country {w}")[0]
        tot = self.q("SELECT sum(streams)::BIGINT AS s FROM daily_country")[0]["s"]
        data = {"period": period, "streams": r["streams"], "streams_human": human(r["streams"]),
                "markets_with_data": r["markets"], "all_time_streams": tot, "all_time_streams_human": human(tot),
                "tracks_ever_charted": self.q("SELECT count(*) AS c FROM track_all")[0]["c"],
                "artists_ever_charted": self.q("SELECT count(*) AS c FROM artist_all")[0]["c"]}
        if days:
            prev = self.q(f"SELECT sum(streams)::BIGINT AS s FROM daily_country WHERE date > DATE '{self.as_of}' - INTERVAL {2 * days} DAY "
                          f"AND date <= DATE '{self.as_of}' - INTERVAL {days} DAY")[0]["s"]
            if prev:
                data["change_vs_previous_period_pct"] = round(100.0 * (r["streams"] - prev) / prev, 1)
        return self._ok(data, links=[{"label": "Overview", "href": "#/overview"}])

    def country_stats(self, country: str, period: str = "last_30_days"):
        m, sug = self.resolve_country(country)
        if not m:
            return self._err(f"Unknown country '{country}'", sug)
        days = self._window(period)
        name = self.countries[m]
        last_date = self.q("SELECT max(date)::VARCHAR AS d FROM daily_country WHERE market = ?", m)[0]["d"]
        notes = []
        if last_date < str(self.as_of - __import__("datetime").timedelta(days=3)):
            notes.append(f"{name} has no stream counts after {last_date} in the source data.")
        total = self.q("SELECT sum(streams)::BIGINT AS s FROM daily_country WHERE market = ?", m)[0]["s"]
        data = {"country": name, "latest_chart_date": last_date, "all_time_streams": total,
                "all_time_streams_human": human(total)}
        if days:
            cur = self.q(f"SELECT sum(streams)::BIGINT AS s FROM daily_country WHERE market = ? AND {self._since(days)}", m)[0]["s"]
            prev = self.q(f"SELECT sum(streams)::BIGINT AS s FROM daily_country WHERE market = ? AND "
                          f"date > DATE '{self.as_of}' - INTERVAL {2 * days} DAY AND date <= DATE '{self.as_of}' - INTERVAL {days} DAY", m)[0]["s"]
            glob = self.q(f"SELECT sum(streams)::BIGINT AS s FROM daily_country WHERE {self._since(days)}")[0]["s"]
            data.update(period=period, streams=cur, streams_human=human(cur))
            if cur and glob:
                data["share_of_all_markets_pct"] = round(100.0 * cur / glob, 1)
            if prev and cur is not None:
                data["change_vs_previous_period_pct"] = round(100.0 * (cur - prev) / prev, 1)
        return self._ok(data, notes, [{"label": name, "href": f"#/country/{m}"}])

    def top_tracks(self, period: str = "last_30_days", country: str | None = None, limit: int = 5):
        limit = max(1, min(int(limit), MAX_LIMIT))
        days = self._window(period)
        notes, links = [], []
        scope = "global"
        if country:
            m, sug = self.resolve_country(country)
            if not m:
                return self._err(f"Unknown country '{country}'", sug)
            scope = self.countries[m]
            links.append({"label": scope, "href": f"#/country/{m}"})
            if days and days <= COUNTRY_WINDOW_MAX:
                rows = self.q(f"""SELECT t.uri, t.track_name, t.artist_names, sum(d.streams)::BIGINT AS streams
                                  FROM track_country_day d JOIN track_all t USING (uri)
                                  WHERE d.market = ? AND {self._since(days)} GROUP BY ALL ORDER BY streams DESC LIMIT ?""", m, limit)
            else:
                if days:
                    notes.append(f"Per-country windows are limited to {COUNTRY_WINDOW_MAX} days; showing all-time instead.")
                    period = "all_time"
                rows = self.q("""SELECT t.uri, t.track_name, t.artist_names, c.streams FROM track_country c
                                 JOIN track_all t USING (uri) WHERE c.market = ? ORDER BY c.streams DESC LIMIT ?""", m, limit)
        else:
            if days and days <= GLOBAL_TRACK_WINDOW_MAX:
                rows = self.q(f"""SELECT t.uri, t.track_name, t.artist_names, sum(d.streams)::BIGINT AS streams
                                  FROM track_day d JOIN track_all t USING (uri)
                                  WHERE {self._since(days)} GROUP BY ALL ORDER BY streams DESC LIMIT ?""", limit)
            else:
                if days:
                    notes.append(f"Global windows are limited to {GLOBAL_TRACK_WINDOW_MAX} days; showing all-time instead.")
                    period = "all_time"
                rows = self.q("SELECT uri, track_name, artist_names, streams FROM track_all ORDER BY streams DESC LIMIT ?", limit)
        out = []
        for i, r in enumerate(rows, 1):
            out.append({"rank": i, "track": r["track_name"], "artists": r["artist_names"].replace("|", ", "),
                        "streams": r["streams"], "streams_human": human(r["streams"])})
            ln = self._track_link(r["uri"], r["track_name"])
            if ln:
                links.append(ln)
        return self._ok({"scope": scope, "period": period, "tracks": out}, notes, links)

    def top_artists(self, period: str = "last_30_days", country: str | None = None, limit: int = 5):
        limit = max(1, min(int(limit), MAX_LIMIT))
        days = self._window(period)
        notes, links = [], []
        scope = "global"
        if country:
            m, sug = self.resolve_country(country)
            if not m:
                return self._err(f"Unknown country '{country}'", sug)
            scope = self.countries[m]
            links.append({"label": scope, "href": f"#/country/{m}"})
            if days and days <= COUNTRY_WINDOW_MAX:
                rows = self.q(f"""SELECT a.artist, sum(d.streams)::BIGINT AS streams FROM track_country_day d
                                  JOIN track_artist a USING (uri) WHERE d.market = ? AND {self._since(days)}
                                  GROUP BY 1 ORDER BY 2 DESC LIMIT ?""", m, limit)
            else:
                if days:
                    notes.append(f"Per-country windows are limited to {COUNTRY_WINDOW_MAX} days; showing all-time instead.")
                    period = "all_time"
                rows = self.q("""SELECT a.artist, sum(c.streams)::BIGINT AS streams FROM track_country c
                                 JOIN track_artist a USING (uri) WHERE c.market = ? GROUP BY 1 ORDER BY 2 DESC LIMIT ?""", m, limit)
        else:
            if days and days <= GLOBAL_TRACK_WINDOW_MAX:
                rows = self.q(f"""SELECT a.artist, sum(d.streams)::BIGINT AS streams FROM track_day d
                                  JOIN track_artist a USING (uri) WHERE {self._since(days)}
                                  GROUP BY 1 ORDER BY 2 DESC LIMIT ?""", limit)
            else:
                if days:
                    notes.append(f"Global windows are limited to {GLOBAL_TRACK_WINDOW_MAX} days; showing all-time instead.")
                    period = "all_time"
                rows = self.q("SELECT artist, streams FROM artist_all ORDER BY streams DESC LIMIT ?", limit)
        out = []
        for i, r in enumerate(rows, 1):
            out.append({"rank": i, "artist": r["artist"], "streams": r["streams"], "streams_human": human(r["streams"])})
            ln = self._artist_link(r["artist"])
            if ln:
                links.append(ln)
        notes.append("Collaborations count towards every credited artist.")
        return self._ok({"scope": scope, "period": period, "artists": out}, notes, links)

    def artist_summary(self, artist: str):
        a, sug = self.resolve_artist(artist)
        if not a:
            return self._err(f"Unknown artist '{artist}'", sug)
        s = self.q("SELECT * FROM artist_all WHERE artist = ?", a)[0]
        rank = self.q("SELECT count(*) + 1 AS r FROM artist_all WHERE streams > ?", s["streams"])[0]["r"]
        tops = self.q("""SELECT t.track_name, t.streams, t.peak_rank, t.uri FROM track_artist x JOIN track_all t USING (uri)
                         WHERE x.artist = ? ORDER BY t.streams DESC LIMIT 5""", a)
        mk = self.q("""SELECT c.market, sum(c.streams)::BIGINT AS streams FROM track_country c JOIN track_artist x USING (uri)
                       WHERE x.artist = ? GROUP BY 1 ORDER BY 2 DESC LIMIT 5""", a)
        l30 = self.q(f"SELECT coalesce(sum(d.streams),0)::BIGINT AS s FROM track_day d JOIN track_artist x USING (uri) "
                     f"WHERE x.artist = ? AND {self._since(30)}", a)[0]["s"]
        data = {"artist": a, "all_time_rank_by_streams": rank, "all_time_streams": s["streams"],
                "all_time_streams_human": human(s["streams"]), "tracks_charted": s["tracks"],
                "tracks_that_reached_top10": s["top10_tracks"], "best_chart_rank": s["peak_rank"],
                "first_charted": str(s["first_seen"]), "last_charted": str(s["last_seen"]),
                "last_30_days_streams": l30, "last_30_days_streams_human": human(l30),
                "top_tracks": [{"track": t["track_name"], "streams_human": human(t["streams"]), "best_rank": t["peak_rank"]} for t in tops],
                "top_markets": [{"country": self.countries.get(m["market"], m["market"]), "streams_human": human(m["streams"])} for m in mk]}
        links = [x for x in [self._artist_link(a)] if x]
        links += [x for x in (self._track_link(t["uri"], t["track_name"]) for t in tops) if x]
        return self._ok(data, ["Collaborations count towards every credited artist."], links)

    def track_summary(self, track: str, artist: str | None = None):
        t, sug = self.resolve_track(track, artist)
        if not t:
            return self._err(f"Unknown track '{track}'", sug)
        uris = t["uris"]
        ph = ",".join("?" * len(uris))
        s = self.q(f"""SELECT any_value(track_name) AS track_name, any_value(artist_names) AS artist_names, any_value(label) AS label,
                              sum(streams)::BIGINT AS streams, max(days) AS days, min(peak_rank) AS peak_rank,
                              min(first_seen) AS first_seen, max(last_seen) AS last_seen FROM track_all WHERE uri IN ({ph})""", *uris)[0]
        mk = self.q(f"""SELECT market, sum(streams)::BIGINT AS streams, min(peak_rank) AS peak_rank FROM track_country
                        WHERE uri IN ({ph}) GROUP BY 1 ORDER BY 2 DESC LIMIT 5""", *uris)
        nm = self.q(f"SELECT count(DISTINCT market) AS n FROM track_country WHERE uri IN ({ph})", *uris)[0]["n"]
        l30 = self.q(f"SELECT coalesce(sum(streams),0)::BIGINT AS s FROM track_day WHERE uri IN ({ph}) AND {self._since(30)}", *uris)[0]["s"]
        data = {"track": s["track_name"], "artists": s["artist_names"].replace("|", ", "), "label": s["label"],
                "all_time_streams": s["streams"], "all_time_streams_human": human(s["streams"]),
                "best_chart_rank": s["peak_rank"], "markets_charted": nm,
                "first_charted": str(s["first_seen"]), "last_charted": str(s["last_seen"]),
                "last_30_days_streams": l30, "last_30_days_streams_human": human(l30),
                "top_markets": [{"country": self.countries.get(m["market"], m["market"]), "streams_human": human(m["streams"]),
                                 "best_rank": m["peak_rank"]} for m in mk]}
        notes = []
        if len(uris) > 1:
            notes.append(f"{len(uris)} chart versions of this song (same title and artists) are combined.")
        other = self.q("SELECT count(DISTINCT artist_names) AS n FROM track_all WHERE lower(track_name) = lower(?)", track)[0]["n"]
        if other > 1:
            notes.append("Other songs share this title; the most-streamed one is shown. Pass the artist to pick another.")
        ln = self._track_link(uris[0], s["track_name"])
        return self._ok(data, notes, [ln] if ln else [])

    def compare_countries(self, country_a: str, country_b: str, period: str = "last_30_days"):
        a = self.country_stats(country_a, period)
        b = self.country_stats(country_b, period)
        for r in (a, b):
            if not r["ok"]:
                return r
        da, db = a["data"], b["data"]
        key = "streams" if "streams" in da else "all_time_streams"
        ratio = round(da[key] / db[key], 2) if db[key] else None
        return self._ok({"period": period, da["country"]: da, db["country"]: db,
                         "first_to_second_ratio": ratio}, a["notes"] + b["notes"], a["links"] + b["links"])

    def monthly_trend(self, subject: str = "global", name: str | None = None, months: int = 12,
                      since: str | None = None, until: str | None = None):
        months = max(2, min(int(months), 120))
        partial_note = []
        if subject == "global":
            rows = self.q("SELECT strftime(date_trunc('month', date), '%Y-%m') AS ym, sum(streams)::BIGINT AS s FROM daily_country GROUP BY 1 ORDER BY 1")
            links = [{"label": "Overview", "href": "#/overview"}]
        elif subject == "country":
            m, sug = self.resolve_country(name or "")
            if not m:
                return self._err(f"Unknown country '{name}'", sug)
            rows = self.q("SELECT strftime(date_trunc('month', date), '%Y-%m') AS ym, sum(streams)::BIGINT AS s FROM daily_country WHERE market = ? GROUP BY 1 ORDER BY 1", m)
            links = [{"label": self.countries[m], "href": f"#/country/{m}"}]
            name = self.countries[m]
        elif subject == "artist":
            a, sug = self.resolve_artist(name or "")
            if not a:
                return self._err(f"Unknown artist '{name}'", sug)
            rows = self.q("SELECT m.ym, sum(m.streams)::BIGINT AS s FROM track_month m JOIN track_artist x USING (uri) WHERE x.artist = ? GROUP BY 1 ORDER BY 1", a)
            links = [x for x in [self._artist_link(a)] if x]
            name = a
        elif subject == "track":
            t, sug = self.resolve_track(name or "")
            if not t:
                return self._err(f"Unknown track '{name}'", sug)
            rows = self.q("SELECT ym, streams AS s FROM track_month WHERE uri = ? ORDER BY ym", t["uri"])
            links = [x for x in [self._track_link(t["uri"], t["track_name"])] if x]
            name = f"{t['track_name']} - {t['artist_names'].replace('|', ', ')}"
        else:
            return self._err("subject must be one of: global, country, artist, track")
        if rows and rows[-1]["ym"] == str(self.as_of)[:7]:
            partial_note = [f"{rows[-1]['ym']} is a partial month (data through {self.as_of})."]
        ym = re.compile(r"^\d{4}-\d{2}$")
        if since or until:
            if (since and not ym.match(since)) or (until and not ym.match(until)):
                return self._err("since/until must look like YYYY-MM")
            rows = [r for r in rows if (not since or r["ym"] >= since) and (not until or r["ym"] <= until)]
            if not rows:
                return self._err("No data in that month range", [f"Data covers {str(self.first)[:7]} to {str(self.as_of)[:7]}"])
        else:
            rows = rows[-months:]
        series = [{"month": r["ym"], "streams": r["s"], "streams_human": human(r["s"])} for r in rows]
        return self._ok({"subject": subject, "name": name, "months": series}, partial_note, links)

    def biggest_movers(self, kind: str = "climbers", limit: int = 5):
        limit = max(1, min(int(limit), MAX_LIMIT))
        base = f"""
            WITH w AS (
              SELECT uri,
                sum(streams) FILTER (WHERE {self._since(7)}) AS cur,
                sum(streams) FILTER (WHERE date <= DATE '{self.as_of}' - INTERVAL 7 DAY
                                       AND date > DATE '{self.as_of}' - INTERVAL 14 DAY) AS prev
              FROM track_day GROUP BY uri)
            SELECT t.uri, t.track_name, t.artist_names, t.first_seen, w.cur::BIGINT AS cur, coalesce(w.prev,0)::BIGINT AS prev
            FROM w JOIN track_all t USING (uri) """
        if kind == "climbers":
            rows = self.q(base + "WHERE w.prev > 200000 AND w.cur > w.prev ORDER BY (w.cur - w.prev) / w.prev DESC LIMIT ?", limit)
        elif kind == "new_entries":
            rows = self.q(base + f"WHERE t.first_seen > DATE '{self.as_of}' - INTERVAL 14 DAY AND w.cur IS NOT NULL ORDER BY w.cur DESC LIMIT ?", limit)
        elif kind == "fallers":
            rows = self.q(base + "WHERE w.prev > 500000 AND w.cur < w.prev ORDER BY (w.cur - w.prev) / w.prev ASC LIMIT ?", limit)
        else:
            return self._err("kind must be one of: climbers, new_entries, fallers")
        out, links = [], [{"label": "Trending", "href": "#/trending"}]
        for r in rows:
            chg = round(100.0 * (r["cur"] - r["prev"]) / r["prev"], 1) if r["prev"] else None
            out.append({"track": r["track_name"], "artists": r["artist_names"].replace("|", ", "),
                        "streams_last_7_days": r["cur"], "streams_last_7_days_human": human(r["cur"]),
                        "streams_previous_7_days_human": human(r["prev"]), "change_pct": chg,
                        "first_charted": str(r["first_seen"])})
            ln = self._track_link(r["uri"], r["track_name"])
            if ln:
                links.append(ln)
        return self._ok({"kind": kind, "tracks": out}, ["Compares the last 7 days with the 7 days before."], links)


# ---- tool registry for the LLM (OpenAI-style function schemas) ----
_PERIOD = {"type": "string", "enum": sorted(PERIODS), "description": "Time window. Defaults to last_30_days."}
TOOL_SPECS = [
    {"name": "data_status", "description": "Freshness of the data and markets with no recent stream counts in the source.", "parameters": {"type": "object", "properties": {}}},
    {"name": "global_overview", "description": "Total charted streams across all markets, number of tracks/artists, and change vs the previous period.", "parameters": {"type": "object", "properties": {"period": _PERIOD}}},
    {"name": "country_stats", "description": "Streams for one country in a period, its share of all markets, change vs previous period, and data freshness.", "parameters": {"type": "object", "properties": {"country": {"type": "string"}, "period": _PERIOD}, "required": ["country"]}},
    {"name": "top_tracks", "description": "Most-streamed tracks, globally or in one country. Country windows are limited to 60 days.", "parameters": {"type": "object", "properties": {"period": _PERIOD, "country": {"type": "string", "description": "Optional. Omit for global."}, "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT}}}},
    {"name": "top_artists", "description": "Most-streamed artists, globally or in one country. Collaborations count for every credited artist.", "parameters": {"type": "object", "properties": {"period": _PERIOD, "country": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT}}}},
    {"name": "artist_summary", "description": "Profile of one artist: rank, streams, top tracks, top markets, last-30-day streams.", "parameters": {"type": "object", "properties": {"artist": {"type": "string"}}, "required": ["artist"]}},
    {"name": "track_summary", "description": "Profile of one track: streams, best rank, markets, last-30-day streams.", "parameters": {"type": "object", "properties": {"track": {"type": "string"}, "artist": {"type": "string", "description": "Optional, to disambiguate."}}, "required": ["track"]}},
    {"name": "compare_countries", "description": "Compare two countries over a period.", "parameters": {"type": "object", "properties": {"country_a": {"type": "string"}, "country_b": {"type": "string"}, "period": _PERIOD}, "required": ["country_a", "country_b"]}},
    {"name": "monthly_trend", "description": "Monthly streams for the whole world, one country, one artist or one track.", "parameters": {"type": "object", "properties": {"subject": {"type": "string", "enum": ["global", "country", "artist", "track"]}, "name": {"type": "string", "description": "Required unless subject is global."}, "months": {"type": "integer", "minimum": 2, "maximum": 120, "description": "How many most-recent months. Ignored if since is given."}, "since": {"type": "string", "description": "First month, YYYY-MM. Use for a specific past month or range."}, "until": {"type": "string", "description": "Last month, YYYY-MM. Optional."}}, "required": ["subject"]}},
    {"name": "biggest_movers", "description": "Tracks rising fastest, newly entered, or dropping, comparing the last 7 days with the 7 days before.", "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["climbers", "new_entries", "fallers"]}, "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT}}, "required": ["kind"]}},
]
TOOL_NAMES = {s["name"] for s in TOOL_SPECS}


def call_tool(facts: Facts, name: str, args: dict):
    """Run a tool by name with model-supplied args. Never raises."""
    if name not in TOOL_NAMES:
        return Facts._err(f"Unknown tool '{name}'")
    try:
        allowed = next(s for s in TOOL_SPECS if s["name"] == name)["parameters"]["properties"]
        clean = {k: v for k, v in (args or {}).items() if k in allowed}
        return getattr(facts, name)(**clean)
    except (ValueError, TypeError) as e:
        return Facts._err(str(e))
    except Exception:  # a tool bug must never take the chat down
        log.exception("tool %s failed", name)
        return Facts._err("That lookup failed. Try rephrasing the question.")
