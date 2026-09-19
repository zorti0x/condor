---
name: attendance_health_check
description: zbot HR daily attendance & health checklist — 7-person roster, id resolution,
  freshness/protection/sync checks, escalation format.
when_to_use: Every HR daily check, whenever a desk looks silent, and before any restart
  or attendance report.
created: '2026-09-17T16:39:56Z'
source: agent:zbot_hr
---

# HR — Attendance & Health Check (zbot)

## Roster — verify ALL of these (7)
- CEO: zbot
- Desks: zbot_btc (BTC-USD), zbot_eth (ETH-USD), zbot_sol (SOL-USD)
- Chartist: zbot_charts
- Auditor: zbot_audit
- HR: zbot_hr (you)

Each employee is a running agent INSTANCE. Ids look like `<slug>.<loop_name>_<session>`;
the loop name AND the session number CHANGE on every restart.

## Resolve ids — never construct
`manage_trading_agent(action="list_agents")`, then match by slug PREFIX: `zbot_btc.`,
`zbot_eth.`, `zbot_sol.`, `zbot_charts.`, `zbot_audit.`, `zbot_`. A bare slug or a guessed
loop name returns "(no journal available for this agent)" — that is a WRONG ID, not an
absence. Re-resolve before declaring anyone missing.

## Checklist per employee
1. **Present?** — in `list_agents`, status running.
2. **Journal fresh?** — last tick within its cadence + 1h (desks / CEO / charts / audit 4h; HR 24h).
3. **Journal real?** — content present; "(no journal available)" = wrong id, re-resolve.
4. **Position protected?** — for any live perp position, a RUNNING executor with SL (+trail) exists.
5. **State sync?** — journal position == platform position (`get_portfolio_overview`).
6. **Open items?** — unresolved incidents still logged, not silently dropped.

## Report
Daily, Telegram-safe (bullets + key:value, NO markdown tables): one line per employee
(tick #, journal age, position, protected?), then exceptions, then the single most urgent
action. Escalate a DOWN or UNPROTECTED employee IMMEDIATELY — don't wait for the digest.
Never invent a status; if a check can't be run, say so and mark it UNVERIFIED.
