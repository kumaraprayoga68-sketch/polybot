"""
Kontrol polybot dari Telegram — ketik command di chat, bot jalanin & balas hasil.

Pakai long-polling (getUpdates) — TIDAK butuh URL publik / webhook. Cukup jalan:
    python -m polybot telegram

Command:
    /status      status config + gate keamanan
    /scan        discovery market by kategori
    /arb         scan peluang arbitrage
    /copy        copy-trade 1 pass
    /evaluate    cek win/loss market resolve
    /hunt        siklus penuh (scan + arb + copy + evaluate)
    /loop [menit] mulai loop otomatis (default 30 menit) — nyari peluang terus
    /stop        hentikan loop
    /ping        cek bot hidup
    /help        daftar command

KEAMANAN: cuma merespons pesan dari TELEGRAM_CHAT_ID yang dikonfigurasi. Chat lain
diabaikan total. Live trading tetap TIDAK bisa dari sini (butuh gate lokal + wallet).
"""
import io
import html
import time
import queue
import threading
from contextlib import redirect_stdout

import requests

from .. import config

_API = None
_CHAT = None
_job_q = queue.Queue()
_loop_stop = None
_loop_thread = None
_live_pending = 0  # timestamp saat /live diminta (butuh /live confirm dalam 60 detik)


def _api(method):
    return f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}"


def send(text, chat_id=None):
    """Kirim pesan (HTML). Trim ke batas Telegram (~4096)."""
    chat = chat_id or _CHAT
    if not chat:
        return
    if len(text) > 4000:
        text = text[:4000] + "\n… (dipotong)"
    try:
        requests.post(_api("sendMessage"),
                      json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=10)
    except Exception:
        pass


def _pre(s):
    s = (s or "").strip() or "selesai."
    if len(s) > 3400:
        s = s[-3400:]
    return "<pre>" + html.escape(s) + "</pre>"


# ── job runner: semua command jalan sekuensial di 1 worker (hindari tabrakan API) ──
def _job_worker():
    while True:
        name, fn = _job_q.get()
        try:
            send(f"▶️ menjalankan <b>/{name}</b>…")
            buf = io.StringIO()
            with redirect_stdout(buf):
                fn()
            send(f"✅ <b>/{name}</b> selesai:\n{_pre(buf.getvalue())}")
        except Exception as e:
            send(f"❌ <b>/{name}</b> error: {html.escape(str(e))}")
        finally:
            _job_q.task_done()


def _enqueue(name, fn):
    qsize = _job_q.qsize()
    _job_q.put((name, fn))
    if qsize:
        send(f"⏳ <b>/{name}</b> masuk antrian (posisi {qsize + 1}).")


# ── strategi (lazy import biar startup cepat) ──
def _run_scan():
    from ..strategies import scanner
    scanner.run()


def _run_arb():
    from ..strategies import arbitrage
    arbitrage.run(execute=False)


def _run_copy():
    from ..strategies import copytrade
    copytrade.run(loop=False)


def _run_evaluate():
    from . import evaluate
    evaluate.run()


def _run_hunt():
    _run_arb()
    _run_scan()
    _run_copy()
    _run_evaluate()


def _status_text():
    live = (not config.Common.SIMULASI_MODE) and config.LIVE_TRADING_ENABLED
    from ..config import CopyTrade, Arbitrage, Scanner
    loop_on = _loop_thread is not None and _loop_thread.is_alive()
    return (
        f"<b>polybot status</b>\n"
        f"Mode: {'🔴 LIVE' if live else '🟢 PAPER (aman)'}\n"
        f"Loop otomatis: {'🔁 ON' if loop_on else '⏹️ OFF'}\n"
        f"Antrian job: {_job_q.qsize()}\n"
        f"Strategi: copy={CopyTrade.ENABLED} arb={Arbitrage.ENABLED} scan={Scanner.ENABLED}\n"
        f"Hard cap/order: ${config.MAX_ORDER_SIZE_ABSOLUTE} · budget ${config.Common.BUDGET}\n"
        f"Dashboard: {'ON' if config.POLYBOT_DASHBOARD_URL else 'OFF'}"
    )


HELP = (
    "<b>polybot — kontrol Telegram</b>\n"
    "/status — status bot\n"
    "/scan — discovery market\n"
    "/arb — scan arbitrage\n"
    "/copy — copy-trade 1 pass\n"
    "/evaluate — cek win/loss\n"
    "/hunt — siklus penuh sekali\n"
    "/loop [menit] — nyari peluang terus (default 30m)\n"
    "/stop — hentikan loop\n"
    "/mode — cek paper/live\n"
    "/live — aktifin trading beneran (2 langkah konfirmasi)\n"
    "/paper — balik ke simulasi (aman)\n"
    "/window [1d|7d|30d|all] — window leaderboard copy-trade\n"
    "/agresif [on|off] — paper: ikut bet banyak (anti skip mulu)\n"
    "/ping — cek bot hidup"
)


def _start_loop(minutes):
    global _loop_stop, _loop_thread
    if _loop_thread is not None and _loop_thread.is_alive():
        send("🔁 Loop sudah jalan. Kirim /stop dulu kalau mau restart.")
        return
    _loop_stop = threading.Event()

    def worker():
        send(f"🔁 <b>Loop ON</b> — nyari peluang tiap {minutes} menit. /stop buat berhenti.")
        n = 0
        while not _loop_stop.is_set():
            n += 1
            send(f"🔎 cycle #{n}…")
            _enqueue(f"hunt#{n}", _run_hunt)
            _loop_stop.wait(minutes * 60)
        send("🛑 <b>Loop OFF</b>.")

    _loop_thread = threading.Thread(target=worker, daemon=True)
    _loop_thread.start()


def _stop_loop():
    global _loop_stop
    if _loop_stop is not None and not _loop_stop.is_set():
        _loop_stop.set()
        send("🛑 Menghentikan loop… (job yang lagi jalan akan diselesaikan dulu)")
    else:
        send("Loop memang tidak sedang jalan.")


def _mode_text():
    live = (not config.Common.SIMULASI_MODE) and config.LIVE_TRADING_ENABLED
    return ("🔴 <b>LIVE</b> — order beneran AKTIF" if live
            else "🟢 <b>PAPER</b> — simulasi (aman)")


def _cmd_live(arg):
    """Aktifin live trading — 2 langkah: /live lalu /live confirm (dalam 60 detik)."""
    global _live_pending
    # wallet wajib ada, kalau nggak live gak mungkin jalan
    if not (config.PRIVATE_KEY and config.FUNDER_ADDRESS):
        send("❌ Wallet belum diset. Isi <code>PRIVATE_KEY</code> &amp; "
             "<code>FUNDER_ADDRESS</code> di <code>.env</code> dulu — live gak bisa diaktifin.")
        return
    if arg == "confirm":
        if time.time() - _live_pending > 60:
            send("⌛ Konfirmasi kadaluarsa. Kirim /live lagi.")
            return
        config.Common.SIMULASI_MODE = False
        config.LIVE_TRADING_ENABLED = True
        _live_pending = 0
        send(f"🔴 <b>LIVE MODE ON</b>. Order BENERAN akan dikirim.\n"
             f"Hard cap: <b>${config.MAX_ORDER_SIZE_ABSOLUTE}/order</b> (gak bisa diubah dari sini).\n"
             f"Kirim /paper buat balik ke simulasi.\n"
             f"⚠️ restart bot = otomatis balik PAPER.")
    else:
        _live_pending = time.time()
        send(f"⚠️ <b>Aktifin LIVE trading?</b> Duit BENERAN bakal kepake.\n"
             f"Hard cap: ${config.MAX_ORDER_SIZE_ABSOLUTE}/order · budget ${config.Common.BUDGET}\n\n"
             f"Balas <b>/live confirm</b> dalam 60 detik buat lanjut, atau abaikan buat batal.")


def _cmd_paper():
    global _live_pending
    _live_pending = 0
    config.Common.SIMULASI_MODE = True
    config.LIVE_TRADING_ENABLED = False
    send("🟢 <b>PAPER MODE</b> (simulasi). Aman — gak ada order beneran yang dikirim.")


_WINDOW_VALID = ("1d", "7d", "30d", "all")


def _cmd_window(arg):
    """Ganti window leaderboard buat auto-pilih trader copy-trade."""
    from ..config import CopyTrade
    if not arg:
        send(f"Window leaderboard sekarang: <b>{CopyTrade.LEADERBOARD_WINDOW}</b>\n"
             f"Ganti: /window &lt;{' | '.join(_WINDOW_VALID)}&gt;")
        return
    w = arg.lower().strip()
    if w not in _WINDOW_VALID:
        send(f"❌ Window gak valid: <code>{html.escape(w)}</code>. "
             f"Pilih: {', '.join(_WINDOW_VALID)}")
        return
    CopyTrade.LEADERBOARD_WINDOW = w
    send(f"✅ Window leaderboard di-set ke <b>{w}</b>. "
         f"Berlaku di /copy &amp; /hunt berikutnya.\n"
         f"⚠️ restart bot balik ke default (.env LEADERBOARD_WINDOW).")


def _cmd_agresif(arg):
    """Toggle mode agresif copy-trade (paper only) — biar bot ikut bet, bukan skip mulu."""
    from ..config import CopyTrade
    if arg not in ("on", "off"):
        status = "ON 🔥" if CopyTrade.AGGRESSIVE else "OFF"
        aktif = "aktif" if (CopyTrade.AGGRESSIVE and config.Common.SIMULASI_MODE) else "gak aktif (butuh PAPER)"
        send(f"Mode agresif copy-trade: <b>{status}</b> ({aktif})\n"
             f"Aktifin: /agresif on · Matiin: /agresif off\n"
             f"<i>Cuma jalan di paper. Di live otomatis balik selektif.</i>")
        return
    CopyTrade.AGGRESSIVE = (arg == "on")
    if arg == "on":
        send("🔥 <b>Mode agresif ON</b> (paper) — bot bakal IKUT bet lebih banyak "
             "biar ngumpulin data, bukan skip mulu.\n"
             "<i>Otomatis nonaktif kalau mode live.</i>")
    else:
        send("✅ <b>Mode agresif OFF</b> — balik ke logika selektif (skor + Kelly + consensus).")


def _handle(text):
    parts = text.strip().split()
    cmd = parts[0].lower().lstrip("/").split("@")[0]  # /cmd@botname -> cmd
    arg = parts[1] if len(parts) > 1 else None

    if cmd in ("start", "help"):
        send(HELP)
    elif cmd == "status":
        send(_status_text())
    elif cmd == "ping":
        send("🏓 pong — bot hidup.")
    elif cmd == "scan":
        _enqueue("scan", _run_scan)
    elif cmd in ("arb", "arbitrage"):
        _enqueue("arb", _run_arb)
    elif cmd in ("copy", "copytrade"):
        _enqueue("copy", _run_copy)
    elif cmd == "evaluate":
        _enqueue("evaluate", _run_evaluate)
    elif cmd == "hunt":
        _enqueue("hunt", _run_hunt)
    elif cmd == "loop":
        try:
            minutes = max(5, int(arg)) if arg else 30
        except ValueError:
            minutes = 30
        _start_loop(minutes)
    elif cmd == "stop":
        _stop_loop()
    elif cmd == "mode":
        send("Mode sekarang: " + _mode_text())
    elif cmd == "live":
        _cmd_live(arg)
    elif cmd == "paper":
        _cmd_paper()
    elif cmd == "window":
        _cmd_window(arg)
    elif cmd in ("agresif", "aggressive"):
        _cmd_agresif(arg)
    else:
        send(f"❓ Command tidak dikenal: /{html.escape(cmd)}\n{HELP}")


def _register_commands():
    cmds = [
        {"command": "status", "description": "status bot"},
        {"command": "scan", "description": "discovery market"},
        {"command": "arb", "description": "scan arbitrage"},
        {"command": "copy", "description": "copy-trade 1 pass"},
        {"command": "evaluate", "description": "cek win/loss"},
        {"command": "hunt", "description": "siklus penuh sekali"},
        {"command": "loop", "description": "nyari peluang terus (menit)"},
        {"command": "stop", "description": "hentikan loop"},
        {"command": "mode", "description": "cek paper/live"},
        {"command": "live", "description": "aktifin trading beneran"},
        {"command": "paper", "description": "balik ke simulasi (aman)"},
        {"command": "window", "description": "window leaderboard (1d/7d/30d/all)"},
        {"command": "agresif", "description": "paper: ikut bet banyak (on/off)"},
        {"command": "ping", "description": "cek bot hidup"},
        {"command": "help", "description": "daftar command"},
    ]
    try:
        requests.post(_api("setMyCommands"), json={"commands": cmds}, timeout=10)
    except Exception:
        pass


def run():
    """Mulai listener long-polling. Blocking; Ctrl+C buat stop."""
    global _CHAT
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("❌ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum diset di .env.")
        return
    _CHAT = str(config.TELEGRAM_CHAT_ID)

    # verifikasi token
    try:
        me = requests.get(_api("getMe"), timeout=10).json()
        bot_name = me.get("result", {}).get("username", "?")
    except Exception as e:
        print(f"❌ Gagal konek Telegram: {e}")
        return

    threading.Thread(target=_job_worker, daemon=True).start()
    _register_commands()

    # skip pesan lama biar command basi gak ke-eksekusi ulang
    offset = None
    try:
        old = requests.get(_api("getUpdates"), params={"timeout": 0, "offset": -1}, timeout=15).json()
        res = old.get("result", [])
        if res:
            offset = res[-1]["update_id"] + 1
    except Exception:
        pass

    print(f"🤖 Telegram control aktif sebagai @{bot_name}. Kirim /help di chat kamu.")
    send("🤖 <b>polybot control ONLINE</b>\nKirim /help buat lihat command.")

    while True:
        try:
            r = requests.get(_api("getUpdates"),
                             params={"timeout": 25, "offset": offset}, timeout=35).json()
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message") or u.get("edited_message") or {}
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "")
                if not text:
                    continue
                if chat_id != _CHAT:
                    continue  # abaikan chat lain (keamanan)
                _handle(text)
        except KeyboardInterrupt:
            print("\n👋 stop.")
            send("👋 polybot control OFFLINE.")
            return
        except Exception:
            time.sleep(3)  # network hiccup — coba lagi
