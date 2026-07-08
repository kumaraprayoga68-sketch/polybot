"""
Scoring deterministik untuk keputusan copy-trade (IKUT/SKIP). Input sama = output
sama SELALU (bisa di-backtest, gak ada variasi LLM). AI — kalau diaktifkan — cuma
nulis narasi "alasan", gak bisa ngubah angka/keputusan.
"""
SAMPLE_SIZE_PENUH = 15
WIN_RATE_LANTAI = 50
WIN_RATE_ATAP = 100
BONUS_PER_TRADER_CONSENSUS = 0.3
BONUS_CONSENSUS_MAX = 1.0


def skor_trader_individu(win_rate, net_pnl, total_closed):
    """0-10 untuk 1 trader. net_pnl<=0 -> 0 langsung (gak layak diikuti)."""
    if net_pnl is None or net_pnl <= 0 or not total_closed:
        return 0.0
    wr_score = max(0.0, (win_rate - WIN_RATE_LANTAI) / (WIN_RATE_ATAP - WIN_RATE_LANTAI)) * 10
    sample_conf = min(1.0, (total_closed / SAMPLE_SIZE_PENUH) ** 0.5)
    return round(wr_score * sample_conf, 2)


def skor_single_trader(performa):
    if not performa:
        return 0.0
    return skor_trader_individu(performa.get("win_rate", 0),
                                performa.get("net_pnl", 0),
                                performa.get("total_closed", 0))


def skor_consensus(performa_list, jumlah_trader):
    """0-10 untuk consensus (2+ trader): rata-rata 0.6 + weakest-link 0.4 + bonus jumlah."""
    skor = [skor_trader_individu(t.get("win_rate", 0), t.get("net_pnl", 0), t.get("total_closed", 0))
            for t in performa_list if "net_pnl" in t]
    if not skor:
        return 0.0
    kombinasi = (sum(skor) / len(skor)) * 0.6 + min(skor) * 0.4
    bonus = min(max(0, jumlah_trader - 2) * BONUS_PER_TRADER_CONSENSUS, BONUS_CONSENSUS_MAX)
    return round(min(kombinasi + bonus, 10), 2)


def keputusan_dari_skor(skor, threshold):
    return "IKUT" if skor >= threshold else "SKIP"
