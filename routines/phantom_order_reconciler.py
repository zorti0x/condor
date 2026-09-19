import asyncio
import logging
import subprocess
from typing import Any, Dict, List

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client
from condor.reports import LiveReport

logger = logging.getLogger(__name__)

CONTINUOUS = True

CATEGORY = "Monitoring"


class Config(BaseModel):
    """Reconcile 'phantom' order rows — orders stuck at status=OPEN in the Hummingbot DB that no longer exist in the venue's live active-orders feed — so they stop showing up as active orders. Runs a pass every interval_hours; quiet on clean runs, notifies on change or anomaly."""
    interval_hours: float = Field(default=4.0, description="Hours between reconcile passes")
    account_name: str = Field(default="master_account", description="Account whose orders are reconciled")
    dry_run: bool = Field(default=False, description="Compute + report only — never write to the DB")
    db_container: str = Field(default="hummingbot-postgres", description="Docker container that exposes postgres")
    db_user: str = Field(default="hbot", description="Postgres user")
    db_name: str = Field(default="hummingbot_api", description="Postgres database")
    db_table: str = Field(default="orders", description="Postgres orders table")


def _psql(config: Config, sql: str) -> str:
    cmd = ["docker", "exec", config.db_container, "psql",
           "-U", config.db_user, "-d", config.db_name, "-Atc", sql]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"psql failed rc={out.returncode}: {out.stderr.strip()[:400]}")
    return out.stdout


def _parse_open_rows(raw: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 6:
            continue
        rows.append({
            "id": parts[0],
            "client_order_id": parts[1],
            "exchange_order_id": parts[2],
            "trading_pair": parts[3],
            "filled_amount": parts[4],
            "created_at": parts[5],
        })
    return rows


def _extract_live_ids(feed: Any) -> set:
    data = feed.get("data", []) if isinstance(feed, dict) else (feed or [])
    live = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        for key in ("client_order_id", "exchange_order_id", "id"):
            v = item.get(key)
            if v not in (None, ""):
                live.add(str(v))
    return live


def _live_count(feed: Any) -> int:
    data = feed.get("data", []) if isinstance(feed, dict) else (feed or [])
    return len(data)


async def _reconcile(client, config: Config) -> Dict[str, Any]:
    select_sql = (
        f"SELECT id, client_order_id, exchange_order_id, trading_pair, "
        f"filled_amount, created_at FROM {config.db_table} "
        f"WHERE status='OPEN' AND account_name='{config.account_name}' ORDER BY id;"
    )

    feed = await client.trading.get_active_orders([config.account_name], None, limit=200)
    live_ids = _extract_live_ids(feed)
    live_before = _live_count(feed)

    open_rows = _parse_open_rows(_psql(config, select_sql))
    db_before = len(open_rows)

    phantoms: List[Dict[str, Any]] = []
    partial_flags: List[Dict[str, Any]] = []
    for r in open_rows:
        if (str(r["client_order_id"]) in live_ids) or (str(r["exchange_order_id"]) in live_ids):
            continue  # genuinely live
        try:
            filled = float(r["filled_amount"] or 0)
        except ValueError:
            filled = 0.0
        if filled > 0:
            partial_flags.append(r)   # real partial fill — never touch, flag
        else:
            phantoms.append(r)

    closed: List[Dict[str, Any]] = []
    if phantoms and not config.dry_run:
        # Re-verify the live feed is still clear before writing (one row at a time).
        fresh = await client.trading.get_active_orders([config.account_name], None, limit=200)
        fresh_ids = _extract_live_ids(fresh)
        for r in phantoms:
            if (str(r["client_order_id"]) in fresh_ids) or (str(r["exchange_order_id"]) in fresh_ids):
                continue
            upd = (
                f"BEGIN; UPDATE {config.db_table} SET status='CANCELLED', updated_at=now() "
                f"WHERE id={r['id']} AND status='OPEN' AND filled_amount=0; COMMIT;"
            )
            try:
                out = _psql(config, upd).strip()
                ok = "UPDATE 1" in out
            except Exception as e:
                logger.warning(f"phantom update failed id={r['id']}: {e}")
                ok = False
            closed.append({**r, "action": "CANCELLED" if ok else "FAILED"})
    elif config.dry_run:
        closed = [{**r, "action": "WOULD-CANCEL (dry run)"} for r in phantoms]

    db_after = len(_parse_open_rows(_psql(config, select_sql)))
    feed_after = await client.trading.get_active_orders([config.account_name], None, limit=200)
    live_after = _live_count(feed_after)
    anomaly = db_after != live_after

    return {
        "db_open": db_before,
        "live": live_before,
        "phantoms": len(phantoms),
        "closed": closed,
        "partial_flags": partial_flags,
        "db_open_after": db_after,
        "live_after": live_after,
        "anomaly": anomaly,
        "dry_run": config.dry_run,
    }


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id = context._chat_id
    report = LiveReport(
        "Phantom Order Reconciler",
        source_name="phantom_order_reconciler",
        tags=["monitoring", "orders"],
    )
    passes = 0
    try:
        while True:
            try:
                client = await get_client(chat_id, context=context)
                if client is None:
                    await asyncio.sleep(60)
                    continue
                result = await _reconcile(client, config)
                passes += 1

                report.clear()
                report.builder.manual_order()
                report.builder.section(
                    f"RECONCILE PASS #{passes}",
                    "DRY RUN — no DB writes" if config.dry_run else "Live reconciliation",
                )
                report.builder.kpi("Open orders (DB)", str(result["db_open"]))
                report.builder.kpi("Live active orders", str(result["live"]))
                report.builder.kpi("Phantoms closed", str(len(result["closed"])))
                report.builder.kpi(
                    "Consistency",
                    "OK — DB == live" if not result["anomaly"]
                    else f"MISMATCH (DB {result['db_open_after']} vs live {result['live_after']})",
                )

                action_rows = [
                    {"order_id": r["id"], "pair": r["trading_pair"], "filled": r["filled_amount"],
                     "created": r["created_at"], "action": r["action"]}
                    for r in result["closed"]
                ] + [
                    {"order_id": r["id"], "pair": r["trading_pair"], "filled": r["filled_amount"],
                     "created": r["created_at"], "action": "FLAGGED — partial fill, left untouched"}
                    for r in result["partial_flags"]
                ]
                if action_rows:
                    report.builder.table(action_rows, ["order_id", "pair", "filled", "created", "action"])

                report.builder.markdown(
                    f"_Verified after pass: DB OPEN **{result['db_open_after']}** vs live active **{result['live_after']}** — "
                    f"{'consistent' if not result['anomaly'] else 'MISMATCH'}._"
                )
                await report.update()

                if result["closed"] or result["anomaly"] or result["partial_flags"]:
                    lines = ["🧩 Phantom Order Reconciler"]
                    if result["closed"]:
                        lines.append(f"Closed {len(result['closed'])} phantom order(s):")
                        for c in result["closed"]:
                            lines.append(f" • #{c['id']} {c['trading_pair']} — {c['action']}")
                    if result["partial_flags"]:
                        lines.append(f"⚠️ {len(result['partial_flags'])} OPEN row(s) with partial fill left untouched:")
                        for f in result["partial_flags"]:
                            lines.append(f" • #{f['id']} {f['trading_pair']} filled={f['filled_amount']}")
                    if result["anomaly"]:
                        lines.append(f"❌ VERIFY MISMATCH: DB OPEN {result['db_open_after']} != live {result['live_after']}")
                    await context.bot.send_message(chat_id=chat_id, text="\n".join(lines))

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Phantom reconciler tick error: {e}")

            await asyncio.sleep(config.interval_hours * 3600)

    except asyncio.CancelledError:
        if report.report_id is not None:
            report.clear()
            report.builder.manual_order()
            report.builder.section("MONITOR STOPPED", "Final fixed snapshot")
            report.builder.kpi("Passes", str(passes))
            await report.update()
        return f"Phantom Order Reconciler stopped after {passes} pass(es) — clean"