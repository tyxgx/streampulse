terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }

  # Local state on purpose (same reasoning as liveflights): a one-person demo
  # project; an S3 backend + lock table would add cost and setup for no benefit.
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      project = var.project
    }
  }
}

data "aws_caller_identity" "current" {}
