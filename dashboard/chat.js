/* "Ask StreamPulse" chat drawer. Hidden unless window.SP_API is set (see config.js). */
(function () {
  "use strict";
  const API = window.SP_API;
  if (!API) return;

  const CHIPS = ["Top 5 tracks in Brazil this month", "How is Japan trending?", "Who is the biggest artist right now?", "Compare the UK and Germany", "What happened to India's data?"];
  const history = [];
  let busy = false;

  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

  const fab = el("button", "chat-fab");
  fab.setAttribute("aria-label", "Open StreamPulse assistant");
  fab.innerHTML = '<i class="ph-light ph-chat-circle-text"></i><span>Ask StreamPulse</span>';
  const panel = el("section", "chat-panel");
  panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "StreamPulse assistant"); panel.hidden = true;
  const head = el("header", "chat-head");
  head.append(el("b", null, "Ask StreamPulse"));
  const close = el("button", "chat-x"); close.setAttribute("aria-label", "Close"); close.innerHTML = '<i class="ph-light ph-x"></i>';
  head.append(close);
  const log = el("div", "chat-log"); log.setAttribute("aria-live", "polite");
  const chips = el("div", "chat-chips");
  const form = el("form", "chat-form");
  const input = el("input"); input.type = "text"; input.maxLength = 500; input.placeholder = "Ask about countries, artists, tracks"; input.setAttribute("aria-label", "Your question");
  const send = el("button", "chat-send"); send.type = "submit"; send.setAttribute("aria-label", "Send"); send.innerHTML = '<i class="ph-light ph-arrow-up"></i>';
  form.append(input, send);
  const foot = el("div", "chat-foot", "Answers come from the chart data only. Streams = charted streams.");
  panel.append(head, log, chips, form, foot);
  document.body.append(fab, panel);

  // safe text -> DOM (bold + line breaks, no innerHTML from model output)
  function render(into, text) {
    text.split("\n").forEach((line, i) => {
      if (i) into.append(document.createElement("br"));
      line.split(/(\*\*[^*]+\*\*)/).forEach((part) => {
        if (/^\*\*[^*]+\*\*$/.test(part)) into.append(el("strong", null, part.slice(2, -2)));
        else if (part) into.append(document.createTextNode(part));
      });
    });
  }

  function add(role, text, sources) {
    const m = el("div", "msg " + role);
    const b = el("div", "bubble"); render(b, text); m.append(b);
    if (sources && sources.length) {
      const s = el("div", "srcs");
      sources.forEach((x) => { if (typeof x.href === "string" && x.href.startsWith("#/")) { const a = el("a", null, x.label); a.href = x.href; a.addEventListener("click", () => { if (matchMedia("(max-width:700px)").matches) toggle(false); }); s.append(a); } });
      if (s.childNodes.length) m.append(s);
    }
    log.append(m); log.scrollTop = log.scrollHeight;
    return m;
  }

  async function ask(q) {
    q = q.trim();
    if (!q || busy) return;
    busy = true; send.disabled = true; chips.hidden = true;
    add("user", q); input.value = "";
    const wait = add("bot", "Looking at the data");
    wait.classList.add("wait");
    try {
      const r = await fetch(API.replace(/\/$/, "") + "/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q, history: history.slice(-6) }) });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const d = await r.json();
      wait.remove();
      add("bot", d.answer || "No answer.", d.sources);
      history.push({ role: "user", content: q }, { role: "assistant", content: d.answer || "" });
    } catch (e) {
      wait.remove();
      add("bot error", "I could not reach the assistant. Please try again in a moment.");
    } finally { busy = false; send.disabled = false; input.focus(); }
  }

  CHIPS.forEach((c) => { const b = el("button", "chip", c); b.type = "button"; b.addEventListener("click", () => ask(c)); chips.append(b); });
  form.addEventListener("submit", (e) => { e.preventDefault(); ask(input.value); });
  function toggle(open) { panel.hidden = !open; fab.hidden = open; document.body.classList.toggle("chat-open", open); if (open) { if (!log.childNodes.length) add("bot", "Hi, I answer questions about the Spotify chart data on this site. Try one of the questions below."); setTimeout(() => input.focus(), 50); } else fab.focus(); }
  fab.addEventListener("click", () => toggle(true));
  close.addEventListener("click", () => toggle(false));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden) toggle(false); });
})();
