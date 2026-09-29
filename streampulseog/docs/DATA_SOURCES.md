# Data sources (streampulseOG)

Status legend: DONE = downloaded/verified here, TESTED = join-tested, TODO = planned, CHECK = needs a look first.
Last updated 2026-09-25. Numbers marked "verified" were measured in this repo's sessions; the rest are from dataset cards/search and NOT verified.

## A. Our own chart data (the spine)
| What | Where | Coverage | Status |
|---|---|---|---|
| Silver `song_charts` (newest) | `~/job/projects/streamPulse/aws-backup-2026-09-19/streampulse-lake-dev-data/silver/song_charts` | 43,729,987 rows, 2017-01-01 -> **2026-09-17**, 72 markets, 250,248 tracks (verified) | DONE |
| Silver (older) | `~/job/projects/streamPulse/.silver_local` | 42,182,468 rows, -> 2026-05-28 (verified) | DONE |
| Gold (7 tables) | `.gold_local_new` (old), `aws-backup.../gold` (new) | see DATA_DICTIONARY.md | DONE |
| `gold_chunks` (RAG) | `streamPulse/hf_space/demo_data/gold_chunks.parquet` | 560,359 chunks + embeddings | DONE |
| Postgres dump (only copy) | `streamPulse/deploy/dump_2026_09_22/gold_dump.dump` | 1.2 GB | DONE |

## B. Kaggle `gonzalopezgil/spotify-charts-daily-updated` (CC BY-SA 4.0, updated daily; last update 2026-09-25 15:52 UTC, v116, 14.8 GB total)
Downloaded to `data/raw/kaggle_gonzalopezgil/` (182 MB, verified):
| File | Rows (verified) | Notes |
|---|---|---|
| `songs.csv` | 214,408 | track_uri, name, artists, label, release_date |
| `artists.csv` | 73,676 | monthly_listeners only for 25,150 |
| `albums.csv` | 25,746 | |
| `links.csv` | 146,138 | 98,256 songs have a YouTube video id |
| `artwork.csv` | 306,784 | cover image URLs (254k song, 29k album, 23k artist) |
| `artist_listeners_daily.csv` | 2,004,831 | 27,411 artists, ONLY 2026-06-08 -> 2026-09-25 |

NOT downloaded yet (names are `.csv`, not `.csv.gz`, for the download API):
- `charts_songs_daily.csv` (size not checked; our Silver is built from it, ends 2026-09-17)
- `charts_artists_daily.csv` (3.0 GB): date,country,rank,uri,artist_name,peak_rank,previous_rank,days_on_chart,consecutive_days,entry_status,peak_date,entry_rank,entry_date
- `charts_albums_weekly.csv` (635 MB): date,country,rank,uri,album_name,artist_names,label,peak_rank,previous_rank,weeks_on_chart,...,release_date,artist_uris
Kaggle is ~8 days ahead of our Silver (2026-09-17). Kaggle's own latest chart date is NOT verified.

## C. External audio-feature / text datasets (join key = Spotify track id; ours = `uri` minus `spotify:track:`)
| Source | Size / rows | License | Provenance | Join test | Decision |
|---|---|---|---|---|---|
| HF `GildasLeDrogoff/spotify-huge-track-analysis-dataset` | 56,277,664 rows (track x artist) | CC-BY-NC-4.0 | card: "derived from Spotify", exact origin NOT stated | **TESTED: 66.5% of our tracks, 81.5% of chart rows, 81.8% of streams** (by last year: 51% in 2017 -> 84% in 2024, 75% 2025, **45% 2026**) | USE (he decided). Columns: tempo, key, mode, danceability, energy, loudness, speechiness, acousticness, instrumentalness, liveness, valence, duration_ms, explicit, track/album/artist popularity, artist_followers, album_release_date |
| HF `ozefe/spotify_audio_features` | ~256M rows, 10 parquet parts (part 0 = 25.6M) | "other" (spotify-developer-terms) | card says raw data = **Anna's Archive Spotify scrape** | **TESTED 2026-09-26 (only rows with null_response=0): 87.4% of our tracks (218,787), 91.7% of chart rows, 90.4% of streams**; by last year 90-95% for 2017-2025, **51% for 2026**. Remote read took 73 min (10 files x 390-570 s, latency-bound) - do NOT read it remotely again, download it once | Best coverage of the three. Publishing/portfolio use = his decision (provenance + Spotify terms), keep private. Columns: name, popularity, duration_ms, time_signature, key, mode, tempo + 8 audio features (NO artist name/popularity/followers - those are only in Gildas) |
| HF `maharshipandya/spotify-tracks-dataset` | 114,000 rows | bsd | Kaggle-origin | TESTED: 2.7% tracks / 21.3% chart rows / 17.8% streams | Too small; has 125 `track_genre` values (only for the matched ~6.8k) |
| Kaggle `serkantysz/550k-spotify-songs-audio-lyrics-and-genres` | 862 MB | CC BY-NC-SA 4.0 | rebuilt from Spotify IDs "using Spotify API and third-party tools" | TODO | audio + lyrics + 10 genres |
| Kaggle `devdope/900k-spotify` | 4.1 GB (~500k tracks; 900k intended) | CC BY-NC 4.0 | not checked | TODO | lyrics + emotion labels |
| Kaggle `krishsharma0413/2-million-songs-from-mpd-with-audio-features` | 409 MB, 2M tracks | not checked | not checked | TODO | audio features |

## D. Open music data (GitHub / Zenodo / others) - all CHECK, nothing verified yet
| Source | What (from search snippets, unverified) |
|---|---|
| `renesemela/lastfm-dataset-2020` (GitHub) | 122,877 tracks + 100 tags, SQLite (metadata + tags) |
| Music4All-Onion (Zenodo 6609677) | 109,269 tracks with audio/video/metadata features + 252,984,396 listening records of 119,140 Last.fm users |
| Last.fm Global Trends (Kaggle `tiagoadrianunes/last-fm-global-trends`) | weekly global top artists/tracks/tags + per-country, DuckDB/Parquet/CSV |
| `trebi/music-genres-dataset` (GitHub) | 1,494 genres x 200 songs |
| `sai-chaitanya-reddy/spotify-tracks-dataset` (GitHub) | 114k tracks, CC0 (probably the same 114k as maharshipandya) |
| Kaggle `jfreyberg/spotify-artist-feature-collaboration-network` | artist collaboration graph, 15 MB |
| ListenBrainz / MusicBrainz dumps | ~1B listens; CC0. Join via ISRC/MBID - our data has NO ISRC, so needs name matching |

## E. What does NOT work
- Spotify Web API `audio-features`, `audio-analysis`, `recommendations`, `related-artists`: closed to new apps since 2024-11-27 (403). Do not build on it.
- Spotify Million Playlist Dataset: no longer directly downloadable.
- Scraping Spotify directly: against Spotify's terms.

## Licence / provenance rules for this project
- Non-commercial licences (CC-BY-NC, CC-BY-NC-SA) are fine for a portfolio; attribute the dataset in the README.
- CC-BY-SA (our Kaggle data) needs share-alike on derived datasets.
- Keep raw external data OUT of any public git repo (only code + derived metrics/plots). Decide separately what the public demo shows.
- Anna's Archive-derived data: he decides whether to use; if used, keep it private and do not name it as a source on the CV.
