---
name: SOL Aggressive Profit Maximizer
description: ''
agent_key: null
skills: []
default_config:
  frequency_sec: 300
  execution_mode: live
  total_amount_quote: 160
default_trading_context: ''
created_by: 358518143
created_at: '2026-09-13T16:20:30.622223+00:00'
---

## SOL Aggressive Profit Maximization Strategy

**Objective**: Maximize profit extraction from SOL perp positions through dynamic risk management and momentum detection.

### Tick Actions (Every 5 minutes):

1. **Position Assessment**
   - Check current SOL position P&L and drawdown
   - Calculate risk-reward ratio vs initial entry
   - Monitor unrealized vs realized profit ratio

2. **Market Momentum Check**
   - Analyze 1m, 5m, 15m SOL price action
   - Check volume spikes and RSI momentum
   - Monitor funding rate for crowd positioning shifts

3. **Dynamic Risk Adjustment**
   - Tighten trailing stops if 2:1 R/R achieved
   - Scale out 25% at major resistance levels  
   - Add to position if breakout confirmed with volume

4. **Profit Maximization Decisions**
   - **Let it run**: Strong momentum + funding neutral
   - **Take partials**: Momentum slowing + extreme funding  
   - **Trail tight**: Overbought but trend intact
   - **Full exit**: Momentum broken or stop triggered

### Risk Rules:
- Max 10% account risk per position
- Trail stops once 8% profit achieved
- Scale out 1/3 at 12% profit
- Full stop loss at 5% down
- Monitor funding rate extremes

### Automation:
- Use manage_executors for position updates
- Send notifications on major decisions
- Update trailing stops every momentum shift
- Log all decisions in journal

The strategy focuses on riding SOL momentum while protecting capital through intelligent risk management.
