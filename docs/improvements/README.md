# Improvements log (StreamPulse)

Every change made after the 2026-10-03 audit is written up here in the **STAR** format used in interviews, one file per step:

- **S**ituation: what was wrong or missing, with the measurement that shows it.
- **T**ask: what had to be true afterwards.
- **A**ction: what was done, including decisions and alternatives rejected.
- **R**esult: the measured outcome, how to verify it, how to roll it back, and what is still open.

The reference docs (architecture, chatbot, operations, decisions) are updated alongside; these files tell the story of *why* each change was made.

## Plan and status

Steps marked **apply** need a `terraform apply` run by the owner (the agent environment cannot apply infrastructure).

| # | Step | Kind | Status |
|---|---|---|---|
| 01 | [AWS cost and consumption audit](01-aws-cost-audit.md) | audit | done 2026-10-04 |
| 02 | [Plain "How it works" page](02-how-it-works-page.md): data source, processing, how it is shown | feature | done and live 2026-10-04 |
| 03 | Public "Quality" page: the 41-question evaluation, published | feature | planned |
| 04 | Bar-chart race: top artists month by month, and a map time slider | feature | planned |
| 05 | Pipeline history page: rows appended, duration, lineage per run | feature | planned |
| 06 | Collaboration network (artists who chart together) | feature | planned |
| 07 | Compare mode (two countries or two artists) | feature | planned |
| 08 | Chat: inline mini charts and streamed answers | feature | planned |
| 09 | CloudWatch alarms and a synthetic endpoint check | infra, **apply** | planned |

Rule for every step: change one thing, verify it for real (browser or test), write the result here, and keep a rollback.
