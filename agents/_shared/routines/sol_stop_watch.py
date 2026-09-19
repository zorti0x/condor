"""SOL-USD price stop watch — Telegram alerts near a manual stop-loss.

Continuous monitor for a spot/perp position you are managing by hand. Polls the
SOL-USD perpetual price on hyperliquid_perpetual and sends a Telegram WARNING
when price first drops below a warn level, and an ALERT when it first hits the
stop level. Each level fires once per breach and re-arms only after price
recovers above it again. Pure alerting — never places orders.

Works with no chat_id (dashboard/scheduler starts): it goes through
condor.notifications, which sends Telegram when a chat is present and otherwise
files the alert on the user's notification bell. Fetch errors are logged and
skipped, never fatal.
"""

CATEGORY = "Monitoring"

import asyncio
import logging
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client

logger = logging.getLogger(__name__)

CONTINUOUS = True


class Config(BaseModel):
    """SOL-USD stop watch: Telegram WARNING below the warn level, ALERT at/below the stop level."""

    connector_name: str = Field(default="hyperliquid_perpetual", description="Perp connector")
    trading_pair: str = Field(default="SOL-USD", description="Pair to watch")
    warn_level: float = Field(default=100.50, description="WARN below this price (approaching stop)")
    stop_level: float = Field(default=99.00, description="ALERT at/below this price (stop level)")
    interval_sec: int = Field(default=60, description="Poll interval in seconds")
    entry_price: float = Field(default=100.41, description="Position entry price (report context)")
    position_size: float = Field(default=5.01, description="Position size in base asset (report context)")


def evaluate(price, warn, stop, aw, aa):
    """Fire-once edge triggers per level.

    Returns (warn_hit, alert_hit, new_aw, new_aa). warn_hit is True exactly on
    the first tick price < warn while armed; alert_hit on the first tick
    price <= stop while armed. Each rearms once price is back above it.
    """
    warn_hit = alert_hit = False
    if price is None:
        return warn_hit, alert_hit, aw, aa
    if warn <= stop:
        raise ValueError("warn_level must be above stop_level")
    if price < warn:
        if aw:
            warn_hit, aw = True, False
    else:
        aw = True
    if price <= stop:
        if aa:
            alert_hit, aa = True, False
    else:
        aa = True
    return warn_hit, alert_hit, aw, aa


def status_for(price, warn, stop):
    if price is None:
        return "n/a"
    if price <= stop:
        return "STOP HIT"
    if price < warn:
        return "BELOW WARN"
    return "OK"


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


async def _notify(context, text, kind="alert"):
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


async def _fetch_price(client, config):
    """Current price or None. Defensive parse of the get_prices response."""
    try:
        res = await client.market_data.get_prices(
            connector_name=config.connector_name,
            trading_pairs=[config.trading_pair],
        )
    except Exception as e:
        logger.warning("get_prices failed for %s: %s", config.trading_pair, e)
        return None
    if not isinstance(res, dict):
        logger.warning("unexpected get_prices response type: %s", type(res).__name__)
        return None
    data = res.get("prices", res)
    if not isinstance(data, dict):
        logger.warning("unexpected prices payload: %r", res)
        return None
    return data.get(config.trading_pair)


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    if config.warn_level <= config.stop_level:
        return (
            f"Invalid config: warn_level ({config.warn_level}) must be above "
            f"stop_level ({config.stop_level})"
        )

    chat_id = getattr(context, "_chat_id", None) or None
    client = await get_client(chat_id, context=context)
    if not client:
        return "No server available"

    base = config.trading_pair.split("-")[0]
    await _notify(
        context,
        (
            f"\U0001f7e2 {config.trading_pair} stop watch started\n"
            f"WARN below ${config.warn_level:.2f} | ALERT at/below ${config.stop_level:.2f}\n"
            f"Poll every {config.interval_sec}s"
        ),
        kind="system",
    )

    state = {"armed_warn": True, "armed_alert": True, "ticks": 0, "alerts": 0, "last_price": None}
    history = []

    from condor.reports import LiveReport

    report = LiveReport(
        f"{config.trading_pair} Stop Watch",
        source_name="sol_stop_watch",
        tags=["monitoring", "alert", "live"],
        auto_refresh_seconds=config.interval_sec,
    )

    try:
        while True:
            try:
                client = await get_client(chat_id, context=context)
                if not client:
                    await asyncio.sleep(config.interval_sec)
                    continue

                price = await _fetch_price(client, config)
                state["ticks"] += 1

                if price is not None:
                    state["last_price"] = price
                    warn_hit, alert_hit, state["armed_warn"], state["armed_alert"] = evaluate(
                        price,
                        config.warn_level,
                        config.stop_level,
                        state["armed_warn"],
                        state["armed_alert"],
                    )
                    if warn_hit:
                        state["alerts"] += 1
                        await _notify(
                            context,
                            (
                                f"\u26a0\ufe0f {config.trading_pair} WARNING\n"
                                f"Dropped below ${config.warn_level:.2f} (approaching your "
                                f"${config.stop_level:.2f} stop)\n"
                                f"Current price: ${price:.2f}"
                            ),
                        )
                    if alert_hit:
                        state["alerts"] += 1
                        await _notify(
                            context,
                            (
                                f"\U0001f6a8 {config.trading_pair} ALERT \u2014 STOP-LEVEL HIT\n"
                                f"At/below your ${config.stop_level:.2f} stop-loss\n"
                                f"Current price: ${price:.2f}"
                            ),
                        )

                status = status_for(price, config.warn_level, config.stop_level)
                logger.info(
                    "tick=%d price=%s status=%s armed_warn=%s armed_alert=%s alerts=%d",
                    state["ticks"],
                    f"${price:.2f}" if price is not None else "n/a",
                    status,
                    state["armed_warn"],
                    state["armed_alert"],
                    state["alerts"],
                )

                history.append(
                    {
                        "Time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                        "Price": f"${price:,.2f}" if price is not None else "n/a",
                        "Status": status,
                    }
                )
                if len(history) > 50:
                    history = history[-50:]

                # Report block is intentionally unguarded (its failures must surface).
                report.clear()
                report.builder.manual_order()
                report.builder.kpi("Current", f"${price:,.2f}" if price is not None else "n/a")
                report.builder.kpi("Warn Level", f"${config.warn_level:,.2f}")
                report.builder.kpi("Stop Level", f"${config.stop_level:,.2f}")
                report.builder.kpi("Status", status)
                report.builder.kpi("Alerts", str(state["alerts"]))
                report.builder.kpi("Ticks", str(state["ticks"]))
                report.builder.section("POSITION", "Manual stop-loss watch context")
                report.builder.markdown(
                    f"Position: **{config.position_size}** {base} @ entry **${config.entry_price:,.2f}**"
                    f" | Stop-loss: **${config.stop_level:,.2f}** | Warn below: **${config.warn_level:,.2f}**"
                )
                report.builder.section("RECENT CHECKS", "Price / status per poll")
                report.builder.table(history)
                report.builder.markdown(
                    f"_Last check: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}_"
                )
                await report.update()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("sol_stop_watch tick error: %s", e, exc_info=True)

            await asyncio.sleep(config.interval_sec)

    except asyncio.CancelledError:
        last = f"${state['last_price']:,.2f}" if state["last_price"] is not None else "n/a"
        await _notify(
            context,
            (
                f"\U0001f6d1 {config.trading_pair} stop watch stopped\n"
                f"Last price: {last} | Ticks: {state['ticks']} | Alerts: {state['alerts']}"
            ),
            kind="system",
        )
        # Leave a fixed stopped snapshot.
        report.clear()
        report.builder.auto_refresh(None)
        report.builder.section("MONITOR STOPPED", "Final fixed snapshot")
        report.builder.markdown(
            f"Last price: {last} | Ticks: {state['ticks']} | Alerts: {state['alerts']}"
        )
        await report.update()
        return f"Stopped after {state['ticks']} ticks, {state['alerts']} alerts"