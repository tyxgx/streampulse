variable "aws_region" {
  type    = string
  default = "ap-south-1"
}

variable "project" {
  type    = string
  default = "streampulse"
}

variable "github_repo" {
  description = "GitHub repo (owner/name) allowed to assume the pipeline role."
  type        = string
  default     = "tyxgx/streampulse"
}

variable "monthly_budget_usd" {
  description = "Monthly GROSS usage budget in USD (credits are NOT netted out)."
  type        = string
  default     = "3"
}

variable "alert_email" {
  type    = string
  default = "uttkarsh25tyagi@gmail.com"
}

variable "github_repo_immutable" {
  description = "Immutable-subject form of the repo (owner@ownerId/repo@repoId)."
  type        = string
  default     = "tyxgx@175643418/streampulse@1339322726"
}
