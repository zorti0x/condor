# Endpoint probe — CEX / DEX / RPC reachability from this server.
# Run via run_code (plain HTTP probes; no keys, nothing executed on-chain).
import json, urllib.request

def probe(label, url, method="GET", data=None):
    try:
        req = urllib.request.Request(url, data=data, method=method,
            headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            b = r.read(100).decode(errors="replace").replace("\n", " ")[:70]
            print(f"{label:14s} -> OK {r.status} | {b}")
    except urllib.error.HTTPError as e:
        print(f"{label:14s} -> HTTP {e.code} {e.reason}")
    except Exception as e:
        print(f"{label:14s} -> ERR {str(e)[:85]}")

# --- CEX REST (health/ping/time endpoints) ---
probe("Binance",     "https://api.binance.com/api/v3/ping")
probe("Binance.US",  "https://api.binance.us/api/v3/ping")
probe("OKX",         "https://www.okx.com/api/v5/public/time")
probe("Bybit",       "https://api.bybit.com/v5/market/time")
probe("Hyperliquid", "https://api.hyperliquid.xyz/info", "POST", b'{"type":"meta"}')
probe("Derive",      "https://api.derive.xyz/")
probe("Gate.io",     "https://api.gateio.ws/api/v4/spot/currencies/BTC")
probe("Kraken",      "https://api.kraken.com/0/public/Time")
probe("Coinbase",    "https://api.exchange.coinbase.com/time")
probe("KuCoin",      "https://api.kucoin.com/api/v1/timestamp")
probe("MEXC",        "https://api.mexc.com/api/v3/ping")
probe("Bitget",      "https://api.bitget.com/api/v2/public/time")

# --- Gateway / DEX layer ---
probe("Solana RPC",  "https://api.mainnet-beta.solana.com", "POST",
      b'{"jsonrpc":"2.0","id":1,"method":"getHealth"}')
probe("Jupiter",     "https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v&amount=100000000&slippageBps=50")
probe("GeckoTerm",   "https://api.geckoterminal.com/api/v2/networks?limit=1")
probe("CoinGecko",   "https://api.coingecko.com/api/v3/ping")

# --- EVM RPCs (blockNumber probe) ---
probe("BSC",         "https://bsc-dataseed.binance.org", "POST",
      b'{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}')
probe("Base",        "https://mainnet.base.org", "POST",
      b'{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}')
probe("Arbitrum",    "https://arb1.arbitrum.io/rpc", "POST",
      b'{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}')
probe("Ethereum",    "https://ethereum.publicnode.com", "POST",
      b'{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}')
probe("Polygon",     "https://polygon-bor-rpc.publicnode.com", "POST",
      b'{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}')

# Interpretation: 200 = reachable; 403/451 = geo-block/key-wall;
# 404/405/400 = wrong path/method (host OK); 525/5xx = provider flake;
# ERR (DNS/TLS/conn) = local egress or DNS quirk -> retry via Gateway client.