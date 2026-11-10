# Scripts

| Script | Purpose |
|---|---|
| `run_tests.sh [pattern]` | Backend tests, one process per file (`scripts/run_tests.sh`, or `scripts/run_tests.sh event_pricing`). |
| `deploy_frontend.sh <bucket> [dist]` | Upload `frontend/dist` to the frontend bucket with cache headers (used by the deploy workflows). |
| `package_lambdas.sh` | Optional: zip each Lambda directory into `lambdas/dist/`. Terraform builds its own archives, so this is only for manual inspection. |
