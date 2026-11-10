#!/usr/bin/env bash
# Upload frontend/dist to the frontend S3 bucket with browser cache headers.
#   scripts/deploy_frontend.sh <bucket> [dist-dir]
#
# - assets/*  : content-hashed filenames, so cache for a year (immutable)
# - *.html    : always revalidate, so a new deploy is picked up straight away
# - the rest (logo, favicon, robots…): one day
# Uploaded in that order so index.html never points at assets that aren't there yet.
set -euo pipefail

bucket="${1:?usage: deploy_frontend.sh <bucket> [dist-dir]}"
dist="${2:-frontend/dist}"

aws s3 sync "$dist/assets" "s3://$bucket/assets" --delete \
  --cache-control "public, max-age=31536000, immutable"

aws s3 sync "$dist" "s3://$bucket" --delete \
  --exclude "assets/*" --exclude "*.html" \
  --cache-control "public, max-age=86400"

aws s3 sync "$dist" "s3://$bucket" --delete \
  --exclude "*" --include "*.html" \
  --cache-control "no-cache"
