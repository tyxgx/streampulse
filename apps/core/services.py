"""
Content for the core informational pages (Architecture & Pipeline).

This is hand-authored project description, not pipeline output, so it
isn't a "swap for a real API" integration point. It's centralized here
anyway so the content is easy to find and edit without touching template
markup.

Writing pattern used throughout this file: every card/caption opens with a
plain-English clause anyone can follow, then backs it up with the precise
technical detail — so the page works for a first-time visitor and a
mid-level technical reader at once. This matters because the Architecture
page doubles as the visual aid for the team's live presentation.
"""


def get_progress_markers():
    """The 0% / 50% / 100% framing strip shown at three points down the
    page, so it reads as one continuous story instead of two unrelated
    halves — matches the language used in the live presentation."""
    return {
        'start': {'percent': '0%', 'label': 'Where this starts: raw streaming data'},
        'mid': {'percent': '50%', 'label': "Data is now trustworthy. Here's what we built on top of it."},
        'end': {'percent': '100%', 'label': 'Live, answering real questions right now'},
    }


def get_architecture_flow():
    """Ordered stages shown as connected cards on the Architecture page."""
    return [
        {
            'icon': 'bi-hdd-network',
            'title': 'Raw Data',
            'text': 'Nothing has been touched yet — this is streaming activity exactly as it happened, per country, per track, per day.',
        },
        {
            'icon': 'bi-cloud-arrow-up',
            'title': 'Amazon S3',
            'text': 'Every stage below reads from and writes to one shared storage location, organized into a structured file format (Parquet) so later stages can read only what they need instead of scanning everything.',
        },
        {
            'icon': 'bi-layers',
            'title': 'Bronze Layer',
            'text': "The data lands here untouched, kept as a permanent, unedited record — so there's always an original copy to audit against if something downstream looks wrong.",
        },
        {
            'icon': 'bi-funnel',
            'title': 'Silver Layer',
            'text': 'Now it gets cleaned — duplicates removed, and when a song has two or more artists, each one gets proper, separated credit instead of being tangled together.',
        },
        {
            'icon': 'bi-gem',
            'title': 'Gold Layer',
            'text': 'The data is now business-ready: 7 curated tables covering country, artist, label, and song performance across 72 countries and 2017–2026 — and also turned into 560K+ searchable pieces of text for the chatbot.',
        },
        {
            'icon': 'bi-chat-dots',
            'title': 'RAG Chatbot',
            'text': "Ask it a question in plain English and it looks up the real answer instead of guessing — combining an exact-match database lookup with an AI-powered search, and it says so honestly when it doesn't know something.",
        },
        {
            'icon': 'bi-people',
            'title': 'Users',
            'text': 'This is what analysts and stakeholders actually touch — asking the chatbot a question directly and getting an answer grounded in real data.',
        },
    ]


def get_layer_details():
    """Bronze/Silver/Gold medallion-architecture summaries."""
    return [
        {
            'badge': 'Raw',
            'title': 'Bronze',
            'text': "Think of this as the original receipt — an unedited copy of every streaming export, kept exactly as it arrived so nothing is ever silently lost or altered.",
        },
        {
            'badge': 'Cleaned',
            'title': 'Silver',
            'text': "This is the clean-up stage — duplicate records removed, and every song's artist credits correctly parsed out, even for collaborations. It's also the source we used to rebuild artist-level metrics the original Gold layer was missing.",
        },
        {
            'badge': 'Curated',
            'title': 'Gold',
            'text': "The finished, business-ready product — 7 tables covering country, label, song, and monthly-trend performance, plus two (artist performance and the track catalog) we built ourselves directly from Silver. This is what actually powers the chatbot.",
        },
    ]


def get_gold_stats():
    """Headline scale numbers for the Gold layer, shown as a stat strip."""
    return [
        {'value': '72', 'label': 'countries'},
        {'value': '59,776', 'label': 'artists'},
        {'value': '242,572', 'label': 'tracks'},
        {'value': '27,000+', 'label': 'labels'},
        {'value': '2017–2026', 'label': 'years of data'},
    ]


def get_gold_tables():
    """The Gold layer's table families, shown as a card grid."""
    return [
        {'icon': 'bi-globe-americas', 'title': 'Country', 'text': 'How each country performed — streams, hit songs, and growth, month by month.'},
        {'icon': 'bi-mic', 'title': 'Artist', 'text': 'How each artist performed — streams, how many tracks charted, and their best rank, per country and month.'},
        {'icon': 'bi-tags', 'title': 'Label', 'text': 'How each record label performed — streams and catalog size, per country and month.'},
        {'icon': 'bi-music-note-beamed', 'title': 'Song', 'text': 'How each individual track performed, and whether it counted as a hit, per country and month.'},
        {'icon': 'bi-graph-up-arrow', 'title': 'Trends', 'text': 'The bigger picture — how the whole catalog is trending over time.'},
    ]


def get_grain_examples():
    """A tiny worked example of what 'grain' means, sitting right under the
    Gold-tables grid so both a beginner and a mid-level reader immediately
    know what one row of any of those tables actually represents."""
    return {
        'intro': "One question worth answering before going further: what does a single row in any of these tables actually mean? That's called the table's grain — get it wrong, and every number built on top of it is wrong too.",
        'examples': [
            {'title': 'country_performance', 'text': 'One row = one country, in one month.'},
            {'title': 'kpi_song', 'text': 'One row = one country, one song, in one month.'},
        ],
    }


def get_rag_concept():
    """The 'What is RAG?' beginner concept section — introduces the idea in
    plain language before the branching architecture diagram gets into the
    mechanics. Mirrors the shape used in the live presentation."""
    return {
        'intro': 'Instead of asking an AI model to answer purely from what it already "knows," we first look up the real, relevant facts from our own data — and only then let the model use those facts to write the answer.',
        'tagline': "Don't guess. Look it up, then answer.",
        'nodes': [
            {'icon': 'bi-chat-left-text', 'title': 'Question', 'text': 'A user asks something in plain English.'},
            {'icon': 'bi-search', 'title': 'Retrieve real facts', 'text': 'We look up what\'s actually true in our data.'},
            {'icon': 'bi-robot', 'title': 'Generate answer', 'text': 'An AI model turns those facts into a sentence.'},
            {'icon': 'bi-check-circle', 'title': 'Answer', 'text': 'Grounded in real data — or an honest "I don\'t know."'},
        ],
    }


def get_rag_flow_overview():
    """Node data for the compact 'at a glance' RAG architecture diagram —
    Gold tables feeding a prep stage, a routing stage, and an output stage.
    The 7-step get_rag_pipeline_steps() below opens each of these boxes up
    in more detail; this is deliberately just the shape, not the mechanics."""
    return {
        'source': {'icon': 'bi-database', 'title': 'Gold Tables', 'text': 'Our trustworthy data, in PostgreSQL.'},
        'prep': [
            {'icon': 'bi-scissors', 'title': 'Chunking', 'text': 'Every row becomes a short, readable summary sentence.'},
            {'icon': 'bi-compass', 'title': 'Embedding', 'text': 'That sentence becomes 384 numbers capturing its meaning (pgvector), so similar ideas end up numerically close together.'},
            {'icon': 'bi-type', 'title': 'Full-text Index', 'text': 'The same text is also indexed for exact keyword search, in parallel.'},
        ],
        'route': [
            {'icon': 'bi-123', 'title': 'SQL Router', 'text': 'Exact-number questions get a real database query — guaranteed correct.'},
            {'icon': 'bi-search', 'title': 'Hybrid Search', 'text': 'Open-ended questions get a search across the summaries above.'},
        ],
        'output': [
            {'icon': 'bi-shield-check', 'title': 'Confidence Gate', 'text': 'An honesty check — is what we found actually good enough to answer from?'},
            {'icon': 'bi-robot', 'title': 'LLM Answer', 'text': 'A grounded response, written only from what was actually retrieved.'},
        ],
    }


def get_tech_stack():
    """Badges naming the concrete technology behind the architecture."""
    return [
        {'icon': 'bi-filetype-py', 'label': 'Django'},
        {'icon': 'bi-database', 'label': 'PostgreSQL'},
        {'icon': 'bi-compass', 'label': 'pgvector'},
        {'icon': 'bi-cpu', 'label': 'MiniLM Embeddings'},
        {'icon': 'bi-lightning-charge', 'label': 'Redis'},
        {'icon': 'bi-robot', 'label': 'Gemini · Groq · Ollama'},
        {'icon': 'bi-cloud', 'label': 'AWS EC2'},
        {'icon': 'bi-hdd-network', 'label': 'Nginx · Gunicorn'},
    ]


def get_deployment_flow():
    """Linear stages showing how a real request reaches the app on AWS."""
    return [
        {'icon': 'bi-window', 'title': 'Browser', 'text': "Where the user actually is — anywhere in the world."},
        {'icon': 'bi-door-open', 'title': 'Nginx', 'text': 'The front door — routes incoming traffic and serves images/styles directly, without bothering the app.'},
        {'icon': 'bi-gear', 'title': 'Gunicorn', 'text': 'The engine that actually runs our Python/Django code for each request.'},
        {'icon': 'bi-filetype-py', 'title': 'Django', 'text': "Our application itself — the chatbot's logic and backend."},
        {'icon': 'bi-cloud', 'title': 'AWS EC2', 'text': 'The real server all of this runs on, live, 24/7 — not a laptop demo.'},
    ]


def get_rag_pipeline_steps():
    """Numbered steps shown on the Architecture page describing the RAG
    chatbot's own internal pipeline — the detailed breakdown behind the
    compact get_rag_flow_overview() diagram above it, since this is the
    most differentiated part of the project."""
    return [
        {
            'step': 1,
            'title': 'Query Rewrite',
            'text': 'Follow-up questions shouldn\'t need to repeat themselves — "what about Brazil?" is automatically resolved into a standalone question using recent conversation history before anything else runs.',
        },
        {
            'step': 2,
            'title': 'Intent Routing',
            'text': 'Every question gets sorted first: exact-number questions (counts, sums, trends, "top N") go to a deterministic SQL path; everything else goes to semantic retrieval.',
        },
        {
            'step': 3,
            'title': 'Hybrid Retrieval',
            'text': 'For open questions, we search two different ways at once — by meaning (dense vector search via pgvector) and by exact keyword (full-text search) — then merge the results fairly using Reciprocal Rank Fusion.',
        },
        {
            'step': 4,
            'title': 'Reranking',
            'text': 'The merged results get double-checked — a more accurate (but slower) cross-encoder model re-scores just the top candidates to fix any ordering mistakes the fast first pass made.',
        },
        {
            'step': 5,
            'title': 'Confidence Gate',
            'text': "Before answering, we ask: is the best match we found actually good enough? If the closest data is still too far from what was asked, the bot honestly says it doesn't have that data — instead of guessing.",
        },
        {
            'step': 6,
            'title': 'Grounded Generation',
            'text': 'Only once the confidence check passes does an AI model write the actual answer — and only from the retrieved data (Gemini first, with Groq and a local Ollama model as automatic fallbacks if one is unavailable).',
        },
        {
            'step': 7,
            'title': 'Response Caching',
            'text': "Repeated questions don't redo all of the above — Redis caches recent answers and tracks usage per AI provider, keeping the bot fast and within free-tier rate limits.",
        },
    ]


def get_rag_walkthrough_narrative():
    """The 7-beat spoken narrative for the standalone RAG pipeline
    walkthrough page (core:rag_pipeline_walkthrough) — not the marketing
    copy used on the Architecture page above, this is written to be read
    aloud live, standing at a laptop, presenting. Same plain-English-then-
    technical-detail pattern as the rest of this file, just longer per
    beat since there's no card grid constraining length here."""
    return [
        {
            'title': 'The problem this actually solves',
            'text': (
                "A dashboard is built to answer the questions someone thought to design a chart "
                "for, ahead of time. It cannot answer an arbitrary custom business question that "
                "wasn't anticipated. So instead of asking a user to manually dig through dashboards "
                "or write SQL themselves, we built an intelligent chatbot: it understands the "
                "question in plain language, retrieves the relevant business information from our "
                "Gold layer, and generates a conversational answer using an LLM — a large language "
                "model, an AI system trained to understand and generate human-like language."
            ),
        },
        {
            'title': 'The data foundation',
            'text': (
                "The workflow starts with our Gold layer data, sitting in S3 after being processed "
                "through the AWS pipeline. That data is fetched and loaded into PostgreSQL — one "
                "single source of truth that everything downstream, including this chatbot, reads "
                "from directly."
            ),
        },
        {
            'title': 'Making structured rows searchable',
            'text': (
                "Our Spotify data lives in tables — rows and columns. To make that meaningful for a "
                "language-based AI system, we first convert the important records into short, "
                "meaningful text descriptions — we call these chunks. But when the system needs to "
                "search and compare the *meaning* of these chunks, it works far more effectively "
                "with numbers than with raw text. So each chunk is converted into a numerical "
                "representation called a vector embedding, using the all-MiniLM-L6-v2 sentence "
                "transformer model. Those embeddings are stored directly inside PostgreSQL using the "
                "pgvector extension, which lets us quickly find information that is similar in "
                "meaning to whatever the user just asked."
            ),
        },
        {
            'title': "The smart part — deciding how to answer",
            'text': (
                "When a user asks a question, the backend first analyzes it. If the question needs "
                "an exact numerical value — 'which country had the highest streams?', 'top 10 "
                "artists in 2024' — the system directly executes an optimized SQL query. But if the "
                "question is semantic — 'tell me about Taylor Swift's performance over the years' — "
                "the system converts the question into an embedding, performs a vector similarity "
                "search using pgvector, retrieves the most relevant chunks, and sends them to the "
                "language model as context. And if it doesn't find any relevant chunks, it doesn't "
                "hallucinate — it answers honestly that it doesn't have the data to answer that "
                "question. That's what makes this smarter than a simple chatbot."
            ),
        },
        {
            'title': 'Generating the answer',
            'text': (
                "We primarily use Gemini Flash for response generation, with fallback support for "
                "Groq and a local Ollama model to improve reliability. The model generates answers "
                "only from the retrieved business data — reducing hallucination and keeping every "
                "response grounded in our actual Spotify dataset, never in what the model already "
                "'knows' from its training."
            ),
        },
        {
            'title': 'Redis caching',
            'text': (
                "If the same or a very similar question has already been asked and processed, "
                "caching lets us avoid repeating that work — which improves response time and "
                "reduces how much we rely on external AI provider calls."
            ),
        },
        {
            'title': 'Why this architecture',
            'text': (
                "Users can ask questions naturally, without knowing any SQL. Because responses are "
                "generated from retrieved business data, the answers stay relevant and trustworthy. "
                "And by using PostgreSQL with the pgvector extension instead of standing up a "
                "separate vector database, the whole solution stays simpler to run and more "
                "cost-effective — one database doing both jobs."
            ),
        },
    ]


def get_project_summary_capabilities():
    """Short capability cards for the shareable project-summary page
    (core:summary) — what a visitor can actually ask the chatbot,
    written for someone with zero prior context on the project."""
    return [
        {'icon': 'bi-123', 'title': 'Exact totals & counts', 'text': '"How many total streams do we have?" — scoped by any year or country.'},
        {'icon': 'bi-trophy', 'title': 'Rankings', 'text': '"Who are the top streamed artists?" "Which label has the highest market share?"'},
        {'icon': 'bi-graph-up', 'title': 'Rates & growth', 'text': '"What\'s the catalog hit rate globally?" "Which countries have the highest growth?"'},
        {'icon': 'bi-clock-history', 'title': 'Trends over time', 'text': '"Show me the streaming trend from 2017 to 2026" — a real multi-year series.'},
        {'icon': 'bi-chat-square-text', 'title': 'Open-ended stories', 'text': '"Tell me about Bad Bunny\'s performance." — found via semantic search, not a fixed query.'},
        {'icon': 'bi-shield-check', 'title': 'Honest limits', 'text': '"What\'s the weather today?" → an honest "I don\'t have that data," never a hallucination.'},
    ]
