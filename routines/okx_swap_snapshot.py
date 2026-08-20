import requests
from pydantic import BaseModel

class Config(BaseModel):
    assets: list = ["BTC", "ETH", "SOL", "HYPE"]

async def run(config: Config, context):
    base = "https://www.okx.com/api/v5"
    out = []
    for a in config.assets:
        spot = f"{a}-USDT"
        swap = f"{a}-USDT-SWAP"
        try:
            ts = requests.get(f"{base}/market/ticker", params={"instId": spot}, timeout=15).json()
            tw = requests.get(f"{base}/market/ticker", params={"instId": swap}, timeout=15).json()
            fr = requests.get(f"{base}/public/funding-rate", params={"instId": swap}, timeout=15).json()

            def g(obj, k):
                d = (obj.get("data") or [{}])[0]
                return d.get(k)

            spot_last = float(g(ts, "last") or 0)
            swap_last = float(g(tw, "last") or 0)
            swap_ask = float(g(tw, "askPx") or 0)
            swap_bid = float(g(tw, "bidPx") or 0)
            fund = float(fr.get("data", [{}])[0].get("fundingRate") or 0)
            basis_pct = (swap_last - spot_last) / spot_last * 100 if spot_last else 0
            out.append({
                "asset": a,
                "okx_spot": spot_last,
                "okx_swap": swap_last,
                "okx_swap_bid": swap_bid,
                "okx_swap_ask": swap_ask,
                "okx_basis_pct": round(basis_pct, 4),
                "okx_funding_raw": fund,
                "okx_funding_ann_8h_pct": round(fund * 3 * 365, 2),
            })
        except Exception as e:
            out.append({"asset": a, "error": str(e)})
    return {"okx_swaps": out}