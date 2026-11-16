"""
Penyimpanan ekor data di Postgres (Neon) — biar data GAK ILANG pas listener
restart dan dashboard gak telat 15-20 menit.

KENAPA ADA (16 Nov 2026)
  Dulu satu-satunya penyimpanan = riwayat.csv yang di-commit ke git tiap siklus.
  Tiga masalah:
    1. Listener dimatiin paksa tiap ~5j40m (timeout / cancel-in-progress) dan gak
       ada yang nyimpen sebelum mati -> baris sejak commit terakhir ILANG, padahal
       alert Telegram-nya udah kekirim. Listener baru gak tau posisi itu udah
       di-bet -> bisa nge-bet ulang.
    2. Dashboard baca CSV dari GitHub: telat <=15 mnt commit + <=5 mnt cache CDN.
    3. Repo bengkak: commit 55 MB tiap 15 mnt -> 270 MB pack dalam 4 bulan.

RANCANGAN: DB = EKOR, git = SNAPSHOT
  Batas Neon Free yang nentuin bentuknya (dicek 16 Nov 2026):
    - egress 5 GB/bulan, lewat = compute DISUSPEND sampai bulan depan
    - 100 CU-jam/bulan, tidur otomatis setelah 5 mnt nganggur
  Jadi DB gak boleh jadi tempat narik SELURUH riwayat (240rb baris, ~55 MB) —
  sekali boot aja udah 55 MB, 5 boot/hari = 8 GB/bulan. Makanya:
    - CSV lokal tetep jadi salinan kerja (semua baca tetep cepet dari disk).
    - Tiap baris baru juga diantre ke DB, di-flush sekali di akhir tiap siklus
      dan pas proses mau dimatiin (SIGTERM/SIGINT). Sekali per siklus = DB
      sempet tidur ~10 dari 15 menit -> ~70 CU-jam/bulan.
    - Snapshot CSV ke git cuma tiap SNAPSHOT_JAM (bukan tiap siklus).
    - Pas boot: CSV dari git (snapshot) + baris DB yang lebih baru dari
      (snapshot - JENDELA_BOOT_JAM) -> digabung, dedup per kunci baris.
    - Dashboard: snapshot dari GitHub sekali + /api/terbaru (baris DB yang lebih
      baru dari baris terakhir yang dia punya) tiap menit.

GAGAL = TURUN KELAS, BUKAN MATI
  DATABASE_URL kosong -> modul ini mati total, bot jalan persis kayak dulu.
  DB gak kejangkau pas boot -> mode git (commit tiap siklus kayak dulu) sampai
  restart berikutnya, plus satu alert Telegram. Flush gagal di tengah jalan ->
  antrean ditahan di memori dan dicoba lagi siklus depan; kalau gagal terus
  >= _MAX_GAGAL kali, pindah ke mode git juga.
"""
import json
import os
import signal
import threading
import time
from datetime import datetime, timedelta, timezone

from .. import config

SNAPSHOT_JAM = 3          # commit CSV ke git tiap segini jam (mode DB)
JENDELA_BOOT_JAM = 12     # boot narik ekor DB segini jam ke belakang dari snapshot
_MAX_GAGAL = 4            # flush gagal beruntun segini kali -> turun ke mode git



# kolom persis kayak CSV (semuanya teks, biar semantik "" vs "true" sama).
# Diambil lazy: tracker & dampak sendiri ngimport modul ini.
def _kolom_riwayat():
    from .tracker import FIELDS
    return FIELDS


def _kolom_dampak():
    from .dampak import FIELDS
    return FIELDS

_lock = threading.RLock()
_kunci_flush = threading.Lock()   # satu flush dalam satu waktu (worker vs sinyal)
_conn = None
_antre_riwayat = []       # list of dict (baris riwayat yang belum masuk DB)
_antre_dampak = {}        # kunci (wallet,cid,outcome) -> dict (versi terakhir)
_antre_kv = {}            # kunci -> teks
_gagal = 0
_mode = None              # None = belum diputusin, "db", "git"
_snapshot_terakhir = 0.0  # time.monotonic() commit snapshot terakhir


def _url():
    return (os.getenv("DATABASE_URL") or "").strip()


def dipakai():
    """True kalau DB dikonfigurasi DAN lagi sehat (mode db)."""
    return bool(_url()) and _mode == "db"


def mode():
    return _mode or ("db" if _url() else "git")


def _skema():
    # skema terpisah buat tes, biar gak pernah nyentuh tabel asli
    s = (os.getenv("POLYBOT_DB_SKEMA") or "public").strip()
    if not s.replace("_", "").isalnum():
        raise ValueError(f"POLYBOT_DB_SKEMA gak valid: {s!r}")
    return s


def _q(nama):
    return f'"{_skema()}"."{nama}"'


def _sambung():
    """Koneksi tunggal, dibikin ulang kalau putus. Neon nidurin compute setelah
    5 mnt nganggur; bangun lagi butuh ~1-3 detik, jadi connect_timeout longgar."""
    global _conn
    import psycopg
    if _conn is not None and not _conn.closed:
        return _conn
    # prepare_threshold=None: URL Neon default lewat pgbouncer (pooler), yang
    # gak bisa diandelin buat prepared statement lintas transaksi.
    _conn = psycopg.connect(_url(), connect_timeout=15, autocommit=True,
                            prepare_threshold=None)
    return _conn


def _putus():
    global _conn
    try:
        if _conn is not None:
            _conn.close()
    except Exception:
        pass
    _conn = None


def _siapkan_tabel(cur):
    kol_r = ", ".join(f'"{k}" text' for k in _kolom_riwayat())
    kol_d = ", ".join(f'"{k}" text' for k in _kolom_dampak())
    cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{_skema()}"')
    # uid = kunci baris (lihat _uid_riwayat): insert idempoten, retry aman.
    # ts = timestamp yang bisa di-query (dashboard nanya "lebih baru dari X").
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {_q('riwayat')} (
        uid text PRIMARY KEY, ts timestamptz NOT NULL, {kol_r},
        masuk timestamptz NOT NULL DEFAULT now())""")
    cur.execute(f"CREATE INDEX IF NOT EXISTS riwayat_ts ON {_q('riwayat')} (ts)")
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {_q('dampak')} (
        wallet_k text, cid_k text, outcome_k text, {kol_d},
        diubah timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (wallet_k, cid_k, outcome_k))""")
    cur.execute(f"CREATE INDEX IF NOT EXISTS dampak_diubah ON {_q('dampak')} (diubah)")
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {_q('kv')} (
        k text PRIMARY KEY, v text, diubah timestamptz NOT NULL DEFAULT now())""")


def _uid_riwayat(r):
    """Kunci baris. timestamp dari datetime.now() (mikrodetik) + aksi + market
    + outcome cukup unik; dua baris yang identik di semua itu = baris yang sama."""
    return "|".join(str(r.get(k, "")) for k in ("timestamp", "aksi", "condition_id", "outcome"))


def _kunci_dampak(r):
    return (r.get("wallet", ""), r.get("condition_id", ""), r.get("outcome", ""))


# ── antre (dipanggil dari tracker / dampak / trader_prefs) ───────────────────
def antre_riwayat(row):
    if not _url() or _mode == "git":
        return
    with _lock:
        _antre_riwayat.append(dict(row))


def antre_dampak(rows):
    if not _url() or _mode == "git":
        return
    with _lock:
        for r in rows:
            _antre_dampak[_kunci_dampak(r)] = dict(r)


def antre_kv(k, v):
    if not _url() or _mode == "git":
        return
    with _lock:
        _antre_kv[k] = v


def sisa_antrean():
    with _lock:
        return len(_antre_riwayat) + len(_antre_dampak) + len(_antre_kv)


# ── flush ────────────────────────────────────────────────────────────────────
def flush():
    """Kirim semua antrean ke DB dalam satu transaksi. Balikin True kalau sukses
    (atau gak ada yang dikirim). Gagal -> antrean TETEP ada, dicoba lagi nanti."""
    if not dipakai():
        return False
    with _kunci_flush:
        return _flush()


def _flush():
    global _gagal, _mode
    with _lock:
        r_rows = list(_antre_riwayat)
        d_rows = list(_antre_dampak.values())
        kv = dict(_antre_kv)
    if not (r_rows or d_rows or kv):
        return True
    try:
        conn = _sambung()
        with conn.transaction():
            with conn.cursor() as cur:
                if r_rows:
                    kol = ", ".join(f'"{k}"' for k in _kolom_riwayat())
                    ph = ", ".join(["%s"] * (len(_kolom_riwayat()) + 2))
                    cur.executemany(
                        f"INSERT INTO {_q('riwayat')} (uid, ts, {kol}) VALUES ({ph}) "
                        f"ON CONFLICT (uid) DO NOTHING",
                        [[_uid_riwayat(r), r.get("timestamp")]
                         + [str(r.get(k, "") if r.get(k) is not None else "") for k in _kolom_riwayat()]
                         for r in r_rows])
                if d_rows:
                    kol = ", ".join(f'"{k}"' for k in _kolom_dampak())
                    ph = ", ".join(["%s"] * (len(_kolom_dampak()) + 3))
                    upd = ", ".join(f'"{k}" = EXCLUDED."{k}"' for k in _kolom_dampak())
                    cur.executemany(
                        f"INSERT INTO {_q('dampak')} (wallet_k, cid_k, outcome_k, {kol}) "
                        f"VALUES ({ph}) ON CONFLICT (wallet_k, cid_k, outcome_k) "
                        f"DO UPDATE SET {upd}, diubah = now()",
                        [list(_kunci_dampak(r)) + [str(r.get(k, "") or "") for k in _kolom_dampak()]
                         for r in d_rows])
                for k, v in kv.items():
                    cur.execute(
                        f"INSERT INTO {_q('kv')} (k, v) VALUES (%s, %s) "
                        f"ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v, diubah = now()",
                        (k, v))
    except Exception as e:
        _putus()
        _gagal += 1
        print(f"  ⚠️  DB flush gagal ({_gagal}/{_MAX_GAGAL}): {str(e)[:150]}")
        if _gagal >= _MAX_GAGAL:
            _mode = "git"
            _kabari(f"⚠️ <b>Database gak kejangkau</b> {_gagal}x beruntun — balik ke "
                    f"mode git (commit tiap siklus) sampai restart. Error: "
                    f"<code>{str(e)[:150]}</code>")
        return False
    # sukses: buang yang barusan kekirim (yang nambah selama flush tetep antre)
    with _lock:
        del _antre_riwayat[:len(r_rows)]
        for r in d_rows:
            k = _kunci_dampak(r)
            if _antre_dampak.get(k) == r:
                del _antre_dampak[k]
        for k, v in kv.items():
            if _antre_kv.get(k) == v:
                del _antre_kv[k]
    _gagal = 0
    return True


def _kabari(teks):
    try:
        from . import notify
        notify.kirim(teks, parse_mode="HTML")
    except Exception:
        pass


# ── boot: gabungin ekor DB ke snapshot lokal ─────────────────────────────────
def _ts_terakhir_lokal(rows, kolom):
    ts = [r.get(kolom) or "" for r in rows]
    ts = [t for t in ts if t]
    return max(ts) if ts else None


def pulihkan():
    """Dipanggil SEKALI pas boot, sebelum loop jalan. Gabungin baris DB yang
    belum ada di snapshot git ke file lokal. Nentuin mode: db / git."""
    global _mode, _snapshot_terakhir
    _snapshot_terakhir = time.monotonic()   # snapshot dari git = "barusan"
    if not _url():
        _mode = "git"
        return {"mode": "git", "alasan": "DATABASE_URL kosong"}

    from . import tracker, dampak, trader_prefs
    terakhir_err = None
    for coba in range(3):
        try:
            conn = _sambung()
            with conn.cursor() as cur:
                _siapkan_tabel(cur)
                hasil = _gabung_riwayat(cur, tracker)
                hasil.update(_gabung_dampak(cur, dampak))
                hasil.update(_pulihkan_kv(cur, trader_prefs))
            _mode = "db"
            hasil["mode"] = "db"
            return hasil
        except Exception as e:
            terakhir_err = e
            _putus()
            time.sleep(3 * (coba + 1))
    _mode = "git"
    _kabari("⚠️ <b>Database gak kejangkau pas boot</b> — jalan di mode git (commit "
            "tiap siklus, kayak dulu) sampai restart berikutnya. Error: "
            f"<code>{str(terakhir_err)[:150]}</code>")
    return {"mode": "git", "alasan": str(terakhir_err)[:200]}


def _batas(ts_teks):
    """(snapshot - JENDELA_BOOT_JAM) sebagai timestamptz; None = ambil semua."""
    if not ts_teks:
        return None
    try:
        t = datetime.fromisoformat(ts_teks)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t - timedelta(hours=JENDELA_BOOT_JAM)


def _gabung_riwayat(cur, tracker):
    import csv
    lokal = tracker.baca_semua()
    batas = _batas(_ts_terakhir_lokal(lokal, "timestamp"))
    kol = ", ".join(f'"{k}"' for k in _kolom_riwayat())
    if batas is None:
        cur.execute(f"SELECT {kol} FROM {_q('riwayat')} ORDER BY ts")
    else:
        cur.execute(f"SELECT {kol} FROM {_q('riwayat')} WHERE ts > %s ORDER BY ts", (batas,))
    dari_db = [dict(zip(_kolom_riwayat(), row)) for row in cur.fetchall()]
    punya = {_uid_riwayat(r) for r in lokal}
    baru = [r for r in dari_db if _uid_riwayat(r) not in punya]
    if baru:
        path = tracker._path()
        ada = os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_kolom_riwayat())
            if not ada:
                w.writeheader()
            for r in sorted(baru, key=lambda x: x.get("timestamp") or ""):
                w.writerow({k: r.get(k) or "" for k in _kolom_riwayat()})
        tracker._kunci_cache = None     # file berubah dari luar catat()
    return {"riwayat_dari_db": len(dari_db), "riwayat_ditambah": len(baru)}


def _gabung_dampak(cur, dampak):
    lokal = dampak._baca()
    batas = _batas(_ts_terakhir_lokal(lokal, "ts0"))
    kol = ", ".join(f'"{k}"' for k in _kolom_dampak())
    if batas is None:
        cur.execute(f"SELECT {kol} FROM {_q('dampak')}")
    else:
        # diubah, bukan ts0: baris lama yang p1/p6-nya baru keisi juga harus kebawa
        cur.execute(f"SELECT {kol} FROM {_q('dampak')} WHERE diubah > %s", (batas,))
    dari_db = [dict(zip(_kolom_dampak(), row)) for row in cur.fetchall()]
    if not dari_db:
        return {"dampak_dari_db": 0, "dampak_berubah": 0}
    idx = {_kunci_dampak(r): i for i, r in enumerate(lokal)}
    berubah = 0
    for r in dari_db:
        r = {k: (r.get(k) or "") for k in _kolom_dampak()}
        i = idx.get(_kunci_dampak(r))
        if i is None:
            lokal.append(r)
            berubah += 1
        elif lokal[i] != r:
            lokal[i] = r                 # versi DB selalu yang terbaru
            berubah += 1
    if berubah:
        lokal.sort(key=lambda x: x.get("ts0") or "")
        dampak._tulis(lokal, ke_db=False)
    return {"dampak_dari_db": len(dari_db), "dampak_berubah": berubah}


def _pulihkan_kv(cur, trader_prefs):
    cur.execute(f"SELECT v FROM {_q('kv')} WHERE k = 'trader_prefs'")
    row = cur.fetchone()
    if not row or not row[0]:
        return {"prefs_dari_db": False}
    try:
        trader_prefs.tulis(json.loads(row[0]), ke_db=False)
        return {"prefs_dari_db": True}
    except Exception:
        return {"prefs_dari_db": False}


# ── snapshot ke git ──────────────────────────────────────────────────────────
def waktunya_snapshot():
    """Mode db: commit ke git cuma tiap SNAPSHOT_JAM. Mode git: tiap siklus."""
    if not dipakai():
        return True
    return time.monotonic() - _snapshot_terakhir >= SNAPSHOT_JAM * 3600


def tandai_snapshot():
    global _snapshot_terakhir
    _snapshot_terakhir = time.monotonic()


# ── matiin dengan rapi ───────────────────────────────────────────────────────
def pasang_penangkap_sinyal():
    """SIGTERM (timeout 340m) / SIGINT (cancel GitHub) -> flush dulu, baru keluar.
    GitHub ngasih ~7,5 detik antara SIGINT dan SIGKILL; satu INSERT batch cukup.
    Cuma bisa dipasang dari main thread."""
    if not _url():
        return

    def _tangkap(signum, frame):
        try:
            ok = flush()
            print(f"🛑 sinyal {signum}: flush DB {'OK' if ok else 'GAGAL'} "
                  f"(sisa antre {sisa_antrean()})")
        finally:
            raise SystemExit(0)

    for s in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(s, _tangkap)
        except (ValueError, OSError):
            pass       # bukan main thread / platform gak dukung
