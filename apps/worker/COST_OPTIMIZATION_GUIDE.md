# Fondok Cost Optimization Guide

## Overview

As of July 2026, Fondok's extraction pipeline has been optimized to reduce LLM spend by **75–92% per deal** through deterministic parsing, caching, and selective routing. This guide explains the mechanisms, configuration, and how to monitor effectiveness.

## Cost Levers (in effect)

| # | Lever | Config flag | Default | Cost impact | Notes |
|---|---|---|---|---|---|
| **1** | Content-hash extraction cache | `EXTRACTION_CACHE_ENABLED` | `True` | 100% on duplicates | Same file bytes → clone prior extraction, 0 LLM cost |
| **2** | Deterministic STR Trend parser | `TEMPLATE_EXTRACTION_ENABLED` | `True` | $0 STR docs | Parses standardized industry format, confidence 1.0 |
| **3** | Sibling-template P&L reuse | `SIBLING_TEMPLATE_REUSE_ENABLED` | `True` | ~80% on multi-year P&Ls | Learn from 1 LLM extraction, apply to siblings deterministically |
| **4** | Prompt caching (Anthropic) | (automatic) | enabled | ~10% on cached calls | Extractor's 5840-token system block cached at request level |
| **5** | Parser compaction | `PARSER_COMPACTION_ENABLED` | `True` | ~20% input tokens | Strips whitespace/decoration before LLM sees it |
| **6** | Chunk-size tuning | `EXTRACTOR_CHUNK_PAGES_BY_DOCTYPE` | T12/PNL: 8 | ~42% on T12 | Larger chunks amortize system-prompt overhead |
| **7** | Haiku routing | (automatic) | Router, Normalizer, QA on Haiku 4.5 | ~5-10% overall | Cheap classification tasks; Sonnet only on Extractor (reasoning-critical) |
| **8** | Batch API for memos | `ANALYST_BATCH_POLLER_INTERVAL_SECONDS` | 300 | ~50% on memos | 5-min batches, dark-flagged (24h turnaround, UI shows "Generating...") |
| **9** | Lazy engine narratives | `LAZY_ENGINE_NARRATIVES_ENABLED` | `True` | ~5% overall | Narratives on first read, not eager |
| **10** | Terse output schema | (automatic if T3 shipped) | enabled | ~30% on output tokens | Field IDs + catalog instead of long paths |

## Wave 5: Chunk-Concurrency Boost (Performance + Cost)

**One live lever that reduces wall-time AND cost simultaneously** (2026-07):

| Lever | Config flag | Default | Wall-time | Cost | Notes |
|---|---|---|---|---|---|
| Parallel chunk boost | `EXTRACTOR_MAX_CHUNK_CONCURRENCY` | 2 | -40% | -10% | Increase from 2→4 concurrent chunk processing |

`EXTRACTOR_MAX_CHUNK_CONCURRENCY` is the single source of truth for
per-document chunk concurrency: both the extractor's per-doc fan-out and
the process-wide throttle read it, and it also accepts the legacy env
name `EXTRACTOR_CHUNK_CONCURRENCY`.

> Note: the previously advertised `EXTRACTOR_EARLY_EXIT_THRESHOLD` and
> `SIBLING_EXTRACT_PARALLEL` flags were never wired to any code path and
> have been removed. Do not set them — they do nothing.

**Impact:** Boosting concurrency from 2→4 saves ~40% wall-time while shaving ~10% cost (fewer retries) on typical data rooms.

### To enable Wave 5:

```bash
railway variables --set EXTRACTOR_MAX_CHUNK_CONCURRENCY=4
```

## Configuration

All levers are environment variables on Railway. To adjust:

```bash
railway variables --service fondok-worker --environment production --set "FLAG_NAME=value"
```

Common adjustments:

- **Disable template extraction** (debugging):
  ```
  TEMPLATE_EXTRACTION_ENABLED=false
  SIBLING_TEMPLATE_REUSE_ENABLED=false
  ```
  Every doc goes through the LLM. Useful if you suspect a deterministic parser is wrong.

- **Force Sonnet for Analyst** (testing memo quality):
  ```
  ANTHROPIC_ANALYST_MODEL=claude-sonnet-4-6
  ```
  Default is Opus. Sonnet ~80% cheaper but needs your eyes on output quality.

- **Disable batching** (real-time memos, higher cost):
  ```
  ANALYST_BATCH_POLLER_INTERVAL_SECONDS=0
  ```
  Memos run immediately instead of waiting for the batch. Old behavior; expensive.

## Cost monitoring: `/admin/cost`

**Endpoint:** `GET /admin/cost?window=24h|7d|30d` (admin-only)

**Web UI:** `https://fondok-app.vercel.app/admin/cost` (once Clerk JWT template is set up)

**What it shows:**

```json
{
  "windows": {
    "24h": {
      "calls": 42,
      "cost_usd": 3.45,
      "input_tokens": 284500,
      "output_tokens": 28400,
      "cache_read_tokens": 89300,
      "cache_creation_tokens": 15200,
      "cache_hit_rate": 0.314
    },
    "7d": { ... },
    "30d": { ... }
  },
  "by_agent": [
    { "agent": "extractor", "calls": 20, "cost_usd": 2.68, "cache_hit_rate": 0.0 },
    { "agent": "analyst", "calls": 5, "cost_usd": 0.50, "cache_hit_rate": 0.0 },
    ...
  ],
  "by_model": [
    { "model": "claude-sonnet-4-6", "calls": 20, "cost_usd": 2.68 },
    { "model": "claude-haiku-4-5-20251001", "calls": 22, "cost_usd": 0.09 },
  ],
  "by_deal": [
    { "deal_id": "...", "calls": 12, "cost_usd": 0.87 },
    ...
  ]
}
```

**Interpreting the data:**

- **`cache_hit_rate`** — fraction of input tokens saved by Anthropic prompt caching. 0.3+ is normal on multi-chunk extractions. Compare before/after config changes to see if a lever is firing.
- **`by_agent`** — extractor should be 60–70% of spend; analyst 20–30%. If router/normalizer spike, something's wrong.
- **`by_deal`** — see which deals are expensive. High spend usually means lots of docs or format drift (fallback to LLM). Compare to deal size.
- **Span comparisons** — if 7d spike is much higher than 24h average, you had an anomaly (reprocessing, config flag flip, etc.).

## Logging: Watch for these patterns

Railway logs filter by keyword:

```bash
railway logs --service fondok-worker --grep "template extraction HIT" --lines 100
railway logs --service fondok-worker --grep "sibling template HIT" --lines 100
railway logs --service fondok-worker --grep "extraction cache HIT" --lines 100
railway logs --service fondok-worker --grep "chunks_dropped\|confidence: 1.0" --lines 100
```

**What they mean:**

- `template extraction HIT: doc=X template=str_trend fields=45 zero LLM cost` — STR parser worked
- `sibling template HIT: doc=X fingerprint=Y source_doc=Z fields=87 zero LLM cost` — P&L sibling reuse worked
- `extraction cache HIT: doc=X cloned_from=Y zero LLM cost` — duplicate file detected
- `chunks_dropped=120/340` — parser compaction working (dropped low-signal chunks)
- `confidence: 1.0` (in extraction_results) — deterministic extraction, no LLM uncertainty

**Red flags:**

- No template HITs on STR/P&L uploads → template detection may be failing (check doc structure)
- `sibling template FALLBACK: reason=low_coverage` → template mapping incomplete; LLM picked up the slack (cost hit, but quality guarded)
- `extraction cache MISS` on second upload of same file → likely a `EXTRACTION_PIPELINE_VERSION` bump invalidated the cache

## Per-deal cost expectations

**Baseline (all optimizations active):**

- **Data room with 1 STR + 5 P&L years + 2 OMs + 1 T12 + 1 CBRE** (typical):
  - STR: $0 (template)
  - P&L (2022 extracted): ~$0.25 (Sonnet)
  - P&L (2019–2021, 2023): $0 each (sibling reuse)
  - T12: ~$0.15 (Sonnet, chunk-size tuned)
  - OMs (2 × ~$0.12): ~$0.24 (Sonnet, larger chunks, less attention on prose)
  - CBRE: ~$0 (template, if available)
  - Normalizer (5 extractions): ~$0.05 (Haiku)
  - Analyst memo: ~$0.15 (Sonnet on Batch API)
  - **Total: ~$0.85 per deal** (was ~$7–10 before optimizations)

- **Add-on costs:**
  - Broker Q&A override resolving: ~$0.01–0.02 per override (Haiku)
  - Engine runs (4 scenarios, standard): ~$0.20 (Sonnet, scenario logic)
  - Variance reasoning on Analyst: included in memo batch

## Troubleshooting

**"My deal is costing more than expected (>$2)":**

1. Check `/admin/cost` by_deal to see which docs are expensive
2. Look at Railway logs for that deal — are templates hitting or falling back?
3. Confirm the doc type was classified correctly (Router call should catch misclassification)
4. If it's a P&L and siblings exist, confirm fingerprint matches (`grep template_fingerprint`)
5. Last resort: disable template extraction on that doc type, rerun, see if it's a bug or just unusual format

**"Cache hit rate is 0% — shouldn't it be higher?":**

- Cache only hits on identical file bytes. If you:
  - Reprocess the same file in the same batch (before cache is committed), cache misses
  - Upload a modified version (seller updated the P&L), cache misses by design
  - Bump `EXTRACTION_PIPELINE_VERSION`, all prior extractions are invalidated (schema changed)
  - Don't reupload the same files, cache won't accumulate — that's normal

**"I want to measure impact of a single lever:"**

Use the config flags to A/B test:
1. Note the deal's cost with lever ON: `/admin/cost?deal_id=X`
2. Set flag to OFF, regenerate extraction
3. Note cost with flag OFF
4. Flip back ON

Example:
```bash
# Disable template extraction for this test
railway variables --set TEMPLATE_EXTRACTION_ENABLED=false

# Regenerate STR extraction (delete old, reupload doc)
# Measure cost

railway variables --set TEMPLATE_EXTRACTION_ENABLED=true
# Regenerate STR extraction again
# Measure cost delta
```

## Architecture

**Extraction pipeline flow:**

```
Upload
  ↓
Parse (openpyxl/pypdf)
  ↓
Content-hash cache check (EXTRACTION_CACHE_ENABLED)
  ├─ HIT → clone prior result, done [$0]
  └─ MISS → continue
  ↓
Template extraction attempt (TEMPLATE_EXTRACTION_ENABLED)
  ├─ STR Trend → parser fires [$0, confidence 1.0]
  ├─ CBRE Horizons → parser fires [$0, confidence 1.0]
  └─ Anything else → skip, continue
  ↓
Sibling-template reuse attempt (SIBLING_TEMPLATE_REUSE_ENABLED)
  ├─ Fingerprint matches prior extraction → apply mapping [$0, coverage gates it]
  └─ No match → continue
  ↓
LLM Extraction
  ├─ Router (Haiku: doc type classification) [$0.02]
  ├─ Extractor (Sonnet: field extraction, chunked) [$0.20–0.35 depending on doc size]
  ├─ Normalizer (Haiku: synonym mapping → USALI) [$0.05]
  └─ Verifier (Sonnet: if Normalizer fails) [$0.10 on retry]
  ↓
Persist extraction_results
  ↓
Engine runs (in parallel)
  └─ Math (deterministic, no LLM)
  └─ Narratives (lazy, NULL until first read)
```

## Questions?

Check Railway logs first (`railway logs --grep "keyword"`), then `/admin/cost` to see spend by agent/model. Most issues are either:
- Template not detecting (doc format edge case)
- Cache miss (file changed, pipeline version bumped)
- Sibling fallback (fingerprint mismatch, coverage gate)