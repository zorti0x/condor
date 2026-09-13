---
name: hyperliquid_top5_scan
description: Screens top-5 Hyperliquid perps by volume with the perp-entry signal
  every few hours.
when_to_use: User wants the top Hyperliquid perps ranked by volume screened for high-probability
  entries, or to tune the hyperliquid_top5_scan routine/alerts.
created: '2026-09-09T03:41:20Z'
source: chat
references_routine: hyperliquid_top5_scan
---

# Hyperliquid Top-5 Perp Scanner

## When to use
- User wants the top-5 perps by volume screened every few hours with the perp-entry signal on each.
- Tuning/improving the `hyperliquid_top5_scan` routine or reviewing alert quality after entries play out.

## The routine
- `hyperliquid_top5_scan` — continuous, every 4h (default).
- Ranks Hyperliquid perps by 24h notional volume (Hyperliquid meta API, dayNtlVlm) → takes top N (default 5).
- Runs the composite perp signal (EMA 1h/4h trend 33% / RSI 25% / funding filter 20% / volume 17% / swing breakout 5% → confidence 0-1) on each.
- Telegram summary + ALERT blocks only on high-confidence signals by default; LiveReport dashboard every scan.
- Config: top_n, min_volume_usd, exclude_pairs, confidence_threshold, notify_mode (signals_only/summary_always), cooldown_hours, funding filter, EMA/RSI/volume windows, all tunable.

## Operate
1. Ensure running: manage_routines(action="start", name="hyperliquid_top5_scan") — confirm cadence & mode with list_instances.
2. When an ALERT fires, log (pair, direction, confidence, price, funding) for later scoring.
3. After the trade window, score each call: did price reach ~1R in the signaled direction? Entry timing? Was confidence calibrated?
4. Improve the routine ONLY via delegate: delegate(action="start", agent="condor", task="Edit hyperliquid_top5_scan: <what changed and why>").

## Levers (ranked)
- confidence_threshold (raise if too many alerts)
- funding filter thresholds (crowding guard)
- top_n / min_volume_usd (focus on liquid names)
- notify_mode (summary_always when a quick screen is wanted)
- EMA windows (faster = more signals, wider = cleaner)

## Rules
- Routine edits go through delegate, never manage_routines directly.
- Notifications stay Telegram-mobile friendly: no markdown tables, short lines.
- If routine_ok=false, re-create via delegate before invoking.
