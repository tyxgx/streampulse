# Markdown UI Plan — Validation Report (read-only)

## Part 1 — SYSTEM_PROMPT formatting instruction

**Verdict: Plan is accurate.**

Current `SYSTEM_PROMPT` (`apps/chatbot/rag.py:232-238`):
```
"You are a data analyst assistant for a Spotify streaming analytics "
"platform. Answer the user's question using ONLY the context provided "
"below. If the context doesn't contain the answer, say so — do not "
"make up numbers. When you cite a fact, name the artist/country/label "
"and time period it came from."
```
`build_prompt()` (`rag.py:241-243`) just concatenates `Context:\n<chunks>\n\nQuestion: <q>` — no formatting instruction anywhere. `build_sql_prompt()` (`rag.py:373-375`) is the same, no formatting guidance. Confirmed: zero markdown/formatting instructions exist today. A one-sentence addition to `SYSTEM_PROMPT` is a trivial, low-risk change — both `_call_groq` and `_call_ollama` share this one constant, so the change applies to both providers automatically.

## Part 2 — Markdown rendering in the frontend

**Verdict: Plan needs adjustment (bigger than "replace one line").**

File: `apps/chatbot/static/chatbot/js/chatbot.js`. Bot replies are injected via **`bubble.textContent = text`** (`chatbot.js:27`), inside `appendMessage()`. This is plain-text assignment — safe today (browser auto-escapes), but means:
- Swapping to `marked.parse()` + `bubble.innerHTML = DOMPurify.sanitize(...)` is a real code change to `appendMessage()`, not a drop-in replacement — the function is shared by **both** user and bot messages (`sender` param), so it needs a branch (render markdown only for `sender === 'bot'`; keep `textContent` for user input, since user input must never be parsed as HTML/markdown).
- `chatbot.html` has no `<div id="chatMessages">` sub-structure beyond one bot greeting bubble — that greeting is currently static HTML in the template (`chatbot.html:29-34`), not JS-rendered, so it's unaffected either way.

## Part 3 — Markdown/sanitization libraries already present

**Verdict: Plan is accurate — nothing is currently loaded, both must be added new.**

Searched all templates and static JS for `marked`, `DOMPurify`, `markdown-it`, `showdown`, and any CDN `<script>` tags: only `templates/base.html:18-19,33` load **Bootstrap 5.3.3 CSS/JS + Bootstrap Icons**, all from `cdn.jsdelivr.net`. No markdown or sanitization library anywhere in the repo. Both `marked.js` and `DOMPurify` will need to be newly added as CDN `<script>` tags (in `chatbot.html`, scoped to that page — not `base.html`, to avoid loading them site-wide for pages that don't need them).

## Part 4 — CSS design tokens for dark theme

**Verdict: Plan is accurate — reusable tokens exist and cover most needs.**

Defined in `static/css/main.css:13-46` (`:root`):
- Colors: `--color-ink`, `--color-body`, `--color-muted`, `--color-border`, `--color-primary`, `--color-card`, `--color-surface`, `--color-surface-alt`, `--chip-bg` (already exists, semantically fits a sources-chip restyle — see Part 5)
- Radius: `--radius-md`, `--radius-lg`
- Shadow: `--shadow-sm`, `--shadow-md`, `--shadow-glow`
- Font: `--font-sans`
- Spacing: `--space-xs` through `--space-2xl`

`chatbot.css` already consumes these consistently (`var(--color-border)`, `var(--color-card)`, etc. throughout). New markdown styling (`.chat-bubble ul/ol/li/strong/h3/p`) should reuse these directly — no new hardcoded colors/spacing needed. One gap: no existing token for a monospace font (relevant if markdown answers ever include `code` spans) — not currently used anywhere in the codebase, would need a plain fallback (`monospace`) if code-formatting ever appears in LLM output.

## Part 5 — "Sources" list rendering

**This is the most significant correction to the plan's assumptions.**

The "sources" list **is not rendered in the frontend at all today.** `services.get_bot_reply()` and `rag.get_rag_reply()` both return `{'reply': ..., 'sources': [...]}`, and `ChatMessageResponseSerializer` (`apps/chatbot/serializers.py`) exposes `sources` in the API response — but `chatbot.js:62-63` only reads `data.reply` and discards `data.sources` entirely:
```js
const data = await response.json();
return data.reply;   // sources is silently dropped
```
So "restyle the sources list as chips" is not a restyle — it's **new frontend work**: adding a sources array to the fetch handling, a new DOM element per message for the chip row, and new CSS. This is independent of Part 2's markdown work (different code path), so it can be scoped/estimated separately, but the plan's framing of it as an "optional adjustment" undersells it — there is no existing sources UI to adjust.

## Part 6 — Streaming vs. complete response

**Verdict: No streaming exists — plan's assumption is correct, flagging as confirmed-safe.**

`ChatMessageView.post()` (`apps/chatbot/api.py:21-28`) calls `services.get_bot_reply()` synchronously and returns one JSON `Response` — no `StreamingHttpResponse`, no SSE, no chunked transfer. Frontend (`chatbot.js:51-64`) does one `await fetch(...)` → `await response.json()`, no `ReadableStream` reading. The full reply text arrives in one piece, so `marked.parse(fullText)` on receipt (not incrementally) is the correct and only-needed approach — no partial-markdown-parsing edge cases to handle.

## Part 7 — Risk: switching to innerHTML

- **No event listeners** are attached to bot-bubble children anywhere in `chatbot.js` — the file only ever appends full bubbles, never queries into `.chat-bubble`'s children after creation. Low risk there.
- **User messages must stay on `textContent`** (confirmed above) — the plan doesn't explicitly say this, but it's implied by "sanitize before injecting"; worth being explicit that user input should never be run through `marked.parse()` at all (not even sanitized-and-parsed), since there's no product reason to support user-authored markdown, and it needlessly widens the XSS surface.
- **Typing indicator** (`showTypingIndicator`, `chatbot.js:33-40`) already uses `innerHTML` today (static, hardcoded three-span markup) — no conflict, but confirms `innerHTML` usage isn't new to this file, just currently only used for trusted static markup, not LLM output.
- **No existing character-escaping logic** to preserve/replace — `textContent` did that implicitly; once removed for bot messages, DOMPurify is the only thing standing between LLM output and the DOM. Since Groq/Ollama output is not attacker-controlled in the traditional sense but is also not fully trustworthy (prompt injection via retrieved chunk text is theoretically possible, though the chunks are our own generated data, not user-submitted), DOMPurify is a correctly-scoped mitigation — this should default to a safelist (no `<script>`, no `on*` attributes, no `<iframe>`), which is DOMPurify's default behavior.

## Effort estimate (per plan part)

| Part | Plan's implied effort | Actual effort | Reasoning |
|---|---|---|---|
| 1. SYSTEM_PROMPT wording | Low | **Low** | One string edit, shared by both LLM providers, no other code touches it |
| 2. Markdown rendering + sanitization in JS | Low/Medium | **Medium** | `appendMessage()` must branch by sender; two new CDN deps; must verify DOMPurify config; must confirm Groq output doesn't already contain literal markdown that renders oddly (untested) |
| 3. CSS for rendered markdown | Low | **Low** | Tokens already exist and are used consistently; mostly `.chat-bubble :is(ul,ol,li,strong,p,h1,h2,h3){...}` mapped onto existing vars |
| 4. Sources chip layout | Low ("optional adjustment") | **Medium** | No existing sources UI at all — this is new JS (read `data.sources`, build DOM) + new CSS, not a restyle |

## Files that will need to change (implementation step, not done here)

- `apps/chatbot/rag.py` — `SYSTEM_PROMPT` constant (Part 1)
- `apps/chatbot/templates/chatbot/chatbot.html` — add `marked.js` + `DOMPurify` CDN `<script>` tags (Part 2/3)
- `apps/chatbot/static/chatbot/js/chatbot.js` — `appendMessage()` branch logic for markdown rendering + sanitization; new logic to read and render `data.sources` (Parts 2, 5)
- `apps/chatbot/static/chatbot/css/chatbot.css` — new rules for rendered markdown elements inside `.chat-bubble`, new `.chat-sources`/chip styles (Parts 3, 4)

## Edge cases not covered by the plan

1. **`bubble.textContent = text` currently also displays the canned fallback replies** (`services.py:13-17`, plain sentences with no markdown) — these will pass through `marked.parse()` harmlessly, but worth testing since they're a real code path (RAG/Ollama-down fallback), not just a hypothetical.
2. **SQL-router replies** (`rag.py:434`, e.g. `"There are {count} distinct {label} in the data."`) are plain f-strings with no markdown — fine to render through `marked.parse()`, but confirms the prompt-level formatting instruction (Part 1) won't reach this code path at all, since it's a hardcoded string, not an LLM call. If the team wants this reply markdown-formatted too, it needs a separate change to that f-string, not just the `SYSTEM_PROMPT`.
3. **No dark/light theme split exists** in `main.css`'s `:root` (single dark theme only, per the tokens found) — so no need to design markdown styles twice for two themes, simplifying Part 3 further.
