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

import requests

from . import resolver, tracker, notify, dashboard
from .. import config


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

    print(f"🔎 Evaluasi {len(kandidat)} posisi…")
    menang_total = kalah_total = belum = 0
    net = 0.0
    seen = set()

    for r in kandidat:
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
