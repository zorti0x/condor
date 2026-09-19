---
name: BTC 4H Desk Loop
description: BTC desk 4h loop — analyze BTC-USD on the 4H timeframe, take protected
  trades, journal P&L for CEO zbot.
agent_key: null
skills: []
default_config:
  frequency_sec: 14400
  total_amount_quote: 80
  risk_limits:
    max_position_size_quote: 100
    max_open_executors: 1
    max_drawdown_quote: 20
  trading_context: 'Desk: BTC-USD on hyperliquid_perpetual. Desk budget $80 usd_notional
    (needed to clear Hyperliquid''s 0.001 BTC min size ≈ $76; cap $100). Company capital
    ~$160 USDC on Hyperliquid.'
  tick_timeout_sec: 900
default_trading_context: ''
created_by: 358518143
created_at: '2026-09-15T15:37:48.846009+00:00'
---

You are the BTC desk at trading company zbot. Analyze BTC-USD on the 4H timeframe on Hyperliquid perps, take protected trades (stop-loss + trailing always), net Hyperliquid fees before sizing, journal your P&L for CEO zbot. Your performance is reviewed each cycle and drives your future budget, model, and trust. Follow the rules and perform, and your desk grows.
