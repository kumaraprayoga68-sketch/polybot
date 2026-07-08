"""
Strategi ARBITRAGE (YES+NO) — dari poly-ai/arb_scanner + 9router/arb executor.

Konsep: kalau total harga ASK LIVE semua outcome di 1 market < $1.00, beli 1 share
tiap outcome = guaranteed profit (murni matematika, bukan forecasting).

⚠️ FEE & EKSEKUSI 2-LEG: Polymarket charge taker fee per kategori (~1.25-2.5% PER
LEG di non-geopolitics). Strategi ini beli 2 sisi = 2x fee. MIN_EDGE_PCT itu buffer
KASAR, bukan hitungan fee presisi — WAJIB cek manual di UI sebelum eksekusi.
Eksekusi tidak atomic (lihat executor.execute_arbitrage).
"""
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..core import api, executor, tracker, notify
from ..config import Arbitrage, Common

FEE_WARNING = ("⚠️ edge BELUM dikurangi taker fee (~1.25-2.5%/leg, 2 leg). "
               "Cek manual di UI Polymarket sebelum eksekusi.")


def _edge_live(market):
    """Total harga ASK live + edge% untuk 1 market. None kalau gagal fetch salah satu."""
    if not market.get("token_ids"):
        return None
    harga = []
    for tid in market["token_ids"]:
        p = api.clob_ask_price(tid)
        if p is None:
            return None
        harga.append(p)
    total = sum(harga)
    if total <= 0:
        return None
    return {
        "question": market["question"],
        "conditionId": market["conditionId"],
        "outcomes": market["outcomes"],
        "prices": harga,
        "total_harga": round(total, 4),
        "edge_pct": round((1 - total) / total * 100, 2),
        "liquidity": market["liquidity"],
    }


def scan():
    """Scan semua market aktif, balikin (peluang_lolos, semua_hasil)."""
    valid = [m for m in (api.parse_market(x) for x in
                         api.iter_all_markets(max_markets=Arbitrage.MAX_MARKET,
                                              page_size=Arbitrage.PAGE_SIZE)) if m]
    peluang, semua = [], []
    with ThreadPoolExecutor(max_workers=Arbitrage.SCAN_WORKERS) as ex:
        futs = {ex.submit(_edge_live, m): m for m in valid}
        for fut in as_completed(futs):
            hasil = fut.result()
            if not hasil:
                continue
            semua.append(hasil)
            if (hasil["edge_pct"] >= Arbitrage.MIN_EDGE_PCT
                    and hasil["liquidity"] >= Arbitrage.MIN_LIQUIDITY_USD):
                peluang.append(hasil)
    peluang.sort(key=lambda x: x["edge_pct"], reverse=True)
    semua.sort(key=lambda x: x["edge_pct"], reverse=True)
    return peluang, semua


def _eksekusi(p):
    """Beli tiap outcome senilai MAX_PER_TRADE (dibagi rata). Paper by default."""
    per_leg = round(Common.MAX_PER_TRADE / max(1, len(p["outcomes"])), 2)
    legs = [(o, per_leg) for o in p["outcomes"]]
    hasil = executor.execute_arbitrage(p["conditionId"], legs)
    tracker.catat("arbitrage", "eksekusi", market=p["question"][:60],
                  condition_id=p["conditionId"], edge_pct=p["edge_pct"],
                  size_usd=per_leg * len(legs), keterangan=hasil["status"])
    return hasil


def run(execute=False):
    """1 siklus scan. execute=True -> coba eksekusi tiap peluang (tetap kena gate)."""
    print(f"🔍 [arbitrage] scanning… (edge≥{Arbitrage.MIN_EDGE_PCT}%, "
          f"liq≥${Arbitrage.MIN_LIQUIDITY_USD:,.0f})")
    peluang, semua = scan()
    if not peluang:
        print("✅ Gak ada peluang arbitrase lolos threshold.")
        if semua:
            print("   Terdekat:")
            for h in semua[:3]:
                print(f"    {h['question'][:55]} — edge {h['edge_pct']}% "
                      f"(total ${h['total_harga']}, liq ${h['liquidity']:,.0f})")
        return []

    print(f"\n🎯 {len(peluang)} PELUANG (edge sebelum fee!)\n{FEE_WARNING}\n")
    lines = []
    for p in peluang:
        print(f"  {p['question'][:60]}")
        print(f"    total ${p['total_harga']} | edge {p['edge_pct']}% | liq ${p['liquidity']:,.0f}")
        lines.append(f"• {p['question'][:50]} — edge {p['edge_pct']}%")
        tracker.catat("arbitrage", "peluang", market=p["question"][:60],
                      condition_id=p["conditionId"], edge_pct=p["edge_pct"],
                      keterangan=f"total ${p['total_harga']}")
        if execute:
            _eksekusi(p)
    if lines:
        notify.alert_sinyal(f"🎯 {len(peluang)} peluang arbitrase", lines[:10] + [FEE_WARNING])
    return peluang
