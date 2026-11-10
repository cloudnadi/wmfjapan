#!/usr/bin/env bash
# Package all Lambda functions into zip files for Terraform
# Run from project root: ./scripts/package_lambdas.sh

set -e

LAMBDAS_DIR="lambdas"
DIST_DIR="lambdas/dist"
SHARED_ENTRY_SLOTS_LOGIC="$PWD/$LAMBDAS_DIR/entry_slots_logic.py"

echo "📦 Packaging Lambda functions..."
mkdir -p "$DIST_DIR"

for handler in "$LAMBDAS_DIR"/*/handler.py; do
  fn="$(basename "$(dirname "$handler")")"
  echo "  → Zipping $fn..."
  (
    cd "$LAMBDAS_DIR/$fn"
    rm -f "../../$DIST_DIR/$fn.zip"
    zip -q -r "../../$DIST_DIR/$fn.zip" . -x "*.pyc" -x "__pycache__/*"
    if grep -q "import entry_slots_logic\|from entry_slots_logic" ./*.py 2>/dev/null; then
      zip -q -j "../../$DIST_DIR/$fn.zip" "$SHARED_ENTRY_SLOTS_LOGIC"
    fi
  )
  echo "    ✅ $DIST_DIR/$fn.zip"
done

echo ""
echo "✅ All Lambda packages ready in $DIST_DIR/"
echo ""
echo "Next: cd terraform && terraform init && terraform apply"
