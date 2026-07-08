"""
Eksekusi order — SATU-SATUNYA modul yang KIRIM DUIT BENERAN ke Polymarket.

⚠️⚠️⚠️ TRIPLE SAFETY (sengaja ribet biar gak ke-trigger gak sengaja) ⚠️⚠️⚠️
Order live cuma kekirim kalau SEMUA ini terpenuhi:
  1. config.SIMULASI_MODE = False          (gate 1, di .env / config)
  2. config.LIVE_TRADING_ENABLED = True     (gate 2, terpisah)
  3. usd_amount <= config.MAX_ORDER_SIZE_ABSOLUTE   (hard cap absolut)
Kalau salah satu gagal -> DRY-RUN (cuma simulasi, gak ngirim apa-apa).

Pakai py-clob-client (SDK resmi Polymarket). Struktur divalidasi ke SDK v0.34.6.
JANGAN pernah share/commit PRIVATE_KEY. Pakai wallet KHUSUS bot, isi seperlunya.
"""
import time
from datetime import datetime

from .. import config

_client = None


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}")


def _get_client():
    """Init ClobClient sekali (lazy)."""
    global _client
    if _client is not None:
        return _client
    if not config.PRIVATE_KEY or not config.FUNDER_ADDRESS:
        raise RuntimeError(
            "PRIVATE_KEY / FUNDER_ADDRESS kosong. Isi .env dulu buat live trading "
            "(paper trading gak butuh ini)."
        )
    from py_clob_client.client import ClobClient
    client = ClobClient(
        config.CLOB_HOST,
        key=config.PRIVATE_KEY,
        chain_id=config.CHAIN_ID,
        signature_type=config.SIGNATURE_TYPE,
        funder=config.FUNDER_ADDRESS,
    )
    client.set_api_creds(client.create_or_derive_api_creds())
    _client = client
    log("🔑 ClobClient authenticated.")
    return _client


def _live_allowed(usd_amount):
    """Cek ketiga gate. Balikin (boleh_live: bool, alasan: str)."""
    if usd_amount > config.MAX_ORDER_SIZE_ABSOLUTE:
        return False, f"melebihi hard cap ${config.MAX_ORDER_SIZE_ABSOLUTE}"
    if config.Common.SIMULASI_MODE:
        return False, "SIMULASI_MODE=True"
    if not config.LIVE_TRADING_ENABLED:
        return False, "LIVE_TRADING_ENABLED=False"
    return True, ""


def token_id_for_outcome(condition_id, outcome_text):
    """Cari token_id buat order dari conditionId + nama outcome ('Yes'/'No')."""
    client = _get_client()
    market = client.get_market(condition_id)
    if not market or "tokens" not in market:
        return None
    for t in market["tokens"]:
        if t.get("outcome", "").strip().lower() == outcome_text.strip().lower():
            return t.get("token_id")
    return None


def place_market_buy(condition_id, outcome_text, usd_amount):
    """
    MARKET BUY (FOK). Balikin dict hasil — TIDAK pernah raise ke caller (biar loop
    bot gak crash gara-gara 1 order gagal). Otomatis dry-run kalau gate belum lolos.
    """
    if usd_amount > config.MAX_ORDER_SIZE_ABSOLUTE:
        log(f"⛔ ${usd_amount} > hard cap ${config.MAX_ORDER_SIZE_ABSOLUTE}. DIBATALIN.")
        return {"status": "blocked", "alasan": "melebihi hard cap"}

    boleh, alasan = _live_allowed(usd_amount)
    if not boleh:
        log(f"🟡 [DRY-RUN] BUY '{outcome_text}' ${usd_amount} @ {condition_id[:10]}… ({alasan})")
        return {"status": "dry_run", "condition_id": condition_id,
                "outcome": outcome_text, "usd_amount": usd_amount, "alasan": alasan}

    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY
        token_id = token_id_for_outcome(condition_id, outcome_text)
        if not token_id:
            return {"status": "error", "alasan": "token_id gak ketemu"}
        client = _get_client()
        args = MarketOrderArgs(token_id=token_id, amount=usd_amount, side=BUY,
                               order_type=OrderType.FOK)
        log(f"🚀 LIVE ORDER — ${usd_amount} BUY '{outcome_text}' (token={token_id[:14]}…)")
        signed = client.create_market_order(args)
        resp = client.post_order(signed, OrderType.FOK)
        log(f"✅ Terkirim: {resp}")
        return {"status": "success", "response": resp}
    except Exception as e:
        log(f"❌ Gagal kirim order: {e}")
        return {"status": "error", "alasan": str(e)}


def execute_arbitrage(condition_id, legs):
    """
    Eksekusi arbitrase multi-leg (beli tiap outcome).

    ⚠️ TIDAK ATOMIC: tiap leg dikirim satu-satu. Kalau leg pertama sukses tapi
    berikutnya gagal, bot BERHENTI dan kasih warning keras — TIDAK nyoba
    "beresin sendiri". Posisi sepihak = bukan lagi guaranteed profit. Cek manual.

    legs = list of (outcome_text, usd_amount).
    """
    hasil = []
    for i, (outcome, usd) in enumerate(legs, 1):
        res = place_market_buy(condition_id, outcome, usd)
        hasil.append({"leg": i, "outcome": outcome, "usd": usd, **res})
        if res["status"] not in ("success", "dry_run"):
            log(f"🛑 LEG {i} ({outcome}) GAGAL setelah {i-1} leg jalan. "
                f"STOP — cek posisi manual, arb ini gak lagi 'guaranteed'.")
            return {"status": "partial_fail", "legs": hasil}
    return {"status": "ok", "legs": hasil}


def cek_koneksi():
    """Test koneksi & auth tanpa kirim order apapun."""
    try:
        c = _get_client()
        c.get_ok()
        log(f"✅ Koneksi OK. Server time: {c.get_server_time()}")
        return True
    except Exception as e:
        log(f"❌ Koneksi/auth gagal: {e}")
        return False
