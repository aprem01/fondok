#!/usr/bin/env bash
# Convenience runner for the Kimpton QA regression suite.
#
# Reads secrets from e2e/kimpton-qa/.env.local (gitignored via the repo's
# root .env.local pattern — never commit it) so they never have to be typed
# into a chat, a script, or this file. Create that file yourself with:
#
#   CLERK_PUBLISHABLE_KEY=pk_test_...
#   CLERK_SECRET_KEY=sk_test_...
#   FONDOK_SMOKE_EMAIL=...
#   FONDOK_SMOKE_PASSWORD=...
#   FONDOK_KIMPTON_DEAL_ID=...   # the DUPLICATE Kimpton Angler deal's id
#
# Usage: ./e2e/kimpton-qa/run.sh [playwright args...]
#   ./e2e/kimpton-qa/run.sh                      # run everything
#   ./e2e/kimpton-qa/run.sh --grep "Pass Criteria 1"
set -euo pipefail
cd "$(dirname "$0")/../.."  # -> apps/web

ENV_FILE="e2e/kimpton-qa/.env.local"
if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE. See the header of this script for what it needs." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

export FONDOK_E2E_KIMPTON=1
export PLAYWRIGHT_BASE_URL="${PLAYWRIGHT_BASE_URL:-https://fondok-app.vercel.app}"

exec pnpm exec playwright test --project=kimpton-qa "$@"
