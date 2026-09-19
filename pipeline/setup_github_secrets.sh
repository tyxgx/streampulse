#!/usr/bin/env bash
# One-time setup for .github/workflows/daily-refresh.yml. Run it yourself:
#   ! bash pipeline/setup_github_secrets.sh
# Creates a lake-bucket-only IAM user, then stores its key + the Kaggle key as
# GitHub Actions secrets. Secrets are piped straight to `gh`, never printed.
set -euo pipefail

REPO=tyxgx/streampulse
BUCKET=streampulse-lake-dev-data
REGION=ap-south-1
IAM_USER=streampulse-pipeline

aws iam create-user --user-name "$IAM_USER" >/dev/null 2>&1 || echo "IAM user already exists, reusing"
aws iam put-user-policy --user-name "$IAM_USER" --policy-name lake-bucket-only --policy-document "{
  \"Version\":\"2012-10-17\",
  \"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":[\"s3:ListBucket\",\"s3:GetBucketLocation\"],\"Resource\":\"arn:aws:s3:::$BUCKET\"},
    {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:PutObject\",\"s3:DeleteObject\"],\"Resource\":\"arn:aws:s3:::$BUCKET/*\"}
  ]}"

# Rotate: an IAM user can hold at most 2 keys, so drop old ones first.
for k in $(aws iam list-access-keys --user-name "$IAM_USER" --query 'AccessKeyMetadata[].AccessKeyId' --output text); do
  aws iam delete-access-key --user-name "$IAM_USER" --access-key-id "$k"
done
KEY_JSON=$(aws iam create-access-key --user-name "$IAM_USER" --output json)
echo "$KEY_JSON" | python3 -c "import json,sys;print(json.load(sys.stdin)['AccessKey']['AccessKeyId'],end='')" \
  | gh secret set AWS_ACCESS_KEY_ID --repo "$REPO"
echo "$KEY_JSON" | python3 -c "import json,sys;print(json.load(sys.stdin)['AccessKey']['SecretAccessKey'],end='')" \
  | gh secret set AWS_SECRET_ACCESS_KEY --repo "$REPO"

for p in username key; do
  name=$([ "$p" = username ] && echo KAGGLE_USERNAME || echo KAGGLE_KEY)
  aws ssm get-parameter --region "$REGION" --name "/streampulse-lake/dev/kaggle/$p" --with-decryption \
    --query Parameter.Value --output text | tr -d '\n' | gh secret set "$name" --repo "$REPO"
done

gh variable set LAKE_BUCKET --body "$BUCKET" --repo "$REPO"
gh variable set AWS_REGION --body "$REGION" --repo "$REPO"
echo "Done. Secrets:"; gh secret list --repo "$REPO"
