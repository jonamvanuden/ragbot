// Chat UI: two modes
//   "rag"   – questions answered from the knowledge base   (POST api/chat)
//   "agent" – the LangGraph agent uses Personal-Planer tools (POST api/agent/chat, api/agent/resume)
//            and asks for approval before anything that changes data.

const chat = document.getElementById("chat");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");

let history = [];   // [{role, content}] (answers only, never thinking or tool steps)
const newId = () => (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2));
let sessionId = newId();   // groups the turns of one conversation in the log
let busy = false;
let mode = "rag";

const MODE_HINT = {
  rag: "Antworten aus der Wissensbasis",
  agent: "Der Agent nutzt Personal-Planer-Funktionen. Änderungen erst nach Ihrer Freigabe.",
};

// ---------- small helpers ----------
const render = (md) => (window.marked ? DOMPurify.sanitize(marked.parse(md)) : md.replace(/</g, "&lt;"));
const label = (tool) => tool.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());   // urlaub_beantragen → Urlaub beantragen
const short = (obj, n = 140) => { const s = JSON.stringify(obj); return s.length > n ? s.slice(0, n) + " …" : s; };
function scrollDown() { chat.scrollTop = chat.scrollHeight; }

function el(tag, props = {}, ...children) {   // builds DOM safely: text is always text, never HTML
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v != null) e.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return e;
}

// ---------- message bubbles ----------
function addUser(text) {
  chat.appendChild(el("div", { class: "msg user" }, text));
}

function addBot() {
  const row = document.createElement("div");
  row.className = "row";
  row.innerHTML = `
    <img class="avatar" src="assets/favicon.png" alt="">
    <div class="msg bot">
      <details class="steps" hidden open><summary>Arbeitsschritte</summary><ol></ol></details>
      <div class="approvals"></div>
      <details class="think"><summary>Denkt nach…</summary><div class="body"></div></details>
      <div class="answer"></div>
      <div class="sources"></div>
    </div>`;
  chat.appendChild(row);
  const bot = {
    steps: row.querySelector(".steps"),
    stepList: row.querySelector(".steps ol"),
    approvals: row.querySelector(".approvals"),
    think: row.querySelector(".think"),
    summary: row.querySelector(".think summary"),
    thinkBody: row.querySelector(".think .body"),
    answerEl: row.querySelector(".answer"),
    sources: row.querySelector(".sources"),
    answer: "",
    lastTool: null,
  };
  bot.finishThinking = () => {
    if (!bot.thinkBody.textContent.trim()) { bot.think.remove(); return; }
    bot.think.classList.add("done");
    bot.summary.textContent = "Gedankengang anzeigen";
  };
  bot.appendAnswer = (text) => {
    if (!bot.answer) { bot.finishThinking(); bot.steps.open = false; }   // answer starts → fold the steps away
    bot.answer += text;
    bot.answerEl.innerHTML = render(bot.answer);
  };
  bot.notice = (text) => { bot.finishThinking(); bot.answerEl.append(el("p", { class: "notice" }, text)); };
  bot.status = (text, active = true) => {           // the small line under the steps: what's happening now
    if (bot.think.classList.contains("done") || !bot.think.isConnected) return;
    bot.summary.textContent = text;
    bot.think.classList.toggle("waiting", !active);
  };
  return bot;
}

function setBusy(b) {
  busy = b;
  sendBtn.disabled = b;
  document.querySelectorAll("#modes button").forEach((x) => (x.disabled = b));
}

// ---------- NDJSON stream reader ----------
async function readStream(res, onEvent) {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Server antwortet mit ${res.status}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl);
      buf = buf.slice(nl + 1);
      if (line.trim()) onEvent(JSON.parse(line));
    }
  }
}

const postJSON = (url, data) => fetch(url, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
});

// ================= mode "rag": knowledge base chat =================
function renderSources(box, items) {
  const bySource = new Map();
  for (const it of items) {
    const e = bySource.get(it.source) || { title: it.title, nums: [] };
    e.nums.push(it.n);
    bySource.set(it.source, e);
  }
  box.replaceChildren(...[...bySource].map(([src, e]) => {
    const text = `[${e.nums.join("][")}] ${e.title}`;
    return /^https?:/.test(src) ? el("a", { href: src, target: "_blank", rel: "noopener" }, text) : el("span", {}, text);
  }));
}

async function runRag(bot) {
  const res = await postJSON("api/chat", { messages: history.slice(-10), session_id: sessionId });
  let finished = false;
  await readStream(res, (ev) => {
    if (ev.type === "sources") renderSources(bot.sources, ev.items);
    else if (ev.type === "think") bot.thinkBody.textContent += ev.text;
    else if (ev.type === "answer") bot.appendAnswer(ev.text);
    else if (ev.type === "done") finished = true;
    else if (ev.type === "cutoff") bot.notice("Beim Nachdenken ist der Platz ausgegangen. Bitte einfacher fragen.");
    else if (ev.type === "error") throw new Error(ev.text);
    scrollDown();
  });
  return finished;
}

// ================= mode "agent": tools + approvals =================
function addStep(bot, li) {
  bot.steps.hidden = false;
  bot.stepList.append(li);
  const n = bot.stepList.querySelectorAll("li.tool").length;
  bot.steps.querySelector("summary").textContent = `Arbeitsschritte (${n} ${n === 1 ? "Aktion" : "Aktionen"})`;
}

function onAgentEvent(bot, ev) {
  switch (ev.type) {
    case "thought":
      addStep(bot, el("li", { class: "thought" }, `💭 ${ev.text}`));
      bot.status("Plant den nächsten Schritt …");
      break;
    case "tool_start": {
      const badge = el("span", { class: "badge running" }, "läuft …");
      const res = el("span", { class: "res" });
      bot.lastTool = { badge, res };
      addStep(bot, el("li", { class: "tool" },
        "🔧 ", el("span", { class: "tname" }, label(ev.tool)), badge,
        el("div", { class: "args" }, short(ev.args, 200)), res));
      bot.status(`${label(ev.tool)} …`);
      break;
    }
    case "tool_result":
      if (bot.lastTool) {
        bot.lastTool.badge.className = `badge ${ev.status}`;
        bot.lastTool.badge.textContent = { ok: "ok", error: "Fehler", invalid: "ungültig" }[ev.status] || ev.status;
        bot.lastTool.res.textContent = ev.status === "ok" ? short(ev.result) : ev.error;
      }
      break;
    case "rejected":
      addStep(bot, el("li", { class: "tool" }, "⛔ ", el("span", { class: "tname" }, label(ev.tool)),
        el("span", { class: "badge abgelehnt" }, "abgelehnt, nicht ausgeführt")));
      break;
    case "think":
      bot.thinkBody.textContent += ev.text;
      break;
    case "answer":
      bot.appendAnswer(ev.text);
      break;
    case "error":
      throw new Error(ev.text);
  }
  scrollDown();
}

// Shows the approval card and resolves with true/false when a button is clicked.
function askApproval(bot, ap) {
  return new Promise((resolve) => {
    const rows = Object.entries(ap.args).map(([k, v]) =>
      el("tr", {}, el("td", {}, k.replace(/_/g, " ")), el("td", {}, typeof v === "object" ? JSON.stringify(v) : String(v))));
    const actions = el("div", { class: "ap-actions" });
    const card = el("div", { class: "approval" },
      el("div", { class: "ap-head" }, "⚠ Freigabe erforderlich"),
      el("div", { class: "ap-title" }, label(ap.tool), el("span", {}, ` · ${ap.product}`)),
      el("p", { class: "ap-desc" }, ap.description),
      rows.length ? el("table", {}, el("tbody", {}, rows)) : null,
      ap.gedanke ? el("p", { class: "ap-thought" }, `💭 ${ap.gedanke}`) : null,
      actions);
    const decide = (ok) => {
      card.classList.add(ok ? "yes" : "no");
      actions.replaceChildren(el("span", { class: "ap-decided" }, ok ? "✓ Freigegeben" : "✗ Abgelehnt"));
      resolve(ok);
    };
    actions.append(
      el("button", { class: "ap-yes", type: "button", onclick: () => decide(true) }, "Ausführen"),
      el("button", { class: "ap-no", type: "button", onclick: () => decide(false) }, "Ablehnen"));
    bot.approvals.append(card);
    scrollDown();
    actions.querySelector(".ap-yes").focus();
  });
}

async function runAgent(bot) {
  bot.status("Plant den ersten Schritt …");
  let res = await postJSON("api/agent/chat", { messages: history.slice(-10), session_id: sessionId });
  while (true) {
    let approval = null;
    let finished = false;
    await readStream(res, (ev) => {
      if (ev.type === "approval") approval = ev;
      else if (ev.type === "done") finished = true;
      else onAgentEvent(bot, ev);
    });
    if (!approval) return finished;
    bot.status("Wartet auf Ihre Freigabe", false);
    const ok = await askApproval(bot, approval);          // the graph waits on the server meanwhile
    bot.status(ok ? "Führt aus …" : "Fasst zusammen …");
    res = await postJSON("api/agent/resume", { thread_id: approval.thread_id, approved: ok });
  }
}

// ================= send =================
async function send(text) {
  if (busy || !text.trim()) return;
  document.body.classList.add("chatting");
  setBusy(true);
  history.push({ role: "user", content: text });
  addUser(text);
  const bot = addBot();
  scrollDown();

  try {
    const finished = mode === "agent" ? await runAgent(bot) : await runRag(bot);
    bot.finishThinking();
    if (finished && bot.answer.trim()) history.push({ role: "assistant", content: bot.answer });
    else history.pop();
    if (!bot.answer.trim() && finished) bot.notice("Keine Antwort erhalten.");
  } catch (err) {
    if (history.at(-1)?.role === "user") history.pop();
    bot.notice(`Fehler: ${err.message}`);
  } finally {
    setBusy(false);
    input.focus();
  }
}

// ---------- wiring ----------
form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value;
  input.value = "";
  input.style.height = "auto";
  send(text);
});

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
});

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = input.scrollHeight + "px";
});

document.querySelectorAll(".tile").forEach((t) => t.addEventListener("click", () => send(t.dataset.q)));

function setMode(m) {
  mode = m;
  document.querySelectorAll("#modes button").forEach((b) => b.classList.toggle("on", b.dataset.mode === m));
  document.getElementById("modeHint").textContent = MODE_HINT[m];
  document.body.classList.toggle("agent-mode", m === "agent");
  input.placeholder = m === "agent" ? "Was soll ich erledigen? z. B. „Trag für Anna nächste Woche Urlaub ein“" : "Ihre Frage an den Assistenten…";
  try { localStorage.setItem("ragbot-mode", m); } catch { /* storage blocked: fine */ }
}
document.querySelectorAll("#modes button").forEach((b) => b.addEventListener("click", () => !busy && setMode(b.dataset.mode)));
try { setMode(localStorage.getItem("ragbot-mode") === "agent" ? "agent" : "rag"); } catch { setMode("rag"); }

document.getElementById("reset").addEventListener("click", () => {
  if (busy) return;
  history = [];
  sessionId = newId();
  chat.querySelectorAll(".row, .msg.user").forEach((e) => e.remove());
  document.body.classList.remove("chatting");
  input.focus();
});
