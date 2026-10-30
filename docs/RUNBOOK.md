# Fondok Runbook

> One-page on-call playbook. If you're paged, start here.

**Live surfaces**

| Surface | URL |
|---|---|
| Web app  | https://fondok-app.vercel.app/ |
| Worker   | https://fondok-worker-production.up.railway.app/ |
| Worker health | https://fondok-worker-production.up.railway.app/health (`build_sha` = the commit serving) |
| Web build | https://fondok-app.vercel.app/api/version (`{sha, env}`) |
| Operator health | `GET /admin/health` with an internal admin JWT — auth posture, `llm_provider`, last-hour failure counters |

**Dashboards**

| What | Where |
|---|---|
| Worker logs / deploys | https://railway.com/project/4c62714e-3546-4779-8bdc-5600a086cac8 |
| Web deploys           | https://vercel.com/aprem01s-projects/fondok |
| Auth / users          | https://dashboard.clerk.com |
| LLM traces            | https://smith.langchain.com |
| Anthropic spend       | https://console.anthropic.com |

---

## Triage flowcharts

### "I pushed, but production doesn't have it"

1. Compare what is serving with what you pushed:
   ```bash
   curl -s https://fondok-worker-production.up.railway.app/health \
     | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["build_sha"], d["version"], d["status"])'
   curl -s https://fondok-app.vercel.app/api/version
   git rev-parse origin/main
   ```
2. Worker `build_sha` behind `origin/main` → Railway has not deployed it: open the Deployments tab. A failed health check rolls the deploy back and keeps the old container (so does a crash loop); a `null` `build_sha` means `RAILWAY_GIT_COMMIT_SHA` is not reaching the container. On 2026-10-28 Railway simply lagged 3.5 hours.
3. Web `sha` behind `origin/main` while the push touched `apps/web/` → the Vercel build failed or the alias was not promoted; see "Web app doesn't load" and the alias workflow (`Promote fondok-app alias`). A docs-only push legitimately leaves the web sha where it was.
4. The post-deploy smoke's `worker-deployed` job performs exactly this comparison for 20 minutes after every alias promotion; a red run there with `::error::` is this page.

### "Worker isn't responding"

1. `curl https://fondok-worker-production.up.railway.app/health` — if 5xx or hangs, worker is down.
2. Open Railway logs (link above) and check the latest deploy:
   - **Deploy in flight** → wait, retry in 60s.
   - **OOM kill** → bump the service memory plan; investigate the latest LangGraph run for a runaway agent loop.
   - **Postgres connection refused** → Railway Postgres add-on may be restarting; check the add-on health tab.
   - **Crash loop on import** → revert the last worker commit; redeploy.
3. If the worker is up but slow, check Anthropic console for a regional outage and LangSmith for tail-latency spikes.

### "Extraction returns empty fields" / "every document FAILED"

1. Read `GET /admin/health` (internal admin JWT). `llm_provider.last_error` names the last refusal from Anthropic — `authentication` (revoked or wrong `ANTHROPIC_API_KEY`), `permission_denied`, `insufficient_credit` (402 or "credit balance is too low") — with `last_error_at`; `extraction_failures_last_hour` and `engine_failures_last_hour` say how wide the blast radius is. The same refusal pages Slack / Sentry once per 10 minutes as `llm_provider_unavailable` (critical).
2. `authentication` → the key on Railway is dead: rotate it in the Anthropic console, set it through stdin (`printf '%s' "$KEY" | railway variable set --service fondok-worker --stdin ANTHROPIC_API_KEY`), confirm the redeploy with the `build_sha` check above. `insufficient_credit` → top up at console.anthropic.com → Billing; no redeploy needed. Rotated keys are the #1 cause.
3. Confirm `EVALS_MOCK` is **not** set to `true` in production — that flag short-circuits the LLM and returns canned data.
4. Open LangSmith and find the most recent extraction trace:
   - Tool returned but with low confidence → check the input PDF in Postgres `documents` table; OCR may have produced garbage text.
   - Tool errored on JSON parse → schema drift; verify `packages/schemas-py/` matches the prompt's expected fields.
5. If the issue is reproducible, run `make worker-dev` locally with the same input to capture a stack trace.

### "Web app doesn't load"

1. Check Vercel deployment status (link above). If the latest deploy failed, the previous version is still serving — investigate the build log.
2. Open the browser devtools network tab — if requests to the worker fail with CORS or 5xx, the issue is the worker, not the web. Jump to "Worker isn't responding".
3. Confirm `NEXT_PUBLIC_WORKER_URL` is set on Vercel (Settings → Environment Variables). Missing var = web falls back to mock data and looks "broken" to a user expecting their real deal.
4. Check the Clerk status page if sign-in is failing.

---

## Rollbacks

### Worker (Railway)

Preferred (one-click):

```bash
railway rollback <deployment-id>
```

Find the deployment ID in the Railway dashboard → Deployments tab. Each commit gets a deployment. Afterwards confirm `/health` `build_sha` is the commit you rolled back to (the one-liner in "I pushed, but production doesn't have it").

Fallback (revert + redeploy):

```bash
git revert <bad-sha>
git push origin main
# Railway auto-deploys main on push.
```

### Web (Vercel)

```bash
vercel alias set <previous-deploy>.vercel.app fondok-app.vercel.app
```

You can grab the previous deployment URL from the Vercel dashboard. The alias swap is instantaneous and doesn't trigger a rebuild.

Fallback: `git revert <bad-sha> && git push origin main` and let Vercel rebuild.

---

## Where to find things

| Thing | Location |
|---|---|
| Env vars (worker)     | Railway → service → Variables tab |
| Env vars (web)        | Vercel → project → Settings → Environment Variables |
| Production database   | Railway → Postgres add-on (psql via the connection string in Variables) |
| Anthropic invoices    | https://console.anthropic.com → Billing |
| LLM traces / prompts  | https://smith.langchain.com → fondok project |
| Sentry / error logs   | (not yet wired — Railway logs are the source of truth) |
| Daily Anthropic spend | `console.anthropic.com` → Usage; alarm at $100/day |

---

## Escalation

There's currently one engineer (Prem). For partner-facing incidents (Fondok or Zed Truong demos), Slack is the fastest channel.
