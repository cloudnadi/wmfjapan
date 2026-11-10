#!/usr/bin/env bash
# Configure GitHub Actions variables and deployment environment secrets.
# Run from the repository root: ./scripts/configure_github_actions.sh
# % ./scripts/configure_github_actions.sh  
# AWS region [ap-northeast-1]: 
# Terraform state key prefix [wmfjapan]: 
# Terraform state S3 bucket: cloudnadi-terraform-state
# Preview tfvars file [backend/terraform.preview.tfvars]: 
# Production tfvars file [backend/terraform.production.tfvars]: 
# Configuring repository variables for cloudnadi/wmfjapan...
# ✓ Updated variable AWS_REGION for cloudnadi/wmfjapan
# ✓ Updated variable TF_STATE_KEY_PREFIX for cloudnadi/wmfjapan
# AWS OIDC role ARN (recommended; leave empty to use access keys): 
# AWS access key ID: AKIAVHYANKFUCYDIQNPN
# AWS secret access key: 
# Configuring preview environment secrets...
# ✓ Set Actions secret TF_STATE_BUCKET for cloudnadi/wmfjapan
# ✓ Set Actions secret AWS_ACCESS_KEY_ID for cloudnadi/wmfjapan
# ✓ Set Actions secret AWS_SECRET_ACCESS_KEY for cloudnadi/wmfjapan
# Configuring production environment secrets...
# ✓ Set Actions secret TF_STATE_BUCKET for cloudnadi/wmfjapan
# ✓ Set Actions secret AWS_ACCESS_KEY_ID for cloudnadi/wmfjapan
# ✓ Set Actions secret AWS_SECRET_ACCESS_KEY for cloudnadi/wmfjapan
# ✓ Set Actions secret TFVARS_PREVIEW for cloudnadi/wmfjapan
# GitHub Actions configuration completed for cloudnadi/wmfjapan.
# Review the preview and production environments before running a deployment.
# % 

set -euo pipefail

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) is required: https://cli.github.com/"
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "Authenticate with GitHub first: gh auth login"
  exit 1
fi

repo="${GH_REPO:-$(gh repo view --json nameWithOwner --jq '.nameWithOwner')}"
if [[ -z "$repo" ]]; then
  echo "Unable to determine the GitHub repository. Set GH_REPO=OWNER/REPOSITORY."
  exit 1
fi

read -r -p "AWS region [ap-northeast-1]: " aws_region
aws_region="${aws_region:-ap-northeast-1}"

read -r -p "Terraform state key prefix [wmfjapan]: " state_key_prefix
state_key_prefix="${state_key_prefix:-wmfjapan}"

read -r -p "Terraform state S3 bucket: " state_bucket
if [[ -z "$state_bucket" ]]; then
  echo "Terraform state bucket is required."
  exit 1
fi

read -r -p "Preview tfvars file [backend/terraform.preview.tfvars]: " preview_tfvars
preview_tfvars="${preview_tfvars:-backend/terraform.preview.tfvars}"
read -r -p "Production tfvars file [backend/terraform.production.tfvars]: " production_tfvars
production_tfvars="${production_tfvars:-backend/terraform.production.tfvars}"

for tfvars_file in "$preview_tfvars" "$production_tfvars"; do
  if [[ ! -f "$tfvars_file" ]]; then
    echo "Terraform variables file not found: $tfvars_file"
    exit 1
  fi
done

echo "Configuring repository variables for $repo..."
gh variable set AWS_REGION --repo "$repo" --body "$aws_region"
gh variable set TF_STATE_KEY_PREFIX --repo "$repo" --body "$state_key_prefix"

read -r -p "AWS OIDC role ARN (recommended; leave empty to use access keys): " aws_role
if [[ -n "$aws_role" ]]; then
  aws_access_key_id=""
  aws_secret_access_key=""
else
  read -r -p "AWS access key ID: " aws_access_key_id
  read -r -s -p "AWS secret access key: " aws_secret_access_key
  echo
  if [[ -z "$aws_access_key_id" || -z "$aws_secret_access_key" ]]; then
    echo "Both AWS access key values are required when OIDC is not used."
    exit 1
  fi
fi

set_environment_secret() {
  local environment="$1"
  local name="$2"
  local value="$3"

  printf '%s' "$value" | gh secret set "$name" --repo "$repo" --env "$environment"
}

set_file_secret() {
  local environment="$1"
  local name="$2"
  local file="$3"

  gh secret set "$name" --repo "$repo" --env "$environment" < "$file"
}

for environment in preview production; do
  gh api --method PUT "repos/$repo/environments/$environment" --silent >/dev/null
  echo "Configuring $environment environment secrets..."
  set_environment_secret "$environment" TF_STATE_BUCKET "$state_bucket"

  if [[ -n "$aws_role" ]]; then
    set_environment_secret "$environment" AWS_ROLE_TO_ASSUME "$aws_role"
  else
    set_environment_secret "$environment" AWS_ACCESS_KEY_ID "$aws_access_key_id"
    set_environment_secret "$environment" AWS_SECRET_ACCESS_KEY "$aws_secret_access_key"
  fi
done

set_file_secret preview TFVARS_PREVIEW "$preview_tfvars"
set_file_secret production TFVARS_PRODUCTION "$production_tfvars"

echo "GitHub Actions configuration completed for $repo."
echo "Review the preview and production environments before running a deployment."