# polybot dashboard (Vercel)

Dashboard web buat mantau polybot dari mana aja. **Tanpa login, tanpa token,
tanpa database.**

## Cara kerja
- `index.html` — halaman statis. Data ditarik **langsung dari
  `../data/riwayat.csv`** via URL raw GitHub (repo public), auto-refresh 60 detik.
  Jadi dashboard selalu sinkron sama riwayat yang di-commit CI tiap 30 menit.
- `api/event.js` — endpoint `POST /api/event`. Terima semua event dari bot
  (`core/dashboard.py`) **tanpa cek token**, balas `200`. Cuma buat ACK biar
  `push_event` nggak error — sumber data tampilan tetap CSV di atas.

## Deploy
Deploy manual ke Vercel (project `polybot-dashboard`) dari folder ini:

```bash
cd dashboard
vercel --prod        # butuh vercel CLI + login
```

## Auto-deploy dari Git (opsional)
Biar tiap ubah dashboard langsung ke-deploy:
1. Vercel → project `polybot-dashboard` → **Settings → Git** → Connect ke
   repo `kumaraprayoga68-sketch/polybot`.
2. Set **Root Directory** = `dashboard`.
3. **PENTING** — Settings → Git → **Ignored Build Step**, isi:
   ```
   git diff --quiet HEAD^ HEAD .
   ```
   Biar commit CI (`riwayat.csv` tiap 30 menit) **nggak** mancing rebuild dashboard
   terus-terusan — cuma rebuild kalau ada perubahan di folder `dashboard/`.

> Framework: none (static + Node function). Nggak ada dependency / build step.
