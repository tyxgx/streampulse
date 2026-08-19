"""
Aggregates country_performance / label_performance_enhanced / kpi_song /
artist_performance from monthly grain to yearly grain, generates one RAG
chunk per (entity, year), embeds with all-MiniLM-L6-v2, and loads into
gold_chunks.

kpi_artist is intentionally excluded — it has no metric columns (country x
artist x month presence only), so there is no fact to narrate; use
artist_performance instead (built from Silver, has real metrics — see
schema.sql). monthly_trends is intentionally excluded too — it's a
per-country-per-month aggregate used only for the dashboard's cross-country
trend chart, not per-entity narrative material (same rationale the original
dashboard_summary/monthly_trends exclusion used before this source's schema
changed).

Usage:
    python scripts/build_gold_chunks.py
"""
import os
import time

import pandas as pd
import psycopg2
from sentence_transformers import SentenceTransformer

PG_DSN = (
    f"host={os.environ.get('PGHOST', 'localhost')} "
    f"port={os.environ.get('PGPORT', 5432)} "
    f"dbname={os.environ.get('PGDATABASE', 'gold')} "
    f"user={os.environ.get('PGUSER', os.environ.get('USER'))} "
    f"password={os.environ.get('PGPASSWORD', '')}"
)


# --- Yearly aggregation -----------------------------------------------

def aggregate_country_performance(conn):
    df = pd.read_sql(
        "SELECT year, country_name, total_streams, active_songs, hit_songs, "
        "avg_chart_strength, active_artists, growth_percentage, "
        "top_song_name, top_artist_name FROM country_performance",
        conn,
    )
    agg = df.groupby(['country_name', 'year']).agg(
        total_streams=('total_streams', 'sum'),        # sum: additive volume
        active_songs=('active_songs', 'max'),            # max: broadest catalog seen
        hit_songs=('hit_songs', 'max'),                   # max: broadest hit-song count seen
        avg_chart_strength=('avg_chart_strength', 'mean'),  # mean: already an average metric
        active_artists=('active_artists', 'max'),
        growth_percentage=('growth_percentage', 'mean'),  # mean: a rate
        top_song_name=('top_song_name', 'last'),           # last: most recent month's leader
        top_artist_name=('top_artist_name', 'last'),
    ).reset_index()
    return agg


def aggregate_label_performance(conn):
    # Aggregated across countries too (not just months) -- the old
    # label_performance table this replaces had no country_name column, so
    # a yearly label chunk was always a global figure; label_performance_
    # enhanced adds country_name, so it must be summed out here to keep the
    # same global-per-label-per-year chunk semantics.
    df = pd.read_sql(
        "SELECT year, standardized_label, total_streams, active_songs, "
        "active_artists FROM label_performance_enhanced",
        conn,
    )
    agg = df.groupby(['standardized_label', 'year']).agg(
        total_streams=('total_streams', 'sum'),      # sum: additive volume
        active_songs=('active_songs', 'max'),
        active_artists=('active_artists', 'max'),
    ).reset_index()
    return agg


def aggregate_kpi_song(conn):
    # Summed across country and month -- kpi_song is country x track x
    # month grain; a yearly per-track chunk is the track's global total for
    # that year. LEFT JOINed with track_catalog (built from Silver) to cite
    # the real track name instead of the bare uri -- track_catalog doesn't
    # cover every kpi_song uri (different source pipelines), hence LEFT JOIN
    # and a uri fallback in song_chunk() below.
    df = pd.read_sql(
        "SELECT s.year, s.uri, s.standardized_label, s.total_streams, s.is_hit, "
        "t.track_name FROM kpi_song s LEFT JOIN track_catalog t ON t.uri = s.uri",
        conn,
    )
    agg = df.groupby(['uri', 'year']).agg(
        standardized_label=('standardized_label', 'first'),
        total_streams=('total_streams', 'sum'),
        is_hit=('is_hit', 'max'),   # hit in any country/month that year
        track_name=('track_name', 'first'),
    ).reset_index()
    return agg


def aggregate_artist_performance(conn):
    df = pd.read_sql(
        "SELECT year, artist_uri, artist_name, total_streams, track_count, "
        "hit_track_count, best_rank FROM artist_performance",
        conn,
    )
    agg = df.groupby(['artist_uri', 'year']).agg(
        artist_name=('artist_name', 'first'),
        total_streams=('total_streams', 'sum'),      # sum: additive volume across countries/months
        track_count=('track_count', 'sum'),            # sum: distinct-per-country-month, not globally deduped -- an approximation, not an exact global distinct-track count
        hit_track_count=('hit_track_count', 'sum'),
        best_rank=('best_rank', 'min'),                 # min: best (lowest) rank achieved anywhere that year
    ).reset_index()
    return agg


# --- Chunk text templates (yearly framing) -----------------------------

def country_chunk(row):
    return (
        f"In {row['country_name']} during {int(row['year'])}, Spotify "
        f"recorded {int(row['total_streams']):,} total streams, with up to "
        f"{int(row['active_songs'])} active song(s) (up to "
        f"{int(row['hit_songs'])} of them hits) and an average chart "
        f"strength of {row['avg_chart_strength']:.2f}. Up to "
        f"{int(row['active_artists'])} active artists were represented "
        f"that year, with average growth of {row['growth_percentage']:.2f}%. "
        f"Most recent top song was {row['top_song_name']}, top artist was "
        f"{row['top_artist_name']}."
    )


def label_chunk(row):
    return (
        f"Label {row['standardized_label']} in {int(row['year'])} had "
        f"{int(row['total_streams']):,} total streams across up to "
        f"{int(row['active_songs'])} active song(s) from up to "
        f"{int(row['active_artists'])} artist(s)."
    )


def song_chunk(row):
    hit_note = "was a hit" if row['is_hit'] else "was not a hit"
    label_note = f" on label {row['standardized_label']}" if row['standardized_label'] else ""
    # track_catalog doesn't cover every kpi_song uri, so fall back to the
    # raw uri when no name was found -- same behavior as before this table
    # existed, just no longer the common case.
    name = row['track_name'] if pd.notna(row['track_name']) else row['uri']
    return (
        f"Track \"{name}\"{label_note} had {int(row['total_streams']):,} "
        f"total streams in {int(row['year'])} and {hit_note} that year."
    )


def artist_chunk(row):
    return (
        f"In {int(row['year'])}, artist {row['artist_name']} "
        f"({row['artist_uri']}) had {int(row['total_streams']):,} total "
        f"streams across up to {int(row['track_count'])} charting track "
        f"appearance(s), including {int(row['hit_track_count'])} hit "
        f"track appearance(s), reaching a best chart rank of "
        f"{int(row['best_rank'])} that year."
    )


TABLES = [
    ('country_performance', aggregate_country_performance, country_chunk, 'country_name'),
    ('label_performance_enhanced', aggregate_label_performance, label_chunk, 'standardized_label'),
    ('kpi_song', aggregate_kpi_song, song_chunk, 'uri'),
    ('artist_performance', aggregate_artist_performance, artist_chunk, 'artist_uri'),
]


def main():
    conn = psycopg2.connect(PG_DSN)

    all_chunks = []  # (source_table, source_key, chunk_text)
    for table_name, agg_fn, chunk_fn, key_col in TABLES:
        df_monthly_count = pd.read_sql(f"SELECT count(*) AS n FROM {table_name}", conn)
        monthly_rows = int(df_monthly_count['n'][0])

        yearly = agg_fn(conn)
        yearly_rows = len(yearly)
        ratio = monthly_rows / yearly_rows if yearly_rows else 0
        print(f"{table_name}: monthly rows={monthly_rows:,} -> yearly rows="
              f"{yearly_rows:,} ({ratio:.1f}x reduction)")

        for _, row in yearly.iterrows():
            text = chunk_fn(row)
            key = f"{row[key_col]}|{int(row['year'])}"
            all_chunks.append((table_name, key, text))

    print(f"\nTotal chunks to embed: {len(all_chunks):,}")

    model = SentenceTransformer('all-MiniLM-L6-v2')
    texts = [c[2] for c in all_chunks]

    t0 = time.time()
    embeddings = model.encode(texts, show_progress_bar=True, batch_size=64)
    elapsed = time.time() - t0
    print(f"Embedded {len(texts):,} chunks in {elapsed:.1f}s "
          f"({elapsed/60:.1f} min)")

    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE gold_chunks RESTART IDENTITY")
    rows = [
        (src, key, text, emb.tolist())
        for (src, key, text), emb in zip(all_chunks, embeddings)
    ]
    cur.executemany(
        "INSERT INTO gold_chunks (source_table, source_key, chunk_text, embedding) "
        "VALUES (%s, %s, %s, %s)",
        rows,
    )
    conn.commit()

    cur.execute("SELECT count(*) FROM gold_chunks")
    final_count = cur.fetchone()[0]
    print(f"gold_chunks final row count: {final_count:,}")
    conn.close()


if __name__ == '__main__':
    main()
