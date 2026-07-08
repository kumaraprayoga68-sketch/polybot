# Setup polybot 24/7 (GitHub Actions — gratis, tanpa laptop nyala)

Bot bakal jalan tiap **30 menit** otomatis di server GitHub: scan arbitrage +
discovery market + copytrade (paper) + evaluasi hasil, lalu kirim ke **Telegram +
dashboard Vercel**. Riwayat disimpan balik ke repo biar state-nya persisten.

Repo ini udah di-`git init` + commit pertama. Tinggal 3 langkah:

## 1. Bikin repo GitHub & push

Di https://github.com/new bikin repo baru (misal `polybot`).
**Saran: bikin PUBLIC** — GitHub Actions gratis unlimited menit di repo public.
Kalau PRIVATE, ada kuota 2.000 menit/bulan (cukup buat ~hourly, bukan tiap 30 menit).

Lalu dari folder ini (`bot/polybot`):

```bash
git remote add origin https://github.com/<username>/polybot.git
git push -u origin main
```

> `.env` (isi token kamu) **tidak** ikut ke-push — udah di-`.gitignore`. Kredensial
> masuk lewat GitHub Secrets di langkah 2, bukan lewat kode.

## 2. Set Secrets

Di repo GitHub: **Settings → Secrets and variables → Actions → New repository secret**.
Tambah 4 ini (nilainya ada di `.env` lokal kamu):

| Secret | Nilai |
|--------|-------|
| `TELEGRAM_BOT_TOKEN` | token bot Telegram |
| `TELEGRAM_CHAT_ID` | chat id kamu |
| `POLYBOT_DASHBOARD_URL` | `https://polybot-dashboard-eight.vercel.app` |
| `POLYBOT_TOKEN` | access token dashboard |

## 3. Aktifkan & tes

- Tab **Actions** → kalau ada prompt "enable workflows", klik enable.
- Pilih workflow **"polybot 24/7"** → **Run workflow** (trigger manual buat tes
  pertama, gak usah nunggu 30 menit).
- Cek: Telegram dapet alert + dashboard Vercel keisi event.

Setelah itu jalan sendiri tiap 30 menit. Ubah jadwal di
`.github/workflows/hunt.yml` (baris `cron`).

## Bikin bot Telegram BISA DIBALES (tanpa laptop)

`hunt.yml` cuma kirim alert **satu arah**. Biar bot Telegram jawab command
(`/status`, `/hunt`, `/agresif`, `/kelly`, `/window`, dll) walau laptop mati,
ada workflow kedua: **`.github/workflows/telegram.yml`** yang jalanin listener
`python -m polybot telegram` nonstop dan **restart sendiri tiap ~5,5 jam**.

Aktifin: tab **Actions** → **"polybot Telegram control (listener 24/7)"** →
**Run workflow** (sekali aja — habis itu nyambung sendiri). Pakai secret yang
sama (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). Tes: kirim `/ping` di chat →
harusnya dibales `🏓 pong`.

> Catatan: GitHub Actions bukan dirancang buat service nonstop — kalau listener
> mati cepet (<5 menit) restart otomatis ditahan (kemungkinan secret salah).
> Buat yang bener-bener stabil, host listener di mesin always-on (VPS/Railway/
> Render/Oracle free) — tapi buat paper trading, cara ini udah cukup.

## Catatan

- **Live trading TIDAK PERNAH dari CI** — workflow hard-set `SIMULASI_MODE=true`.
  Live cuma bisa dari lokal (butuh `PRIVATE_KEY` yang gak pernah masuk repo).
- Copytrade di CI pakai auto-pilih trader (leaderboard). Mau trader manual? set
  repository variable / ubah env di workflow.
- Mau lebih sering dari 30 menit? Edit `cron` (GitHub minimum ~5 menit, kadang
  telat pas server sibuk — normal).
- Arbitrage jarang nemu peluang lolos 5% (market efisien) — itu normal, bukan bug.
  Scanner & copytrade tetap ngasih sinyal.
