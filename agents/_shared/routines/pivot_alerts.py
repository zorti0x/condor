"""Livermore PP2 pivot band alerts - notify when a price enters a band (continuous)."""

CATEGORY = "Monitoring"

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
from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)

CONTINUOUS = True

STATE_FILE = os.path.join(tempfile.gettempdir(), "pivot_alerts_state.json")


class Watch(BaseModel):
    """One instrument band watch."""
    instrument: str = Field(..., description="Friendly name (LINK, HYPE, ...)")
    connector: str = Field(..., description="CEX/perp connector name")
    trading_pair: str = Field(..., description="Trading pair (LINK-USDT, HYPE-USD)")
    lower: float = Field(..., description="Lower band bound (inclusive)")
    upper: float = Field(..., description="Upper band bound (inclusive)")


class Config(BaseModel):
    """Livermore PP2 pivot band alerts: watch instruments for entry into a configured price band.

    Each watch has a connector + trading_pair + inclusive [lower, upper] band. Fires a
    Telegram alert on band ENTRY (edge-triggered), never on repeat polls inside the band.
    State is persisted so a restart resumes without re-firing already-in-band watches.
    """
    watches: list[Watch] = Field(
        default_factory=lambda: [
            Watch(instrument="LINK", connector="binance", trading_pair="LINK-USDT", lower=9.0, upper=9.30),
            Watch(instrument="HYPE", connector="hyperliquid_perpetual", trading_pair="HYPE-USD", lower=60.0, upper=62.0),
        ],
        description="Instruments: connector, pair, and lower/upper PP2 band",
    )
    interval_sec: int = Field(default=60, description="Poll interval in seconds")


def _load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        logger.warning(f"Could not persist pivot state: {e}")


def _key(w: Watch) -> str:
    return f"{w.connector}|{w.trading_pair}"


async def _fetch_price(client, w: Watch):
    """Best effort price fetch; returns None when unavailable."""
    try:
        prices = await client.market_data.get_prices(
            connector_name=w.connector, trading_pairs=w.trading_pair
        )
    except Exception as e:
        logger.warning(f"get_prices failed for {w.instrument}: {e}")
        return None
    try:
        data = prices.get("prices", prices) if isinstance(prices, dict) else prices
        if isinstance(data, dict):
            return data.get(w.trading_pair)
    except Exception:
        pass
    return None


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id = context._chat_id if hasattr(context, "_chat_id") else None

    client = await get_client(chat_id, context=context)
    if not client:
        return "No server available"

    state = _load_state()
    if not isinstance(state, dict):
        state = {}

    alerts_sent = 0
    started = time.time()

    try:
        names = ", ".join(w.instrument for w in config.watches)
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🟢 *Pivot Alerts Started*\n"
                f"Watches: `{escape_markdown_v2(names)}`\n"
                f"Interval: `{config.interval_sec}s`"
            ),
            parse_mode="MarkdownV2",
        )
    except Exception as e:
        logger.error(f"Start message failed: {e}")

    from condor.reports import LiveReport

    report = LiveReport("Pivot Band Alerts", source_name="pivot_alerts", tags=["monitoring", "livermore", "live"])

    try:
        while True:
            try:
                client = await get_client(chat_id, context=context)
                if not client:
                    await asyncio.sleep(config.interval_sec)
                    continue

                rows = []
                for w in config.watches:
                    price = await _fetch_price(client, w)
                    band = f"${w.lower:,.2f}-${w.upper:,.2f}"
                    if price is None:
                        rows.append({"Instrument": w.instrument, "Band": band, "Price": "-", "Status": "n/a"})
                        continue

                    in_band = w.lower <= price <= w.upper
                    if in_band:
                        status = "INSIDE"
                    elif price < w.lower:
                        status = "below"
                    else:
                        status = "above"

                    rows.append({
                        "Instrument": w.instrument,
                        "Band": band,
                        "Price": f"${price:,.2f}",
                        "Status": status,
                    })

                    k = _key(w)
                    prev_in_band = bool(state.get(k, False))

                    # Edge-triggered: only alert on the transition into the band.
                    if in_band and not prev_in_band:
                        alerts_sent += 1
                        try:
                            inst = escape_markdown_v2(w.instrument)
                            band_esc = escape_markdown_v2(band)
                            price_esc = escape_markdown_v2(f"${price:,.2f}")
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=(
                                    f"🔔 *{inst} entered PP2 pivot band*\n"
                                    f"Instrument: `{inst}`\n"
                                    f"Pair: `{escape_markdown_v2(w.trading_pair)}`  ({escape_markdown_v2(w.connector)})\n"
                                    f"Band: `{band_esc}`\n"
                                    f"Price: `{price_esc}`"
                                ),
                                parse_mode="MarkdownV2",
                            )
                        except Exception as e:
                            logger.error(f"Alert send failed for {w.instrument}: {e}")

                    state[k] = in_band

                _save_state(state)

                # Live dashboard (every routine must produce a report)
                report.clear()
                report.builder.manual_order()
                report.builder.kpi("Watches", str(len(config.watches)))
                report.builder.kpi("Alerts Sent", str(alerts_sent))
                report.builder.kpi("Interval", f"{config.interval_sec}s")
                report.builder.section("BAND STATUS", "Current price vs configured PP2 bands")
                report.builder.table(rows, ["Instrument", "Band", "Price", "Status"])
                report.builder.markdown(
                    f"_Last check: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}_"
                )
                await report.update()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"pivot_alerts tick error: {e}")

            await asyncio.sleep(config.interval_sec)

    except asyncio.CancelledError:
        elapsed = int(time.time() - started)
        mins, secs = divmod(elapsed, 60)
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔴 *Pivot Alerts Stopped*\nDuration: {mins}m {secs}s\nAlerts: {alerts_sent}",
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass
        return f"Stopped after {mins}m {secs}s, {alerts_sent} alerts"