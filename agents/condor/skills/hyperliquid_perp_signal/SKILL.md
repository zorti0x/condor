---
name: hyperliquid_perp_signal
description: Periodic Hyperliquid BTC perp entry signals (every 4h, both directions)
  with a self-improvement loop.
when_to_use: User wants periodic BTC perp entry signals from Hyperliquid, asks to
  refine/improve the hyperliquid_perp_scan routine, or to review notification quality
  after entries have played out.
created: '2026-09-09T01:16:49Z'
source: chat
references_routine: hyperliquid_perp_scan
---

# Hyperliquid Perp Signal (BTC, every 4h)

## When to use
- Deploy/monitor the periodic BTC perp entry scanner.
- Refine the routine's thresholds or notification quality after real fills/entries.

## The routine
- `hyperliquid_perp_scan` — continuous routine, runs every 4h against hyperliquid_perpetual (BTC-USD).
- Computes a composite signal in EITHER direction: EMA trend (1h/4h) for direction + RSI(14) for timing + funding-rate crowd filter + volume confirmation → confidence score 0-1.
- Notifies via send_notification ONLY above the confidence threshold, with a short description (direction, price, reason, confidence).
- Config schema is tunable: interval, pair, confidence threshold, funding thresholds, EMA/RSI windows, cooldown.

## Steps to operate
1. Ensure the routine is running: manage_routines(action="start", name="hyperliquid_perp_scan") — list_instances to confirm cadence.
2. When a notification fires, note the signal in the user's context (direction, price, confidence) for later scoring.
3. After the trade window plays out, score the call: did price move in the signaled direction ≥ ~1R? Was the entry early/late? Was the confidence well-calibrated (did >threshold signals win more than threshold implies)?
4. Improve the routine via DELEGATE only (never edit a routine directly): delegate(action="start", agent="condor", task="Edit hyperliquid_perp_scan: <what changed and why>"). Read this skill's self_improve companion for the feedback loop.

## Improvement levers (ranked by impact)
- Funding filter thresholds (the crowd-crowding guard usually matters most on perps).
- Confidence threshold (raise it if notifications are too frequent / lower quality).
- Add time-of-day / volatility filter (ATR) to avoid ranging chop.
- Tighten EMA windows for faster reaction on BTC; widen for fewer false signals.

## Rules
- Routine edits go through delegate, never manage_routines directly.
- Notifications stay Telegram-mobile friendly: no markdown tables, short bullets.
- If the routine is missing (routine_ok=false), re-create it via delegate before invoking.
