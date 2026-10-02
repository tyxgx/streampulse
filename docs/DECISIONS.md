# Decisions

Short records of why things are the way they are. Newest first.

## 2026-10 Bronze, Silver, Gold with an append-only Bronze, and Silver rebuilt from Bronze
**Context.** The first serverless version kept Silver and Gold but threw the raw CSV away after every run, so there was no raw layer and no way to replay
cleaning rules, and Gold was built but nothing read it. **Decision.** Persist Bronze (the Kaggle rows as parsed, immutable, with lineage columns), make Silver a pure
function of Bronze, and serve monthly views from Gold. **Why.** Kaggle only serves its latest file, so the raw history would otherwise be unrecoverable; with
Bronze any change to the cleaning rules can be applied to the whole history with `--rebuild-silver`. **How Bronze stays small.** Each run appends only rows that are new or
changed, found by comparing a hash of the whole row with what Bronze already holds over a 7-day lookback; an unchanged day appends nothing. **Costs and limits.**
About 1.3 GB more storage; corrections older than the lookback are not seen; the CSV is read with `ignore_errors=true`, so a malformed line is skipped before
it reaches Bronze (Bronze is "raw as parsed", not byte-for-byte); column types are inferred from a sample, as before. **Alternative rejected:** storing every column as text
(truest raw, but every later step then needs casts and the files are larger).

## 2026-10 Gold serves the monthly views, and a reconciliation check guards it
Gold was already being computed but nothing used it. The overview chart, per-country monthly series and label shares now come from Gold; daily and track-level views
stay on Silver. Before switching, Gold and Silver were compared: 117 months and 7,662 country-months differed by 0. The build now repeats that comparison on every run
and publishes it on the Data health page, so a bad Gold write cannot reach the dashboard unnoticed.

## 2026-10 Deterministic names in the serving layer
About 420 tracks carry more than one artist spelling over time. `any_value()` picked an arbitrary one, so artist counts changed between runs (59,576, then 59,572 on the same data).
The serving layer now takes the most recent spelling with a value tie-break; two builds on the same data are byte-identical, and a unit test covers it.

## 2026-10 India is shown by chart position, not hidden
From 2026-08-10 the source has India's 200 ranked rows every day with blank stream counts. Treating India as "stale" threw away a working chart. Rank data (60 days, with names) now feeds an India
section on the dashboard (latest top 10, days in the top 10) and a `chart_ranking` tool; any tool that finds no streams for a country in the window falls back to positions and says so. Positions are never
presented as streams. Stream totals for India stay frozen at 2026-08-09 because that is all the source has.

## 2026-10 Tools plus a verifier, not retrieval, for the assistant
**Context.** Questions are analytical; answers are numbers. **Decision.** Eleven deterministic tools, a tool-calling agent, and a verifier that rejects
any figure absent from tool output. **Why.** Similarity search cannot guarantee the right number, and free-text SQL generation cannot guarantee a safe or
correct query. **Cost.** The assistant cannot answer anything the tools do not cover. **Alternative kept in the repo:** the old pgvector RAG
(`docs/LEGACY_DJANGO.md`).

## 2026-10 LangGraph for the agent loop, with my own thin LLM client
**Why LangGraph.** The flow has a real loop (plan, act, verify, retry) and explicit state; a graph makes the retry path visible and testable.
**Why not LangChain's model wrappers.** The providers expose OpenAI-compatible endpoints, so the plain `openai` SDK is smaller (the Lambda image is 220 MB)
and gives direct control over fallback and rate-limit handling.

## 2026-10 Limits in DynamoDB, not reserved concurrency
The account allows only 10 concurrent Lambda executions (shared with another project). AWS keeps a minimum pool of unreserved concurrency, so with a limit this low there is nothing left to reserve (I did not attempt the API call to confirm the exact error). Atomic counters with a conditional
update enforce 30 questions per IP per hour and 1,500 per day. The limiter fails open if DynamoDB errors, because availability matters more than a
missed count when the model providers are already capped.

## 2026-10 Precompute for the browser, query only for the assistant
The dashboard reads 8 MB of JSON built once a day. Visitors cause no compute, the site is a set of static files, and a page never waits for a query.
The cost is that only precomputed views exist (top 300 artists, top 500 tracks, 72 countries). The assistant covers the long tail through tools.

## 2026-10 S3 website hosting instead of CloudFront
CloudFront distribution creation was denied on this account (the same restriction was hit by another project). The site is HTTP only. Accepted for a
portfolio dashboard; revisit if CloudFront becomes available or a custom domain with another host is added.

## 2026-10 Ingest stays on GitHub Actions
Kaggle only serves the full ~10 GB CSV, which exceeds Lambda's 15 minute and 10 GB limits and would make a managed cluster pointless for a once-a-day job.
A free runner does it in about 7 minutes. The cost is a dependency on GitHub, mitigated by manual dispatch and a documented bootstrap path.

## 2026-10 OIDC for CI, no stored AWS keys
The workflow assumes a role with GitHub OIDC. The trust policy lists both the classic and the immutable subject formats (see Troubleshooting).

## 2026-10 DuckDB pinned to 1.5.5
Release 1.5.6 raised an internal optimizer error in the test suite. Pinned with a comment; revisit when a fixed version is verified.

## 2026-10 Image built in CI on an ARM runner
Building the container on the 8 GB development laptop is slow and risky; a native arm64 runner matches the Lambda architecture and builds in about a minute.

## 2026-10 Terraform with local state
One person, one account, a few dozen resources. A remote backend and lock table would add cost and setup for no benefit. The state file is backed up by hand.

## 2026-10 EC2 retired
The original app ran on one EC2 instance. A stopped instance still cost money (idle Elastic IP, EBS), the app needed a database server, and it was not
how the other project in this account is deployed. The serverless version costs less and has no host to maintain.
