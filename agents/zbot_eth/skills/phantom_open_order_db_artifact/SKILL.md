---
name: phantom_open_order_db_artifact
description: 'Standing rule: a zero-fill OPEN order row absent from the live active-orders
  feed is a cosmetic DB artifact, not a sync break or rule breach.'
when_to_use: When reviewing your own active orders / journal state each tick and you
  see a zero-fill OPEN ETH-USD order that you don't recall placing or that never fills.
created: '2026-09-18T00:25:14Z'
source: agent:zbot_eth
---

STANDING RULE (set by Condor/owner directive, 2026-09-17 cycle):

A known OPEN order row, order_id 0xf036f8793f9c9391a23c1545fb5b717b, ETH-USD MARKET BUY 0.0163 @ 2585.80, created 2026-09-17T16:43, filled 0.0, is a COSMETIC DB ARTIFACT. Live get_active_orders() returns [] for all connectors (it is not live/working), cancel_order 404s on it, and there is no API to clear it. Zero fill, zero exposure.

APPLY: Do not treat this row as an open order you must manage, hedge, or worry about in your notional/exposure accounting. Do not repeatedly log it as an anomaly each tick — it's known. If a genuinely NEW zero-fill OPEN order appears (different order_id or timestamp) or this one ever shows filled_amount > 0, that IS real and must be logged/escalated immediately.
