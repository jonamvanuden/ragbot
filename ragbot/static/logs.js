const $ = (s) => document.querySelector(s);
const state = { q: "", status: "", model: "", session: "", offset: 0, limit: 50, selected: null };

// ---------- helpers ----------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ms = (v) => (v == null ? "–" : v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(1)} s`);
const num = (v) => (v == null ? "–" : Number(v).toLocaleString("de-DE"));
const time = (ts) => new Date(ts).toLocaleString("de-DE", {
  day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
const md = (s) => (window.marked ? DOMPurify.sanitize(marked.parse(s || "")) : `<pre>${esc(s)}</pre>`);
const badge = (s) => `<span class="badge ${esc(s)}">${esc(s || "?")}</span>`;
const metric = (label, value) => `<div><span>${label}</span><strong>${value}</strong></div>`;

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// ---------- list ----------
async function load() {
  const p = new URLSearchParams({ limit: state.limit, offset: state.offset });
  for (const k of ["q", "status", "model", "session"]) if (state[k]) p.set(k, state[k]);
  try {
    const data = await getJSON(`api/logs?${p}`);
    renderStats(data.stats);
    renderModels(data.models);
    renderRows(data.items);
    renderPager(data.total);
  } catch (e) {
    $("#rows").innerHTML = `<tr><td colspan="9" class="empty">Fehler beim Laden: ${esc(e.message)}</td></tr>`;
  }
}

function renderStats(s) {
  const pct = s.count ? Math.round((s.no_context / s.count) * 100) : 0;
  const tiles = [
    ["Anfragen", num(s.count)],
    ["Ø Antwortzeit", ms(s.avg_total_ms)],
    ["Ø erstes Token", ms(s.avg_ttft_ms)],
    ["Ø Tokens/s", s.avg_tps ?? "–"],
    ["Ohne Kontext", `${num(s.no_context)} <small>(${pct} %)</small>`],
    ["Abbrüche / Fehler", num(s.problems)],
  ];
  $("#stats").innerHTML = tiles
    .map(([label, value]) => `<div class="stat"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
}

function renderModels(models) {
  const sel = $("#model");
  const current = [...sel.options].slice(1).map((o) => o.value).join("|");
  if (current === models.join("|")) return;
  sel.innerHTML = `<option value="">Alle Modelle</option>` +
    models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
  sel.value = state.model;
}

function renderRows(items) {
  if (!items.length) {
    $("#rows").innerHTML = `<tr><td colspan="9" class="empty">Keine Einträge.</td></tr>`;
    return;
  }
  $("#rows").innerHTML = items.map((t) => {
    const top = t.top_score != null ? ` · ${t.top_score.toFixed(2)}` : "";
    const hits = t.kept ? `${t.kept}${top}` : `<span class="warn" title="Kein Treffer über MIN_SCORE">0${top}</span>`;
    return `<tr data-id="${t.id}" class="${t.id === state.selected ? "sel" : ""}">
      <td>${t.id}</td>
      <td class="nowrap">${time(t.ts)}</td>
      <td>${badge(t.status)}</td>
      <td class="q" title="${esc(t.question)}">${esc(t.question)}</td>
      <td class="nowrap">${hits}</td>
      <td class="nowrap">${num(t.prompt_tokens)} + ${num(t.completion_tokens)}</td>
      <td class="nowrap">${ms(t.retrieval_ms)}</td>
      <td class="nowrap">${ms(t.total_ms)}</td>
      <td>${t.tokens_per_s ?? "–"}</td>
    </tr>`;
  }).join("");
}

function renderPager(total) {
  const from = total ? state.offset + 1 : 0;
  const to = Math.min(state.offset + state.limit, total);
  $("#pager").innerHTML = `
    <span>${from}–${to} von ${total}</span>
    <button data-dir="-1" ${state.offset === 0 ? "disabled" : ""}>‹ Neuer</button>
    <button data-dir="1" ${to >= total ? "disabled" : ""}>Älter ›</button>`;
}

// ---------- detail ----------
async function select(id) {
  state.selected = id;
  history.replaceState(null, "", `#${id}`);
  document.querySelectorAll("#rows tr[data-id]").forEach((tr) =>
    tr.classList.toggle("sel", Number(tr.dataset.id) === id));

  let t;
  try {
    t = await getJSON(`api/logs/${id}`);
  } catch (e) {
    $("#detail").innerHTML = `<p class="error">Turn ${id} konnte nicht geladen werden (${esc(e.message)}).</p>`;
    return;
  }

  const hits = t.hits.map((h) => {
    const width = Math.max(0, Math.min(1, h.score)) * 100;
    const link = /^https?:/.test(h.source)
      ? `<a href="${esc(h.source)}" target="_blank" rel="noopener">${esc(h.source)}</a>`
      : `<code>${esc(h.source)}</code>`;
    return `<tr class="${h.kept ? "" : "dropped"}">
      <td title="${h.kept ? "an das Modell übergeben" : "unter MIN_SCORE, verworfen"}">${h.kept ? "✓" : "✗"}</td>
      <td class="score"><span class="bar"><i style="width:${width}%"></i></span>${h.score.toFixed(3)}</td>
      <td><details>
        <summary>${esc(h.title)} <small class="muted">#${h.chunk}</small></summary>
        <p class="preview">${esc(h.preview)}…</p>${link}
      </details></td>
    </tr>`;
  }).join("");

  $("#detail").innerHTML = `
    <div class="dhead">
      <h2>#${t.id}</h2>${badge(t.status)}
      <span class="muted">${time(t.ts)} · ${esc(t.model)}</span>
    </div>
    ${t.session_id ? `<button class="chip" data-session="${esc(t.session_id)}">Ganze Session anzeigen</button>` : ""}
    ${t.error ? `<p class="error">${esc(t.error)}</p>` : ""}
    <div class="metrics">
      ${metric("Prompt-Tokens", num(t.prompt_tokens))}
      ${metric("Antwort-Tokens", num(t.completion_tokens))}
      ${metric("Suche", ms(t.retrieval_ms))}
      ${metric("Erstes Token", ms(t.ttft_ms))}
      ${metric("Generierung", ms(t.generation_ms))}
      ${metric("Gesamt", ms(t.total_ms))}
      ${metric("Tokens/s", t.tokens_per_s ?? "–")}
    </div>

    <h3>Frage</h3>
    <p class="question">${esc(t.question)}</p>

    <h3>Vektorsuche</h3>
    <table class="hits"><tbody>${hits || `<tr><td class="empty">Keine Treffer.</td></tr>`}</tbody></table>

    <h3>Antwort</h3>
    <div class="answer">${md(t.answer) || `<p class="muted">(leer)</p>`}</div>

    ${t.thinking ? `<details class="block"><summary>Gedankengang</summary><pre>${esc(t.thinking)}</pre></details>` : ""}
    <details class="block"><summary>Prompt an das Modell (letzte Nachricht)</summary><pre>${esc(t.prompt)}</pre></details>`;
}

// ---------- filters & events ----------
function renderSessionChip() {
  const chip = $("#sessionChip");
  chip.hidden = !state.session;
  chip.innerHTML = state.session ? `Session ${esc(state.session.slice(0, 8))} ✕` : "";
}

function applyFilter(key, value) {
  state[key] = value;
  state.offset = 0;
  load();
}

let debounce;
$("#q").addEventListener("input", (e) => {
  clearTimeout(debounce);
  debounce = setTimeout(() => applyFilter("q", e.target.value.trim()), 300);
});
$("#status").addEventListener("change", (e) => applyFilter("status", e.target.value));
$("#model").addEventListener("change", (e) => applyFilter("model", e.target.value));

$("#sessionChip").addEventListener("click", () => {
  state.session = "";
  renderSessionChip();
  applyFilter("session", "");
});

$("#detail").addEventListener("click", (e) => {
  const b = e.target.closest("[data-session]");
  if (!b) return;
  state.session = b.dataset.session;
  renderSessionChip();
  applyFilter("session", state.session);
});

$("#rows").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (tr) select(Number(tr.dataset.id));
});

$("#pager").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-dir]");
  if (!b || b.disabled) return;
  state.offset = Math.max(0, state.offset + Number(b.dataset.dir) * state.limit);
  load();
});

let timer;
$("#auto").addEventListener("change", (e) => {
  clearInterval(timer);
  if (e.target.checked) timer = setInterval(load, 5000);
});

// ---------- start ----------
load();
const fromHash = Number(location.hash.slice(1));
if (fromHash) select(fromHash);