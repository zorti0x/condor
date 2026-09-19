---
name: chart_brief_format
description: Fixed schema for zbot chartist briefs so the desks can act on them directly
  — pattern, levels, bias, invalidation, confidence.
when_to_use: Every chartist tick, before publishing a brief — and whenever a level
  break invalidates a live desk position.
created: '2026-09-17T16:39:56Z'
source: agent:zbot_charts
---

# Chartist Brief Format (zbot)

The desks trade off your briefs — a vague brief is worse than none. Publish in a FIXED schema
so they can act on it without interpretation.

## One block per token (BTC-USD, ETH-USD, SOL-USD · 4h · hyperliquid_perpetual)
    Token: <BTC-USD>
    Timeframe: 4h
    Pattern: <named structure — e.g. lower-highs, range, bull flag — or "none clean">
    Key levels: <support> / <resistance>
    Bias: <bullish | bearish | neutral> — <one clause of why>
    Invalidation: <the price that proves the bias wrong>
    Confidence: <0-100%>

## Rules
- Levels are the round numbers that matter (e.g. 76.55–76.75) — not decimals.
- Bias MUST carry an invalidation level. No invalidation → it is not a call.
- Confidence < 60% → write "no clean setup" instead of dressing up noise.
- Publish in your journal summary the SAME tick. Never leave a stale brief attached to a
  market that has moved — the desks read the latest only.

## Escalate
A level break that invalidates a live desk position → notify the CEO the same tick.
