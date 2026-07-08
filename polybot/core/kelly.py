"""
Kelly Criterion — position sizing berbasis EDGE (win rate historis vs harga pasar
saat ini), bukan cuma confidence. f* negatif -> JANGAN bet (harga udah gak ngasih
edge) walau skor formula tinggi. Pakai FRACTIONAL Kelly (half) biar gak agresif.
    f* = p - (1-p) * P / (1-P)   ; p = win rate proxy, P = harga beli (0-1)
"""
KELLY_FRACTION_MULTIPLIER = 0.5
KELLY_FRACTION_MAX = 1.0


def kelly_fraction(win_rate_pct, harga_beli):
    """Fraksi dari MAX_PER_TRADE yang worth di-bet. 0.0 = jangan bet."""
    if harga_beli is None or harga_beli <= 0 or harga_beli >= 1:
        return 0.0
    p = max(0.0, min(1.0, win_rate_pct / 100))
    q = 1 - p
    f_full = p - (q * harga_beli / (1 - harga_beli))
    if f_full <= 0:
        return 0.0
    return round(min(f_full * KELLY_FRACTION_MULTIPLIER, KELLY_FRACTION_MAX), 3)
