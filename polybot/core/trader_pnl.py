"""
Net PnL calculator — gabungan /positions + /activity, buat screening trader
copy-trade tanpa bias endpoint.

Kenapa gabungan? /positions doang bias (posisi menang ilang begitu di-redeem,
posisi kalah numpuk). /activity doang butuh matching BUY vs REDEEM per market.
Solusi: /positions (curPrice 0/1 = resolved) untuk kalah + menang-belum-klaim,
/activity (REDEEM/SELL) untuk menang-sudah-klaim, digabung by conditionId.
"""
import time
from . import api

REBATE_TYPES = {"MAKER_REBATE", "TAKER_REBATE", "REWARD", "YIELD"}


def _fetch_resolved_positions(wallet):
    resolved = []
    for p in api.trader_positions(wallet, limit=500):
        try:
            cur = float(p.get("curPrice", -1))
        except (TypeError, ValueError):
            continue
        if cur in (0.0, 1.0):
            resolved.append(p)
    return resolved


def _fetch_activity_all(wallet, page_size=500, max_records=5000):
    semua, offset = [], 0
    while len(semua) < max_records:
        page = api.trader_activity(wallet, limit=page_size, offset=offset)
        if not page:
            break
        semua.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
        time.sleep(0.3)
    return semua


def hitung_net_pnl(wallet):
    """Ringkasan performa realized. None kalau gagal total / gak ada market closed."""
    resolved = _fetch_resolved_positions(wallet)
    activity = _fetch_activity_all(wallet)
    if not resolved and not activity:
        return None

    buy_cost, proceeds = {}, {}
    for a in activity:
        cid, tipe, side = a.get("conditionId", ""), a.get("type", ""), a.get("side", "")
        if not cid or tipe in REBATE_TYPES:
            continue
        try:
            usdc = float(a.get("usdcSize", 0))
        except (TypeError, ValueError):
            usdc = 0.0
        if tipe == "TRADE" and side == "BUY":
            buy_cost[cid] = buy_cost.get(cid, 0.0) + usdc
        elif (tipe == "TRADE" and side == "SELL") or tipe == "REDEEM":
            proceeds[cid] = proceeds.get(cid, 0.0) + usdc

    per_market = {}
    for cid, proc in proceeds.items():
        per_market[cid] = proc - buy_cost.get(cid, 0.0)
    for p in resolved:
        cid = p.get("conditionId", "")
        if not cid or cid in per_market:
            continue
        try:
            per_market[cid] = float(p.get("cashPnl", 0))
        except (TypeError, ValueError):
            per_market[cid] = 0.0

    if not per_market:
        return None

    total = len(per_market)
    menang = sum(1 for v in per_market.values() if v > 0)
    kalah = sum(1 for v in per_market.values() if v < 0)
    net = sum(per_market.values())
    return {
        "wallet": wallet,
        "total_closed": total,
        "menang": menang,
        "kalah": kalah,
        "win_rate": (menang / total * 100) if total else 0,
        "net_pnl": net,
        "avg_pnl_per_posisi": net / total if total else 0,
    }


def screening(daftar_wallet, min_closed=5, min_net_pnl=0, min_win_rate=50):
    """Screening trader pake net PnL gabungan. Balikin (lolos, gagal)."""
    lolos, gagal = [], []
    for wallet in daftar_wallet:
        hasil = hitung_net_pnl(wallet)
        if not hasil or hasil["total_closed"] < min_closed:
            continue
        if hasil["net_pnl"] >= min_net_pnl and hasil["win_rate"] >= min_win_rate:
            lolos.append(hasil)
        else:
            gagal.append(hasil)
    return lolos, gagal
