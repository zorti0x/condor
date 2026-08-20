import requests
from pydantic import BaseModel

class Config(BaseModel):
    inst_type: str = "SPOT"  # SPOT or SWAP

async def run(config: Config, context):
    try:
        url = "https://www.okx.com/api/v5/public/instruments"
        params = {"instType": config.inst_type, "limit": "500"}
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "0":
            return {"error": data.get("msg", data)}
        insts = data.get("data", [])
        pairs = sorted({i["instId"] for i in insts if i.get("state") in ("live", "online")})
        return {
            "api_ok": True,
            "instType": config.inst_type,
            "total": len(insts),
            "live_insts": len(pairs),
            "sample": pairs[:60],
            "count": len(pairs),
        }
    except Exception as e:
        return {"error": str(e)}