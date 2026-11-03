"""Analisa dampak harga: begitu trader masuk, harganya gerak ke arah dia gak?

Pakai:  python tools/analisa_dampak.py data/dampak_harga.csv

Yang diukur per posisi: selisih harga dari saat trader kelihatan masuk (p0) ke
1 jam kemudian (p1) dan 6 jam kemudian (p6). Karena yang dicatat selalu outcome
yang DIBELI trader, harga NAIK = gerak ke arah dia = sinyalnya berisi info.

Tiga pertanyaan yang dijawab:
  1. Secara keseluruhan, posisi trader diikuti pergerakan harga yang
     menguntungkan? (kalau nol -> leaderboard gak ngasih info apa-apa)
  2. Trader mana yang dampaknya konsisten? (dasar pemilihan yang BUKAN dari
     hasil menang/kalah masa lalu)
  3. Pergerakannya kejadian di jam pertama atau nyebar? Kalau hampir semua
     di jam pertama, bot yang telat 0-15 menit udah ketinggalan -> itu
     menjelaskan KENAPA copytrade gak jalan.

CATATAN: harga 0/1 artinya market udah resolve. Itu tetap data valid (harga
gerak penuh ke satu sisi), jadi gak dibuang.
"""
import csv
import math
import statistics as st
import sys
from collections import defaultdict

CSV = sys.argv[1] if len(sys.argv) > 1 else "data/dampak_harga.csv"


def angka(x):
    try:
        v = float(x)
        return v if 0 <= v <= 1 else None
    except (TypeError, ValueError):
        return None


def uji(nama, nilai, lebar=30):
    """Lapor rata-rata + t-stat. t = seberapa yakin ini bukan kebetulan."""
    n = len(nilai)
    if n < 20:
        print(f"  {nama:<{lebar}} n={n} (kekecilan)")
        return None
    m = st.mean(nilai)
    se = st.stdev(nilai) / math.sqrt(n)
    t = m / se if se else 0
    tag = "SIGNIFIKAN" if abs(t) >= 1.96 else "noise"
    print(f"  {nama:<{lebar}} n={n:5d}  rata2 {m:+.4f}  "
          f"(={m*100:+.2f} sen)  t={t:+5.2f}  {tag}")
    return t


def main():
    try:
        rows = list(csv.DictReader(open(CSV, newline="", encoding="utf-8")))
    except OSError:
        print(f"File {CSV} belum ada — bot perlu jalan dulu beberapa siklus.")
        return

    d1, d6 = [], []
    per_trader = defaultdict(list)
    for r in rows:
        p0 = angka(r.get("p0"))
        if p0 is None:
            continue
        p1, p6 = angka(r.get("p1")), angka(r.get("p6"))
        if p1 is not None:
            d1.append(p1 - p0)
            per_trader[r["wallet"]].append(p1 - p0)
        if p6 is not None:
            d6.append(p6 - p0)

    print(f"Total posisi tercatat : {len(rows):,}")
    print(f"  terukur T+1 jam     : {len(d1):,}")
    print(f"  terukur T+6 jam     : {len(d6):,}")
    if not d1:
        print("\nBelum ada yang terukur. Pengukuran pertama jatuh tempo 1 jam "
              "setelah posisi kecatat — balik lagi nanti.")
        return

    print()
    print("=== 1. APAKAH POSISI TRADER DIIKUTI GERAKAN HARGA? ===")
    print("   (positif = harga gerak ke arah mereka = sinyalnya berisi info)")
    t1 = uji("gerakan T+1 jam", d1)
    t6 = uji("gerakan T+6 jam", d6)

    if d6 and d1:
        print()
        print("=== 2. KAPAN GERAKANNYA TERJADI? ===")
        m1, m6 = st.mean(d1), st.mean(d6)
        se6 = st.stdev(d6) / math.sqrt(len(d6))
        # Porsi = m1/m6 cuma bermakna kalau m6 BEDA DARI NOL secara meyakinkan.
        # Kalau dua-duanya ~nol, bagi-bagiannya ngasih angka ngawur (pernah
        # keluar "198%") dan kesimpulannya nyesatin.
        if abs(m6) < 1.96 * se6:
            print("  Gerakan 6 jam gak beda dari nol, jadi porsinya GAK BISA dihitung.")
            print("  (bagi dua angka yang sama-sama ~nol = hasilnya ngawur)")
            print("  -> Pertanyaan 'kapan gerakannya' baru bisa dijawab kalau")
            print("     gerakannya sendiri terbukti ada.")
        else:
            porsi = m1 / m6 * 100
            print(f"  jam pertama menyumbang {porsi:.0f}% dari total gerakan 6 jam")
            if porsi > 80:
                print("  -> Hampir semua gerakan di jam PERTAMA. Bot yang telat")
                print("     0-15 menit kemungkinan besar udah ketinggalan.")
            elif porsi < 40:
                print("  -> Gerakan nyebar; masuk belakangan masih mungkin kebagian.")

    print()
    print("=== 3. PER TRADER (T+1 jam, min 30 posisi) ===")
    hasil = []
    for w, v in per_trader.items():
        if len(v) < 30:
            continue
        m = st.mean(v)
        se = st.stdev(v) / math.sqrt(len(v))
        hasil.append((m / se if se else 0, m, len(v), w))
    if not hasil:
        print("  belum ada trader dengan >=30 posisi terukur")
    else:
        hasil.sort(reverse=True)
        for t, m, n, w in hasil[:10]:
            tag = " <- SIGNIFIKAN" if abs(t) >= 1.96 else ""
            print(f"  {w[:10]}...  n={n:4d}  rata2 {m:+.4f}  t={t:+5.2f}{tag}")
        print()
        sig = sum(1 for t, *_ in hasil if abs(t) >= 1.96)
        t_maks = max(abs(t) for t, *_ in hasil)
        print(f"  {sig}/{len(hasil)} trader signifikan.")
        print(f"  Dengan ambang 5%, dari {len(hasil)} trader kita HARAPKAN "
              f"~{len(hasil)*0.05:.1f} signifikan karena kebetulan semata.")
        # Jumlah saja tidak cukup: 1 trader dengan t=11 jelas bukan kebetulan
        # walau "1 dari 2" terdengar wajar. Jadi cek juga t TERKUAT-nya.
        # Ambang Bonferroni: nilai t yang bikin 5% peluang salah TETAP 5% walau
        # kita ngintip banyak trader sekaligus.
        amb = 1.96 + math.log(max(1, len(hasil))) * 0.45
        if t_maks >= amb:
            print(f"  -> t terkuat {t_maks:+.2f} LEWAT ambang terkoreksi {amb:.2f}")
            print("     (sudah memperhitungkan banyaknya trader yang diintip).")
            print("     Ini bukan sekadar kebetulan — layak ditindaklanjuti.")
        else:
            print(f"  -> t terkuat cuma {t_maks:+.2f}, di bawah ambang terkoreksi "
                  f"{amb:.2f}.")
            print("     Belum ada bukti ada trader yang beneran beda.")

    print()
    print("=== KESIMPULAN ===")
    if t1 is None:
        print("  Data belum cukup.")
    elif abs(t1) < 1.96:
        print("  Posisi trader TIDAK diikuti gerakan harga yang berarti.")
        print("  Artinya leaderboard gak ngasih info yang bisa dipakai —")
        print("  konsisten sama kegagalan copytrade selama ini.")
    elif t1 > 0:
        print("  ADA gerakan harga ke arah trader. Cek bagian 2: kalau hampir")
        print("  semuanya di jam pertama, kita tetap gak bisa kebagian.")
    else:
        print("  Harga gerak MELAWAN trader setelah mereka masuk.")
        print("  Kalau konsisten, justru melawan mereka yang masuk akal.")


if __name__ == "__main__":
    main()
