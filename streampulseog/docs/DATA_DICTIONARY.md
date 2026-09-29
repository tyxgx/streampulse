# Data dictionary (what features we have)

Verified 2026-09-25 on the newest Silver (`aws-backup-2026-09-19/.../silver/song_charts`, 43.7M rows, 2017-01-01 -> 2026-09-17).
One row = one day x one market x one track (Spotify daily Top 200). Streams are always >= 1,001 (the chart cut-off).
Only 2 columns have nulls: `release_date` 1.5%. Everything else 0%.

## Silver `song_charts` (25 columns + partitions)
| Group | Columns |
|---|---|
| When / where | `date`, `market` (72 codes), `country_name`, `year`, `month`, `quarter` |
| Track / artist | `uri` (spotify:track:ID), `track_name`, `artist_names` (`|`-separated), `artist_uris`, `release_date` |
| Label | `label` (raw, 29,009 distinct), `standardized_label` (28,397) |
| Daily performance | `rank` (1-200), `streams` (min 1,001, median ~11,950, max ~13.6M), `previous_rank` |
| Chart history | `peak_rank`, `peak_date`, `days_on_chart`, `consecutive_days`, `entry_rank`, `entry_date`, `entry_status` (MOVED_DOWN 19.4M, MOVED_UP 17.3M, NO_CHANGE 4.3M, RE_ENTRY 2.3M, NEW_ENTRY 0.56M) |
| Derived (our ETL) | `hit_category` (Charting Track 21.1M, Popular Track 11.1M, Major Hit 9.2M, Global Hit 2.3M), `chart_strength_score` (1 -> ~1202; **formula not verified, read the ETL code before using it as a target**) |
| Bookkeeping | `ingest_date` |

Distinct: 250,248 tracks, ~96k artist-name strings, 72 markets.

## Gold tables (`.gold_local_new`, older snapshot; rows verified)
| Table | Rows | Columns |
|---|---|---|
| `kpi_song` | 2,463,747 | country_name, uri, standardized_label, total_streams, is_hit, month, year |
| `artist_performance` | 1,894,592 | country_name, artist_uri, artist_name, month, total_streams, track_count, hit_track_count, best_rank, year_month, year |
| `kpi_artist` | 1,894,035 | keys only (country_name, artist_uri, month, year) |
| `label_performance_enhanced` | 929,858 | month, country_name, standardized_label, total_streams, active_songs, active_artists, year |
| `country_performance` | 7,383 | month, country_name, total_streams, active_songs, hit_songs, avg_chart_strength, active_artists, monthly_total_streams, top_song_name, top_artist_name, growth_percentage, year |
| `monthly_trends` | 7,313 | month, country_name, total_streams, active_songs, active_labels, hit_songs, avg_chart_strength, active_artists, growth_percentage, year |
| `track_catalog` | 242,572 | uri, track_name |
| `gold_chunks` (RAG) | 560,359 | chunk_id, source_table, source_key, chunk_text, embedding |

## Extra Kaggle files (downloaded, `data/raw/kaggle_gonzalopezgil/`)
- `artists.csv`: artist_uri, artist_name, monthly_listeners, monthly_listeners_rank, monthly_listeners_peak_rank, monthly_listeners_peak_listeners
- `songs.csv`: track_uri, track_name, artist_names, artist_uris, label, release_date, all_uris
- `albums.csv`: album_uri, album_name, artist_names, artist_uris, label, release_date
- `links.csv`: spotify_uri, type, youtube_music_id, youtube_video_id, youtube_browse_id, youtube_channel_id, youtube_video_type, youtube_video_title, youtube_video_source, youtube_video_confidence
- `artwork.csv`: uri, type (song/album/artist), image_url, last_seen_date
- `artist_listeners_daily.csv`: artist_id, date, listeners (2026-06-08 -> 2026-09-25 only)

## What we do NOT have in our own data
Audio features, genre, lyrics, popularity score, ISRC. These come from external sources (DATA_SOURCES.md, section C).

## External audio features (Gildas, join-tested: 66.5% of tracks / 81.5% of chart rows)
track_id, artist_name, track_name, album_name, album_release_date, duration_ms, explicit, track_number, disc_number, track_popularity, album_popularity, track_vs_album_popularity, artist_popularity, artist_followers, album_vs_artist_popularity, tempo, key, mode, danceability, energy, loudness, speechiness, acousticness, instrumentalness, liveness, valence, energy_danceability_score.
One row per (track_id, artist): dedupe by track_id before joining. Missing for 33.5% of tracks and 45% of tracks last seen in 2026 -> models need a "no audio features" mask.
