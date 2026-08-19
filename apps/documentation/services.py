"""
Documentation catalog for the Documentation page.

get_documentation_cards() returns real project reference content — Gold
schema, RAG chatbot capabilities, API surface, and deployment — kept here
so it's easy to find and edit without touching template markup. Swap for
markdown files or a docs CMS later if this outgrows a Python list.
"""


def get_documentation_cards():
    return [
        {
            'icon': 'bi-rocket-takeoff',
            'category': 'Getting Started',
            'title': 'Project Setup Guide',
            'text': 'Clone the repo, configure environment variables, and run the site locally.',
            'code': {
                'language': 'bash',
                'snippet': 'python -m venv venv\nsource venv/bin/activate\npip install -r requirements.txt\npython manage.py runserver',
            },
        },
        {
            'icon': 'bi-diagram-3',
            'category': 'Architecture',
            'title': 'Pipeline Architecture',
            'text': 'Spotify streaming data flows through Bronze, Silver, and Gold layers on S3, then into Postgres/pgvector and Redis for serving. See the Architecture page for the full RAG pipeline breakdown.',
        },
        {
            'icon': 'bi-table',
            'category': 'Data',
            'title': 'Gold Schema Reference',
            'text': '7 tables: country_performance, kpi_artist, kpi_song, label_performance_enhanced, and monthly_trends from the source pipeline, plus artist_performance and track_catalog — rebuilt directly from the Silver layer since the original Gold source had no artist-level metrics.',
            'code': {
                'language': 'sql',
                'snippet': '-- artist_performance: country_name, artist_uri, artist_name,\n-- total_streams, track_count, hit_track_count, best_rank, year_month\nSELECT * FROM artist_performance WHERE country_name = \'India\';',
            },
        },
        {
            'icon': 'bi-chat-dots',
            'category': 'RAG Chatbot',
            'title': 'What the Chatbot Can Answer',
            'text': 'Aggregate questions (counts, totals, trends, "top N") are computed with exact SQL, not guessed. Entity questions (a country, artist, label, or song) use hybrid vector + keyword retrieval with reranking. Follow-up questions carry conversation memory. Out-of-scope questions get an honest "I don\'t have data" instead of a fabricated answer.',
        },
        {
            'icon': 'bi-code-slash',
            'category': 'API Reference',
            'title': 'REST API Reference',
            'text': 'Interactive Swagger/Redoc docs are live at /api/v1/docs/ and /api/v1/redoc/.',
            'code': {
                'language': 'http',
                'snippet': 'POST /api/v1/chatbot/messages/',
            },
        },
        {
            'icon': 'bi-box-seam',
            'category': 'Deployment',
            'title': 'Deployment Guide',
            'text': 'Runs on AWS EC2 behind Nginx + Gunicorn as a systemd service (streampulse.service), backed by Postgres with the pgvector extension and a local Redis instance for caching and LLM rate-limit tracking.',
            'code': {
                'language': 'bash',
                'snippet': 'sudo systemctl restart streampulse\nsudo systemctl status streampulse',
            },
        },
        {
            'icon': 'bi-people',
            'category': 'Contributing',
            'title': 'Contributing Guide',
            'text': 'Coding standards, branching strategy, and how to submit changes.',
        },
    ]
