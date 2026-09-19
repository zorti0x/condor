import asyncio
import json
import math
import os
import tempfile
import time
import logging

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client
from mcp_servers.hummingbot_api.schemas import ManageExecutorsRequest
from mcp_servers.hummingbot_api.tools.executors import (
    manage_executors as create_managed_executor,
)

logger = logging.getLogger(__name__)

CONTINUOUS = True
CATEGORY = "Monitoring"

# profit-fraction tiers -> multiplier (feather tighter as profit grows).
# base_multiplier anchors at the FIRST tier; higher tiers scale down from it.
RATCHET_TIERS = [(0.00, 3.0), (0.03, 2.5), (0.06, 2.0), (0.10, 1.5), (0.15, 1.2)]


class Config(BaseModel):
    """Agentic real-time trailing-stop manager for a live Hyperliquid perp position (30m candles)."""
    interval_minutes: float = Field(30, description="Cycle interval in minutes")
    connector: str = Field("hyperliquid_perpetual", description="Perp connector")
    trading_pair: str = Field("USELESS-USD", description="Perp trading pair")
    side: str = Field("SHORT", description="Direction: SHORT or LONG")
    account_name: str = Field("master_account", description="Account name")
    auto_open: bool = Field(True, description="Open the position if none exists (active mode)")
    amount: float = Field(1350, description="Position size in base units (open)")
    leverage: int = Field(3, description="Leverage for the position (open)")
    open_order_type: str = Field("MARKET", description="MARKET or LIMIT for open")
    hard_stop_pct: float = Field(0.12, description="Hard disaster stop from entry (fraction); NEVER widened beyond")
    atr_window: int = Field(14, description="ATR window (30m candles)")
    candle_interval: str = Field("30m", description="Candle interval for ATR/trend")
    base_multiplier: float = Field(2.5, description="Trail multiplier at zero profit (x ATR)")
    min_trail_pct: float = Field(0.008, description="Min trail distance as fraction of price")
    max_trail_pct: float = Field(0.12, description="Max trail distance as fraction of price")
    vol_spike_cap: float = Field(2.0, description="Candle range > this x ATR = vol spike -> cap widening")
    strong_trend_mult: float = Field(0.5, description="Add to multiplier on strong trend with position")
    reversal_tighten: float = Field(0.5, description="Subtract from multiplier on reversal candle")
    reversal_pct: float = Field(0.006, description="Reversal candle move threshold (fraction of price)")
    strong_trend_pct: float = Field(0.01, description="EMA slope threshold to count as strong trend (fraction)")
    funding_contra_guard: bool = Field(True, description="Tighten/exit when funding is crowded against the side")
    funding_contra_threshold: float = Field(-0.30, description="Annualized funding threshold (magnitude used)")
    funding_contra_exit: bool = Field(False, description="If True, exit on funding-contra instead of just tightening")
    funding_period_hours: float = Field(0.5, description="Funding settlement period in hours (annualization)")
    mode: str = Field("monitor", description="active (executes) or monitor (compute + recommend only)")
    dry_run: bool = Field(False, description="Log actions but place no orders")
    single_cycle: bool = Field(False, description="Run one pass and return (for testing/monitor)")
    kill_switch: bool = Field(False, description="When True, stop acting (safety)")
    kill_switch_file: str = Field("", description="Optional path to a kill-switch file; acts when it exists")


# ---------------------------------------------------------------- helpers

def _state_path(pair: str) -> str:
    safe = pair.replace("-", "_").replace("/", "_")
    return os.path.join(tempfile.gettempdir(), f"dynamic_trail_state_{safe}.json")


def _load_state(cfg: Config) -> dict:
    try:
        with open(_state_path(cfg.trading_pair), "r") as f:
            st = json.load(f)
            if isinstance(st, dict):
                return st
    except Exception:
        pass
    return {"entry": None, "best_stop": None, "status": "flat", "last_action_ts": 0,
            "tier": 0.0, "trail_history": [], "trade_log": []}


def _save_state(cfg: Config, st: dict) -> None:
    try:
        with open(_state_path(cfg.trading_pair), "w") as f:
            json.dump(st, f, indent=2, default=str)
    except Exception as e:
        logger.warning(f"state save failed: {e}")


def _atr(candles, window: int):
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]; l = candles[i]["low"]; pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < window:
        return None
    return sum(trs[-window:]) / window


def _ema(values, n: int):
    if not values:
        return None
    k = 2.0 / (n + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def _tier_ratio(profit: float) -> float:
    """Multiplier ratio from ratchet tiers (0.0 -> 1.0, deep profit -> <1)."""
    first = RATCHET_TIERS[0][1]
    mult = RATCHET_TIERS[0][1]
    for p, m in RATCHET_TIERS:
        if profit >= p:
            mult = m
    return mult / first


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _compute(cfg: Config, candles, mark, entry, side, funding_rate, last_range, best_stop_prev):
    """Compute dynamic trail. Returns dict or None if insufficient data."""
    atr = _atr(candles, cfg.atr_window)
    if atr is None or atr <= 0 or mark <= 0 or entry <= 0:
        return None

    profit = (entry - mark) / entry if side == "SHORT" else (mark - entry) / entry
    mult = cfg.base_multiplier * _tier_ratio(max(profit, 0.0))

    # trend strength (EMA slope over last ~6 candles)
    closes = [c["close"] for c in candles]
    adj = 0.0
    strong_trend = False
    if len(closes) >= cfg.atr_window + 7:
        e_now = _ema(closes[-(cfg.atr_window + 6):], cfg.atr_window)
        e_prev = _ema(closes[-(cfg.atr_window + 12):-(cfg.atr_window + 6)], cfg.atr_window)
        if e_prev:
            slope = (e_now - e_prev) / e_prev
            if side == "SHORT" and slope < -cfg.strong_trend_pct:
                strong_trend = True
            if side == "LONG" and slope > cfg.strong_trend_pct:
                strong_trend = True
            if strong_trend:
                mult += cfg.strong_trend_mult
                adj += cfg.strong_trend_mult

    # reversal candle against direction
    reversal = False
    if len(closes) >= 2:
        last_move = closes[-1] - closes[-2]
        if side == "SHORT" and last_move >= cfg.reversal_pct * mark:
            reversal = True
        if side == "LONG" and last_move <= -cfg.reversal_pct * mark:
            reversal = True
    if reversal:
        mult -= cfg.reversal_tighten
        adj -= cfg.reversal_tighten

    # vol spike: cap widening (drop any boost)
    vol_spike = last_range > cfg.vol_spike_cap * atr
    if vol_spike:
        mult = min(mult, cfg.base_multiplier * _tier_ratio(max(profit, 0.0)))

    mult = max(mult, 0.3)

    # funding contra guard
    annual = funding_rate * (24.0 / cfg.funding_period_hours) * 365.0 if cfg.funding_period_hours else 0.0
    mag = abs(cfg.funding_contra_threshold)
    contra = False
    if cfg.funding_contra_guard:
        if side == "SHORT" and annual <= -mag:
            contra = True
        if side == "LONG" and annual >= mag:
            contra = True
    if contra:
        mult = min(mult, 0.6)

    trail_distance = _clamp(mult * atr, cfg.min_trail_pct * mark, cfg.max_trail_pct * mark)

    # stop + ratchet
    hard = entry * (1 + cfg.hard_stop_pct) if side == "SHORT" else entry * (1 - cfg.hard_stop_pct)
    candidate = (mark + trail_distance) if side == "SHORT" else (mark - trail_distance)
    if side == "SHORT":
        candidate = min(candidate, hard)
        best_stop = hard if best_stop_prev is None else min(best_stop_prev, candidate)
    else:
        candidate = max(candidate, hard)
        best_stop = hard if best_stop_prev is None else max(best_stop_prev, candidate)

    broke = (mark >= best_stop) if side == "SHORT" else (mark <= best_stop)

    return {
        "atr": atr, "profit": profit, "mult": mult, "adj": adj,
        "trail_distance": trail_distance, "hard": hard, "best_stop": best_stop,
        "candidate": candidate, "broke": broke, "strong_trend": strong_trend,
        "reversal": reversal, "vol_spike": vol_spike, "contra": contra,
        "annual_funding": annual, "tier_ratio": _tier_ratio(max(profit, 0.0)),
    }


async def _notify(context, chat_id, text: str) -> None:
    try:
        await context.bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        logger.warning(f"notify failed: {e}")


async def _submit_managed_order(client, cfg, trade_type: str, amount: float, position_action: str):
    """Submit an order through an owned executor instead of the raw trading API."""
    request = ManageExecutorsRequest(
        action="create",
        executor_type="order_executor",
        account_name=cfg.account_name,
        controller_id=f"routine:dynamic_trail:{cfg.trading_pair}",
        executor_config={
            "connector_name": cfg.connector,
            "trading_pair": cfg.trading_pair,
            "side": 1 if trade_type == "BUY" else 2,
            "amount": amount,
            "execution_strategy": cfg.open_order_type if position_action == "OPEN" else "MARKET",
            "position_action": position_action,
        },
    )
    result = await create_managed_executor(client, request)
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result


async def _open_position(client, cfg, mark, st) -> str:
    """Open the perp position in active mode. Returns a human status string."""
    trade_type = "BUY" if cfg.side == "LONG" else "SELL"
    # margin guard (user rule: always check perp margin before opening)
    avail = 0.0
    try:
        state = await client.portfolio.get_state()
        acct = (state or {}).get(cfg.account_name, {})
        for conn, holdings in (acct or {}).items():
            if conn == cfg.connector:
                for h in holdings or []:
                    avail = float(h.get("available_units", h.get("available", 0)) or 0)
    except Exception as e:
        logger.warning(f"margin check failed: {e}")
    required = (cfg.amount * mark) / cfg.leverage if mark else 0.0
    if required and required > avail:
        return (f"⛔ SKIP OPEN: need ~${required:.0f} margin at {cfg.leverage}x but "
                f"only ${avail:.0f} available on {cfg.connector}. Increase margin or cut size.")
    if cfg.dry_run:
        st["status"] = "open"; st["entry"] = mark; st["best_stop"] = None
        return f"DRY-RUN: would OPEN {cfg.side} {cfg.amount} {cfg.trading_pair} @ ~{mark:.6f} (margin ok)"
    # set leverage
    try:
        await client.trading.set_leverage(cfg.account_name, cfg.connector, cfg.trading_pair, cfg.leverage)
    except Exception as e:
        logger.warning(f"set_leverage failed (continuing): {e}")
    await _submit_managed_order(client, cfg, trade_type, cfg.amount, "OPEN")
    fill = mark
    st["status"] = "open"; st["entry"] = float(fill or mark); st["best_stop"] = None
    st["last_action_ts"] = time.time()
    st["trade_log"].append({"type": "open", "side": cfg.side, "amount": cfg.amount,
                            "price": float(fill or mark), "ts": time.time()})
    _save_state(cfg, st)
    return f"🟢 OPENED {cfg.side} {cfg.amount} {cfg.trading_pair} @ ~{float(fill or mark):.6f}"


async def _close_position(client, cfg, amount, st) -> str:
    trade_type = "BUY" if cfg.side == "SHORT" else "SELL"
    if cfg.dry_run:
        return f"DRY-RUN: would CLOSE {cfg.side} {amount} {cfg.trading_pair}"
    await _submit_managed_order(client, cfg, trade_type, amount, "CLOSE")
    fill = 0.0
    st["status"] = "flat"; st["last_action_ts"] = time.time()
    st["trade_log"].append({"type": "close", "side": cfg.side, "amount": amount,
                            "price": float(fill or 0), "ts": time.time()})
    st["entry"] = None; st["best_stop"] = None
    _save_state(cfg, st)
    return f"🔴 CLOSED {cfg.side} {amount} {cfg.trading_pair} @ ~{float(fill or 0):.6f}"


async def one_cycle(cfg: Config, context, chat_id, report, st) -> str:
    client = await get_client(chat_id, context=context)
    if not client:
        return "No server available"

    # kill switch (config flag or file presence)
    killed = cfg.kill_switch
    if cfg.kill_switch_file and os.path.exists(cfg.kill_switch_file):
        killed = True
    if killed:
        if not st.get("killed_notified"):
            await _notify(context, chat_id, f"🛑 KILL SWITCH active — {cfg.trading_pair} trail manager NOT acting.")
            st["killed_notified"] = True; _save_state(cfg, st)
        report.clear()
        report.builder.section("KILL SWITCH", "Not acting")
        await report.update()
        return "kill_switch active"

    now = int(time.time())
    # fetch candles (stateless historical endpoint — fast)
    try:
        candles = await client.market_data.get_historical_candles(
            cfg.connector, cfg.trading_pair, cfg.candle_interval,
            start_time=now - 26 * 3600, end_time=now)
        if isinstance(candles, dict):
            candles = candles.get("data", candles.get("candles", []))
    except Exception as e:
        return f"candle fetch failed: {e}"
    if not candles:
        return "no candles"

    # funding + mark
    try:
        finfo = await client.market_data.get_funding_info(cfg.connector, cfg.trading_pair)
    except Exception as e:
        finfo = {}
    funding_rate = float((finfo or {}).get("funding_rate") or 0.0)
    mark = float((finfo or {}).get("mark_price") or 0.0) or float(candles[-1]["close"])

    # open position for the pair
    our = None
    try:
        pos = await client.trading.get_open_positions(cfg.account_name, cfg.connector)
        data = pos.get("data", []) if isinstance(pos, dict) else (pos if isinstance(pos, list) else [])
        for p in data or []:
            tp = str(p.get("trading_pair", ""))
            if tp.upper() == cfg.trading_pair.upper():
                our = p; break
    except Exception as e:
        logger.warning(f"position fetch failed: {e}")

    last_range = float(candles[-1]["high"]) - float(candles[-1]["low"])

    # ----- reconcile state with reality -----
    if our is not None:
        st["status"] = "open"
        pentry = our.get("entry_price") or our.get("avg_entry_price") or our.get("entry")
        if pentry:
            st["entry"] = float(pentry)
        elif not st.get("entry"):
            st["entry"] = mark
        if st.get("entry") is None:
            st["entry"] = mark
    else:
        # no live position
        if st.get("status") == "open":
            # was managing, now gone (external close) -> reset
            st["status"] = "flat"; st["entry"] = None; st["best_stop"] = None; _save_state(cfg, st)

    side = cfg.side.upper()

    # ----- OPEN if needed (active only) -----
    if our is None and st.get("status") != "open" and cfg.auto_open and cfg.mode == "active":
        msg = await _open_position(client, cfg, mark, st)
        await _notify(context, chat_id, msg)
        st["status"] = "open"
        # adopt our newly opened position
        st["entry"] = st["entry"] or mark
        report.clear(); report.builder.section("OPEN", msg); await report.update()
        return msg

    entry = st.get("entry") or mark
    best_prev = st.get("best_stop")

    if our is None and st.get("status") != "open":
        # no position and not opening -> monitor/hypothetical
        st["status"] = "monitoring"
        entry = mark  # hypothetical entry
        best_prev = None

    comp = _compute(cfg, candles, mark, entry, side, funding_rate, last_range, best_prev)
    if comp is None:
        return "insufficient data for ATR"

    # persist ratchet
    st["best_stop"] = comp["best_stop"]
    st["tier"] = comp["tier_ratio"]
    st["trail_history"].append({"ts": time.time(), "mark": mark, "stop": comp["best_stop"],
                                "atr": comp["atr"], "mult": comp["mult"], "profit": comp["profit"]})
    if len(st["trail_history"]) > 500:
        st["trail_history"] = st["trail_history"][-500:]
    _save_state(cfg, st)

    # ----- ACTIONS -----
    out = []
    # funding-contra exit
    if comp["contra"] and cfg.funding_contra_exit and cfg.mode == "active" and our is not None:
        msg = await _close_position(client, cfg, abs(our.get("amount", cfg.amount)), st)
        await _notify(context, chat_id, f"🟠 FUNDING CONTRA EXIT {cfg.trading_pair}: {msg}")
        out.append(msg)
    # break
    elif comp["broke"] and our is not None and cfg.mode == "active":
        amt = abs(float(our.get("amount") or cfg.amount))
        msg = await _close_position(client, cfg, amt, st)
        await _notify(context, chat_id, f"🔴 STOP HIT {cfg.trading_pair}: {msg}")
        out.append(msg)

    # trail-change notification
    prev_stop = best_prev
    changed = prev_stop is None or (abs(comp["best_stop"] - prev_stop) / prev_stop) > 0.0005
    pct_off = (comp["best_stop"] - entry) / entry if side == "SHORT" else (entry - comp["best_stop"]) / entry

    # build report each tick
    report.clear()
    report.builder.section("POSITION", f"{cfg.trading_pair} {side}")
    report.builder.kpi("Status", st.get("status", "?"))
    report.builder.kpi("Mark", f"{mark:.6f}")
    report.builder.kpi("Entry", f"{entry:.6f}")
    report.builder.kpi("Profit", f"{comp['profit']*100:+.2f}%")
    report.builder.kpi("Trail stop", f"{comp['best_stop']:.6f} ({pct_off*100:+.2f}%)")
    report.builder.kpi("ATR", f"{comp['atr']:.5f}")
    report.builder.kpi("Mult", f"{comp['mult']:.2f}x")
    flags = []
    if comp["strong_trend"]: flags.append("trend+")
    if comp["reversal"]: flags.append("reversal-")
    if comp["vol_spike"]: flags.append("volspike")
    if comp["contra"]: flags.append("funding-contra")
    report.builder.kpi("Flags", ", ".join(flags) if flags else "—")
    report.builder.markdown(f"_Annualized funding {comp['annual_funding']*100:+.1f}%_")
    report.builder.markdown(f"_Last update: {time.strftime('%H:%M:%S')} (mode={cfg.mode})_")
    await report.update()

    if cfg.mode == "monitor":
        if changed or True:
            await _notify(context, chat_id,
                          f"🛰 {cfg.trading_pair} {side} (MONITOR, no orders)\n"
                          f"Mark {mark:.6f} · entry {entry:.6f} ({comp['profit']*100:+.2f}%)\n"
                          f"Recommended stop {comp['best_stop']:.6f} ({pct_off*100:+.2f}% from entry)\n"
                          f"Tier ×{comp['mult']:.2f} · ATR {comp['atr']:.5f}\n"
                          + (f"Flags: {', '.join(flags)}" if flags else "No flags"))
        return "monitor " + (" | ".join(out) if out else "no action")

    # active mode notifications on trail change / action
    if changed and our is not None and not comp["broke"] and not comp["contra"]:
        await _notify(context, chat_id,
                      f"🛰 trail update {cfg.trading_pair} {side}\n"
                      f"stop {comp['best_stop']:.6f} ({pct_off*100:+.2f}%), tier ×{comp['mult']:.2f}, "
                      f"ATR {comp['atr']:.5f}\n"
                      + (f"widening for trend" if comp["strong_trend"] else
                         (f"tightening: reversal" if comp["reversal"] else
                          (f"vol spike: holding" if comp["vol_spike"] else ""))))
    return "active " + (" | ".join(out) if out else "ok")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id = context._chat_id
    from condor.reports import LiveReport
    report = LiveReport(
        f"🛰 Dynamic Trail — {config.trading_pair} {config.side}",
        source_name="hyperliquid_dynamic_trail",
        tags=["live", "trail", "hyperliquid"],
        auto_refresh_seconds=config.interval_minutes * 60,
    )
    st = _load_state(config)

    if config.single_cycle:
        await _notify(context, chat_id, f"🛰 {config.trading_pair} trail — single {config.mode} pass")
        out = await one_cycle(config, context, chat_id, report, st)
        report.clear()
        report.builder.section("SINGLE PASS", out)
        await report.update()
        return out

    await _notify(context, chat_id,
                  f"🛰 {config.trading_pair} {config.side} dynamic trail STARTED\n"
                  f"mode={config.mode} · cycle={config.interval_minutes}min · "
                  f"hard stop {config.hard_stop_pct*100:.0f}% · auto_open={config.auto_open}")

    try:
        while True:
            try:
                await one_cycle(config, context, chat_id, report, st)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"tick error: {e}")
            await asyncio.sleep(config.interval_minutes * 60)
    except asyncio.CancelledError:
        if report.report_id is not None:
            report.clear()
            report.builder.auto_refresh(None)
            report.builder.section("MONITOR STOPPED", "Final snapshot")
            await report.update()
        return f"{config.trading_pair} trail stopped after state: {st.get('status')}"