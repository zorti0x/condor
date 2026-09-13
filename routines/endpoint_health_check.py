"""Endpoint Health Check — probe CEX / DEX / RPC reachability from this server
and triage every failure into a root-cause bucket.

Reuses the probe inventory from the `endpoint_health_check` skill's companion
script (endpoint_probe.py): 12 CEX REST endpoints (Binance, Binance.US, OKX,
Bybit, Hyperliquid POST /info, Derive, Gate.io, Kraken, Coinbase, KuCoin, MEXC,
Bitget), the Gateway/DEX layer (Solana RPC getHealth, Jupiter quote,
GeckoTerminal, CoinGecko) and 5 EVM RPCs (BSC, Base, Arbitrum, Ethereum,
Polygon). Probes run concurrently with short per-request timeouts and results
are bucketed by the skill's triage rules.
"""

import asyncio
import time
from collections import Counter
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

CATEGORY = "Monitoring"

# (label, layer, method, url, json_body)
PROBES = [
    # --- CEX REST (12) ---
    ("Binance",       "CEX", "GET",  "https://api.binance.com/api/v3/ping", None),
    ("Binance.US",    "CEX", "GET",  "https://api.binance.us/api/v3/ping", None),
    ("OKX",           "CEX", "GET",  "https://www.okx.com/api/v5/public/time", None),
    ("Bybit",         "CEX", "GET",  "https://api.bybit.com/v5/market/time", None),
    ("Hyperliquid",   "CEX", "POST", "https://api.hyperliquid.xyz/info", {"type": "meta"}),
    ("Derive",        "CEX", "GET",  "https://api.derive.xyz/", None),
    ("Gate.io",       "CEX", "GET",  "https://api.gateio.ws/api/v4/spot/currencies/BTC", None),
    ("Kraken",        "CEX", "GET",  "https://api.kraken.com/0/public/Time", None),
    ("Coinbase",      "CEX", "GET",  "https://api.exchange.coinbase.com/time", None),
    ("KuCoin",        "CEX", "GET",  "https://api.kucoin.com/api/v1/timestamp", None),
    ("MEXC",          "CEX", "GET",  "https://api.mexc.com/api/v3/ping", None),
    ("Bitget",        "CEX", "GET",  "https://api.bitget.com/api/v2/public/time", None),
    # --- Gateway / DEX layer (4) ---
    ("Solana RPC",    "DEX", "POST", "https://api.mainnet-beta.solana.com",
     {"jsonrpc": "2.0", "id": 1, "method": "getHealth"}),
    ("Jupiter",       "DEX", "GET",
     "https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v&amount=100000000&slippageBps=50", None),
    ("GeckoTerminal", "DEX", "GET",  "https://api.geckoterminal.com/api/v2/networks?limit=1", None),
    ("CoinGecko",     "DEX", "GET",  "https://api.coingecko.com/api/v3/ping", None),
    # --- EVM RPCs (5) ---
    ("BSC",           "RPC", "POST", "https://bsc-dataseed.binance.org",
     {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}),
    ("Base",          "RPC", "POST", "https://mainnet.base.org",
     {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}),
    ("Arbitrum",      "RPC", "POST", "https://arb1.arbitrum.io/rpc",
     {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}),
    ("Ethereum",      "RPC", "POST", "https://ethereum.publicnode.com",
     {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}),
    ("Polygon",       "RPC", "POST", "https://polygon-bor-rpc.publicnode.com",
     {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}),
]

_COLUMNS = ["Venue", "Layer", "Status", "Bucket", "Latency", "Detail"]
_GEO_CODES = (401, 403, 451)  # region-restricted venue or key-walled RPC
_PATH_CODES = (400, 404, 405)  # wrong path/method — host IS reachable


class Config(BaseModel):
    """Probe CEX/DEX/RPC endpoint reachability and triage failures into root-cause buckets."""

    timeout: float = Field(
        default=8.0, ge=1.0, le=30.0,
        description="Per-request timeout in seconds",
    )
    concurrency: int = Field(
        default=10, ge=1, le=21,
        description="Max concurrent probes",
    )


def _bucket(code: int) -> str:
    """Map an HTTP status to a root-cause bucket per the skill's triage rules."""
    if 200 <= code < 300:
        return "reachable"
    if code in _GEO_CODES:
        return "geo-block"
    if code in _PATH_CODES:
        return "path-method"
    return "provider-flake"


async def _probe(client, sem, name, layer, method, url, body, timeout_s) -> dict:
    """Probe one endpoint. Never raises — the row carries the error."""
    async with sem:
        start = time.monotonic()
        try:
            resp = await client.request(
                method, url, json=body, timeout=timeout_s,
                headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
            )
            ms = int((time.monotonic() - start) * 1000)
            code = resp.status_code
            snippet = resp.text[:70].replace("\n", " ").strip()
            if 200 <= code < 300:
                status = f"OK {code}"
            else:
                status = f"HTTP {code}"
            return {
                "Venue": name, "Layer": layer, "Status": status,
                "Bucket": _bucket(code), "Latency": f"{ms}ms", "Detail": snippet,
            }
        except Exception as e:  # noqa: BLE001 — connection/timeout/DNS = local egress
            ms = int((time.monotonic() - start) * 1000)
            msg = f"{type(e).__name__}: {str(e)[:60]}"
            return {
                "Venue": name, "Layer": layer, "Status": "ERR",
                "Bucket": "local-dns", "Latency": f"{ms}ms", "Detail": msg,
            }


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    sem = asyncio.Semaphore(config.concurrency)
    async with httpx.AsyncClient() as client:
        rows = await asyncio.gather(
            *(_probe(client, sem, *p, config.timeout) for p in PROBES)
        )

    counts = Counter(r["Bucket"] for r in rows)
    n_total = len(rows)
    n_reach = counts.get("reachable", 0)
    n_geo = counts.get("geo-block", 0)
    n_path = counts.get("path-method", 0)
    n_flake = counts.get("provider-flake", 0)
    n_local = counts.get("local-dns", 0)
    pct = round(100.0 * n_reach / n_total, 1)

    now = datetime.now(timezone.utc)
    lines = [
        f"🌐 Endpoint Health Check — {n_reach}/{n_total} reachable ({pct}%) "
        f"@ {now.strftime('%Y-%m-%d %H:%M UTC')} · timeout {config.timeout:g}s",
        f"bucket breakdown — geo-block: {n_geo} · path/method (host up): {n_path} "
        f"· provider-flake: {n_flake} · local dns/egress: {n_local}",
    ]
    trouble = [r for r in rows if r["Bucket"] in ("geo-block", "provider-flake", "local-dns")]
    if trouble:
        lines.append("")
        lines.append("Non-reachable / needs attention:")
        for r in trouble:
            lines.append(f"  • {r['Venue']} [{r['Layer']}] {r['Status']} -> {r['Bucket']} — {r['Detail']}")
    else:
        lines.append("")
        lines.append("✅ Every endpoint reachable; any non-2xx are path/method quirks with the host up.")
    summary = "\n".join(lines)

    from condor.reports import ReportBuilder

    builder = ReportBuilder("Endpoint Health Check")
    builder.source("routine", "endpoint_health_check").tags(["endpoints", "connectivity", "monitoring"])
    builder.kpi("Reachable", f"{n_reach}/{n_total}")
    builder.kpi("Reachability", f"{pct}%")
    builder.kpi("Geo-blocked", n_geo)
    builder.kpi("Path/Method", n_path)
    builder.kpi("Provider Flakes", n_flake)
    builder.kpi("Local DNS/Egress", n_local)
    builder.markdown(summary)
    builder.table(rows, _COLUMNS)
    builder.manual_order()
    await builder.save()

    try:
        from routines.base import RoutineResult

        return RoutineResult(text=summary, table_data=rows, table_columns=_COLUMNS)
    except Exception:  # noqa: BLE001
        return summary