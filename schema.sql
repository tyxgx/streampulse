-- Gold layer schema, generated from actual s3://spotify-lake-dev-data/gold/ contents
-- inspected on 2026-08-06.
--
-- Source bucket changed from s3://group-1-dbda/gold/ (5 tables:
-- artist_performance, country_performance, label_performance,
-- dashboard_summary, monthly_trends) to s3://spotify-lake-dev-data/gold/,
-- and artist_performance / dashboard_summary / label_performance were
-- deleted from that bucket, leaving 5 DIFFERENT tables: country_performance,
-- kpi_artist, kpi_song, label_performance_enhanced, monthly_trends.
--
-- kpi_artist and kpi_song do NOT join -- kpi_artist.artist_uri is a
-- spotify:artist:... URI, kpi_song.uri is a spotify:track:... URI, and
-- there is no artist<->track bridge in this data. kpi_artist carries no
-- metrics at all (country x artist x month presence only), so it cannot
-- reconstruct anything like the old artist_performance table.
--
-- artist_performance and track_catalog (below) are NOT part of the
-- original Gold source -- they're built by scripts/build_artist_gold.py
-- from the SILVER layer (s3://spotify-lake-dev-data/silver/song_charts/),
-- which has real per-(country, track, day) streams plus artist_names/
-- artist_uris (collab tracks are pipe '|'-delimited), something the Gold
-- layer's kpi_artist/kpi_song never had. Uploaded to
-- s3://spotify-lake-dev-data/gold/artist_performance/ and .../track_catalog/
-- as first-class Gold tables so they load through the exact same
-- load_gold_to_postgres.py pipeline as the other five.
--
-- `year` is only present as an S3 partition (year=YYYY/) for every table
-- below; `month` is a partition (month=M/) for kpi_artist/kpi_song only --
-- for the other three it is an ordinary column inside the parquet files.
-- The ETL script derives year (and, for kpi_artist/kpi_song, month) from
-- the object key and computes a zero-padded year_month ('YYYY-MM') column
-- at load time for consistent ordering/display.

CREATE EXTENSION IF NOT EXISTS vector;

-- Partitioned in S3 by year=YYYY/, month is a column. 10 partition-years.
CREATE TABLE country_performance (
    year                  INTEGER NOT NULL,   -- derived from S3 partition path
    month                 INTEGER NOT NULL,
    year_month            TEXT NOT NULL,
    country_name          TEXT NOT NULL,
    total_streams         BIGINT,
    active_songs          BIGINT,
    hit_songs             BIGINT,
    avg_chart_strength    DOUBLE PRECISION,
    active_artists        BIGINT,
    monthly_total_streams BIGINT,
    top_song_name         TEXT,
    top_artist_name       TEXT,
    growth_percentage     DOUBLE PRECISION,
    PRIMARY KEY (country_name, year_month)
);
CREATE INDEX idx_country_performance_year_month ON country_performance (year_month);

-- Partitioned in S3 by year=YYYY/month=M/. Pure country x artist x month
-- presence/dimension table -- no metric columns exist in the source.
CREATE TABLE kpi_artist (
    year          INTEGER NOT NULL,   -- derived from S3 partition path
    month         INTEGER NOT NULL,   -- derived from S3 partition path
    year_month    TEXT NOT NULL,
    country_name  TEXT NOT NULL,
    artist_uri    TEXT NOT NULL,
    PRIMARY KEY (country_name, artist_uri, year_month)
);
CREATE INDEX idx_kpi_artist_artist_uri ON kpi_artist (artist_uri);

-- Partitioned in S3 by year=YYYY/month=M/. Country x track x month.
CREATE TABLE kpi_song (
    year                INTEGER NOT NULL,   -- derived from S3 partition path
    month               INTEGER NOT NULL,   -- derived from S3 partition path
    year_month          TEXT NOT NULL,
    country_name        TEXT NOT NULL,
    uri                 TEXT NOT NULL,
    standardized_label  TEXT,
    total_streams       BIGINT,
    is_hit              INTEGER,
    PRIMARY KEY (country_name, uri, year_month)
);
CREATE INDEX idx_kpi_song_uri ON kpi_song (uri);

-- Partitioned in S3 by year=YYYY/, month is a column. Country x label x month.
-- Renamed from the old label_performance -- market_share/catalog_hit_rate
-- columns that table had do not exist in this source.
CREATE TABLE label_performance_enhanced (
    year                INTEGER NOT NULL,   -- derived from S3 partition path
    month               INTEGER NOT NULL,
    year_month          TEXT NOT NULL,
    country_name        TEXT NOT NULL,
    standardized_label  TEXT NOT NULL,
    total_streams       BIGINT,
    active_songs        BIGINT,
    active_artists      BIGINT,
    PRIMARY KEY (standardized_label, country_name, year_month)
);
CREATE INDEX idx_label_performance_enhanced_year_month ON label_performance_enhanced (year_month);

-- Partitioned in S3 by year=YYYY/, month is a column. Now country x month
-- grain (was a single global-aggregate row per month in the old source).
CREATE TABLE monthly_trends (
    year                INTEGER NOT NULL,   -- derived from S3 partition path
    month               INTEGER NOT NULL,
    year_month          TEXT NOT NULL,
    country_name        TEXT NOT NULL,
    total_streams       BIGINT,
    active_songs        BIGINT,
    active_labels       BIGINT,
    hit_songs           BIGINT,
    avg_chart_strength  DOUBLE PRECISION,
    active_artists      BIGINT,
    growth_percentage   DOUBLE PRECISION,
    PRIMARY KEY (country_name, year_month)
);
CREATE INDEX idx_monthly_trends_year_month ON monthly_trends (year_month);

-- Built from Silver (see note above), not the original Gold source.
-- Partitioned in S3 by year=YYYY/ only -- month is a real column, derived
-- from Silver's per-day `date` field during aggregation. A collab track's
-- streams are attributed in FULL to each credited artist (not split) --
-- see scripts/build_artist_gold.py's docstring for the reasoning.
CREATE TABLE artist_performance (
    year             INTEGER NOT NULL,   -- derived from S3 partition path
    month            INTEGER NOT NULL,
    year_month       TEXT NOT NULL,
    country_name     TEXT NOT NULL,
    artist_uri       TEXT NOT NULL,
    artist_name      TEXT,
    total_streams    BIGINT,
    track_count      BIGINT,
    hit_track_count  BIGINT,
    best_rank        INTEGER,
    PRIMARY KEY (country_name, artist_uri, year_month)
);
CREATE INDEX idx_artist_performance_artist_uri ON artist_performance (artist_uri);
CREATE INDEX idx_artist_performance_year_month ON artist_performance (year_month);

-- Built from Silver (see note above). Not partitioned in S3 -- one row per
-- unique track (uri), first-seen track_name wins. Lookup-only table, no
-- metrics -- used to enrich RAG chunk text with real track names instead
-- of citing the bare kpi_song.uri.
CREATE TABLE track_catalog (
    uri         TEXT PRIMARY KEY,
    track_name  TEXT
);

-- RAG chunk store. embedding dimension N pinned after Step 4's local
-- embedding-model test (all-MiniLM-L6-v2 -> 384-dim). Update N here if a
-- different model is chosen.
CREATE TABLE gold_chunks (
    chunk_id      SERIAL PRIMARY KEY,
    source_table  TEXT NOT NULL,   -- e.g. 'country_performance'
    source_key    TEXT NOT NULL,   -- e.g. country_name + '|' + year
    chunk_text    TEXT NOT NULL,
    embedding     vector(384),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_gold_chunks_source ON gold_chunks (source_table, source_key);
-- Full-text search side of hybrid retrieval (apps/chatbot/rag.py's
-- _search_chunks()) — combined with the vector <-> search via Reciprocal
-- Rank Fusion so an exact keyword hit (e.g. a country/artist name) isn't
-- solely at the mercy of embedding similarity. GENERATED ALWAYS AS ...
-- STORED backfills existing rows automatically on ALTER TABLE.
ALTER TABLE gold_chunks ADD COLUMN chunk_tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('english', chunk_text)) STORED;
CREATE INDEX idx_gold_chunks_tsv ON gold_chunks USING GIN (chunk_tsv);
-- ANN index. Opclass is vector_l2_ops, not vector_cosine_ops, because
-- apps/chatbot/rag.py's retrieve_chunks() queries with the `<->` (L2
-- distance) operator, and an ivfflat index is only used by the planner
-- when its opclass matches the operator in the query. `lists` should be
-- re-tuned (rows/1000, per pgvector's own guidance) once the real
-- post-rebuild chunk count is known -- this is a placeholder until
-- scripts/build_gold_chunks.py's new row count is measured.
CREATE INDEX idx_gold_chunks_embedding ON gold_chunks
  USING ivfflat (embedding vector_l2_ops) WITH (lists = 100);
