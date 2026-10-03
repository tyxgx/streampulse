#!/usr/bin/env bash
# Copies the Terraform state (local only, gitignored) to two places: a timestamped file under ~/job/backups and a private S3 object.
# The state holds no secrets (checked), but the bucket is private and encrypted anyway. Run it after every `terraform apply`.
set -euo pipefail
cd "$(dirname "$0")/../infra/terraform"
TS=$(date -u +%Y%m%dT%H%M%SZ)
LOCAL="$HOME/job/backups/terraform-state/streampulse"
mkdir -p "$LOCAL"
cp terraform.tfstate "$LOCAL/terraform.tfstate.$TS"
aws s3 cp terraform.tfstate "s3://streampulse-lake-922120357133/_backups/terraform/$TS/terraform.tfstate" --sse AES256 --region ap-south-1 --only-show-errors
echo "backed up serial $(python3 -c "import json;print(json.load(open('terraform.tfstate'))['serial'])") to $LOCAL and s3://streampulse-lake-922120357133/_backups/terraform/$TS/"
