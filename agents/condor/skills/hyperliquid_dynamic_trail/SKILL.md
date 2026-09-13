---
name: hyperliquid_dynamic_trail
description: Agentic 30m-candle dynamic trailing-stop manager for a live perp position.
when_to_use: User wants a live perp position's trailing stop adapted in real time
  from 30m candles, or wants to tune the hyperliquid_dynamic_trail routine.
created: '2026-09-09T04:23:55Z'
source: chat
references_routine: hyperliquid_dynamic_trail
---

# Hyperliquid Dynamic Trail (agentic real-time stop)

## When to use
- User wants a live perp position's trailing stop adapted in real time from 30m candles (to capture maximum of a move).
- Reviewing/tuning the `hyperliquid_dynamic_trail` routine or scoring its exit quality after trades.

## The routine
- `hyperliquid_dynamic_trail` — continuous, every 30m, owns the dynamic trail itself (no live executor config update exists).
- Dynamic trail = ATR(14 of 30m) × multiplier; multiplier = ratchet tier (feathers tighter as profit grows) × trend/vol adjustments; bounded by min/max; hard disaster stop at hard_stop_pct never widened.
- Profit ratchet, momentum boost, vol-spike guard, funding-contra guard (crowded short on a SHORT → tighten/exit).
- Modes: monitor (compute + recommend via Telegram, no trades) / active (opens, trails, closes via trading API).
- Every trail change notified; open/close notified; LiveReport dashboard each cycle.

## Operate
1. Ensure running: manage_routines(action="start", name="hyperliquid_dynamic_trail", config={...}) — confirm pairing + mode (start monitor first for trust, then active).
2. On trail updates, log (pair, stop, tier, ATR, reason). On close, log fill vs entry for scoring.
3. Score each exit: did it capture the move? Was the trail optimal vs simple static? Too tight (shaken out) or too wide (gave back)?
4. Improve ONLY via delegate: delegate(action="start", agent="condor", task="Edit hyperliquid_dynamic_trail: <change + why>").

## Levers (ranked)
- Ratchet tiers/multipliers (the core aggressiveness)
- ATR window + base_multiplier (sensitivity to the coin's real volatility)
- hard_stop_pct (the risk cap — raise/lower with care)
- Vol-spike guard cap (avoid blowup in crazy candles)
- Funding-contra threshold

## Rules
- Never edit the routine directly — always delegate.
- Hard stop must never be widened beyond hard_stop_pct (safety invariant).
- Idempotent: always check open positions before acting (no doubling).
- Telegram-mobile friendly, no tables.
