"""Ukur DAMPAK HARGA dari posisi trader — pertanyaan yang beda dari menang/kalah.

Selama ini kita nilai trader dari hasil akhir (menang/kalah). Itu lambat dan
berisik. Di sini kita tanya hal lain: **begitu trader masuk, harganya gerak ke
arah dia gak?**

    trader beli YES @ $0.40  ->  1 jam kemudian $0.45  = dia duluan tau
                             ->  1 jam kemudian $0.40  = gak ada info

Kenapa ini jauh lebih bertenaga dari menang/kalah:
  * umpan balik ~1 jam, bukan 29 jam  -> sampel numpuk ~50x lebih cepat
  * nilainya KONTINU (selisih harga), bukan biner 0/1 -> varians jauh lebih
    kecil, jadi butuh jauh lebih sedikit pengamatan buat kesimpulan
  * gak perlu nunggu market resolve sama sekali

Dan sekalian jawab pertanyaan inti yang belum pernah diuji: kalau harga gerak
SEKETIKA pas mereka masuk, berarti waktu bot nyalin (telat 0-15 menit) pestanya
udah bubar. Itu menjelaskan KENAPA copytrade gagal, bukan cuma bahwa dia gagal.

Dicatat untuk SEMUA posisi baru yang kelihatan — bukan cuma yang kita bet.
Sampelnya jadi jauh lebih besar dan ngukur sinyal TRADER-nya, bukan hasil
saringan kita sendiri.
"""
import csv
import os
from datetime import datetime, timezone

from . import api
from .. import config

FIELDS = ["ts0", "wallet", "condition_id", "outcome", "token_id", "market",
          "p0", "ts1", "p1", "ts6", "p6"]

_JAM = 3600
_TEMPO = {"p1": 1 * _JAM, "p6": 6 * _JAM}   # kapan tiap pengukuran jatuh tempo
_MENYERAH = 14 * _JAM                        # lewat ini, baris ditutup apa adanya
_MAKS_UKUR = 40                              # batas panggilan API per siklus


def _path():
    return os.path.join(config.Common.DATA_DIR, "dampak_harga.csv")


def _sekarang():
    return datetime.now(timezone.utc)


def _baca():
    p = _path()
    if not os.path.exists(p):
        return []
    try:
        with open(p, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def _tulis(rows):
    p = _path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    os.replace(tmp, p)       # atomik: file gak pernah kebaca setengah jadi


def _umur(r):
    try:
        return (_sekarang() - datetime.fromisoformat(r["ts0"])).total_seconds()
    except (ValueError, KeyError):
        return None


def catat_baru(posisi_baru):
    """Catat harga T+0 untuk posisi yang baru kelihatan.

    posisi_baru: list dict {wallet, condition_id, outcome, token_id, market, harga}
    Satu (wallet, market, outcome) cuma dicatat SEKALI — kalau trader nambah
    posisi di market yang sama, itu bukan sinyal masuk yang baru.
    """
    if not posisi_baru:
        return 0
    rows = _baca()
    sudah = {(r["wallet"], r["condition_id"], r["outcome"]) for r in rows}
    ts = _sekarang().isoformat(timespec="seconds")
    n = 0
    for p in posisi_baru:
        kunci = (p["wallet"], p["condition_id"], p["outcome"])
        if kunci in sudah or not p.get("token_id") or not p.get("harga"):
            continue
        sudah.add(kunci)
        rows.append({"ts0": ts, "wallet": p["wallet"],
                     "condition_id": p["condition_id"], "outcome": p["outcome"],
                     "token_id": p["token_id"], "market": (p.get("market") or "")[:60],
                     "p0": f"{float(p['harga']):.4f}"})
        n += 1
    if n:
        _tulis(rows)
    return n


def ukur_jatuh_tempo(maks=_MAKS_UKUR):
    """Isi harga T+1j / T+6j untuk baris yang waktunya udah tiba."""
    rows = _baca()
    if not rows:
        return 0
    diukur = 0
    for r in rows:
        if diukur >= maks:
            break
        umur = _umur(r)
        if umur is None:
            continue
        for kolom, tempo in _TEMPO.items():
            if r.get(kolom) or umur < tempo:
                continue
            if umur > _MENYERAH:
                r[kolom] = "-"          # kelewat, tandai biar gak dicoba terus
                continue
            harga = api.clob_midpoint(r["token_id"])
            diukur += 1
            # midpoint 0/1 = market udah resolve; itu tetap info valid (harga
            # gerak sepenuhnya ke satu sisi), jadi dicatat apa adanya.
            r[kolom] = f"{harga:.4f}" if harga is not None else "-"
            r["ts1" if kolom == "p1" else "ts6"] = _sekarang().isoformat(timespec="seconds")
            break                        # 1 pengukuran per baris per siklus
    if diukur:
        _tulis(rows)
    return diukur


def ringkas():
    """Ringkasan buat ditampilkan di /status atau log siklus."""
    rows = _baca()
    jadi = [r for r in rows if r.get("p1") and r["p1"] != "-"]
    return {"total": len(rows), "terukur": len(jadi)}
