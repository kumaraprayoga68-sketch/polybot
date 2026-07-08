# Setup polybot 24/7 (GitHub Actions — GRATIS)

Bikin bot nyari peluang otomatis tiap 30 menit tanpa laptop nyala. Hasil ke Telegram
+ dashboard. Selalu **paper** (CI gak pernah live trading).

## 1. Bikin repo GitHub
- Buka <https://github.com/new>
- Nama: `polybot` (bebas)
- Pilih **Public** ✅ — biar GitHub Actions **gratis unlimited**. Kodenya gak ada
  rahasia (semua token disimpan terpisah di Secrets, `.env` gak ikut ke-push).
  (Kalau maksa Private: minutes gratis cuma 2000/bln, ganti cron di `hunt.yml`
  jadi `"0 * * * *"` = tiap jam.)
- JANGAN centang "Add a README / .gitignore" (biar gak konflik)
- Create repository

## 2. Push kode (terminal, dari folder `bot/polybot`)
```bash
git remote add origin https://github.com/<USERNAME>/polybot.git
git branch -M main
git push -u origin main
```
Kalau diminta password → pakai **Personal Access Token** (github.com → Settings →
Developer settings → Tokens), bukan password akun.

## 3. Tambah 4 Secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**.
Bikin 4 (nama harus persis):

| Name | Value |
|------|-------|
| `TELEGRAM_BOT_TOKEN` | token bot Telegram kamu |
| `TELEGRAM_CHAT_ID` | chat id kamu |
| `POLYBOT_DASHBOARD_URL` | `https://polybot-dashboard-eight.vercel.app` |
| `POLYBOT_TOKEN` | access token dashboard |

## 4. Aktifin & test
- Repo → tab **Actions** → kalau ada tombol enable, klik.
- Pilih workflow **"polybot 24/7"** → **Run workflow** (buat tes manual sekarang).
- Cek Telegram + dashboard — harusnya ada hasil scan masuk.
- Setelah itu jalan **otomatis tiap 30 menit**.

## Catatan
- CI **selalu paper** (gak ada `PRIVATE_KEY` di secrets → live gak mungkin).
- `data/riwayat.csv` di-commit balik tiap run biar dedup + histori persist.
- Mau ubah frekuensi: edit baris `cron` di `.github/workflows/hunt.yml`.
- Kontrol interaktif (`/loop`, `/live`, `/window`) tetap butuh listener
  (`python -m polybot telegram`) jalan di mesin — CI ini cuma auto-scan satu arah.
