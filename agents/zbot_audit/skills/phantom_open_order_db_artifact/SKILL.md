---
name: phantom_open_order_db_artifact
description: 'Standing rule: a zero-fill OPEN order row absent from the live active-orders
  feed is a cosmetic DB artifact, not a sync break or rule breach.'
when_to_use: Whenever reconciling a desk's journal/platform state against live active
  orders (audit cycle, HR sync check, or ETH desk self-check) and a zero-fill OPEN
  order row is found.
created: '2026-09-18T00:25:14Z'
source: agent:zbot_audit
---

STANDING RULE (set by Condor/owner directive, recorded on this audit cycle):

An OPEN order row in the DB/search_history / get_portfolio_overview feed that has filled_amount == 0.00 AND does NOT appear in the live get_active_orders() feed for its connector is a COSMETIC DB ARTIFACT.

- It is NOT a state-sync break.
- It is NOT a rule breach (not a journal-vs-platform mismatch, not an unprotected/untracked position — it never filled, zero exposure, zero notional).
- Do NOT escalate it as CRITICAL or WARN. Do NOT penalize the owning desk's reconciliation score for it.
- DO still log its presence each cycle as an open transparency item (it should not be silently dropped from records) — note count and persistence, but do not treat as new each time.

Reference case (baseline evidence, first observed 2026-09-17):
- order_id: 0xf036f8793f9c9391a23c1545fb5b717b
- ETH-USD MARKET BUY 0.0163 (~0.02 shown rounded) @ 2585.80
- created 2026-09-17T16:43, filled 0.0, DB status OPEN
- Appears in get_portfolio_overview Active Orders and search_history(orders, status=OPEN)
- get_active_orders() (live venue) returns [] for ALL connectors
- cancel_order returns 404 'not found in active orders'
- No API endpoint exists to mutate/clear the DB row's status
- Zero fill, zero exposure — confirmed harmless, just un-cancelable due to missing API support

HOW TO APPLY:
1. When active-orders count from get_portfolio_overview/search_history is nonzero, check if the extra row(s) match this pattern (filled==0, OPEN, MARKET, stale timestamp).
2. Cross-check against live feed if available. If the row cannot be found live and is zero-fill, tag it PHANTOM/COSMETIC in your notes, not CRITICAL/WARN.
3. Record it once in the transparency section so it stays visible to the owner. On later
  cycles, deduplicate by exact client order ID and write `no new order finding` unless the
  live, fill, or position state changes. Do not reopen the old alert.
4. If a NEW zero-fill OPEN row appears beyond this known one, or if filled_amount > 0 on any such row, treat it as a fresh finding requiring normal audit escalation — this exemption applies ONLY to the known pattern, not a blanket pass on all zero-fill rows.
