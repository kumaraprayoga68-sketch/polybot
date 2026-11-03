"""
Pencatatan terpadu — semua keputusan/peluang/eksekusi dari SEMUA strategi masuk
ke satu CSV (data/riwayat.csv). Dipakai buat evaluasi hasil setelah market resolve
dan (nanti) dynamic sizing berbasis streak.
"""
import os
import csv
import uuid
from datetime import datetime, timezone

from .. import config
from . import dashboard

FIELDS = [
    "timestamp", "strategi", "mode", "aksi", "market", "condition_id",
    "outcome", "harga", "size_usd", "skor", "edge_pct", "keterangan",
    "end_date", "resolved", "menang", "pnl",
    "trader",   # wallet(s) trader yg di-copy (pipe-separated kalau consensus). Baru: kosong buat baris lama.
]


def _path():
    os.makedirs(config.Common.DATA_DIR, exist_ok=True)
    return os.path.join(config.Common.DATA_DIR, "riwayat.csv")


def _migrasi_header(path):
    """Kalau header CSV lama (belum ada kolom baru mis. end_date), tulis ulang file
    dengan header FIELDS terkini — baris lama dapet kolom baru kosong. Sekali jalan."""
    try:
        with open(path, newline="", encoding="utf-8") as f:
            header = f.readline().strip()
    except OSError:
        return
    if header == ",".join(FIELDS):
        return  # sudah terkini
    rows = baca_semua()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def catat(strategi, aksi, **kw):
    """Tulis 1 baris. Field yang gak diisi -> kosong. Dedup diserahkan ke caller."""
    path = _path()
    baru = not os.path.exists(path)
    if not baru:
        _migrasi_header(path)   # jamin header sinkron sebelum append
    row = {k: "" for k in FIELDS}
    row.update({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "strategi": strategi,
        "mode": "paper" if config.Common.SIMULASI_MODE else "live",
        "aksi": aksi,
    })
    for k, v in kw.items():
        if k in row:
            row[k] = v
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if baru:
            w.writeheader()
        w.writerow(row)

    # Jaga cache dedup sinkron sama baris yang BARUSAN kita tulis, dan segerin
    # stat-nya — kalau engga, tiap catat() bikin cache-nya kebuang dan siklus
    # berikutnya parse ulang 48 MB (lihat catatan di _muat_kunci).
    global _cache_stat
    if _kunci_cache is not None:
        _kunci_cache.add((row.get("condition_id", ""), row.get("outcome", "")))
        _cache_stat = _stat(path)

    # Push ke dashboard Vercel (fail-safe; gak nge-block kalau gagal/gak dikonfigurasi).
    event = dict(row)
    event["id"] = "EVT-" + uuid.uuid4().hex[:12]
    event["time"] = event.pop("timestamp")
    dashboard.push_event(event)
    return row


def baca_semua():
    path = _path()
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── cache dedup ───────────────────────────────────────────────────────────────
# sudah_dievaluasi() dipanggil SEKALI PER KANDIDAT, dan dulu dia manggil
# baca_semua() -> parse ulang riwayat.csv (48 MB, 196rb baris) tiap panggilan.
# Terukur 3 Nov: satu siklus 484 kandidat -> 10 bet kelar dalam 35 detik, tapi
# siklusnya baru beres 17 MENIT kemudian, hampir semuanya kebuang di sini.
#
# Sekarang kuncinya di-cache. Divalidasi pakai (ukuran, mtime) file: kalau file
# diganti pihak lain (mis. git pull --rebase di _persist_riwayat narik baris dari
# hunt.yml) stat-nya beda -> cache dibangun ulang. Append dari catat() kita
# sendiri nambahin kunci + nyegerin stat, jadi nulis gak bikin cache kebuang.
_kunci_cache = None     # set of (condition_id, outcome)
_cache_stat = None      # (ukuran, mtime_ns) file pas cache dibikin


def _stat(path):
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def _muat_kunci(path):
    """Set semua (condition_id, outcome) di riwayat. Streaming, bukan DictReader."""
    global _kunci_cache, _cache_stat
    st = _stat(path)
    if _kunci_cache is not None and st == _cache_stat:
        return _kunci_cache
    kunci = set()
    try:
        with open(path, newline="", encoding="utf-8") as f:
            baris = csv.reader(f)
            head = next(baris, None)
            if head and "condition_id" in head and "outcome" in head:
                i_cid, i_out = head.index("condition_id"), head.index("outcome")
                lebar = max(i_cid, i_out)
                for b in baris:
                    if len(b) > lebar:
                        kunci.add((b[i_cid], b[i_out]))
    except OSError:
        pass
    _kunci_cache, _cache_stat = kunci, st
    return kunci


def sudah_dievaluasi(condition_id, outcome):
    """Cek apakah sinyal (market+outcome) ini udah pernah dicatat (dedup).

    Sengaja ngecek SEMUA aksi (ikut, skip_harga, hasil, dll), sama kaya versi
    lama yang nyisir tiap baris — bukan cuma baris 'ikut'.
    """
    return (condition_id, outcome) in _muat_kunci(_path())
