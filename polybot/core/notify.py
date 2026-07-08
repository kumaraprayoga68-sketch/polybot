"""
Alert Telegram terpadu — dipakai semua strategi buat notif sinyal/peluang/eksekusi.
Fail-safe total: kalau token gak diset atau gagal kirim, cuma di-skip, gak pernah
nge-block pipeline bot.
"""
import requests
from .. import config


def kirim(text):
    """Kirim pesan ke Telegram (Markdown). Balikin True kalau sukses."""
    tk, chat = config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID
    if not tk or not chat:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{tk}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "Markdown",
                  "disable_web_page_preview": True},
            timeout=8,
        )
        return r.ok
    except Exception:
        return False


def alert_sinyal(judul, baris):
    """Format standar: judul tebal + daftar baris."""
    body = "\n".join(baris) if isinstance(baris, (list, tuple)) else str(baris)
    return kirim(f"*{judul}*\n{body}")
