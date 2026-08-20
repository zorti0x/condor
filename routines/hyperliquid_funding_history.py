import requests, time
from pydantic import BaseModel

class Config(BaseModel):
    coin: str = "CASHCAT"
    hours: int = 168

async def run(config: Config, context):
    try:
        now = int(time.time() * 1000)
        start = now - config.hours * 3600 * 1000
        r = requests.post("https://api.hyperliquid.xyz/info",
                          json={"type": "fundingHistory", "coin": config.coin, "startTime": start}, timeout=30)
        r.raise_for_status()
        data = r.json()
        vals = sorted(float(d["fundingRate"]) for d in data if d.get("fundingRate") is not None)
        n = len(vals)
        if not n:
            return {"error": "no funding data"}
        avg = sum(vals) / n
        # Hyperliquid funding is an hourly rate expressed such that raw * 24 * 365 = annualized % on notional
        return {
            "hours_sampled": n,
            "avg_hourly_raw": round(avg, 10),
            "annualized_pct": round(avg * 24 * 365, 3),
            "daily_pct": round(avg * 24 * 100, 5),
            "min_hourly": round(min(vals), 10),
            "max_hourly": round(max(vals), 10),
            "latest_hourly_raw": round(vals[-1], 10),
        }
    except Exception as e:
        return {"error": str(e)}