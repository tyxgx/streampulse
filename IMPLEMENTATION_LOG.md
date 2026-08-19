# RAG Implementation Log — P0 Items 1-6

Scope: exactly the 6 pre-approved P0 items. Files touched: `apps/chatbot/rag.py`, `scripts/build_gold_chunks.py`, `schema.sql`, `scripts/rag_baseline_probe.py` (new), `baseline_before.json` (new), `baseline_after.json` (new), this log. No other file was modified — `apps/chatbot/services.py`, `apps/gold_data/*`, and Django settings were not touched. No gold table data was modified. The `country_performance` chunk re-embed touched exactly 672 rows (confirmed via row count before/after); `gold_chunks`' total row count is unchanged at 215,725.

A real regression was found mid-implementation (inside item 5's own scope) and is reported in full below, per the stop-and-report instruction, rather than glossed over.

---

## Item 1 — Confidence threshold

**File:line**: `apps/chatbot/rag.py:212-213` (`NO_MATCH_DISTANCE_THRESHOLD = 1.10`, `NO_DATA_REPLY`), gate logic at `apps/chatbot/rag.py:414-419` inside `get_rag_reply()`, distance computed by `_top1_distance()` at `apps/chatbot/rag.py:173-187`.

**What changed**: When `classify_query()` returns `None` (no exact country-name or keyword match) and the closest retrieved chunk's L2 distance exceeds 1.10, `get_rag_reply()` returns `{'reply': "I don't have data to answer that.", 'sources': []}` directly, without calling the LLM.

**Threshold sanity-check against baseline_before.json (as instructed)**: 1.10 was checked against all 15 baseline top-1 distances before implementing. Two findings changed the design from a naive global threshold to one scoped to `source_table is None` only:
- `out_of_scope` ("weather"): distance 1.157, `source_table=None` → correctly gated. This is the case the threshold targets.
- `lowercase_entity` ("how is india doing"): distance **1.1856** (routed, `source_table='country_performance'`) — this is a genuinely correct answer that exceeds 1.10. A global threshold would have suppressed a good answer here. Since this case is routed (not `None`), scoping the gate to `source_table is None` avoids the misclassification entirely — confirmed no regression in `baseline_after.json` (still correctly answered, see per-question table below).
- `ambiguous_entity` ("Georgia"): distance 1.0979, `source_table=None`, just under 1.10 — **not gated**, stays a known, documented limitation (see per-question table; this needs the P1 entity-existence layer, out of scope here).

**Before/after evidence**: `out_of_scope` — before: *"I don't have any information about the artist Better Weather's music or streaming data, but I can tell you that I'm a large language model..."* (a rambling non-refusal that still leaked an unrelated artist name) → after: *"I don't have data to answer that."* (clean refusal, `sources: []`, no LLM call made).

---

## Item 2 — Per-entity scoped retrieval for country comparisons

**File:line**: `detect_countries()` at `apps/chatbot/rag.py:82-91`; `retrieve_chunks_for_entity()` at `apps/chatbot/rag.py:150-168`; orchestration in `get_rag_reply()` at `apps/chatbot/rag.py:392-405`.

**What changed**: `get_rag_reply()` now calls `detect_countries(question)` (reusing `_get_country_names()`'s exact-match list, the same mechanism `classify_query()` already uses for single-country routing). If 2+ real country names are found, it runs one `retrieve_chunks_for_entity()` call per country (filtered via `source_key LIKE 'CountryName|%'`) and merges the results before building the prompt, instead of one shared `retrieve_chunks()` call that lets one country's chunks crowd out the other's.

**Scope limitation, as instructed**: this only covers countries. `comparison_2_artists` ("Compare Kendrick Lamar and Drake's streaming performance") is **not** detected by this mechanism — no artist-name list exists (`classify_query()` has none either), and building one is explicitly out of scope (P1). This is left as a documented, unfixed limitation, not a broader fix.

**Before/after evidence**:
- `comparison_2_countries` ("Compare India and Brazil's streaming performance."): before — sources were `['country_performance:Brazil|2025', 'country_performance:Brazil|2023', ...]`, all 5 slots Brazil, **zero India chunks**, despite `classify_query()` having routed to `country_performance` (this table-level routing doesn't prevent one country's chunks from dominating). After — sources are `['country_performance:Brazil|2025', 'country_performance:Brazil|2022', 'country_performance:Brazil|2018', 'country_performance:Brazil|2017', 'country_performance:Brazil|2022', 'country_performance:India|2022', 'country_performance:India|2026', 'country_performance:India|2019', 'country_performance:India|2020', 'country_performance:India|2023']` — **5 Brazil + 5 India**, both entities represented. The answer text after correctly cites both countries' real numbers.
- `comparison_2_artists` (Drake): **unchanged mechanism** (still 0/5 Drake chunks, still `source_table=None`) — but see item 6 for why the *answer text* nonetheless improved.

---

## Item 3 — SQL template router for MAX/COUNT/AVG/trend

**File:line**: keyword sets at `apps/chatbot/rag.py:243-259`; `detect_sql_intent()` at `apps/chatbot/rag.py:270-301`; `run_sql_intent()` at `apps/chatbot/rag.py:303-355` (parameterized throughout — table/column names come only from the fixed `_SQL_TABLE_KEYWORDS` dict at line 243, never from question text; only numeric/year values are passed as `%s` params); `build_sql_prompt()` at `apps/chatbot/rag.py:357-359`; orchestration at `apps/chatbot/rag.py:382-390`.

**What changed**: questions matching a table keyword (`artist`/`country`/`countries`/`market`/`nation`/`label`/`records`/`recordings`) plus a verb keyword are routed to a deterministic SQL query instead of vector retrieval:
- `how many` / `total number of` / `number of` → `SELECT COUNT(DISTINCT <key_col>) FROM <table>`, answer returned directly, **no LLM call**.
- `highest`/`strongest`/`most`/`least`/`lowest`/`top` → `SELECT <name_col>, SUM(total_streams) ... ORDER BY total {DESC/ASC} LIMIT 5`, formatted into context, LLM summarizes.
- `grew`/`growth`/`grow` + an explicit 4-digit year in the question → year-over-year delta query (`WITH yearly AS (...) SELECT ... ORDER BY growth {DESC/ASC} LIMIT 5`). If no year is stated, falls through to vector retrieval rather than guessing which year — this is a deliberate, narrower behavior than a fully general trend detector.

**Before/after evidence**:
- `superlative_country`: before — *"The country with the strongest streaming numbers cannot be directly determined based on the provided information..."* (an honest hedge, but the retrieved context was 5 arbitrary countries — Paraguay, Hungary, Uruguay, Kazakhstan, Bolivia — not the real top country) → after — *"The country with the strongest streaming numbers is Global, with approximately 1.02 trillion streams."*, `sources: ['sql:country_performance:SUM(total_streams) DESC']`, a deterministic, verifiably-correct-against-the-underlying-data answer.
- `superlative_artist_growth`: before — *"I don't have enough information to determine which artist grew the most in 2023. The data only shows a comparison between Michelangelo..."* → after — *"Peso Pluma."*, `sources: ['sql:artist_performance:growth(2022->2023) DESC']`.
- `count_labels`: before — a hedged non-answer → after — *"There are 27381 distinct labels in the data."* (no LLM call).
- `count_countries`: before — *"The data covers four countries: 1. Paraguay 2. Saudi Arabia 3. Bolivia 4. Ecuador"* (**wrong** — confidently stated as fact, actual count is 73) → after — *"There are 73 distinct countries in the data."* (correct, no LLM call).

**Data-quality finding surfaced, not fixed (correctly out of scope)**: `superlative_country`'s SQL answer, "Global," is a real distinct value in `country_performance` (`SELECT DISTINCT country_name ... WHERE country_name ILIKE '%global%'` returns exactly one row: `Global`) — almost certainly a worldwide-aggregate rollup row mixed in with individual countries, not a real country. The SQL router surfaces this transparently rather than silently filtering it, since deciding whether "Global" should be excluded is a data-modeling judgment call outside this session's scope (no instruction authorized filtering/cleaning gold data). Flagged here for the team's attention, not patched.

`count_labels`'s answer (27,381) is arithmetically correct against `standardized_label` as it exists today, but that column is known (from prior audits) to be severely fragmented — label normalization is explicitly P1/out of scope this session, so this number, while a correct *SQL* answer, is not necessarily what a person means by "how many labels." Noted, not addressed.

---

## Item 4 — Fix `country_chunk()` missing fields

**File:line**: `scripts/build_gold_chunks.py:98-110` (`country_chunk()` — added `active_songs` and `catalog_hit_rate` to the f-string, matching the pattern already correct in `artist_chunk()` and `label_chunk()`).

**Re-embed scope, confirmed**: ran a scoped re-embed touching only the `country_performance` partition. Before: 672 `country_performance` chunks, 215,725 total. After: `DELETE FROM gold_chunks WHERE source_table = 'country_performance'` removed exactly 672 rows; re-insert produced exactly 672 rows back; total row count confirmed unchanged at 215,725. `artist_performance` (151,264) and `label_performance` (63,789) chunks were never touched.

**Live confirmation** (`psql`, `source_key='Brazil|2024'`): chunk text now reads *"...avg 0.99% growth), with up to 407 active song(s) and an average catalog hit rate of 26.23%. Up to 412 active artists..."* — both previously-missing fields present.

**Before/after evidence** (`missing_field_probe`, "What is the active_songs count for Brazil?"): before — *"Unfortunately, I don't have enough context information to determine the 'active_songs' count for Brazil. The provided data only includes streams, total artists, labels, and market share/growth rates."* → after — *"According to the context provided, the highest active song(s) count recorded in Brazil on Spotify was up to 407 active song(s)."* — correctly answered from the now-present field.

---

## Item 5 — Build the vector index (includes a found-and-fixed regression)

**File:line**: `schema.sql:105-113` (uncommented and corrected the `ivfflat` index block).

**Correction made to the originally-commented-out line**: the original commented line specified `vector_cosine_ops`, but `apps/chatbot/rag.py`'s queries use the `<->` (L2 distance) operator throughout — an ivfflat index is only used by the query planner when its opclass matches the operator in the query, so a cosine-ops index would never actually have been used by this codebase's real queries. Built with `vector_l2_ops` instead. `lists = 216` follows pgvector's own `rows / 1000` guidance for the confirmed 215,725-row table.

**Index confirmed live** (`pg_indexes`): `idx_gold_chunks_embedding` exists, `USING ivfflat (embedding) WITH (lists='216')`.

### Regression found, diagnosed, and fixed (all within item 5's scope)

After building the index with pgvector's default `ivfflat.probes` (1), the first post-change `baseline_after.json` run showed `single_entity_lookup` and `multi_year_trend` — both previously-correct, `baseline_before.json`-passing Kendrick Lamar questions — returning wrong results: retrieved chunks became `label_performance:Lamar Entertainment` (an unrelated label whose name happens to contain "Lamar") instead of the correct `artist_performance:spotify:artist:2YZyLoL8N0Wb9xBt1NhZWg` (Kendrick Lamar) chunks.

**Root cause, confirmed via direct comparison**: `ivfflat` is an *approximate* nearest-neighbor index. With `probes=1` (pgvector's default), only 1 of the index's 216 lists is searched per query. Ran the exact same query embedding through an index-disabled exact brute-force search (`SET LOCAL enable_indexscan = off; SET LOCAL enable_bitmapscan = off;`) versus the approximate indexed search:
```
EXACT   top-1: spotify:artist:2YZyLoL8N0Wb9xBt1NhZWg|2025   distance 0.8959
APPROX  top-1: Lamar Entertainment|2022                      distance 0.9903
```
The true nearest neighbor (distance 0.896) was in a list the default `probes=1` search never visited.

**Fix**: swept `probes ∈ {1, 8, 15, 20}` against the same query and several others, comparing each against the exact brute-force result:
```
probes=1   correct_in_top5=0/5   (regression)
probes=8   correct_in_top5=5/5   time=0.031s
probes=15  correct_in_top5=5/5   time=0.049s
probes=20  correct_in_top5=5/5   time=0.147s
```
`probes=8` already matched the exact result set on every case tested (also independently verified exact-match on `country_specific` and `label_specific` queries). Added `IVFFLAT_PROBES = 8` and a `_set_probes(cur)` helper (`apps/chatbot/rag.py:108-115`), called at the start of every vector-search function (`retrieve_chunks()` line 121, `retrieve_chunks_for_entity()` line 153, `_top1_distance()` line 176) so every query in the codebase runs with the tuned probes value, not pgvector's under-tuned default.

**Re-ran `baseline_after.json` after the fix** — confirmed the regression is gone: `single_entity_lookup`'s sources are back to `artist_performance:spotify:artist:2YZyLoL8N0Wb9xBt1NhZWg|{2022,2023,2024,2025,2026}`, and every one of the 15 questions' `routed_table` matches `baseline_before.json` exactly (see final table below).

**Final latency numbers** (steady-state, 3 runs each, first cold-cache run excluded — noted honestly since it differs from the misleadingly-fast `probes=1` numbers first observed):

| Query shape | Before index (seq scan) | After index, `probes=8` (steady-state) |
|---|---|---|
| Unfiltered (all 215,725 rows) | ~374ms | ~34-72ms |
| Filtered, `artist_performance` (151,264 rows) | ~354ms | ~24-33ms |
| Filtered, `label_performance` (63,789 rows) | ~248ms | ~20-24ms |
| Filtered, `country_performance` (672 rows) | ~3.8ms (already used the existing btree, not ivfflat) | ~2.0-2.9ms steady-state (planner still prefers the existing `idx_gold_chunks_source` btree + sort for this small partition over the ANN index — confirmed via `EXPLAIN`; the very first post-rebuild run measured 10.9ms, a cold-cache artifact, not a repeatable cost) |

Net: a real, honest 85-93% latency reduction on the two large-partition query shapes, at full retrieval recall (not the ~94% reduction first measured at the broken default `probes=1` — that number was invalid because it also produced 0/5 correct results on a passing baseline case).

---

## Item 6 — Generation parameters + Sources label

**File:line**: `GENERATION_OPTIONS = {'temperature': 0, 'num_ctx': 8192}` at `apps/chatbot/rag.py:16-23` (named constant, not inline, per the instruction — so the eventual Groq request-parameter swap is a one-line change); used in `_call_llm()`'s request payload at `apps/chatbot/rag.py:362-376` (`'options': GENERATION_OPTIONS`).

**Before/after evidence of the temperature=0 change**: `comparison_2_artists` (Drake) — before: *"Unfortunately, the provided context does not contain information about Drake's streaming performance, so I cannot make a comparison between Kendrick Lamar and Drake. The data is only available for Kendrick Lamar from 2017 to 2023."* → after: *"I don't have the specific data on Drake's streaming performance, as it is not provided in the context. The context only provides information about Kendrick Lamar's streaming performance across different years. Therefore, I cannot make a comparison..."* Both refuse correctly and neither fabricates Drake's numbers in this particular run — this specific baseline case was already a clean refusal before this change (an improvement over an even earlier audit-pass run of the same question, not captured in `baseline_before.json`, that *had* fabricated a "33 billion streams / Billboard citation" for Drake). `temperature=0` did not regress this case and is a reasonable, low-risk hardening against that class of fabrication going forward, but retrieval never surfacing Drake's actual chunks (item 2's documented artist-name limitation) remains the deeper unfixed cause.

**Sources template relabel — could not be completed as specified, reported rather than invented**: searched every template and JS file involved in the chat UI (`templates/partials/chat_widget.html`, `apps/chatbot/templates/chatbot/chatbot.html`, `apps/chatbot/static/chatbot/js/chatbot.js`) and grepped the full repo for `Sources`/`sources` in template/static code. Finding: **no "Sources" label exists anywhere in the rendered UI.** `apps/chatbot/static/chatbot/js/chatbot.js:63` (`sendMessage()`) reads only `data.reply` from the API response and silently discards `data.sources` entirely — the backend has always computed and returned a `sources` list (`apps/chatbot/serializers.py:11`), but the frontend never displays it. Per the strict scope instructions ("do not expand scope... do not touch anything not explicitly listed"), building a new sources-rendering UI feature was not attempted, since the task specifically authorized a *relabel* of existing text, not a new display feature. No template file was modified for this half of item 6.

---

## Final per-question status (baseline_before.json → baseline_after.json)

| # | Question (id) | Before | After | Status |
|---|---|---|---|---|
| 1 | `single_entity_lookup` | Correct | Correct (unchanged, confirmed not regressed after the probes fix) | Unchanged — already passing |
| 2 | `multi_year_trend` | Honest partial answer (data starts 2017, not 2020) | Same, honest partial answer (unchanged, confirmed not regressed) | Unchanged — already passing |
| 3 | `comparison_2_countries` | **Wrong** — 0/5 India chunks, Brazil-only | **Fixed** — 5 Brazil + 5 India chunks, both entities answered | **Flipped fail → pass** (item 2) |
| 4 | `comparison_2_artists` | Honest refusal (Drake absent from context) | Honest refusal (Drake still absent — item 2 doesn't cover artists, documented limitation) | Unchanged — known, documented limitation, not in scope |
| 5 | `superlative_country` | **Wrong** — hedged/incomplete, based on 5 arbitrary countries | **Fixed** — deterministic SQL answer | **Flipped fail → pass** (item 3) |
| 6 | `superlative_artist_growth` | **Wrong** — refused, based on an arbitrary retrieved artist | **Fixed** — deterministic SQL answer ("Peso Pluma") | **Flipped fail → pass** (item 3) |
| 7 | `count_labels` | Refused (no path to a real count) | **Fixed** — deterministic SQL answer (27,381; data-quality caveat noted above) | **Flipped fail → pass** (item 3) |
| 8 | `count_countries` | **Wrong** — confidently stated "four countries," actual is 73 | **Fixed** — deterministic SQL answer (73) | **Flipped fail → pass** (item 3) |
| 9 | `country_specific` | Correct | Correct (unchanged) | Unchanged — already passing |
| 10 | `label_specific` | Honest hedge (label fragmentation, P1/out of scope) | Similar hedge/generalized answer (fragmentation unfixed, as scoped) | Unchanged — known limitation, correctly out of scope |
| 11 | `missing_field_probe` | Refused — field absent from chunk text | **Fixed** — field now present, correctly answered (407 active songs) | **Flipped fail → pass** (item 4) |
| 12 | `out_of_scope` | Non-refusal, leaked an unrelated artist name | **Fixed** — clean refusal, no LLM call | **Flipped fail → pass** (item 1) |
| 13 | `nonexistent_entity` | Wrong — answered as if "Jordan" the label exists | Still wrong — different wrong label surfaced (Pauly Jr. Pictures etc., due to the probes-tuned ANN index returning a different approximate top-5 than before) | Unchanged in kind — known limitation (P1 entity-existence layer, out of scope); underlying chunks returned shifted because retrieval is now indexed/approximate rather than exact, but the *failure mode* (confidently answering for a nonexistent entity) is identical before and after |
| 14 | `ambiguous_entity` | Wrong — mixed artist/label answer for "Georgia" | Same kind of answer, still not gated (distance 1.098, just under the 1.10 threshold) | Unchanged — known, documented borderline case (item 1's design note) |
| 15 | `lowercase_entity` | Correct | Correct (unchanged, confirmed not regressed by the confidence gate) | Unchanged — already passing, **and confirmed not regressed** by the gate-scoping decision in item 1 |

---

# Markdown UI Improvement — Items 1-5 (separate, later task)

Scoped implementation of 5 UI/formatting items, validated beforehand in
`MARKDOWN_UI_PLAN_VALIDATION.md`. Files touched: `apps/chatbot/rag.py`,
`apps/chatbot/templates/chatbot/chatbot.html`, `apps/chatbot/static/chatbot/js/chatbot.js`,
`apps/chatbot/static/chatbot/css/chatbot.css`. No other files modified. SQL router query logic,
confidence-gate threshold, and retrieval logic (all P0 work above) were not touched.

## Item 1 — SYSTEM_PROMPT formatting instruction

**File**: `apps/chatbot/rag.py:232-241`

Before:
```python
SYSTEM_PROMPT = (
    "You are a data analyst assistant for a Spotify streaming analytics "
    "platform. Answer the user's question using ONLY the context provided "
    "below. If the context doesn't contain the answer, say so — do not "
    "make up numbers. When you cite a fact, name the artist/country/label "
    "and time period it came from."
)
```

After — appended one sentence, existing instruction unchanged:
```python
SYSTEM_PROMPT = (
    "You are a data analyst assistant for a Spotify streaming analytics "
    "platform. Answer the user's question using ONLY the context provided "
    "below. If the context doesn't contain the answer, say so — do not "
    "make up numbers. When you cite a fact, name the artist/country/label "
    "and time period it came from. "
    "Format your response in markdown: bold key numbers and entity names "
    "with **asterisks**, use a bullet list when presenting 2 or more facts "
    "or a comparison, keep paragraphs to 2-3 lines, and use a markdown "
    "table when comparing multiple entities across the same metrics."
)
```

**Evidence** (`ui_baseline_after.json`, comparison question): the LLM went beyond bullets and
produced a full markdown table on its own for the India/Brazil comparison — headers
`Country | Year | Total Streams | Average Market Share`, rendered correctly in the browser (see
Item 5 evidence below).

## Item 2 — SQL-router reply formatting

**File**: `apps/chatbot/rag.py:438`

Before:
```python
return {'reply': f"There are {count} distinct {label} in the data.", 'sources': [description]}
```
After:
```python
return {'reply': f"There are **{count}** distinct {label} in the data.", 'sources': [description]}
```

This was the only hardcoded, LLM-bypassing reply string in the SQL router path (`rag.py:434-441`).
The other two SQL-router kinds (`superlative`, `trend`) already route through
`build_sql_prompt()` + `_call_llm()`, so Item 1's prompt change reaches them automatically — no
separate f-string edit needed for those.

**Evidence** — before: `"There are 73 distinct countries in the data."` / after:
`"There are **73** distinct countries in the data."`, confirmed rendering as bold **73** in the
browser.

## Item 3 — Markdown rendering + sanitization

**Files**: `chatbot.html` (new CDN scripts), `chatbot.js` (`appendMessage()` branch)

`chatbot.html` — added two CDN `<script>` tags, scoped to this page only (not `base.html`):
```html
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3.1.5/dist/purify.min.js"></script>
<script src="{% static 'chatbot/js/chatbot.js' %}"></script>
```

`chatbot.js` — `appendMessage()` now branches by sender:
```js
if (sender === 'bot') {
  bubble.innerHTML = DOMPurify.sanitize(marked.parse(text));
} else {
  bubble.textContent = text;
}
```
DOMPurify's default safelist is used, no config overrides. User-sender path is byte-for-byte
unchanged from the original (`textContent`).

**Verified live in browser** (Chrome extension, `http://127.0.0.1:8000/chatbot/`):
- "How many countries are covered in the data?" → rendered as bold **73**, no raw asterisks visible.
- "Compare India and Brazil's streaming performance." → rendered with an `<h3>` heading, a
  bulleted list, bold country names/numbers, and a real `<table>` (border, header row).
- "How did Kendrick Lamar perform in 2024?" → rendered with bold key numbers and a bulleted list
  of metrics.
- **DOM structure check** (`read_page` accessibility tree): user messages appear as flat
  single-text nodes with no children; the bot reply for the comparison question shows real nested
  elements (`strong`, `list`/`listitem`, `table` with cells) — structurally confirms bot output is
  parsed as markdown/HTML while user input stays plain text.
- No console errors during any of the three sends (`read_console_messages`).

## Item 4 — Sources display

**Files**: `chatbot.js` (new `formatSource()`/`appendSources()`, fetch handler, submit handler)

Before, `sendMessage()` returned only `data.reply` — `data.sources` was read from the API response
and silently discarded. Now:
```js
const data = await response.json();
return { reply: data.reply, sources: data.sources };
```
and the submit handler passes `sources` through to `appendMessage()`, which renders one chip per
source string via a new `appendSources()` helper. Source strings (e.g.
`"country_performance:India|2023"`, `"sql:country_performance:COUNT(DISTINCT country_name)"`) are
plain strings, not structured objects — `formatSource()` trims the table-name prefix and, if the
key contains `|year`, formats it as `"key (year)"`.

**Bug found and fixed during live testing** (required for Item 4 to actually work as intended, not
in the original plan text): `.chat-message` is `display: flex` (row) for left/right message
alignment. The first implementation appended the bubble and the sources row as direct siblings of
that flex row, so chips rendered *beside* the bubble instead of *below* it. Fixed by introducing a
`.chat-content` column wrapper (`chatbot.js`) holding the bubble + sources together, and adjusting
`max-width` on `.chat-bubble` (100%, was 75%) and `.chat-sources` (removed its own 75%) since the
75% constraint now lives on the new wrapper — two nested 75%'s would have made bubbles too narrow.
Verified visually after the fix: chips sit in a wrapped row directly under the bubble.

**Evidence**: count question shows one chip, `COUNT(DISTINCT country_name)`, under the reply.
Comparison question shows 10 chips (`Brazil (2025)`, `India (2022)`, etc.) wrapping onto multiple
rows under the reply, styled with `--chip-bg`/`--color-border`/`--color-muted` — no new hardcoded
colors.

## Item 5 — Markdown CSS

**File**: `chatbot.css` — added rules for `p`, `ul`/`ol`/`li`, `strong`, `h1`-`h3`, `table`/`th`/`td`
inside `.chat-bubble`, plus `.chat-content` and `.chat-sources`/`.chat-source-chip`. All values
reuse existing tokens from `main.css` (`--color-ink`, `--color-body`, `--color-muted`,
`--color-border`, `--chip-bg`, `--radius-md`) — no new hardcoded colors introduced. No light-theme
variant added, matching the rest of the project (dark-only).

**Evidence**: table borders, bold headings, bullet indentation, and chip styling all render
consistent with the rest of the dark-theme chat window (card background, border color, muted text
for secondary info) — see Item 3/4 evidence above.

---

### Markdown-rendering oddities observed

- **Artist source chips show a raw Spotify URI, not an artist name** — e.g.
  `2YZyLoL8N0Wb9xBt1NhZWg (2024)`. `source_key` for `artist_performance` chunks is the
  `artist_uri`, not `artist_name` (existing chunk-key design from `build_gold_chunks.py`, not
  touched here). Country/SQL sources display cleanly (`India (2022)`,
  `COUNT(DISTINCT country_name)`) since those keys are already human-readable. Not fixed — would
  require changing `source_key` format in retrieval/chunk-building code, out of scope.
- No broken/unclosed markdown (no stray asterisks, no malformed nested lists) observed in any of
  the 3 test replies — Groq's `llama-3.3-70b-versatile` output was well-formed markdown in every
  case, including the unprompted table it generated for the comparison question.
- SQL-router `count` replies and the confidence-gate `NO_DATA_REPLY` string are plain sentences
  with no markdown syntax beyond the one bolded number added in Item 2 — pass through
  `marked.parse()` harmlessly.

### Confirmed: user input never passed through marked.parse()

- Code: `appendMessage()`'s `else` branch (any non-`'bot'` sender) is untouched from the original —
  still `bubble.textContent = text`.
- Live DOM check: user message nodes in the accessibility tree are flat text with no child
  elements, unlike bot replies which show real `<strong>`/`<ul>`/`<table>` structure.

### Baseline files

- `ui_baseline_before.json` — 3 questions (normal factual, comparison, count/SQL-router), raw API
  JSON captured before any change.
- `ui_baseline_after.json` — same 3 questions, captured after all 5 items implemented.
- Reply text content is materially unchanged in meaning between before/after (same facts, same
  numbers) — the only difference is markdown syntax now present in `reply` where before it was
  plain prose. `sources` arrays are identical between before/after for all 3 questions (retrieval
  logic untouched, as required).

**Plain-English verdict**: yes, the rendered output looks better — bold numbers and entity names
stand out, multi-fact answers are scannable as bullets instead of one dense paragraph, the
comparison question renders as an actual table, and sources (previously silently dropped by the
frontend) are now visible as chips under each bot reply. No raw `**`/`-`/`#` markdown characters
are visible in the rendered UI.

**Summary**: **7 of 15** tested questions flipped from fail to pass — `comparison_2_countries` (item 2), `superlative_country`, `superlative_artist_growth`, `count_labels`, `count_countries` (item 3), `missing_field_probe` (item 4), `out_of_scope` (item 1). **5 stayed correctly unchanged** as already-passing (`single_entity_lookup`, `multi_year_trend`, `country_specific`, `lowercase_entity` — the last explicitly confirmed *not* regressed by the item-1 gate-scoping decision — plus `label_specific`, an unchanged, correctly-out-of-scope hedge). **3 remain known, explicitly out-of-scope limitations** (`comparison_2_artists`, `nonexistent_entity`, `ambiguous_entity`), each requiring P1 work (an artist-name index, an entity-existence layer) that was correctly not attempted per the scope constraints. **Zero net regressions** in the final state — one was found mid-implementation (item 5, pgvector's default `ivfflat.probes=1`), diagnosed with a direct exact-vs-approximate comparison, and fixed within item 5's own scope (tuning `probes`, not touching anything outside the approved file list) before finalizing `baseline_after.json`.
