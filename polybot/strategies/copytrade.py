"""
Strategi COPY-TRADE consensus — inti dari multi-trader-bot + polymarket-bot.

Pipeline:
 1. Pilih trader: auto (screening leaderboard by net PnL gabungan) atau manual.
 2. Monitor posisi tiap trader (data-api). Deteksi posisi BARU.
 3. Sinyal = consensus 2+ trader di market+outcome yang sama, ATAU 1 trader di
    SINGLE_TRADER_MODE.
 4. Keputusan IKUT/SKIP = FORMULA deterministik (scoring.py), bukan AI.
 5. Kalau IKUT -> Kelly (kelly.py) tentukan size berbasis edge (harga live).
 6. Eksekusi paper/live (executor, triple-gated). Semua dicatat (tracker).
"""
import time
from datetime import datetime, timezone

from ..core import api, executor, scoring, kelly, tracker, notify, trader_pnl
from ..config import CopyTrade, Common


def _hari_ke_resolve(end_date):
    """Berapa hari lagi market resolve dari sekarang. None kalau tanggal gak kebaca."""
    if not end_date:
        return None
    try:
        d = datetime.strptime(str(end_date)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return (d - datetime.now(timezone.utc).date()).days


def _agresif():
    """Mode agresif CUMA aktif kalau paper. Di live, selalu False (balik selektif)."""
    return CopyTrade.AGGRESSIVE and Common.SIMULASI_MODE


def pilih_trader():
    """Balikin (daftar_wallet, performa_map). Auto screening atau manual list."""
    if not CopyTrade.AUTO_PILIH_TRADER and CopyTrade.DAFTAR_TRADER_MANUAL:
        wallets = CopyTrade.DAFTAR_TRADER_MANUAL
    else:
        print(f"🔍 [copytrade] scan leaderboard ({CopyTrade.LEADERBOARD_WINDOW}) + screening net PnL…")
        lb = api.leaderboard(window=CopyTrade.LEADERBOARD_WINDOW, limit=100)
        kandidat = []
        for row in lb:
            addr = row.get("proxyWallet") or row.get("wallet") or row.get("address") or row.get("user")
            if addr:
                kandidat.append(addr)
        wallets = kandidat[:20] or CopyTrade.DAFTAR_TRADER_MANUAL

    # paper agresif: screening dilonggarin biar cukup trader lolos (ada bahan buat bet)
    if _agresif():
        min_wr, min_pnl = CopyTrade.AGG_MIN_WIN_RATE, -1e18
    else:
        min_wr, min_pnl = CopyTrade.MIN_WIN_RATE_PNL, CopyTrade.MIN_NET_PNL
    lolos, _ = trader_pnl.screening(
        wallets, min_closed=5, min_net_pnl=min_pnl, min_win_rate=min_wr)
    lolos.sort(key=lambda t: (t["win_rate"], t["net_pnl"]), reverse=True)
    lolos = lolos[:CopyTrade.TOP_N_TRADER]

    performa = {t["wallet"]: t for t in lolos}
    if lolos:
        print(f"✅ {len(lolos)} trader lolos screening:")
        for t in lolos:
            print(f"   {t['wallet'][:10]}… wr {t['win_rate']:.0f}% "
                  f"net ${t['net_pnl']:,.0f} ({t['total_closed']} closed)")
    else:
        print("⚠️ Gak ada trader lolos screening saat ini.")
    return [t["wallet"] for t in lolos], performa


def _snapshot_posisi(wallet):
    """Map { (conditionId, outcome): posisi } dari posisi AKTIF trader."""
    snap = {}
    for p in api.trader_positions(wallet, limit=200):
        try:
            cur = float(p.get("curPrice", -1))
        except (TypeError, ValueError):
            continue
        if cur in (0.0, 1.0):
            continue  # sudah resolve, bukan sinyal aktif
        cid = p.get("conditionId", "")
        outcome = p.get("outcome", "")
        if cid and outcome:
            snap[(cid, outcome)] = {
                "market": p.get("title", p.get("question", "N/A")),
                "harga": cur if cur > 0 else None,
                "end_date": p.get("endDate", ""),
            }
    return snap


def _evaluasi_sinyal(cid, outcome, info, pendukung, performa):
    """
    Skor + keputusan + (kalau IKUT) sizing + eksekusi. Dicatat ke tracker.
    Balikin True kalau nge-bet (IKUT), False kalau skip — buat cap per siklus.
    """
    if tracker.sudah_dievaluasi(cid, outcome):
        return False

    # filter harga: bet cuma di BAND "value" [MIN, MAX].
    #  - di atas MAX = FAVORIT (untung recehan, rugi penuh).
    #  - di bawah MIN = LONGSHOT (tiket lotre, hampir pasti kalah).
    # MAX_ENTRY_PRICE >= 1 dan MIN_ENTRY_PRICE <= 0 = filter mati.
    harga = info.get("harga")
    if harga is not None:
        if CopyTrade.MAX_ENTRY_PRICE < 1 and harga > CopyTrade.MAX_ENTRY_PRICE:
            tracker.catat("copytrade", "skip_harga", market=info["market"][:60], condition_id=cid,
                          outcome=outcome, harga=harga, end_date=info.get("end_date", ""),
                          keterangan=f"harga {harga} > maxprice {CopyTrade.MAX_ENTRY_PRICE} (favorit)")
            print(f"  ⏭️  SKIP(harga) {info['market'][:42]} — ${harga} > {CopyTrade.MAX_ENTRY_PRICE} (favorit recehan)")
            return False
        if CopyTrade.MIN_ENTRY_PRICE > 0 and harga < CopyTrade.MIN_ENTRY_PRICE:
            tracker.catat("copytrade", "skip_harga", market=info["market"][:60], condition_id=cid,
                          outcome=outcome, harga=harga, end_date=info.get("end_date", ""),
                          keterangan=f"harga {harga} < minprice {CopyTrade.MIN_ENTRY_PRICE} (longshot)")
            print(f"  ⏭️  SKIP(harga) {info['market'][:42]} — ${harga} < {CopyTrade.MIN_ENTRY_PRICE} (longshot lotre)")
            return False

    agresif = _agresif()

    if CopyTrade.SINGLE_TRADER_MODE or agresif:
        perf = performa.get(pendukung[0], {})
        skor = scoring.skor_single_trader(perf)
    else:
        perf_list = [performa[w] for w in pendukung if w in performa]
        skor = scoring.skor_consensus(perf_list, len(pendukung))

    # paper agresif: threshold 0 -> semua sinyal yang lolos screening = IKUT
    threshold = 0 if agresif else CopyTrade.SKOR_THRESHOLD
    keputusan = scoring.keputusan_dari_skor(skor, threshold)

    if keputusan == "SKIP":
        tracker.catat("copytrade", "skip", market=info["market"][:60], condition_id=cid,
                      outcome=outcome, harga=harga, skor=skor, end_date=info.get("end_date", ""),
                      keterangan=f"{len(pendukung)} trader")
        print(f"  ⏭️  SKIP {info['market'][:45]} — skor {skor} < {threshold}")
        return False

    # sizing: Kelly (berbasis edge) ATAU flat (kalau Kelly di-off)
    wr = sum(performa[w]["win_rate"] for w in pendukung if w in performa) / max(1, len(pendukung))
    if CopyTrade.KELLY_ENABLED:
        frac = kelly.kelly_fraction(wr, harga)
        if frac <= 0 and agresif:
            frac = CopyTrade.FLAT_FRAC   # paper agresif: flat fallback biar tetap ada bet
    else:
        frac = CopyTrade.FLAT_FRAC       # Kelly off -> flat sizing (gak diciutin)
    size = round(Common.MAX_PER_TRADE * frac, 2)
    if size <= 0:
        tracker.catat("copytrade", "skip_kelly", market=info["market"][:60], condition_id=cid,
                      outcome=outcome, harga=harga, skor=skor, end_date=info.get("end_date", ""),
                      keterangan="Kelly=0 (harga gak ngasih edge)")
        print(f"  ⏭️  SKIP(Kelly) {info['market'][:45]} — harga ${harga} gak ada edge")
        return False

    hasil = executor.place_market_buy(cid, outcome, size)
    tag = " [agresif]" if agresif else ""
    tracker.catat("copytrade", "ikut", market=info["market"][:60], condition_id=cid,
                  outcome=outcome, harga=harga, size_usd=size, skor=skor,
                  end_date=info.get("end_date", ""),
                  keterangan=f"{len(pendukung)} trader · {hasil['status']}{tag}")
    print(f"  ✅ IKUT {info['market'][:45]} '{outcome}' ${size} (skor {skor}, {hasil['status']}){tag}")
    notify.alert_sinyal("✅ Copy-trade signal" + tag, [
        f"{info['market'][:55]}", f"Outcome: {outcome} @ ${harga}",
        f"Size: ${size} · Skor: {skor} · {len(pendukung)} trader · {hasil['status']}"])
    return True


def satu_siklus(wallets, performa, state):
    """1 putaran: refresh posisi tiap trader, cari posisi baru, evaluasi consensus."""
    posisi_sekarang = {}
    for w in wallets:
        posisi_sekarang[w] = _snapshot_posisi(w)
        time.sleep(0.2)

    # kumpulkan siapa saja yang megang tiap (market, outcome)
    holders = {}
    for w, snap in posisi_sekarang.items():
        for key, info in snap.items():
            holders.setdefault(key, {"pendukung": [], "info": info})
            holders[key]["pendukung"].append(w)

    # paper agresif: 1 trader udah cukup jadi sinyal (bukan butuh consensus 2)
    butuh = 1 if (CopyTrade.SINGLE_TRADER_MODE or _agresif()) else 2
    bet_count = 0
    skip_jauh = 0
    for (cid, outcome), data in holders.items():
        pendukung = data["pendukung"]
        if len(pendukung) < butuh:
            continue
        # filter resolve: skip market yang resolve-nya lebih dari MAX_HARI_KE_RESOLVE
        hari = _hari_ke_resolve(data["info"].get("end_date"))
        if hari is not None and hari > CopyTrade.MAX_HARI_KE_RESOLVE:
            skip_jauh += 1
            continue
        if _evaluasi_sinyal(cid, outcome, data["info"], pendukung, performa):
            bet_count += 1
            # cap IKUT per siklus biar gak flood Telegram/dashboard sekaligus
            if _agresif() and bet_count >= CopyTrade.AGG_MAX_BETS:
                print(f"  ⏸️  cap {CopyTrade.AGG_MAX_BETS} bet/siklus tercapai — sisanya siklus berikut.")
                break

    if skip_jauh:
        print(f"  ⏭️  {skip_jauh} market di-skip (resolve > {CopyTrade.MAX_HARI_KE_RESOLVE} hari).")
    state["last"] = posisi_sekarang


def run(loop=False):
    """Jalankan copy-trade. loop=True -> monitor terus tiap CHECK_INTERVAL."""
    wallets, performa = pilih_trader()
    if not wallets:
        print("🛑 Tidak ada trader untuk diikuti. Set TRADER_WALLETS atau coba lagi nanti.")
        return
    mode = "SINGLE" if CopyTrade.SINGLE_TRADER_MODE else "CONSENSUS"
    tag = " · AGRESIF" if _agresif() else ""
    print(f"\n▶️  Monitoring {len(wallets)} trader (mode {mode}{tag}, "
          f"{'PAPER' if Common.SIMULASI_MODE else 'LIVE'})…\n")
    state = {}
    while True:
        try:
            satu_siklus(wallets, performa, state)
        except Exception as e:
            print(f"⚠️ siklus error: {e}")
        if not loop:
            break
        time.sleep(CopyTrade.CHECK_INTERVAL)
