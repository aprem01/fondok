#!/bin/bash
#
# push-and-alias — one command to ship: push to main, wait for Vercel to
# build the resulting deploy, then flip fondok-app.vercel.app onto it.
# Removes the "did you forget to alias?" tax every push otherwise pays.
#
# Usage:
#   ./scripts/deploy.sh           # pushes + waits + aliases
#   ./scripts/deploy.sh --alias-only   # skip push, alias whatever's latest
#
# Notes:
#   * Matches Vercel deployments by git commit SHA so we don't alias an
#     older Ready build if two are in flight.
#   * Fails loudly if the Vercel build errors out.
#   * Railway auto-redeploys on push regardless — this script only owns
#     the Vercel side.

set -euo pipefail

SCOPE=aprem01s-projects
CANONICAL=fondok-app.vercel.app
POLL_INTERVAL=15
MAX_WAIT_SECS=600

log() { printf "[deploy] %s\n" "$*"; }
die() { printf "[deploy] ERROR: %s\n" "$*" >&2; exit 1; }

ALIAS_ONLY=false
if [ "${1:-}" = "--alias-only" ]; then
  ALIAS_ONLY=true
fi

if [ "$ALIAS_ONLY" = false ]; then
  log "pushing to origin/main..."
  git push origin main
fi

SHA=$(git rev-parse HEAD)
SHORT=${SHA:0:7}
log "target commit: $SHORT ($SHA)"

log "polling Vercel for a Ready build of $SHORT (up to $((MAX_WAIT_SECS / 60)) min)..."

elapsed=0
target_url=""
while [ $elapsed -lt $MAX_WAIT_SECS ]; do
  # Vercel's --json output includes meta.githubCommitSha per deployment.
  match=$(vercel ls fondok --scope "$SCOPE" 2>/dev/null | \
          grep -oE "fondok-[a-z0-9]+-aprem01s-projects\.vercel\.app" | \
          head -20 | while read url; do
    # inspect each recent deploy for a matching commit sha via inspect
    inspect=$(vercel inspect "https://$url" --scope "$SCOPE" 2>&1 | head -50)
    if echo "$inspect" | grep -q "$SHA"; then
      # also check it's Ready
      if echo "$inspect" | grep -qE "state\s+READY|Status\s+● Ready"; then
        echo "$url"
        break
      fi
    fi
  done | head -1)

  if [ -n "$match" ]; then
    target_url="$match"
    break
  fi
  sleep "$POLL_INTERVAL"
  elapsed=$((elapsed + POLL_INTERVAL))
  log "  ... still building (${elapsed}s elapsed)"
done

if [ -z "$target_url" ]; then
  # Fallback: just take the newest Ready if we couldn't match by SHA
  log "couldn't match by SHA; falling back to newest Ready deploy"
  target_url=$(vercel ls fondok --scope "$SCOPE" 2>&1 | \
    grep "● Ready" | head -1 | \
    grep -oE "fondok-[a-z0-9]+-aprem01s-projects\.vercel\.app")
fi

[ -n "$target_url" ] || die "no Ready deploy found after ${MAX_WAIT_SECS}s"

log "aliasing $CANONICAL → $target_url"
vercel alias set "$target_url" "$CANONICAL" --scope "$SCOPE" > /dev/null

sleep 2

log "confirming..."
headers=$(curl -sI "https://$CANONICAL/")
vid=$(echo "$headers" | grep -i "x-vercel-id" | tr -d '\r' | awk '{print $2}')
cache=$(echo "$headers" | grep -i "x-vercel-cache" | tr -d '\r' | awk '{print $2}')

log "done."
log "  x-vercel-id: $vid"
log "  x-vercel-cache: $cache"
log "  deploy: https://$target_url"
log ""
log "hard-refresh the browser (Cmd+Shift+R) to see the new bundle."
