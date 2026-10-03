# Chatbot backend: ECR image -> Lambda (Function URL) + DynamoDB rate limiter.
# The chat Lambda needs an image in ECR first. On a brand-new account: `terraform apply -var deploy_chat=false`
# (ECR, role, table), let CI push the image, then plain `terraform apply`.
# deploy_chat defaults to TRUE because the Lambda is live: with a false default, any normal `terraform apply`
# would plan to DESTROY the Lambda and its public URL.

variable "deploy_chat" {
  description = "Create the Lambda + Function URL (needs an image in ECR first)."
  type        = bool
  default     = true
}

locals {
  site_origin = "http://${aws_s3_bucket_website_configuration.site.website_endpoint}"
  # the S3 REST endpoint serves the same site over HTTPS (the website endpoint is HTTP only)
  site_https_origin = "https://${aws_s3_bucket.site.bucket_regional_domain_name}"
}

resource "aws_ecr_repository" "chat" {
  name                 = "${var.project}-chat"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_ecr_lifecycle_policy" "chat" {
  repository = aws_ecr_repository.chat.name
  policy = jsonencode({ rules = [{
    rulePriority = 1, description = "keep last 5 images",
    selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 5 },
    action       = { type = "expire" }
  }] })
}

resource "aws_dynamodb_table" "limits" {
  name         = "${var.project}-chat-limits"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  attribute {
    name = "pk"
    type = "S"
  }
  ttl {
    attribute_name = "expires"
    enabled        = true
  }
}

resource "aws_cloudwatch_log_group" "chat" {
  name              = "/aws/lambda/${var.project}-chat"
  retention_in_days = 14
}

data "aws_iam_policy_document" "chat_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "chat" {
  name               = "${var.project}-chat-lambda"
  assume_role_policy = data.aws_iam_policy_document.chat_assume.json
}

data "aws_iam_policy_document" "chat" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.lake.arn}/chat/*"]
  }
  statement {
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.aws_region}:${local.account_id}:parameter/${var.project}/*"]
  }
  statement {
    actions   = ["dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.limits.arn]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.chat.arn}:*"]
  }
}

resource "aws_iam_role_policy" "chat" {
  name   = "chat-runtime"
  role   = aws_iam_role.chat.id
  policy = data.aws_iam_policy_document.chat.json
}

resource "aws_lambda_function" "chat" {
  count         = var.deploy_chat ? 1 : 0
  function_name = "${var.project}-chat"
  role          = aws_iam_role.chat.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.chat.repository_url}:latest"
  architectures = ["arm64"]
  memory_size   = 1536
  timeout       = 45
  ephemeral_storage { size = 1024 }

  environment {
    variables = {
      LAKE_BUCKET    = aws_s3_bucket.lake.bucket
      CHAT_PREFIX    = "chat/"
      SSM_PREFIX     = "/${var.project}"
      LIMITS_TABLE   = aws_dynamodb_table.limits.name
      IP_PER_HOUR    = "30"
      GLOBAL_PER_DAY = "1500"
    }
  }
  depends_on = [aws_cloudwatch_log_group.chat, aws_iam_role_policy.chat]
}

resource "aws_lambda_function_url" "chat" {
  count              = var.deploy_chat ? 1 : 0
  function_name      = aws_lambda_function.chat[0].function_name
  authorization_type = "NONE"
  cors {
    allow_origins = [local.site_origin, local.site_https_origin, "http://localhost:8787"]
    allow_methods = ["GET", "POST"]
    allow_headers = ["content-type"]
    max_age       = 3600
  }
}

resource "aws_lambda_permission" "chat_url" {
  count                  = var.deploy_chat ? 1 : 0
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.chat[0].function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "chat_invoke" {
  count         = var.deploy_chat ? 1 : 0
  statement_id  = "AllowPublicInvokeViaUrl"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.chat[0].function_name
  principal     = "*"
}

# CI (GitHub OIDC role) may push the image and roll the function forward.
data "aws_iam_policy_document" "ci_chat" {
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    actions = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
    "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = [aws_ecr_repository.chat.arn]
  }
  statement {
    actions   = ["lambda:UpdateFunctionCode", "lambda:GetFunction"]
    resources = ["arn:aws:lambda:${var.aws_region}:${local.account_id}:function:${var.project}-chat"]
  }
}

resource "aws_iam_role_policy" "ci_chat" {
  name   = "chat-image-deploy"
  role   = aws_iam_role.pipeline.id
  policy = data.aws_iam_policy_document.ci_chat.json
}

output "chat_url" {
  value = var.deploy_chat ? aws_lambda_function_url.chat[0].function_url : "not deployed (deploy_chat=false)"
}
output "ecr_repo" { value = aws_ecr_repository.chat.repository_url }
