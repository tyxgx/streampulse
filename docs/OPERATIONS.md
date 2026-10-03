# Operations runbook

Everything needed to deploy, run, monitor, fix and tear down StreamPulse. Region is `ap-south-1`, account `922120357133`.

## 1. What runs on its own

| What | When | Where | Result |
|---|---|---|---|
| `daily-refresh` | 03:30 UTC daily | GitHub Actions | Bronze append, Silver and Gold rebuild, site JSON, chat Parquet and dashboard files published to S3 (about 15 to 30 minutes; the first Bronze bootstrap took 28) |
| `chatbot-image` | on push to `main` touching `chatbot/**`, or manual | GitHub Actions, ARM runner | builds the Lambda image, pushes to ECR, rolls the function forward (about 1 minute) |
| Chat Lambda | on request | AWS | answers questions; reloads data when `chat/_version.json` changes (checked every 15 min) |

Nothing else is scheduled. There is no server to patch.

## 2. First-time setup (from scratch)

Needs: AWS CLI configured for the account, Terraform 1.6+, GitHub CLI, and the repo's Kaggle credentials in GitHub secrets
(`KAGGLE_USERNAME`, `KAGGLE_KEY`).

```bash
cd infra/terraform
terraform init
terraform apply -var deploy_chat=false # buckets, OIDC role, budget, ECR, DynamoDB, Lambda role (the Lambda needs an image first)

bash chatbot/set_secrets.sh           # copies GROQ_API_KEY / GEMINI_API_KEY from .env into SSM SecureString

gh workflow run daily-refresh.yml     # first run on an empty Bronze loads the full history from Kaggle (Bronze, Silver, Gold)
gh workflow run chatbot-image.yml     # builds and pushes the image

terraform apply                       # Lambda + Function URL (deploy_chat now defaults to true); prints chat_url
# put chat_url in dashboard/config.js as window.SP_API, commit, push (the workflow publishes it)
```

The Lambda is created in a second step because it cannot exist before an image is in ECR.

## 3. Everyday tasks

| Task | How |
|---|---|
| Re-run the refresh now | `gh workflow run daily-refresh.yml --repo tyxgx/streampulse`. If Kaggle has nothing new the run ends `up_to_date` and skips the S3 push and the data build (the dashboard files are still synced) |
| Replay Silver and Gold from Bronze (after changing a cleaning rule) | Actions, *daily-refresh*, Run workflow, tick **rebuild_silver**; or locally `python pipeline/refresh.py --csv <any csv> --lake <lake with all of bronze/> --rebuild-silver`. Pulls all of Bronze, rebuilds every month, pushes Silver and Gold |
| Inspect a corrected row | read `bronze/charts/` with `hive_partitioning=1` and look at the same (date, country, uri) across `_ingest_date`; Bronze keeps every version, Silver only the latest |
| Deploy a dashboard change | commit and push to `main`; the next daily run publishes it. For an immediate update also run `aws s3 sync dashboard s3://streampulse-site-922120357133 --exclude "data/*"`. **Always commit first** (see Troubleshooting) |
| Deploy a chatbot change | push to `main`; `chatbot-image` rebuilds and updates the Lambda |
| Change a rate limit | edit `IP_PER_HOUR` / `GLOBAL_PER_DAY` in `infra/terraform/chatbot.tf`, `terraform apply` |
| Rotate an LLM key | update `.env`, run `bash chatbot/set_secrets.sh`; warm Lambdas pick it up on their next cold start (force one by updating the function code or config) |
| Run the evaluation | `chatbot/.venv/bin/python chatbot/eval/run_eval.py --data /tmp/lk/chat_data`; regenerate `golden.json` with `make_golden.py` after the data moves on |
| Local preview of site plus chat | `chatbot/.venv/bin/python chatbot/serve_local.py` then open `http://localhost:8787` |
| Rebuild local data | see "Run it locally" in the README |

## 4. Monitoring and where to look

- **Pipeline:** GitHub Actions, `daily-refresh`. Each run writes the refresh summary JSON to the job summary. `state/last_run.json` in the lake
  holds the watermark. The dashboard's **Data health** page shows freshness and quality checks from the same run.
- **Chat:** CloudWatch log group `/aws/lambda/streampulse-chat` (14 days). One JSON line per request:
  `{"event":"ask","q":...,"tools":[...],"ok":[...],"verified":...,"blocked":...,"model":...,"ms":...,"tok_in":...,"tok_out":...}`.
  Useful CloudWatch Logs Insights queries: `filter event="ask" | stats count(), avg(ms), pct(ms,95) by bin(1h)`, and
  `filter event="ask" and verified=0` for unverified answers.
- **Limits:** DynamoDB table `streampulse-chat-limits` (keys `ip#<hash>#<yyyymmddhh>` and `day#<yyyymmdd>`, item expires automatically).
- **Cost:** AWS Budgets `streampulse-monthly-gross` ($3, gross, emails at 40 % forecast and 100 % actual). In Cost Explorer use
  `RECORD_TYPE != Credit` to see gross usage; the default view is net of credits and shows $0.
- **Alarms:** none configured yet (planned: Lambda errors and throttles, daily limit reached).

## 5. Troubleshooting (all of these actually happened)

| Symptom | Cause | Fix |
|---|---|---|
| Unit tests fail in CI with `INTERNAL Error: Failed to cast expression` | DuckDB 1.5.6 optimizer bug (`TopNWindowElimination`) | `duckdb==1.5.5` is pinned in `pipeline/requirements.txt`. Do not unpin until a fixed release is verified |
| `Could not assume role with OIDC: Not authorized to perform sts:AssumeRoleWithWebIdentity` | GitHub's "immutable subject" is on for the repo, so the token subject is `repo:owner@<id>/name@<id>:...` | The role trusts both forms (`github_oidc.tf`, variable `github_repo_immutable`). If the repo is renamed or recreated, update the ids: `gh api repos/<owner>/<repo>/actions/oidc/customization/sub` |
| A dashboard change disappears after a workflow run | the workflow syncs `dashboard/` from the repo to S3 and overwrote an uncommitted upload | commit and push first, then upload |
| A market shows "stale" or has no last-30-day data | the Kaggle source has no stream counts for it after a date (India: ranks continue but `streams` is blank from 2026-08-10; Belarus and Israel: no rows after 2026-03-24/25) | not a bug; it is flagged on **Data health** and in chat answers |
| Chat says "The assistant is busy right now" | every configured model was rate limited | wait a minute; check provider quotas; add a model to the chain in `llm.py` |
| Chat model calls return 404 | a provider retired a model id | list models (`client.models.list()`), update the chain in `llm.py`, push |
| First chat question takes ~10 s | Lambda cold start downloads 63 MB into `/tmp` | expected; a scheduled warm-up ping would hide it |
| Want a reserved-concurrency cap on the chat Lambda | the account limit is 10 and AWS keeps a minimum unreserved pool, so nothing can be reserved (not attempted) | limits are enforced in DynamoDB instead; ask AWS for a quota increase if a hard cap is needed |
| Browser blocks the chat call | origin not in the Function URL CORS list | add the origin in `chatbot.tf` (`cors.allow_origins`) and apply |
| `daily-refresh` OOM on the runner | too much data in memory | Bronze and Silver are built one year at a time; `--memory-limit 5GB` is set |
| Image build fails at ECR login | ECR repo or CI policy missing | run `terraform apply` (creates the repo and the `chat-image-deploy` policy), then re-run the workflow |

## 6. Recovery

- **Silver or Gold lost or wrong:** rebuild them from Bronze with the *rebuild_silver* run (no Kaggle needed). **Bronze lost:** it can only be restored from a copy of
  the bucket, or re-created from the current Kaggle file (which holds the current state of the history but not earlier versions of corrected rows). Consider enabling S3 versioning on
  `bronze/` if that history matters.
- **Bad deploy of the chat image:** re-run `chatbot-image` from an earlier commit (`workflow_dispatch` on that ref), or point the function at an
  older tag: `aws lambda update-function-code --function-name streampulse-chat --image-uri <repo>:<sha12>`. The last 5 images are kept.
- **Chat misbehaving or too costly:** disable the widget by emptying `window.SP_API` in `dashboard/config.js` (push, then sync), or throttle by lowering
  `GLOBAL_PER_DAY`. As a last resort delete the Function URL (`terraform apply -var deploy_chat=false` removes the Lambda and URL; never run a plain apply after that without setting it back).
- **Terraform state** is local (`infra/terraform/terraform.tfstate`, not in Git). Back it up; without it, resources must be imported.

## 7. Tear down

```bash
cd infra/terraform
aws s3 rm s3://streampulse-site-922120357133 --recursive
aws s3 rm s3://streampulse-lake-922120357133 --recursive
terraform destroy
aws ssm delete-parameter --name /streampulse/groq_api_key
aws ssm delete-parameter --name /streampulse/gemini_api_key
```

Buckets must be emptied first (Terraform will not delete non-empty buckets). The GitHub OIDC provider is shared with another project and is not
managed here; it stays.

## 8. Costs to watch

The one real cost incident in this account's history came from a different project (a DynamoDB table rewritten every minute), so the pattern to
avoid is "a per-minute job writing many items". StreamPulse's only recurring writes are one S3 sync a day and a DynamoDB counter increment per
chat question. Check Cost Explorer (gross, not net of credits) after the first full month and replace the estimates in the README with real numbers.
