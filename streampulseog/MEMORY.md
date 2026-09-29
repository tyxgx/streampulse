# streampulseOG - project memory

Read this first every session in this folder. Keep it updated in place. Last updated 2026-09-25.

## What this is
A fresh, data-first workspace for a bigger StreamPulse (Spotify charts). Goal: collect the richest legal-enough data first, then decide what to build with the new stacks (PyTorch, NLP/RAG upgrades, MLOps: Great Expectations/Evidently/MLflow/ONNX/k8s). Nothing is built yet, no model chosen. Data first, project idea after.
Not the same as `~/job/projects/streamPulse` (his solo Django + RAG app, keep untouched) or the CDAC capstone (`~/spotify/Group-1-...`, 8-person team). See global memory `cdac_group_project_integrity` before claiming anything from the capstone.

## Folder map
- `MEMORY.md` this file | `docs/` (index: docs/README.md - DATA_SOURCES, DATA_DICTIONARY, COMMANDS, STACKS_AND_ROLES, PROJECT_IDEAS, SESSION_LOG_2026-09-25, chat snapshot, results/) | `run_logs/` (terminal logs, see its README) | `scripts/` (join_test.py + scratch_2026-09-25/) | `data/raw/` (downloads) | `data/processed/` (derived, e.g. our_tracks.parquet) | `data/tmp/`
- Session name: "spotify update" (chat saved at `~/job/Claude Chats/2026-09-25/2202 - spotify update.md`). Full story of that session: `docs/SESSION_LOG_2026-09-25.md`.
- `data/` is local only, never commit it. Only code + docs + derived metrics may go to git.

## How we work here (his rules)
- **He runs terminal commands himself. Claude does NOT run commands/Bash here.** Claude writes files/scripts and hands him ONE command at a time (no lists), wrapped in `run_logs/run.sh`; then reads `run_logs/INDEX.txt` and `LATEST.txt`. (Slip on 2026-09-25: Claude ran downloads/queries itself earlier in the session before he set this rule.)
- Fish shell: give commands with the `!` prefix, full paths, no prompt symbol.
- Chrome only after his explicit yes. Hinglish, short answers, simple language.
- Honesty: say what is verified vs not. Never claim a data source is clean/legal without checking its card.

## Data status (details: docs/DATA_SOURCES.md, docs/DATA_DICTIONARY.md)
- Our spine: Silver 43.7M rows, 2017-01-01 -> 2026-09-17, 72 markets, 250,248 tracks (in `streamPulse/aws-backup-2026-09-19`).
- Kaggle `gonzalopezgil/spotify-charts-daily-updated` is updated daily (last 2026-09-25), so we are ~8 days behind. 6 small files downloaded to `data/raw/kaggle_gonzalopezgil/`. Big files (`charts_songs_daily.csv`, `charts_artists_daily.csv` 3.0 GB, `charts_albums_weekly.csv` 635 MB) not downloaded; download names end in `.csv`.
- **Gildas audio features (HF, CC-BY-NC): join-tested 66.5% of tracks / 81.5% of chart rows. He wants to USE it.** Provenance in the card is only "derived from Spotify" (unclear). Missing for 45% of tracks last seen in 2026.
- ozefe (256M rows): card says Anna's Archive Spotify scrape, licence spotify-developer-terms. **Join-tested 2026-09-26: 87.4% of our tracks / 91.7% chart rows / 90.4% streams (2026 tracks only 51%)** - better than Gildas (66.5/81.5/81.8). No artist popularity/followers in it (Gildas has them). Remote read took 73 min, so download locally once (COMMANDS 1b). Publishing decision is his; keep private.
- serkantysz (862 MB, lyrics+genre) and devdope (4.1 GB, lyrics+emotions): he wants both; join tests pending.
- Open data to check: Last.fm 2020, Music4All-Onion, Last.fm Global Trends, trebi genres, jfreyberg graph, 2M-songs, ListenBrainz/MusicBrainz.
- Spotify Web API audio-features are closed for new apps (since 2024-11-27). No scraping of Spotify.

## Decisions so far
- Data first. Use Gildas. Check ozefe, serkantysz, devdope, open GitHub/Zenodo datasets. Do everything in stages, coverage measured before building.
- Not decided: which project to build (options discussed: breakout/hit prediction with PyTorch vs LightGBM baseline; vibe/lyrics semantic search + reranker + RAGAS; MLOps layer on the daily pipeline; LangGraph router + MCP server).

## Next
2026-09-29 findings:
- 27 GB gap explained: it was liveflights GRU work (`globe_extract*`, `gru_v2*` folders, 25-28 Sep), not spotify. Not our problem to fix.
- **HuggingFace's xet-bridge CDN is heavily throttled** for both ozefe and Gildas (same CDN, ~100-400 KB/s even authenticated after `hf auth login`). A full `hf download` of ozefe (12.1 GB) was tried twice (once unauthenticated, once authenticated) and both were abandoned - would have taken tens of hours. **Don't try a raw `hf download` of either dataset again** - always go through DuckDB remote reads instead (below).
- **Fix that works:** remote DuckDB read selecting only the needed columns (not `SELECT *`) - this uses Parquet's columnar layout to pull far less data over the slow CDN. A first `SELECT *` extraction attempt for Gildas ran 40+ minutes with no end in sight and was killed; narrowing to 18 numeric columns (dropping track/artist/album name strings we already have) finished in 835s.
- **Gildas matched-rows extraction DONE**: `data/processed/gildas_matched.parquet`, 166,369 rows, 9 MB. Script: `scripts/extract_matched.py --source gildas`.
- Ozefe matched-rows extraction NOT done yet - same column-narrowing approach should be applied (`--source ozefe --per-file`, edit `SOURCES["ozefe"]["cols"]` in extract_matched.py the same way `GILDAS_COLS` was added) before attempting it, or it will hang the same way.
- This session ran very long on babysitting one download (checked progress ~15+ times over ~90 min). **Lesson for next time: launch background + Monitor once with a completion check, don't re-check manually every couple minutes** - he called this out as not worth the time given no project is even chosen yet.

Next: decide whether ozefe (legally riskier - Anna's Archive sourced) is worth extracting given Gildas alone already covers 66.5%/81.5%/81.8%. If yes, apply the same column-narrowing fix. Otherwise move to serkantysz/devdope/open datasets, or pause data collection and actually decide on a project.
