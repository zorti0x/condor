import requests, math, time
from datetime import datetime, timezone
from pydantic import BaseModel

XRPL_NODES = ["https://xrplcluster.com/", "https://s1.ripple.com:51234/"]
HOUR_VOL_MULT = {
    0: 1.03, 1: 1.10, 2: 0.95, 3: 0.89, 4: 0.82, 5: 0.85,
    6: 0.82, 7: 0.82, 8: 0.91, 9: 0.83, 10: 0.78, 11: 0.82,
    12: 0.92, 13: 1.28, 14: 1.50, 15: 1.40, 16: 1.16, 17: 1.21,
    18: 1.07, 19: 1.04, 20: 1.02, 21: 0.97, 22: 0.97, 23: 0.82,
}

class Config(BaseModel):
    requote_sec: int = 300
    adverse_k: float = 1.0

async def run(config: Config, context):
    out = []
    try:
        tk = requests.get("https://www.okx.com/api/v5/market/ticker",
                          params={"instId": "XRP-USDT"}, timeout=15).json()
        ref = float(tk["data"][0]["last"])
        implied_rlusd = 1.0 / ref if ref > 0 else 0.0
        cd = requests.get("https://www.okx.com/api/v5/market/candles",
                          params={"instId": "XRP-USDT", "bar": "1m", "limit": "120"}, timeout=15).json()
        rows = sorted(cd["data"], key=lambda r: r[0])
        closes = [float(r[4]) for r in rows if float(r[4]) > 0]
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i] > 0 and closes[i - 1] > 0]
        mean = sum(rets) / len(rets) if rets else 0
        var = sum((r - mean) ** 2 for r in rets) / len(rets) if rets else 0
        vol_raw = math.sqrt(var / 60.0)
        now_hr = datetime.now(timezone.utc).hour
        mult = HOUR_VOL_MULT.get(now_hr, 1.0)
        floor_bps = config.adverse_k * (vol_raw * mult) * math.sqrt(config.requote_sec) * 10000
        out.append(f"XRP ref: {ref:.4f} | implied RLUSD-XRP: {implied_rlusd:.6f}")
        out.append(f"floor_bps: {floor_bps:.2f} (hr{now_hr}u mult {mult:.2f}, {config.requote_sec}s)")

        # Resolve RLUSD issuer via XRPSCAN top-tokens list
        issuer = None
        for url in ["https://api.xrpscan.com/api/v1/tokens", "https://api.xrpscan.com/api/v1/assets"]:
            try:
                raw = requests.get(url, timeout=15)
                data = raw.json()
                lst = data if isinstance(data, list) else data.get("tokens", data.get("assets", []))
                for t in lst:
                    sym = (t.get("symbol") or t.get("currency") or "")
                    if str(sym).upper() == "RLUSD":
                        issuer = t.get("issuer") or t.get("account")
                        if issuer:
                            break
                if issuer:
                    break
            except Exception as e:
                out.append(f"resolver {url}: {type(e).__name__}")
        out.append(f"RLUSD issuer resolved: {issuer}")

        amm_fee = None
        note = "no issuer -> fallback"
        if issuer:
            for node in XRPL_NODES:
                payload = {"method": "amm_info", "params": [{"asset": {"currency": "XRP"}, "asset2": {"currency": "RLUSD", "issuer": issuer}}]}
                try:
                    res = requests.post(node, json=payload, timeout=15).json().get("result", {})
                    amm = res.get("amm")
                    if amm:
                        amm_fee = float(amm.get("TradingFee", 0)) / 1000.0
                        note = f"live {node}"
                        break
                    out.append(f"amm_info {node}: {res.get('error','no amm')}")
                except Exception as e:
                    out.append(f"amm_info {node}: {type(e).__name__}")
        if amm_fee is None:
            amm_fee = 0.1
            out.append("AMM fee: FALLBACK 0.1%")
        else:
            out.append(f"AMM fee: {amm_fee:.3f}% ({note})")
        ceiling_bps = amm_fee * 100
        viable = floor_bps < ceiling_bps
        headroom = ceiling_bps - floor_bps
        out.append(f"VIABLE: {str(viable).lower()} (floor {floor_bps:.2f} vs ceiling {ceiling_bps:.2f} bps)")
        out.append(f"headroom_bps: {headroom:.2f}")
        if viable:
            target = floor_bps + headroom * 0.5
            out.append(f"suggested_bid: {implied_rlusd * (1 - target / 10000):.6f} | ask: {implied_rlusd * (1 + target / 10000):.6f} (target {target:.2f} bps/side)")
        else:
            out.append("action: DO NOT QUOTE")
        return "\n".join(out)
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"