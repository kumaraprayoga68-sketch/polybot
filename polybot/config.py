"""
Konfigurasi terpusat untuk polybot — hasil unifikasi 5 bot Polymarket
(multi-trader-bot, polymarket-bot, poly-ai, market-scanner-bot, 9router).

Semua tuning parameter dari bot-bot lama dikumpulkan di sini, dikelompokkan per
strategi. Kredensial dibaca dari environment (.env) — TIDAK pernah di-hardcode.

⚠️ Ini eksperimen pribadi, BUKAN financial advice. Trading prediction market ada
resiko rugi total. Default SEMUA strategi = paper trading (simulasi).
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _env_bool(name, default=False):
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# ── ENDPOINT PUBLIK (tanpa auth) ──────────────────────────────────────────────
GAMMA_URL       = "https://gamma-api.polymarket.com/markets"
CLOB_HOST       = "https://clob.polymarket.com"
DATA_API        = "https://data-api.polymarket.com"
LEADERBOARD_URL = "https://lb-api.polymarket.com/profit"

# ── KREDENSIAL (dari .env, cuma perlu buat LIVE trading) ───────────────────────
PRIVATE_KEY    = os.getenv("PRIVATE_KEY", "")
FUNDER_ADDRESS = os.getenv("FUNDER_ADDRESS", "")
CHAIN_ID       = 137            # Polygon mainnet
SIGNATURE_TYPE = 1              # proxy wallet

# Narasi opsional (AI) — kalau kosong, narasi di-skip, keputusan tetap deterministik.
GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")

# Alert Telegram (opsional) — dipakai semua strategi buat notif sinyal/peluang.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# Dashboard Vercel (opsional) — tiap event tercatat juga di-push ke sini buat
# dipantau live dari web. Kosongkan buat matiin (tetap ada CSV lokal + Telegram).
POLYBOT_DASHBOARD_URL = os.getenv("POLYBOT_DASHBOARD_URL", "")
POLYBOT_TOKEN         = os.getenv("POLYBOT_TOKEN", "")


class Common:
    """Setting global lintas strategi."""
    SIMULASI_MODE = _env_bool("SIMULASI_MODE", True)   # True = paper trading (default aman)
    BUDGET        = _env_float("BUDGET", 100.0)         # modal (simulasi) total
    MAX_PER_TRADE = _env_float("MAX_PER_TRADE", 5.0)    # size maksimal per posisi
    DATA_DIR      = os.getenv("POLYBOT_DATA_DIR", "data")


class CopyTrade:
    """Copy-trading consensus (dari multi-trader-bot + polymarket-bot)."""
    ENABLED             = _env_bool("COPYTRADE_ENABLED", True)
    SINGLE_TRADER_MODE  = _env_bool("SINGLE_TRADER_MODE", False)  # True=copy 1 trader terbaik
    AUTO_PILIH_TRADER   = _env_bool("AUTO_PILIH_TRADER", True)    # scan leaderboard otomatis
    DAFTAR_TRADER_MANUAL = [w for w in os.getenv("TRADER_WALLETS", "").split(",") if w.strip()]
    CHECK_INTERVAL      = int(_env_float("COPYTRADE_INTERVAL", 60))    # detik antar cek posisi
    SKOR_THRESHOLD      = _env_float("SKOR_THRESHOLD", 5.0)            # skor >= ini -> IKUT
    MIN_WIN_RATE_PNL    = _env_float("MIN_WIN_RATE_PNL", 55.0)
    MIN_NET_PNL         = _env_float("MIN_NET_PNL", 0.0)
    MAX_HARI_KE_RESOLVE = int(_env_float("MAX_HARI_KE_RESOLVE", 90))
    TOP_N_TRADER        = int(_env_float("TOP_N_TRADER", 5))
    LEADERBOARD_WINDOW  = os.getenv("LEADERBOARD_WINDOW", "30d")  # 1d / 7d / 30d / all
    # --- MODE AGRESIF (khusus PAPER, buat ngumpulin data biar gak skip mulu) ---
    # Cuma aktif kalau SIMULASI_MODE=True. Di live, otomatis balik selektif (aman).
    AGGRESSIVE          = _env_bool("COPYTRADE_AGGRESSIVE", False)
    AGG_MIN_WIN_RATE    = _env_float("AGG_MIN_WIN_RATE", 40.0)   # screening dilonggarin
    AGG_FLAT_FRAC       = _env_float("AGG_FLAT_FRAC", 0.3)       # size fallback kalau Kelly=0
    AGG_MAX_BETS        = int(_env_float("AGG_MAX_BETS", 10))    # cap IKUT per siklus (anti-flood)


class Arbitrage:
    """YES+NO arbitrage scanner/executor (dari poly-ai + 9router)."""
    ENABLED           = _env_bool("ARB_ENABLED", True)
    MIN_EDGE_PCT      = _env_float("ARB_MIN_EDGE_PCT", 5.0)   # buffer fee (bukan presisi!)
    MIN_LIQUIDITY_USD = _env_float("ARB_MIN_LIQUIDITY", 500.0)
    SCAN_INTERVAL_MIN = _env_float("ARB_SCAN_INTERVAL_MIN", 5.0)
    MAX_MARKET        = int(_env_float("ARB_MAX_MARKET", 2000))
    PAGE_SIZE         = 500
    SCAN_WORKERS      = 6


class Scanner:
    """Market discovery by keyword category (dari market-scanner-bot)."""
    ENABLED           = _env_bool("SCANNER_ENABLED", True)
    MIN_VOLUME_USD    = _env_float("SCANNER_MIN_VOLUME", 10000.0)
    SCAN_INTERVAL_MIN = _env_float("SCANNER_INTERVAL_MIN", 15.0)
    CATEGORIES        = [c.strip() for c in os.getenv(
        "SCANNER_CATEGORIES", "sports,politics,crypto").split(",") if c.strip()]


# ── HARD SAFETY CAP (independen dari config manapun di atas) ───────────────────
# Cap absolut ukuran order live. Gak bisa dilewati parameter apapun.
MAX_ORDER_SIZE_ABSOLUTE = _env_float("MAX_ORDER_SIZE_ABSOLUTE", 5.0)

# Gate kedua buat live trading (selain SIMULASI_MODE=False). DUA-DUANYA harus aktif.
LIVE_TRADING_ENABLED = _env_bool("LIVE_TRADING_ENABLED", False)
