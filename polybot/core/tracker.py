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


def sudah_dievaluasi(condition_id, outcome):
    """Cek apakah sinyal (market+outcome) ini udah pernah dicatat (dedup)."""
    for r in baca_semua():
        if r.get("condition_id") == condition_id and r.get("outcome") == outcome:
            return True
    return False
