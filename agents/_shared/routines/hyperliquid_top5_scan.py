"""Every ~4h, rank the top-5 Hyperliquid perpetuals by 24h notional volume and run the
same composite perp-entry signal used by hyperliquid_perp_scan on each, then post a
compact, mobile-friendly ranked summary to Telegram with any high-confidence alerts.

Signal (identical logic/weights to hyperliquid_perp_scan):
  - Multi-timeframe trend EMA(20/50) on 1h and 4h  (33%)
  - RSI(14) timing on 1h                           (25%)
  - Funding crowd filter (20%) — annualized, blocks LONG above +X%/yr,
    SHORT below -X%/yr (tunable)
  - Volume confirmation (17%)
  - Swing high/low breakout (5%)
Confidence is the weighted sum (0..1). A pair ALERTS only when confidence >= threshold
AND the timeframe trend cleanly picks a direction.

Ranking: Hyperliquid public info API (POST https://api.hyperliquid.xyz/info with
{"type": "metaAndAssetCtxs"}) ranked by dayNtlVlm (24h notional USD). Falls back to
ranking from 1h candle volume over a list of major perps when the API is unreachable
(noted in the report/notification).
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

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
HOURS_TO_FETCH_1H = 120   # candles needed for EMA50 + RSI + volume on the 1h series
CANDLES_4H = 80           # 80 × 4h = 13.3 days → EMA50 on 4h

# Candidate list for the 1h-candle-volume fallback ranking.
FALLBACK_CANDIDATES = [
    "BTC", "ETH", "SOL", "HYPE", "ZEC", "DOGE", "XRP", "VVV", "PONS", "PUMP",
    "ATOM", "WLD", "UNI", "ENA", "XMR", "TAO", "ARB", "SUI", "LINK", "AVAX",
]


def _state_file(trading_pair):
    """Per-pair state file: each pair gets its own last_notify_ts + history,
    so one pair's notification does not pause another pair's cooldown."""
    safe = "".join(ch if ch.isalnum() else "_" for ch in trading_pair)
    return os.path.join(tempfile.gettempdir(), f"hyperliquid_top5_scan_state_{safe}.json")


def _report_state_file():
    """Report-level state: cooldown for the ranked-summary board."""
    return os.path.join(tempfile.gettempdir(), "hyperliquid_top5_scan_report_state.json")


class Config(BaseModel):
    """Top-5 Hyperliquid perps by 24h volume, composite entry signal on each, ranked Telegram summary every 4h."""
    interval_hours: float = Field(default=4.0, description="How often to scan (hours)")
    connector: str = Field(default="hyperliquid_perpetual", description="Perp connector")
    top_n: int = Field(default=5, ge=1, le=20, description="How many top perps by 24h volume to scan")
    min_volume_usd: float = Field(default=10_000_000, description="Only include pairs with 24h notional >= this (USD)")
    exclude_pairs: list = Field(default_factory=list, description="Symbols/pairs to skip (e.g. ['ZEC-USD'] or ['ZEC'])")
    confidence_threshold: float = Field(default=0.65, ge=0.0, le=1.0, description="Minimum confidence (0-1) for a pair to alert")
    notify_mode: str = Field(default="signals_only", pattern="^(signals_only|summary_always)$",
                             description="signals_only: send board only when >=1 pair fires; summary_always: send every scan")
    cooldown_hours: float = Field(default=4.0, description="Min hours between notifications (per pair + summary board)")
    funding_filter_enabled: bool = Field(default=True, description="Apply the crowded-funding filter")
    funding_crowded_annualized_pct: float = Field(default=20.0, description="Funding (annualized %) beyond which a side is 'crowded'")
    ema_fast: int = Field(default=20, ge=2, description="Fast EMA period")
    ema_slow: int = Field(default=50, ge=2, description="Slow EMA period")
    rsi_window: int = Field(default=14, ge=2, description="RSI lookback window")
    volume_window: int = Field(default=24, ge=2, description="Candles used for the average-volume baseline")
    dry_run: bool = Field(default=False, description="Test mode: compute once, print board + signals, no loop, no notify")


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


# ------------------------------------------------------------------ ranking
def _excluded(name, pair, excludes):
    for ex in excludes or []:
        e = str(ex).upper().strip().replace("-USD", "")
        if e and (e == name.upper() or e == pair.upper()):
            return True
    return False


async def _fetch_top_perps(cfg):
    """Rank tradable perps by dayNtlVlm via the Hyperliquid public info API.
    Returns (ranked_rows, source) or (None, error_str) on any failure."""
    try:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.post(HL_INFO_URL,
                              data=json.dumps({"type": "metaAndAssetCtxs"}),
                              headers={"Content-Type": "application/json"}, timeout=30) as resp:
                if resp.status != 200:
                    logger.warning("hyperliquid info api status %s", resp.status)
                    return None, f"meta API http {resp.status}"
                payload = await resp.json()
    except Exception as e:
        logger.warning("hyperliquid info api failed: %s", e)
        return None, f"meta API error: {e}"

    if not isinstance(payload, list) or len(payload) < 2:
        return None, "meta API unexpected shape"
    universe = payload[0].get("universe", []) if isinstance(payload[0], dict) else []
    ctxs = payload[1] if isinstance(payload[1], list) else []

    rows = []
    for u, c in zip(universe, ctxs):
        name = str(u.get("name") or "") if isinstance(u, dict) else ""
        if not name:
            continue
        try:
            vlm = float(c.get("dayNtlVlm") or 0)
            mark = float(c.get("markPx") or 0)
            funding = float(c.get("funding") or 0)
        except Exception:
            continue
        pair = f"{name}-USD"
        if vlm < cfg.min_volume_usd or _excluded(name, pair, cfg.exclude_pairs):
            continue
        rows.append({"symbol": name, "pair": pair, "volume_24h": vlm, "mark_px": mark, "funding_raw": funding})

    if not rows:
        return None, "no pairs above min_volume_usd"
    rows.sort(key=lambda r: r["volume_24h"], reverse=True)
    for i, r in enumerate(rows[: cfg.top_n], start=1):
        r["rank"] = i
    return rows[: cfg.top_n], "metaAndAssetCtxs"


async def _fetch_top_perps_candle_fallback(client, cfg):
    """Fallback: rank from 24h of 1h candle volume (~notional) over major perps."""
    end = int(time.time())
    start = end - 24 * 3600
    rows = []
    for sym in FALLBACK_CANDIDATES:
        pair = f"{sym}-USD"
        if _excluded(sym, pair, cfg.exclude_pairs):
            continue
        try:
            candles = _sort_candles(await fetch_historical_candles(
                client, cfg.connector, pair, interval="1h", start_time=start, end_time=end))
        except Exception as e:
            logger.warning("fallback candles %s: %s", pair, e)
            continue
        if not candles:
            continue
        base_vol = sum(c.get("volume") or 0 for c in candles)
        avg = sum(c["close"] for c in candles) / len(candles)
        notional = base_vol * avg
        if notional < cfg.min_volume_usd:
            continue
        funding = None
        try:
            fi = await client.market_data.get_funding_info(cfg.connector, pair)
            if isinstance(fi, dict):
                funding = fi.get("funding_rate")
        except Exception:
            pass
        rows.append({"symbol": sym, "pair": pair, "volume_24h": notional,
                     "mark_px": avg, "funding_raw": funding})
    rows.sort(key=lambda r: r["volume_24h"], reverse=True)
    for i, r in enumerate(rows[: cfg.top_n], start=1):
        r["rank"] = i
    return rows[: cfg.top_n]


# ------------------------------------------------------------------ signal
async def _fetch_pair_data(client, cfg, trading_pair, meta_funding):
    now = time.time()
    end = int(now)
    start1 = int(now - HOURS_TO_FETCH_1H * 3600)
    start4 = int(now - CANDLES_4H * 4 * 3600)
    try:
        c1 = _sort_candles(await fetch_historical_candles(
            client, cfg.connector, trading_pair, interval="1h", start_time=start1, end_time=end))
    except Exception as e:
        logger.warning("1h candles failed for %s: %s", trading_pair, e)
        c1 = []
    try:
        c4 = _sort_candles(await fetch_historical_candles(
            client, cfg.connector, trading_pair, interval="4h", start_time=start4, end_time=end))
    except Exception as e:
        logger.warning("4h candles failed for %s: %s", trading_pair, e)
        c4 = []
    funding = meta_funding
    try:
        fi = await client.market_data.get_funding_info(cfg.connector, trading_pair)
        if isinstance(fi, dict) and fi.get("funding_rate") is not None:
            funding = fi.get("funding_rate")
    except Exception as e:
        logger.warning("funding fetch failed %s: %s", trading_pair, e)
    return c1, c4, funding


def _compute_signal(c1, c4, funding_raw, cfg):
    """Return dict with direction, components, confidence — or None when the
    timeframe trend does not cleanly pick a direction. Identical to hyperliquid_perp_scan."""
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


async def _pair_signal(client, cfg, row):
    pair = row["pair"]
    c1, c4, funding = await _fetch_pair_data(client, cfg, pair, row.get("funding_raw"))
    sig = _compute_signal(c1, c4, funding, cfg)
    fund_ann = _funding_annualized_pct(funding) if funding is not None else None
    price = sig.get("entry_price") if sig.get("entry_price") else row.get("mark_px")
    return {**row, "candles1": len(c1), "candles4": len(c4),
            "fund_ann": fund_ann, "price": price, "sig": sig}


async def _scan_once(client, cfg):
    """Rank top perps and compute the signal on each. Returns (ranked_rows, source)."""
    ranked, source = await _fetch_top_perps(cfg)
    if ranked is None:
        ranked = await _fetch_top_perps_candle_fallback(client, cfg)
        source = "1h candle fallback"
    results = await asyncio.gather(*(_pair_signal(client, cfg, r) for r in ranked))
    now = time.time()
    for r in results:
        sig = r["sig"]
        crossed = sig.get("direction") is not None and sig["confidence"] >= cfg.confidence_threshold
        st = _load_state(_state_file(r["pair"]))
        last = float(st.get("last_notify_ts") or 0)
        r["cooldown_ok"] = (now - last) >= cfg.cooldown_hours * 3600
        r["fired"] = bool(crossed and r["cooldown_ok"])
        r["pair_state"] = st
    return results, source


# ------------------------------------------------------------ notifications
def _fmt_price(p):
    if p is None:
        return "?"
    if p >= 1000:
        return f"{p:,.0f}"
    if p >= 1:
        return f"{p:,.2f}"
    return f"{p:.4f}".rstrip("0").rstrip(".")


def _build_notify_text(ranked, cfg, source):
    lines = [f"\U0001F4CA Hyperliquid top {len(ranked)} by 24h vol"]
    for r in ranked:
        sig = r["sig"]
        if sig.get("direction") is not None:
            side = "\U0001F535LONG" if sig["direction"] == "LONG" else "\U0001F534SHORT"
            s = f"{side} {round(sig['confidence'] * 100)}%"
        else:
            s = "flat"
        fund = f"fund {r['fund_ann']:+.1f}%/yr" if r.get("fund_ann") is not None else "fund n/a"
        lines.append(f" {r['rank']}. {r['pair']}  ${_fmt_price(r['price'])}  {s} · {fund}")
    fired = [r for r in ranked if r["fired"]]
    if fired:
        lines.append("")
        for r in fired:
            sig = r["sig"]
            side = "\U0001F535 LONG" if sig["direction"] == "LONG" else "\U0001F534 SHORT"
            fund = f"funding {r['fund_ann']:+.1f}%/yr" if r.get("fund_ann") is not None else "funding n/a"
            lines.append(f"\U0001F514 ALERT: {r['pair']} {side} {round(sig['confidence'] * 100)}% — "
                         f"entry ~${_fmt_price(sig['entry_price'])}, RSI {sig['rsi']:.0f}, {fund}")
    if source != "metaAndAssetCtxs":
        lines.append("")
        lines.append(f"(ranking: {source} — meta API unavailable this scan)")
    return "\n".join(lines)


async def _dry_run(client, cfg):
    ranked, source = await _scan_once(client, cfg)
    out = [f"DRY-RUN hyperliquid_top5_scan · source={source}"]
    out.append(f"TOP {len(ranked)} by 24h notional volume:")
    for r in ranked:
        out.append(f"  {r['rank']}. {r['pair']}  ${r['volume_24h']/1e9:,.2f}B 24h  (mark ${_fmt_price(r['mark_px'])}, "
                   f"funding_raw={r['funding_raw']})")
    out.append("")
    out.append(f"signal per pair (threshold={cfg.confidence_threshold}):")
    crossed = []
    for r in ranked:
        sig = r["sig"]
        pair = r["pair"]
        fund = f"{r['fund_ann']:+.1f}%/yr" if r.get("fund_ann") is not None else "n/a"
        if sig.get("error"):
            out.append(f"  {pair}: ERROR {sig['error']} (c1={r['candles1']}, c4={r['candles4']}, funding={fund})")
            continue
        if sig.get("direction") is None:
            out.append(f"  {pair}: flat (reason={sig.get('reason')}) funding={fund}")
            continue
        conf = sig["confidence"]
        mark = "  <-- ALERT" if conf >= cfg.confidence_threshold else ""
        out.append(f"  {pair}: {sig['direction']} conf={conf:.3f} rsi={sig['rsi']:.1f} funding={fund} "
                   f"{sig['rsi_note']} · {sig['fund_note']} · {sig['vol_note']}{mark}")
        if conf >= cfg.confidence_threshold:
            crossed.append(pair)
    out.append("")
    out.append(f"would alert: {crossed if crossed else 'none'}")
    return "\n".join(out)


# ---------------------------------------------------------------------- run
async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id = getattr(context, "_chat_id", None)

    client = await get_client(chat_id, context=context)
    if not client:
        return "No server available"

    if config.dry_run:
        return await _dry_run(client, config)

    notifications = 0
    started = time.time()
    interval_sec = max(60, int(config.interval_hours * 3600))

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"\U0001F7A2 hyperliquid_top5_scan started — scanning top {config.top_n} Hyperliquid "
                 f"perps by 24h volume every {config.interval_hours:g}h. I'll ping the ranked board "
                 f"when a high-confidence signal fires (mode: {config.notify_mode}).",
        )
    except Exception as e:
        logger.error("start msg failed: %s", e)

    report = LiveReport(
        "Hyperliquid Top-5 Perp Scan",
        source_name="hyperliquid_top5_scan",
        tags=["monitoring", "hyperliquid", "signal", "live"],
    )

    try:
        while True:
            try:
                client = await get_client(chat_id, context=context)
                if not client:
                    await asyncio.sleep(interval_sec)
                    continue

                ranked, source = await _scan_once(client, config)

                now = time.time()
                rep_state = _load_state(_report_state_file())
                last_summary = float(rep_state.get("last_summary_ts") or 0)
                rep_ok = (now - last_summary) >= config.cooldown_hours * 3600
                any_fired = any(r["fired"] for r in ranked)
                send = (config.notify_mode == "summary_always" and rep_ok) or \
                       (config.notify_mode == "signals_only" and any_fired and rep_ok)

                if send:
                    text = _build_notify_text(ranked, config, source)
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=text)
                        rep_state["last_summary_ts"] = now
                        _save_state(_report_state_file(), rep_state)
                        for r in ranked:
                            if r["fired"]:
                                st = r["pair_state"]
                                st["last_notify_ts"] = now
                                st["history"] = (st.get("history") or []) + [{
                                    "ts": datetime.now(timezone.utc).strftime("%m-%d %H:%M"),
                                    "pair": r["pair"], "direction": r["sig"]["direction"],
                                    "conf": round(r["sig"]["confidence"] * 100),
                                    "price": round(r["sig"]["entry_price"]),
                                }]
                                _save_state(_state_file(r["pair"]), st)
                                notifications += 1
                    except Exception as e:
                        logger.error("summary msg failed: %s", e)

                report.clear()
                report.builder.manual_order()
                report.builder.section("LATEST SCAN", f"Ranked board · source {source}")
                rows = []
                for r in ranked:
                    sig = r["sig"]
                    rows.append({
                        "Rank": r["rank"], "Pair": r["pair"],
                        "Price": f"${_fmt_price(r['price'])}",
                        "Dir": sig.get("direction") or ("—" if sig.get("error") else "flat"),
                        "Conf": f"{sig['confidence']:.2f}" if sig.get("direction") else "—",
                        "Funding": f"{r['fund_ann']:+.1f}%/yr" if r.get("fund_ann") is not None else "—",
                        "Alert": "\U0001F514" if r["fired"] else "",
                    })
                report.builder.table(rows, ["Rank", "Pair", "Price", "Dir", "Conf", "Funding", "Alert"])
                report.builder.section("ALERTS", "Fired this scan")
                fired_rows = [{"Pair": r["pair"],
                               "Signal": f"{r['sig']['direction']} {round(r['sig']['confidence']*100)}%"}
                              for r in ranked if r["fired"]]
                report.builder.table(fired_rows, ["Pair", "Signal"] if fired_rows else [])
                report.builder.markdown(
                    f"_Last check: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} · "
                    f"interval {config.interval_hours:g}h · threshold {config.confidence_threshold:.2f} · "
                    f"mode {config.notify_mode}_"
                )
                await report.update()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("hyperliquid_top5_scan tick error: %s", e)

            await asyncio.sleep(interval_sec)

    except asyncio.CancelledError:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"\u23F9 hyperliquid_top5_scan stopped · alerts sent: {notifications}",
            )
        except Exception:
            pass
        if report.report_id is not None:
            report.clear()
            report.builder.auto_refresh(None)
            report.builder.section("SCAN STOPPED", "Final snapshot")
            report.builder.markdown(f"Alerts sent: {notifications}")
            await report.update()
        return f"Stopped after {(time.time() - started)/3600:.1f}h, {notifications} alerts"