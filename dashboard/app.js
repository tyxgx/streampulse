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

pages[""] = async () => {
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
    document.getElementById("note").textContent = stale.length ? ` Grey = no data in the last 30 days (${stale.join(", ")}) or not on Spotify charts.` : "";
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
    { h: "Latest data", k: "last_date", num: true, html: (c) => `${esc(String(c.last_date).slice(0, 10))}${c.last30 == null ? ' <span class="tag warn">stale</span>' : ""}` },
  ], cs, { sort: "last30", search: "Search countries", text: (c) => c.country_name, limit: 100 }));
};

pages.country = async (m) => {
  const c = await load(`country/${m}.json`);
  const last = c.monthly[c.monthly.length - 1];
  mount(`<div class="crumb"><a href="#/countries">Countries</a> / ${esc(c.country_name)}</div><h1>${esc(c.country_name)}</h1>
    <p class="sub">${c.last30 == null ? `<span class="tag warn">No chart data since ${esc(String(c.last_date).slice(0, 10))}</span>` : `Latest chart day ${esc(String(c.last_date).slice(0, 10))}.`}</p>
    ${kpis([[fmt(c.total), "Charted streams, all time"], [fmt(c.last30), "Last 30 days"], [c.mom_pct == null ? "-" : `${c.mom_pct}%`, "Change vs previous 30 days"], [fmt(last?.streams), `Last full month (${last?.ym || "-"})`]])}
    <div class="grid g2" style="margin-top:14px">
      <div class="card" style="grid-column:1/-1"><h2>Charted streams per month</h2><div class="chart"><canvas id="c1"></canvas></div></div>
      <div class="card"><h2>Top tracks, last 30 days</h2>${bars(c.recent_top_tracks.map((t) => ({ ...t, name: `${t.track_name} - ${String(t.artist_names).split("|").join(", ")}` })), "name", "streams", (t) => `#/track/${t.id}`) || '<p class="muted">No recent data</p>'}</div>
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

pages.health = async () => {
  const h = await load("health.json");
  mount(`<h1>Data health</h1><p class="sub">Checks run on every refresh. Latest chart day: ${esc(String(h.as_of).slice(0, 10))}.</p>
    <div class="grid g2">
      <div class="card"><h2>Checks <span class="tag ${h.all_ok ? "ok" : "bad"}">${h.all_ok ? "all passing" : "attention"}</span></h2>
        <table><tbody>${h.checks.map((c) => `<tr><td>${esc(c.name)}</td><td><span class="tag ${c.ok ? "ok" : "bad"}">${c.ok ? "pass" : "fail"}</span></td><td class="muted">${esc(c.detail)}</td></tr>`).join("")}</tbody></table></div>
      <div class="card"><h2>Markets with no recent data</h2>${h.stale_markets.length ? `<table><tbody>${h.stale_markets.map((m) => `<tr><td>${esc(m.country_name)}</td><td class="muted">last chart ${esc(String(m.last_date).slice(0, 10))}</td></tr>`).join("")}</tbody></table><p class="muted" style="font-size:13px">These charts stopped in the source data, not in the pipeline.</p>` : '<p class="muted">None</p>'}</div>
      <div class="card" style="grid-column:1/-1"><h2>Chart rows per day, last 45 days</h2><div class="chart"><canvas id="c1"></canvas></div></div>
    </div>`);
  barChart("c1", h.per_day.map((d) => String(d.date).slice(5, 10)), h.per_day.map((d) => d.chart_rows), { tooltip: { callbacks: { label: (c) => `${num(c.parsed.y)} rows` } } });
};

// ---------- router ----------
async function route() {
  app.dispatchEvent(new Event("route-away"));
  document.body.classList.remove("navopen");
  destroyCharts();
  const [r, arg] = (location.hash.replace(/^#\/?/, "") || "").split("/");
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("on", a.dataset.r === (r === "country" ? "countries" : r === "artist" ? "artists" : r === "track" ? "tracks" : r)));
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
