import asyncio
import logging
from pydantic import BaseModel, Field
from config_manager import get_client
from condor.reports import LiveReport

logger = logging.getLogger(__name__)

CATEGORY = "Monitoring"
CONTINUOUS = True


class Config(BaseModel):
    """Hourly funding-carry monitor for Hyperliquid perps — alerts when any pair's funding rate crosses a configurable threshold in either direction (long-crowded or short-crowded)."""
    trading_pairs: list = Field(
        default=["BTC-USD", "ETH-USD", "SOL-USD"],
        description="Perpetual pairs to monitor",
    )
    connector_name: str = Field(
        default="hyperliquid_perpetual",
        description="Perpetual connector",
    )
    carry_threshold_pct: float = Field(
        default=0.01,
        description="Alert when |funding| exceeds this percent (e.g. 0.01 = 0.01%). Positive = long-crowded, negative = short-crowded.",
    )
    interval_sec: int = Field(
        default=3600,
        description="Seconds between checks (hourly)",
    )
    max_ticks: int = Field(
        default=0,
        description="0 = run until stopped; >0 = run N ticks then stop (for one-shot tests)",
    )


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _fetch(client, connector, pair):
    """Fetch one pair's funding info defensively. Returns a dict or None."""
    try:
        info = await client.market_data.get_funding_info(connector, pair)
    except Exception as exc:
        logger.warning("funding fetch failed for %s on %s: %s", pair, connector, exc)
        return None
    if not isinstance(info, dict):
        return None
    raw = _to_float(info.get("funding_rate"))
    if raw is None:
        return None
    return {
        "pair": pair,
        "funding_pct": raw * 100.0,
        "next_funding_time": info.get("next_funding_time"),
        "mark_price": info.get("mark_price"),
        "index_price": info.get("index_price"),
    }


def _side(pct, threshold):
    if pct > threshold:
        return "LONG-CROWDED"
    if pct < -threshold:
        return "SHORT-CROWDED"
    return None


async def run(config: Config, context):
    report = LiveReport(
        "Hyperliquid Funding Carry Monitor",
        source_name="funding_carry_monitor",
        tags=["monitoring", "funding", "hyperliquid"],
        auto_refresh_seconds=config.interval_sec,
    )
    chat_id = getattr(context, "_chat_id", None)
    alerts_sent = []
    ticks = 0
    try:
        while True:
            ticks += 1
            client = await get_client(chat_id, context=context)
            if not client:
                logger.warning("tick %s: no server available", ticks)
                if config.max_ticks and ticks >= config.max_ticks:
                    break
                await asyncio.sleep(config.interval_sec)
                continue

            triggered = []
            rows = []
            for pair in config.trading_pairs:
                info = await _fetch(client, config.connector_name, pair)
                if info is None:
                    rows.append({"Pair": pair, "Funding": "n/a", "Side": "n/a", "Next funding": "n/a"})
                    continue
                pct = info["funding_pct"]
                side = _side(pct, config.carry_threshold_pct)
                rows.append({
                    "Pair": pair,
                    "Funding": f"{pct:+.4f}%",
                    "Side": side or "normal",
                    "Next funding": str(info["next_funding_time"] or "n/a"),
                })
                if side:
                    triggered.append((pair, pct, side))

            if triggered:
                lines = ["🚨 Funding carry on {} — threshold ±{}%:".format(config.connector_name, config.carry_threshold_pct)]
                lines += [f"• {pair}: {pct:+.4f}% — {side}" for pair, pct, side in triggered]
                alert_msg = "\n".join(lines)
                try:
                    if chat_id:
                        await context.bot.send_message(chat_id=chat_id, text=alert_msg)
                        alerts_sent.append(alert_msg)
                    else:
                        logger.info("carry alert (no chat_id, not sent):\n%s", alert_msg)
                except Exception as exc:
                    logger.warning("alert send failed: %s", exc)

            report.clear()
            report.builder.manual_order()
            report.builder.section(
                "01 / FUNDING SNAPSHOT",
                f"{config.connector_name} — {config.interval_sec // 60}m check, carry threshold ±{config.carry_threshold_pct}%",
            )
            report.builder.kpi("Threshold", f"±{config.carry_threshold_pct}%")
            report.builder.kpi("Pairs", f"{len(rows)}")
            report.builder.kpi("Crowded this tick", str(len(triggered)))
            report.builder.table(rows, ["Pair", "Funding", "Side", "Next funding"])
            if alerts_sent:
                report.builder.markdown("**Alerts sent this session:**\n\n" + "\n\n".join(alerts_sent))
            await report.update()

            if config.max_ticks and ticks >= config.max_ticks:
                break
            await asyncio.sleep(config.interval_sec)

    except asyncio.CancelledError:
        raise
    finally:
        if report.report_id is not None:
            report.clear()
            report.builder.auto_refresh(None)
            report.builder.section("MONITOR STOPPED", f"{ticks} tick(s) completed")
            await report.update()

    return f"funding_carry_monitor completed {ticks} tick(s); {len(alerts_sent)} alert(s) sent."