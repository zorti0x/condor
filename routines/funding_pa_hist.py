import requests, time
from pydantic import BaseModel

def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0

class Config(BaseModel):
    coins: list = ["BTC", "ETH", "SOL", "HYPE", "XRP", "PUMP", "CASHCAT"]
    hours: int = 180 * 24

async def run(config: Config, context):
    base = "https://api.hyperliquid.xyz/info"
    start = int(time.time() * 1000) - config.hours * 3600 * 1000
    out = []
    for coin in config.coins:
        try:
            rh = requests.post(base, json={"type": "fundingHistory", "coin": coin, "startTime": start}, timeout=30)
            rh.raise_for_status()
            vals = sorted(_f(d.get("fundingRate")) for d in rh.json())
            n = len(vals)
            if not n:
                out.append({"coin": coin, "error": "no data"})
                continue
            ann = lambda v: v * 24 * 365
            avg = sum(vals) / n
            days = n / 24
            pct_pos = 100 * sum(1 for v in vals if v > 0) / n
            def percentile(p):
                idx = min(n - 1, int(n * p))
                return vals[idx]
            out.append({
                "coin": coin,
                "hours": n,
                "days": round(days, 1),
                "avg_ann_pct": round(ann(avg), 2),
                "persist_positive_pct": round(pct_pos, 1),
                "p50_ann_pct": round(ann(percentile(0.5)), 2),
                "p90_ann_pct": round(ann(percentile(0.9)), 2),
                "min_ann_pct": round(ann(vals[0]), 2),
                "max_ann_pct": round(ann(vals[-1]), 2),
                "last_ann_pct": round(ann(vals[-1]), 2),
            })
        except Exception as e:
            out.append({"coin": coin, "error": str(e)})
    out.sort(key=lambda x: -x.get("avg_ann_pct", 0) if isinstance(x.get("avg_ann_pct"), (int, float)) else 0)
    return {"window_days": round(config.hours / 24, 1), "results": out}