import requests
from pydantic import BaseModel

def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0

class Config(BaseModel):
    top_by_volume: int = 20

async def run(config: Config, context):
    try:
        r = requests.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"}, timeout=30)
        r.raise_for_status()
        data = r.json()
        meta = data[0]
        universe = meta["universe"] if isinstance(meta, dict) else meta
        ctxs = data[1]
        rows = []
        for u, c in zip(universe, ctxs):
            if u.get("isDelisted"):
                continue
            name = u["name"]
            mark = _f(c.get("markPx"))
            prev = _f(c.get("prevDayPx"))
            funding_hr = _f(c.get("funding"))
            day_ntl = _f(c.get("dayNtlVlm"))
            oi = _f(c.get("openInterest"))
            change_24h = ((mark / prev) - 1) * 100 if prev else 0.0
            rows.append({
                "name": name,
                "mark": mark,
                "fund_ann": round(funding_hr * 24 * 365, 2),
                "vol": day_ntl,
                "oi": oi,
                "chg": round(change_24h, 2),
            })
        rows.sort(key=lambda x: x["vol"], reverse=True)
        top = rows[: config.top_by_volume]
        by_fund = sorted(rows, key=lambda x: x["fund_ann"], reverse=True)
        hi = by_fund[:5]
        lo = [x for x in by_fund if x["fund_ann"] < 0][:5]
        lines = []
        lines.append(f"ACTIVE={len(rows)} TOTAL24H_VOL=${round(sum(x['vol'] for x in rows)/1e9,2)}B TOTAL_OI=${round(sum(x['oi'] for x in rows)/1e9,2)}B")
        lines.append("TOP_BY_VOL: " + " | ".join(f"{x['name']} ${round(x['vol']/1e6,1)}M chg{x['chg']}% fund{x['fund_ann']}%pa" for x in top))
        lines.append("HIGHEST_FUND: " + " | ".join(f"{x['name']} {x['fund_ann']}%pa" for x in hi))
        lines.append("MOST_NEG_FUND: " + " | ".join(f"{x['name']} {x['fund_ann']}%pa" for x in lo))
        # anon/vol buckets (mature vs degen by 24h vol)
        mature = [x for x in rows if x["vol"] >= 50e6]
        mid = [x for x in rows if 5e6 <= x["vol"] < 50e6]
        degen = [x for x in rows if x["vol"] < 5e6]
        lines.append(f"BUCKETS mature(>=50M vol):{len(mature)} mid(5-50M):{len(mid)} low(<5M):{len(degen)}")
        lines.append("MATURE: " + ", ".join(x["name"] for x in sorted(mature, key=lambda z: z["vol"], reverse=True)[:25]))
        return {"summary": "\n".join(lines)}
    except Exception as e:
        return {"error": str(e)}