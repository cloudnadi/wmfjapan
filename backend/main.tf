terraform {
  required_version = ">= 1.3"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }

  # Remote state backend is configured externally via:
  # terraform init -backend-config=backend.<env>.hcl
  backend "s3" {
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = upper(var.project_name)
      Environment = upper(var.environment)
      ManagedBy   = "Terraform"
    }
  }
}

# Random suffix to ensure globally unique names
resource "random_id" "suffix" {
  byte_length = 4
}

locals {
  name_prefix = "${lower(var.project_name)}-${lower(var.environment)}"
  suffix      = random_id.suffix.hex
}
