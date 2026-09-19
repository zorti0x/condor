---
name: stale_order_escalation
description: ETH desk response to an open, zero-filled order with no running executor
when_to_use: Immediately when ETH-USD has an OPEN order that is zero-filled beyond the execution timeout, especially when the desk is flat or no executor owns the order.
created: '2026-09-17T00:00:00Z'
source: chat
---

# ETH Desk - Stale Order Escalation

## Incident meaning

An order such as `ETH-USD BUY 0.02 MARKET @ 2585.80`, `OPEN`, `0-filled`, with no running executor is a **platform/desk sync break**. It is not proof that ETH is long, and it is not safe to treat the order as harmless because the position is flat.

The current evidence is consistent with a post-close phantom order: a managed ETH position closed, but a raw exchange-order record remained open after its owning executor disappeared. Do not claim the exact trigger without executor logs and order history. Possible triggers include a direct-order or legacy path, an executor termination/cleanup race, or an exchange/API acknowledgement mismatch.

## Immediate response

1. Freeze new ETH entries. Do not create a replacement order while the stale order is unresolved.
2. Read platform state in the same tick:
   - `get_portfolio_overview(include_perp_positions=True, include_active_orders=True)`
   - `manage_executors(action="search", status="RUNNING", connector_names=["hyperliquid_perpetual"], trading_pairs=["ETH-USD"])`
   - `manage_executors(action="search", connector_names=["hyperliquid_perpetual"], trading_pairs=["ETH-USD"], limit=100)`
3. Record the order ID, exchange status, filled quantity, creation time, connector, pair, side, amount, and price. Also record the position and executor state. The platform is the source of truth; the journal is not.
4. Check whether a running or recently terminated executor owns the order. Fetch executor logs for the matching executor and look for submit, fill, cancel, timeout, termination, and cleanup events.
5. If a live executor owns the order, use the supported path: `manage_executors(action="stop", executor_id=<id>, keep_position=false)`. Never call an ad hoc `place_order` cancellation path and never open a second order to compensate.
6. If no executor owns it, do not invent an executor ID. The normal stop operation requires an executor ID, so a raw unowned order must be escalated to the platform/operator cleanup path. Include the order ID and the full state evidence. If the platform exposes a connector-native cancellation operation, use that only after confirming the order ID and pair; otherwise mark it unresolved rather than claiming it was cancelled.
7. Re-read portfolio and active orders after the intervention. Confirm all of the following:
   - the stale order is absent or `CANCELED`/terminal,
   - the ETH position remains flat or matches an explicitly intended position,
   - no replacement order appeared,
   - no executor is left in a contradictory state.
8. Notify the CEO/owner and HR in the same tick. Use `CRITICAL` while an unowned live order remains. Include the exact order ID, age, filled amount, position, executor count, action attempted, and what remains unverified.
9. Journal one execution learning, factually and briefly. Example: `execution: ETH order <id> remained OPEN/0-filled after executor termination; owner and cancellation path were missing; escalated for platform cleanup.`

## Future prevention

- Trade only through `manage_executors(action="create", executor_type="position_executor", ...)`; never use `place_order` for desk entries or exits.
- After every create, verify both the executor state and the exchange position/order state. A submit response is not a fill.
- After every stop, verify that the executor is terminal and that its exchange orders are terminal. Do not report `Position: none` until the platform read confirms it.
- On every tick, scan active orders as well as positions and running executors. An `OPEN` order with age beyond the connector's execution timeout, zero fill, and no owning executor is a CRITICAL stale-order event.
- Keep the one-executor ETH limit. While any stale artifact exists, the next action is reconciliation, not a new trade.
- Preserve the order/executor IDs and logs in the journal so the next tick can continue the incident instead of re-deriving it.

## Fixed escalation report

`CRITICAL: ETH stale order <id> | <side> <amount> <pair> | status=<status> filled=<filled> age=<age> | position=<position> | running_executors=<count> | action=<action> | unresolved=<item>`

The incident is closed only after a fresh platform read shows no live stale order and the ETH position/executor state reconcile.
