# Deploy listener Telegram ke Render (jalan 24/7 di cloud)

Biar kontrol Telegram (`/scan`, `/loop`, `/kelly`, dll) jalan terus tanpa PC kamu.

> ⚠️ **Cuma boleh ada 1 listener** yang polling Telegram. Sebelum Render nyala,
> **matiin listener di PC** (kalau masih jalan) — kalau dua-duanya nyala, rebutan &
> command bisa gak kejawab. GitHub Actions 24/7 (auto-scan) TIDAK kena — itu terpisah.

## 1. Bikin akun + connect repo
- Daftar di <https://render.com> (bisa login pakai GitHub)
- New → **Blueprint** → pilih repo `kumaraprayoga68-sketch/polybot`
- Render otomatis baca `render.yaml`

## 2. Isi 4 environment variable (rahasia)
Pas diminta (atau di Settings → Environment service `polybot-telegram`):

| Key | Value |
|-----|-------|
| `TELEGRAM_BOT_TOKEN` | token bot Telegram |
| `TELEGRAM_CHAT_ID` | chat id kamu |
| `POLYBOT_DASHBOARD_URL` | `https://polybot-dashboard-eight.vercel.app` |
| `POLYBOT_TOKEN` | access token dashboard |

(Semua ada di file `.env` lokal kamu — tinggal copy.)

## 3. Deploy
- Klik **Apply / Create** → Render build & jalanin `python -m polybot telegram`
- Cek tab **Logs** → harusnya muncul `🤖 Telegram control aktif sebagai @snowe00_bot`
- Coba `/ping` di Telegram → harus dibales

## Biaya
- **Worker** (default `render.yaml`): ~$7/bln, nyala terus, paling stabil. **Rekomendasi.**
- **Gratis** (Web Service free): edit `render.yaml` → `type: web`, `plan: free`,
  tambah `healthCheckPath: /`. Listener otomatis buka health server (pakai env `PORT`).
  ⚠️ Free "tidur" 15 menit idle → biar melek, pasang ping tiap ~10 menit ke URL Render
  (gratis via cron-job.org atau UptimeRobot). Bisa telat bales pas lagi "bangun".

## Catatan
- Cloud ini **selalu paper** (`SIMULASI_MODE=true`, gak ada wallet) — live mustahil dari sini.
- `riwayat.csv` di Render ephemeral (reset tiap redeploy) — histori permanen tetap di
  GitHub Actions. Render ini fokus **kontrol interaktif + loop**.
- Tiap `git push` ke main → Render auto-redeploy (kalau `autoDeploy: true`).
