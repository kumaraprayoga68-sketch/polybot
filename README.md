# polybot — 5 bot Polymarket jadi 1

Hasil unifikasi **multi-trader-bot + polymarket-bot + poly-ai + market-scanner-bot +
9router** jadi satu package modular dengan **core shared** dan **3 strategi** yang
bisa dipilih. Satu entry point, satu config, satu sistem pencatatan + alert Telegram.

> ⚠️ Eksperimen pribadi, **bukan financial advice**. Trading prediction market ada
> resiko rugi total. **Default semua = paper trading (simulasi).**

## Kenapa digabung

Kelima bot lama semuanya nyentuh API yang sama (Gamma / CLOB / data-api) dan
duplikasi executor, resolver, kelly, dsb. polybot menyatukannya:

```
polybot/
├── core/           # dipakai semua strategi
│   ├── api.py         Gamma + CLOB + data-api + leaderboard (public, no auth)
│   ├── executor.py    eksekusi order (authed, TRIPLE-GATE + hard cap)
│   ├── resolver.py    cek resolusi market (menang/kalah)
│   ├── scoring.py     formula deterministik keputusan copy-trade
│   ├── kelly.py       position sizing berbasis edge
│   ├── trader_pnl.py  net PnL gabungan /positions + /activity
│   ├── tracker.py     pencatatan semua keputusan ke data/riwayat.csv
│   └── notify.py      alert Telegram
└── strategies/
    ├── copytrade.py   copy-trading consensus (multi-trader + polymarket-bot)
    ├── arbitrage.py   YES+NO arbitrage scan/exec (poly-ai + 9router)
    └── scanner.py     discovery market by kategori (market-scanner-bot)
```

## Setup

```bash
cd polybot
pip install -r requirements.txt
cp .env.example .env      # paper trading gak perlu isi apa-apa
```

## Pakai

```bash
python -m polybot status              # lihat config + gate keamanan
python -m polybot scan                # discovery market (sport/politik/crypto)
python -m polybot arbitrage           # scan peluang arbitrase (1x)
python -m polybot arbitrage --loop    # loop tiap ARB_SCAN_INTERVAL_MIN
python -m polybot copytrade           # 1 siklus copy-trade
python -m polybot copytrade --loop    # monitor terus
python -m polybot all --loop          # scanner + arbitrage + copytrade
```

Semua peluang/keputusan tercatat di `data/riwayat.csv` dan (kalau Telegram diisi)
dikirim sebagai alert.

## Keamanan Live Trading (TRIPLE GATE)

Order live **cuma** kekirim kalau ketiganya terpenuhi — kalau salah satu gagal,
otomatis dry-run:

1. `SIMULASI_MODE=false`
2. `LIVE_TRADING_ENABLED=true`
3. `usd_amount <= MAX_ORDER_SIZE_ABSOLUTE` (hard cap absolut)

Plus wallet `PRIVATE_KEY`/`FUNDER_ADDRESS` di `.env`. **Pakai wallet khusus bot**,
isi seperlunya, jangan pernah commit `.env`.

Test koneksi/auth tanpa kirim order:
```bash
python -m polybot test-connection
```

## Catatan penting per strategi

- **Arbitrage**: edge yang kedeteksi BELUM dikurangi taker fee (~1.25-2.5%/leg, 2
  leg). `ARB_MIN_EDGE_PCT` cuma buffer kasar — **cek manual di UI** sebelum eksekusi.
  Eksekusi 2-leg **tidak atomic**: kalau leg kedua gagal, bot berhenti & warning.
- **Copy-trade**: keputusan IKUT/SKIP **100% formula deterministik** (`scoring.py`),
  bukan AI. Net PnL dihitung gabungan /positions + /activity (best-effort, histori
  sangat panjang mungkin gak ke-capture penuh).
- **Scanner**: discovery + watchlist, **bukan** eksekutor.

## Bot lama

Kelima folder bot lama dibiarkan utuh (tidak dihapus) sebagai referensi. polybot
berdiri sendiri dan tidak meng-import apa pun dari situ.
