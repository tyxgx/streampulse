/* StreamPulse dashboard: static pages reading pre-aggregated JSON from ./data */
"use strict";

const DATA = "data/";
const app = document.getElementById("app");
const cache = new Map();
let charts = [];

// ---------- helpers ----------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => {
  if (n == null) return "-";
  const a = Math.abs(n);
  if (a >= 1e12) return (n / 1e12).toFixed(2) + "T";
  if (a >= 1e9) return (n / 1e9).toFixed(a >= 1e11 ? 0 : 2) + "B";
  if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e8 ? 0 : 1) + "M";
  if (a >= 1e3) return (n / 1e3).toFixed(0) + "K";
  return String(n);
};
const num = (n) => (n == null ? "-" : Number(n).toLocaleString("en-US"));
const pct = (p) => (p == null ? "-" : `<span class="${p >= 0 ? "up" : "down"}">${p >= 0 ? "+" : ""}${p}%</span>`);
const artistsOf = (s) => esc(String(s || "").split("|").join(", "));
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

async function load(path) {
  if (cache.has(path)) return cache.get(path);
  const r = await fetch(path === "world.json" ? path : DATA + path);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  const j = await r.json();
  cache.set(path, j);
  return j;
}

function destroyCharts() { charts.forEach((c) => c.destroy()); charts = []; }

function chartDefaults() {
  Chart.defaults.color = css("--muted");
  Chart.defaults.borderColor = css("--line");
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.plugins.legend.labels.boxWidth = 10;
}

function addChart(id, config) {
  chartDefaults();
  const el = document.getElementById(id);
  if (!el) return;
  config.options = Object.assign({ responsive: true, maintainAspectRatio: false, animation: { duration: 350 } }, config.options || {});
  charts.push(new Chart(el, config));
}

const axisFmt = { ticks: { callback: (v) => fmt(v) } };
const tipFmt = { callbacks: { label: (c) => `${c.dataset.label || ""} ${fmt(c.parsed.y ?? c.parsed.x)}` } };

function lineChart(id, labels, series, opts = {}) {
  addChart(id, {
    type: "line",
    data: {
      labels,
      datasets: series.map((s, i) => ({
        label: s.label, data: s.data, borderColor: s.color || [css("--accent"), css("--accent2"), css("--warn")][i % 3],
        backgroundColor: (s.color || css("--accent")) + "22", fill: opts.fill ?? i === 0, tension: 0.25, pointRadius: 0, borderWidth: 2,
        yAxisID: s.axis || "y",
      })),
    },
    options: {
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: series.length > 1 }, tooltip: opts.tooltip || tipFmt },
      scales: Object.assign({ x: { ticks: { maxTicksLimit: 9 }, grid: { display: false } }, y: Object.assign({ beginAtZero: true }, axisFmt) }, opts.scales || {}),
    },
  });
}

function barChart(id, labels, data, opts = {}) {
  addChart(id, {
    type: "bar",
    data: { labels, datasets: [{ label: opts.label || "", data, backgroundColor: opts.colors || css("--accent"), borderRadius: 5 }] },
    options: {
      indexAxis: opts.horizontal ? "y" : "x",
      plugins: { legend: { display: false }, tooltip: opts.tooltip || tipFmt },
      scales: { x: opts.horizontal ? axisFmt : { grid: { display: false } }, y: opts.horizontal ? { grid: { display: false } } : Object.assign({ beginAtZero: true }, axisFmt) },
    },
  });
}

function kpis(items) {
  return `<div class="grid g4">${items.map(([v, l]) => `<div class="card kpi"><div class="v">${v}</div><div class="l">${esc(l)}</div></div>`).join("")}</div>`;
}

function bars(items, nameKey, valKey, href) {
  const max = Math.max(...items.map((i) => i[valKey]), 1);
  return items.map((i) => {
    const name = href ? `<a href="${href(i)}">${esc(i[nameKey])}</a>` : esc(i[nameKey]);
    return `<div class="bar"><span class="n">${name}</span><span class="t"><i style="width:${(100 * i[valKey] / max).toFixed(1)}%"></i></span><span class="x">${fmt(i[valKey])}</span></div>`;
  }).join("");
}

// sortable + filterable table; rows: [{...}], cols: [{h, k, num?, html?(row)}]
function table(id, cols, rows, opts = {}) {
  const state = { key: opts.sort || cols[0].k, dir: opts.dir || -1, q: "" };
  const host = document.createElement("div");
  host.innerHTML = (opts.search ? `<input class="search" placeholder="${esc(opts.search)}" aria-label="Search">` : "") +
    `<div style="overflow-x:auto"><table><thead><tr>${cols.map((c) => `<th class="${c.num ? "num" : ""}" data-k="${c.k}">${esc(c.h)}</th>`).join("")}</tr></thead><tbody></tbody></table></div>`;
  const body = host.querySelector("tbody");
  const draw = () => {
    const q = state.q.toLowerCase();
    let r = rows.filter((x) => !q || (opts.text ? opts.text(x) : JSON.stringify(x)).toLowerCase().includes(q));
    r = r.slice().sort((a, b) => {
      const av = a[state.key] ?? -Infinity, bv = b[state.key] ?? -Infinity;
      return (av > bv ? 1 : av < bv ? -1 : 0) * state.dir;
    });
    body.innerHTML = r.slice(0, opts.limit || 500).map((x) => `<tr>${cols.map((c) => `<td class="${c.num ? "num" : ""}">${c.html ? c.html(x) : esc(x[c.k])}</td>`).join("")}</tr>`).join("") ||
      `<tr><td colspan="${cols.length}" class="muted">No matches</td></tr>`;
  };
  host.querySelectorAll("th").forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.k;
    state.dir = state.key === k ? -state.dir : -1; state.key = k; draw();
  }));
  const inp = host.querySelector("input");
  if (inp) inp.addEventListener("input", () => { state.q = inp.value; draw(); });
  draw();
  return host;
}

function mount(html) { app.innerHTML = html; window.scrollTo(0, 0); }
function slot(id, el) { document.getElementById(id).replaceChildren(el); }

// ---------- pages ----------
const pages = {};

pages.overview = async () => {
  const [meta, ov] = await Promise.all([load("meta.json"), load("overview.json")]);
  mount(`
    <h1>Spotify charts, across 72 markets</h1>
    <p class="sub">Daily top-200 chart data from ${esc(meta.first_date)} to ${esc(meta.last_date)}. A pipeline refreshes it every day and rebuilds every number below.</p>
    ${kpis([[fmt(meta.streams), "Charted streams, all time"], [num(meta.tracks), "Tracks charted"], [num(meta.artists), "Artists"], [num(meta.chart_rows), "Daily chart entries"]])}
    <div class="grid g2" style="margin-top:14px">
      <div class="card"><h2>Charted streams per month (all markets)</h2><div class="chart"><canvas id="c1"></canvas></div></div>
      <div class="card"><h2>Last 120 days, per day</h2><div class="chart"><canvas id="c2"></canvas></div></div>
      <div class="card"><h2>Top tracks, last 30 days</h2>${bars(ov.top_tracks.map((t) => ({ ...t, name: `${t.track_name} - ${String(t.artist_names).split("|").join(", ")}` })), "name", "streams", (t) => `#/track/${t.id}`)}</div>
      <div class="card"><h2>Top artists, last 30 days</h2>${bars(ov.top_artists, "artist", "streams")}</div>
    </div>`);
  lineChart("c1", ov.monthly.map((m) => m.ym), [{ label: "Streams", data: ov.monthly.map((m) => m.streams) }]);
  lineChart("c2", ov.daily.map((d) => String(d.date).slice(0, 10)), [{ label: "Streams", data: ov.daily.map((d) => d.streams) }]);
};

// world map -------------------------------------------------
const NAME_ALIAS = { "United States": "United States of America", "Czech Republic": "Czechia", "Dominican Republic": "Dominican Rep." };
const POINTS = { sg: [103.82, 1.35], hk: [114.17, 22.32] };

pages.map = async () => {
  const [cs, world] = await Promise.all([load("countries.json"), load("world.json")]);
  mount(`<h1>World map</h1><p class="sub">Charted streams by market. Click a country for its page.</p>
    <div class="seg" id="seg"><button data-m="last30" class="on">Last 30 days</button><button data-m="total">All time</button><button data-m="mom_pct">30-day change</button></div>
    <div class="card"><div id="map"></div><div class="legend"><span>low</span><i></i><span>high</span><span id="note" class="muted"></span></div></div>`);
  const byName = new Map(cs.map((c) => [NAME_ALIAS[c.country_name] || c.country_name, c]));
  const feats = topojson.feature(world, world.objects.countries).features;
  const el = document.getElementById("map");
  const W = 960, H = 500;
  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", "Choropleth of charted streams by country");
  const proj = d3.geoNaturalEarth1().fitSize([W, H], { type: "FeatureCollection", features: feats.filter((f) => f.properties.name !== "Antarctica") });
  const path = d3.geoPath(proj);
  const tip = document.createElement("div"); tip.className = "tip"; tip.style.display = "none"; document.body.appendChild(tip);
  let metric = "last30";
  const ramp = (t) => (t < 0.6 ? d3.interpolateRgb("#16251c", "#1db954")(t / 0.6) : d3.interpolateRgb("#1db954", "#e8ff9a")((t - 0.6) / 0.4));
  const scales = {};
  for (const k of ["last30", "total"]) {
    const vals = cs.map((x) => x[k]).filter((v) => v > 0);
    scales[k] = d3.scaleSequentialLog(ramp).domain([d3.min(vals), d3.max(vals)]);
  }
  const color = (c) => {
    if (!c || c[metric] == null) return css("--panel2");
    if (metric === "mom_pct") return d3.interpolateRdYlGn(Math.max(0, Math.min(1, (c.mom_pct + 40) / 80)));
    return scales[metric](c[metric]);
  };
  const info = (c, name) => c ? `<b>${esc(c.country_name)}</b><br>Last 30 days: ${fmt(c.last30)}<br>All time: ${fmt(c.total)}<br>30-day change: ${c.mom_pct == null ? "-" : c.mom_pct + "%"}` : `${esc(name)}<br><span class="muted">no chart data</span>`;
  const shapes = svg.selectAll("path").data(feats.filter((f) => f.properties.name !== "Antarctica")).join("path").attr("d", path).attr("stroke", css("--bg")).attr("stroke-width", 0.5)
    .style("cursor", (f) => byName.has(f.properties.name) ? "pointer" : "default")
    .on("mousemove", (e, f) => { tip.style.display = "block"; tip.style.left = e.clientX + 14 + "px"; tip.style.top = e.clientY + 14 + "px"; tip.innerHTML = info(byName.get(f.properties.name), f.properties.name); })
    .on("mouseleave", () => (tip.style.display = "none"))
    .on("click", (e, f) => { const c = byName.get(f.properties.name); if (c) location.hash = `#/country/${c.market}`; });
  const dots = svg.selectAll("circle").data(cs.filter((c) => POINTS[c.market])).join("circle").attr("r", 5).attr("stroke", css("--bg")).style("cursor", "pointer")
    .attr("cx", (c) => proj(POINTS[c.market])[0]).attr("cy", (c) => proj(POINTS[c.market])[1])
    .on("mousemove", (e, c) => { tip.style.display = "block"; tip.style.left = e.clientX + 14 + "px"; tip.style.top = e.clientY + 14 + "px"; tip.innerHTML = info(c); })
    .on("mouseleave", () => (tip.style.display = "none")).on("click", (e, c) => (location.hash = `#/country/${c.market}`));
  const paint = () => {
    shapes.attr("fill", (f) => color(byName.get(f.properties.name)));
    dots.attr("fill", (c) => color(c));
    const stale = cs.filter((c) => c.last30 == null).map((c) => c.country_name);
    document.getElementById("note").textContent = stale.length ? ` Grey = no stream counts in the last 30 days (${stale.join(", ")}) or not on Spotify charts.` : "";
  };
  paint();
  document.getElementById("seg").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    metric = b.dataset.m; document.querySelectorAll("#seg button").forEach((x) => x.classList.toggle("on", x === b)); paint();
  });
  app.addEventListener("route-away", () => tip.remove(), { once: true });
};

pages.countries = async () => {
  const cs = await load("countries.json");
  mount(`<h1>Countries</h1><p class="sub">Every market, sortable. Click a header to sort.</p><div class="card" id="t"></div>`);
  slot("t", table("t", [
    { h: "Country", k: "country_name", html: (c) => `<a href="#/country/${c.market}">${esc(c.country_name)}</a>` },
    { h: "Last 30 days", k: "last30", num: true, html: (c) => fmt(c.last30) },
    { h: "30-day change", k: "mom_pct", num: true, html: (c) => pct(c.mom_pct) },
    { h: "All time", k: "total", num: true, html: (c) => fmt(c.total) },
    { h: "Latest data", k: "last_date", num: true, html: (c) => `${esc(String(c.last_date).slice(0, 10))}${c.last30 == null ? (c.streams_missing ? ' <span class="tag warn">ranks only</span>' : ' <span class="tag warn">stale</span>') : ""}` },
  ], cs, { sort: "last30", search: "Search countries", text: (c) => c.country_name, limit: 100 }));
};

pages.country = async (m) => {
  const c = await load(`country/${m}.json`);
  const last = c.monthly[c.monthly.length - 1];
  const d10 = (x) => esc(String(x).slice(0, 10));
  const sub = c.streams_missing
    ? `Stream counts through ${d10(c.last_date)}. Chart positions through ${d10(c.rank_through)}.`
    : c.last30 == null ? `<span class="tag warn">No data since ${d10(c.last_date)}</span>` : `Latest chart day ${d10(c.last_date)}.`;
  const notice = c.streams_missing
    ? `<div class="card notice"><b>Stream counts are not published for ${esc(c.country_name)} after ${d10(c.last_date)}.</b> The chart itself continues, so the lists below use chart positions, which run through ${d10(c.rank_through)}.</div>` : "";
  const ranked = (items) => items.map((t) => `<li><span class="rk-n">${t.rank ?? ""}</span><span class="rk-t">${t.id ? `<a href="#/track/${esc(t.id)}">${esc(t.track_name)}</a>` : esc(t.track_name)}<small>${artistsOf(t.artist_names)}</small></span><span class="rk-v">${t.days_top10 != null ? `${t.days_top10} of ${t.days_charted} days in top 10` : ""}</span></li>`).join("");
  const posCards = (c.latest_chart && c.latest_chart.length) ? `
      <div class="card"><h2>Latest chart, top 10 (${d10(c.rank_through)})</h2><ol class="chartlist">${ranked(c.latest_chart)}</ol></div>
      <div class="card"><h2>Most days in the top 10, last 30 days</h2><ol class="chartlist">${ranked(c.top_by_rank_30d.map((t, i) => ({ ...t, rank: i + 1 })))}</ol></div>` : "";
  mount(`<div class="crumb"><a href="#/countries">Countries</a> / ${esc(c.country_name)}</div><h1>${esc(c.country_name)}</h1>
    <p class="sub">${sub}</p>${notice}
    ${kpis([[fmt(c.total), "Charted streams, all time"], [fmt(c.last30), "Last 30 days"], [c.mom_pct == null ? "-" : `${c.mom_pct}%`, "Change vs previous 30 days"], [fmt(last?.streams), `Last full month (${last?.ym || "-"})`]])}
    <div class="grid g2" style="margin-top:14px">
      ${c.streams_missing ? posCards : ""}
      <div class="card" style="grid-column:1/-1"><h2>Charted streams per month</h2><div class="chart"><canvas id="c1"></canvas></div></div>
      ${c.streams_missing ? "" : posCards}
      <div class="card"><h2>Top tracks, last 30 days</h2>${bars(c.recent_top_tracks.map((t) => ({ ...t, name: `${t.track_name} - ${String(t.artist_names).split("|").join(", ")}` })), "name", "streams", (t) => `#/track/${t.id}`) || '<p class="muted">No stream counts in this window</p>'}</div>
      <div class="card"><h2>Top tracks, all time</h2>${bars(c.top_tracks.map((t) => ({ ...t, name: `${t.track_name} - ${String(t.artist_names).split("|").join(", ")}` })), "name", "streams", (t) => `#/track/${t.id}`)}</div>
      <div class="card"><h2>Top artists, all time</h2>${bars(c.top_artists, "artist", "streams")}</div>
    </div>`);
  lineChart("c1", c.monthly.map((x) => x.ym), [{ label: "Streams", data: c.monthly.map((x) => x.streams) }]);
};

pages.artists = async () => {
  const as = await load("artists.json");
  mount(`<h1>Artists</h1><p class="sub">Top ${as.length} artists by all-time charted streams. Features and collaborations count for every credited artist.</p><div class="card" id="t"></div>`);
  slot("t", table("t", [
    { h: "#", k: "id", num: true, html: (a) => a.id },
    { h: "Artist", k: "artist", html: (a) => `<a href="#/artist/${a.id}">${esc(a.artist)}</a>` },
    { h: "Streams", k: "streams", num: true, html: (a) => fmt(a.streams) },
    { h: "Tracks charted", k: "tracks", num: true, html: (a) => num(a.tracks) },
    { h: "Top-10 tracks", k: "top10_tracks", num: true, html: (a) => num(a.top10_tracks) },
    { h: "Peak rank", k: "peak_rank", num: true, html: (a) => num(a.peak_rank) },
  ], as, { sort: "streams", search: "Search artists", text: (a) => a.artist, limit: 300 }));
};

pages.artist = async (id) => {
  const a = await load(`artist/${id}.json`);
  mount(`<div class="crumb"><a href="#/artists">Artists</a> / ${esc(a.artist)}</div><h1>${esc(a.artist)}</h1>
    ${kpis([[fmt(a.streams), "Charted streams"], [num(a.tracks), "Tracks charted"], [num(a.top10_tracks), "Tracks that hit top 10"], [num(a.peak_rank), "Best chart rank"]])}
    <div class="grid g2" style="margin-top:14px">
      <div class="card" style="grid-column:1/-1"><h2>Charted streams per month</h2><div class="chart"><canvas id="c1"></canvas></div></div>
      <div class="card"><h2>Top tracks</h2>${bars(a.tracks_top, "track_name", "streams", (t) => `#/track/${t.id}`)}</div>
      <div class="card"><h2>Top markets</h2>${bars(a.countries, "country_name", "streams", (c) => `#/country/${c.market}`)}</div>
    </div>`);
  lineChart("c1", a.monthly.map((x) => x.ym), [{ label: "Streams", data: a.monthly.map((x) => x.streams) }]);
};

pages.tracks = async () => {
  const ts = await load("tracks.json");
  mount(`<h1>Tracks</h1><p class="sub">Top ${ts.length} tracks by all-time charted streams.</p><div class="card" id="t"></div>`);
  slot("t", table("t", [
    { h: "Track", k: "track_name", html: (t) => `<a href="#/track/${t.id}">${esc(t.track_name)}</a><div class="muted" style="font-size:12px">${artistsOf(t.artist_names)}</div>` },
    { h: "Streams", k: "streams", num: true, html: (t) => fmt(t.streams) },
    { h: "Markets", k: "markets", num: true, html: (t) => num(t.markets) },
    { h: "Peak rank", k: "peak_rank", num: true, html: (t) => num(t.peak_rank) },
  ], ts, { sort: "streams", search: "Search tracks or artists", text: (t) => `${t.track_name} ${t.artist_names}`, limit: 500 }));
};

pages.track = async (id) => {
  const t = await load(`track/${id}.json`);
  mount(`<div class="crumb"><a href="#/tracks">Tracks</a> / ${esc(t.track_name)}</div><h1>${esc(t.track_name)}</h1>
    <p class="sub">${artistsOf(t.artist_names)}${t.label ? ` &middot; ${esc(t.label)}` : ""}</p>
    ${kpis([[fmt(t.streams), "Charted streams"], [num(t.days), "Days on a chart"], [num(t.markets), "Markets charted"], [num(t.peak_rank), "Best rank"]])}
    <div class="grid g2" style="margin-top:14px">
      <div class="card"><h2>Daily streams, last ~7 months</h2><div class="chart"><canvas id="c1"></canvas></div></div>
      <div class="card"><h2>Best rank per day (lower is better)</h2><div class="chart"><canvas id="c2"></canvas></div></div>
      <div class="card"><h2>Streams per month</h2><div class="chart"><canvas id="c3"></canvas></div></div>
      <div class="card"><h2>Top markets</h2>${bars(t.countries, "country_name", "streams", (c) => `#/country/${c.market}`)}</div>
    </div>`);
  const d = t.daily.map((x) => String(x.date).slice(0, 10));
  if (d.length) {
    lineChart("c1", d, [{ label: "Streams", data: t.daily.map((x) => x.streams) }]);
    lineChart("c2", d, [{ label: "Best rank", data: t.daily.map((x) => x.best_rank), color: css("--accent2") }],
      { fill: false, tooltip: { callbacks: { label: (c) => `Rank ${c.parsed.y}` } }, scales: { y: { reverse: true, min: 1, ticks: { callback: (v) => v } } } });
  }
  lineChart("c3", t.monthly.map((x) => x.ym), [{ label: "Streams", data: t.monthly.map((x) => x.streams) }]);
};

pages.trending = async () => {
  const t = await load("trending.json");
  const tbl = (rows, cols) => `<div style="overflow-x:auto"><table><thead><tr><th>Track</th>${cols.map((c) => `<th class="num">${c[0]}</th>`).join("")}</tr></thead><tbody>` +
    rows.map((r) => `<tr><td><a href="#/track/${r.id}">${esc(r.track_name)}</a><div class="muted" style="font-size:12px">${artistsOf(r.artist_names)}</div></td>${cols.map((c) => `<td class="num">${c[1](r)}</td>`).join("")}</tr>`).join("") + "</tbody></table></div>";
  mount(`<h1>Trending</h1><p class="sub">Last 7 days against the 7 days before, as of ${esc(String(t.as_of).slice(0, 10))}.</p>
    <div class="grid g2">
      <div class="card"><h2>Fastest climbers</h2><p class="muted" style="margin-top:-6px;font-size:13px">Tracks with at least 200K streams the week before.</p>${tbl(t.climbers, [["Now", (r) => fmt(r.cur)], ["Before", (r) => fmt(r.prev)], ["Change", (r) => pct(r.change_pct)]])}</div>
      <div class="card"><h2>New entries</h2><p class="muted" style="margin-top:-6px;font-size:13px">First charted in the last 14 days.</p>${tbl(t.new_entries, [["7-day streams", (r) => fmt(r.cur)], ["Markets", (r) => num(r.markets)], ["First seen", (r) => esc(String(r.first_seen).slice(0, 10))]])}</div>
      <div class="card" style="grid-column:1/-1"><h2>Biggest drops</h2>${tbl(t.fallers, [["Now", (r) => fmt(r.cur)], ["Before", (r) => fmt(r.prev)], ["Change", (r) => pct(r.change_pct)]])}</div>
    </div>`);
};

pages.labels = async () => {
  const l = await load("labels.json");
  const years = [...new Set(l.by_year.map((r) => r.year))];
  const names = [...new Set(l.by_year.map((r) => r.label))];
  mount(`<h1>Labels</h1><p class="sub">Which record labels own the charts.</p>
    <div class="grid g2"><div class="card"><h2>Top labels, all time</h2>${bars(l.labels.slice(0, 15), "label", "streams")}</div>
    <div class="card"><h2>Top 10 labels, share by year</h2><div class="chart tall"><canvas id="c1"></canvas></div></div></div>`);
  const pal = ["#1db954", "#5ad1ff", "#f5b84b", "#ff6b6b", "#b48cff", "#ff9ecf", "#9be15d", "#3ddcd0", "#e8ff9a", "#8a9a8f"];
  addChart("c1", {
    type: "bar",
    data: {
      labels: years,
      datasets: names.map((n, i) => ({
        label: n, backgroundColor: pal[i % pal.length],
        data: years.map((y) => { const tot = l.by_year.filter((r) => r.year === y).reduce((s, r) => s + r.streams, 0); const r = l.by_year.find((r) => r.year === y && r.label === n); return r ? +(100 * r.streams / tot).toFixed(1) : 0; }),
      })),
    },
    options: { plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${c.parsed.y}%` } } }, scales: { x: { stacked: true, grid: { display: false } }, y: { stacked: true, max: 100, ticks: { callback: (v) => v + "%" } } } },
  });
};

pages.seasonality = async () => {
  const s = await load("seasonality.json");
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const mons = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  mount(`<h1>Seasonality</h1><p class="sub">When people listen. 100 = average. Weekdays use the last year; months use 2019 onward.</p>
    <div class="grid g2"><div class="card"><h2>By weekday</h2><div class="chart"><canvas id="c1"></canvas></div></div>
    <div class="card"><h2>By month of year</h2><div class="chart"><canvas id="c2"></canvas></div></div></div>`);
  const idx = { callbacks: { label: (c) => `Index ${c.parsed.y}` } };
  barChart("c1", s.weekday.map((r) => days[r.dow]), s.weekday.map((r) => r.index), { tooltip: idx });
  lineChart("c2", s.month.map((r) => mons[r.month - 1]), [{ label: "Index", data: s.month.map((r) => r.index) }], { tooltip: idx, scales: { y: { beginAtZero: false, ticks: { callback: (v) => v } } } });
};

pages.how = async () => {
  const [m, h] = await Promise.all([load("meta.json"), load("health.json")]);
  const day = (d) => esc(String(d).slice(0, 10));
  const passing = h.checks.filter((c) => c.ok).length;
  const gaps = h.stale_markets.map((x) => `${esc(x.country_name)} (streams through ${day(x.last_date)})`).join(", ");
  const step = (t, d) => `<li><strong>${t}</strong><span>${d}</span></li>`;
  mount(`<h1>How it works</h1>
    <p class="sub">From a public chart file to the pages you are looking at. Nothing here is mocked: the numbers below are read from the live data.</p>
    <div class="how">
      <div class="card"><h2><span class="n">1</span>Where the data comes from</h2>
        <p>A public Kaggle dataset, <a href="https://www.kaggle.com/datasets/gonzalopezgil/spotify-charts-daily-updated" target="_blank" rel="noopener">spotify-charts-daily-updated</a>, which republishes Spotify's daily top-200 chart of every market.
        It holds <strong>${num(m.chart_rows)}</strong> chart rows for <strong>${m.markets}</strong> markets, <strong>${day(m.first_date)}</strong> to <strong>${day(m.last_date)}</strong>.</p>
        <p class="muted">"Streams" means charted streams: the streams of tracks that were on a market's top-200 that day, not total Spotify streams.${gaps ? ` Gaps come from the source file itself: ${gaps}.` : ""}</p></div>
      <div class="card"><h2><span class="n">2</span>How it is processed</h2>
        <ol class="how-steps">
          ${step("Download", "A scheduled job (GitHub Actions) fetches the file every morning, 03:30 UTC.")}
          ${step("Bronze: raw, never overwritten", "Only new or changed rows are added, each with the date and file it came from. Corrections are kept as versions.")}
          ${step("Silver: cleaned", "Duplicates and unmapped markets are dropped. Silver is rebuilt from Bronze, so it can always be replayed from scratch.")}
          ${step("Gold: summed", "Monthly totals per market, track and artist. Every run checks that Gold adds up to Silver.")}
          ${step("Site files", "Small JSON files for the pages, and a compact table for the chatbot. Visitors never trigger a query.")}
        </ol>
        <p class="muted">Last refresh: ${esc(String(m.generated_at).replace("T", " ").slice(0, 16))} UTC &middot; <a href="#/health">${passing} of ${h.checks.length} data checks passing</a></p></div>
      <div class="card"><h2><span class="n">3</span>How it is shown</h2>
        <p><strong>The pages</strong> are plain HTML and JavaScript on S3 that read those JSON files and draw the charts in your browser. No server runs while you browse.</p>
        <p><strong>The chatbot</strong> ("Ask StreamPulse") works differently: a model picks from eleven fixed data tools, the tools run SQL on the data, and the model only writes the sentence. A checker then rejects any number the tools did not return.</p>
        <p class="muted">Runs on AWS (S3, Lambda, DynamoDB), set up with Terraform. <a href="https://github.com/tyxgx/streampulse" target="_blank" rel="noopener">Source and docs on GitHub</a>.</p></div>
    </div>`);
};

pages.health = async () => {
  const h = await load("health.json");
  mount(`<h1>Data health</h1><p class="sub">Checks run on every refresh. Latest chart day: ${esc(String(h.as_of).slice(0, 10))}.</p>
    <div class="grid g2">
      <div class="card"><h2>Checks <span class="tag ${h.all_ok ? "ok" : "bad"}">${h.all_ok ? "all passing" : "attention"}</span></h2>
        <table><tbody>${h.checks.map((c) => `<tr><td>${esc(c.name)}</td><td><span class="tag ${c.ok ? "ok" : "bad"}">${c.ok ? "pass" : "fail"}</span></td><td class="muted">${esc(c.detail)}</td></tr>`).join("")}</tbody></table></div>
      <div class="card"><h2>Markets without recent stream counts</h2>${h.stale_markets.length ? `<table><tbody>${h.stale_markets.map((m) => `<tr><td>${esc(m.country_name)}</td><td class="muted">streams through ${esc(String(m.last_date).slice(0, 10))}${m.rank_through && m.rank_through > m.last_date ? `, chart positions through ${esc(String(m.rank_through).slice(0, 10))}` : ""}</td></tr>`).join("")}</tbody></table><p class="muted" style="font-size:13px">The source file has no stream counts for these markets after the dates shown (for India the chart itself continues, only the stream column is blank). That comes from the source data, not from the pipeline.</p>` : '<p class="muted">None</p>'}</div>
      <div class="card" style="grid-column:1/-1"><h2>Chart rows per day, last 45 days</h2><div class="chart"><canvas id="c1"></canvas></div></div>
    </div>`);
  barChart("c1", h.per_day.map((d) => String(d.date).slice(5, 10)), h.per_day.map((d) => d.chart_rows), { tooltip: { callbacks: { label: (c) => `${num(c.parsed.y)} rows` } } });
};


// ---------- home ----------
const ICON = (n) => `<i class="ph-light ph-${n}"></i>`;
const nameOf = (t) => esc(String(t.artist_names || "").split("|").join(", "));

pages[""] = async () => {
  const [meta, ov, tr, cs] = await Promise.all([load("meta.json"), load("overview.json"), load("trending.json"), load("countries.json")]);
  const year = String(meta.first_date).slice(0, 4);
  const asof = String(meta.last_date).slice(0, 10);
  const top = ov.top_tracks[0];
  const climber = tr.climbers[0];
  const fresh = tr.new_entries[0];
  const artist = ov.top_artists[0];
  const live = cs.filter((c) => c.last30 > 0).sort((a, b) => b.last30 - a.last30);
  const sum30 = live.reduce((s, c) => s + c.last30, 0);
  const lead = live[0];
  const top8 = live.slice(0, 8);
  const maxv = top8[0].last30;
  const nowTop = ov.top_tracks.slice(0, 3);

  const rows = [
    ["overview", "Overview", "Global trends and the biggest tracks now", "chart-line-up"],
    ["map", "World map", "Every market, coloured by streams", "globe-hemisphere-west"],
    ["countries", "Countries", "72 markets, side by side", "flag"],
    ["artists", "Artists", "The top 300 and where they win", "users-three"],
    ["tracks", "Tracks", "500 songs, day by day", "music-notes"],
    ["trending", "Trending", "Climbers, new entries and drops", "trend-up"],
    ["labels", "Labels", "Who owns the charts", "vinyl-record"],
    ["seasonality", "Seasonality", "When the world listens", "calendar-dots"],
    ["health", "Data health", "What the pipeline checks every run", "heartbeat"],
    ["how", "How it works", "Where the data comes from and how it is processed", "info"],
  ];
  const steps = [
    ["cloud-arrow-down", "Download", "Every morning a GitHub Actions job pulls the latest top-200 chart files from Kaggle."],
    ["broom", "Clean", `DuckDB turns ${fmt(meta.chart_rows)} daily chart rows into tidy Silver tables, one market and day at a time.`],
    ["stack", "Aggregate", "Streams, ranks and trends are summed once, into small files made for the browser."],
    ["rocket-launch", "Publish", "The files land in S3 on AWS and this site reads them directly. No server runs when you visit."],
  ];

  mount(`
  <section class="hero"><div class="wrap">
    <div>
      <h1 class="rv">What the world is <em>streaming</em>, every day.</h1>
      <p class="lead rv" style="--d:80ms">Top-200 charts from ${meta.markets} markets since ${esc(year)}, rebuilt each morning. Browse countries, artists, tracks and breakouts.</p>
      <div class="cta-row rv" style="--d:160ms">
        <a class="btn primary" href="#/overview">Open the dashboard <span class="ico">${ICON("arrow-up-right")}</span></a>
        <a class="btn ghost" href="#/map">World map <span class="ico">${ICON("globe-hemisphere-west")}</span></a>
      </div>
    </div>
    <div class="bezel rv" style="--d:200ms"><div class="core">
      <div class="hero-chart-h"><b>Charted streams per month</b><span>${esc(String(meta.first_date).slice(0, 7))} to ${esc(String(ov.monthly[ov.monthly.length - 1].ym))}</span></div>
      <div class="chart"><canvas id="hc" role="img" aria-label="Line chart of charted streams per month across all markets"></canvas></div>
      <div class="nowlist">${nowTop.map((t, i) => `<a href="#/track/${esc(t.id)}"><span class="r">0${i + 1}</span><span class="t">${esc(t.track_name)}<small>${nameOf(t)}</small></span><span class="v">${fmt(t.streams)}</span></a>`).join("")}</div>
    </div></div>
  </div></section>

  <section class="blk"><div class="wrap">
    <div class="figures">
      <div class="fig rv"><div class="n" data-count="${meta.streams}" data-fmt="big">0</div><div class="l">streams counted on the charts</div></div>
      <div class="fig rv" style="--d:70ms"><div class="n" data-count="${meta.tracks}">0</div><div class="l">different tracks have charted</div></div>
      <div class="fig rv" style="--d:140ms"><div class="n" data-count="${meta.artists}">0</div><div class="l">artists behind them</div></div>
      <div class="fig rv" style="--d:210ms"><div class="n" data-count="${meta.markets}">0</div><div class="l">markets, from Argentina to Vietnam</div></div>
    </div>
  </div></section>

  <section class="blk" style="padding-top:0"><div class="wrap">
    <h2 class="rv">On the charts right now</h2>
    <p class="lead rv" style="--d:60ms">Last 30 days across every market, straight from the latest refresh.</p>
    <div class="bento">
      <a class="tile t-a rv" href="#/track/${esc(top.id)}"><span class="k">Most streamed track</span><div><div class="big">${esc(top.track_name)}</div><div class="by">${nameOf(top)}</div></div><span class="sm">${fmt(top.streams)} streams</span></a>
      <a class="tile t-b rv" style="--d:70ms" href="#/track/${esc(climber.id)}"><span class="k">Fastest climber this week</span><div><div class="big">+${Math.round(climber.change_pct)}%</div><div class="by">${esc(climber.track_name)}</div></div></a>
      <a class="tile t-c rv" style="--d:140ms" href="#/track/${esc(fresh.id)}"><span class="k">Biggest new entry</span><div><div class="big" style="font-size:clamp(22px,2.2vw,30px)">${esc(fresh.track_name)}</div><div class="by">${nameOf(fresh)}</div></div><span class="sm">${fresh.markets} markets in 7 days</span></a>
      <div class="tile t-d rv" style="--d:100ms"><span class="k">Most streamed artist</span><div><div class="big">${esc(artist.artist)}</div></div><span class="sm">${fmt(artist.streams)} streams</span></div>
      <a class="tile t-e rv" href="#/country/${esc(lead.market)}"><span class="k">Biggest market</span><div class="big">${esc(lead.country_name)}</div><span class="sm">${(100 * lead.last30 / sum30).toFixed(1)}% of all charted streams</span></a>
    </div>
  </div></section>

  <section class="blk" style="padding-top:0"><div class="wrap rank-grid">
    <div class="stick"><h2 class="rv">Where the world listens</h2><p class="lead rv" style="--d:60ms;margin-bottom:0">The eight biggest markets over the last 30 days. Open any of them for the full story.</p></div>
    <div>${top8.map((c, i) => `<a class="rk rv" style="--d:${i * 50}ms" href="#/country/${esc(c.market)}"><span class="i">${String(i + 1).padStart(2, "0")}</span><span><span class="nm">${esc(c.country_name)}</span><span class="ln" style="width:${(100 * c.last30 / maxv).toFixed(1)}%"></span></span><span class="vv">${fmt(c.last30)}</span></a>`).join("")}</div>
  </div></section>

  <section class="blk" style="padding-top:0"><div class="wrap">
    <h2 class="rv">Everything you can open</h2>
    <div class="idx" style="margin-top:34px">${rows.map((r, i) => `<a class="rv" style="--d:${i * 40}ms" href="#/${r[0]}">${esc(r[1])} ${ICON("arrow-up-right")}<small>${esc(r[2])}</small></a>`).join("")}</div>
  </div></section>

  <section class="blk" style="padding-top:0"><div class="wrap flow">
    <div class="stick"><h2 class="rv">Rebuilt every morning</h2><p class="lead rv" style="--d:60ms;margin-bottom:0">One automated pipeline keeps every number on this site current.</p></div>
    <div>${steps.map((s, i) => `<div class="fstep rv" style="--d:${i * 70}ms"><div class="ic">${ICON(s[0])}</div><div><h3>${esc(s[1])}</h3><p>${s[2]}</p></div></div>`).join("")}</div>
  </div></section>

  <section class="final"><div class="wrap">
    <h2 class="rv">See what is playing.</h2>
    <div class="cta-row rv" style="--d:100ms"><a class="btn primary" href="#/overview">Open the dashboard <span class="ico">${ICON("arrow-up-right")}</span></a></div>
    <div class="fdata rv" style="--d:160ms">Data through ${esc(asof)}</div>
  </div></section>`);

  // hero chart: real monthly series, draws itself in
  chartDefaults();
  const ctx = document.getElementById("hc").getContext("2d");
  const grad = ctx.createLinearGradient(0, 0, 0, 320);
  grad.addColorStop(0, "rgba(29,185,84,.45)"); grad.addColorStop(1, "rgba(29,185,84,0)");
  charts.push(new Chart(ctx, {
    type: "line",
    data: { labels: ov.monthly.map((m) => m.ym), datasets: [{ label: "Streams", data: ov.monthly.map((m) => m.streams), borderColor: "#1ed760", backgroundColor: grad, fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2.5 }] },
    options: { responsive: true, maintainAspectRatio: false, animation: { duration: reduced() ? 0 : 1600, easing: "easeOutQuart" },
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false }, tooltip: tipFmt },
      scales: { x: { ticks: { maxTicksLimit: 6 }, grid: { display: false } }, y: Object.assign({ beginAtZero: true, grid: { color: "rgba(255,255,255,.05)" } }, axisFmt) } },
  }));
  revealOnScroll();
  countUp();
};

function reduced() { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; }

let io;
function revealOnScroll() {
  const els = document.querySelectorAll(".rv");
  if (reduced() || !("IntersectionObserver" in window)) { els.forEach((e) => e.classList.add("in")); return; }
  io = new IntersectionObserver((entries) => entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }), { threshold: 0.15, rootMargin: "0px 0px -6% 0px" });
  els.forEach((e) => io.observe(e));
}

function countUp() {
  document.querySelectorAll("[data-count]").forEach((el) => {
    const end = Number(el.dataset.count), big = el.dataset.fmt === "big";
    const show = (v) => (big ? fmt(v) : Math.round(v).toLocaleString("en-US"));
    if (reduced()) { el.textContent = show(end); return; }
    const obs = new IntersectionObserver((es) => {
      if (!es[0].isIntersecting) return;
      obs.disconnect();
      const t0 = performance.now(), dur = 1400;
      const tick = (t) => { const p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 4); el.textContent = show(end * e); if (p < 1) requestAnimationFrame(tick); };
      requestAnimationFrame(tick);
    }, { threshold: 0.4 });
    obs.observe(el);
  });
}

// ---------- router ----------
async function route() {
  app.dispatchEvent(new Event("route-away"));
  document.body.classList.remove("navopen");
  destroyCharts();
  if (io) { io.disconnect(); io = null; }
  const [r, arg] = (location.hash.replace(/^#\/?/, "") || "").split("/");
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("on", a.dataset.r === (r === "country" ? "countries" : r === "artist" ? "artists" : r === "track" ? "tracks" : r)));
  app.classList.toggle("home", r === "");
  const page = pages[r];
  try {
    if (!page) { mount('<h1>Not found</h1><p class="sub"><a href="#/">Back to overview</a></p>'); return; }
    await page(arg);
  } catch (e) {
    console.error(e);
    mount(`<h1>Could not load this page</h1><p class="sub">${esc(e.message)}. <a href="#/">Back to overview</a></p>`);
  }
}
window.addEventListener("hashchange", route);
load("meta.json").then((m) => { document.getElementById("asof").textContent = `Data through ${m.last_date}`; }).catch(() => {});
route();
