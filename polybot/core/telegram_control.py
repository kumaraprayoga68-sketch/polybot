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
    # scorecard resmi (baca sumber yang sama dengan dashboard) — bukan file lokal mesin ini
    from . import evaluate
    evaluate.scorecard()


def _run_hunt():
    _run_arb()
    _run_scan()
    _run_copy()
    _run_evaluate()


def _run_kapan():
    """Daftar tanggal resolve tiap taruhan IKUT yang belum resolve, urut paling dekat.
    Pakai end_date dari riwayat; kalau kosong (taruhan lama) fetch dari CLOB."""
    from datetime import datetime, timezone
    from . import tracker
    rows = tracker.baca_semua()
    seen, items = set(), []
    for r in rows:
        if r.get("aksi") != "ikut" or r.get("resolved"):
            continue
        cid, outcome = r.get("condition_id", ""), r.get("outcome", "")
        if (cid, outcome) in seen:
            continue
        seen.add((cid, outcome))
        end = (r.get("end_date") or "").strip()
        if not end and cid:
            m = api.clob_market(cid)
            if m:
                end = m.get("end_date_iso") or ""
        items.append((end, r.get("market", ""), outcome))

    if not items:
        print("Belum ada taruhan IKUT aktif yang perlu dicek.")
        return

    items.sort(key=lambda it: it[0] or "9999-12-31")
    today = datetime.now(timezone.utc).date()
    print(f"📅 Resolve {len(items)} taruhan aktif (urut paling dekat):\n")
    for end, market, outcome in items:
        if not end:
            print(f"• (tanggal ?) — {market[:42]} '{outcome}'")
            continue
        tgl = str(end)[:10]
        try:
            sisa = (datetime.strptime(tgl, "%Y-%m-%d").date() - today).days
            sisa_txt = ("hari ini" if sisa == 0 else
                        f"{sisa} hari lagi" if sisa > 0 else f"lewat {abs(sisa)} hari")
        except ValueError:
            sisa_txt = "?"
        print(f"• {tgl} ({sisa_txt}) — {market[:42]} '{outcome}'")


def _status_text():
    from ..config import CopyTrade, Arbitrage, Scanner
    live = (not config.Common.SIMULASI_MODE) and config.LIVE_TRADING_ENABLED
    loop_on = _loop_thread is not None and _loop_thread.is_alive()
    agresif = CopyTrade.AGGRESSIVE and config.Common.SIMULASI_MODE
    flat = round(config.Common.MAX_PER_TRADE * CopyTrade.FLAT_FRAC, 2)
    kelly_txt = "ON (berbasis edge)" if CopyTrade.KELLY_ENABLED else f"OFF · flat ${flat}"
    return (
        f"<b>📟 polybot — status</b>\n"
        f"Mode: {'🔴 LIVE' if live else '🟢 PAPER (aman)'}\n"
        f"Loop otomatis: {'🔁 ON' if loop_on else '⏹️ OFF'}\n"
        f"Antrian job: {_job_q.qsize()}\n"
        f"\n"
        f"<b>⚙️ Tuning copy-trade</b>\n"
        f"• Agresif: {'🔥 ON' if agresif else 'OFF'}   <i>/agresif</i>\n"
        f"• Kelly: {kelly_txt}   <i>/kelly</i>\n"
        f"• Resolve: ≤ {CopyTrade.MAX_HARI_KE_RESOLVE} hari   <i>/resolve</i>\n"
        f"• Max harga: {'OFF' if CopyTrade.MAX_ENTRY_PRICE>=1 else '≤ $'+str(CopyTrade.MAX_ENTRY_PRICE)}   <i>/maxprice</i>\n"
        f"• Leaderboard: {CopyTrade.LEADERBOARD_WINDOW}   <i>/window</i>\n"
        f"• Skor minimal: {CopyTrade.SKOR_THRESHOLD}\n"
        f"• Size/bet: maks ${config.Common.MAX_PER_TRADE}\n"
        f"\n"
        f"<b>🧩 Strategi</b>: copy={CopyTrade.ENABLED} · arb={Arbitrage.ENABLED} · scan={Scanner.ENABLED}\n"
        f"<b>🔒 Hard cap/order</b>: {'OFF' if config.MAX_ORDER_SIZE_ABSOLUTE>=1e11 else '$'+str(config.MAX_ORDER_SIZE_ABSOLUTE)} · budget ${config.Common.BUDGET}   <i>/cap</i>\n"
        f"<b>📊 Dashboard</b>: {'ON' if config.POLYBOT_DASHBOARD_URL else 'OFF'}"
    )


HELP = (
    "<b>polybot — kontrol Telegram</b>\n"
    "/status — status bot\n"
    "/scan — discovery market\n"
    "/arb — scan arbitrage\n"
    "/copy — copy-trade 1 pass\n"
    "/evaluate — cek win/loss\n"
    "/kapan — tanggal resolve tiap taruhan aktif\n"
    "/hunt — siklus penuh sekali\n"
    "/loop [menit] — nyari peluang terus (default 30m)\n"
    "/stop — hentikan loop\n"
    "/mode — cek paper/live\n"
    "/live — aktifin trading beneran (2 langkah konfirmasi)\n"
    "/paper — balik ke simulasi (aman)\n"
    "/window [1d|7d|30d|all] — window leaderboard copy-trade\n"
    "/agresif [on|off] — paper: ikut bet banyak (anti skip mulu)\n"
    "/kelly [on|off] — sizing Kelly (off = flat, gak penakut)\n"
    "/resolve [hari] — cuma copy market resolve ≤ N hari (feedback cepet)\n"
    "/maxprice [n|off] — skip favorit (harga > n) biar gak untung recehan\n"
    "/cap [n|off] — hard cap ukuran order (rem live)\n"
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


def _cmd_resolve(arg):
    """Set max hari ke resolve — bot cuma copy market yang resolve <= N hari (feedback cepet)."""
    from ..config import CopyTrade
    if not arg:
        send(f"Max hari ke resolve: <b>{CopyTrade.MAX_HARI_KE_RESOLVE} hari</b>\n"
             f"Bot cuma ikut market yang resolve ≤ segini (biar hasil W/L cepet keliatan).\n"
             f"Ganti: /resolve 14")
        return
    try:
        n = max(1, int(arg))
    except ValueError:
        send("❌ Isi angka hari, mis. /resolve 14")
        return
    CopyTrade.MAX_HARI_KE_RESOLVE = n
    send(f"✅ Max resolve di-set ke <b>{n} hari</b>. Bot bakal skip market yang "
         f"resolve-nya lebih jauh dari itu (fokus jangka pendek).")


def _cmd_maxprice(arg):
    """Set batas harga entry — skip bet favorit (harga kemahalan = untung recehan)."""
    from ..config import CopyTrade
    if not arg:
        cur = CopyTrade.MAX_ENTRY_PRICE
        txt = "OFF (bet semua harga)" if cur >= 1 else f"≤ ${cur} (skip favorit di atas ini)"
        send(f"Max harga entry: <b>{txt}</b>\n"
             f"Set: /maxprice 0.8 · Matiin: /maxprice off\n"
             f"<i>Makin rendah = makin cuma bet market 'value', skip favorit recehan.</i>")
        return
    if arg == "off":
        CopyTrade.MAX_ENTRY_PRICE = 1.0
        send("⚠️ Filter harga OFF — bot bet semua harga (termasuk favorit recehan).")
        return
    try:
        v = float(arg)
    except ValueError:
        send("Isi angka 0-1, mis. /maxprice 0.8  atau  /maxprice off")
        return
    CopyTrade.MAX_ENTRY_PRICE = max(0.05, min(1.0, v))
    send(f"✅ Max harga entry di-set <b>≤ ${CopyTrade.MAX_ENTRY_PRICE}</b>.\n"
         f"Bot bakal skip bet dengan harga di atas itu (favorit) — fokus market value.")


def _cmd_cap(arg):
    """Set / matiin hard cap ukuran order (rem terakhir di live)."""
    cur = config.MAX_ORDER_SIZE_ABSOLUTE
    if not arg:
        txt = "❌ OFF (gak ada batas)" if cur >= 1e11 else f"${round(cur,2)}/order"
        send(f"Hard cap: <b>{txt}</b>\n"
             f"Set: /cap 5 · Matiin: /cap off · Nyalain: /cap 1\n"
             f"<i>(order juga selalu dibatasi sizing, maks ${config.Common.MAX_PER_TRADE}/bet)</i>")
        return
    if arg == "off":
        config.MAX_ORDER_SIZE_ABSOLUTE = 1e12
        send("⚠️ <b>Hard cap OFF</b> — order gak dibatasi cap lagi.\n"
             f"<i>Masih dibatasi sizing (maks ${config.Common.MAX_PER_TRADE}/bet), tapi di LIVE "
             f"ini ngilangin rem terakhir. Restart bot = balik ke default.</i>")
        return
    try:
        v = float(arg)
    except ValueError:
        send("Isi angka, mis. /cap 5  atau  /cap off")
        return
    config.MAX_ORDER_SIZE_ABSOLUTE = max(0.01, v)
    send(f"✅ Hard cap di-set <b>${v}/order</b>.")


def _cmd_kelly(arg):
    """Toggle Kelly sizing. Off = flat sizing (gak diciutin — Kelly kadang 'terlalu takut')."""
    from ..config import CopyTrade
    if arg not in ("on", "off"):
        status = "ON (berbasis edge)" if CopyTrade.KELLY_ENABLED else f"OFF (flat {CopyTrade.FLAT_FRAC}×)"
        send(f"Kelly sizing: <b>{status}</b>\n"
             f"On: /kelly on · Off (flat): /kelly off\n"
             f"<i>Off = bet rata {CopyTrade.FLAT_FRAC}× MAX_PER_TRADE (${config.Common.MAX_PER_TRADE}), "
             f"gak diciutin walau harga mahal.</i>")
        return
    CopyTrade.KELLY_ENABLED = (arg == "on")
    if arg == "on":
        send("✅ <b>Kelly ON</b> — size berbasis edge (ciut/skip kalau harga gak ngasih edge).")
    else:
        flat = round(config.Common.MAX_PER_TRADE * CopyTrade.FLAT_FRAC, 2)
        send(f"🎯 <b>Kelly OFF</b> — flat sizing <b>${flat}</b>/bet (gak diciutin).\n"
             f"<i>Catatan: tanpa Kelly, proteksi 'jangan bet kalau gak ada edge' hilang. "
             f"Buat paper aman; di live lebih beresiko.</i>")


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
    elif cmd == "kapan":
        _enqueue("kapan", _run_kapan)
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
    elif cmd == "kelly":
        _cmd_kelly(arg)
    elif cmd == "cap":
        _cmd_cap(arg)
    elif cmd in ("maxprice", "harga"):
        _cmd_maxprice(arg)
    elif cmd == "resolve":
        _cmd_resolve(arg)
    else:
        send(f"❓ Command tidak dikenal: /{html.escape(cmd)}\n{HELP}")


def _register_commands():
    cmds = [
        {"command": "status", "description": "status bot"},
        {"command": "scan", "description": "discovery market"},
        {"command": "arb", "description": "scan arbitrage"},
        {"command": "copy", "description": "copy-trade 1 pass"},
        {"command": "evaluate", "description": "cek win/loss"},
        {"command": "kapan", "description": "tanggal resolve taruhan aktif"},
        {"command": "hunt", "description": "siklus penuh sekali"},
        {"command": "loop", "description": "nyari peluang terus (menit)"},
        {"command": "stop", "description": "hentikan loop"},
        {"command": "mode", "description": "cek paper/live"},
        {"command": "live", "description": "aktifin trading beneran"},
        {"command": "paper", "description": "balik ke simulasi (aman)"},
        {"command": "window", "description": "window leaderboard (1d/7d/30d/all)"},
        {"command": "agresif", "description": "paper: ikut bet banyak (on/off)"},
        {"command": "kelly", "description": "sizing Kelly on/off (off=flat)"},
        {"command": "resolve", "description": "copy market resolve <= N hari"},
        {"command": "maxprice", "description": "skip favorit (harga > n)"},
        {"command": "cap", "description": "hard cap order (n / off)"},
        {"command": "ping", "description": "cek bot hidup"},
        {"command": "help", "description": "daftar command"},
    ]
    try:
        requests.post(_api("setMyCommands"), json={"commands": cmds}, timeout=10)
    except Exception:
        pass


def _start_health_server():
    """
    Kalau env PORT diset (mis. Render Web Service), nyalain HTTP health endpoint
    di thread terpisah. Buat Render Background Worker, PORT gak ada -> di-skip.
    """
    import os
    port = os.environ.get("PORT")
    if not port:
        return
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"polybot telegram control OK")

        def log_message(self, *a):
            pass  # jangan spam log

    def serve():
        try:
            HTTPServer(("0.0.0.0", int(port)), H).serve_forever()
        except Exception as e:
            print(f"health server error: {e}")

    threading.Thread(target=serve, daemon=True).start()
    print(f"🩺 health server di port {port}")


def run():
    """Mulai listener long-polling. Blocking; Ctrl+C buat stop."""
    global _CHAT
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("❌ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum diset di .env.")
        return
    _CHAT = str(config.TELEGRAM_CHAT_ID)
    _start_health_server()

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

    # AUTO-LOOP: kalau di-set, mulai loop otomatis pas boot biar nyambung terus
    # walau listener restart (mis. di GitHub Actions ~tiap 5,5 jam). Tanpa ini,
    # loop selalu balik OFF tiap restart karena state-nya cuma di memori proses.
    if config.AUTO_LOOP_MIN and config.AUTO_LOOP_MIN > 0:
        _start_loop(max(5, config.AUTO_LOOP_MIN))

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
