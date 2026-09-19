"""zbot company cross-cycle performance review scorecard.

Read-only evaluation of the zbot trading company (BTC/ETH/SOL desks, chartist,
HR, CEO) aggregated over a configurable review window from each agent's
journal + tracker data (JournalManager). Produces the employee_review_scorecard
structure: a per-employee card (net P&L, win rate, R:R, rule-compliance gate,
chartist call quality, verdict) plus a one-line company summary sheet.

NEVER trades or moves capital. Numbers are parsed best-effort from desk
journals; the verbatim desk narrative is always shown as the authoritative
source. Agents with no data appear with zeros, not errors.

SELF-CORRECTING ROSTER: the roster is keyed by RUN_KEY (strategy id WITH the
session number stripped), which never changes across restarts. At build time
each employee's CURRENT session agent_id is resolved from the on-disk session
registry (condor.agents.sessions_index), so the map no longer goes stale when
the zbot company is restarted and session numbers bump.

Continuous routine: recomputes every interval_hours and refreshes a live report.
"""
import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from condor.agents import JournalManager
from condor.agents.sessions_index import infer_latest_session_status, list_sessions
from condor.reports import LiveReport

logger = logging.getLogger(__name__)

CATEGORY = "Bot Analysis"
CONTINUOUS = True

# Staff roster keyed by RUN_KEY = strategy id WITHOUT the session number.
# run_key is stable across restarts — only the appended session number bumps
# (…_loop_5 -> …_loop_6) — so build_agents() re-resolves the current agent_id.
STAFF: dict[str, dict[str, Any]] = {
    "zbot.ceo_4h_coordination_loop": {"name": "CEO", "role": "ceo", "icon": "💼"},
    "zbot_btc.btc_4h_desk_loop": {
        "name": "BTC Desk", "role": "desk", "pair": "BTC-USD",
        "budget": 80.0, "max_pos": 100.0, "icon": "🟠",
    },
    "zbot_eth.eth_4h_desk_loop": {
        "name": "ETH Desk", "role": "desk", "pair": "ETH-USD",
        "budget": 40.0, "max_pos": 60.0, "icon": "🔵",
    },
    "zbot_sol.sol_4h_desk_loop": {
        "name": "SOL Desk", "role": "desk", "pair": "SOL-USD",
        "budget": 40.0, "max_pos": 60.0, "icon": "🟣",
    },
    "zbot_charts.pattern_desk_loop": {"name": "Chartist", "role": "analyst", "icon": "📐"},
    "zbot_hr.daily_attendance_health_loop": {"name": "HR", "role": "hr", "icon": "🧑‍⚖️"},
}

# LAST-RESORT fallback ONLY. build_agents() normally resolves the current
# session numbers live from the registry; this hardcoded, session-numbered map
# is used only when a strategy dir is unreadable on disk. MUST be refreshed
# whenever zbot sessions bump (it is still the stale-map failure mode, so do
# not let it grow — discovery is the source of truth).
DEFAULT_AGENTS: dict[str, dict[str, Any]] = {
    "zbot.ceo_4h_coordination_loop_5": {"name": "CEO", "role": "ceo", "icon": "💼"},
    "zbot_btc.btc_4h_desk_loop_5": {
        "name": "BTC Desk", "role": "desk", "pair": "BTC-USD",
        "budget": 80.0, "max_pos": 100.0, "icon": "🟠",
    },
    "zbot_eth.eth_4h_desk_loop_4": {
        "name": "ETH Desk", "role": "desk", "pair": "ETH-USD",
        "budget": 40.0, "max_pos": 60.0, "icon": "🔵",
    },
    "zbot_sol.sol_4h_desk_loop_4": {
        "name": "SOL Desk", "role": "desk", "pair": "SOL-USD",
        "budget": 40.0, "max_pos": 60.0, "icon": "🟣",
    },
    "zbot_charts.pattern_desk_loop_4": {"name": "Chartist", "role": "analyst", "icon": "📐"},
    "zbot_hr.daily_attendance_health_loop_3": {"name": "HR", "role": "hr", "icon": "🧑‍⚖️"},
}


class Config(BaseModel):
    """zbot cross-cycle performance review scorecard. Read-only — never trades."""
    window_cycles: int = Field(default=6, description="Review window: cycles (ticks) to aggregate")
    interval_hours: float = Field(default=24, description="Recompute cadence, hours")
    cycle_hours: float = Field(default=4, description="Assumed cycle length (h) for the window label")
    notify: bool = Field(default=True, description="Send a concise company summary to Telegram")
    write_ceo: bool = Field(default=True, description="Append the review summary to the CEO journal")
    agents: dict[str, dict[str, Any]] | None = Field(
        default=None,
        description="agent_id -> review profile. Explicit override, wins when supplied; "
                    "leave empty/None to auto-resolve current session ids from the agent registry.",
    )


# ---------------------------------------------------------------------------
# Roster resolution — self-correcting agent_id discovery
# ---------------------------------------------------------------------------
def _resolve_current_agent_id(run_key: str) -> str | None:
    """Current session's agent_id for a run_key, from the on-disk session registry.

    Prefers the latest session recorded as 'running'; falls back to the newest
    session by number. Returns None only if the strategy dir is missing.
    """
    slug, sep, sslug = run_key.partition(".")
    if not sep:
        return None
    sdir = Path("agents") / slug / "strategies" / sslug
    if not sdir.exists():
        logger.warning("zbot review: strategy dir missing for %s (%s)", run_key, sdir)
        return None
    try:
        latest = infer_latest_session_status(sdir, run_key)
        if latest:
            if latest.get("status") == "running":
                return latest["agent_id"]
            if latest.get("agent_id"):
                return latest["agent_id"]
        sessions = list_sessions(sdir)
        if sessions:
            nums = [s["number"] for s in sessions if s.get("number")]
            if nums:
                return f"{run_key}_{max(nums)}"
    except Exception as e:
        logger.warning("zbot review: session discovery failed for %s: %s", run_key, e)
    return None


def build_agents(override: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """agent_id -> review profile.

    An explicit `override` (the `agents` config field) wins verbatim; otherwise
    each staff run_key is resolved to its CURRENT session agent_id from the
    live session registry, with DEFAULT_AGENTS as a documented last-resort fallback.
    """
    if override:
        return {str(k): dict(v) for k, v in override.items()}
    agents: dict[str, dict[str, Any]] = {}
    for run_key, prof in STAFF.items():
        aid = _resolve_current_agent_id(run_key)
        if not aid:
            aid = next((k for k in DEFAULT_AGENTS if k.startswith(run_key + "_")), None)
        if aid:
            agents[aid] = dict(prof)
    return agents


# ---------------------------------------------------------------------------
# Parsing — best-effort numbers out of desk journal narratives
# ---------------------------------------------------------------------------
_NUM = re.compile(r"[-+]?\$?\s?\d[\d,]*(?:\.\d+)?")


def _tokens(text: str) -> list[tuple[str, float]]:
    out = []
    for m in _NUM.finditer(text or ""):
        t = m.group(0).replace(" ", "")
        try:
            num = float(t.replace("$", "").replace(",", ""))
        except ValueError:
            continue
        out.append((t, num))
    return out


def _find_after(text: str, kw: str, window: int = 70) -> str:
    idx = (text or "").lower().find(kw.lower())
    if idx < 0:
        return ""
    return text[idx + len(kw): idx + len(kw) + window]


def _first_money(seg: str, require_signed: bool = False, window: int = 0) -> float | None:
    if window:
        seg = seg[:window]
    for t, num in _tokens(seg):
        clean = re.sub(r"[^\d]", "", t)
        if clean.endswith(("d", "h")):  # "7d", "1h"
            continue
        signed = ("$" in t) or ("+" in t) or (t.startswith("-"))
        if require_signed and not signed:
            continue
        return num
    return None


def parse_pnl(st: str) -> dict[str, Any]:
    """Extract realized / unrealized / total / 7d / today P&L from a desk summary."""
    st = st or ""
    realized = _first_money(_find_after(st, "realized"), require_signed=True)
    unrealized = _first_money(_find_after(st, "uPnL"), require_signed=True)
    if unrealized is None:
        unrealized = _first_money(_find_after(st, "unrealized"), require_signed=True)
    if unrealized is None:
        # tight window so "open +$0.36" wins but "open 0.013@…trail1.2-0.8" does not
        unrealized = _first_money(_find_after(st, "open "), require_signed=True, window=24)
    if unrealized is None:
        unrealized = _first_money(_find_after(st, "PnL +"), require_signed=True, window=12)
    total = _first_money(_find_after(st, "total"), require_signed=False)
    if total is None:
        total = _first_money(_find_after(st, "≈"), require_signed=True) \
            or _first_money(_find_after(st, "approx"), require_signed=True)
    if total is None:
        total = (realized or 0.0) + (unrealized or 0.0)
    seven = _first_money(_find_after(st, "7d"), require_signed=True)
    today = _first_money(_find_after(st, "today"), require_signed=True)

    def _first_non_none(*vals):
        for v in vals:
            if v is not None:
                return v
        return None

    return {
        "realized": realized,
        "unrealized": unrealized,
        "total": total,
        "net7d": _first_non_none(seven, total, 0.0),
        "today": today,
    }


def compliance_flags(text: str) -> dict[str, bool | None]:
    st = text or ""
    return {
        "sl": bool(re.search(r"\bSL\d|\bSL\b|stop[\s-]?loss", st, re.I)),
        "trailing": bool(re.search(r"\btrail", st, re.I)),
        "no_revenge": "revenge" not in st.lower(),
        "fees_netted": True if re.search(r"\b(netted|net of|after fees)\b", st, re.I) else None,
    }


def _ex_pnl(e: dict) -> float:
    try:
        return float(e.get("pnl", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _narrative_notional(st: str) -> float | None:
    """Estimate open-position notional from 'SHORT 0.001 @76014' / 'SHORT open 0.013@2406.7' lines."""
    m = re.search(r"\b(LONG|SHORT)[^\d@]{0,20}([\d.]+)\s*@\s*([\d.,]+)", st or "", re.I)
    if not m:
        return None
    try:
        return float(m.group(2)) * float(m.group(3).replace(",", ""))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Collection — one record per agent, robust to missing data
# ---------------------------------------------------------------------------
def collect(aid: str, prof: dict, window_cycles: int) -> dict[str, Any]:
    jm = JournalManager(aid)
    sd = jm.get_summary_dict() or {}
    st = jm.read_summary() or ""
    recent = jm.read_recent(max(10, window_cycles * 3)) or ""
    learn = jm.read_learnings() or ""
    runs = jm.list_runs(window_cycles) or []
    exs = jm._parse_executors() or []

    budget = prof.get("budget")
    cap = prof.get("max_pos") or budget
    role = prof.get("role", "")

    pnl = parse_pnl(st)
    closed = [e for e in exs if str(e.get("status", "")).lower() == "closed"]
    open_exs = [e for e in exs if str(e.get("status", "")).lower() == "open"]
    wins = [e for e in closed if _ex_pnl(e) > 0]
    losses = [e for e in closed if _ex_pnl(e) < 0]
    avg_win = sum(_ex_pnl(e) for e in wins) / len(wins) if wins else None
    avg_loss = sum(_ex_pnl(e) for e in losses) / len(losses) if losses else None
    rr = (avg_win / abs(avg_loss)) if (avg_win is not None and avg_loss) else None

    net_pnl = float(pnl["net7d"] or 0.0)
    pnl_pct = (net_pnl / budget * 100.0) if budget else None

    # budget/cap check: executor ledger amount, else notional parsed from narrative
    pos_amt = None
    for e in open_exs:
        try:
            pos_amt = float(str(e.get("amount", "0")).lstrip("$"))
        except (TypeError, ValueError):
            continue
    if pos_amt is None:
        pos_amt = _narrative_notional(st)
    budget_ok = (pos_amt <= cap) if (pos_amt is not None and cap) else None

    comp = compliance_flags(st + " " + recent)
    comp["budget_ok"] = budget_ok
    comp["position_notional"] = pos_amt

    if role == "desk":
        gate_vals = [comp["sl"], comp["trailing"], comp["no_revenge"], comp["budget_ok"]]
        gate = "PASS" if all(v is True for v in gate_vals) else \
            ("FAIL" if any(v is False for v in gate_vals) else "PENDING")
    else:
        gate = "n/a"

    rec = {
        "agent_id": aid,
        "name": prof.get("name", aid),
        "role": role,
        "pair": prof.get("pair"),
        "icon": prof.get("icon", ""),
        "budget": budget,
        "cap": cap,
        "ticks": int(sd.get("total_ticks", 0)),
        "cycles_in_window": max(1, len(runs)) if runs else (1 if st else 0),
        "open_executors": int(sd.get("open_executors", 0)),
        "open_ledger": len(open_exs),
        "closed_trades": len(closed),
        "executor_count": len(exs),
        "narrative": st.strip(),
        "raw_pnl": pnl,
        "net_pnl": net_pnl,
        "pnl_pct_budget": pnl_pct,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "rr": rr,
        "win_rate": (len(wins) / len(closed)) if closed else None,
        "compliance": comp,
        "gate": gate,
        "learnings_note": learn.strip()[-400:],
        "executors": exs,
    }

    if role == "analyst":
        full_text = st + " " + recent
        try:
            full_text = jm.read_full() or ""
        except Exception:
            pass
        rec["briefs"] = len(re.findall(r"\bbrief", full_text, re.I))
        rec["bias_statements"] = len(re.findall(r"\b(bullish|bearish|neutral)\b", full_text, re.I))
        rec["bias_outcomes"] = len(re.findall(r"\b(correct|paid off|acted on|confirmed|respected)\b", full_text, re.I))
    return rec


# ---------------------------------------------------------------------------
# Verdict + rendering
# ---------------------------------------------------------------------------
def _tf(v: bool | None) -> str:
    if v is None:
        return "⚠️ unknown"
    return "✅" if v else "❌"


def rate(rec: dict) -> str:
    role, gate = rec.get("role"), rec.get("gate")
    if role != "desk":
        return "Keep" if rec.get("ticks", 0) > 0 else "Watch (no cycles yet)"
    pnl = rec.get("net_pnl") or 0.0
    budget = rec.get("budget") or 0.0
    if gate == "FAIL":
        return "Improve"
    if pnl <= -0.5 * budget:
        return "Cut"
    if pnl < 0:
        return "Watch"
    if rec.get("closed_trades", 0) == 0:
        return "Watch (no closed trades yet)"
    return "Keep"


def render_card(aid: str, rec: dict, cfg: Config) -> dict[str, str]:
    role = rec.get("role", "")
    icon = rec.get("icon", "")
    window_label = f"last {cfg.window_cycles} cycles (~{cfg.window_cycles * cfg.cycle_hours:.0f}h)"
    lines = []
    lines.append(f"## {icon} {rec.get('name')}  (`{aid}`)  ·  {role}")
    lines.append(f"**Review period:** {window_label} · Cycles in window: {rec.get('cycles_in_window', 0)}")
    lines.append("")
    if rec.get("error"):
        lines.append(f"⚠️ Could not read journal: {rec['error']}")
        lines.append("")
        lines.append("### 6 · Verdict")
        lines.append("- Rating: **Watch (no data)**")
        return {"markdown": "\n".join(lines)}

    if role == "desk":
        p = rec.get("raw_pnl", {}) or {}
        lines.append("### 1 · Trading performance")
        lines.append(f"- Net P&L (7d, parsed from desk journal): **${rec.get('net_pnl', 0.0):+.2f}** "
                     f"· today {('$%+.2f' % p['today']) if p.get('today') is not None else 'n/a'}")
        lines.append(f"- Realized {('$%+.2f' % p['realized']) if p.get('realized') is not None else 'n/a'} · "
                     f"Unrealized {('$%+.2f' % p['unrealized']) if p.get('unrealized') is not None else 'n/a'} · "
                     f"Total {('$%+.2f' % p['total']) if p.get('total') is not None else 'n/a'}")
        if rec.get("budget"):
            lines.append(f"- P&L as % of budget: **{rec.get('pnl_pct_budget', 0.0):+.1f}%** (budget ${rec['budget']:,.0f})")
        if rec.get("closed_trades"):
            wr = rec.get("win_rate")
            lines.append(f"- Win rate: **{wr * 100:.0f}%** ({rec.get('closed_trades')} closed trades)" if wr is not None
                         else f"- Win rate: n/a ({rec.get('closed_trades')} closed)")
            rr = rec.get("rr")
            lines.append(f"- Avg win vs avg loss (R:R achieved): **{rr:.2f} : 1**" if rr is not None
                         else "- Avg win vs avg loss (R:R achieved): n/a")
            lines.append(f"- Avg win ${rec.get('avg_win', 0):+.2f} · Avg loss ${rec.get('avg_loss', 0):+.2f}")
        else:
            lines.append("- Win rate: n/a (no closed executor records in window yet)")
            lines.append("- Avg win vs avg loss (R:R achieved): n/a (not enough closed trades)")
        lines.append("")
        lines.append("### 2 · Rule compliance (GATE — blocks bonus if any ❌)")
        c = rec.get("compliance", {})
        lines.append(f"- Stop-loss on every entry: {_tf(c.get('sl'))}")
        lines.append(f"- Trailing stop per company policy: {_tf(c.get('trailing'))}")
        amt = c.get("position_notional")
        cap = rec.get("cap") or rec.get("budget")
        lines.append(f"- Within budget & max position cap: {_tf(c.get('budget_ok'))}"
                     + (f"  (notional {('$%.2f' % amt) if amt is not None else 'n/a'} / cap ${cap:,.0f})" if cap else ""))
        lines.append(f"- No revenge / forced trades: {_tf(c.get('no_revenge'))}")
        lines.append(f"- Fees netted before sizing: {_tf(c.get('fees_netted'))}")
        lines.append(f"**GATE: {rec.get('gate')}**" + (" — bonus zeroed this cycle" if rec.get("gate") == "FAIL" else ""))
    elif role == "analyst":
        lines.append("### 3 · Chartist call quality")
        lines.append(f"- Pattern briefs published: **{rec.get('briefs', 0)}** (heuristic count of 'brief' mentions)")
        lines.append(f"- Bias statements (bullish/bearish/neutral): {rec.get('bias_statements', 0)}")
        lines.append(f"- Outcome-cue mentions (correct/paid off/confirmed): {rec.get('bias_outcomes', 0)}")
        lines.append("- Bias-correct vs price move / key-levels respected: n/a (needs per-cycle price outcomes)")
    elif role == "ceo":
        lines.append("### 4 · CEO coordination")
        lines.append("- Company net P&L under tenure: see company summary below")
        lines.append("- Capital allocation quality / desks consulted / interventions: qualitative — see journal notes")
    else:  # hr
        lines.append("### 5 · Attendance & health")
        lines.append(f"- Health-check cycles run: **{rec.get('ticks', 0)}** · no trading role")

    lines.append("")
    lines.append("### 5 · Qualitative notes")
    if rec.get("narrative"):
        lines.append(f"> {rec['narrative'][:500]}")
    else:
        lines.append("_No journal narrative yet._")
    if rec.get("learnings_note"):
        lines.append("")
        lines.append("**Learnings tail:**")
        lines.append(f"> {rec['learnings_note'][:300]}")
    lines.append("")
    lines.append("### 6 · Verdict")
    lines.append(f"- Rating: **{rate(rec)}** (first-pass heuristic; owner overrides)")
    lines.append(f"- One-line: {rec.get('net_pnl', 0.0):+.2f} net, gate {rec.get('gate')}, {rec.get('closed_trades', 0)} closed trades")
    return {"markdown": "\n".join(lines)}


def build_company(records: dict[str, dict]) -> tuple[list[dict], str]:
    rows = []
    desk_total = 0.0
    for aid, rec in records.items():
        role = rec.get("role", "")
        if role == "desk":
            desk_total += rec.get("net_pnl", 0.0) or 0.0
        wr = rec.get("win_rate")
        wr_s = f"{wr*100:.0f}%" if wr is not None else "—"
        rows.append({
            "Staff": rec.get("name", aid),
            "Role": role,
            "Net P&L": f"${rec.get('net_pnl', 0.0):+.2f}",
            "% Budget": f"{rec.get('pnl_pct_budget', 0.0):+.1f}%" if rec.get("pnl_pct_budget") is not None else "—",
            "Trades": str(rec.get("closed_trades", 0)),
            "Win%": wr_s,
            "Gate": rec.get("gate", "—"),
            "Rating": rate(rec),
        })
    md = [f"- Desks net (parsed): **${desk_total:+.2f}**"]
    for r in rows:
        md.append(f"- {r['Staff']}: {r['Net P&L']} · gate {r['Gate']} · {r['Rating']}")
    return rows, "\n".join(md)


def build_scorecard(cfg: Config) -> dict[str, Any]:
    agents = build_agents(cfg.agents)
    records: dict[str, dict] = {}
    for aid, prof in agents.items():
        try:
            records[aid] = collect(aid, prof, cfg.window_cycles)
        except Exception as e:  # one broken journal must not kill the review
            records[aid] = {
                "agent_id": aid, "name": prof.get("name", aid), "role": prof.get("role", ""),
                "icon": prof.get("icon", ""), "error": f"{type(e).__name__}: {e}",
                "net_pnl": 0.0, "closed_trades": 0, "ticks": 0, "gate": "—",
            }
    rows, summary_md = build_company(records)
    cards = [render_card(aid, rec, cfg) for aid, rec in records.items()]

    notify_lines = []
    for aid, rec in records.items():
        if rec.get("role") == "desk" and not rec.get("error"):
            g = {"PASS": "✅", "FAIL": "❌", "PENDING": "⚠️"}.get(rec.get("gate"), "—")
            notify_lines.append(f"{rec['name']} ${rec.get('net_pnl', 0.0):+.2f} {g}")
    notify = (f"🏢 zbot review — last {cfg.window_cycles} cycles\n"
              + " · ".join(notify_lines) if notify_lines else "🏢 zbot review — no desk data yet")
    return {"records": records, "rows": rows, "summary_md": summary_md,
            "cards": cards, "notify": notify,
            "window_label": f"last {cfg.window_cycles} cycles (~{cfg.window_cycles * cfg.cycle_hours:.0f}h)"}


# ---------------------------------------------------------------------------
# Run (continuous)
# ---------------------------------------------------------------------------
async def run(config: Config, context) -> str:
    chat_id = getattr(context, "_chat_id", None)

    report = LiveReport(
        "zbot Performance Review",
        source_name="zbot_performance_review",
        tags=["zbot", "scorecard", "review"],
        auto_refresh_seconds=60 if config.interval_hours > 4 else None,
    )

    def _latest_tick(ceo_aid: str) -> int:
        try:
            return int(JournalManager(ceo_aid).get_summary_dict().get("total_ticks", 0))
        except Exception:
            return 0

    async def _one_pass():
        sc = build_scorecard(config)
        report.clear()
        b = report.builder
        b.source("routine", "zbot_performance_review")
        b.tags(["zbot", "scorecard", "review"])
        b.section("01 / COMPANY SUMMARY", "Window: " + sc["window_label"] + " · read-only, from agent journals + trackers")
        desk_total = sum(r.get("net_pnl", 0.0) or 0.0 for r in sc["records"].values() if r.get("role") == "desk")
        open_pos = sum(r.get("open_ledger", 0) for r in sc["records"].values())
        b.kpi("Desks net P&L", f"${desk_total:+.2f}")
        b.kpi("Closed trades", f"{sum(r.get('closed_trades', 0) for r in sc['records'].values())}")
        b.kpi("Open positions (ledger)", f"{open_pos}")
        b.table(sc["rows"], ["Staff", "Role", "Net P&L", "% Budget", "Trades", "Win%", "Gate", "Rating"])
        b.markdown("**One-line summary**\n" + sc["summary_md"])
        b.section("02 / PER-EMPLOYEE SCORECARDS", "employee_review_scorecard structure")
        for card in sc["cards"]:
            b.markdown(card["markdown"])
        await report.update()

        ceo_aid = next((a for a, p in build_agents(config.agents).items() if p.get("role") == "ceo"), None)
        if config.write_ceo and ceo_aid:
            try:
                jm_ceo = JournalManager(ceo_aid)
                jm_ceo.append_action(
                    tick=_latest_tick(ceo_aid),
                    action=f"[review] {sc['notify']}",
                    reasoning="Automated zbot cross-cycle performance review (read-only)",
                )
            except Exception as e:
                logger.warning("CEO journal write failed: %s", e)

        if config.notify and chat_id:
            try:
                await context.bot.send_message(chat_id=chat_id, text=sc["notify"])
            except Exception as e:
                logger.warning("notify failed: %s", e)

    try:
        await _one_pass()
        while True:
            await asyncio.sleep(config.interval_hours * 3600)
            try:
                await _one_pass()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("review tick failed: %s", e)
    except asyncio.CancelledError:
        try:
            sc = build_scorecard(config)
            report.clear()
            report.builder.source("routine", "zbot_performance_review")
            report.builder.auto_refresh(None)
            report.builder.section("REVIEW STOPPED", "Final fixed snapshot")
            report.builder.markdown(sc["notify"])
            await report.update()
        except Exception:
            pass
        return "zbot_performance_review stopped"
    return "zbot_performance_review running"