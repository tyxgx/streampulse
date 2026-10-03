# Security

## Reporting
Please report security problems privately by email to uttkarsh25tyagi@gmail.com instead of opening a public issue.

## What this project does to stay safe
- No long-lived AWS keys: CI uses GitHub OIDC to assume a role limited to this project's buckets, one ECR repository and one Lambda.
- Secrets (Groq and Gemini keys) live in AWS SSM Parameter Store as SecureString and are read by the Lambda at runtime.
  They are never in Git, Terraform state, logs or the container image.
- GitHub secret scanning with push protection is enabled; the full history was scanned and contains no credentials.
- The public chat endpoint is rate limited per IP and per day (DynamoDB), refuses prompt-injection and secret requests before
  any model call, and its CORS is restricted to the site origin.
- A gross-usage AWS budget alerts by email.

## Known limits
The chat endpoint is public by design (a portfolio demo), so its protection is application-level limits rather than authentication.
