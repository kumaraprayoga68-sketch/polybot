"""
Preferensi trader yang di-copy — PERSISTEN (disimpen ke data/trader_prefs.json,
ikut di-commit sama listener). Beda dari config env yang ke-reset tiap listener
restart (~5 jam); ini bertahan.

Dipakai buat:
  - mode "auto"   : ikut hasil scan leaderboard (default)
  - mode "manual" : cuma copy wallet yang dipilih user
  - blocklist     : buang wallet tertentu, jalan di dua mode
"""
import os
import json

from .. import config

_NAMA = "trader_prefs.json"
_DEFAULT = {"mode": "auto", "manual": [], "block": []}


def _path():
    os.makedirs(config.Common.DATA_DIR, exist_ok=True)
    return os.path.join(config.Common.DATA_DIR, _NAMA)


def baca():
    """Balikin dict prefs. Fail-safe: file rusak/gak ada -> default."""
    try:
        with open(_path(), encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return dict(_DEFAULT)
        out = dict(_DEFAULT)
        out["mode"] = d.get("mode") if d.get("mode") in ("auto", "manual") else "auto"
        for k in ("manual", "block"):
            v = d.get(k, [])
            out[k] = [str(w).lower() for w in v if isinstance(w, str) and w.strip()]
        return out
    except Exception:
        return dict(_DEFAULT)


def tulis(prefs):
    """Simpan prefs. Balikin True kalau sukses."""
    try:
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
        return True
    except Exception:
        return False


def terapkan(wallets):
    """Saring daftar wallet sesuai prefs. Dipanggil copytrade sebelum screening.

    mode manual -> pakai daftar manual (abaikan hasil leaderboard).
    blocklist   -> selalu dibuang, di mode apa pun.
    """
    p = baca()
    if p["mode"] == "manual" and p["manual"]:
        wallets = list(p["manual"])
    blok = set(p["block"])
    return [w for w in wallets if w.lower() not in blok]


def cocokkan(inputan, kandidat):
    """Cocokin inputan user ke wallet lengkap.

    Terima alamat penuh ATAU prefix (>=6 char) — biar bisa copas dari dashboard
    yang nampilinnya dipendekin. Balikin (wallet, error). Ambigu -> error.
    """
    s = (inputan or "").strip().lower().rstrip("…").rstrip(".")
    if not s.startswith("0x") or len(s) < 6:
        return None, "Alamat harus mulai '0x' dan minimal 6 karakter."
    if len(s) == 42:
        return s, None
    cocok = sorted({w.lower() for w in kandidat if w.lower().startswith(s)})
    if not cocok:
        return None, f"Gak ada trader yang cocok '{s}'. Pakai alamat lengkap."
    if len(cocok) > 1:
        return None, f"'{s}' cocok ke {len(cocok)} trader — pakai alamat lebih panjang."
    return cocok[0], None
