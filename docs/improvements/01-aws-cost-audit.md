# 01. AWS cost and consumption audit (2026-10-04)

## Situation
StreamPulse runs on its own slice of an AWS account that also hosts the liveflights project. A stopped EC2 left over from the first version had cost money,
five budgets existed, and the new serverless stack (S3, Lambda, DynamoDB, ECR) had never been checked against its free tier. The account-wide analysis, with
every service and the Lambda numbers, is in the
[liveflights audit](https://github.com/tyxgx/liveflights/blob/main/docs/improvements/01-aws-cost-audit.md); this file covers StreamPulse's own part.

## Task
Know exactly what StreamPulse consumes and costs, gross (credits do not hide anything), and whether anything about it can surprise the owner.

## Action
Cost Explorer (gross, by service and usage type), CloudWatch metrics for the chat Lambda, S3 bucket sizes, and the Terraform state as the list of what exists.

## Result
| Part | Consumption | Gross per month | Verdict |
|---|---|---|---|
| S3, `streampulse-lake` (Bronze, Silver, Gold, chat tables) | about 2.8 GB, 305 objects, plus the daily sync requests | about $0.07 storage, requests are cents | fine |
| S3, `streampulse-site` (dashboard and JSON) | under 10 MB, 887 objects | cents | fine |
| Lambda `streampulse-chat` (1.5 GB, arm64) | 19 invocations in 7 days, 4.0 s average, 0 errors, 490 GB-s per month | $0 | **0.1% of the 400,000 GB-s account allowance**; chat traffic is not a cost risk |
| DynamoDB limiter (on-demand) | counters only, a few writes per question | $0, about $0.1 to $0.2 even if the daily cap of 1,500 questions is hit every day | fine |
| ECR (one 220 MB image, last 5 kept) | about 0.24 GB-month | about $0.03 | fine |
| SSM parameters (2, SecureString) | standard tier | $0 | fine |
| CloudWatch Logs (14-day retention) | tiny | $0 | fine |
| GitHub Actions (public repository) | daily refresh about 12 to 15 minutes, CI | $0 | fine |
| Groq and Gemini | free tiers | $0 | the real throttle on chat throughput, not a bill |

Total StreamPulse gross cost: **under $0.30 per month**, almost all of it S3 and ECR storage.

Things worth knowing
- The only way StreamPulse could become expensive is a bug that loops writes (the earlier DynamoDB incident on the other project was exactly that). Nothing here
  runs per minute; the recurring writes are one S3 sync a day and one DynamoDB counter increment per chat question.
- The account's Lambda concurrency is 10, shared with liveflights. The liveflights API Lambda was throttled 202 times in a week, so a burst of chat traffic could
  compete with it. Per-IP and daily limits on the chat endpoint keep that small.
- `streampulse-monthly-gross` is $3 but is account-wide, so it also counts liveflights. Its forecast ($8.29 on 2026-10-04) still includes the EC2 days that no longer
  exist. $5 would match the real run-rate (about $3 for the whole account). `streampulse-ec2-safety-budget` and `Monthly-Budget` are obsolete.

## What this led to
No change to StreamPulse's infrastructure was needed. The budget tidy-up is a small Terraform and CLI change left to the owner. The measurements are the baseline
for step 08 (alarms), which will watch exactly these numbers.
