"""
zbot_company_dashboard — live-updating company overview for the 7-employee
autonomous trading company (zbot) on the local Hyperliquid server.

Continuous routine. Every tick (default 60s):
  - lists every running trading-agent engine (employee)
  - fetches the Hyperliquid balance from portfolio
  - rebuilds the 'zbot Company Overview — 7 Employees' report in place,
    re-saving with the SAME report_id so browsers reload live data.
"""

import asyncio
import logging
from datetime import datetime, timezone

import plotly.graph_objects as go
from pydantic import BaseModel, Field

from config_manager import get_client
from condor.reports import LiveReport
from condor.fetchers.portfolio import fetch_portfolio

logger = logging.getLogger(__name__)

CATEGORY = "Monitoring"

CONTINUOUS = True  # live dashboard, updated in place until stopped

ROLE_BY_SLUG = {
    "zbot": "CEO",
    "zbot_btc": "BTC Desk",
    "zbot_eth": "ETH Desk",
    "zbot_sol": "SOL Desk",
    "zbot_charts": "Chartist",
    "zbot_audit": "Auditor",
    "zbot_hr": "HR",
}

# Canonical display order — unknown/surprise engines sort to the end.
DESK_ORDER = ["zbot", "zbot_btc", "zbot_eth", "zbot_sol", "zbot_charts", "zbot_audit", "zbot_hr"]


class Config(BaseModel):
    """Live zbot company dashboard: 7 employees, updated in place every 60s."""
    interval_sec: int = Field(default=60, description="Refresh interval (seconds) — sets both the loop cadence and the browser auto-refresh")


def _fmt_cadence(freq_sec):
    if not freq_sec:
        return "—"
    if freq_sec % 86400 == 0:
        return f"{freq_sec // 86400}d"
    if freq_sec % 3600 == 0:
        return f"{freq_sec // 3600}h"
    if freq_sec % 60 == 0:
        return f"{freq_sec // 60}m"
    return f"{freq_sec}s"


def _fmt_ts(ts):
    if not ts:
        return "—"
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S UTC")
        if isinstance(ts, datetime):
            return ts.astimezone(timezone.utc).strftime("%H:%M:%S UTC")
        return str(ts)
    except Exception:
        return str(ts)


def _collect_employees():
    """List every running trading-agent engine (employee) with its info dict."""
    from condor.agents.engine import get_all_engines
    rows = []
    for agent_id, eng in get_all_engines().items():
        agent = getattr(eng, "agent", None)
        slug = agent.slug if agent else agent_id.split(".")[0]
        name = agent.name if agent else slug
        info = eng.get_info()
        rows.append({
            "agent_id": agent_id,
            "slug": slug,
            "name": name,
            "role": ROLE_BY_SLUG.get(slug, name),
            "strategy": info.get("strategy", ""),
            "model": info.get("agent_key") or "—",
            "cadence": _fmt_cadence(info.get("frequency_sec")),
            "frequency_sec": info.get("frequency_sec") or 0,
            "state": info.get("status", "unknown"),
            "tick_count": info.get("tick_count") or 0,
            "session_num": info.get("session_num") or 0,
            "daily_pnl": info.get("daily_pnl") or 0.0,
            "realized_pnl": info.get("realized_pnl") or 0.0,
            "unrealized_pnl": info.get("unrealized_pnl") or 0.0,
            "budget": info.get("total_amount_quote") or 0.0,
            "open_executors": info.get("open_executors") or 0,
            "total_exposure": info.get("total_exposure") or 0.0,
            "last_error": info.get("last_error") or "",
            "last_tick_at": info.get("last_tick_at"),
            "tick_timeout_sec": info.get("tick_timeout_sec"),
            "max_ticks": info.get("max_ticks") or 0,
            "execution_mode": info.get("execution_mode") or "loop",
        })
    order = {s: i for i, s in enumerate(DESK_ORDER)}
    rows.sort(key=lambda r: order.get(r["slug"], 99))
    return rows


async def _hl_balance(client):
    """Sum of hyperliquid_perpetual balances in USD. None when unavailable."""
    if client is None:
        return None
    try:
        st = await fetch_portfolio(client)
        acct = st.get("master_account", {}) if isinstance(st, dict) else {}
        hl = acct.get("hyperliquid_perpetual", []) or []
        tot = sum(float(b.get("value") or 0.0) for b in hl if isinstance(b, dict))
        return tot or 0.0
    except Exception as exc:
        logger.warning("zbot_company_dashboard: portfolio fetch failed: %s", exc)
        return None


def _render(report, employees, hl_balance, interval_sec):
    """Rebuild the report body. No try/except here — a report bug must fail loudly.
    The builder is already fresh (LiveReport.clear() was called); we only compose."""
    b = report.builder
    b.manual_order()

    total = len(employees)
    running = sum(1 for e in employees if e["state"] == "running")
    open_pos = sum(e["open_executors"] for e in employees)
    cap_at_risk = sum(e["total_exposure"] or 0.0 for e in employees)
    errors = [e for e in employees if e.get("last_error")]

    if total and running == total:
        health = f"✔ all {total} online"
    else:
        health = f"⚠ {total - running} of {total} not running"
    if errors:
        health += f" · {len(errors)} with last error"
    session = "session_" + str(max((e["session_num"] or 0) for e in employees)) if employees else "—"
    hl_str = "—" if hl_balance is None else f"${hl_balance:,.2f}"

    # 01 — company at a glance
    b.section("01 / COMPANY AT A GLANCE", "Company-level KPIs for the zbot trading company")
    b.kpi("Employees online", f"{running}/{total}")
    b.kpi("Health verdict", health)
    b.kpi("Open positions", str(open_pos))
    b.kpi("Capital at risk", f"${cap_at_risk:,.2f}")
    b.kpi("HL balance (USD)", hl_str)
    b.kpi("Session", session)

    # 02 — employee roster
    b.section("02 / EMPLOYEE ROSTER", "Every running trading-agent engine on the local server")
    roster = [
        {
            "Role": e["role"], "Agent": e["slug"], "Model": e["model"],
            "Cadence": e["cadence"], "State": e["state"], "Ticks": e["tick_count"],
            "Playbook": e["strategy"],
        }
        for e in employees
    ]
    b.table(roster, ["Role", "Agent", "Model", "Cadence", "State", "Ticks", "Playbook"])

    # 03 — capital allocation (budget per desk vs the ceiling)
    b.section("03 / CAPITAL ALLOCATION", "Desk budget (total_amount_quote) vs the company ceiling")
    ceiling = sum(e["budget"] or 0.0 for e in employees)
    desks = [e["slug"] for e in employees]
    budgets = [e["budget"] or 0.0 for e in employees]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=desks, y=budgets, name="Desk budget", marker_color="#4f8ff7",
        text=[f"{x:,.0f}" for x in budgets], textposition="outside",
    ))
    fig.add_hline(
        y=ceiling, line_dash="dash", line_color="#f39c12",
        annotation_text=f"Ceiling {ceiling:,.0f}",
    )
    fig.update_layout(
        template="plotly_dark",
        yaxis_title="Budget (quote units)",
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
        margin=dict(l=40, r=20, t=20, b=60),
        height=380,
    )
    b.plotly(fig)
    alloc_rows = [
        {
            "Desk": e["slug"], "Role": e["role"], "Budget": e["budget"],
            "% of ceiling": f"{((e['budget'] or 0.0) / ceiling * 100):.1f}%" if ceiling else "0.0%",
        }
        for e in employees
    ]
    alloc_rows.append({"Desk": "TOTAL", "Role": "company", "Budget": ceiling, "% of ceiling": "100.0%"})
    b.table(alloc_rows, ["Desk", "Role", "Budget", "% of ceiling"])

    # 04 — per-desk P&L
    b.section("04 / PER-DESK P&L", "Daily / realized / unrealized per desk (populates as desks trade)")
    net = 0.0
    pnl_rows = []
    for e in employees:
        d = e["daily_pnl"] or 0.0
        r = e["realized_pnl"] or 0.0
        u = e["unrealized_pnl"] or 0.0
        tot = d + r + u
        net += tot
        pnl_rows.append({
            "Desk": e["slug"], "Daily": round(d, 4), "Realized": round(r, 4),
            "Unrealized": round(u, 4), "Net": round(tot, 4),
        })
    b.kpi("Company net P&L", f"{net:+,.4f}")
    b.table(pnl_rows, ["Desk", "Daily", "Realized", "Unrealized", "Net"])

    # 05 — health & compliance
    b.section("05 / HEALTH & COMPLIANCE", "HR attendance verdict + auditor baseline")
    hr = next((e for e in employees if e["slug"] == "zbot_hr"), None)
    au = next((e for e in employees if e["slug"] == "zbot_audit"), None)

    def row_for(e):
        return {
            "Unit": e["role"], "Agent": e["slug"], "State": e["state"],
            "Ticks": e["tick_count"], "Last tick": _fmt_ts(e["last_tick_at"]),
            "Last error": e["last_error"] or "—",
        }
    hc_rows = [row_for(e) for e in (hr, au) if e]
    if hc_rows:
        b.table(hc_rows, ["Unit", "Agent", "State", "Ticks", "Last tick", "Last error"])

    hr_verdict = "attendance OK — all employees accounted for" if running == total else f"{total - running} employee(s) not running — HR intervenes"
    au_baseline = "SL + trailing on every perp, net-of-fees P&L, journal discipline, capital ceiling"
    b.markdown(f"**HR verdict:** {hr_verdict}\n\n**Auditor baseline (in force):** {au_baseline}")

    # 06 — what is in force
    b.section("06 / WHAT IS IN FORCE", "Active loop configurations carried by each employee")
    force_rows = [
        {
            "Agent": e["slug"], "Strategy": e["strategy"], "Cadence": e["cadence"],
            "Mode": e["execution_mode"], "Timeout": f"{e['tick_timeout_sec']}s" if e["tick_timeout_sec"] else "—",
            "Max ticks": e["max_ticks"] if e["max_ticks"] else "∞",
            "Budget": e["budget"], "Session": f"session_{e['session_num']}",
        }
        for e in employees
    ]
    b.table(force_rows, ["Agent", "Strategy", "Cadence", "Mode", "Timeout", "Max ticks", "Budget", "Session"])

    b.markdown(f"_Last update: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} — refreshes every {interval_sec}s_")


async def run(config: Config, context) -> str:
    report = LiveReport(
        "zbot Company Overview — 7 Employees",
        source_name="zbot_company_dashboard",
        tags=["zbot", "company", "live", "hyperliquid", "dashboard"],
        auto_refresh_seconds=config.interval_sec,
    )
    chat_id = getattr(context, "_chat_id", None)
    ticks = 0
    try:
        while True:
            try:
                client = None
                if chat_id is not None:
                    client = await get_client(chat_id, context=context)

                employees = _collect_employees()
                hl_balance = await _hl_balance(client)
                report.clear()
                _render(report, employees, hl_balance, config.interval_sec)
                await report.update()
                ticks += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # keep the monitor alive across a bad tick
                logger.warning("zbot_company_dashboard tick error: %s", exc)

            await asyncio.sleep(config.interval_sec)

    except asyncio.CancelledError:
        # Leave a clear stopped frame instead of a report that refreshes forever.
        report.clear()
        report.builder.auto_refresh(None)
        report.builder.section("DASHBOARD STOPPED", "Final fixed snapshot")
        report.builder.markdown(
            f"**zbot company dashboard stopped** after `{ticks}` ticks. "
            f"Report id `{report.report_id}`."
        )
        await report.update()
        return f"zbot_company_dashboard stopped after {ticks} ticks (report {report.report_id})"

    return f"zbot_company_dashboard done after {ticks} ticks"