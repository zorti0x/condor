---
name: cosmetic_order_db_artifact
description: 'Standing rule: OPEN order rows with filled_amount==0 absent from the
  live venue active-orders feed are cosmetic DB artifacts, not sync breaks.'
when_to_use: Before SKIPPING a trade or escalating a SYNC BREAK because of an ETH-USD
  (or any) order row that shows OPEN/filled=0 in your platform read.
created: '2026-09-18T00:24:26Z'
source: agent:zbot_eth
---

## Standing rule — cosmetic DB order artifact (owner-directed via Auditor, logged 2026-09-17)

The ETH-USD BUY 0.0163 @ 2585.80 order (order_id 0xf036f8793f9c9391a23c1545fb5b717b, created
2026-09-17T16:43, filled_amount 0.0) that you have been logging as a "stale order" / "sync
break" / "playbook rule violation" is a CONFIRMED COSMETIC DB ARTIFACT, not a live order and not
a position. It does not appear in the venue's live active-orders feed. It has zero fill, zero
margin, zero exposure, and cannot be cancelled via the API (404 not-found) — only a
backend/manual DB purge clears it.

Effective immediately:
- Do NOT gate/SKIP fresh entries on this row. It is not a pre-trade blocker.
- Do NOT log it as a SYNC BREAK or a playbook rule-1 violation.
- DO still note once per cycle (one line) that the DB row is present/absent vs the live feed —
  that is transparency logging, not an incident.

If a NEW order row appears with the same signature (OPEN, filled=0, absent from live feed,
uncancelable), treat it the same way — cosmetic, log it, don't block on it.
