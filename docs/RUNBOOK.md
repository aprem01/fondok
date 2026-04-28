# Fondok Runbook

> One-page on-call playbook. If you're paged, start here.

**Live surfaces**

| Surface | URL |
|---|---|
| Web app  | https://fondok-app.vercel.app/ |
| Worker   | https://fondok-worker-production.up.railway.app/ |
| Worker health | https://fondok-worker-production.up.railway.app/health |

**Dashboards**

| What | Where |
|---|---|
| Worker logs / deploys | https://railway.com/project/4c62714e-3546-4779-8bdc-5600a086cac8 |
| Web deploys           | https://vercel.com/aprem01s-projects/fondok-app |
| Auth / users          | https://dashboard.clerk.com |
| LLM traces            | https://smith.langchain.com |
| Anthropic spend       | https://console.anthropic.com |

---

## Triage flowcharts

### "Worker isn't responding"

1. `curl https://fondok-worker-production.up.railway.app/health` — if 5xx or hangs, worker is down.
2. Open Railway logs (link above) and check the latest deploy:
   - **Deploy in flight** → wait, retry in 60s.
   - **OOM kill** → bump the service memory plan; investigate the latest LangGraph run for a runaway agent loop.
   - **Postgres connection refused** → Railway Postgres add-on may be restarting; check the add-on health tab.
   - **Crash loop on import** → revert the last worker commit; redeploy.
3. If the worker is up but slow, check Anthropic console for a regional outage and LangSmith for tail-latency spikes.

### "Extraction returns empty fields"

1. Confirm `ANTHROPIC_API_KEY` is set on the Railway worker service (Variables tab). Rotated keys are the #1 cause.
2. Confirm `EVALS_MOCK` is **not** set to `true` in production — that flag short-circuits the LLM and returns canned data.
3. Open LangSmith and find the most recent extraction trace:
   - Tool returned but with low confidence → check the input PDF in Postgres `documents` table; OCR may have produced garbage text.
   - Tool errored on JSON parse → schema drift; verify `packages/schemas-py/` matches the prompt's expected fields.
4. If the issue is reproducible, run `make worker-dev` locally with the same input to capture a stack trace.

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

Find the deployment ID in the Railway dashboard → Deployments tab. Each commit gets a deployment.

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
