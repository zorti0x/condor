---
name: cosmetic_order_db_artifact
description: 'Standing rule: OPEN order rows with filled_amount==0 absent from the
  live venue active-orders feed are cosmetic DB artifacts, not sync breaks.'
when_to_use: Any time a desk, HR, or audit sees an order row with status=OPEN, filled_amount=0,
  in get_portfolio_overview / search_history(orders) but the order does not appear
  in the platform's live active-orders feed (client.trading.get_active_orders()) and
  cancel_order returns 404 not-found.
created: '2026-09-18T00:24:26Z'
source: agent:zbot_audit
---

## Standing rule — cosmetic DB order artifact (owner-directed, logged 2026-09-17)

An order row that is:
- status = OPEN in the DB / search_history(orders) / get_portfolio_overview Active Orders, AND
- filled_amount == 0, AND
- absent from the live venue active-orders feed (client.trading.get_active_orders() returns [] for the connector)

...is a COSMETIC DB ARTIFACT. It carries zero fill, zero margin, zero exposure. It is NOT:
- a state-sync break
- evidence of an untracked position
- grounds for a CRITICAL finding
- grounds for a desk reconciliation penalty
- a valid reason to SKIP/block a new trade decision (do not gate fresh entries on it)

Known unpurgeable example: order_id 0xf036f8793f9c9391a23c1545fb5b717b (exchange_order_id
548049707719), ETH-USD MARKET BUY 0.0163 @ 2585.80, created 2026-09-17T16:43. Full API surface
probed — no PATCH/PUT/DELETE endpoint mutates order status; cancel_order returns HTTP 404 "not
found in active orders". Only a backend/manual DB purge clears it. Treat any row matching this
same signature (OPEN, filled=0, absent from live feed, uncancelable) the same way.

## What to still do
1. Record it once as a known, unpurgeable artifact — per zbot log-transparency policy, never
  paper over a discrepancy even when it is benign. State explicitly: DB shows OPEN, live
  feed shows absent, API cannot purge it, zero exposure. On later cycles, use the exact
  client order ID as the deduplication key and report `no new order finding` unless its
  live, fill, or position state changes.
2. Diff DB order search vs the live active-orders feed explicitly before calling it resolved or
   escalating it — do not accept either view alone.
3. Do NOT let it block trading decisions or get escalated as CRITICAL/SYNC BREAK going forward.

## Never
Never re-classify this as a live/unknown position. Never instruct a desk to try to cancel it
again once it has been confirmed 404/unreachable — that is a known dead end.
