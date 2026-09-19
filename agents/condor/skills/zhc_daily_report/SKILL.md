---
name: zhc_daily_report
description: ZHC (ZERO-HUMAN COMPANY) daily watch — LP pool liquidity, trading activity,
  and a 0-100 health score, delivered at 04:00 Mountain Time.
when_to_use: On the daily 04:00 Mountain Time schedule, or whenever the user asks
  for ZHC status, pool health, LP/trading activity, or the daily ZHC report.
created: '2026-09-15T15:10:02Z'
source: chat
references_routine: zhc_daily_report
---

# ZHC Daily Report — Playbook

## What this is
Daily monitoring of the ZHC token (ZERO-HUMAN COMPANY)
- Solana mint: AWc8uws9nh7pYjFQ8FzxavmP8WTUPwmQZAvK2yAPBAGS
- Tracks all LP pools, trading activity, and a custom 0-100 health score
- One Telegram report every morning

## When to apply
- Scheduled: every day at 04:00 Mountain Time (America/Denver, DST-aware) via the continuous routine `zhc_daily_report`
- On demand: user asks "ZHC status/health", "how are ZHC pools", "ZHC activity today", "run the ZHC report"

## What the routine does
1. Fetches ZHC data from GeckoTerminal: price / mcap / FDV, total reserves, 24h volume, all LP pools (dex, reserve, volume, fee tier, created_at), recent trades + ~7d OHLCV on the active pools, and detects NEW pools since last run.
2. Computes the Health Score (0-100):
   - Liquidity (40): total reserves, effective pool count, 7d reserve trend, concentration penalty
   - Trading (40): 24h vol vs 7d avg, txns, buy:sell ratio, active pools
   - News/Pulse (20): new pools, volume spikes, price moves
   - Bands: 70+ Healthy / 40-69 Watch / <40 Danger
3. Persists prior run state (score, reserves, volume, price, pool set) for ▲/▼/— trend arrows.
4. Sends a Telegram report: summary, price/mcap, LP pools, trading activity, health score + drivers, news, and a one-line LP recommendation.

## Operations
- One-shot (wait): manage_routines(action="run", name="zhc_daily_report")
- One-shot (async, no wait): manage_routines(action="run_async", name="zhc_daily_report")
- View current/running instances: manage_routines(action="list_instances")
- Read a run back: manage_routines(action="get_instance", name="<instance_id>")
- Stop the daily loop: manage_routines(action="stop", name="<instance_id>")

## Notes
- ZHC history: launched 2026-01-22; original LP pool was Meteora DBC Ad4HNrRY...; today's only active pool is Meteora DMM v2 C8KA6cyj... (still ~$100/day — very weak). The score is designed to warn before this gets LPed.
- Report format must stay Telegram-safe: no markdown tables, short lines.
