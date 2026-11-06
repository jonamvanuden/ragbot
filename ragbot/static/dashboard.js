// Monitoring dashboard + raw database viewer for the chat log.
// Charts are plain SVG (no library): column, line with crosshair, segmented bar, horizontal bars.

const $ = (s) => document.querySelector(s);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const COLOR = {
  s1: css("--series-1"), s2: css("--series-2"), below: css("--below"),
  grid: css("--grid"), surface: css("--surface"),
};
const STATUS = {
  done:    { color: css("--good"),     icon: "✓", label: "abgeschlossen" },
  cutoff:  { color: css("--warning"),  icon: "…", label: "abgeschnitten (Token-Limit)" },
  aborted: { color: css("--serious"),  icon: "■", label: "abgebrochen (Browser geschlossen)" },
  error:   { color: css("--critical"), icon: "!", label: "Fehler" },
};

const state = { tab: "overview", period: "7d", table: null, order: null, desc: true, offset: 0, limit: 50 };
let lastData = null;

// ---------- formatting ----------
const nf = new Intl.NumberFormat("de-DE");
const num = (v) => (v == null ? "–" : nf.format(v));
const compact = (v) => (v == null ? "–" : new Intl.NumberFormat("de-DE", { notation: "compact", maximumFractionDigits: 1 }).format(v));
const ms = (v) => (v == null ? "–" : v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toLocaleString("de-DE", { maximumFractionDigits: 1 })} s`);
const sec = (v) => (v == null ? "–" : `${(v / 1000).toLocaleString("de-DE", { maximumFractionDigits: 1 })} s`);
const dt = (iso, opts) => new Date(iso).toLocaleString("de-DE", opts || { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

function el(tag, props = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") e.className = v;
    else if (k === "style") e.style.cssText = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) e.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return e;
}

async function api(url, opts) {
  const r = await fetch(url, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
  return body;
}

// ---------- tooltip ----------
const tipEl = $("#tip");
function showTip(evt, title, rows) {
  tipEl.replaceChildren(el("div", { class: "tt-title" }, title));
  for (const r of rows) {
    tipEl.append(el("div", { class: "tt-row" },
      r.color ? el("span", { class: "tt-key", style: `background:${r.color}` }) : null,
      el("strong", {}, r.value),
      r.name ? el("span", {}, r.name) : null));
  }
  tipEl.hidden = false;
  let x, y;
  if (evt.type.startsWith("pointer")) { x = evt.clientX; y = evt.clientY; }
  else { const b = evt.target.getBoundingClientRect(); x = b.left + b.width / 2; y = b.top; }
  const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
  tipEl.style.left = `${Math.min(x + 14, innerWidth - w - 8)}px`;
  tipEl.style.top = `${y + 14 + h > innerHeight ? y - h - 10 : y + 14}px`;
}
const hideTip = () => { tipEl.hidden = true; };

// ---------- SVG helpers ----------
const NS = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.append(e);
  return e;
}
function niceScale(max, ticks = 4) {
  if (!max || max <= 0) return { max: ticks, step: 1 };
  const raw = max / ticks, mag = 10 ** Math.floor(Math.log10(raw)), n = raw / mag;
  const step = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * mag;
  return { max: Math.ceil(max / step) * step, step };
}
// bar with 4px rounded top, square at the baseline
function barPath(x, y, w, h, r) {
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}
function frame(container, height, yMax, fmtTick, right = 10) {
  container.replaceChildren();
  const W = Math.max(280, container.clientWidth), H = height;
  const m = { t: 10, r: right, b: 26, l: 52 };
  const root = svg("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img" }, container);
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const { max, step } = niceScale(yMax);
  const y = (v) => m.t + ih - (v / max) * ih;
  for (let v = 0; v <= max + step / 1e6; v += step) {
    svg("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: COLOR.grid, "stroke-width": 1 }, root);
    svg("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end", class: "tick" }, root).textContent = fmtTick(v);
  }
  return { root, W, H, m, iw, ih, y };
}
function xLabels(f, labels, xAt) {
  const every = Math.max(1, Math.ceil(labels.length / Math.max(1, Math.floor(f.iw / 58))));
  labels.forEach((lab, i) => {
    if (i % every) return;
    svg("text", { x: xAt(i), y: f.H - 8, "text-anchor": "middle", class: "tick" }, f.root).textContent = lab;
  });
}

// ---------- column chart (single series, or per-bar colors) ----------
function columnChart(container, { labels, values, colors, fmt = num, title = (i) => labels[i], seriesName }) {
  const f = frame(container, 220, Math.max(0, ...values), fmt);
  const band = f.iw / labels.length;
  const bw = Math.max(2, Math.min(24, band - 2));          // ≤ 24px, ≥ 2px gap between bars
  const xAt = (i) => f.m.l + band * (i + 0.5);
  labels.forEach((_, i) => {
    const v = values[i] || 0, top = f.y(v), h = f.m.t + f.ih - top;
    const color = colors ? colors[i] : COLOR.s1;
    const bar = h > 0 ? svg("path", { d: barPath(xAt(i) - bw / 2, top, bw, h, 4), fill: color, class: "mark" }, f.root) : null;
    const hit = svg("rect", { x: f.m.l + band * i, y: f.m.t, width: band, height: f.ih, fill: "transparent",
                              tabindex: 0, class: "hit", "aria-label": `${title(i)}: ${fmt(v)}` }, f.root);
    const on = (e) => { bar?.classList.add("hot"); showTip(e, title(i), [{ value: fmt(v), name: seriesName, color }]); };
    const off = () => { bar?.classList.remove("hot"); hideTip(); };
    hit.addEventListener("pointermove", on); hit.addEventListener("focus", on);
    hit.addEventListener("pointerleave", off); hit.addEventListener("blur", off);
  });
  xLabels(f, labels, xAt);
}

// ---------- line chart (crosshair + one tooltip for all series) ----------
function lineChart(container, { labels, series, fmt, title = (i) => labels[i] }) {
  const all = series.flatMap((s) => s.values).filter((v) => v != null);
  const f = frame(container, 240, Math.max(0, ...all), fmt, 44);
  const n = labels.length;
  const xAt = (i) => f.m.l + (n === 1 ? f.iw / 2 : (f.iw * i) / (n - 1));

  const ends = [];
  for (const s of series) {
    let d = "", pen = false;
    s.values.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${xAt(i)},${f.y(v)}`;
      pen = true;
    });
    if (d) svg("path", { d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, f.root);
    // isolated points (a bucket with data between two empty ones) need a dot to be visible
    s.values.forEach((v, i) => {
      if (v != null && s.values[i - 1] == null && s.values[i + 1] == null)
        svg("circle", { cx: xAt(i), cy: f.y(v), r: 3, fill: s.color }, f.root);
    });
    const li = s.values.map((v, i) => (v == null ? -1 : i)).filter((i) => i >= 0).pop();
    if (li != null) {
      svg("circle", { cx: xAt(li), cy: f.y(s.values[li]), r: 4, fill: s.color, stroke: COLOR.surface, "stroke-width": 2 }, f.root);
      ends.push({ x: xAt(li), y: f.y(s.values[li]), name: s.name });
    }
  }
  // direct end labels, but only if they don't collide (otherwise the legend carries it)
  const collide = ends.some((a, i) => ends.some((b, j) => i < j && Math.abs(a.y - b.y) < 14));
  if (!collide) for (const e of ends)
    svg("text", { x: e.x + 8, y: e.y + 4, class: "endlabel" }, f.root).textContent = e.name;

  // crosshair layer
  const cross = svg("line", { y1: f.m.t, y2: f.m.t + f.ih, stroke: "#9CA3AF", "stroke-width": 1, visibility: "hidden" }, f.root);
  const dots = series.map((s) => svg("circle", { r: 4, fill: s.color, stroke: COLOR.surface, "stroke-width": 2, visibility: "hidden" }, f.root));
  const overlay = svg("rect", { x: f.m.l, y: f.m.t, width: f.iw, height: f.ih, fill: "transparent", tabindex: 0, class: "hit" }, f.root);
  let focusIdx = n - 1;
  const at = (i, evt) => {
    const x = xAt(i);
    cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("visibility", "visible");
    series.forEach((s, k) => {
      const v = s.values[i];
      if (v == null) { dots[k].setAttribute("visibility", "hidden"); return; }
      dots[k].setAttribute("cx", x); dots[k].setAttribute("cy", f.y(v)); dots[k].setAttribute("visibility", "visible");
    });
    showTip(evt, title(i), series.map((s) => ({ value: s.values[i] == null ? "–" : fmt(s.values[i]), name: s.name, color: s.color })));
  };
  const hide = () => { cross.setAttribute("visibility", "hidden"); dots.forEach((d) => d.setAttribute("visibility", "hidden")); hideTip(); };
  overlay.addEventListener("pointermove", (e) => {
    const box = f.root.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * f.W;
    const i = n === 1 ? 0 : Math.max(0, Math.min(n - 1, Math.round(((px - f.m.l) / f.iw) * (n - 1))));
    at(i, e);
  });
  overlay.addEventListener("pointerleave", hide);
  overlay.addEventListener("blur", hide);
  overlay.addEventListener("focus", (e) => at(focusIdx, e));
  overlay.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    focusIdx = Math.max(0, Math.min(n - 1, focusIdx + (e.key === "ArrowRight" ? 1 : -1)));
    at(focusIdx, e);
    e.preventDefault();
  });
  xLabels(f, labels, xAt);
}

// ---------- "Tabelle" toggle under each chart ----------
function setTable(cardId, headers, rows) {
  const card = document.getElementById(cardId);
  const box = card.querySelector(".datatable");
  box.replaceChildren(el("table", {},
    el("thead", {}, el("tr", {}, headers.map((h, i) => el("th", { class: i ? "n" : "" }, h)))),
    el("tbody", {}, rows.map((r) => el("tr", {}, r.map((c, i) => el("td", { class: i ? "n" : "" }, c)))))));
}
document.querySelectorAll(".tbl-btn").forEach((b) => b.addEventListener("click", () => {
  const box = b.closest(".card").querySelector(".datatable");
  box.hidden = !box.hidden;
  b.textContent = box.hidden ? "Tabelle" : "Diagramm";
  b.closest(".card").querySelector(".chart").hidden = !box.hidden;
}));

// ================= OVERVIEW =================
async function loadOverview() {
  const sec_ = $("#tab-overview");
  sec_.classList.add("loading");                        // keep the old frame while refetching
  try {
    lastData = await api(`api/dashboard?period=${state.period}`);
    renderOverview(lastData);
    $("#updated").textContent = `Stand ${dt(lastData.generated, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
  } catch (e) {
    $("#tiles").replaceChildren(el("p", { class: "error" }, `Daten konnten nicht geladen werden: ${e.message}`));
  } finally {
    sec_.classList.remove("loading");
  }
}

function renderOverview(d) {
  const t = d.tiles;
  const tile = (label, value, sub) => el("div", { class: "tile" },
    el("div", { class: "label" }, label), el("div", { class: "value" }, value), sub ? el("div", { class: "sub" }, sub) : null);
  $("#tiles").replaceChildren(
    tile("Anfragen", num(t.requests), `${num(t.sessions)} Sessions`),
    tile("Ø Antwortzeit", sec(t.avg_ms), `95 % unter ${sec(t.p95_ms)}`),
    tile("Ø erstes Token", ms(t.avg_ttft_ms), "Wartezeit bis Text erscheint"),
    tile("Ø Tokens/s", t.avg_tps == null ? "–" : t.avg_tps.toLocaleString("de-DE"), "Generierungsgeschwindigkeit"),
    tile("Tokens gesamt", compact(t.tokens), "Prompt + Antwort"),
    tile("Ohne Kontext", num(t.no_context), `${t.no_context_rate} % der Anfragen`),
    tile("Probleme", num(t.problems), "abgeschnitten, abgebrochen, Fehler"),
  );

  // requests over time
  const tl = d.timeline;
  const bucketTitle = (i) => dt(tl[i].start, d.unit === "hour"
    ? { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { weekday: "short", day: "2-digit", month: "2-digit", year: "numeric" });
  $("#requestsSub").textContent = d.unit === "hour" ? "pro Stunde" : "pro Tag";
  if (!t.requests) $("#chRequests").replaceChildren(el("p", { class: "empty" }, "Keine Anfragen im Zeitraum."));
  else columnChart($("#chRequests"), { labels: tl.map((b) => b.label), values: tl.map((b) => b.count), title: bucketTitle, seriesName: "Anfragen" });
  setTable("card-requests", ["Zeitraum", "Anfragen"], tl.map((b, i) => [bucketTitle(i), num(b.count)]));

  // latency: avg + p95 share one axis (same unit)
  const lat = [
    { name: "Ø", color: COLOR.s1, values: tl.map((b) => b.avg_ms) },
    { name: "p95", color: COLOR.s2, values: tl.map((b) => b.p95_ms) },
  ];
  $("#latencyLegend").replaceChildren(...lat.map((s) => el("span", {}, el("i", { class: "key-line", style: `background:${s.color}` }), s.name === "Ø" ? "Durchschnitt" : "95. Perzentil")));
  if (!tl.some((b) => b.avg_ms != null)) $("#chLatency").replaceChildren(el("p", { class: "empty" }, "Noch keine abgeschlossenen Antworten."));
  else lineChart($("#chLatency"), { labels: tl.map((b) => b.label), series: lat, fmt: sec, title: bucketTitle });
  setTable("card-latency", ["Zeitraum", "Ø", "p95"], tl.map((b, i) => [bucketTitle(i), sec(b.avg_ms), sec(b.p95_ms)]));

  renderStatus(d.status);

  // search quality histogram: bins below MIN_SCORE are gray (dropped)
  const bins = d.scores.bins;
  $("#scoresLegend").replaceChildren(...[
    el("span", {}, el("i", { class: "key-rect", style: `background:${COLOR.s1}` }), `genutzt (≥ ${d.min_score})`),
    el("span", {}, el("i", { class: "key-rect", style: `background:${COLOR.below}` }), "unter MIN_SCORE, verworfen"),
    d.scores.no_hits ? el("span", {}, `${d.scores.no_hits} ohne Treffer`) : null,
  ].filter(Boolean));
  const binTitle = (i) => (i === 0 ? "Score unter 0,25" : i === bins.length - 1 ? "Score ab 0,90"
    : `Score ${bins[i].lo.toFixed(2)} – ${bins[i].hi.toFixed(2)}`);
  if (!bins.some((b) => b.count)) $("#chScores").replaceChildren(el("p", { class: "empty" }, "Noch keine Suchen."));
  else columnChart($("#chScores"), { labels: bins.map((b) => b.label), values: bins.map((b) => b.count),
    colors: bins.map((b) => (b.below_min ? COLOR.below : COLOR.s1)), title: binTitle, seriesName: "Fragen" });
  setTable("card-scores", ["Bester Score", "Fragen"], bins.map((b, i) => [binTitle(i), num(b.count)]));

  // sources
  const maxSrc = Math.max(1, ...d.sources.map((s) => s.count));
  $("#sources").replaceChildren(d.sources.length
    ? el("ul", { class: "hbars" }, d.sources.map((s) => el("li", {},
        el("div", { class: "name" },
          /^https?:/.test(s.source) ? el("a", { href: s.source, target: "_blank", rel: "noopener", title: s.source }, s.title) : el("span", { title: s.source }, s.title),
          el("strong", {}, num(s.count))),
        el("div", { class: "track" }, el("div", { class: "fill", style: `width:${(s.count / maxSrc) * 100}%` })))))
    : el("p", { class: "empty" }, "Noch keine Quellen genutzt."));

  // models
  $("#models").replaceChildren(d.models.length
    ? el("table", {},
        el("thead", {}, el("tr", {}, el("th", {}, "Modell"), el("th", { class: "n" }, "Anfragen"), el("th", { class: "n" }, "Ø Zeit"),
           el("th", { class: "n" }, "Ø tok/s"), el("th", { class: "n" }, "Tokens"))),
        el("tbody", {}, d.models.map((m) => el("tr", {}, el("td", {}, m.model), el("td", { class: "n" }, num(m.requests)),
           el("td", { class: "n" }, sec(m.avg_ms)), el("td", { class: "n" }, m.avg_tps == null ? "–" : m.avg_tps.toLocaleString("de-DE")),
           el("td", { class: "n" }, compact(m.tokens))))))
    : el("p", { class: "empty" }, "Keine Daten."));

  // lists → link into the protocol page
  const list = (items, meta) => items.length
    ? el("ul", { class: "rows" }, items.map((t) => el("li", {},
        el("a", { class: "q", href: `logs.html#${t.id}`, title: t.question }, t.question || "(leer)"),
        el("span", { class: "meta" }, meta(t)))))
    : el("p", { class: "empty" }, "Nichts zu zeigen.");
  $("#nocontext").replaceChildren(list(d.no_context, (t) => `${t.top_score == null ? "kein Treffer" : "bester " + t.top_score.toFixed(2)} · ${dt(t.ts)}`));
  $("#slowest").replaceChildren(list(d.slowest, (t) => `${sec(t.total_ms)} · ${dt(t.ts)}`));
}

function renderStatus(status) {
  const entries = Object.entries(status);
  const total = entries.reduce((a, [, n]) => a + n, 0);
  if (!total) { $("#status").replaceChildren(el("p", { class: "empty" }, "Keine Anfragen im Zeitraum.")); return; }
  const meta = (k) => STATUS[k] || { color: "#9CA3AF", icon: "?", label: k };
  $("#status").replaceChildren(
    el("div", { class: "segbar", role: "img", "aria-label": entries.map(([k, n]) => `${meta(k).label} ${n}`).join(", ") },
      entries.filter(([, n]) => n).map(([k, n]) => el("span", { style: `flex:${n};background:${meta(k).color}`, title: `${meta(k).label}: ${n}` }))),
    el("ul", { class: "statuslist" }, entries.map(([k, n]) => el("li", {},
      el("span", { class: "ico", style: `background:${meta(k).color}` }, meta(k).icon),
      el("span", {}, meta(k).label),
      el("strong", {}, num(n)),
      el("span", { class: "pct" }, `${Math.round((n / total) * 100)} %`)))));
}

// ================= DATABASE =================
const EXAMPLES = [
  ["Letzte 20 Anfragen", "SELECT id, ts, model, status, total_ms, question\nFROM turns\nORDER BY id DESC\nLIMIT 20"],
  ["Ohne Kontext", "SELECT id, ts, question\nFROM turns\nWHERE NOT EXISTS (\n  SELECT 1 FROM json_each(turns.hits) WHERE json_extract(value, '$.kept')\n)\nORDER BY id DESC"],
  ["Langsamste", "SELECT id, model, total_ms, ttft_ms, tokens_per_s, question\nFROM turns\nWHERE status = 'done'\nORDER BY total_ms DESC\nLIMIT 20"],
  ["Tokens pro Tag", "SELECT substr(ts, 1, 10) AS tag,\n       COUNT(*) AS anfragen,\n       SUM(prompt_tokens) AS prompt_tokens,\n       SUM(completion_tokens) AS antwort_tokens\nFROM turns\nGROUP BY tag\nORDER BY tag DESC"],
  ["Meistgenutzte Dokumente", "SELECT json_extract(value, '$.title') AS dokument,\n       COUNT(*) AS treffer,\n       ROUND(AVG(json_extract(value, '$.score')), 3) AS avg_score\nFROM turns, json_each(turns.hits)\nWHERE json_extract(value, '$.kept')\nGROUP BY dokument\nORDER BY treffer DESC\nLIMIT 20"],
  ["Fehler", "SELECT id, ts, model, status, error, question\nFROM turns\nWHERE status <> 'done'\nORDER BY id DESC"],
];

async function loadTables() {
  try {
    const d = await api("api/db/tables");
    $("#dbinfo").replaceChildren("Datei ", el("code", {}, d.db), ` · ${(d.size_bytes / 1024).toLocaleString("de-DE", { maximumFractionDigits: 1 })} KB`);
    $("#tables").replaceChildren(...d.tables.map((t) => el("li", {},
      el("button", { class: t.name === state.table ? "on" : "", onclick: () => { state.table = t.name; state.order = null; state.desc = true; state.offset = 0; loadTables(); } },
        el("span", {}, t.name), el("span", { class: "count" }, num(t.count))))));
    if (!state.table && d.tables.length) { state.table = d.tables[0].name; return loadTables(); }
    if (state.table) loadRows();
  } catch (e) {
    $("#dbinfo").replaceChildren(el("span", { class: "error" }, `Datenbank nicht lesbar: ${e.message}`));
  }
}

async function loadRows() {
  const p = new URLSearchParams({ table: state.table, limit: state.limit, offset: state.offset, desc: state.desc });
  if (state.order) p.set("order", state.order);
  try {
    const d = await api(`api/db/rows?${p}`);
    state.order = d.order;
    $("#tblTitle").textContent = d.table;
    renderGrid($("#tblGrid"), d.columns, d.rows, {
      order: d.order, desc: d.desc,
      onSort: (c) => { state.desc = state.order === c ? !state.desc : true; state.order = c; state.offset = 0; loadRows(); },
    });
    const from = d.total ? d.offset + 1 : 0, to = Math.min(d.offset + d.limit, d.total);
    $("#tblPager").replaceChildren(
      el("span", {}, `${num(from)}–${num(to)} von ${num(d.total)}`),
      el("button", { disabled: d.offset === 0 ? "" : null, onclick: () => { state.offset = Math.max(0, state.offset - state.limit); loadRows(); } }, "‹"),
      el("button", { disabled: to >= d.total ? "" : null, onclick: () => { state.offset += state.limit; loadRows(); } }, "›"));
  } catch (e) {
    $("#tblGrid").replaceChildren(el("p", { class: "error" }, e.message));
  }
}

function renderGrid(box, columns, rows, opts = {}) {
  box.hidden = false;
  if (!columns.length) { box.replaceChildren(el("p", { class: "empty" }, "Keine Spalten.")); return; }
  const head = el("tr", {}, columns.map((c) => {
    const arrow = opts.order === c ? (opts.desc ? " ↓" : " ↑") : "";
    return el("th", opts.onSort ? { class: "sortable", onclick: () => opts.onSort(c), title: "Sortieren" } : {}, c + arrow);
  }));
  const body = rows.map((r) => el("tr", {}, r.map((v, i) => {
    const isNull = v === null;
    const text = isNull ? "NULL" : String(v);
    return el("td", { class: isNull ? "null" : "", title: text.length > 60 ? "Klicken für den vollen Inhalt" : null,
                      onclick: () => openCell(columns[i], v) },
      text.length > 120 ? text.slice(0, 120) + "…" : text);
  })));
  box.replaceChildren(el("table", {}, el("thead", {}, head), el("tbody", {}, rows.length ? body : el("tr", {}, el("td", { colspan: columns.length, class: "null" }, "keine Zeilen")))));
}

function openCell(column, value) {
  let text = value === null ? "NULL" : String(value);
  if (typeof value === "string" && /^\s*[[{]/.test(value)) {
    try { text = JSON.stringify(JSON.parse(value), null, 2); } catch { /* not JSON, show raw */ }
  }
  $("#cellTitle").textContent = column;
  $("#cellBody").textContent = text;
  $("#cell").showModal();
}
$("#cellClose").addEventListener("click", () => $("#cell").close());
$("#cell").addEventListener("click", (e) => { if (e.target === $("#cell")) $("#cell").close(); });

async function runSql() {
  const sql = $("#sql").value;
  $("#sqlError").hidden = true;
  $("#sqlInfo").textContent = "läuft …";
  try {
    const d = await api("api/db/query", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sql }) });
    renderGrid($("#sqlGrid"), d.columns, d.rows);
    $("#sqlInfo").textContent = `${num(d.rows.length)} Zeilen · ${d.ms} ms${d.truncated ? " · auf 1.000 gekürzt" : ""}`;
  } catch (e) {
    $("#sqlInfo").textContent = "";
    $("#sqlError").textContent = e.message;
    $("#sqlError").hidden = false;
    $("#sqlGrid").hidden = true;
  }
}
$("#examples").replaceChildren(...EXAMPLES.map(([label, sql]) =>
  el("button", { onclick: () => { $("#sql").value = sql; runSql(); } }, label)));
$("#sql").value = EXAMPLES[0][1];
$("#run").addEventListener("click", runSql);
$("#sql").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); runSql(); } });

// ================= tabs, range, live =================
function setTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  $("#tab-overview").hidden = tab !== "overview";
  $("#tab-db").hidden = tab !== "db";
  $("#range").hidden = tab !== "overview";
  history.replaceState(null, "", tab === "db" ? "#db" : "#");
  if (tab === "db") loadTables(); else loadOverview();
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
document.querySelectorAll("#range button").forEach((b) => b.addEventListener("click", () => {
  state.period = b.dataset.period;
  document.querySelectorAll("#range button").forEach((x) => x.classList.toggle("on", x === b));
  loadOverview();
}));

let timer;
$("#auto").addEventListener("change", (e) => {
  clearInterval(timer);
  if (e.target.checked) timer = setInterval(() => (state.tab === "db" ? loadTables() : loadOverview()), 30000);
});

// redraw charts on resize (SVG is drawn at the container's pixel width)
let resizeT;
addEventListener("resize", () => {
  clearTimeout(resizeT);
  resizeT = setTimeout(() => { if (lastData && state.tab === "overview") renderOverview(lastData); }, 150);
});

setTab(location.hash === "#db" ? "db" : "overview");
