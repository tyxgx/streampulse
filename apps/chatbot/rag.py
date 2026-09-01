"""
RAG pipeline: embed the question, retrieve nearest gold_chunks via pgvector,
assemble a grounded prompt, generate an answer via an LLM. Provider
priority in _call_llm(): Gemini (GEMINI_API_KEY) > Groq (GROQ_API_KEY) >
local Ollama. Gemini was added after Groq's free-tier 100K-tokens/day cap
was repeatedly exhausted during testing/normal use — see get_rag_reply()
for the overall pipeline.
"""
import decimal
import os
import random
import re

import requests
from django.db import connections
from rapidfuzz import fuzz, process

from . import cache

OLLAMA_URL = os.environ.get('OLLAMA_URL', 'http://localhost:11434')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'llama3.2:3b')

# If GROQ_API_KEY is set, _call_llm() uses Groq's OpenAI-compatible chat
# endpoint instead of the local Ollama server — no other code path changes.
# Unset/empty GROQ_API_KEY (the default) keeps using local Ollama, so this
# is purely additive for local dev.
GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
GROQ_MODEL = os.environ.get('GROQ_MODEL', 'llama-3.3-70b-versatile')
GROQ_URL = 'https://api.groq.com/openai/v1/chat/completions'

# Gemini via Google's OpenAI-compatible endpoint — same messages format as
# Groq/OpenAI, so _call_gemini() is nearly identical to _call_groq(). Takes
# priority over Groq in _call_llm() when set, since it was added
# specifically to route around Groq's daily quota, not as a fallback for
# when Groq is down.
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
# gemini-2.0-flash returned 429 with "limit: 0" for this project's free
# tier -- not a transient rate limit, that model's quota was zero.
# gemini-flash-latest works, so it's the default rather than 2.0-flash.
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-flash-latest')
GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'

# Named constant, not inline, so the request target swaps without touching
# call sites — deterministic output (temperature=0) is used throughout
# since every answer here is meant to summarize retrieved facts/SQL
# results, not generate creative text. num_ctx is Ollama-specific (Groq
# manages its own context window) and is only applied in the Ollama path.
GENERATION_OPTIONS = {'temperature': 0, 'num_ctx': 8192}

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer('all-MiniLM-L6-v2')
    return _model


def embed_query(text):
    return _get_model().encode([text])[0].tolist()


# Order matters: a question can contain more than one table's keywords at
# once (e.g. "which artist has the most tracks" has both "artist" and
# "tracks"), and the first dict entry whose keyword appears wins. kpi_song
# ("song"/"track") is checked LAST because those are the most generic,
# weakest signal here — "artist"/"country"/"label" are more specific and
# should win when a question mentions both.
_TABLE_KEYWORDS = {
    'country_performance': ['country', 'countries', 'market', 'nation'],
    'artist_performance': ['artist', 'artists', 'singer', 'singers', 'musician', 'musicians'],
    'label_performance_enhanced': ['label', 'records', 'recordings'],
    'kpi_song': ['song', 'track', 'songs', 'tracks'],
}

_country_names = None

# Common abbreviations/aliases for country names that appear verbatim in
# country_performance — a stakeholder asking "How is the US doing?" is a
# far more natural phrasing than the full country name, and without this
# the question falls through to unfiltered vector search entirely (see
# README's "known rough edges"). Matched via regex word boundaries, not
# plain substring, since short tokens like "US"/"UK" would otherwise match
# inside unrelated words (e.g. "US" inside "bonus").
_COUNTRY_ALIASES = {
    'us': 'United States',
    'usa': 'United States',
    'u.s.': 'United States',
    'u.s.a.': 'United States',
    'uk': 'United Kingdom',
    'u.k.': 'United Kingdom',
    'uae': 'United Arab Emirates',
}


def _get_country_names():
    """Cached list of real country names from country_performance, longest
    first so a multi-word name (e.g. "United States") matches before a
    shorter unrelated substring would."""
    global _country_names
    if _country_names is None:
        with connections['gold'].cursor() as cur:
            cur.execute("SELECT DISTINCT country_name FROM country_performance")
            names = [r[0] for r in cur.fetchall()]
        _country_names = sorted(names, key=len, reverse=True)
    return _country_names


def _name_in_question(name, lowered_question):
    """Word-boundary match of `name` inside lowered_question — same
    approach _alias_countries() already used for aliases, now applied to
    the main exact-name loops too. Plain `in` substring matching let a
    short entity name embedded inside an unrelated word falsely match
    (e.g. an artist literally named "Tream" matching inside "streams", or
    an artist named "Swift" matching inside a typo'd "Taylr Swift" before
    the fuzzy fallback for "Taylor Swift" ever got a chance to run) —
    discovered via live regression testing. \\b works correctly for
    multi-word names too (e.g. "Taylor Swift") since a space is a
    non-word character."""
    return re.search(r'\b' + re.escape(name.lower()) + r'\b', lowered_question) is not None


def _alias_countries(lowered_question):
    """Real country names implied by an abbreviation/alias in the question
    (e.g. "US" -> "United States"), matched on word boundaries so short
    tokens don't match inside unrelated words. Returns real country_name
    values, not the aliases themselves, so callers never need to know
    aliases exist."""
    found = []
    for alias, real_name in _COUNTRY_ALIASES.items():
        if re.search(r'\b' + re.escape(alias) + r'\b', lowered_question):
            found.append(real_name)
    return found


_artist_catalog = None


def _get_artist_catalog():
    """Cached (artist_name, artist_uri) pairs from artist_performance,
    longest name first. gold_chunks' source_key for artist_performance is
    keyed on artist_uri (not name — see build_gold_chunks.py), so
    name-based routing needs the uri to actually filter chunks by; names
    aren't unique (e.g. 8 different artist_uris are all named "Kali" in
    this data) so each name resolves to whichever uri has the most total
    streams — the most likely real match for a bare-name question.

    Names under 5 characters are excluded entirely — 5,782 artist names in
    this data are <=4 chars (many are bare single letters, e.g. "E", "F",
    "T"), which would match as a substring inside huge numbers of unrelated
    questions. This trades a small amount of recall (short-named artists
    never get exact-match routing, only generic keyword/vector fallback)
    for not hijacking routing on ordinary text."""
    global _artist_catalog
    if _artist_catalog is None:
        with connections['gold'].cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (artist_name) artist_name, artist_uri
                FROM (
                    SELECT artist_name, artist_uri, SUM(total_streams) AS total
                    FROM artist_performance
                    WHERE artist_name IS NOT NULL AND length(artist_name) >= 5
                    GROUP BY artist_name, artist_uri
                ) ranked
                ORDER BY artist_name, total DESC
                """
            )
            pairs = cur.fetchall()
        _artist_catalog = sorted(pairs, key=lambda p: len(p[0]), reverse=True)
    return _artist_catalog


# Fuzzy matching is a FALLBACK ONLY, tried after exact substring matching
# finds nothing — preserves today's fast, precise exact-match behavior for
# the common case, and only spends the extra rapidfuzz pass on questions
# that would otherwise get zero entity signal at all. score_cutoff=90 is
# deliberately conservative: this path has no keyword/exact evidence behind
# it, so a wrong fuzzy match (routing to the wrong entity) is worse than
# missing one and falling through to generic vector search — today's
# behavior, so a miss here is never a regression.
_FUZZY_SCORE_CUTOFF = 90


_WORD_RE = re.compile(r"[a-z0-9']+")


def _question_ngrams(lowered_question, max_words=3):
    """Word-level 1..max_words n-grams of the question, so fuzzy matching
    compares whole tokens/phrases against candidate names instead of
    scanning raw substrings. This exists because the first version of this
    function used partial_ratio directly against the whole question text,
    which matched "Tream" (a real, unrelated artist name) inside "streams"
    at 100% — the exact same word-boundary problem _name_in_question()
    fixes for exact matches, just re-appearing in the fuzzy fallback since
    rapidfuzz's partial_ratio has no concept of word boundaries. Comparing
    only whole n-grams closes that hole."""
    words = _WORD_RE.findall(lowered_question)
    grams = set()
    for n in range(1, max_words + 1):
        for i in range(len(words) - n + 1):
            grams.add(' '.join(words[i:i + n]))
    return grams


def _fuzzy_match_one(lowered_question, candidate_names):
    """Best fuzzy match of any candidate name against a whole-word/phrase
    n-gram of the question (see _question_ngrams()) — never a raw
    substring scan. fuzz.ratio (whole-string similarity) is used per
    n-gram rather than partial_ratio, since n-grams are already the right
    granularity (complete words/phrases), not arbitrary substrings.
    processor=str.lower makes the comparison case-insensitive (candidate
    names are stored in their original case) while the returned match is
    still the original-case string from candidate_names, unchanged."""
    if not candidate_names:
        return None
    best_score, best_name = 0, None
    for ngram in _question_ngrams(lowered_question):
        match = process.extractOne(
            ngram, candidate_names, scorer=fuzz.ratio,
            processor=str.lower, score_cutoff=_FUZZY_SCORE_CUTOFF,
        )
        if match and match[1] > best_score:
            best_score, best_name = match[1], match[0]
    return best_name


def classify_query(question):
    """Route the question to a source_table, so a small table (e.g.
    country_performance) isn't drowned out by a much larger one (e.g.
    kpi_song) in nearest-neighbor search.

    Checks real country and artist names first (e.g. "India", "Taylor
    Swift") since those are unambiguous signals a generic keyword search
    would miss — e.g. "How did Taylor Swift perform in 2025?" has no
    "artist"/"singer" keyword at all otherwise. Falls back to generic
    keywords, then a fuzzy name match (typos/slight misspellings), then to
    no filter (search all tables).

    Returns (source_table, confident) — confident is False only for a
    fuzzy-fallback match. This matters to the confidence gate in
    _get_rag_reply_uncached(): an exact name/keyword match is strong enough
    evidence to skip that gate (see NO_MATCH_DISTANCE_THRESHOLD's docstring
    for why), but a fuzzy match is not — discovered via live testing, where
    "What is the weather today?" fuzzy-matched an unrelated real artist
    named "TOODAY" (ratio("today","tooday") is high) and, because
    source_table came back non-None, skipped the confidence gate entirely
    and returned a confidently wrong answer instead of "I don't have data
    to answer that." A fuzzy match must still pass the distance check.
    """
    lowered = question.lower()

    for name in _get_country_names():
        if _name_in_question(name, lowered):
            return 'country_performance', True
    if _alias_countries(lowered):
        return 'country_performance', True

    for name, _uri in _get_artist_catalog():
        if _name_in_question(name, lowered):
            return 'artist_performance', True

    for table, keywords in _TABLE_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            return table, True

    if _fuzzy_match_one(lowered, _get_country_names()):
        return 'country_performance', False
    if _fuzzy_match_one(lowered, [name for name, _uri in _get_artist_catalog()]):
        return 'artist_performance', False
    return None, False


def _drop_substring_duplicates(matches, name_of):
    """Given matches found longest-name-first, drops any match whose name
    is itself a substring of an already-kept (longer) match's name — e.g.
    a real artist literally named "Bunny" word-boundary-matches inside
    "Bad Bunny" too (space is a non-word char, so "Bunny" legitimately
    satisfies \\b...\\b on its own), which _name_in_question()'s word-
    boundary fix does NOT catch since it's not a false substring match,
    it's a real shorter name nested inside a real longer one. Found live:
    "Compare Bad Bunny and Drake" pulled in a 3rd, unrelated artist named
    "Bunny" alongside the two intended artists. `name_of` extracts the
    name from each item (matches can be plain names or (name, uri)
    tuples), so this works for both detect_countries() and
    detect_artists()."""
    kept = []
    for item in matches:
        name = name_of(item).lower()
        if any(name in name_of(k).lower() for k in kept):
            continue
        kept.append(item)
    return kept


def detect_countries(question):
    """Returns every real country name mentioned in the question (including
    via an alias like "US" -> "United States"), longest full-name match
    first (reuses the same name list/ordering as classify_query()), alias
    matches appended after. Exact/alias matches ONLY — deliberately no
    fuzzy fallback here (unlike classify_query()). This feeds directly into
    the ungated multi/single-country comparison paths in
    _get_rag_reply_uncached() (2/2b below), which were only ever designed
    for high-confidence exact matches and have no confidence-gate distance
    check; a fuzzy match plugged in here bypasses that check entirely and
    can produce a confidently wrong answer (discovered live: "What is the
    weather today?" fuzzy-matched an unrelated real artist named "TOODAY"
    through this same pattern in detect_artists() and got a wrong answer
    with no rejection). A typo'd country/artist name still gets routed
    correctly via classify_query()'s OWN fuzzy fallback in step 3, which
    DOES have the confidence gate. Used to detect multi-country comparison
    questions ("Compare India and Brazil", "Compare US and India") —
    classify_query() itself still returns a single table."""
    lowered = question.lower()
    found = []
    for name in _get_country_names():
        if _name_in_question(name, lowered):
            found.append(name)
    for name in _alias_countries(lowered):
        if name not in found:
            found.append(name)
    return _drop_substring_duplicates(found, name_of=lambda n: n)


def detect_artists(question):
    """Same idea as detect_countries(), for artist_performance's
    artist_name (see _get_artist_catalog() for why short names are
    excluded and how name collisions are resolved). Exact match ONLY — see
    detect_countries()'s docstring for why fuzzy matching was removed from
    here specifically (feeds the ungated comparison paths 2c/2d). Used for
    multi-artist comparison ("Compare Bad Bunny and Drake") and
    single-artist exact-chunk routing. Returns (name, artist_uri) pairs —
    gold_chunks' source_key for artist_performance is keyed on artist_uri,
    not name, so callers need the uri to actually filter chunks."""
    lowered = question.lower()
    found = []
    for name, uri in _get_artist_catalog():
        if _name_in_question(name, lowered):
            found.append((name, uri))
    return _drop_substring_duplicates(found, name_of=lambda pair: pair[0])


# ivfflat (schema.sql, idx_gold_chunks_embedding) is an APPROXIMATE nearest-
# neighbor index — pgvector's default ivfflat.probes=1 only searches 1 of
# the index's 216 lists, which measurably hurt recall once the index went
# live: a live regression check (single-entity "How did Kendrick Lamar
# perform in 2024?", previously correct in baseline_before.json) came back
# with 0/5 correct chunks at the default probes=1 (top result was an
# unrelated "Lamar Entertainment" label chunk).
#
# ivfflat's list clustering is randomized at CREATE INDEX time, so the
# right probes value is NOT portable across deployments/rebuilds — a probes
# value tuned against one CREATE INDEX run does not necessarily hit the
# same recall against a different run's clusters (confirmed: probes=8 (dev)
# and probes=32 (first AWS rebuild) both still missed the correct top-5 on
# an unfiltered 215K-row search on a second AWS deployment; compared
# against an exact/brute-force search — which did return all 5 correct
# chunks — and swept probes=32/60/100/150/216 directly against the live
# index: probes=60 was the first value to reach 5/5 correct, at ~1s per
# unfiltered query, and higher probes added query time without any further
# recall gain up to the exhaustive probes=216 case. 60 is used here as a
# recall-first choice — this is deployment-specific data, not a universal
# constant, so if the index is ever rebuilt again, re-sweep rather than
# assuming this value still holds.
IVFFLAT_PROBES = 60


def _set_probes(cur):
    cur.execute('SELECT 1')  # ensures the vector extension is loaded in this
                              # backend before SET, which a truly first-statement
                              # SET can otherwise reject as an unrecognized GUC
    cur.execute('SET ivfflat.probes = %s', [IVFFLAT_PROBES])


_reranker = None


def _get_reranker():
    """Lazy singleton, same pattern as _get_model() above. ms-marco-MiniLM-
    L-6-v2 is a small (~80MB) cross-encoder — fast enough on CPU for the
    15-20 candidate pairs scored per query here."""
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
    return _reranker


def rerank_chunks(question, chunks, top_k=5):
    """Cross-encoder rerank of an already-retrieved candidate set. Unlike
    embedding similarity (which scores question and chunk independently,
    then compares vectors), a cross-encoder scores the (question, chunk)
    PAIR jointly — materially better at judging "is this chunk actually
    relevant" than distance alone, at the cost of being too slow to run
    over the full table (hence: rerank a small candidate set, don't
    replace the initial retrieval)."""
    if not chunks:
        return chunks
    pairs = [(question, c['chunk_text']) for c in chunks]
    scores = _get_reranker().predict(pairs)
    ranked = sorted(zip(scores, chunks), key=lambda pair: pair[0], reverse=True)
    return [chunk for _score, chunk in ranked[:top_k]]


_RRF_K = 60  # standard Reciprocal Rank Fusion constant


def _rrf_merge(vector_rows, fts_rows):
    """Combines two independently-ranked candidate lists (vector distance
    order, full-text ts_rank order) into one deduped list ordered by
    combined Reciprocal Rank Fusion score — a chunk ranked highly by EITHER
    signal (not just vector similarity) surfaces near the top of the
    candidate set that then gets reranked/truncated to top_k. A chunk
    missing from one list simply doesn't get that list's term added (not
    zero-filled), so being in just one list still counts for something."""
    scores = {}
    rows_by_key = {}
    for rank, row in enumerate(vector_rows):
        key = (row['source_table'], row['source_key'])
        scores[key] = scores.get(key, 0) + 1 / (_RRF_K + rank)
        rows_by_key[key] = row
    for rank, row in enumerate(fts_rows):
        key = (row['source_table'], row['source_key'])
        scores[key] = scores.get(key, 0) + 1 / (_RRF_K + rank)
        rows_by_key.setdefault(key, row)
    ordered_keys = sorted(scores, key=lambda k: scores[k], reverse=True)
    return [rows_by_key[k] for k in ordered_keys]


def _rows_to_chunks(rows):
    return [{'source_table': r[0], 'source_key': r[1], 'chunk_text': r[2]} for r in rows]


def _search_chunks(query_embedding, question, source_table=None, source_key_like=None, top_k=5):
    """Hybrid retrieval: merges dense vector search (embedding <->, catches
    semantic similarity) with Postgres full-text search (chunk_tsv @@,
    catches exact keyword/entity-name hits vector similarity can under-rank
    — see schema.sql's idx_gold_chunks_tsv) via _rrf_merge(), then
    cross-encoder reranks the merged candidates down to top_k (see
    rerank_chunks()). source_table/source_key_like apply identically to
    both the vector and full-text queries, so entity-scoped callers (see
    retrieve_chunks_for_entity()) still only ever see that entity's chunks."""
    candidate_k = max(top_k * 4, 15)
    where_clauses = []
    base_params = []
    if source_table:
        where_clauses.append('source_table = %s')
        base_params.append(source_table)
    if source_key_like:
        where_clauses.append('source_key LIKE %s')
        base_params.append(source_key_like)
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ''

    with connections['gold'].cursor() as cur:
        _set_probes(cur)
        cur.execute(
            f"""
            SELECT source_table, source_key, chunk_text
            FROM gold_chunks
            {where_sql}
            ORDER BY embedding <-> %s::vector
            LIMIT %s
            """,  # noqa: S608 — where_sql built from fixed column names only
            [*base_params, query_embedding, candidate_k],
        )
        vector_rows = _rows_to_chunks(cur.fetchall())

        fts_where = where_clauses + ['chunk_tsv @@ websearch_to_tsquery(\'english\', %s)']
        fts_where_sql = f"WHERE {' AND '.join(fts_where)}"
        cur.execute(
            f"""
            SELECT source_table, source_key, chunk_text
            FROM gold_chunks
            {fts_where_sql}
            ORDER BY ts_rank(chunk_tsv, websearch_to_tsquery('english', %s)) DESC
            LIMIT %s
            """,  # noqa: S608 — fts_where_sql built from fixed column names only
            [*base_params, question, question, candidate_k],
        )
        fts_rows = _rows_to_chunks(cur.fetchall())

    merged = _rrf_merge(vector_rows, fts_rows)
    return rerank_chunks(question, merged, top_k=top_k)


def retrieve_chunks(query_embedding, question, source_table=None, top_k=5):
    """Hybrid vector+full-text search against gold_chunks, reranked. When
    source_table is given, restricts the search to that table."""
    return _search_chunks(query_embedding, question, source_table=source_table, top_k=top_k)


def retrieve_chunks_for_entity(query_embedding, question, source_table, entity_name, top_k=5):
    """Same hybrid search as retrieve_chunks(), restricted to one named
    entity's own chunks (source_key LIKE 'EntityName|%'). Used for multi-
    entity comparison questions so one entity's chunks can't crowd out
    another's in a single shared top-k (see get_rag_reply())."""
    return _search_chunks(
        query_embedding, question, source_table=source_table,
        source_key_like=f"{entity_name}|%", top_k=top_k,
    )


def _top1_distance(query_embedding, chunks):
    """L2 distance from query_embedding to the single nearest already-
    retrieved chunk (chunks[0], since retrieve_chunks() orders by
    distance). Returns None if chunks is empty. Used only by the
    confidence gate in get_rag_reply() — a second small query rather than
    changing retrieve_chunks()'s return shape everywhere it's called."""
    if not chunks:
        return None
    with connections['gold'].cursor() as cur:
        _set_probes(cur)
        cur.execute(
            "SELECT embedding <-> %s::vector FROM gold_chunks "
            "WHERE source_table = %s AND source_key = %s",
            [query_embedding, chunks[0]['source_table'], chunks[0]['source_key']],
        )
        row = cur.fetchone()
        return float(row[0]) if row else None


# Confidence gate (see get_rag_reply()): if classify_query() found no exact
# entity-name match (source_table is None, meaning the search fell back to
# an unfiltered scan across all 215K chunks) AND the closest result is
# still this far away, the retrieved context is treated as unreliable and
# generation is skipped entirely rather than risking a confidently-wrong
# answer.
#
# Threshold re-measured against the current (spotify-lake-dev-data-sourced)
# gold_chunks: a confirmed out-of-scope query ("what is the weather like
# today?", "tell me a joke") sits ~1.21-1.26. Vague/no-entity-keyword
# questions that coincidentally match a label whose name shares words with
# the query (e.g. "tell me about the global streaming trend" nearest-
# matching a label called "Trending Now") sit ~0.98-1.0 — tightened from
# 1.10 to 0.95 to catch this class without the confidence gate ever
# applying to a real entity-routed match (classify_query() sets
# source_table for those, so the gate is skipped entirely; see below).
#
# NOT fully solved by this threshold: some coincidental label-name matches
# sit as low as ~0.87 (e.g. "total streams of all years??" nearest-matching
# a label literally named "17 Earth Years") — indistinguishable by distance
# alone from a legitimate match (a correctly-routed India query sits at
# ~0.88). Fixing that class would need an entity-plausibility check beyond
# distance, not attempted here.
#
# Deliberately scoped to source_table is None only, not applied globally:
# baseline_before.json showed an exact-name-routed case ("how is india
# doing", lowercase, routed to country_performance via classify_query()'s
# country-name match, top-1 distance 1.1856) that is CORRECT despite
# exceeding this threshold — informal phrasing measurably increases
# embedding distance even for a right answer. Gating on distance alone for
# that case would suppress a good answer, so the gate only fires when
# classify_query() also failed to find an entity-name/keyword match — i.e.
# when there is no other signal of relevance to fall back on.
NO_MATCH_DISTANCE_THRESHOLD = 0.95
NO_DATA_REPLY = "I don't have data to answer that."


SYSTEM_PROMPT = (
    "You are a data analyst assistant for a Spotify streaming analytics "
    "platform. Answer the user's question using ONLY the context provided "
    "below. If the context doesn't contain the answer, say so — do not "
    "make up numbers. When you cite a fact, name the artist/country/label "
    "and time period it came from. "
    "Format your response in markdown: bold key numbers and entity names "
    "with **asterisks**, use a bullet list when presenting 2 or more facts "
    "or a comparison, keep paragraphs to 2-3 lines, and use a markdown "
    "table when comparing multiple entities across the same metrics. "
    "Write large stream counts in abbreviated form (e.g. 18.6B, 245M, 3.2K) "
    "instead of the full digit string, even if the context shows the full "
    "number — round to 1-2 decimal places. "
    "Lead with a short natural-language sentence before any bullets/table — "
    "don't open straight into a list. If a recent conversation is included "
    "below, answer as a continuation of it: reference earlier facts where "
    "relevant (e.g. \"compared to X...\") and don't reintroduce yourself or "
    "repeat framing you already used."
)


def _format_streams(n):
    """Abbreviates a large stream count to K/M/B/T instead of the full
    digit string — e.g. 18606949004 -> "18.61B". Used both for the
    deterministic sum_streams reply (built directly here, no LLM involved)
    and for the numeric values fed into build_sql_prompt()'s context,
    so trend/superlative answers phrase numbers the same way even though
    an LLM assembles the final sentence. Accepts int/float/Decimal;
    below 1000 falls back to a plain comma-formatted number, where an
    abbreviation wouldn't help readability."""
    n = float(n)
    sign = '-' if n < 0 else ''
    n = abs(n)
    for threshold, suffix in ((1e12, 'T'), (1e9, 'B'), (1e6, 'M'), (1e3, 'K')):
        if n >= threshold:
            return f"{sign}{n / threshold:.2f}{suffix}"
    return f"{sign}{n:,.0f}"

# Small-talk gets its own, much lighter system prompt — the data-analyst
# rules above (cite sources, tables, "ONLY use context") don't apply to "hi"
# or "thanks", and forcing them on produces stiff, out-of-place replies.
_SMALLTALK_SYSTEM_PROMPT = (
    "You are the friendly assistant for a Spotify streaming analytics "
    "platform (StreamPulse). Reply warmly and briefly (1-2 sentences, no "
    "markdown lists/tables) to this greeting/thanks/meta message. If asked "
    "what you can do, mention you can answer questions about country, "
    "artist, label, and song streaming performance, trends, and "
    "comparisons — grounded in real data, not guesses."
)


# Deliberately narrow, same style as _COUNT_KEYWORDS/_TREND_KEYWORDS — must
# never fire on a real data question. Checked via startswith/short-message
# heuristics rather than a bare substring match, since e.g. "thanks for the
# india stats" is a real follow-up, not small talk.
_GREETING_WORDS = {'hi', 'hello', 'hey', 'yo', 'sup'}
_THANKS_WORDS = {'thanks', 'thank you', 'thx', 'ty', 'cheers'}
_META_PHRASES = [
    'who are you', 'what are you', 'what can you do', 'what do you do',
    'help me', 'how does this work', 'what can i ask',
]

_REPEATED_LETTER_RE = re.compile(r'(.)\1+')


def _collapse_repeats(word):
    """Collapses runs of the same repeated letter to one — "heyyy"/"hiii"
    both become their base greeting word. Applied to BOTH the candidate
    word and the greeting set (see _GREETING_WORDS_COLLAPSED) so a real
    word with an intentional double letter (e.g. "hello"'s "ll") still
    matches consistently rather than only working for the un-collapsed
    form. Discovered via live testing: "heyyy" wasn't recognized as a
    greeting and fell through to "I don't have data to answer that.\""""
    return _REPEATED_LETTER_RE.sub(r'\1', word)


_GREETING_WORDS_COLLAPSED = {_collapse_repeats(w) for w in _GREETING_WORDS}


def detect_smalltalk(question):
    """True for greetings/thanks/meta questions about the bot itself — see
    module docstring note above _GREETING_WORDS. Short-message check (<=4
    words) on greetings/thanks avoids matching a longer real question that
    happens to start with "hi" or contain "thanks"."""
    lowered = question.strip().lower().rstrip('!.?')
    words = lowered.split()
    if len(words) <= 4:
        first_word_collapsed = _collapse_repeats(words[0]) if words else ''
        if lowered in _GREETING_WORDS or first_word_collapsed in _GREETING_WORDS_COLLAPSED:
            return True
        if any(lowered.startswith(t) for t in _THANKS_WORDS):
            return True
    return any(phrase in lowered for phrase in _META_PHRASES)


def _format_history(history):
    """Last 2 turns as a labelled block for the generation prompt (not just
    condensation — see get_rag_reply()/plan). Empty string when there's no
    history, so build_prompt() stays a no-op change for a fresh
    conversation."""
    if not history:
        return ""
    recent = history[-2:]
    lines = "\n".join(f"{turn['role']}: {turn['content']}" for turn in recent)
    return f"Recent conversation:\n{lines}\n\n"


def build_prompt(question, chunks, history=None):
    context = "\n".join(f"- {c['chunk_text']}" for c in chunks)
    return f"{_format_history(history)}Context:\n{context}\n\nQuestion: {question}"


# --- SQL router -------------------------------------------------------
#
# Vector similarity can only ever return the top-k chunks nearest to the
# question text — it cannot compute a MAX/COUNT/AVG or a year-over-year
# delta across rows it never retrieves together. Questions shaped like
# that are routed here instead, straight to the real gold tables, so the
# answer is a deterministic SQL result rather than a plausible-looking
# guess assembled from 5 semantically-similar-but-not-necessarily-correct
# chunks. Falls through to the normal vector-retrieval path (further down
# in get_rag_reply()) whenever no trigger keyword matches — this is
# additive, it never removes the pre-existing behavior.
#
# (table, entity_key_column, entity_display_column)
# artist_performance (built from Silver, see schema.sql) has real metrics,
# unlike kpi_artist -- so unlike the old kpi_artist-only setup, artist
# questions now support the full count/superlative/trend range through this
# generic dict, same as every other entity.
# Same ordering rationale as _TABLE_KEYWORDS above — kpi_song's generic
# "song"/"track" keywords checked last so a more specific co-occurring
# keyword (e.g. "artist") wins.
_SQL_TABLE_KEYWORDS = {
    'country_performance': ('country_name', 'country_name', ['country', 'countries', 'market', 'nation']),
    'artist_performance': ('artist_uri', 'artist_name', ['artist', 'artists', 'singer', 'singers', 'musician', 'musicians']),
    'label_performance_enhanced': ('standardized_label', 'standardized_label', ['label', 'labels', 'records', 'recordings']),
    'kpi_song': ('uri', 'uri', ['song', 'track', 'songs', 'tracks']),
}

_COUNT_KEYWORDS = ['how many', 'total number of', 'number of']
_TABLE_DISPLAY_NAME = {
    'country_performance': 'countries',
    'label_performance_enhanced': 'labels',
    'kpi_song': 'tracks',
    'artist_performance': 'artists',
}
_TREND_KEYWORDS = ['grew', 'growth', 'grow']
_SUPERLATIVE_KEYWORDS = [
    'highest', 'strongest', 'most', 'least', 'lowest', 'top', 'best',
    'worst', 'bottom', 'fastest', 'number one', 'number 1',
]
_ASCENDING_KEYWORDS = ['least', 'lowest', 'worst', 'bottom']

# Four keyword sets added to close a gap found by cross-checking this
# module against a separate Tableau dashboard the team also ships: it has
# widgets (catalog hit rate, a growth-percentage map/ranking, label market
# share, and a full multi-year streaming trend) with no equivalent
# deterministic intent here, so those questions previously fell through to
# vector retrieval — which, for a ranking or percentage question, produces
# an answer that *looks* authoritative but isn't a real computed result
# (confirmed live: a "highest growth" question returned a plausible-looking
# but non-exhaustive ranking assembled from whatever chunks were retrieved,
# not a true sorted query over all 72 countries). All four follow the same
# "compute with real SQL, only let the LLM phrase it" rule as every other
# intent in this file. Checked deliberately BEFORE _COUNT_KEYWORDS/
# _TREND_KEYWORDS/_SUPERLATIVE_KEYWORDS below since _GROWTH_RANK_KEYWORDS
# overlaps with _TREND_KEYWORDS ('growth' is a substring of both) — the
# more specific phrasing here wins by being checked first; a bare "how much
# did India grow in 2024" still falls through to the existing two-year
# delta `trend` intent unchanged.
_HIT_RATE_KEYWORDS = ['hit rate', 'catalog hit rate', 'hit percentage', 'percentage of hits']
_GROWTH_RANK_KEYWORDS = [
    'investment opportunity', 'growth percentage', 'growth rate',
    'fastest growing', 'highest growth', 'avg growth', 'average growth',
    'growth category',
]
_MARKET_SHARE_KEYWORDS = [
    'market share', 'market leader', 'market leaders',
    '% of total streams', 'percentage of streams', 'share of streams',
]
_TIME_SERIES_KEYWORDS = [
    'streaming trend', 'trend over time', 'over the years', 'by year',
    'by month', 'yearly trend', 'monthly trend', 'trend from',
    'history of streams',
]

_YEAR_RE = re.compile(r'\b(20\d{2})\b')


def _sql_target_table(lowered_question):
    """Pick which gold table an aggregate/count/trend question is about,
    using the same keyword-matching style as classify_query()/_TABLE_KEYWORDS."""
    for table, (key_col, name_col, keywords) in _SQL_TABLE_KEYWORDS.items():
        if any(kw in lowered_question for kw in keywords):
            return table, key_col, name_col
    return None, None, None


def detect_sql_intent(question):
    """Returns a dict describing the SQL path to take, or None to fall
    through to vector retrieval. Keyword-triggered only — false negatives
    (an aggregate question phrased without a trigger word) degrade
    gracefully to the existing vector-retrieval behavior, not a regression."""
    lowered = question.lower()

    # See the _HIT_RATE_KEYWORDS/_GROWTH_RANK_KEYWORDS/_MARKET_SHARE_KEYWORDS/
    # _TIME_SERIES_KEYWORDS docstring comment above for why these four are
    # checked before the generic table-keyword routing below — each has its
    # own table-defaulting logic instead of relying on _sql_target_table().
    if any(kw in lowered for kw in _HIT_RATE_KEYWORDS):
        years = _YEAR_RE.findall(question)
        entity_filter, entity_names = _detect_single_entity_filter('country_performance', question)
        return {
            'kind': 'hit_rate', 'table': 'country_performance',
            'entity_filter': entity_filter, 'entity_names': entity_names,
            'year': int(years[0]) if years else None,
        }

    if any(kw in lowered for kw in _MARKET_SHARE_KEYWORDS):
        # Not a plain _sql_target_table(lowered) call — country_performance's
        # own keyword list includes 'market' (for phrasing like "the market
        # in India"), which collided with _MARKET_SHARE_KEYWORDS itself: any
        # "market share" question contains "market", so it always matched
        # country_performance first regardless of what was actually asked
        # about (a live "which label has the highest market share" returned
        # countries, not labels). 'market' is excluded from the candidate
        # keyword lists here so the real entity noun (label/artist/song/
        # country) decides the table instead.
        ms_table, ms_key_col, ms_name_col = None, None, None
        for cand_table, (cand_key, cand_name, cand_kws) in _SQL_TABLE_KEYWORDS.items():
            if any(kw in lowered for kw in cand_kws if kw != 'market'):
                ms_table, ms_key_col, ms_name_col = cand_table, cand_key, cand_name
                break
        if ms_table is None:
            ms_table = 'label_performance_enhanced'
            ms_key_col, ms_name_col, _kw = _SQL_TABLE_KEYWORDS[ms_table]
        years = _YEAR_RE.findall(question)
        return {
            'kind': 'market_share', 'table': ms_table, 'key_col': ms_key_col, 'name_col': ms_name_col,
            'country_scope': _detect_country_scope(ms_table, question),
            'year': int(years[0]) if years else None,
        }

    if any(kw in lowered for kw in _TIME_SERIES_KEYWORDS):
        years = sorted({int(y) for y in _YEAR_RE.findall(question)})
        entity_filter, entity_names = _detect_single_entity_filter('country_performance', question)
        return {
            'kind': 'time_series', 'table': 'country_performance',
            'entity_filter': entity_filter, 'entity_names': entity_names,
            'year_start': years[0] if years else None,
            'year_end': years[-1] if years else None,
        }

    if any(kw in lowered for kw in _GROWTH_RANK_KEYWORDS):
        years = _YEAR_RE.findall(question)
        entity_filter, entity_names = _detect_single_entity_filter('country_performance', question)
        return {
            'kind': 'growth_rank', 'table': 'country_performance',
            'ascending': any(kw in lowered for kw in _ASCENDING_KEYWORDS),
            'entity_filter': entity_filter, 'entity_names': entity_names,
            'year': int(years[0]) if years else None,
        }

    table, key_col, name_col = _sql_target_table(lowered)
    if table is None:
        # No table KEYWORD at all, but a bare "how many streams do we have"
        # is still answerable — it's an implicit global total (every
        # country) UNLESS a specific country/artist is actually named (e.g.
        # "How many total streams does Brazil have?" — a real condensed
        # follow-up seen in live testing), in which case the SUM must be
        # scoped to just that entity via _detect_single_entity_filter() —
        # see its docstring for the bug this fixes (a global total that
        # silently ignored the named entity).
        if any(kw in lowered for kw in _COUNT_KEYWORDS) and 'stream' in lowered:
            years = _YEAR_RE.findall(question)
            for candidate_table, candidate_key in (
                ('country_performance', 'country_name'),
                ('artist_performance', 'artist_uri'),
            ):
                entity_filter, entity_names = _detect_single_entity_filter(candidate_table, question)
                if entity_filter:
                    return {
                        'kind': 'sum_streams', 'table': candidate_table, 'key_col': candidate_key,
                        'entity_filter': entity_filter, 'entity_names': entity_names,
                        'year': int(years[0]) if years else None,
                    }
            return {
                'kind': 'sum_streams', 'table': 'country_performance', 'key_col': None,
                'entity_filter': None, 'entity_names': None,
                'year': int(years[0]) if years else None,
            }
        return None

    if any(kw in lowered for kw in _COUNT_KEYWORDS):
        # "how many streams..." asks to SUM a metric, not COUNT(DISTINCT
        # entity) — without this, a question like "how many total streams
        # across all countries in 2025" matched "countries" as the table
        # keyword and wrongly answered with the *country count* (72)
        # instead of a streams total. 'stream' takes priority over the
        # generic count path whenever both are present, since a metric
        # noun in the question is a stronger, more specific signal than an
        # entity-plural keyword.
        if 'stream' in lowered:
            years = _YEAR_RE.findall(question)
            entity_filter, entity_names = _detect_single_entity_filter(table, question)
            return {
                'kind': 'sum_streams', 'table': table, 'key_col': key_col,
                'entity_filter': entity_filter, 'entity_names': entity_names,
                'country_scope': _detect_country_scope(table, question),
                'year': int(years[0]) if years else None,
            }
        # "how many hit songs/tracks [in YEAR]" — a blanket COUNT(DISTINCT
        # uri) ignored both the "hit" filter and the year, e.g. answering
        # "how many hit songs were there in 2024" with the total track
        # count (242,572) regardless of hit status or year. Only kpi_song
        # has is_hit; other tables' "hit" concepts (hit_track_count on
        # artist_performance, hit_songs on country_performance) are
        # pre-aggregated counts, not filterable rows, so this only applies
        # here.
        if table == 'kpi_song' and 'hit' in lowered:
            years = _YEAR_RE.findall(question)
            return {
                'kind': 'count_hits', 'table': table,
                'year': int(years[0]) if years else None,
            }
        # A bare COUNT(DISTINCT ...) ignored any year mentioned (e.g. "how
        # many artists in 2024?" and "...in 2025?" both silently returned
        # the same all-time total) — the year is passed through here and
        # used to filter the count in run_sql_intent() when present.
        years = _YEAR_RE.findall(question)
        return {
            'kind': 'count', 'table': table, 'key_col': key_col,
            'country_scope': _detect_country_scope(table, question),
            'year': int(years[0]) if years else None,
        }

    if any(kw in lowered for kw in _TREND_KEYWORDS):
        years = [int(y) for y in _YEAR_RE.findall(question)]
        if not years:
            return None  # no explicit year to compute a delta against — don't guess
        # "grew ... in 2024" (1 year) implies "vs the year before" — target
        # is that one year, prev is target-1. "grew ... between 2023 and
        # 2024" (2 years) explicitly names BOTH endpoints, and always means
        # growth FROM the earlier year TO the later one regardless of the
        # order they're mentioned in — max()/min() here, not years[0],
        # fixes a real bug found via live testing where "between 2023 and
        # 2024" computed growth(2022->2023) instead (years[0]=2023 was
        # wrongly treated as target, giving prev=2022).
        if len(years) >= 2:
            target_year, prev_year = max(years[:2]), min(years[:2])
        else:
            target_year, prev_year = years[0], years[0] - 1
        return {
            'kind': 'trend', 'table': table, 'key_col': key_col, 'name_col': name_col,
            'target_year': target_year, 'prev_year': prev_year,
            'ascending': any(kw in lowered for kw in _ASCENDING_KEYWORDS),
            'entity_filter': _detect_entity_filter(table, question),
        }

    if any(kw in lowered for kw in _SUPERLATIVE_KEYWORDS):
        # A mentioned year ("top artists in 2025?") was previously ignored
        # entirely, always ranking by all-time total instead — found via
        # live testing, where "who were the top artists in 2025?" returned
        # the same all-time global ranking as a plain "top artists?" would.
        # Same for a mentioned country ("top artists in India?") — see
        # _detect_country_scope()'s docstring.
        years = _YEAR_RE.findall(question)
        return {
            'kind': 'superlative', 'table': table, 'key_col': key_col, 'name_col': name_col,
            'ascending': any(kw in lowered for kw in _ASCENDING_KEYWORDS),
            'entity_filter': _detect_entity_filter(table, question),
            'country_scope': _detect_country_scope(table, question),
            'year': int(years[0]) if years else None,
        }

    return None


def _detect_country_scope(table, question):
    """A single named country used to scope a non-country-table query —
    e.g. "top artists in India", "top labels in India in 2025". This is
    the cross-type case: artist_performance/label_performance_enhanced/
    kpi_song all have their own country_name column (per schema.sql), but
    nothing previously checked the question for a country name unless the
    table itself WAS country_performance. Found live: "top artists/labels
    in India" always returned the same all-time GLOBAL ranking, silently
    ignoring "India" — the same class of bug as the entity_filter fixes
    above, just for country name instead of artist/label name. Only
    applies when exactly one country is named (2+ would need a real
    per-country comparison, which this single-scope filter can't express)
    and the table isn't country_performance itself (which already uses
    country_name AS its own entity, via _detect_entity_filter())."""
    if table == 'country_performance':
        return None
    countries = detect_countries(question)
    return countries[0] if len(countries) == 1 else None


def _detect_single_entity_filter(table, question):
    """Like _detect_entity_filter() but for a SINGLE named entity (>=1, not
    >=2 — a comparison needs two entities, a plain "how much does Brazil
    have" needs only one). Used by sum_streams so a question like "How many
    total streams does Brazil have?" computes an entity-scoped SUM instead
    of a global one — discovered via live testing where a real condensed
    follow-up ("What about Brazil?" → "How many total streams does Brazil
    have?") silently summed every country's streams together, ignoring
    "Brazil" entirely, because the old sum_streams path had no entity
    awareness at all. Returns (key_values_for_sql, display_names_for_reply)
    — for country_performance these are identical (country_name IS the
    readable name); for artist_performance the SQL side needs artist_uri
    but the reply needs the real artist_name, hence two separate lists.
    Returns (None, None) when no entity of the right type is named."""
    if table == 'country_performance':
        names = detect_countries(question)
        return (names, names) if names else (None, None)
    if table == 'artist_performance':
        pairs = detect_artists(question)
        if pairs:
            return [uri for _name, uri in pairs], [name for name, _uri in pairs]
        return None, None
    return None, None


def _detect_entity_filter(table, question):
    """2+ named entities in a trend/superlative question (e.g. "which grew
    faster, India or Brazil") means the answer should be computed ONLY over
    those entities, not a global top-N — otherwise a comparison question
    silently gets an unrelated global ranking back (the entities the user
    named never even appear in the reply). Returns the key_col values to
    filter on (country_name for country_performance, artist_uri for
    artist_performance — same values key_col already stores), or None when
    fewer than 2 named entities are found, preserving today's global-top-N
    behavior exactly for questions that don't name specific entities."""
    if table == 'country_performance':
        names = detect_countries(question)
        return names if len(names) >= 2 else None
    if table == 'artist_performance':
        pairs = detect_artists(question)
        return [uri for _name, uri in pairs] if len(pairs) >= 2 else None
    return None


# kpi_song's only "name" column is uri (spotify:track:...) -- there's no
# title column on that table itself, but track_catalog (built from Silver,
# see schema.sql) has the real track_name for most uris. Superlative/trend
# queries on kpi_song join it in so "top track" cites a real name instead
# of the bare uri; COALESCE falls back to the uri for the tracks
# track_catalog doesn't cover (different source pipelines, not every
# kpi_song uri has a Silver-side match).
_NAME_JOIN = {
    'kpi_song': (
        'LEFT JOIN track_catalog tc ON tc.uri = {table}.uri',
        'COALESCE(tc.track_name, {table}.uri)',
    ),
}


def run_sql_intent(intent, limit=5):
    """Executes the SQL template selected by detect_sql_intent(). All
    table/column names come from the fixed _SQL_TABLE_KEYWORDS mapping
    (never from the raw question text); only numeric/year values are
    passed as query parameters. Returns (rows, description) where rows is
    a list of (label, value) tuples and description is a short string
    used both for the LLM context and for the 'sources' field."""
    table = intent['table']
    key_col = intent.get('key_col')
    join_clause, name_expr = _NAME_JOIN.get(table, ('', '{name_col}'))
    join_clause = join_clause.format(table=table)

    with connections['gold'].cursor() as cur:
        if intent['kind'] == 'count':
            year = intent.get('year')
            country_scope = intent.get('country_scope')
            # A mentioned year, or a mentioned country for a non-country
            # table (e.g. "how many artists in India?"), previously had no
            # effect here at all — see detect_sql_intent()'s docstring
            # notes on these two fixes.
            clauses, params = [], []
            if year is not None:
                clauses.append("year = %s")
                params.append(year)
            if country_scope is not None:
                clauses.append("country_name = %s")
                params.append(country_scope)
            where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cur.execute(
                f"SELECT COUNT(DISTINCT {key_col}) FROM {table} {where_sql}",  # noqa: S608 — key_col/table from fixed dict, not user input
                params,
            )
            desc_parts = [p for p in [
                f"year={year}" if year is not None else None,
                f"country={country_scope}" if country_scope is not None else None,
            ] if p]
            desc = f"sql:{table}:COUNT(DISTINCT {key_col})" + (f" WHERE {' AND '.join(desc_parts)}" if desc_parts else "")
            count = cur.fetchone()[0]
            return [(table, count)], desc

        if intent['kind'] == 'count_hits':
            if intent['year'] is not None:
                cur.execute(
                    "SELECT COUNT(DISTINCT uri) FROM kpi_song WHERE is_hit = 1 AND year = %s",
                    [intent['year']],
                )
                desc = f"sql:kpi_song:COUNT(DISTINCT uri) WHERE is_hit=1 AND year={intent['year']}"
            else:
                cur.execute("SELECT COUNT(DISTINCT uri) FROM kpi_song WHERE is_hit = 1")
                desc = "sql:kpi_song:COUNT(DISTINCT uri) WHERE is_hit=1"
            count = cur.fetchone()[0]
            return [(table, count)], desc

        if intent['kind'] == 'sum_streams':
            entity_filter = intent.get('entity_filter')
            country_scope = intent.get('country_scope')
            clauses, params = [], []
            if intent['year'] is not None:
                clauses.append(f"{table}.year = %s")
                params.append(intent['year'])
            if entity_filter:
                clauses.append(f"{table}.{intent['key_col']} = ANY(%s)")
                params.append(entity_filter)
            if country_scope is not None:
                clauses.append(f"{table}.country_name = %s")
                params.append(country_scope)
            where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cur.execute(
                f"SELECT SUM(total_streams) FROM {table} {where_sql}",  # noqa: S608 — table/key_col from fixed dict, not user input
                params,
            )
            total = cur.fetchone()[0] or 0
            desc_parts = []
            if entity_filter:
                desc_parts.append(f"{intent['key_col']} IN {tuple(entity_filter)}")
            if country_scope is not None:
                desc_parts.append(f"country={country_scope}")
            if intent['year'] is not None:
                desc_parts.append(f"year={intent['year']}")
            desc = f"sql:{table}:SUM(total_streams)" + (f" WHERE {' AND '.join(desc_parts)}" if desc_parts else "")
            return [(table, total)], desc

        if intent['kind'] == 'superlative':
            name_col = intent['name_col']
            select_name = name_expr.format(table=table, name_col=name_col)
            direction = 'ASC' if intent['ascending'] else 'DESC'
            entity_filter = intent.get('entity_filter')
            year = intent.get('year')
            country_scope = intent.get('country_scope')
            # Named entities (e.g. "which of India and Brazil has the
            # highest streams") restrict the ranking to just those entities
            # instead of a global top-N — otherwise the named entities might
            # not even appear in the result (see _detect_entity_filter()).
            # A mentioned year ("top artists in 2025?") or a mentioned
            # country for a non-country table ("top artists in India?")
            # previously had no effect at all, always ranking by the
            # all-time global total instead — found via live testing.
            clauses = []
            params = []
            if entity_filter:
                clauses.append(f"{table}.{key_col} = ANY(%s)")
                params.append(entity_filter)
            if year is not None:
                clauses.append(f"{table}.year = %s")
                params.append(year)
            if country_scope is not None:
                clauses.append(f"{table}.country_name = %s")
                params.append(country_scope)
            where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            row_limit = len(entity_filter) if entity_filter else limit
            params.append(row_limit)
            cur.execute(
                f"SELECT {select_name}, SUM({table}.total_streams) AS total "  # noqa: S608
                f"FROM {table} {join_clause} {where_clause} GROUP BY {table}.{key_col}, {select_name} "
                f"ORDER BY total {direction} LIMIT %s",
                params,
            )
            rows = cur.fetchall()
            filter_notes = []
            if entity_filter:
                filter_notes.append(f"{key_col} IN {tuple(entity_filter)}")
            if country_scope is not None:
                filter_notes.append(f"country={country_scope}")
            if year is not None:
                filter_notes.append(f"year={year}")
            filter_note = f" WHERE {' AND '.join(filter_notes)}" if filter_notes else ""
            return rows, f"sql:{table}:SUM(total_streams) {direction}{filter_note}"

        if intent['kind'] == 'trend':
            name_col = intent['name_col']
            select_name = name_expr.format(table=table, name_col=name_col)
            direction = 'ASC' if intent['ascending'] else 'DESC'
            entity_filter = intent.get('entity_filter')
            entity_clause = f"AND {table}.{key_col} = ANY(%s)" if entity_filter else ""
            params = [intent['target_year'], intent['prev_year']]
            if entity_filter:
                params.append(entity_filter)
            params.extend([intent['prev_year'], intent['target_year']])
            row_limit = len(entity_filter) if entity_filter else limit
            params.append(row_limit)
            cur.execute(
                f"""
                WITH yearly AS (
                    SELECT {table}.{key_col} AS k, {select_name} AS name, {table}.year AS year, SUM({table}.total_streams) AS total
                    FROM {table} {join_clause}
                    WHERE {table}.year IN (%s, %s) {entity_clause}
                    GROUP BY {table}.{key_col}, {select_name}, {table}.year
                )
                SELECT a.name, (a.total - COALESCE(b.total, 0)) AS growth
                FROM yearly a
                LEFT JOIN yearly b ON a.k = b.k AND b.year = %s
                WHERE a.year = %s
                ORDER BY growth {direction}
                LIMIT %s
                """,  # noqa: S608 — key_col/name_col/table from fixed dict, not user input
                params,
            )
            rows = cur.fetchall()
            filter_note = f" WHERE {key_col} IN {tuple(entity_filter)}" if entity_filter else ""
            return rows, f"sql:{table}:growth({intent['prev_year']}->{intent['target_year']}) {direction}{filter_note}"

        if intent['kind'] == 'hit_rate':
            # Catalog Hit Rate — hit_songs/active_songs, as a percentage.
            # Pre-formatted as a string ("27.22%") rather than a bare float
            # — build_sql_prompt() auto-runs numeric row values through
            # _format_streams() for K/M/B/T abbreviation, which rounds
            # anything under 1000 to 0 decimals (27.22 -> "27"). A string
            # value bypasses that formatting entirely, same trick used by
            # market_share/growth_rank below.
            year = intent.get('year')
            entity_filter = intent.get('entity_filter')
            clauses, params = [], []
            if year is not None:
                clauses.append("year = %s")
                params.append(year)
            if entity_filter:
                clauses.append("country_name = ANY(%s)")
                params.append(entity_filter)
            where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cur.execute(
                f"SELECT SUM(hit_songs)::float / NULLIF(SUM(active_songs), 0) * 100 "
                f"FROM country_performance {where_sql}",
                params,
            )
            pct = cur.fetchone()[0]
            label = ', '.join(entity_names) if (entity_names := intent.get('entity_names')) else 'All Countries'
            value = f"{pct:.2f}%" if pct is not None else "no data"
            desc_parts = [p for p in [
                f"year={year}" if year is not None else None,
                f"country IN {tuple(entity_filter)}" if entity_filter else None,
            ] if p]
            desc = "sql:country_performance:SUM(hit_songs)/SUM(active_songs)*100" + (f" WHERE {' AND '.join(desc_parts)}" if desc_parts else "")
            return [(f"Catalog hit rate ({label})", value)], desc

        if intent['kind'] == 'growth_rank':
            # Investment Opportunity Matrix / growth map — ranks countries
            # by AVG(growth_percentage). Only country_performance carries
            # this column (per schema.sql), so this intent is always
            # country-scoped, unlike superlative's four-table generality.
            year = intent.get('year')
            entity_filter = intent.get('entity_filter')
            direction = 'ASC' if intent['ascending'] else 'DESC'
            # growth_percentage is NaN (not NULL) for each country's very
            # first period, since there's no prior period to compute growth
            # against — Postgres's AVG() propagates NaN through the whole
            # aggregate instead of skipping it the way it skips NULL, which
            # without this filter turned every no-year-given ranking into
            # 'nan%' for every country (found via live testing). Postgres
            # (unlike strict IEEE 754) treats NaN = 'NaN' as true, so this
            # comparison reliably excludes those rows.
            clauses, params = ["growth_percentage <> 'NaN'"], []
            if year is not None:
                clauses.append("year = %s")
                params.append(year)
            if entity_filter:
                clauses.append("country_name = ANY(%s)")
                params.append(entity_filter)
            where_sql = f"WHERE {' AND '.join(clauses)}"
            row_limit = len(entity_filter) if entity_filter else limit
            params.append(row_limit)
            cur.execute(
                f"SELECT country_name, AVG(growth_percentage) AS avg_growth "
                f"FROM country_performance {where_sql} "
                f"GROUP BY country_name ORDER BY avg_growth {direction} NULLS LAST LIMIT %s",
                params,
            )
            rows = [(name, f"{avg:.2f}%" if avg is not None else "no data") for name, avg in cur.fetchall()]
            desc_parts = [p for p in [
                f"year={year}" if year is not None else None,
                f"country IN {tuple(entity_filter)}" if entity_filter else None,
            ] if p]
            desc = f"sql:country_performance:AVG(growth_percentage) {direction}" + (f" WHERE {' AND '.join(desc_parts)}" if desc_parts else "")
            return rows, desc

        if intent['kind'] == 'market_share':
            # Record Label Market Leaders — each entity's share of the
            # total, as a percentage. Two queries (grouped totals, then the
            # grand total under the same WHERE clause) rather than one
            # dense window-function query, matching this file's existing
            # preference for readable, separately-parameterized queries.
            name_col = intent['name_col']
            select_name = name_expr.format(table=table, name_col=name_col)
            year = intent.get('year')
            country_scope = intent.get('country_scope')
            clauses, params = [], []
            if year is not None:
                clauses.append(f"{table}.year = %s")
                params.append(year)
            if country_scope is not None:
                clauses.append(f"{table}.country_name = %s")
                params.append(country_scope)
            where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cur.execute(
                f"SELECT {select_name}, SUM({table}.total_streams) AS total "  # noqa: S608
                f"FROM {table} {join_clause} {where_clause} GROUP BY {table}.{key_col}, {select_name} "
                f"ORDER BY total DESC LIMIT %s",
                [*params, limit],
            )
            grouped = cur.fetchall()
            cur.execute(
                f"SELECT SUM({table}.total_streams) FROM {table} {where_clause}",  # noqa: S608
                params,
            )
            grand_total = cur.fetchone()[0] or 0
            rows = [
                (name, f"{(total / grand_total * 100):.2f}%" if grand_total else "no data")
                for name, total in grouped
            ]
            desc_parts = [p for p in [
                f"year={year}" if year is not None else None,
                f"country={country_scope}" if country_scope is not None else None,
            ] if p]
            desc = f"sql:{table}:SUM(total_streams)/grand_total*100" + (f" WHERE {' AND '.join(desc_parts)}" if desc_parts else "")
            return rows, desc

        if intent['kind'] == 'time_series':
            # Global Streaming Trend — a real multi-row series (year, total),
            # unlike the two-point 'trend' delta above. Values are left as
            # numeric (not pre-formatted strings) since these ARE stream
            # totals — build_sql_prompt()'s existing _format_streams() K/M/
            # B/T abbreviation is correct here, same as sum_streams/superlative.
            entity_filter = intent.get('entity_filter')
            year_start = intent.get('year_start')
            year_end = intent.get('year_end')
            clauses, params = [], []
            if year_start is not None and year_end is not None:
                clauses.append("year BETWEEN %s AND %s")
                params.extend([year_start, year_end])
            if entity_filter:
                clauses.append("country_name = ANY(%s)")
                params.append(entity_filter)
            where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cur.execute(
                f"SELECT year, SUM(total_streams) AS total FROM country_performance "
                f"{where_sql} GROUP BY year ORDER BY year",
                params,
            )
            rows = cur.fetchall()
            desc_parts = [p for p in [
                f"years={year_start}-{year_end}" if year_start is not None else None,
                f"country IN {tuple(entity_filter)}" if entity_filter else None,
            ] if p]
            desc = "sql:country_performance:SUM(total_streams) GROUP BY year" + (f" WHERE {' AND '.join(desc_parts)}" if desc_parts else "")
            return rows, desc

    raise ValueError(f"unhandled SQL intent kind: {intent['kind']}")


def build_sql_prompt(question, rows, description, history=None):
    # Every value here is a stream total or a growth delta (superlative/
    # trend are the only two callers) — always abbreviated via
    # _format_streams(), same K/M/B/T formatting the deterministic
    # sum_streams reply uses, so the LLM's phrasing matches. isinstance
    # checks for (int, float, Decimal) rather than just int — Postgres
    # SUM() returns Decimal, which previously fell through to the plain
    # str(value) branch below and printed without comma formatting at all.
    lines = "\n".join(
        f"- {label}: {_format_streams(value)}" if isinstance(value, (int, float, decimal.Decimal)) else f"- {label}: {value}"
        for label, value in rows
    )
    return f"{_format_history(history)}Context (computed directly from the database, {description}):\n{lines}\n\nQuestion: {question}"


class ProviderBudgetExceeded(Exception):
    """Raised by _call_gemini()/_call_groq() when cache.is_over_budget()
    says we're already at that provider's known free-tier limit -- lets
    _call_llm() move on to the next provider instead of burning a real
    request that would just 429 anyway."""


class AllProvidersUnavailable(Exception):
    """Raised by _call_llm() when every configured provider is either
    unset or over budget. Distinct from a generic HTTPError/connection
    failure so it's inspectable/loggable if this needs debugging later --
    apps.chatbot.services.get_bot_reply()'s existing bare `except
    Exception` still catches it the same as any other failure, no change
    needed there."""


def _call_llm(prompt, system_prompt=None):
    messages = [
        {'role': 'system', 'content': system_prompt or SYSTEM_PROMPT},
        {'role': 'user', 'content': prompt},
    ]
    if GEMINI_API_KEY:
        try:
            return _call_gemini(messages)
        except ProviderBudgetExceeded:
            pass
        except requests.exceptions.HTTPError as e:
            # A real 429 despite our own budget tracking saying we were
            # fine -- can happen right after a fresh Redis flush, or the
            # first time a limit is hit before enough usage was recorded
            # to trip our conservative threshold. Same recovery as
            # ProviderBudgetExceeded: try the next provider rather than
            # letting this become a hard failure.
            if e.response is not None and e.response.status_code == 429:
                cache.mark_exhausted('gemini')
            elif e.response is not None and 500 <= e.response.status_code < 600:
                # A transient 5xx (Service Unavailable, Bad Gateway, etc.)
                # is Google's outage, not our quota or a malformed request
                # -- found live: Gemini returned a plain 503 and this branch
                # re-raised it, killing the whole request before Groq ever
                # got a chance, same failure mode the timeout handling below
                # already covers for a different symptom. Same recovery.
                pass
            else:
                raise
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            # A slow/unreachable Gemini previously killed the whole
            # request instead of falling through to Groq -- found live,
            # right before a presentation: a single Gemini read-timeout
            # (nothing to do with quota/budget) surfaced as the generic
            # "placeholder" canned reply with no indication anything was
            # wrong. Timeouts get the same "try the next provider"
            # treatment as a 429, just without marking the budget exhausted
            # (a slow response isn't evidence of being rate-limited).
            pass
    if GROQ_API_KEY:
        try:
            return _call_groq(messages)
        except ProviderBudgetExceeded:
            pass
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                cache.mark_exhausted('groq')
            elif e.response is not None and 500 <= e.response.status_code < 600:
                pass
            else:
                raise
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            pass
    if not OLLAMA_URL:
        raise AllProvidersUnavailable('Gemini/Groq over budget, timed out, or unset, and no Ollama URL configured')
    return _call_ollama(messages)


def _call_gemini(messages):
    if cache.is_over_budget('gemini'):
        raise ProviderBudgetExceeded('gemini')
    response = requests.post(
        GEMINI_URL,
        headers={
            'Authorization': f'Bearer {GEMINI_API_KEY}',
            'Content-Type': 'application/json',
        },
        json={
            'model': GEMINI_MODEL,
            'messages': messages,
            'temperature': GENERATION_OPTIONS['temperature'],
            # Higher than Groq/Ollama's 1024 -- gemini-flash-latest is a
            # "thinking" model: its internal reasoning tokens are billed
            # against max_tokens too (confirmed: a trivial "what is 2+2"
            # prompt used ~89 reasoning tokens before the 1-token visible
            # answer). 1024 was getting exhausted by reasoning alone on
            # real prompts, truncating the response before any answer text
            # came out (observed: garbled/empty replies).
            'max_tokens': 4096,
        },
        timeout=30,
    )
    response.raise_for_status()
    # Gemini's free tier is request-rate limited (~20/min observed), not
    # token-metered the way Groq's daily cap is -- so usage is tracked as
    # a request count (1 per call), not tokens.
    cache.record_usage('gemini', 1)
    return response.json()['choices'][0]['message']['content']


def _call_groq(messages):
    if cache.is_over_budget('groq'):
        raise ProviderBudgetExceeded('groq')
    response = requests.post(
        GROQ_URL,
        headers={
            'Authorization': f'Bearer {GROQ_API_KEY}',
            'Content-Type': 'application/json',
        },
        json={
            'model': GROQ_MODEL,
            'messages': messages,
            'temperature': GENERATION_OPTIONS['temperature'],
            'max_tokens': 1024,
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    # Groq's free tier is a tokens-per-day cap (100K observed) -- tracked
    # from the actual usage the API reports for this call, not estimated.
    total_tokens = data.get('usage', {}).get('total_tokens', 0)
    cache.record_usage('groq', total_tokens)
    return data['choices'][0]['message']['content']


def _call_ollama(messages):
    response = requests.post(
        f'{OLLAMA_URL}/api/chat',
        json={
            'model': OLLAMA_MODEL,
            'messages': messages,
            'stream': False,
            'options': GENERATION_OPTIONS,
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()['message']['content']


# Condensation is only triggered when the question itself signals it's a
# follow-up — a pronoun/reference word, or an elliptical "what about X"
# pattern. Discovered via live testing: with condensation running
# unconditionally whenever history existed, a fully self-contained new
# question ("Which country grew the fastest between 2023 and 2024?") got
# rewritten to wrongly include unrelated entities from 2 turns earlier
# ("...for Bad Bunny and The Weekend"), corrupting an otherwise-correct SQL
# router match into a wrong, artist-scoped answer. The condense system
# prompt already asks the LLM to "return it unchanged" when a question is
# already self-contained, but that's advisory — an LLM can still be
# over-helpful and inject context that wasn't asked for. This check is a
# deterministic gate in front of it, same "keyword-triggered, fails safe by
# doing less" philosophy as detect_smalltalk()/detect_sql_intent() elsewhere
# in this file: no signal words → skip the LLM call entirely, use the
# question exactly as typed.
_FOLLOWUP_SIGNAL_WORDS = {
    'it', 'its', "it's", 'that', 'this', 'those', 'these', 'they', 'them',
    'their', 'he', 'she', 'him', 'her', 'his',
}
_FOLLOWUP_SIGNAL_PHRASES = [
    'what about', 'and what', 'how about', 'same for', 'compared to that',
    'compared to it', 'compared to them',
]
# A bare comparison ("which grew faster?", "who has more streams?") names
# no entity of its own but still depends entirely on prior conversation —
# see _try_comparison_followup_rewrite()'s docstring. Also checked by
# _needs_condensation() below (not just that function) — without this, a
# question like "which grew faster?" matched none of the pronoun/phrase
# signals above, needs_condensation() returned False, and the ENTIRE
# rewrite pipeline (both deterministic helpers and the LLM condenser) was
# skipped outright — a real bug caught in live testing where the raw,
# unresolved question fell straight into fuzzy/vector matching instead.
_COMPARISON_SIGNAL_WORDS = {'which', 'who', 'faster', 'better', 'higher', 'more', 'most', 'grew', 'growth'}


def _needs_condensation(question):
    """True only when `question` contains a pronoun/reference word, an
    elliptical follow-up pattern, or a bare comparison word — see
    _FOLLOWUP_SIGNAL_WORDS/_PHRASES/_COMPARISON_SIGNAL_WORDS above for why
    each exists. A question with none of these signals is already
    self-contained by construction (nothing in it refers back to
    anything), so condensation is skipped entirely rather than trusting an
    LLM call to correctly no-op."""
    lowered = question.lower()
    if any(phrase in lowered for phrase in _FOLLOWUP_SIGNAL_PHRASES):
        return True
    words = set(re.findall(r"[a-z']+", lowered))
    if words & _FOLLOWUP_SIGNAL_WORDS:
        return True
    if words & _COMPARISON_SIGNAL_WORDS:
        # A comparison word ('which'/'who'/'growth'/'more'/...) only
        # signals a genuine follow-up when the question doesn't already
        # name its own subject — "which countries have the highest growth
        # percentage?" is fully self-contained (it says "countries" right
        # there) even though it contains "which" and "growth". Without
        # this check, _condense_question()'s LLM call still ran for
        # already-complete questions like this and could blend them with
        # an unrelated prior topic — found live: this exact question,
        # asked right after "what is the catalog hit rate globally?",
        # came back garbled as "...growth percentage in catalog hit rate
        # globally..." and got mis-routed to the hit_rate intent instead
        # of growth_rank. Mirrors the identical guard already used inside
        # _try_comparison_followup_rewrite() for the same class of bug
        # (see its docstring) — this just applies it one level up, before
        # the LLM condenser gets a chance to run at all.
        return _sql_target_table(lowered)[0] is None
    return False


# Handles the extremely common "What about X?" / "and X?" follow-up shape
# WITHOUT any LLM call — see _try_simple_followup_rewrite()'s docstring for
# why this exists (the LLM condenser is not reliably faithful to the named
# entity).
_SIMPLE_FOLLOWUP_RE = re.compile(r'^(?:what about|and what about|how about|and)\s+(.+?)\s*\??$', re.IGNORECASE)


def _try_simple_followup_rewrite(question, history):
    """Deterministic rewrite for the "What about X?" / "and X?" pattern —
    bypasses _condense_question()'s LLM call entirely for this one common
    case. Exists because that LLM call is NOT reliably faithful to the
    named entity: live testing caught a real run where "What about
    Brazil?" got rewritten to "What is the total number of streams on
    Spotify in a particular country?" — "Brazil" was silently DROPPED and
    replaced with a vague placeholder, so no amount of downstream entity
    detection could ever recover it (the name was simply gone from the
    text). This pattern is simple enough to handle with zero risk of that:
    extract X, confirm it's a real country/artist name, and directly
    construct the standalone question by substitution — the entity name is
    copied verbatim, never regenerated, so it can't be dropped or altered.
    A year mentioned in the most recent user turn is carried forward too
    (so "...in 2024? / What about Brazil?" stays scoped to 2024, not
    silently becoming an all-time question). Returns None (meaning: fall
    back to the LLM condenser) when the pattern doesn't match or the
    fragment isn't a recognizable real entity name — e.g. "what about the
    weather" correctly falls through, since detect_countries/detect_artists
    won't find anything there."""
    match = _SIMPLE_FOLLOWUP_RE.match(question.strip())
    if not match:
        return None
    fragment = match.group(1)
    countries = detect_countries(fragment)
    artists = detect_artists(fragment)
    if not countries and not artists:
        return None
    entity_name = countries[0] if countries else artists[0][0]
    prior_user_turns = [t['content'] for t in reversed(history) if t.get('role') == 'user']
    year_match = _YEAR_RE.search(prior_user_turns[0]) if prior_user_turns else None
    year_note = f" in {year_match.group(1)}" if year_match else ""
    return f"How did {entity_name} perform{year_note}?"


def _recent_entities(history, limit=2):
    """Most recently mentioned real country/artist names across the
    conversation, most recent turn first, deduped. Used by
    _try_comparison_followup_rewrite() to figure out which entities a bare
    "which grew faster?" is asking about."""
    found = []
    for turn in reversed(history):
        content = turn.get('content', '')
        for name in detect_countries(content):
            if name not in found:
                found.append(name)
        for name, _uri in detect_artists(content):
            if name not in found:
                found.append(name)
        if len(found) >= limit:
            break
    return found[:limit]


def _try_comparison_followup_rewrite(question, history):
    """Deterministic rewrite for a bare comparison follow-up like "which
    grew faster?" / "who has more streams?" — the question names NO entity
    of its own, so it depends entirely on the last 2 distinct entities
    already discussed earlier in the conversation. Same motivation as
    _try_simple_followup_rewrite(): the LLM condenser is not reliably
    faithful here either — live testing caught it turning "which grew
    faster?" (after an India/Brazil discussion) into a rewrite that somehow
    fuzzy-matched an unrelated real artist literally named "Faster",
    comparing two nonsense entities instead of India and Brazil. Naming
    both real entities explicitly, verbatim, removes any need for the LLM
    to guess who "which" refers to. Returns None (fall back to the LLM
    condenser) unless the question has a comparison signal word, names no
    entity of its own, AND at least 2 recent entities were actually found
    in history — a comparison question with nothing to compare falls
    through unchanged rather than fabricating entities.

    Deliberately checks detect_countries() only, NOT detect_artists() —
    this data's artist catalog turns out to contain real artists literally
    named "faster"/"Faster", so detect_artists("which grew faster?") itself
    returns a match, which would make this guard wrongly conclude the bare
    comparison "already names its own entity" and bail out before ever
    reconstructing the real India/Brazil comparison. Country names colliding
    with a common comparison word this way is far less likely, so this
    trades a small amount of theoretical coverage (a genuine artist-name
    comparison phrased with zero other signal) for not being sabotaged by
    this specific real collision."""
    lowered = question.lower()
    words = set(re.findall(r"[a-z']+", lowered))
    if not (words & _COMPARISON_SIGNAL_WORDS):
        return None
    if detect_countries(question):
        return None  # already names its own country — not a bare comparison
    # "who"/"which" alone are ambiguous — they show up in genuine bare
    # comparisons ("which grew faster?") AND in perfectly self-contained
    # superlative questions ("who were the top artists in 2024?"), which
    # already have everything they need and must NOT get history entities
    # spliced in. Found live: right after a "top artists in 2025?" turn,
    # "who were top artists in 2024??" got rewritten into "Fuerza Regida vs
    # Bruno Mars: who were top artists in 2024??" (both names lifted from
    # the PREVIOUS ANSWER, not asked about), which then wrongly scoped the
    # SQL query to just those two names instead of a normal top-5. A
    # question that already resolves to a real table via
    # _sql_target_table() is self-contained — skip the rewrite so the SQL
    # router handles it untouched.
    if _sql_target_table(lowered)[0] is not None:
        return None
    entities = _recent_entities(history, limit=2)
    if len(entities) < 2:
        return None
    return f"{entities[0]} vs {entities[1]}: {question.strip()}"


# Deliberately a different, narrow system prompt from SYSTEM_PROMPT (the
# markdown-formatted-answer one) — this call has exactly one job: rewrite,
# not answer. Reusing SYSTEM_PROMPT here would have the model try to
# format/cite/answer instead of just resolving the reference.
_CONDENSE_SYSTEM_PROMPT = (
    "Rewrite the latest user question into a fully self-contained question, "
    "resolving any pronouns or implicit references (e.g. \"what about "
    "2024\", \"and Brazil\", \"she\", \"that country\") using the "
    "conversation history below. If the latest question is already "
    "self-contained, return it unchanged. Return ONLY the rewritten "
    "question — no explanation, no quotes, no extra text."
)


def _condense_question(question, history):
    """Rewrites `question` into a standalone question using the last
    couple of conversation turns, so every existing routing function
    (classify_query(), detect_sql_intent(), detect_countries()/
    detect_artists(), the confidence gate, etc.) keeps seeing the same
    kind of fully-specified question it already handles — none of that
    logic needs to know conversation history exists. Falls back to the
    original question unchanged if the LLM call fails for any reason, so a
    broken condensation step degrades to today's stateless per-message
    behavior rather than breaking the reply entirely.

    Only the last 4 turns (~2 exchanges) are included — enough for the
    immediate follow-up case ("what about 2024?") this exists for, without
    letting the prompt (and therefore token cost) grow unbounded over a
    long conversation. The client also caps what it sends (see
    chatbot.js), this is a second, independent bound server-side."""
    recent = history[-4:]
    transcript = "\n".join(f"{turn['role']}: {turn['content']}" for turn in recent)
    prompt = (
        f"Conversation so far:\n{transcript}\n\n"
        f"Latest question: {question}\n\n"
        "Rewritten, self-contained question:"
    )
    try:
        rewritten = _call_llm(prompt, system_prompt=_CONDENSE_SYSTEM_PROMPT)
        rewritten = rewritten.strip().strip('"')
        return rewritten or question
    except Exception:
        return question


def get_rag_reply(question, history=None):
    """Public entry point — checks the Redis-backed response cache (see
    apps.chatbot.cache) keyed on the ORIGINAL incoming question+history,
    before any follow-up condensation runs, so a cache hit skips
    condensation, retrieval, AND generation entirely. Falls through to
    _get_rag_reply_uncached() on a miss (or if Redis is unreachable —
    cache.get_cached_reply() fails open), then caches whatever that
    returns."""
    cached = cache.get_cached_reply(question, history)
    if cached is not None:
        return cached
    result = _get_rag_reply_uncached(question, history)
    cache.set_cached_reply(question, history, result)
    return result


def _get_rag_reply_uncached(question, history=None):
    # 0. Follow-up resolution — if there's conversation history AND the
    # question actually looks like a follow-up (see _needs_condensation()),
    # rewrite it into a standalone one before any routing runs. Tries the
    # deterministic _try_simple_followup_rewrite() first (handles "What
    # about X?" with zero risk of dropping the entity — see its docstring);
    # only falls back to the LLM-based _condense_question() for shapes that
    # simple pattern doesn't cover (pronouns, "that country", etc.).
    # Skipped entirely for a fresh conversation (history empty/None) and
    # for an already-complete question, so a first message — and any
    # standalone question later in the conversation — costs exactly zero
    # extra LLM calls.
    if history and _needs_condensation(question):
        question = (
            _try_simple_followup_rewrite(question, history)
            or _try_comparison_followup_rewrite(question, history)
            or _condense_question(question, history)
        )

    # 0b. Small talk — greetings/thanks/meta questions get a short warm
    # reply with no retrieval/SQL at all. Checked right after condensation
    # (so "thanks!" after a data answer still condenses fine, though it
    # rarely needs to) and before the SQL router so it can never be
    # shadowed by a table keyword.
    if detect_smalltalk(question):
        reply_text = _call_llm(question, system_prompt=_SMALLTALK_SYSTEM_PROMPT)
        return {'reply': reply_text, 'sources': []}

    # 1. SQL router — MAX/COUNT/AVG/trend questions have a deterministic
    # answer no top-k vector search can provide (see detect_sql_intent()
    # docstring). Checked first; falls through to normal retrieval below
    # when no trigger keyword matches.
    sql_intent = detect_sql_intent(question)
    if sql_intent is not None:
        rows, description = run_sql_intent(sql_intent)
        if sql_intent['kind'] == 'count':
            count = rows[0][1]
            table = rows[0][0]
            label = _TABLE_DISPLAY_NAME.get(table, table)
            reply_text = random.choice([
                f"There are **{count}** distinct {label} in the data.",
                f"I count **{count}** distinct {label} in the dataset.",
                f"Looking at the data, there are **{count}** {label} total.",
            ])
            return {'reply': reply_text, 'sources': [description]}
        if sql_intent['kind'] == 'sum_streams':
            total = _format_streams(rows[0][1])
            year_note = f" in {sql_intent['year']}" if sql_intent['year'] else ""
            entity_names = sql_intent.get('entity_names')
            entity_note = f" for {', '.join(entity_names)}" if entity_names else ""
            reply_text = random.choice([
                f"Total streams{entity_note}{year_note}: **{total}**.",
                f"That comes to **{total}** total streams{entity_note}{year_note}.",
                f"Streams{entity_note}{year_note} add up to **{total}**.",
            ])
            return {'reply': reply_text, 'sources': [description]}
        if sql_intent['kind'] == 'count_hits':
            count = rows[0][1]
            year_note = f" in {sql_intent['year']}" if sql_intent['year'] else ""
            reply_text = random.choice([
                f"There are **{count}** hit tracks{year_note} in the data.",
                f"I found **{count}** hit tracks{year_note}.",
                f"**{count}** tracks are marked as hits{year_note}.",
            ])
            return {'reply': reply_text, 'sources': [description]}
        prompt = build_sql_prompt(question, rows, description, history=history)
        reply_text = _call_llm(prompt)
        return {'reply': reply_text, 'sources': [description]}

    # 2. Multi-country comparison — a single shared top-k lets whichever
    # country is semantically nearest crowd out the other(s) entirely (see
    # retrieve_chunks_for_entity() docstring), so a question naming 2+ real
    # countries gets one scoped retrieval per country instead of one shared
    # query. Same idea for artists in 2c/2d below.
    countries = detect_countries(question)
    if len(countries) >= 2:
        embedding = embed_query(question)
        chunks = []
        for name in countries:
            chunks.extend(retrieve_chunks_for_entity(embedding, question, 'country_performance', name, top_k=5))
        prompt = build_prompt(question, chunks, history=history)
        reply_text = _call_llm(prompt)
        sources = [f"{c['source_table']}:{c['source_key']}" for c in chunks]
        return {'reply': reply_text, 'sources': sources}

    # 2b. Single named/aliased country — same exact-match reasoning as the
    # comparison case above, not just a table restriction. Matters most for
    # aliases ("US", "UK"): the chunk text itself says "United States", not
    # "US", so relying on embedding similarity alone (the generic path
    # below) can miss it entirely and surface an unrelated country instead.
    if len(countries) == 1:
        embedding = embed_query(question)
        chunks = retrieve_chunks_for_entity(embedding, question, 'country_performance', countries[0], top_k=5)
        prompt = build_prompt(question, chunks, history=history)
        reply_text = _call_llm(prompt)
        sources = [f"{c['source_table']}:{c['source_key']}" for c in chunks]
        return {'reply': reply_text, 'sources': sources}

    # 2c. Multi-artist comparison — same reasoning as multi-country above.
    # Checked after countries (a question naming both would be unusual, and
    # country takes priority since that path was there first).
    artists = detect_artists(question)
    if len(artists) >= 2:
        embedding = embed_query(question)
        chunks = []
        for name, uri in artists:
            chunks.extend(retrieve_chunks_for_entity(embedding, question, 'artist_performance', uri, top_k=5))
        prompt = build_prompt(question, chunks, history=history)
        reply_text = _call_llm(prompt)
        sources = [f"{c['source_table']}:{c['source_key']}" for c in chunks]
        return {'reply': reply_text, 'sources': sources}

    # 2d. Single named artist — same exact-match reasoning as 2b for
    # countries. Without this, "How did Taylor Swift perform in 2025?" (no
    # "artist" keyword) falls all the way to the generic path below, where
    # embedding similarity alone can surface unrelated track chunks instead
    # of her own artist_performance chunks (observed in testing).
    if len(artists) == 1:
        name, uri = artists[0]
        embedding = embed_query(question)
        chunks = retrieve_chunks_for_entity(embedding, question, 'artist_performance', uri, top_k=5)
        prompt = build_prompt(question, chunks, history=history)
        reply_text = _call_llm(prompt)
        sources = [f"{c['source_table']}:{c['source_key']}" for c in chunks]
        return {'reply': reply_text, 'sources': sources}

    # 3. Normal single-entity/unclassified path.
    embedding = embed_query(question)
    source_table, confident_match = classify_query(question)
    chunks = retrieve_chunks(embedding, question, source_table=source_table)

    # Confidence gate — skipped only for a CONFIDENT match (exact
    # entity-name or keyword match; see classify_query()'s confident flag,
    # NO_MATCH_DISTANCE_THRESHOLD's docstring for why routed/informally-
    # phrased matches can legitimately exceed this distance and still be
    # correct). Applied whenever source_table is None OR the match came
    # from the fuzzy fallback (confident_match is False) — a fuzzy match is
    # weak evidence on its own and must still clear the distance check, or
    # a coincidental fuzzy hit (e.g. "today" fuzzy-matching an unrelated
    # artist literally named "TOODAY") can return a confidently wrong
    # answer instead of NO_DATA_REPLY.
    if source_table is None or not confident_match:
        top1_distance = _top1_distance(embedding, chunks)
        if top1_distance is None or top1_distance > NO_MATCH_DISTANCE_THRESHOLD:
            return {'reply': NO_DATA_REPLY, 'sources': []}

    prompt = build_prompt(question, chunks, history=history)
    reply_text = _call_llm(prompt)

    sources = [f"{c['source_table']}:{c['source_key']}" for c in chunks]
    return {'reply': reply_text, 'sources': sources}
