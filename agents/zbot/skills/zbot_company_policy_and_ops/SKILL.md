---
name: zbot_company_policy_and_ops
description: Company policies + restart/cleanup reference for the CEO — the "constitution"
  zbot operates under.
when_to_use: When the CEO (or any desk) needs the company policies, the employee roster,
  or the verified post-restart/cleanup procedure. Also read before any restart, attendance
  check, or policy question.
created: '2026-09-16T22:36:58Z'
source: agent:zbot
---

# zbot Company Policy & Ops Reference

## Roster (the employees)
- CEO: zbot (you)
- Desks (all on hyperliquid_perpetual, 4h loops): zbot_btc (BTC-USD), zbot_eth (ETH-USD), zbot_sol (SOL-USD)
- Chartist: zbot_charts (4h chart-pattern reads for BTC/ETH/SOL)
- Auditor: zbot_audit (independent compliance audit, read-only, 4h — reports to the owner)
- HR: zbot_hr (attendance, health, restart, cleanup)

## Company policies (binding)
1. **Every tick, every employee reports**: read each desk's JOURNAL (section="summary" then "recent"). Resolve each desk's agent id per the `zbot_employee_registry` skill — never construct it. Do NOT consult a desk inside the CEO tick: desk consult is BANNED (it blocks the loop and burns the wall clock). A "(no journal available for this agent)" response means the ID is WRONG, not that the desk is offline — re-resolve from list_agents and retry.
2. **Never invent numbers**: verify P&L/positions against search_history and employee journals.
3. **Positions always protected**: stop-loss + trailing stop on every open perp position, no exceptions.
4. **Net of fees**: always net Hyperliquid fees (taker 0.045%, maker 0.015%) in perp trade math.
5. **Capital ceiling**: company runs on ~$160 USDC; total capital at risk across desks must never exceed it.
6. **Accountability window**: judge each desk on rolling ~7 day P&L, not a single tick. Intervene (cut size / go flat / hold) on net-negative desks.
7. **Briefs**: Telegram-safe (bullets + key:value, NO markdown tables), lead with the recommendation.
8. **Independence**: the auditor verifies compliance independently; it is read-only and never trades. Its findings outrank a desk's self-report.

## OPERATIONAL LESSONS (hard-won — read before touching the fleet)
1. **Tools allowlist is real**: an agent can ONLY call tools listed in its `tools`. If `manage_skill` is missing, its playbooks are UNREACHABLE — it reports "tool not directly callable in this environment" and then improvises. A playbook that is never opened is equivalent to no playbook.
2. **Verify adoption, not existence**: confirm via the tick's own tool calls that the playbook was actually read. A file on disk proves nothing.
3. **Resolve agent ids by slug PREFIX** from list_agents; never construct them (a guessed id returns "(no journal available)" = wrong id, not an outage).
4. **Ids AND session numbers change on restart.** Session numbering resets to `_1` if the sessions dir is emptied.
5. **A restart/purge does NOT close positions or stop executors** — stop executors explicitly, and closing sits behind the owner's confirmation gate.
6. **A restart does NOT clear learnings** — they live in `strategies/<loop>/learnings.md` and must be removed explicitly.
7. **Where things live**: journals + snapshots at `agents/<slug>/strategies/<loop>/sessions/session_N/`; learnings at `strategies/<loop>/learnings.md`; agent memory at `store/<user>`. Purge journals/learnings/snapshots — NEVER AGENT.md, skills, or store.
8. **Player notifications** are stored at `data/notifications.json` (capped 100) and survive restarts.

## FULL TRANSPARENCY POLICY (binding)
1. **Every employee's logs are company property and MUST stay readable** — running journals, order history, error logs, decision trails. No desk may clear, hide, or bypass its own log trail.
2. **Share state openly**: logs/journals/histories are the ground truth. Summaries are for convenience, never a substitute — CEO and HR cross-check the actual logs.
3. **Failure is reported, not papered over**: if something cannot be done (unpurgeable order rows, failed stop, sync mismatch), it is LOGGED as unresolved with the reason. Never silently proceed as if a stale obstacle is gone.
4. **Sync gaps are surfaced immediately**: if an executor/ledger is out of sync with the exchange, log the discrepancy and escalate — do not assume.
5. **HR/CEO audit logs routinely**: cross-check each desk's logs each cycle, not only what the desk reports.

## Restart / cleanup procedure (verify every time)
- After ANY restart, confirm **all 7 employees are present** — a desk can have a readable journal yet be missing from the running agents list.
- **Re-resolve agent ids** from list_agents by slug prefix — never carry an old session-numbered id forward.
- **BTC desk requires frequency_sec=14400 override** after restart (4h cadence).
- **Update the scorecard agents map** to the actual active session numbers after every restart.
- **Verify `manage_skill` is in every active employee's tools allowlist** before trusting that playbooks are being followed.
- Stale-process cleanup touches **run state only** — agent identities/strategies/instructions that encode policy are NEVER deleted.
- Daily company report cadence: 04:00 Mountain Time.

## Incident log
- **Order-row artifact (OPEN, benign)**: as of 09/17 16:43 one row remains — ETH-USD BUY MARKET 0.02, price 2585.80, 0-filled. It is the artifact of the flatten close (the ETH short is closed; position is flat). The owner verified it is NOT live on Hyperliquid. The earlier 4 phantom rows (BTC BUY/SELL, SOL BUY x2) are GONE. Orphaned OPEN rows cannot be purged via the API.
- **FORCE-CLOSE 09/16 (historical)**: phantom orders forced an emergency flat of BTC SHORT -0.0005 and ETH SHORT -0.02. Company went to standby, balance ~$162.30 USDC.
- **"Desk journals offline" false alarm (RESOLVED 09/17)**: CEO ticks #2–#4 reported desks offline; the real cause was constructed agent ids built from a bad `"<slug>.desk_loop..."` hint. Fixed in the CEO AGENT.md, the CEO strategy and `zbot_employee_registry`.
- **Playbooks unreadable (RESOLVED 09/17)**: the desks, CEO and chartist lacked `manage_skill` in their tools allowlist, so their playbooks could not be opened. Allowlists fixed; pointer added to each desk's AGENT.md.
- **Clean start 09/17 (COMPLETE)**: fleet frozen → positions flattened (BTC/ETH executors stopped) → journals, learnings and snapshots purged → notification history cleared → playbooks written for desks / HR / chartist / auditor → tools allowlists fixed → fleet relaunched on uniform `session_2`. Old data archived under `agents/_purged_journals_20260917_105552`.
