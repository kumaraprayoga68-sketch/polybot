"""
Cek status resolusi & harga market via CLOB publik (tanpa auth) — dipakai buat
evaluasi menang/kalah setelah market resolve, dan capture harga entry.
"""
from . import api


def cari_token(market_info, outcome_text):
    if not market_info or "tokens" not in market_info:
        return None
    for t in market_info["tokens"]:
        if t.get("outcome", "").strip().lower() == outcome_text.strip().lower():
            return t
    return None


def cek_status(condition_id, outcome_text):
    """
    Balikin dict: {resolved, menang(bool|None), harga_sekarang, error?}.
    resolved=False -> market masih jalan. resolved=True -> menang True/False final.
    """
    info = api.clob_market(condition_id)
    if not info:
        return {"resolved": False, "menang": None, "harga_sekarang": None, "error": "gagal fetch"}
    token = cari_token(info, outcome_text)
    if not token:
        return {"resolved": False, "menang": None, "harga_sekarang": None, "error": "outcome gak ketemu"}
    if not info.get("closed", False):
        return {"resolved": False, "menang": None, "harga_sekarang": token.get("price")}
    return {"resolved": True, "menang": bool(token.get("winner", False)),
            "harga_sekarang": token.get("price")}
