"""Every ~4h, scan hyperliquid_perpetual BTC-USD for a high-probability perp entry
signal in EITHER direction and notify on Telegram when one fires (mobile-friendly,
short). Stays quiet when there is no signal.

Composite signal (each component 0..1, weighted):
  - Multi-timeframe trend      (33%)  EMA fast/slow on 1h AND 4h → direction + agreement
  - RSI(14) timing             (25%)  overextension guard / entry timing on 1h
  - Funding crowd filter       (20%)  avoid LONG when funding very positive (crowded
                                      long), avoid SHORT when funding very negative
                                      (crowded short); thresholds in annualized %
  - Volume confirmation        (17%)  recent volume vs window average + last candle
                                      moved with the signal
  - Swing high/low breakout    ( 5%)  recent 1h swing high/low (extra conviction)
Confidence = weighted sum (0..1). Only notify when confidence >= threshold AND the
timeframe trend cleanly picks one direction.

State (last_notify_ts + history) is persisted per trading_pair so each pair gets
its own cooldown and notification history.
"""

CATEGORY = "Monitoring"
CONTINUOUS = True

import asyncio
import json
import logging
import os
import tempfile
import time

from datetime import datetime, timezone

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client
from condor.fetchers.market_data import fetch_historical_candles
from condor.reports import LiveReport

logger = logging.getLogger(__name__)

HOURS_TO_FETCH_1H = 120   # candles needed for EMA50 + RSI + volume on the 1h series
CANDLES_4H = 80           # 80 × 4h = 13.3 days → EMA50 on 4h


def _state_file(trading_pair):
    """Per-pair state file: each pair gets its own last_notify_ts + history,
    so one pair's notification does not pause another pair's cooldown."""
    safe = "".join(ch if ch.isalnum() else "_" for ch in trading_pair)
    return os.path.join(tempfile.gettempdir(), f"hyperliquid_perp_scan_state_{safe}.json")


class Config(BaseModel):
    """Periodic BTC perp entry signals (every 4h) from Hyperliquid, both directions. All thresholds tunable."""
    interval_hours: float = Field(default=4.0, description="How often to scan (hours)")
    connector: str = Field(default="hyperliquid_perpetual", description="Perp connector")
    trading_pair: str = Field(default="BTC-USD", description="Pair to scan (ETH-style QUOTE-USD)")
    confidence_threshold: float = Field(default=0.65, ge=0.0, le=1.0, description="Minimum confidence (0-1) to notify")
    funding_filter_enabled: bool = Field(default=True, description="Apply the crowded-funding filter")
    funding_crowded_annualized_pct: float = Field(default=20.0, description="Funding (annualized %) beyond which a side is 'crowded'. Block LONG above +X%, SHORT below -X%.")
    ema_fast: int = Field(default=20, ge=2, description="Fast EMA period")
    ema_slow: int = Field(default=50, ge=2, description="Slow EMA period")
    rsi_window: int = Field(default=14, ge=2, description="RSI lookback window")
    volume_window: int = Field(default=24, ge=2, description="Candles used for the average-volume baseline")
    cooldown_hours: float = Field(default=4.0, description="Min hours between notifications (signal persisting)")
    dry_run: bool = Field(default=False, description="Test mode: compute once, print signal+confidence, no loop, no notify")


def _load_state(state_file):
    try:
        with open(state_file) as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def _save_state(state_file, state):
    try:
        with open(state_file, "w") as f:
            json.dump(state, f)
    except Exception as e:
        logger.warning("state persist failed: %s", e)


# ---------------------------------------------------------------- indicators
def _ema_series(values, period):
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _rsi(values, window):
    if len(values) < window + 1:
        return None
    deltas = [values[i] - values[i - 1] for i in range(len(values) - window, len(values))]
    gain = sum(d for d in deltas if d > 0) / window
    loss = sum(-d for d in deltas if d < 0) / window
    if gain + loss == 0:
        return 50.0
    return 100.0 - 100.0 / (1.0 + gain / loss)


def _piecewise(x, pts):
    """Linear interpolation over (x, y) points; flat outside the ends."""
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1 and x1 != x0:
            return y0 + (x - x0) / (x1 - x0) * (y1 - y0)
    return pts[-1][1]


def _sort_candles(rows):
    rows = [r for r in (rows or []) if isinstance(r, dict) and r.get("close") is not None]
    rows.sort(key=lambda r: r.get("timestamp") or 0)
    return rows


def _funding_annualized_pct(raw_rate):
    return raw_rate * 24 * 365 * 100.0


async def _fetch_data(client, cfg):
    now = time.time()
    end = int(now)
    start1 = int(now - HOURS_TO_FETCH_1H * 3600)
    start4 = int(now - CANDLES_4H * 4 * 3600)
    c1 = _sort_candles(await fetch_historical_candles(
        client, cfg.connector, cfg.trading_pair, interval="1h",
        start_time=start1, end_time=end))
    c4 = _sort_candles(await fetch_historical_candles(
        client, cfg.connector, cfg.trading_pair, interval="4h",
        start_time=start4, end_time=end))
    funding = None
    try:
        fi = await client.market_data.get_funding_info(cfg.connector, cfg.trading_pair)
        if isinstance(fi, dict):
            funding = fi.get("funding_rate")
    except Exception as e:
        logger.warning("funding fetch failed: %s", e)
    return c1, c4, funding


def _compute_signal(c1, c4, funding_raw, cfg):
    """Return dict with direction, components, confidence — or None when the
    timeframe trend does not cleanly pick a direction."""
    closes1 = [c["close"] for c in c1]
    closes4 = [c["close"] for c in c4]
    if len(closes1) < cfg.ema_slow + 1 or len(closes4) < cfg.ema_slow + 1:
        return {"error": "insufficient candles"}

    price = closes1[-1]
    ef1, es1 = _ema_series(closes1, cfg.ema_fast)[-1], _ema_series(closes1, cfg.ema_slow)[-1]
    ef4, es4 = _ema_series(closes4, cfg.ema_fast)[-1], _ema_series(closes4, cfg.ema_slow)[-1]

    # Trend agreement per candidate direction (0 / 0.5 / 1.0)
    long_agree = (int(price > ef1 > es1) + int(price > ef4 > es4)) / 2.0
    short_agree = (int(price < ef1 < es1) + int(price < ef4 < es4)) / 2.0
    if long_agree == short_agree:        # both 0 (flat) or both 0.5 (mixed) -> no clean trend
        return {"direction": None, "reason": "no_clean_trend", "confidence": 0.0}

    direction = "LONG" if long_agree > short_agree else "SHORT"
    agree = long_agree if direction == "LONG" else short_agree

    # RSI timing (overextension guard)
    rsi = _rsi(closes1, cfg.rsi_window)
    if direction == "LONG":
        rsi_score = _piecewise(rsi, [(0, 0.0), (30, 0.4), (45, 0.85), (62, 1.0), (72, 0.4), (100, 0.0)])
        rsi_note = f"RSI {rsi:.0f}" + (" overbought" if rsi >= 72 else "")
    else:
        rsi_score = _piecewise(rsi, [(0, 0.0), (28, 0.4), (38, 1.0), (55, 1.0), (65, 0.6), (72, 0.0), (100, 0.0)])
        rsi_note = f"RSI {rsi:.0f}" + (" oversold" if rsi <= 28 else "")

    # Funding crowd filter
    if funding_raw is None:
        fund_score, fund_note = 0.5, "n/a"
    elif not cfg.funding_filter_enabled:
        fund_score, fund_note = 1.0, "filter off"
    else:
        ann = _funding_annualized_pct(funding_raw)
        if direction == "LONG":
            fund_score = 1.0 if ann <= 0 else max(0.0, 1.0 - ann / cfg.funding_crowded_annualized_pct)
            fund_note = f"funding {ann:+.1f}%/yr" + (" crowded-long" if ann >= cfg.funding_crowded_annualized_pct else "")
        else:
            fund_score = 1.0 if ann >= 0 else max(0.0, 1.0 + ann / cfg.funding_crowded_annualized_pct)
            fund_note = f"funding {ann:+.1f}%/yr" + (" crowded-short" if ann <= -cfg.funding_crowded_annualized_pct else "")

    # Volume confirmation
    vols = [c.get("volume") or 0 for c in c1]
    vol_window = vols[-cfg.volume_window:]
    avg_vol = sum(vol_window) / len(vol_window) if vol_window else 0
    ratio = vols[-1] / avg_vol if avg_vol > 0 else 1.0
    strength = min(1.0, max(0.0, (ratio - 0.5) / 0.8))
    last_up = c1[-1]["close"] >= c1[-1].get("open", c1[-1]["close"])
    move_ok = last_up if direction == "LONG" else not last_up
    vol_score = 0.6 * strength + 0.4 * (1.0 if move_ok else 0.3)
    vol_note = f"vol {ratio:.1f}x avg" + (" +dir" if move_ok else " -dir")

    # Swing high/low breakout (extra conviction)
    past = c1[-24:-1] if len(c1) >= 24 else c1[:-1]
    if direction == "LONG":
        swing = max(c["high"] for c in past)
        break_score = 1.0 if price > swing else 0.4
    else:
        swing = min(c["low"] for c in past)
        break_score = 1.0 if price < swing else 0.4

    confidence = 0.33 * agree + 0.25 * rsi_score + 0.20 * fund_score + 0.17 * vol_score + 0.05 * break_score

    return {
        "direction": direction,
        "entry_price": price,
        "confidence": round(confidence, 4),
        "rsi": rsi, "rsi_score": round(rsi_score, 3), "rsi_note": rsi_note,
        "ema": {"1h": [round(ef1, 0), round(es1, 0)], "4h": [round(ef4, 0), round(es4, 0)]},
        "trend_agree": agree, "fund_score": round(fund_score, 3), "fund_note": fund_note,
        "vol_score": round(vol_score, 3), "vol_note": vol_note,
        "break_score": round(break_score, 3),
        "components": {
            "trend": round(agree, 3), "rsi": round(rsi_score, 3),
            "funding": round(fund_score, 3), "volume": round(vol_score, 3),
            "breakout": round(break_score, 3),
        },
    }


def _notify_text(sig, cfg, funding_note):
    side = "🔵 LONG" if sig["direction"] == "LONG" else "🔴 SHORT"
    conf = round(sig["confidence"] * 100)
    price = sig["entry_price"]
    return (
        f"{side} signal — {cfg.trading_pair} · {conf}% confidence\n\n"
        f"• Entry zone: ${price:,.0f}\n"
        f"• Current: ${price:,.0f}\n"
        f"• Why now: {sig['rsi_note']}, {funding_note}, {sig['vol_note']}\n"
        f"• Read: momentum + volume agree on {sig['direction'].lower()}; enter only on your size/slippage rules."
    )


# ---------------------------------------------------------------------- run
async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id = getattr(context, "_chat_id", None)

    if config.dry_run:
        client = await get_client(chat_id, context=context)
        if not client:
            return "No server available for dry run"
        c1, c4, funding = await _fetch_data(client, config)
        sig = _compute_signal(c1, c4, funding, config)
        if sig.get("direction") is None:
            lines = ["DRY-RUN: no clean directional signal", f"reason={sig.get('reason')}",
                     f"candles 1h={len(c1)} 4h={len(c4)} funding={funding}"]
            return "\n".join(lines)
        rows = [f"DRY-RUN {'LONG' if sig['direction']=='LONG' else 'SHORT'} signal"]
        rows.append(f"confidence={sig['confidence']:.3f} threshold={config.confidence_threshold}")
        for k, v in sig["components"].items():
            rows.append(f"  {k}: {v}")
        rows.append(f"  entry={sig['entry_price']:.2f} rsi={sig['rsi']:.1f}")
        rows.append(f"  ema(1h)=f{int(sig['ema']['1h'][0])}/s{int(sig['ema']['1h'][1])} ema(4h)=f{int(sig['ema']['4h'][0])}/s{int(sig['ema']['4h'][1])}")
        rows.append(f"  {sig['rsi_note']} · {sig['fund_note']} · {sig['vol_note']}")
        return "\n".join(rows)

    client = await get_client(chat_id, context=context)
    if not client:
        return "No server available"

    sf = _state_file(config.trading_pair)
    state = _load_state(sf)
    state.setdefault("history", [])
    notifications = 0
    started = time.time()
    interval_sec = max(60, int(config.interval_hours * 3600))

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🟢 hyperliquid_perp_scan started — watching {config.trading_pair} every {config.interval_hours:g}h. "
                 "No signal = no spam; I'll only ping on a high-confidence setup.",
        )
    except Exception as e:
        logger.error("start msg failed: %s", e)

    report = LiveReport(
        f"Hyperliquid Perp Scan — {config.trading_pair}",
        source_name="hyperliquid_perp_scan",
        tags=["monitoring", "hyperliquid", "signal", "live"],
    )

    try:
        while True:
            try:
                client = await get_client(chat_id, context=context)
                if client:
                    c1, c4, funding = await _fetch_data(client, config)
                    sig = _compute_signal(c1, c4, funding, config)
                else:
                    sig = {"direction": None, "reason": "no_client"}

                now = time.time()
                last_notify = float(state.get("last_notify_ts") or 0)
                cooldown_ok = (now - last_notify) >= config.cooldown_hours * 3600

                if (sig.get("direction") is not None and sig["confidence"] >= config.confidence_threshold
                        and cooldown_ok):
                    state["last_notify_ts"] = now
                    state["history"] = (state["history"] + [{
                        "ts": datetime.now(timezone.utc).strftime("%m-%d %H:%M"),
                        "direction": sig["direction"],
                        "conf": round(sig["confidence"] * 100),
                        "price": round(sig["entry_price"]),
                    }])[-20:]
                    _save_state(sf, state)
                    notifications += 1
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=_notify_text(sig, config, sig["fund_note"]))
                    except Exception as e:
                        logger.error("signal msg failed: %s", e)

                fx = sig.get("fund_note") or "n/a"
                rows = []
                if sig.get("direction") is not None:
                    rows.append({
                        "Component": "Direction",
                        "Value": f"{sig['direction']} (trend agree {sig.get('trend_agree', 0):.0%})",
                    })
                    for k, v in sig["components"].items():
                        rows.append({"Component": k, "Value": f"{v:.2f}"})
                    rows.append({"Component": "Confidence", "Value": f"{sig['confidence']:.2f} (threshold {config.confidence_threshold:.2f})"})
                else:
                    rows.append({"Component": "Direction", "Value": "none — quiet"})
                    rows.append({"Component": "Reason", "Value": str(sig.get("reason"))})

                report.clear()
                report.builder.manual_order()
                report.builder.section("LATEST SCAN", "Current composite signal state")
                report.builder.kpi("Direction", sig.get("direction") or "none")
                report.builder.kpi("Confidence", f"{sig.get('confidence', 0):.2f}" if sig.get("direction") else "—")
                report.builder.kpi("Price", f"${sig.get('entry_price', 0):,.0f}" if sig.get("entry_price") else "—")
                report.builder.kpi("RSI / Funding", f"{sig.get('rsi') and round(sig['rsi'])} / {fx}")
                report.builder.table(rows, ["Component", "Value"])
                report.builder.section("SIGNAL HISTORY", "Last notified signals")
                report.builder.table(state["history"][-15:], ["Time", "Dir", "Conf", "Price"] if state["history"] else [])
                report.builder.markdown(f"_Last check: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} · "
                                        f"interval {config.interval_hours:g}h · funding filter "
                                        f"{'ON' if config.funding_filter_enabled else 'OFF'}_")
                await report.update()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("hyperliquid_perp_scan tick error: %s", e)

            await asyncio.sleep(interval_sec)

    except asyncio.CancelledError:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⏹ hyperliquid_perp_scan stopped · notifications sent: {notifications}",
            )
        except Exception:
            pass
        return f"Stopped after {(time.time() - started)/3600:.1f}h, {notifications} notifications"