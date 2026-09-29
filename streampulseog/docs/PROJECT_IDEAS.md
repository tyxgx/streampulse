# Project ideas for streampulseOG (PROPOSALS ONLY - nothing chosen, nothing built)

Rule from him: data first. Choose only after the data queue (docs/COMMANDS.md) and after the coverage numbers are known.
Data that would exist by then: 43.7M chart rows (2017 -> 2026-09-17, refreshable from Kaggle), audio features for ~87-90% of tracks (ozefe + Gildas), artist popularity/followers (Gildas), artist monthly listeners (Kaggle, only 2026-06-08 onwards), cover-art URLs, YouTube ids, lyrics/genres/emotions (serkantysz/devdope, join untested).

## 1. Breakout / hit prediction (DL + MLE)
- Task: from a track's first days on a chart (rank/streams trajectory) + audio features + artist popularity -> will it become a Major/Global Hit or reach Top N within K days.
- Stack: PyTorch (GRU/MLP) vs LightGBM vs a naive baseline, Optuna, SHAP, MLflow, ONNX export + latency numbers.
- Care: **time-based split** (train old years, test recent), no leakage (use only info available at prediction day), define the target independently of our own `hit_category`/`chart_strength_score` (their formulas are unverified), mask for missing audio features (2026 tracks only ~45-51% covered), report the baseline honestly even if the DL model does not win.

## 2. Vibe / lyrics semantic search + reranker (NLP + GenAI)
- Task: "shaant raat ka gaana" -> tracks, using lyrics/emotion/genre + audio features + chart context; extends StreamPulse RAG.
- Stack: Sentence-Transformers (fine-tune vs base, recall@k/MRR), cross-encoder reranker, hybrid BM25+vector, RAGAS on existing `baseline_*.json`, Langfuse, pgvector.
- Depends on: serkantysz/devdope coverage (untested).

## 3. MLOps layer on the daily pipeline (MLOps)
- Great Expectations checks in `pipeline/refresh.py`, Evidently drift on new daily charts, GitHub Actions retrain + registry (MLflow), ONNX serving, k8s manifests tested on kind.
- Cheap, high signal, closes the "GE/Evidently/k8s" gaps.

## 4. AI-engineer layer (AI Eng)
- LangGraph router (SQL vs RAG), MCP server exposing StreamPulse tools, Langfuse tracing, guardrails (never invent numbers).

## 5. Optional extras
- Cover-art features: image embeddings (CLIP) of album covers as a predictive/visual-similarity feature (sample only; images are copyrighted -> embeddings only, never redistribute).
- Artist collaboration graph (Kaggle `jfreyberg/...`) + graph features.
- Last.fm/ListenBrainz tags for genre where lyrics/genre datasets lack coverage.

## Licence / publication constraints (apply to all)
Non-commercial data (CC-BY-NC/-SA) is fine for a portfolio with attribution. ozefe = Anna's Archive-derived, Gildas source unstated: keep raw data out of public git, decide publicly-visible outputs separately, do not name questionable sources on the CV.
