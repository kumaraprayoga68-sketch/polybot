"""
Alert Telegram terpadu — dipakai semua strategi buat notif sinyal/peluang/eksekusi.
Fail-safe total: kalau token gak diset atau gagal kirim, cuma di-skip, gak pernah
nge-block pipeline bot.

REM LAJU (2026-11-03). Telegram cuma nerima ~1 pesan/detik per chat. Bot ini dulu
nembak 1 pesan PER BET — 25 bet dalam satu siklus (kejadian: 25 bet di menit yang
sama) = 25 pesan beruntun dalam hitungan detik. Telegram balas 429 Too Many
Requests, pesannya dibuang, dan selama jeda hukuman BALASAN COMMAND kamu juga
ikut ditolak — makanya botnya kelihatan lemot/mati. Dua-duanya senyap karena
status HTTP-nya gak pernah dicek (`r.ok` dibalikin tapi gak ada yang baca).

Sekarang semua pesan lewat satu gerbang: jaga jarak antar-kirim, nurutin
`retry_after` kalau tetep kena 429, dan NGELUH ke log kalau gagal.
"""
import threading
import time

import requests

from .. import config

_JEDA = 1.2             # detik — jarak minimum antar pesan ke chat yang sama
_lock = threading.Lock()  # serialisasi: pacer gak ada gunanya kalau thread paralel
_terakhir = 0.0         # monotonic() saat kirim terakhir


def kirim(text, parse_mode="Markdown", chat_id=None):
    """Kirim pesan ke Telegram. Balikin True kalau sukses.

    Nahan diri biar gak kena 429, dan kalau tetep kena: nunggu sesuai
    `retry_after` lalu coba SEKALI lagi.
    """
    global _terakhir
    tk = config.TELEGRAM_BOT_TOKEN
    chat = chat_id or config.TELEGRAM_CHAT_ID
    if not tk or not chat:
        return False
    url = f"https://api.telegram.org/bot{tk}/sendMessage"
    body = {"chat_id": chat, "text": text, "parse_mode": parse_mode,
            "disable_web_page_preview": True}
    with _lock:
        for coba in (1, 2):
            sisa = _JEDA - (time.monotonic() - _terakhir)
            if sisa > 0:
                time.sleep(sisa)
            try:
                r = requests.post(url, json=body, timeout=10)
            except Exception as e:
                print(f"  ⚠️  Telegram gagal kirim ({e})")
                return False
            finally:
                _terakhir = time.monotonic()
            if r.ok:
                return True
            if r.status_code == 429 and coba == 1:
                tunggu = 3
                try:
                    tunggu = int(r.json()["parameters"]["retry_after"])
                except Exception:
                    pass
                tunggu = min(max(tunggu, 1), 30)
                print(f"  ⏳ Telegram rate-limit — nunggu {tunggu}s lalu coba lagi")
                time.sleep(tunggu)
                continue
            # Jangan senyap: kalau ditolak, kelihatan di log Actions.
            print(f"  ⚠️  Telegram nolak ({r.status_code}): {r.text[:150]}")
            return False
    return False


def alert_sinyal(judul, baris):
    """Format standar: judul tebal + daftar baris."""
    body = "\n".join(baris) if isinstance(baris, (list, tuple)) else str(baris)
    return kirim(f"*{judul}*\n{body}")
