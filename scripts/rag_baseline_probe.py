"""
Baseline capture script for the RAG implementation-log task.

Runs a fixed 15-question test set through the live apps.chatbot.rag pipeline
and records, per question: routed table, top-5 retrieved chunk ids/entities/
distances, and the final answer text + sources. Used to produce
baseline_before.json (pre-change) and baseline_after.json (post-change) so
IMPLEMENTATION_LOG.md can diff behavior per question.

Usage:
    PYTHONPATH=<repo root> python scripts/rag_baseline_probe.py <output.json>
"""
import json
import os
import sys
import time

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connections  # noqa: E402

from apps.chatbot import rag  # noqa: E402

QUESTIONS = [
    {"id": "single_entity_lookup", "q": "How did Kendrick Lamar perform in 2024?"},
    {"id": "multi_year_trend", "q": "How has Kendrick Lamar's streaming changed from 2020 to 2024?"},
    {"id": "comparison_2_countries", "q": "Compare India and Brazil's streaming performance."},
    {"id": "comparison_2_artists", "q": "Compare Kendrick Lamar and Drake's streaming performance."},
    {"id": "superlative_country", "q": "Which country had the strongest streaming numbers?"},
    {"id": "superlative_artist_growth", "q": "Which artist grew the most in 2023?"},
    {"id": "count_labels", "q": "How many labels are there in total?"},
    {"id": "count_countries", "q": "How many countries are covered in the data?"},
    {"id": "country_specific", "q": "How is India performing in music streaming?"},
    {"id": "label_specific", "q": "Tell me about Columbia Records."},
    {"id": "missing_field_probe", "q": "What is the active_songs count for Brazil?"},
    {"id": "out_of_scope", "q": "What is the weather like today?"},
    {"id": "nonexistent_entity", "q": "How is the label Jordan performing?"},
    {"id": "ambiguous_entity", "q": "How is Georgia doing?"},
    {"id": "lowercase_entity", "q": "how is india doing"},
]


def probe_one(question):
    t0 = time.time()
    embedding = rag.embed_query(question)
    t1 = time.time()
    source_table = rag.classify_query(question)
    t2 = time.time()
    chunks = rag.retrieve_chunks(embedding, source_table=source_table, top_k=5)
    t3 = time.time()

    # distances for the same top-5, recomputed directly for the record
    with connections['gold'].cursor() as cur:
        if source_table:
            cur.execute(
                "SELECT source_key, embedding <-> %s::vector AS dist "
                "FROM gold_chunks WHERE source_table=%s ORDER BY dist LIMIT 5",
                [embedding, source_table],
            )
        else:
            cur.execute(
                "SELECT source_key, embedding <-> %s::vector AS dist "
                "FROM gold_chunks ORDER BY dist LIMIT 5",
                [embedding],
            )
        distances = [{"source_key": r[0], "distance": float(r[1])} for r in cur.fetchall()]

    reply = rag.get_rag_reply(question)
    t4 = time.time()

    return {
        "question": question,
        "routed_table": source_table,
        "top5_chunks": [
            {"source_table": c["source_table"], "source_key": c["source_key"]}
            for c in chunks
        ],
        "top5_distances": distances,
        "final_answer": reply["reply"],
        "sources": reply["sources"],
        "timing_s": {
            "embed": round(t1 - t0, 3),
            "classify": round(t2 - t1, 3),
            "retrieve": round(t3 - t2, 3),
            "full_get_rag_reply": round(t4 - t3, 3),
        },
    }


def main(out_path):
    results = []
    for item in QUESTIONS:
        print(f"probing [{item['id']}]: {item['q']}", file=sys.stderr)
        r = probe_one(item["q"])
        r["id"] = item["id"]
        results.append(r)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"wrote {len(results)} results to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "baseline.json")
