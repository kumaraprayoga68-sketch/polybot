"""
Uji apakah ADA sinyal yang bisa dipelajari dari riwayat bet polybot.

Pertanyaannya BUKAN "bisa gak model nebak menang/kalah" — harga pasar udah bisa.
Pertanyaannya: "ada gak info di fitur kita yang BELUM kecermin di harga pasar?"

Caranya: model belajar prediksi P(menang) dari fitur, lalu kita cuma bet kalau
prediksi model LEBIH TINGGI dari harga pasar. Kalau itu ngasih untung di data
yang belum pernah dilihat model -> ada sinyal. Kalau nol -> gak ada.

Anti-curang:
  * split URUT WAKTU (bukan acak) — model gak boleh lihat masa depan
  * win-rate trader dihitung dari data LATIH aja
  * baseline pembanding: bet semua (tanpa model)

Pakai:  python tools/uji_ml.py data/riwayat.csv
"""
import csv, sys, math
import numpy as np
from datetime import datetime

CSV = sys.argv[1] if len(sys.argv) > 1 else "data/riwayat.csv"

rows = [r for r in csv.DictReader(open(CSV, encoding="utf-8"))
        if r["strategi"] == "copytrade" and r.get("resolved") == "true"
        and r.get("harga") and r.get("size_usd")]
rows.sort(key=lambda r: r["timestamp"])

def f(x, d=0.0):
    try: return float(x)
    except (TypeError, ValueError): return d

def kategori(m):
    m = (m or "").lower()
    if any(k in m for k in ("o/u", "over", "under")): return "ou"
    if "spread" in m: return "spread"
    if "both teams" in m: return "btts"
    if " vs" in m: return "h2h"
    return "lain"

def hari_ke_resolve(r):
    try:
        d = datetime.strptime(r["end_date"][:10], "%Y-%m-%d").date()
        t = datetime.fromisoformat(r["timestamp"]).date()
        return max(0, min(14, (d - t).days))
    except Exception:
        return 1

KAT = ["ou", "spread", "btts", "h2h", "lain"]

def bangun(rows, trader_map):
    X, y, harga, size = [], [], [], []
    for r in rows:
        h = f(r["harga"])
        if not (0 < h < 1):
            continue
        tr = (r.get("trader") or "").split("|")[0].strip().lower()
        k = kategori(r["market"])
        X.append([
            h, h * h, math.log(h / (1 - h)),
            f(r.get("skor"), 5) / 10,
            hari_ke_resolve(r) / 14,
            len([w for w in (r.get("trader") or "").split("|") if w.strip()]) / 3,
            trader_map.get(tr, 0.5),
        ] + [1.0 if k == kk else 0.0 for kk in KAT])
        y.append(1.0 if r.get("menang") == "true" else 0.0)
        harga.append(h); size.append(f(r["size_usd"], 2.5))
    return np.array(X), np.array(y), np.array(harga), np.array(size)

def latih(X, y, l2=1.0, iters=400):
    Xb = np.hstack([np.ones((len(X), 1)), X]); w = np.zeros(Xb.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(Xb @ w, -30, 30)))
        g = Xb.T @ (p - y) / len(y) + l2 * np.r_[0, w[1:]] / len(y)
        S = p * (1 - p)
        H = (Xb * S[:, None]).T @ Xb / len(y) + l2 * np.eye(Xb.shape[1]) / len(y)
        try: w -= np.linalg.solve(H + 1e-6 * np.eye(len(w)), g)
        except np.linalg.LinAlgError: break
    return w

def prediksi(w, X):
    return 1 / (1 + np.exp(-np.clip(np.hstack([np.ones((len(X), 1)), X]) @ w, -30, 30)))

def pnl(menang, harga, size):
    return np.where(menang == 1, size * (1 - harga) / harga, -size)

def peta_trader(rs):
    tm = {}
    for r in rs:
        tr = (r.get("trader") or "").split("|")[0].strip().lower()
        if tr:
            a = tm.setdefault(tr, [0, 0])
            a[0] += 1 if r.get("menang") == "true" else 0; a[1] += 1
    return {k: (v[0] + 2.5) / (v[1] + 5) for k, v in tm.items()}

def lapor(nama, m, h, s):
    if len(m) < 20:
        print(f"  {nama:38} n={len(m)} (kekecilan)"); return None
    v = pnl(m, h, s); se = v.std(ddof=1) / math.sqrt(len(v))
    t = v.mean() / se if se else 0
    print(f"  {nama:38} n={len(m):5d} wr={m.mean()*100:4.1f}% "
          f"${v.sum():+8.2f} per-bet=${v.mean():+.4f} t={t:+5.2f}  "
          f"{'SIGNIFIKAN' if abs(t) >= 1.96 else 'noise'}")
    return t

if __name__ == "__main__":
    n = len(rows); cut = int(n * 0.70)
    tr_rows, te_rows = rows[:cut], rows[cut:]
    print(f"Total bet resolved : {n:,}")
    print(f"  LATIH : {len(tr_rows):,}  ({tr_rows[0]['timestamp'][:10]} .. {tr_rows[-1]['timestamp'][:10]})")
    print(f"  UJI   : {len(te_rows):,}  ({te_rows[0]['timestamp'][:10]} .. {te_rows[-1]['timestamp'][:10]})  <- belum dilihat model")
    print()
    tm = peta_trader(tr_rows)
    Xtr, ytr, htr, _ = bangun(tr_rows, tm)
    Xte, yte, hte, ste = bangun(te_rows, tm)
    w = latih(Xtr, ytr); p = prediksi(w, Xte)
    print("=== KALIBRASI di data UJI ===")
    print(f"  model {p.mean()*100:.1f}%  |  pasar {hte.mean()*100:.1f}%  |  kenyataan {yte.mean()*100:.1f}%")
    print(f"  Brier model/pasar: {((p-yte)**2).mean():.4f} / {((hte-yte)**2).mean():.4f}")
    print()
    print("=== HASIL di data UJI ===")
    lapor("BASELINE: bet semua (tanpa model)", yte, hte, ste)
    hasil = []
    for margin in (0.00, 0.02, 0.05, 0.10):
        sel = p > hte + margin
        t = lapor(f"model > harga + {margin:.2f}", yte[sel], hte[sel], ste[sel])
        if t is not None: hasil.append(t)
    print()
    print(f"  Ambang jujur setelah koreksi 4 uji (Bonferroni): |t| >= 2.50")
    print(f"  Terbaik: t={max(hasil):+.2f}  ->  {'LOLOS' if hasil and max(hasil) >= 2.50 else 'BELUM lolos'}")
