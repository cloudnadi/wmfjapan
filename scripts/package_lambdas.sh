#!/usr/bin/env bash
# Zip every Lambda directory into lambdas/dist/ for manual inspection.
# Terraform builds its own archives (backend/event_platform_*.tf), including the shared modules, so this
# script is not part of a deploy. Run from the project root: scripts/package_lambdas.sh
set -e

LAMBDAS_DIR="lambdas"
DIST_DIR="lambdas/dist"
mkdir -p "$DIST_DIR"

for handler in "$LAMBDAS_DIR"/*/handler.py; do
  fn="$(basename "$(dirname "$handler")")"
  (cd "$LAMBDAS_DIR/$fn" && rm -f "../../$DIST_DIR/$fn.zip" && zip -q -r "../../$DIST_DIR/$fn.zip" . -x "*.pyc" -x "__pycache__/*")
  echo "  $DIST_DIR/$fn.zip"
done
