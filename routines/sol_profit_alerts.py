"""Instant Telegram profit/loss + risk alerts for a SOL position executor (30s)."""
import asyncio
import json
import logging
import math
import os
import tempfile
import time
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client
from condor.fetchers.executors import get_executor_detail, build_executor_row

logger = logging.getLogger(__name__)
CONTINUOUS = True
CATEGORY = "Monitoring"

class Config(BaseModel):
    """SOL position executor monitor: fills, SL/TP, trailing, PnL milestones, drawdown."""
    executor_id: str = Field(default="yPh4QoZQK3ikz91EQzgRRXMpqkzz9m7DgjsJjfkEqKm", description="position executor id")
    interval_sec: int = Field(default=30, description="poll interval seconds")
    milestone_usd: float = Field(default=5.0, description="alert every $N of P&L")
    drawdown_threshold_pct: float = Field(default=3.0, description="risk alert when drawdown exceeds N% of notional")
    single_cycle: bool = Field(default=False, description="run one pass and return, no telegram")


def _state_path(executor_id):
    safe = "".join(c if c.isalnum() else "_" for c in executor_id)
    return os.path.join(tempfile.gettempdir(), "sol_profit_alerts_" + safe + ".json")

def _default_state():
    return {
        "last_volume": None, "last_bucket": None, "last_status": None,
        "trailing_activated": False, "peak_pnl": None,
        "pmax": None, "lmin": None, "drawdown_warned": False,
        "notified_end": False, "alerts": [],
    }

def _load_state(executor_id):
    d = _default_state()
    try:
        with open(_state_path(executor_id)) as f:
            x = json.load(f)
        if isinstance(x, dict):
            for k in d:
                if k in x:
                    d[k] = x[k]
    except Exception:
        pass
    return d

def _save_state(executor_id, s):
    try:
        with open(_state_path(executor_id), "w") as f:
            json.dump(s, f, indent=2, default=str)
    except Exception as e:
        logger.warning("state save failed: %s", e)

async def _notify(context, chat_id, text):
    try:
        await context.bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        logger.warning("telegram notify failed: %s", e)

def _log_alert(s, msg):
    s["alerts"].append({"ts": time.strftime("%H:%M:%S"), "msg": msg})
    if len(s["alerts"]) > 20:
        s["alerts"] = s["alerts"][-20:]


async def one_cycle(cfg, context, chat_id, report, st):
    client = await get_client(chat_id, context=context)
    if not client:
        return False
    try:
        ex = await get_executor_detail(client, cfg.executor_id)
    except Exception as e:
        logger.warning("executor fetch failed: %s", e)
        return False
    if not isinstance(ex, dict) or not ex:
        if not st.get("notified_end"):
            await _notify(context, chat_id, "SOL position ended - executor no longer found. Stopping sol_profit_alerts.")
            st["notified_end"] = True
            _save_state(cfg.executor_id, st)
        return True
    try:
        row = build_executor_row(ex)
    except Exception as e:
        logger.warning("row build failed: %s", e)
        return False
    pnl = float(row.get("pnl") or 0.0)
    volume = float(row.get("volume") or 0.0)
    entry = float(row.get("entry_price") or 0.0)
    price = float(row.get("current_price") or 0.0) or entry
    amount = abs(float(row.get("amount") or 0.0))
    status = str(row.get("status") or "").upper()
    close_type = str(row.get("close_type") or "").strip()
    side = str(row.get("side") or "BUY").upper()
    notional = entry * amount if entry > 0 else 0.0
    custom = ex.get("custom_info") or {}
    avg = float(custom.get("current_position_average_price") or entry or 0.0)
    tbc = (row.get("config") or {}).get("triple_barrier_config") or {}
    tc = tbc.get("trailing_stop") or {}
    act_pct = float(tc.get("activation_price") or 0.0)
    tdelta = float(tc.get("trailing_delta") or 0.0)
    ms = max(float(cfg.milestone_usd or 5.0), 0.01)
    thresh = max(float(cfg.drawdown_threshold_pct or 3.0), 0.1)
    out = []
    terminal = False
    dd_pct = 0.0
    lv = st.get("last_volume")
    if lv is not None and volume > lv + 1e-9:
        dq = volume - lv
        if avg > 0:
            out.append("FILL +%.3f SOL (~$%.2f) - filled %.2f/%.2f SOL" % (dq/avg, dq, volume/avg, amount))
        else:
            out.append("FILL ~$%.2f - cumulative $%.2f" % (dq, volume))
    st["last_volume"] = volume
    if status not in ("RUNNING", "STARTED", "ACTIVE"):
        key = close_type.lower()
        if "stop" in key:
            head = "STOP LOSS HIT"
        elif "take" in key or "profit" in key:
            head = "TAKE PROFIT HIT"
        elif "trail" in key:
            head = "TRAILING STOP HIT"
        else:
            head = (close_type or "Position closed").upper()
        out.append("%s (SOL-USD %s) exit ~%.2f entry %.2f P&L %+.2f USD" % (head, side, price, entry, pnl))
        out.append("SOL POSITION ENDED - %s - final P&L %+.2f USD. Stopping sol_profit_alerts." % (close_type or "closed", pnl))
        st["notified_end"] = True
        terminal = True
    else:
        if not st.get("trailing_activated") and act_pct > 0 and entry > 0:
            pp = ((price - entry) / entry) if side == "BUY" else ((entry - price) / entry)
            if pp >= act_pct:
                st["trailing_activated"] = True
                out.append("TRAILING ACTIVATED profit %+.1f%% >= +%.0f%% (trail delta %.1f%%)" % (pp*100, act_pct*100, tdelta*100))
        bucket = math.floor(pnl / ms)
        lb = st.get("last_bucket")
        if lb is not None and bucket != lb:
            step = 1 if bucket > lb else -1
            for b in range(int(lb + step), int(bucket + step), step):
                if b == 0:
                    continue
                level = b * ms
                if b > 0 and (st.get("pmax") is None or level > st["pmax"]):
                    st["pmax"] = level
                    out.append("P&L MILESTONE %+d USD (current %+.2f)" % (level, pnl))
                elif b < 0 and (st.get("lmin") is None or level < st["lmin"]):
                    st["lmin"] = level
                    out.append("P&L MILESTONE LOSS %+d USD (current %+.2f)" % (level, pnl))
        st["last_bucket"] = bucket
        peak = st.get("peak_pnl")
        if peak is None or pnl > peak:
            peak = pnl
            st["peak_pnl"] = peak
        dd = peak - pnl
        dd_pct = (dd / notional * 100.0) if notional > 0 else 0.0
        if dd_pct > thresh and not st.get("drawdown_warned"):
            st["drawdown_warned"] = True
            out.append("RISK: drawdown %.2f%% of notional (peak %+.2f now %+.2f)" % (dd_pct, peak, pnl))
        elif st.get("drawdown_warned") and dd_pct < max(thresh - 1.0, 0.5):
            st["drawdown_warned"] = False
    st["last_status"] = status
    for msg in out:
        _log_alert(st, msg)
        if not cfg.single_cycle:
            await _notify(context, chat_id, msg)
    _save_state(cfg.executor_id, st)

    report.clear()
    report.builder.section("SOL POSITION", "%s %s %s" % (row.get("pair"), side, cfg.executor_id[:8]))
    report.builder.kpi("Status", status)
    report.builder.kpi("PnL", "%+.2f USD" % pnl)
    report.builder.kpi("Entry", "%.2f" % entry)
    report.builder.kpi("Mark", "%.2f" % price)
    if avg > 0:
        report.builder.kpi("Filled", "%.2f/%.2f SOL" % (volume/avg, amount))
    report.builder.kpi("Peak", "%+.2f USD" % (st.get("peak_pnl") or 0))
    report.builder.kpi("Trail", "ACTIVE" if st.get("trailing_activated") else "off")
    report.builder.markdown("_drawdown %.2f%% - milestone $%.0f - tick %s UTC - id %s_" % (dd_pct, ms, time.strftime("%H:%M:%S"), cfg.executor_id[:8]))
    if st["alerts"]:
        report.builder.table([{"time": a["ts"], "event": a["msg"][:70]} for a in st["alerts"][-12:]], ["Time", "Event"])
    await report.update()
    return terminal

async def run(config, context):
    chat_id = context._chat_id
    from condor.reports import LiveReport
    report = LiveReport(
        "SOL Profit Alerts - %s" % config.executor_id[:8],
        source_name="sol_profit_alerts",
        tags=["live", "alert", "sol", "hyperliquid"],
        auto_refresh_seconds=config.interval_sec,
    )
    st = _load_state(config.executor_id)
    if config.single_cycle:
        stop = await one_cycle(config, context, chat_id, report, st)
        return "single pass done (stop=%s)" % stop
    await _notify(context, chat_id,
                  "sol_profit_alerts STARTED - monitoring %s every %ss, milestone $%s, drawdown risk >%s%%"
                  % (config.executor_id[:12], config.interval_sec, config.milestone_usd, config.drawdown_threshold_pct))
    try:
        while True:
            try:
                stop = await one_cycle(config, context, chat_id, report, st)
                if stop:
                    break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("tick error: %s", e)
            await asyncio.sleep(config.interval_sec)
    except asyncio.CancelledError:
        return "stopped (status %s)" % (st.get("last_status") or "?")
    if report.report_id is not None:
        report.clear()
        report.builder.auto_refresh(None)
        report.builder.section("MONITOR STOPPED", "Position ended")
        await report.update()
    return "stopped - position ended, final P&L %+.2f" % (st.get("peak_pnl") or 0)
