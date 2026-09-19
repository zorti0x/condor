---
name: zbot_employee_registry
description: How to resolve zbot employee agent IDs (desks/chartist/HR) and read their
  journals — prevents false "(no journal available)" outages.
when_to_use: Every CEO coordination tick, any HR attendance/health check, any restart,
  and ANY time trading_agent_journal_read on a zbot employee returns "(no journal
  available for this agent)".
created: '2026-09-17T15:37:31Z'
source: agent:zbot
---

# zbot Employee Registry — resolving agent IDs

## The roster (employee → running agent instance)
Each employee is an agent; its RUNNING instance id has the form `<slug>.<loop_name>_<session>`:

- zbot_btc (BTC desk)      → zbot_btc.btc_4h_desk_loop_6
- zbot_eth (ETH desk)      → zbot_eth.eth_4h_desk_loop_5
- zbot_sol (SOL desk)      → zbot_sol.sol_4h_desk_loop_5
- zbot_charts (chartist)   → zbot_charts.pattern_desk_loop_5
- zbot_hr (HR)             → zbot_hr.daily_attendance_health_loop_4
- zbot (CEO)               → zbot.ceo_4h_coordination_loop_6

**THE LOOP NAME AND SESSION SUFFIX CHANGE ON EVERY RESTART.** The examples above are
illustrative PATTERNS, never values to copy forward.

## Rule: resolve, never construct
1. Call `manage_trading_agent(action="list_agents")`.
2. For the employee you want, take the running agent whose `agent_id` **STARTS WITH**
   `<slug>.` — an id starting with `zbot_btc.` is the BTC desk.
3. Pass that FULL id: `trading_agent_journal_read(agent_id="<full id>", section="summary")`.

Never pass a bare slug (`zbot_btc`) and never invent a loop name
(`zbot_btc.desk_loop_6`). Both return:
`('content': '(no journal available for this agent)')`

## Failure triage — the important part
`(no journal available for this agent)` means **THE ID IS WRONG** — it is the same
response a nonexistent agent id gets. It does NOT mean the desk is offline. On that response:
- re-run `list_agents`, re-match on the prefix, retry with the full id;
- do NOT report "desk journals offline" / "desk unavailable" to the owner;
- do NOT fall back to `consult()` — desk consult is BANNED in the CEO tick (it blocks
  the loop and times out);
- only if NO running agent matches the prefix is the employee genuinely DOWN.

## Journal sections
- `section="summary"` → last tick number, status, last action (one screen).
- `section="recent"`  → narrative of the last N ticks (use `max_entries`).
- `section="state"`   → current snapshot.

## Why this exists
The CEO tick reported "desk journals unavailable" for 3 consecutive cycles (session 6,
ticks #2–#4) and held capital, while the journals were readable the whole time. Root
cause: the tick playbook said ids looked like `"<slug>.desk_loop..."` and the agent built
`zbot_btc.desk_loop_6` from that pattern. Fixed 2026-09-17 in the CEO AGENT.md, the CEO
strategy and the company policy skill.
