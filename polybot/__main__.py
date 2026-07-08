"""
polybot — orchestrator terpadu 5 bot Polymarket.

  python -m polybot status                 tampilkan config & gate keamanan
  python -m polybot scan                    discovery market by kategori (1x)
  python -m polybot arbitrage [--loop] [--execute]
  python -m polybot copytrade [--loop]
  python -m polybot all [--loop]            jalankan scanner+arbitrage+copytrade
  python -m polybot test-connection         cek auth CLOB (buat live trading)

Default SEMUA = paper trading (SIMULASI_MODE=True). Live butuh 3 gate (lihat status).
"""
import sys
import time
import argparse

# Windows console default cp1252 gak bisa render emoji/box char yang dipakai output.
# Paksa UTF-8 di entry point supaya semua print (termasuk dari modul strategi) aman.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from . import config
from .config import Common, CopyTrade, Arbitrage, Scanner


def cmd_status(_):
    live = (not Common.SIMULASI_MODE) and config.LIVE_TRADING_ENABLED
    print("╔══════════════════════════════════════════════╗")
    print("║  polybot — status                              ║")
    print("╚══════════════════════════════════════════════╝")
    print(f"  Mode trading   : {'🔴 LIVE' if live else '🟢 PAPER (aman)'}")
    print(f"  Gate 1 SIMULASI_MODE      : {Common.SIMULASI_MODE}  (False buat live)")
    print(f"  Gate 2 LIVE_TRADING_ENABLED: {config.LIVE_TRADING_ENABLED}  (True buat live)")
    print(f"  Hard cap / order          : ${config.MAX_ORDER_SIZE_ABSOLUTE}")
    print(f"  Budget / max per trade    : ${Common.BUDGET} / ${Common.MAX_PER_TRADE}")
    print(f"  Wallet auth (live)        : {'ada' if config.PRIVATE_KEY else 'kosong'}")
    print(f"  Telegram alert            : {'ON' if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID else 'OFF'}")
    print("  Strategi aktif:")
    print(f"    copytrade : {CopyTrade.ENABLED}  (mode "
          f"{'single' if CopyTrade.SINGLE_TRADER_MODE else 'consensus'}, "
          f"auto-pilih {CopyTrade.AUTO_PILIH_TRADER})")
    print(f"    arbitrage : {Arbitrage.ENABLED}  (min edge {Arbitrage.MIN_EDGE_PCT}%)")
    print(f"    scanner   : {Scanner.ENABLED}  (kategori {', '.join(Scanner.CATEGORIES)})")


def cmd_scan(_):
    from .strategies import scanner
    scanner.run()


def cmd_arbitrage(args):
    from .strategies import arbitrage
    _loop(lambda: arbitrage.run(execute=args.execute), args.loop,
          Arbitrage.SCAN_INTERVAL_MIN * 60)


def cmd_copytrade(args):
    from .strategies import copytrade
    copytrade.run(loop=args.loop)


def cmd_all(args):
    from .strategies import scanner, arbitrage, copytrade

    def satu_putaran():
        if Scanner.ENABLED:
            scanner.run()
        if Arbitrage.ENABLED:
            arbitrage.run(execute=False)
        if CopyTrade.ENABLED:
            copytrade.run(loop=False)

    _loop(satu_putaran, args.loop, max(Arbitrage.SCAN_INTERVAL_MIN,
                                       Scanner.SCAN_INTERVAL_MIN) * 60)


def cmd_evaluate(_):
    from .core import evaluate
    evaluate.run()


def cmd_test_connection(_):
    from .core import executor
    executor.cek_koneksi()


def _loop(fn, loop, interval_sec):
    while True:
        try:
            fn()
        except KeyboardInterrupt:
            print("\n👋 stop.")
            return
        except Exception as e:
            print(f"⚠️ error: {e}")
        if not loop:
            return
        print(f"\n⏳ tunggu {interval_sec/60:.0f} menit…\n")
        try:
            time.sleep(interval_sec)
        except KeyboardInterrupt:
            print("\n👋 stop.")
            return


def main():
    p = argparse.ArgumentParser(prog="polybot", description="Unified Polymarket bot (5-in-1).")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("scan").set_defaults(func=cmd_scan)
    sub.add_parser("evaluate").set_defaults(func=cmd_evaluate)
    sub.add_parser("test-connection").set_defaults(func=cmd_test_connection)

    pa = sub.add_parser("arbitrage")
    pa.add_argument("--loop", action="store_true")
    pa.add_argument("--execute", action="store_true", help="coba eksekusi (tetap kena gate)")
    pa.set_defaults(func=cmd_arbitrage)

    pc = sub.add_parser("copytrade")
    pc.add_argument("--loop", action="store_true")
    pc.set_defaults(func=cmd_copytrade)

    pall = sub.add_parser("all")
    pall.add_argument("--loop", action="store_true")
    pall.set_defaults(func=cmd_all)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 stop.")
        sys.exit(0)
