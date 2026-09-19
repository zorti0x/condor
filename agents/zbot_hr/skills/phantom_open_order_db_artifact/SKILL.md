---
name: phantom_open_order_db_artifact
description: 'Standing rule: a zero-fill OPEN order row absent from the live active-orders
  feed is a cosmetic DB artifact, not a sync break or rule breach.'
when_to_use: During attendance/health/cleanup checks when a desk's active-order count
  or platform sync check surfaces a zero-fill OPEN order row not present in the live
  venue feed.
created: '2026-09-18T00:25:14Z'
source: agent:zbot_hr
---

STANDING RULE (set by Condor/owner directive, 2026-09-17 cycle):

An OPEN order row in the DB/search_history feed with filled_amount == 0.00 that does NOT appear in the live get_active_orders() feed is a COSMETIC DB ARTIFACT — not a state-sync break, not a desk health failure, not something to flag for restart/cleanup action.

Reference case:
- order_id 0xf036f8793f9c9391a23c1545fb5b717b, ETH-USD MARKET BUY 0.0163 @ 2585.80, created 2026-09-17T16:43, filled 0.0, DB status OPEN.
- Live get_active_orders() returns [] for ALL connectors; cancel_order 404s; no API exists to mutate the row.
- Zero fill, zero exposure — confirmed harmless.

APPLY: Do not raise a health/cleanup incident for this specific known row. Do not attempt to cancel it (it 404s) or restart the ETH desk over it. If a NEW zero-fill OPEN row with a different order_id/timestamp appears, or any such row shows filled_amount > 0, treat that as a genuine new issue and escalate normally.
