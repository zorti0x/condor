import requests, time
from pydantic import BaseModel

def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0

class Config(BaseModel):
    pass

async def run(config: Config, context):
    base = "https://api.hyperliquid.xyz/info"
    r = requests.post(base, json={"type": "metaAndAssetCtxs"}, timeout=30)
    r.raise_for_status()
    data = r.json()
    meta = data[0]
    universe = meta["universe"] if isinstance(meta, dict) else meta
    ctxs = data[1]
    rows = []
    for u, c in zip(universe, ctxs):
        if u.get("isDelisted"):
            continue
        fh = _f(c.get("funding"))
        rows.append({
            "name": u["name"],
            "ann_now": round(fh * 24 * 365, 2),
            "vol": _f(c.get("dayNtlVlm")),
            "mark": _f(c.get("markPx")),
        })
    # proper spot symbol detection
    spot_syms = set()
    try:
        rs = requests.post(base, json={"type": "spotMetaAndAssetCtxs"}, timeout=30)
        rs.raise_for_status()
        smeta = rs.json()[0]
        sn_tokens = smeta.get("tokens", []) if isinstance(smeta, dict) else []
        for t in sn_tokens:
            nm = t.get("name", "")
            if nm:
                spot_syms.add(nm)
    except Exception:
        pass

    def has_spot(name):
        if name in spot_syms:
            return True
        if ("w" + name) in spot_syms:
            return True
        return False

    pos = sorted([c for c in rows if c["ann_now"] > 0], key=lambda x: x["ann_now"], reverse=True)[:10]
    neg = sorted([c for c in rows if c["ann_now"] < 0], key=lambda x: x["ann_now"])[:6]
    liq = sorted(rows, key=lambda x: x["vol"], reverse=True)[:10]
    cands = {}
    for c in pos + neg + liq:
        cands.setdefault(c["name"], c)
    cands = list(cands.values())
    for c in cands:
        c["has_hl_spot"] = has_spot(c["name"])
        try:
            start = int(time.time() * 1000) - 7 * 24 * 3600 * 1000
            rh = requests.post(base, json={"type": "fundingHistory", "coin": c["name"], "startTime": start}, timeout=20)
            rh.raise_for_status()
            vals = [_f(d.get("fundingRate")) for d in rh.json()]
            n = len(vals)
            if n:
                c["avg_ann"] = round((sum(vals) / n) * 24 * 365, 2)
                c["latest_ann"] = round(vals[-1] * 24 * 365, 2)
                c["pos_pct"] = round(100 * sum(1 for v in vals if v > 0) / n, 0)
            else:
                c["avg_ann"] = c["latest_ann"] = 0
                c["pos_pct"] = 0
        except Exception:
            c["avg_ann"] = c["latest_ann"] = None
            c["pos_pct"] = None
    # compact output: funding magnitude, persistence, liquid, spot availability
    def score(c):
        s = min(c["vol"] / 20e6, 1.0) * 5
        if c.get("has_hl_spot"):
            s += 3
        if c.get("pos_pct") is not None:
            s += (c.get("pos_pct", 0) / 100) * 2
        s += min(abs(c["avg_ann"] or 0) * 2, 2)
        return s
    cands.sort(key=score, reverse=True)
    out = []
    for c in cands[:10]:
        out.append({
            "name": c["name"],
            "now_ann": c["ann_now"],
            "avg7d_ann": c.get("avg_ann"),
            "latest_ann": c.get("latest_ann"),
            "pos%": c.get("pos_pct"),
            "hlspot": c.get("has_hl_spot"),
            "volM": round(c["vol"] / 1e6, 1),
        })
    return {
        "pos>1pct_count": len([c for c in rows if c["ann_now"] > 1]),
        "neg_funding_count": len([c for c in rows if c["ann_now"] < 0]),
        "spot_symbols_seen": len(spot_syms),
        "best": out,
    }