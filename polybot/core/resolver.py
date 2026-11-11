"""
Cek status resolusi & harga market via CLOB publik (tanpa auth) — dipakai buat
evaluasi menang/kalah setelah market resolve, dan capture harga entry.
"""
import re
import unicodedata

from . import api

_BUKAN_ALNUM = re.compile(r"[^a-z0-9]+")


def _normal(s):
    """Nama outcome diperas sampai cuma huruf+angka, buat pencocokan cadangan.

    KENAPA ADA: Polymarket gak konsisten soal tanda baca. Dalam SATU respons yang
    sama, judul market bisa "LoJ: Team WE vs Anyone's Legend" sementara nama
    outcome-nya "Anyones Legend" — apostrofnya ilang di satu field doang. Diukur
    11 Nov 2026 di riwayat asli: 22 bet nyangkut permanen gara-gara ini, semuanya
    nama yang pakai apostrof (O'Higgins FC, Anyone's Legend, CA Newell's Old Boys,
    Christopher O'Connell, MH Hapoel Be'er Sheva). Pencocokan persis gak pernah
    ketemu token -> resolved=False SELAMANYA -> hasil menang/kalahnya gak kecatat.
    Catatan: 108 baris lain di riwayat MASIH punya apostrof, jadi ini bukan kita
    yang ngebuang — sumbernya sendiri yang gak konsisten per market.

    Spasi juga dibuang, bukan diganti spasi: "OHiggins FC" dan "O'Higgins FC"
    harus ketemu, dan itu cuma jalan kalau pemisahnya ilang total. Aksen
    di-flatten juga (Beşiktaş -> besiktas).
    """
    s = unicodedata.normalize("NFKD", str(s or "")).lower()
    s = "".join(c for c in s if not unicodedata.combining(c))
    return _BUKAN_ALNUM.sub("", s)


def cari_token(market_info, outcome_text):
    """Token CLOB yang outcome-nya cocok. None kalau gak ada / ambigu.

    Dua tahap: cocok PERSIS dulu (jalur normal, gak berubah), baru cadangan
    tanpa tanda baca. Cadangan cuma dipakai kalau hasilnya TEPAT SATU token —
    kalau dua outcome beda nyusut jadi string yang sama ("Over 2.5" vs "Over 25")
    mendingan nyerah daripada salah pilih sisi. Dipakai bareng evaluate (nentuin
    menang/kalah) dan executor (milih token buat order), jadi salah pilih di sini
    = catat hasil kebalik, atau order ke sisi yang salah.
    """
    if not market_info or "tokens" not in market_info:
        return None
    tokens = market_info.get("tokens") or []
    target = str(outcome_text or "").strip().lower()
    for t in tokens:
        if str(t.get("outcome") or "").strip().lower() == target:
            return t

    kunci = _normal(outcome_text)
    if not kunci:
        return None
    cocok = [t for t in tokens if _normal(t.get("outcome")) == kunci]
    if len(cocok) == 1:
        return cocok[0]
    return None


def cek_status(condition_id, outcome_text):
    """
    Balikin dict: {resolved, menang(bool|None), harga_sekarang, error?}.
    resolved=False -> market masih jalan. resolved=True -> menang True/False final.
    """
    info = api.clob_market(condition_id)
    if not info:
        return {"resolved": False, "menang": None, "harga_sekarang": None, "error": "gagal fetch"}
    token = cari_token(info, outcome_text)
    if not token:
        return {"resolved": False, "menang": None, "harga_sekarang": None, "error": "outcome gak ketemu"}
    if not info.get("closed", False):
        return {"resolved": False, "menang": None, "harga_sekarang": token.get("price")}
    return {"resolved": True, "menang": bool(token.get("winner", False)),
            "harga_sekarang": token.get("price")}
