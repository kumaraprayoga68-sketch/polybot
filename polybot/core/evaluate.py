"""
Evaluasi win/loss — cek market yang udah resolve dari riwayat.csv, hitung menang/
kalah + PnL (paper) beneran. Melengkapi feedback loop (gantiin cek_hasil.py lama).

PnL paper untuk 1 bet directional (copytrade):
  beli $S di harga P -> dapat S/P share. Menang -> tiap share bayar $1:
      pnl = (S/P) - S = S*(1-P)/P
  Kalah -> share jadi $0: pnl = -S
Hasil dicatat sebagai baris aksi="hasil" (resolved/menang/pnl), di-dedup biar gak
dobel, dan di-push ke dashboard + Telegram.
"""
import os
import io
import csv as _csv
from datetime import datetime, timezone

import requests

from . import resolver, tracker, notify, dashboard
from .. import config

# Anti-choke: evaluate dulu ngecek SEMUA bet belum-resolve tiap siklus (bisa 2000+),
# tiap satu = 1 panggilan CLOB -> kena rate-limit/timeout -> nol resolusi. Sekarang
# dibatasi & diprioritas biar resolusi segar selalu keproses.
# CLOB gak nge-rate-limit (kebukti 50 call back-to-back sukses semua), cuma lambat
# ~354ms/call. Jadi cap-nya TINGGI (cuma buat jaga runaway) — biar backlog kekejar
# semua tiap siklus. 1600 call ≈ 9 menit, muat di loop 30 menit; sekali backlog
# kelar, kandidat anjlok jadi ratusan (yg udah resolve keluar antrian) = 2-3 menit.
_MAX_CEK_PER_SIKLUS = 2000  # safety valve anti-runaway, bukan penyekik
_ZOMBIE_HARI = 14           # end_date lewat > ini & masih open = zombie, berhenti dicek


def _hari_lewat(end_date):
    """Berapa hari end_date SUDAH lewat dari sekarang. None kalau gak kebaca.
    >0 = udah berakhir (kandidat resolve). <0 = masih ke depan (belum waktunya)."""
    if not end_date:
        return None
    try:
        d = datetime.strptime(str(end_date)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return (datetime.now(timezone.utc).date() - d).days


def scorecard():
    """
    Scorecard RESMI — baca riwayat.csv dari GitHub (sumber yang sama dengan dashboard,
    di-commit CI). Jadi angka /evaluate konsisten dengan dashboard, gak ketuker sama
    file lokal tiap mesin. Fallback ke file lokal kalau fetch gagal.
    """
    txt, src = None, "GitHub (CI)"
    try:
        r = requests.get(config.POLYBOT_HISTORY_URL, timeout=10)
        if r.ok:
            txt = r.text
    except Exception:
        pass
    if not txt:
        path = os.path.join(config.Common.DATA_DIR, "riwayat.csv")
        if os.path.exists(path):
            txt = open(path, encoding="utf-8").read()
            src = "lokal (fallback)"
    if not txt:
        print("📭 Belum ada data riwayat sama sekali.")
        return

    rows = list(_csv.DictReader(io.StringIO(txt)))
    ikut = [r for r in rows if r.get("aksi") in ("ikut", "eksekusi")]
    hasil = [r for r in rows if r.get("aksi") == "hasil"]
    menang = sum(1 for r in hasil if r.get("menang") == "true")
    kalah = sum(1 for r in hasil if r.get("menang") == "false")
    done = {(r.get("condition_id"), r.get("outcome")) for r in hasil}
    pending = [r for r in ikut if (r.get("condition_id"), r.get("outcome")) not in done]

    def _f(r, k):
        try:
            return float(r.get(k) or 0)
        except (TypeError, ValueError):
            return 0.0
    net = sum(_f(r, "pnl") for r in hasil)
    exposure = sum(_f(r, "size_usd") for r in pending)
    resolved = menang + kalah
    wr = (menang / resolved * 100) if resolved else 0

    print(f"📊 Scorecard polybot (sumber: {src})")
    print(f"  Bet (ikut)      : {len(ikut)}")
    print(f"  Resolved        : {resolved}  ({menang}W / {kalah}L, win rate {wr:.0f}%)")
    print(f"  Pending         : {len(pending)}")
    print(f"  Exposure paper  : ${exposure:.1f}")
    print(f"  Net PnL (paper) : ${net:+.2f}")
    print(f"  (angka ini sama dengan dashboard — evaluasi otomatis jalan tiap 30 menit di CI)")


def _sudah_dievaluasi(rows, cid, outcome):
    for r in rows:
        if r.get("aksi") == "hasil" and r.get("condition_id") == cid and r.get("outcome") == outcome:
            return True
    return False


def _pnl_paper(size, harga, menang):
    try:
        s = float(size or 0)
        p = float(harga or 0)
    except (TypeError, ValueError):
        return 0.0
    if s <= 0:
        return 0.0
    if not menang:
        return round(-s, 2)
    if p <= 0 or p >= 1:
        return 0.0
    return round(s * (1 - p) / p, 2)


def run():
    rows = tracker.baca_semua()
    if not rows:
        print("📭 riwayat.csv kosong — belum ada yang bisa dievaluasi.")
        return

    kandidat = [r for r in rows
                if r.get("aksi") in ("ikut", "eksekusi")
                and r.get("condition_id") and r.get("outcome")
                and not _sudah_dievaluasi(rows, r["condition_id"], r["outcome"])]

    if not kandidat:
        print("✅ Gak ada posisi baru yang perlu dievaluasi (semua sudah / belum ada IKUT).")
        return

    # Prioritas biar evaluate gak keok kalau kandidat menumpuk:
    #  - end_date masih KE DEPAN  -> mustahil udah resolve, skip (hemat panggilan)
    #  - end_date lewat > ZOMBIE  -> market zombie, gak resolve2, skip
    #  - end_date kosong          -> gak tau, tetep cek (taro paling belakang)
    # Sisanya urut PALING BARU BERAKHIR DULU -> resolusi segar selalu keproses
    # walau kena cap per siklus.
    layak = []
    for r in kandidat:
        hl = _hari_lewat(r.get("end_date"))
        if hl is None:
            layak.append((r, None))          # end_date gak kebaca -> cek belakangan
        elif hl < 0:
            continue                         # belum waktunya (mustahil udah resolve)
        elif hl > _ZOMBIE_HARI:
            continue                         # zombie, buang
        else:
            layak.append((r, hl))            # 0 = baru berakhir hari ini
    # Urut: yang PALING BARU berakhir duluan. Data nunjukin bet umur 0-4 hari ~95-100%
    # udah resolve (event olahraga settle cepet), sedangkan yg 5-14 hari malah 0%
    # (macet/zombie). Jadi newest-first = tiap panggilan API paling "berbuah".
    # end_date gak kebaca ditaro paling belakang.
    layak.sort(key=lambda x: (x[1] is None, x[1] if x[1] is not None else 0))
    antri = [r for r, _ in layak][:_MAX_CEK_PER_SIKLUS]
    sisa = len(layak) - len(antri)

    if not antri:
        print(f"✅ {len(kandidat)} belum resolve, tapi semua masih ke depan / zombie — gak ada yg dicek.")
        return

    print(f"🔎 {len(kandidat)} belum resolve · cek {len(antri)} paling mungkin resolve"
          + (f" (sisa {sisa} siklus berikut)" if sisa else "") + "…")
    menang_total = kalah_total = belum = 0
    net = 0.0
    seen = set()

    for r in antri:
        cid, outcome = r["condition_id"], r["outcome"]
        if (cid, outcome) in seen:
            continue
        seen.add((cid, outcome))

        status = resolver.cek_status(cid, outcome)
        if not status.get("resolved"):
            belum += 1
            continue

        menang = bool(status.get("menang"))
        pnl = _pnl_paper(r.get("size_usd"), r.get("harga"), menang)
        net += pnl
        if menang:
            menang_total += 1
        else:
            kalah_total += 1

        tracker.catat("copytrade", "hasil", market=r.get("market", "")[:60],
                      condition_id=cid, outcome=outcome, harga=r.get("harga"),
                      size_usd=r.get("size_usd"), skor=r.get("skor"),
                      trader=r.get("trader", ""),   # bawa wallet dari baris ikut -> win-rate per trader
                      resolved="true", menang="true" if menang else "false", pnl=pnl,
                      keterangan=("MENANG" if menang else "KALAH"))
        emoji = "🟢" if menang else "🔴"
        print(f"  {emoji} {'MENANG' if menang else 'KALAH '} {r.get('market','')[:45]} "
              f"'{outcome}' pnl ${pnl:+.2f}")

    dievaluasi = menang_total + kalah_total
    wr = (menang_total / dievaluasi * 100) if dievaluasi else 0
    print(f"\n── Ringkasan ──")
    print(f"  Dievaluasi: {dievaluasi}  ({menang_total}W / {kalah_total}L, win rate {wr:.0f}%)")
    print(f"  Belum resolve: {belum}")
    print(f"  Net PnL (paper): ${net:+.2f}")

    if dievaluasi:
        notify.alert_sinyal("📊 Evaluasi hasil polybot", [
            f"Dievaluasi: {dievaluasi} ({menang_total}W/{kalah_total}L, wr {wr:.0f}%)",
            f"Net PnL (paper): ${net:+.2f}", f"Belum resolve: {belum}"])
