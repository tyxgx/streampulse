#!/usr/bin/env bash
# Copies your LLM keys from the project's .env into AWS SSM (SecureString).
# Run it yourself:  bash chatbot/set_secrets.sh      (keys are never printed)
set -euo pipefail
cd "$(dirname "$0")/.."
get() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | tr -d "\"'" ; }
for pair in "GROQ_API_KEY:groq_api_key" "GEMINI_API_KEY:gemini_api_key"; do
  env="${pair%%:*}"; name="${pair##*:}"; val="$(get "$env")"
  if [ -z "$val" ]; then echo "skip $env (empty in .env)"; continue; fi
  aws ssm put-parameter --region ap-south-1 --name "/streampulse/$name" --type SecureString --overwrite --value "$val" >/dev/null
  echo "stored /streampulse/$name"
done
