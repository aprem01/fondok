# Fondok web

Next.js 14 + Tailwind front end for the Fondok hotel-underwriting platform.

## Smoke test

1. Visit https://fondok-app.vercel.app/projects → see live deals from worker
2. Click "+ New Project" → fill in wizard → click Create → land on new deal page
3. Drop a real PDF in Data Room → see classification + extraction happen live
4. Watch the IRR slider on Returns tab — recompute is instant (TS engine)
5. Click Excel/PDF/PPTX downloads in Export tab — files generate from worker

Diagnostic page (not linked from UI): `/​_test` shows worker URL, /health response, /deals count.

## Local dev

```bash
cd apps/web
NEXT_PUBLIC_WORKER_URL=https://fondok-worker-production.up.railway.app npm run dev
```

Unset `NEXT_PUBLIC_WORKER_URL` to render with mock data (sidebar shows "Offline · using sample data").

## Environment

| Variable | Required | Purpose |
| --- | --- | --- |
| `NEXT_PUBLIC_WORKER_URL` | yes (prod) | FastAPI worker base URL. When unset, the app falls back to `lib/mockData.ts`. |
