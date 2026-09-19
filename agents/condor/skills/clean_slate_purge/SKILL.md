---
name: clean_slate_purge
description: How to purge/reset state without missing artifacts — enumerate before
  deleting, verify against the filesystem not your own list, sweep by subsystem, re-clean
  after any relaunch.
when_to_use: 'Any request to purge, wipe, reset or clean-slate state: journals, sessions,
  snapshots, learnings, notifications, memories, or a whole agent/company restart.'
created: '2026-09-17T17:13:47Z'
source: chat
---

# Clean-Slate Purge

Written after missing leftover `snapshots/` and the chat notification store during a "purge everything" that reported itself CLEAN.

## Rule 1 — Enumerate BEFORE you delete
Never purge from a mental model of the layout. Walk the whole tree first and print every path + size:
`glob(root + "/**/*", recursive=True)`
Do it for **every** affected agent, not a sample — sampling 3 of 7 is exactly how per-agent differences and `store/` get missed.

## Rule 2 — Verify against the FILESYSTEM, not your own list
The "after" check must re-walk and assert emptiness **globally**. A check built from the paths you chose to delete is circular — it can only ever return CLEAN.

## Rule 3 — Sweep by SUBSYSTEM, not by directory
State lives in several unrelated stores. Name them and check each explicitly:
- agent journals + snapshots: `agents/<slug>/strategies/<loop>/sessions/session_N/`
- learnings: `agents/<slug>/strategies/<loop>/learnings.md`
- agent memory: `agents/<slug>/store/<user>/` (MEMORY.md, memories/*.md, audit.log)
- chat notifications: `data/notifications.json`
- delegations / task history; any `*.json` / `*.db` under `data/`
"I purged the journals" is not "I reset the state".

## Rule 4 — Re-clean anything YOU created after the purge
A purge is not one-shot. Every failed run, dry-run or pre-fix tick you trigger afterwards writes new state. If you hit a bug, restart and move on, the broken run stays on disk. Re-enumerate before declaring victory and remove runs you know are invalid.

## Rule 5 — Never state a limitation you have not tested
"I can't delete X" / "that can't be unsent" must be **verified**, not assumed. Check for the store first.

## Rule 6 — Back up, then MOVE; don't `rm`
Back up the store, move files to a timestamped archive, keep it until the user says hard-delete. Always report the archive path.

## Post-purge checklist
- [ ] full recursive inventory printed BEFORE deleting
- [ ] every subsystem enumerated explicitly
- [ ] moved to a timestamped archive (not deleted)
- [ ] after-check re-walks globally
- [ ] re-sweep after any relaunch / debug cycle
- [ ] stale docs, skills and memories updated to the new state
