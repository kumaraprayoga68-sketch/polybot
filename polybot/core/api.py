"""
PolymarketAPI — satu klien untuk semua endpoint PUBLIK Polymarket (tanpa auth):
- Gamma  : discovery market (question, liquidity, volume, outcomes/clobTokenIds)
- CLOB   : harga ASK/midpoint live + info market (resolusi, token per outcome)
- Data   : posisi & activity trader (buat copy-trading)
- LB     : leaderboard (buat auto-pilih trader)

Menggabungkan pola request/parse yang sebelumnya tersebar di 5 bot berbeda.
Semua endpoint di sini read-only — eksekusi order ada di executor.py (butuh auth).
"""
import json
import time
import requests

from .. import config

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def _get(url, params=None, max_retry=2, timeout=12):
    """GET dengan retry + backoff; balikin JSON atau None. Hormati 429 (rate limit)."""
    for attempt in range(max_retry + 1):
        try:
            r = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)
            if r.status_code == 429 and attempt < max_retry:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt < max_retry:
                time.sleep(1.5 * (attempt + 1))
                continue
            return None
    return None


# ── GAMMA: discovery market ───────────────────────────────────────────────────
def fetch_markets_page(limit=500, offset=0, extra_params=None):
    """1 halaman market aktif dari Gamma. Balikin list (mungkin kosong)."""
    params = {"active": "true", "closed": "false", "limit": limit, "offset": offset}
    if extra_params:
        params.update(extra_params)
    data = _get(config.GAMMA_URL, params=params, timeout=15)
    return data if isinstance(data, list) else []


def iter_all_markets(max_markets=2000, page_size=500, sleep_between=0.2):
    """Generator semua market aktif (paginated) sampai max_markets."""
    offset = 0
    total = 0
    while offset < max_markets:
        page = fetch_markets_page(limit=page_size, offset=offset)
        if not page:
            break
        for m in page:
            total += 1
            yield m
            if total >= max_markets:
                return
        if len(page) < page_size:
            break
        offset += page_size
        time.sleep(sleep_between)


def parse_market(m):
    """
    Normalisasi 1 market mentah Gamma. Field outcomes/outcomePrices/clobTokenIds
    itu STRINGIFIED JSON (perlu json.loads). Balikin None kalau datanya cacat.
    """
    try:
        outcomes = json.loads(m.get("outcomes", "[]"))
        prices_raw = json.loads(m.get("outcomePrices", "[]"))
        token_ids = json.loads(m.get("clobTokenIds", "[]"))
    except (json.JSONDecodeError, TypeError):
        return None

    if not outcomes or len(outcomes) != len(prices_raw) or len(outcomes) != len(token_ids):
        return None

    try:
        summary_prices = [float(p) for p in prices_raw]
    except (TypeError, ValueError):
        return None

    def _f(key):
        try:
            return float(m.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    return {
        "question": m.get("question", "N/A"),
        "conditionId": m.get("conditionId", ""),
        "slug": m.get("slug", ""),
        "outcomes": outcomes,
        "summary_prices": summary_prices,   # dari Gamma — CUMA referensi, jangan buat keputusan
        "token_ids": token_ids,             # buat fetch harga ASK live per outcome
        "liquidity": _f("liquidity"),
        "volume": _f("volume"),
        "end_date": m.get("endDate", ""),
    }


# ── CLOB: harga & info market live ────────────────────────────────────────────
def clob_ask_price(token_id):
    """Harga ASK live (harga eksekusi BELI) 1 token dari CLOB order book."""
    data = _get(f"{config.CLOB_HOST}/price", params={"token_id": token_id, "side": "BUY"})
    if not data:
        return None
    try:
        return float(data.get("price", 0))
    except (TypeError, ValueError):
        return None


def clob_midpoint(token_id):
    """Harga midpoint saat ini 1 token."""
    data = _get(f"{config.CLOB_HOST}/midpoint", params={"token_id": token_id})
    if not data:
        return None
    try:
        return float(data.get("mid", 0))
    except (TypeError, ValueError):
        return None


def clob_market(condition_id):
    """Info market lengkap (termasuk tokens & status resolusi) via CLOB publik."""
    return _get(f"{config.CLOB_HOST}/markets/{condition_id}")


# ── DATA API: posisi & activity trader (copy-trading) ─────────────────────────
def trader_positions(wallet, limit=500):
    """Semua posisi trader (aktif + resolved yang belum di-redeem)."""
    data = _get(f"{config.DATA_API}/positions", params={"user": wallet, "limit": limit})
    return data if isinstance(data, list) else []


def trader_activity(wallet, limit=500, offset=0, activity_type=None):
    """Riwayat activity trader (BUY/SELL/REDEEM/…)."""
    params = {"user": wallet, "limit": limit, "offset": offset}
    if activity_type:
        params["type"] = activity_type
    data = _get(f"{config.DATA_API}/activity", params=params)
    return data if isinstance(data, list) else []


# ── LEADERBOARD: auto-pilih trader ────────────────────────────────────────────
def leaderboard(window="30d", limit=100):
    """
    Ambil leaderboard Polymarket (window mis. '30d'). Balikin list trader
    (address + metric). Struktur bisa berubah; caller wajib defensif.
    """
    data = _get(config.LEADERBOARD_URL, params={"window": window, "limit": limit})
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "leaderboard", "results"):
            if isinstance(data.get(key), list):
                return data[key]
    return []
