"""
Push event ke dashboard Vercel (polybot-dashboard). Fail-safe total: kalau URL/token
gak diset atau gagal kirim, cuma di-skip — gak pernah nge-block bot lokal.
Dipakai berdampingan dengan Telegram (notify.py): bot lokal kirim ke dua-duanya.
"""
import requests
from .. import config


def push_event(event):
    """POST 1 event ke dashboard. Balikin True kalau sukses. Non-blocking-ish (timeout pendek)."""
    url = config.POLYBOT_DASHBOARD_URL
    if not url:
        return False
    try:
        headers = {"Content-Type": "application/json"}
        if config.POLYBOT_TOKEN:
            headers["x-polybot-token"] = config.POLYBOT_TOKEN
        r = requests.post(url.rstrip("/") + "/api/event", json=event,
                          headers=headers, timeout=5)
        return r.ok
    except Exception:
        return False
