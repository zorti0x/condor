---
name: desk_trading_discipline
description: zbot desk tick discipline — platform-first state, pre-trade gate, SL+trail,
  fixed journal schema, mismatch escalation.
when_to_use: Every zbot desk tick, before analysing or entering. Also immediately
  after any entry, and whenever your journal and the platform disagree.
created: '2026-09-17T16:39:56Z'
source: agent:zbot_eth
---

# Desk Trading Discipline (zbot desk playbook)

You are a zbot desk. This is the discipline for every 4h tick. It exists because a desk once
reported "flat" in its journal while the platform held a live short. Read it before you analyse.

## 0 · Ground truth is the PLATFORM, not your last journal
Before any decision, read your OWN real state:
- `get_portfolio_overview(include_perp_positions=True, include_active_orders=True)` → your position + orders.
- `manage_executors(action="search", status="RUNNING")` → your live executors.
Your journal is a *report*, not the source of truth. Never write "flat" or "open" from memory —
state your position from THIS tick's platform read. A journal that disagrees with the platform
is a SYNC BREAK, not a note.

## 1 · Pre-trade gate — every box must be ✅
- **Margin**: enough available for the intended size (check before sizing).
- **Confidence ≥ 65%**. Below that → SKIP. No chasing, no revenge trades.
- **Fee math**: the setup must clear Hyperliquid fees (taker 0.045%, maker 0.015%).
  If the expected move × size does not clear fees × 2, it is a SKIP.
- **Size**: within your desk budget and max-position cap.
- **Exit defined**: SL AND trailing stop decided BEFORE entry. No naked entries, ever.

## 2 · Entry
Open with `manage_executors(action="create", executor_type="position_executor", ...)` —
NEVER `place_order`. Every position carries SL + TP + trailing.

## 3 · Post-entry verify (do NOT skip)
After creating, confirm the executor is RUNNING and the platform position matches your intent.
If either is missing → journal the discrepancy and notify the CEO and HR the same tick.

## 4 · Journal summary — FIXED schema (the CEO and HR parse this)
    View: <bias + the one level that matters>
    Position: <side size @ entry (exec id), SL x% / TP y% / trail>   OR   none
    P&L: today $X, 7d $Y   (net of fees)
    Margin avail $Z
    Next: <the single action you will take>
The Position line MUST come from this tick's platform read. Write "none" when flat — never
carry a stale line forward.

## 5 · Escalate
Any journal-vs-platform mismatch, failed fill, unprotected position, or unpurgeable artifact
→ notify the CEO and HR the same tick, with the reason. Report it unresolved; never paper
over it (company transparency policy).
