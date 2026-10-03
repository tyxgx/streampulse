# Archive: the original Django / pgvector version

These reports and baselines document the first version of StreamPulse (Django app, Postgres with pgvector, one EC2 box) and the
RAG engineering work done on it: audits, the implementation log, the regression baselines and the full deployment log.
They are kept as a record of how that version was built and debugged. Nothing here describes the current system; for that see
the [main README](../../README.md) and [ARCHITECTURE](../ARCHITECTURE.md). The overview of the old design is in
[LEGACY_DJANGO](../LEGACY_DJANGO.md).

| File | What it is |
|---|---|
| `STREAMPULSE_FULL_FLOW.md` | End-to-end deployment log of the EC2 version, with every incident and fix |
| `RAG_AUDIT.md`, `RAG_ENGINEERING_AUDIT.md` | Audits of retrieval, chunking and data quality |
| `RAG_ARCHITECTURE.md`, `RAG_ARCHITECTURE_REPORT.md`, `RAG_IMPLEMENTATION_ROADMAP.md` | Design, report and roadmap for the RAG pipeline |
| `IMPLEMENTATION_LOG.md`, `MARKDOWN_UI_PLAN_VALIDATION.md` | Log of the P0 fixes and the chat UI plan validation |
| `GOLD_LAYER_REPORT.md`, `GOLD_LAYER_LIVE_REPORT.md` | Reports on the Gold layer the first version read from |
| `baseline_*.json`, `ui_baseline_*.json` | Evidence for the 15-question regression harness (before and after each fix) |
