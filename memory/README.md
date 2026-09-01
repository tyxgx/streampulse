# Session Memory

Date-stamped logs of what a Claude Code session actually did on this repo — separate from the
`RAG_*.md`/`STREAMPULSE_FULL_FLOW.md` docs at the repo root (which document the *shipped*
architecture/engineering story). This folder is a working log: what was found, what was fixed,
what's still open, and exactly what state the repo/EC2 deployment was left in.

## Convention

- One file per session/date: `YYYY-MM-DD.md`
- Write what changed, why, and — critically — **what's committed vs. pushed vs. actually
  deployed to the live EC2 instance**, since this project has a real live-vs-local drift risk
  (`.env` on the server is separate from `.env.example` in git, and code changes need a manual
  `git pull` + service restart on the EC2 box to take effect)
- Don't edit past entries; append a new file for a new session

See also the broader daily job-search log at `~/job/memory/YYYY-MM-DD.md` and the demo-reliability
tracking file at
`~/.claude/projects/-Users-uttkarshtyagi-job/memory/project_demo_reliability_gap.md`.

## Index

- [2026-09-01](2026-09-01.md) — 3 real bugs in the RAG chatbot found and fixed (deprecated Groq
  model, a Gemini 5xx not falling through to Groq, silent exception-swallowing hiding both) plus
  a cold-start latency fix. Committed and pushed to GitHub; **not yet deployed to the live EC2
  instance** — needs a fresh AWS Academy Lab session to reach it, and its `.env` needs the same
  `GROQ_MODEL` update the local `.env` got (see the day's entry for the exact value).
