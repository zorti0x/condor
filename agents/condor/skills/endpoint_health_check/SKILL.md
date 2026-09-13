---
name: endpoint_health_check
description: Verify CEX/DEX/RPC endpoint reachability from the server; triage geo-block
  vs code bug vs RPC provider issue.
when_to_use: User asks whether an exchange/DEX/RPC endpoint is reachable, whether
  geo-blocking explains a retry/connectivity error, or wants the full connector endpoint
  inventory re-verified (e.g. "are all connectors reachable?", "is this geo-blocked?",
  "check endpoints").
created: '2026-09-08T23:07:06Z'
source: chat
---

# Endpoint Health Check

Probe the known connector / DEX / RPC endpoints and triage any failures into the right root-cause bucket. Do NOT guess — the probe script gives HTTP status codes that separate the cases.

## When
- Verifying every connector/DEX endpoint is reachable from this server.
- A tool call keeps failing with "exceeded max retries" and geo-blocking is suspected.
- Mixing in a new venue and wanting to confirm its API is reachable before wiring it up.

## Steps
1. Pull the companion probe script `endpoint_probe.py` (skill read_file) and run it via `run_code` (it is a plain HTTP probe; nothing is executed on-chain and no keys are used).
2. Read the results through the triage lens below.
3. For any failure, drill with one alternate endpoint (e.g. publicnode / 1rpc.io for EVM RPCs) before concluding.
4. Report a clean per-venue list with reachability status.

## Triage — same "retries" wrapper, different roots
- HTTP 200 → reachable. Done.
- HTTP 403 / 451 (or geo error body) → GEO-BLOCK or BOT-CHALLENGE. Distinguish them: a Cloudflare "Hello there, human!" page is BOT MANAGEMENT (blocks curl/urllib; a browser with JS would pass) — NOT a country block. A 451 or a "blocked in your region" body IS geo. A key-walled RPC (polygon-rpc.com) 401s/403s without an API key. For bot-challenges, retry with a real client (browser/Gateway); for key-wall, add a key or swap provider.
- HTTP 404 / 405 / 400 → wrong path or method, NOT a block. Host is reachable — BUT probe the real route before declaring the venue "OK"; a bare root 404 on every standard path may mean there is no public REST API at that host (e.g. api.derive.xyz), so check the venue's documented API / app instead.
- HTTP 525 / 5xx from public RPC → provider-side instability (Cloudflare/LlamaNodes). Retry once; fall back to an alternate RPC.
- `NameError` / `AttributeError` / `TypeError` inside the tool result → LOCAL CODE BUG, never geo. No network involved.
- `ConnectionError` / timeout / DNS `No address associated with hostname` → local egress or DNS issue, or an isolated sandbox quirk — retry via the real client (Gateway) before declaring the endpoint down; a sandbox DNS failure is not the endpoint's failure.
- Proper geo-blocks on CEXs (Binance.US-style) always return HTTP codes (451/403) — never TLS/SNI errors; a TLS SNI error means the request never reached the origin's geo logic.

## Baseline reference (verified reachable, no geo-blocks)
CEX: api.binance.com ✓ · api.binance.us ✓ · www.okx.com ✓ · api.bybit.com ✓ · api.hyperliquid.xyz (POST /info) ✓ · api.derive.xyz ⚠️ (see below) · api.gateio.ws ✓ · api.kraken.com ✓ · api.exchange.coinbase.com ✓ · api.kucoin.com ✓ · api.mexc.com ✓ · api.bitget.com ✓
Gateway/DEX: api.mainnet-beta.solana.com ✓ · quote-api.jup.ag / api.jup.ag (via Gateway) ✓ · api.geckoterminal.com ✓ · api.coingecko.com ✓
EVM RPCs: bsc-dataseed.binance.org ✓ · mainnet.base.org ✓ · arb1.arbitrum.io/rpc ✓ · ethereum.publicnode.com ✓ · 1rpc.io/eth ✓ · polygon-bor-rpc.publicnode.com ✓. Unstable/key-walled defaults to avoid: eth.llamarpc.com (525), polygon-rpc.com (401).
Status/app hosts to use for the protected venues: derive.xyz ✓ · app.derive.xyz ✓ · status.derive.xyz ✓

## Derive (ex-Lyra) — the honest picture
- api.derive.xyz: resolves behind Cloudflare but returns 404 on EVERY standard path (root, /health, /v1/*, /trading/*). No public REST health route found; the API lives elsewhere (gRPC / partner-subgraph / app) or requires a client with full browser behavior. Do NOT report this host as a clean "OK" based on the bare 404 — probe the real route or check app.derive.xyz.
- docs.derive.xyz: HTTP 403 "Hello there, human!" — Cloudflare bot management. Blocks curl/urllib, not a country geo-block.
- derive.xyz / app.derive.xyz / status.derive.xyz: all 200 (web, trading app, status page).
- Interpretation for the geo question: NONE of this is IP-country blocking. The 403 is bot-fight mode; the 404s mean the route isn't at that host from a non-browser client. For real Derive data the venue is reachable via its app/trading surface — verify against the specific API you intend to consume.

## Reference points (no REST — reached through RPC)
Meteora (DLMM v2 program) · Raydium (AMM/CLMM) · Orca (Whirlpool) · Uniswap/PancakeSwap (EVM contracts) — all reached via the chain RPC above, NOT by direct REST.
