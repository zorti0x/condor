import requests
from pydantic import BaseModel

class Config(BaseModel):
    pass

async def run(config: Config, context):
    try:
        r = requests.post("https://api.hyperliquid.xyz/info", json={"type": "meta"}, timeout=20)
        r.raise_for_status()
        meta = r.json()
        universe = meta.get("universe", [])
        delisted = [u for u in universe if u.get("isDelisted")]
        active = [u for u in universe if not u.get("isDelisted")]
        converted = meta.get("perpAssetsConvertedToIndex", []) or []
        active_names = sorted(u["name"] for u in active)
        return {
            "total_universe": len(universe),
            "active_perp_pairs": len(active),
            "delisted": len(delisted),
            "converted_to_index": len(converted),
            "sample_pairs": active_names[:30],
        }
    except Exception as e:
        return {"error": str(e)}
