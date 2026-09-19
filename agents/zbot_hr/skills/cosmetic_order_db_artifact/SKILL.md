---
name: cosmetic_order_db_artifact
description: 'Standing rule: OPEN order rows with filled_amount==0 absent from the
  live venue active-orders feed are cosmetic DB artifacts, not sync breaks.'
when_to_use: Any time HR's attendance/health check sees an order row with status=OPEN,
  filled_amount=0, that does not appear in the live active-orders feed. Do not flag
  it as a desk incident.
created: '2026-09-18T00:24:26Z'
source: agent:zbot_hr
---

## Standing rule — cosmetic DB order artifact (owner-directed via Auditor, logged 2026-09-17)

An order row that is OPEN in the DB, filled_amount==0, and absent from the live venue
active-orders feed is a COSMETIC DB ARTIFACT — zero fill, zero margin, zero exposure. It is NOT
a sync break, NOT a desk incident, NOT grounds to flag a desk as unhealthy.

Known example: order_id 0xf036f8793f9c9391a23c1545fb5b717b, ETH-USD MARKET BUY 0.0163 @ 2585.80,
created 2026-09-17T16:43. API cannot mutate/cancel it (404 on cancel, no PATCH/PUT/DELETE
endpoint). Only a backend/manual DB purge clears it.

Still: note its presence/absence each attendance cycle (DB view vs live feed) as a one-line
transparency log — do not paper over it, but do not escalate it as an incident either.
