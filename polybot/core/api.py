"""
PolymarketAPI — satu klien untuk semua endpoint PUBLIK Polymarket (tanpa auth):
- Gamma  : discovery market (question, liquidity, volume, outcomes/clobTokenIds)
- CLOB   : harga ASK/midpoint live + info market (resolusi, token per outcome)
- Data   : posisi & activity trader (buat copy-trading)
- LB     : leaderboard (buat auto-pilih trader)

Menggabungkan pola request/parse yang sebelumnya tersebar di 5 bot berbeda.
Semua endpoint di sini read-only — eksekusi order ada di executor.py (butuh auth).
"""
import json
import time
from urllib.parse import urlsplit

import requests

from .. import config

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


_dilapor = {}       # path -> kapan terakhir di-LOG (redaman log)
_dialarm = {}       # path -> kapan terakhir di-ALERT (redaman Telegram, terpisah)
_beruntun = {}      # path -> gagal berturut-turut (direset tiap sukses)
_AMBANG_BERUNTUN = 20

# Kata yang nandain ENDPOINT-nya yang berubah, bukan resource-nya yang gak ada.
_PETUNJUK_KONTRAK = ("retire", "deprecat", "no longer", "has been removed",
                     "not a query param", "invalid_request", "unsupported")


def _kontrak_berubah(status, badan):
    """Bedain "kontrak/endpoint berubah" dari "resource-nya aja yang gak ada".

    404 itu RUTIN dan gak boleh bikin alert: /midpoint balas
    404 "No orderbook exists for the requested token id" buat token yang order
    book-nya udah gak ada -- sering kejadian pas ngukur dampak harga di market
    yang udah kelar. Versi pertama rem ini nganggep SEMUA 4xx sebagai kontrak
    berubah dan langsung teriak buat kasus itu. Alert yang bohong bikin alert
    diabaikan, dan itu sama bahayanya sama gagal senyap.

    404 tetep dijaga lewat jalur lain: kalau satu path gagal 20x BERUNTUN tanpa
    sekali pun sukses, itu bukan resource nyasar lagi -- endpoint-nya yang mati.
    """
    b = (badan or "").lower()
    if any(k in b for k in _PETUNJUK_KONTRAK):
        return True
    return status in (400, 410, 422, 501)


def _lapor_gagal(url, status, badan):
    """Bikin kegagalan endpoint KELIHATAN.

    DUA KALI bot ini mati senyap gara-gara endpoint Polymarket berubah:
    lb-api diretire 15 Okt (21 hari gak nge-bet), Data API v1 diretire 2 Nov
    (10 jam). Dua-duanya lolos semua cek karena _get nelen error jadi None,
    pemanggil nerjemahin jadi list kosong, dan "nol posisi" gak bisa dibedain
    dari "trader gak punya posisi". Listener tetep kelihatan sehat.

    Sekarang SELALU nongol di log Actions. Yang dikirim ke Telegram cuma yang
    beneran gawat: kontrak/endpoint berubah (lihat _kontrak_berubah), atau satu
    path gagal 20x beruntun. Diredam 1 jam per path, jadi 20 wallet yang gagal
    di endpoint yang sama = 1 alert.
    """
    path = urlsplit(url).path or url
    beruntun = _beruntun.get(path, 0)
    sekarang = time.time()
    pesan = f"{path} gagal (HTTP {status}): {(badan or '')[:160]}"

    # Redaman log dan redaman alert HARUS terpisah. Pernah digabung: begitu
    # kegagalan pertama ke-log, path-nya keredam 1 jam, jadi eskalasi "20x
    # beruntun" gak pernah sampe dikirim.
    if sekarang - _dilapor.get(path, 0) >= 3600:
        _dilapor[path] = sekarang
        print(f"  ⚠️  API {pesan}  [gagal beruntun: {beruntun}]")

    if _kontrak_berubah(status, badan):
        alasan = "Kontrak/endpoint-nya berubah."
    elif beruntun >= _AMBANG_BERUNTUN:
        alasan = f"Gagal {beruntun}x BERUNTUN — kemungkinan endpoint-nya mati."
    else:
        return                 # gangguan sesaat / resource gak ada: log doang
    if sekarang - _dialarm.get(path, 0) < 3600:
        return
    _dialarm[path] = sekarang
    try:
        from . import notify
        notify.kirim(f"🚨 *API Polymarket bermasalah*\n`{pesan}`\n\n{alasan}\n"
                     f"Bot kemungkinan BERHENTI nge-bet sampai ini dibenerin.")
    except Exception:
        pass


def _get(url, params=None, max_retry=2, timeout=12):
    """GET dengan retry + backoff; balikin JSON atau None. Hormati 429 (rate limit).

    Kalau akhirnya gagal, dilaporin lewat _lapor_gagal — JANGAN dibikin senyap
    lagi (lihat catatan di sana).
    """
    path = urlsplit(url).path or url
    status, badan = None, ""
    for attempt in range(max_retry + 1):
        try:
            r = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)
            status = r.status_code
            if status == 429 and attempt < max_retry:
                time.sleep(2 * (attempt + 1))
                continue
            if 400 <= status < 500 and status != 429:
                # 4xx gak bakal beda kalau diulang — endpoint/paramnya yang salah.
                _beruntun[path] = _beruntun.get(path, 0) + 1
                _lapor_gagal(url, status, r.text)
                return None
            r.raise_for_status()
            _beruntun.pop(path, None)     # sukses -> hitungan beruntun direset
            return r.json()
        except Exception as e:
            badan = badan or str(e)
            if attempt < max_retry:
                time.sleep(1.5 * (attempt + 1))
                continue
            _beruntun[path] = _beruntun.get(path, 0) + 1
            _lapor_gagal(url, status, badan)
            return None
    return None


# ── GAMMA: discovery market ───────────────────────────────────────────────────
def fetch_markets_page(limit=500, offset=0, extra_params=None):
    """1 halaman market aktif dari Gamma. Balikin list (mungkin kosong)."""
    params = {"active": "true", "closed": "false", "limit": limit, "offset": offset}
    if extra_params:
        params.update(extra_params)
    data = _get(config.GAMMA_URL, params=params, timeout=15)
    return data if isinstance(data, list) else []


def iter_all_markets(max_markets=2000, page_size=500, sleep_between=0.2):
    """Generator semua market aktif (paginated) sampai max_markets."""
    offset = 0
    total = 0
    while offset < max_markets:
        page = fetch_markets_page(limit=page_size, offset=offset)
        if not page:
            break
        for m in page:
            total += 1
            yield m
            if total >= max_markets:
                return
        if len(page) < page_size:
            break
        offset += page_size
        time.sleep(sleep_between)


def parse_market(m):
    """
    Normalisasi 1 market mentah Gamma. Field outcomes/outcomePrices/clobTokenIds
    itu STRINGIFIED JSON (perlu json.loads). Balikin None kalau datanya cacat.
    """
    try:
        outcomes = json.loads(m.get("outcomes", "[]"))
        prices_raw = json.loads(m.get("outcomePrices", "[]"))
        token_ids = json.loads(m.get("clobTokenIds", "[]"))
    except (json.JSONDecodeError, TypeError):
        return None

    if not outcomes or len(outcomes) != len(prices_raw) or len(outcomes) != len(token_ids):
        return None

    try:
        summary_prices = [float(p) for p in prices_raw]
    except (TypeError, ValueError):
        return None

    def _f(key):
        try:
            return float(m.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    return {
        "question": m.get("question", "N/A"),
        "conditionId": m.get("conditionId", ""),
        "slug": m.get("slug", ""),
        "outcomes": outcomes,
        "summary_prices": summary_prices,   # dari Gamma — CUMA referensi, jangan buat keputusan
        "token_ids": token_ids,             # buat fetch harga ASK live per outcome
        "liquidity": _f("liquidity"),
        "volume": _f("volume"),
        "end_date": m.get("endDate", ""),
    }


# ── CLOB: harga & info market live ────────────────────────────────────────────
def clob_ask_price(token_id):
    """Harga ASK live (harga eksekusi BELI) 1 token dari CLOB order book."""
    data = _get(f"{config.CLOB_HOST}/price", params={"token_id": token_id, "side": "BUY"})
    if not data:
        return None
    try:
        return float(data.get("price", 0))
    except (TypeError, ValueError):
        return None


def clob_midpoint(token_id):
    """Harga midpoint saat ini 1 token."""
    data = _get(f"{config.CLOB_HOST}/midpoint", params={"token_id": token_id})
    if not data:
        return None
    try:
        return float(data.get("mid", 0))
    except (TypeError, ValueError):
        return None


def clob_market(condition_id):
    """Info market lengkap (termasuk tokens & status resolusi) via CLOB publik."""
    return _get(f"{config.CLOB_HOST}/markets/{condition_id}")


# ── DATA API v2: posisi & activity trader (copy-trading) ──────────────────────
# Data API v1 DIRETIRE ~2 Nov 2026 malam ({"error":"Data API v1 has been
# retired"}). Akibatnya bot berhenti nge-bet total dari 2 Nov 21:01 dan GAK ADA
# alarm — persis kaya lb-api yang diretire 15 Okt, karena _get nelen error jadi
# list kosong: nol posisi -> nol sinyal -> nol bet, listener tetep kelihatan sehat.
#
# Beda v1 -> v2: (a) respons dibungkus {"data": [...], "pagination": {...}},
# (b) nama field jadi snake_case, (c) activity pagination CURSOR-ONLY ('offset'
# ditolak 400). Normalisasi ditaruh di SINI biar perubahan kekunci di satu tempat
# -- nama field gaya v1 ditambahin sebagai alias, nama v2 tetep ada, jadi
# copytrade/trader_pnl/dampak jalan tanpa diubah.
_ALIAS_POSISI = {
    "condition_id": "conditionId",
    "current_price": "curPrice",
    "end_date": "endDate",
    "token_id": "asset",
    "current_size": "size",
    "proxy_wallet": "proxyWallet",
    # v1 cashPnl = PnL cash satu posisi. Buat posisi yang udah resolve (harga 0/1
    # -- itu yang dipake trader_pnl) total_pnl yang paling setara.
    "total_pnl": "cashPnl",
}
_ALIAS_ACTIVITY = {
    "condition_id": "conditionId",
    "usdc_size": "usdcSize",
    "token_id": "asset",
    "transaction_hash": "transactionHash",
    "proxy_wallet": "proxyWallet",
}


def _isi(data):
    """Ambil list hasil. v2 bungkus di {"data": [...]}; v1 dulu list polos."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return data["data"]
    return []


def _samakan(rows, alias):
    """Tambahin nama field gaya v1 di samping nama v2 (yang udah ada gak ditimpa)."""
    hasil = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        r = dict(r)
        for baru, lama in alias.items():
            if baru in r and lama not in r:
                r[lama] = r[baru]
        hasil.append(r)
    return hasil


def trader_positions(wallet, limit=500):
    """Semua posisi trader (status OPEN + REDEEMABLE = resolved belum di-redeem)."""
    data = _get(f"{config.DATA_API}/v2/positions",
                params={"user": wallet, "limit": limit})
    return _samakan(_isi(data), _ALIAS_POSISI)


def trader_activity_page(wallet, limit=500, cursor=None, activity_type=None):
    """Satu halaman activity. Balikin (rows, next_cursor).

    v2 cursor-only: 'offset' ditolak 400 ("pages are cursor-only"), jadi
    halaman berikutnya WAJIB pakai next_cursor dari respons sebelumnya.
    """
    params = {"user": wallet, "limit": limit}
    if cursor:
        params["cursor"] = cursor
    if activity_type:
        params["type"] = activity_type
    data = _get(f"{config.DATA_API}/v2/activity", params=params)
    rows = _samakan(_isi(data), _ALIAS_ACTIVITY)
    lanjut = None
    if isinstance(data, dict):
        pag = data.get("pagination") or {}
        if pag.get("has_more"):
            lanjut = pag.get("next_cursor")
    return rows, lanjut


def trader_activity(wallet, limit=500, activity_type=None):
    """Halaman pertama activity trader (BUY/SELL/REDEEM/…)."""
    return trader_activity_page(wallet, limit, None, activity_type)[0]


# ── LEADERBOARD: auto-pilih trader ────────────────────────────────────────────
def leaderboard(window="30d", limit=100):
    """
    Top trader by profit dari data-api /v2/leaderboard.

    Bentuk respons: {"data": [{"user_id", "pnl", "volume", "user_name", ...}],
                     "pagination": {...}}
    Alamat wallet ada di 'user_id' (dulu di lb-api namanya 'proxyWallet').

    CATATAN: `window` sudah TIDAK berpengaruh di endpoint baru — dicoba window=,
    period=, t=, rangeType= dengan nilai day/week/month/all, semuanya balikin
    hasil identik (cursor internal selalu t="day"). Parameter tetap diterima biar
    pemanggil lama gak rusak, tapi jangan dikira ngefek.
    """
    data = _get(config.LEADERBOARD_URL, params={"window": window, "limit": limit})
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "leaderboard", "results"):
            if isinstance(data.get(key), list):
                return data[key]
    return []
