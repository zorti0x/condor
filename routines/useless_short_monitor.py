"""Watch the USELESS-USD short: entry fill + TP/SL vs live price (continuous)."""

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

STATE_FILE = os.path.join(tempfile.gettempdir(), "useless_short_monitor_state.json")
EXECUTOR_ID = "9QRonjjrgcvR9kmeXv4UuRKXgeDXrLMfNvRYHPim2Uvd"

ENTRY = 0.2885
TAKE_PROFIT = ENTRY * (1 - 0.04)   # ~0.2772 (short pays when price falls)
STOP_LOSS = ENTRY * (1 + 0.03)     # ~0.2972 (short cut when price rises)
TIME_LIMIT_H = 24


class Config(BaseModel):
    interval_sec: int = Field(default=60, description="Poll interval in seconds")
    connector: str = Field(default="hyperliquid_perpetual", description="perp connector")
    trading_pair: str = Field(default="USELESS-USD", description="pair to watch")


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
        logger.warning(f"persist failed: {e}")


async def _fetch_price(client, connector, pair):
    try:
        prices = await client.market_data.get_prices(connector_name=connector, trading_pairs=pair)
    except Exception as e:
        logger.warning(f"get_prices failed: {e}")
        return None
    try:
        data = prices.get("prices", prices) if isinstance(prices, dict) else prices
        if isinstance(data, dict):
            return data.get(pair)
    except Exception:
        pass
    return None


async def _fetch_executor(client):
    """Return executor dict or None on failure."""
    try:
        ex = await client.executors.get_executor(EXECUTOR_ID)
        if isinstance(ex, dict):
            return ex
        return ex
    except Exception as e:
        logger.warning(f"get_executor failed: {e}")
        return None


def _executor_outcome(ex):
    """Return (filled, closed, pnl) if discernible from executor payload."""
    if not isinstance(ex, dict):
        return None, None, None
    status = str(ex.get("status", "")).upper()
    filled = status in ("RUNNING",) and bool(ex.get("current_position_average_price")) and float(ex.get("current_position_average_price", 0) or 0) > 0
    closed = status in ("TERMINATED", "CLOSED")
    pnl = ex.get("net_pnl_quote") or ex.get("net_pnl") or 0
    return filled, closed, pnl


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

    def esc(x): return escape_markdown_v2(str(x))

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🟢 *USELESS short monitor started*\n"
                f"Entry limit: `${esc(ENTRY)}`  TP: `${esc(TAKE_PROFIT)}`  SL: `${esc(STOP_LOSS)}`\n"
                f"Poll: `{config.interval_sec}s` · auto-stop ~`{TIME_LIMIT_H}h`"
            ),
            parse_mode="MarkdownV2",
        )
    except Exception as e:
        logger.error(f"start msg failed: {e}")

    from condor.reports import LiveReport
    report = LiveReport("USELESS Short Monitor", source_name="useless_short_monitor", tags=["monitoring", "hyperliquid", "live"])

    # alert-once flags
    warned_entry = state.get("warned_entry", False)
    warned_tp = state.get("warned_tp", False)
    warned_sl = state.get("warned_sl", False)
    notified_filled = state.get("notified_filled", False)

    try:
        while True:
            try:
                elapsed_h = (time.time() - started) / 3600
                if elapsed_h > TIME_LIMIT_H:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="⏰ *USELESS watch time limit reached — monitor stopping.*\nThe 24h executor safety net also covers the position itself.",
                        parse_mode="MarkdownV2",
                    )
                    return "time limit reached"

                client = await get_client(chat_id, context=context)
                price = await _fetch_price(client, config.connector, config.trading_pair)
                ex = await _fetch_executor(client)

                filled, closed, pnl = _executor_outcome(ex)

                rows = []
                status = "waiting-entry"
                note = f"price ${price:,.4f} < entry ${ENTRY}"

                if price is not None:
                    if price >= STOP_LOSS:
                        status, note = "ABOVE-SL", f"price ${price:,.4f} >= SL ${STOP_LOSS}"
                    elif price <= TAKE_PROFIT:
                        status, note = "AT/under-TP", f"price ${price:,.4f} <= TP ${TAKE_PROFIT}"
                    elif price >= ENTRY:
                        status, note = "AT/over-entry", f"price ${price:,.4f} >= entry ${ENTRY}"
                    else:
                        status, note = "below-entry", f"price ${price:,.4f} < entry ${ENTRY}"

                if filled and not notified_filled:
                    notified_filled = True
                    alerts_sent += 1
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=(
                                "🔵 *USELESS short ENTRY FILLED* — you're in!*\n"
                                f"Position open (short {config.trading_pair}).\n"
                                f"TP `${esc(TAKE_PROFIT)}` · SL `${esc(STOP_LOSS)}` — bot manages the exit.\n"
                                f"Now: `${esc(price)}`"
                            ),
                            parse_mode="MarkdownV2",
                        )
                    except Exception as e:
                        logger.error(f"fill alert failed: {e}")

                if closed and not state.get("notified_closed", False):
                    state["notified_closed"] = True
                    alerts_sent += 1
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=(
                                "🏁 *USELESS short CLOSED* — position exited.\n"
                                f"Net PnL: `${esc(pnl)}`\n"
                                f"Final price: `${esc(price)}`"
                            ),
                            parse_mode="MarkdownV2",
                        )
                    except Exception as e:
                        logger.error(f"close alert failed: {e}")

                # Edge-triggered band alerts (price-based only)
                if price is not None:
                    if price >= STOP_LOSS and not warned_sl:
                        warned_sl = True
                        alerts_sent += 1
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=(
                                "🔴 *USELESS hit STOP-LOSS zone* "
                                f"`${esc(price)}` >= SL `${esc(STOP_LOSS)}`\n"
                                "If short is open the executor is cutting now."
                            ),
                            parse_mode="MarkdownV2",
                        )
                    elif price <= TAKE_PROFIT and not warned_tp:
                        warned_tp = True
                        alerts_sent += 1
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=(
                                "🟢 *USELESS hit TAKE-PROFIT zone* "
                                f"`${esc(price)}` <= TP `${esc(TAKE_PROFIT)}`\n"
                                "If short is open the executor is taking profit now."
                            ),
                            parse_mode="MarkdownV2",
                        )
                    elif price >= ENTRY and not warned_entry:
                        warned_entry = True

                state["warned_entry"], state["warned_tp"], state["warned_sl"] = warned_entry, warned_tp, warned_sl
                state["notified_filled"] = notified_filled
                _save_state(state)

                rows.append({
                    "Pair": config.trading_pair,
                    "Price": f"${price:,.4f}" if price is not None else "n/a",
                    "Entry": str(ENTRY),
                    "TP": f"{TAKE_PROFIT:.4f}",
                    "SL": f"{STOP_LOSS:.4f}",
                    "Executor": status,
                    "Note": note,
                })

                report.clear()
                report.builder.manual_order()
                report.builder.kpi("Price", f"${price:,.4f}" if price is not None else "-")
                report.builder.kpi("Entry", str(ENTRY))
                report.builder.kpi("TP / SL", f"{TAKE_PROFIT:.4f} / {STOP_LOSS:.4f}")
                report.builder.section("USELESS SHORT WATCH", "Live price vs entry/TP/SL + executor fill state")
                report.builder.table(rows, ["Pair", "Price", "Entry", "TP", "SL", "Executor", "Note"])
                report.builder.markdown(f"_Last check: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}_")
                await report.update()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"useless_short_monitor tick error: {e}")

            await asyncio.sleep(config.interval_sec)

    except asyncio.CancelledError:
        elapsed = int(time.time() - started)
        mins, secs = divmod(elapsed, 60)
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔴 *USELESS short monitor stopped.*\nDuration: {mins}m {secs}s · Alerts: {alerts_sent}",
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass
        return f"Stopped after {mins}m {secs}s, {alerts_sent} alerts"