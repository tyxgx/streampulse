# Command queue (he runs them; ONE at a time; always through run.sh)

Format: `! bash ~/job/projects/streampulseOG/run_logs/run.sh <name> "<command>"`. Claude then reads `run_logs/INDEX.txt` + `run_logs/LATEST.txt`.
Status: [ ] todo, [x] done (add date + log file).

- [x] 1. **ozefe join coverage** DONE 2026-09-26 (log `2026-09-25_224752_join_ozefe_v2.log`, 4406 s; first attempt with one big remote query was cancelled after 463 s): 87.4% tracks / 91.7% chart rows / 90.4% streams. Result: `docs/results/join_ozefe.json`.
- [x] 1b-i. **ozefe dry-run** DONE 2026-09-29 (log `2026-09-29_160031_ozefe_dryrun.log`): 10 parquet files, **12.1 GB total**. Disk check same day: only **20 GB free** on `/` (was 47 GB on 09-24 after cleanup - 27 GB eaten since, cause not checked). Downloading all 12.1 GB would leave ~8 GB free. **Paused for his decision** - options: (a) download all 12.1 GB anyway, (b) re-check what ate the disk since 09-24 first, (c) download only enough files to cover the tracks we need (`null_response=0` filter, matched tracks already known per-file from the 09-26 per-file join log) instead of all 10.
- [ ] 1b-ii. **ozefe real download + local extract** (blocked on 1b-i decision): into `data/raw/ozefe/`, then extract our 218,787 matched tracks locally with DuckDB (minutes, not hours). Same for Gildas.
- [ ] 2. Kaggle `serkantysz` download (862 MB) + peek columns  (script to be written after 1)
- [ ] 3. serkantysz join test
- [ ] 4. Kaggle `devdope/900k-spotify` (4.1 GB) - disk check first
- [x] 5. **Gildas matched-rows extraction** DONE 2026-09-29 (log `2026-09-29_170721_extract_gildas_v2.log`, 835s): 166,369 rows, 18 numeric columns only (no track/artist/album name strings - already have those), 9 MB -> `data/processed/gildas_matched.parquet`. First attempt with all 27 columns ran 40+ min with no end in sight (same throttled HF xet-bridge CDN as ozefe, ~4.3 GB source file) and was killed; narrowing the column list fixed it.
- [ ] 6. Open-data checks (Last.fm 2020, Music4All-Onion, Last.fm Global Trends, trebi genres, jfreyberg graph, 2M-songs): metadata + join test each
- [ ] 7. Latest `charts_songs_daily.csv` (2017 -> today). Check size first; probably run in the background / GitHub Actions
- [ ] 8. `charts_artists_daily.csv` (3.0 GB) and `charts_albums_weekly.csv` (635 MB)
- [ ] 9. ListenBrainz / MusicBrainz genre tags (name matching, measure quality)

Done so far (by Claude, before this queue existed; not logged in run_logs):
- 2026-09-25 Kaggle 6 small files downloaded (182 MB) -> `data/raw/kaggle_gonzalopezgil/`
- 2026-09-25 join tests: Gildas 66.5%/81.5%/81.8% (tracks/rows/streams), maharshipandya 2.7%/21.3%/17.8%
