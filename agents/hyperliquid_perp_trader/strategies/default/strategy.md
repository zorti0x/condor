---
name: Default
description: Default loop — Hyperliquid Perp Trader's own identity is the playbook.
agent_key: null
skills: []
default_config: {}
default_trading_context: ''
created_by: 358518143
created_at: '2026-09-13T16:05:23.264916+00:00'
---

Run one tick of your own domain loop. There is no bespoke playbook here on
purpose — your AGENT.md identity *is* the playbook.

Each tick:

1. Refresh your read of the market with your own routines and tools.
2. Decide what, if anything, this tick calls for — inside your domain and inside
   the configured risk limits.
3. Act on it, or explicitly decide to do nothing. Both are valid outcomes.
4. Journal the decision and the reasoning behind it.

Prefer doing nothing over acting on a weak read. When you want a tighter, more
specific loop, write a dedicated strategy under this agent and run that instead.

