"""Verify the 4 running zbot loop agents picked up the new employee-incentive strategy instructions.

Continuous verifier for the zbot company. The strategy instructions for the four
running 4h loop instances (zbot_btc.btc_4h_desk_loop_1, zbot_eth.eth_4h_desk_loop_1,
zbot_sol.sol_4h_desk_loop_1, zbot_charts.pattern_desk_loop_1) were updated with a new
employee-reward/incentive line. This routine polls each instance's latest run snapshot
(starting after the projected next-tick window) and confirms the new wording is active —
either the new instruction text is present in the loaded System Prompt, or the agent's
own reasoning/actions mention the reward/review incentive. When all 4 are confirmed it
sends ONE Telegram summary and terminates; if a timeout is reached first it sends the
partial results and stops. Pure alerting/verification — never places orders or edits agents.
"""

CATEGORY = "Bot Analysis"

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.agents.journal import JournalManager, resolve_agent_dirs
from condor.reports import LiveReport

logger = logging.getLogger(__name__)

CONTINUOUS = True

# agent_id -> (kind, new-instruction marker phrases, reasoning-echo keywords)
AGENTS = {
    "zbot_btc.btc_4h_desk_loop_1": (
        "desk",
        ["performance is reviewed each cycle", "your desk grows"],
        ["reviewed each cycle", "incentive", "reward", "desk grows", "future budget"],
    ),
    "zbot_eth.eth_4h_desk_loop_1": (
        "desk",
        ["performance is reviewed each cycle", "your desk grows"],
        ["reviewed each cycle", "incentive", "reward", "desk grows", "future budget"],
    ),
    "zbot_sol.sol_4h_desk_loop_1": (
        "desk",
        ["performance is reviewed each cycle", "your desk grows"],
        ["reviewed each cycle", "incentive", "reward", "desk grows", "future budget"],
    ),
    "zbot_charts.pattern_desk_loop_1": (
        "chartist",
        ["reviewed each cycle on call quality", "your analyst status"],
        ["reviewed each cycle", "call quality", "analyst status", "incentive", "reward"],
    ),
}

CONFIRMED = "CONFIRMED"
PENDING = "pending"
ERROR = "error"
_SNAP_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[^\d]+(\d{2}:\d{2})")


class Config(BaseModel):
    """Verify the 4 zbot loop agents loaded the new incentive instructions; Telegram once all 4 confirm."""

    start_utc: str = Field(
        default="2026-09-15T22:40:00Z",
        description="Do not begin checking before this UTC instant (ISO8601, empty = start now). Projected next ticks: desks ~20:25, chartist ~22:28 UTC 2026-09-15.",
    )
    interval_min: int = Field(default=20, description="Poll interval in minutes (15-30 recommended)")
    max_hours: float = Field(default=24, description="Stop after this long even if not all confirmed")
    max_ticks: int = Field(default=0, description="0 = run until all confirmed / timeout; >0 = run N polls then stop (testing)")


def parse_utc(s: str):
    """ISO8601 UTC string -> aware datetime; None when blank/unparseable."""
    if not s or not s.strip():
        return None
    try:
        return datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def split_snapshot(snap):
    """Return (prompt_block, rest). rest = the reasoning/actions/journal area below the System Prompt."""
    idx = snap.find("## User Memory Index")
    if idx == -1:
        return snap, ""
    return snap[:idx], snap[idx:]


def check_snapshot(snap, markers, echo):
    """Confirm one run snapshot reflects the new incentive wording.

    loaded   -> a new-instruction phrase is present (definitive: the snapshot embeds
                the System Prompt that was loaded that tick, and old snapshots lack it).
    echoed   -> the agent's own reasoning/actions reference reward/review language.
    confirmed-> either signal is enough (matches the requirement).
    """
    prompt, rest = split_snapshot(snap)
    low = snap.lower()
    loaded = any(m.lower() in low for m in markers)
    echoed = any(k.lower() in rest.lower() for k in echo)
    return {"loaded": bool(loaded), "echoed": bool(echoed), "confirmed": bool(loaded or echoed)}


def snapshot_ts(snap):
    m = _SNAP_TS_RE.search(snap[:200])
    return f"{m.group(1)} {m.group(2)} UTC" if m else ""


def _latest_session_jm(aid):
    """JournalManager pinned to the highest-numbered session dir (robust to instance restarts)."""
    session_dir, agent_dir = resolve_agent_dirs(aid)
    if agent_dir is not None:
        sess_root = agent_dir / "sessions"
        try:
            if sess_root.exists():
                sdirs = [d for d in sess_root.iterdir() if d.is_dir() and re.fullmatch(r"session_\d+", d.name)]
                if sdirs:
                    sdirs.sort(key=lambda d: int(d.name.split("_")[1]))
                    return JournalManager(aid, session_dir=sdirs[-1], agent_dir=agent_dir)
        except OSError:
            pass
    return JournalManager(aid, session_dir=session_dir, agent_dir=agent_dir)


def _resolve_targets(context):
    """Best-effort (user_id, chat_id) for notifications; both may be None (headless run)."""
    chat_id = getattr(context, "_chat_id", None) or None
    user_id = getattr(context, "_user_id", None)
    if user_id is None and chat_id:
        try:
            from condor.notifications import user_for_chat

            user_id = user_for_chat(chat_id)
        except Exception:
            user_id = None
    return user_id, chat_id


async def _notify(context, text, kind="system"):
    """Send via condor.notifications — Telegram when a chat exists, bell otherwise. Never raises."""
    from condor.notifications import announce

    user_id, chat_id = _resolve_targets(context)
    try:
        delivery = await announce(user_id, chat_id, text, kind=kind)
        logger.info(
            "notified kind=%s sent=%s recorded=%s chat=%s user=%s",
            kind, delivery.sent, delivery.recorded, chat_id, user_id,
        )
        return delivery
    except Exception as e:
        logger.warning("notification failed (kind=%s): %s", kind, e)
        return None


def _poll(results, now_str):
    """Read one latest snapshot per not-yet-confirmed instance. Mutates results. Returns summary rows."""
    rows = []
    for aid, (kind, markers, echo) in AGENTS.items():
        st = results.setdefault(
            aid, {"kind": kind, "status": PENDING, "tick": None, "ts": "", "loaded": False, "echoed": False}
        )
        if st["status"] == CONFIRMED:
            rows.append(_row(aid, st, now_str))
            continue
        try:
            jm = _latest_session_jm(aid)
            runs = jm.list_runs(limit=5)
            ticks = [r.get("tick") for r in runs if isinstance(r, dict) and isinstance(r.get("tick"), int)]
            if not ticks:
                st.update(status=PENDING, tick=None, ts="")
                rows.append(_row(aid, st, now_str))
                continue
            t = max(ticks)
            snap = jm.read_run_snapshot(t)
            res = check_snapshot(snap, markers, echo)
            st.update(tick=t, ts=snapshot_ts(snap), loaded=res["loaded"], echoed=res["echoed"])
            st["status"] = CONFIRMED if res["confirmed"] else PENDING
        except Exception as e:
            logger.warning("poll error for %s: %s", aid, e, exc_info=True)
            st["status"] = ERROR
        rows.append(_row(aid, st, now_str))
    return rows


def _row(aid, st, now_str):
    return {
        "Instance": aid,
        "Kind": st.get("kind", "?"),
        "Status": st.get("status", PENDING),
        "Tick #": st.get("tick") if st.get("tick") is not None else "—",
        "Snapshot (UTC)": st.get("ts") or "—",
        "Loaded": "✓" if st.get("loaded") else "—",
        "Reasoning echo": "✓" if st.get("echoed") else "—",
        "Checked": now_str,
    }


def _status_line(aid, st):
    if st["status"] == CONFIRMED:
        return f"✅ {aid} — CONFIRMED (tick {st['tick']}, snapshot {st['ts']})"
    if st["status"] == ERROR:
        return f"❌ {aid} — check failed, will retry"
    return f"⏳ {aid} — not yet"


async def run(config: Config, context) -> str:
    interval = max(1, config.interval_min) * 60
    chat_id = getattr(context, "_chat_id", None) or None
    now = datetime.now(timezone.utc)

    start_dt = parse_utc(config.start_utc)
    if start_dt:
        start_dt = start_dt.astimezone(timezone.utc)

    results = {aid: {"kind": kind, "status": PENDING, "tick": None, "ts": "", "loaded": False, "echoed": False}
               for aid, (kind, _, _) in AGENTS.items()}

    report = LiveReport(
        "zbot incentive-instruction verifier",
        source_name="zbot_incentive_verify",
        tags=["zbot", "verification", "monitoring"],
        auto_refresh_seconds=interval,
    )

    persistent = config.max_ticks == 0

    # Wait window: do not spam-check before the projected next-tick window.
    if start_dt and now < start_dt:
        wait_sec = (start_dt - now).total_seconds()
        if persistent:
            await _notify(
                context,
                (
                    "🕓 zbot incentive verifier armed\n"
                    f"Verifying 4 loop instances loaded the new incentive wording.\n"
                    f"First check on/after {start_dt.strftime('%Y-%m-%d %H:%M UTC')} "
                    f"({int(wait_sec // 60)} min away), then every {config.interval_min} min.\n"
                    "Final verdict Telegrammed once all 4 confirm (or on timeout)."
                ),
                kind="system",
            )
            report.clear()
            report.builder.manual_order()
            report.builder.section("WAITING", "Before the projected next-tick window")
            report.builder.markdown(
                f"Routine armed. Waiting to begin checking until **{start_dt.strftime('%Y-%m-%d %H:%M UTC')}** "
                f"({int(wait_sec // 60)} min away), then polling every {config.interval_min} min for "
                f"{config.max_hours:g} h max."
            )
            await report.update()
        try:
            while True:
                wait_sec = (start_dt - datetime.now(timezone.utc)).total_seconds()
                if wait_sec <= 0:
                    break
                await asyncio.sleep(min(wait_sec, 3600.0))
        except asyncio.CancelledError:
            raise

    errors = 0
    ticks = 0
    confirmed_count = 0
    final_msg = None
    deadline = datetime.now(timezone.utc) + timedelta(hours=config.max_hours) if config.max_hours > 0 else None

    try:
        while True:
            try:
                now = datetime.now(timezone.utc)
                now_str = now.strftime("%H:%M")
                ticks += 1

                rows = _poll(results, now_str)
                confirmed_count = sum(1 for st in results.values() if st.get("status") == CONFIRMED)
                errors = sum(1 for st in results.values() if st.get("status") == ERROR)

                # Report block is intentionally unguarded (its failures must surface).
                report.clear()
                report.builder.manual_order()
                report.builder.section(
                    "01 / VERIFICATION STATUS",
                    f"Poll #{ticks} at {now.strftime('%Y-%m-%d %H:%M:%S')} UTC — new incentive wording active?",
                )
                report.builder.kpi("Confirmed", f"{confirmed_count}/4")
                report.builder.kpi("Pending", f"{4 - confirmed_count - errors}")
                report.builder.kpi("Check errors", str(errors))
                report.builder.table(rows, ["Instance", "Kind", "Status", "Tick #", "Snapshot (UTC)", "Loaded", "Reasoning echo", "Checked"])
                report.builder.markdown(
                    "_Confirmed_ = the new instruction phrase is present in the loaded System Prompt "
                    "(or the agent's reasoning echoes the incentive). ⏳ = instance has not ticked with "
                    "the new instructions yet. Snapshot (UTC) is the latest run snapshot's header time._"
                )
                await report.update()

                if confirmed_count == len(AGENTS):
                    lines = ["✅ zbot incentive instructions — ALL 4 instances verified"]
                    lines += [""] + [_status_line(aid, results[aid]) for aid in AGENTS]
                    lines.append("")
                    lines.append("Verifier terminating on full confirmation.")
                    final_msg = "\n".join(lines)
                    break

                timeout = deadline is not None and now >= deadline
                if timeout:
                    lines = [f"⏰ zbot incentive verifier — timeout after {config.max_hours:g} h ({confirmed_count}/4 confirmed)"]
                    lines += [""] + [_status_line(aid, results[aid]) for aid in AGENTS]
                    lines.append("")
                    lines.append("Verifier stopping; run it again to keep checking.")
                    final_msg = "\n".join(lines)
                    break

                if config.max_ticks and ticks >= config.max_ticks:
                    break

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("zbot_incentive_verify tick error: %s", e, exc_info=True)

            await asyncio.sleep(interval)

    except asyncio.CancelledError:
        # Left a fixed stopped snapshot; no final result message (it did not finish).
        report.clear()
        report.builder.auto_refresh(None)
        report.builder.section("STOPPED BY USER", "Final fixed snapshot (incomplete verification)")
        report.builder.kpi("Confirmed", f"{confirmed_count}/4")
        report.builder.table(
            [_row(aid, results[aid], datetime.now(timezone.utc).strftime("%H:%M")) for aid in AGENTS],
            ["Instance", "Kind", "Status", "Tick #", "Snapshot (UTC)", "Loaded", "Reasoning echo", "Checked"],
        )
        await report.update()
        return f"Stopped by user after {ticks} ticks; {confirmed_count}/4 confirmed"

    # Normal completion: all confirmed, timeout, or test max_ticks.
    if final_msg:
        await _notify(context, final_msg, kind="alert")
    report.clear()
    report.builder.auto_refresh(None)
    report.builder.section("VERIFIER FINISHED", "Final fixed snapshot")
    report.builder.kpi("Confirmed", f"{confirmed_count}/4")
    report.builder.kpi("Polls", str(ticks))
    report.builder.table(
        [_row(aid, results[aid], datetime.now(timezone.utc).strftime("%H:%M")) for aid in AGENTS],
        ["Instance", "Kind", "Status", "Tick #", "Snapshot (UTC)", "Loaded", "Reasoning echo", "Checked"],
    )
    await report.update()
    return f"zbot_incentive_verify finished: {confirmed_count}/4 confirmed after {ticks} polls"