output "lake_bucket" { value = aws_s3_bucket.lake.bucket }
output "site_bucket" { value = aws_s3_bucket.site.bucket }
output "site_url" { value = "http://${aws_s3_bucket_website_configuration.site.website_endpoint}" }
output "pipeline_role_arn" { value = aws_iam_role.pipeline.arn }
