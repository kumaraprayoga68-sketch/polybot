"""
Strategi SCANNER — market discovery by kategori (dari market-scanner-bot).
Bukan eksekutor: dia nemuin & mengkategorikan market aktif volume tinggi
(sport / politik / crypto) buat watchlist + alert, bukan langsung trading.
"""
from ..core import api, tracker, notify
from ..config import Scanner

KEYWORDS = {
    "sports": [" vs.", " vs ", "match", "championship", "tournament", "world cup",
               "nba", "nfl", "mlb", "fifa", "uefa", "premier league", "playoffs",
               "total goals", "o/u ", "spread:"],
    "politics": ["election", "president", "senate", "congress", "parliament",
                 "minister", "impeach", "governor", "prime minister", "cabinet"],
    "crypto": ["bitcoin", "btc", "ethereum", "eth", "crypto", "solana", " sol ",
               "dogecoin", "doge", "xrp", "ripple", "binance", "coinbase",
               "token", "blockchain", "defi", "nft", "altcoin", "stablecoin"],
}


def _kategori(question):
    q = question.lower()
    for cat, kws in KEYWORDS.items():
        if cat not in Scanner.CATEGORIES:
            continue
        if any(kw in q for kw in kws):
            return cat
    return None


def scan():
    """Balikin dict {kategori: [market...]} untuk market aktif volume tinggi."""
    hasil = {c: [] for c in Scanner.CATEGORIES}
    for m in api.iter_all_markets(max_markets=2000):
        parsed = api.parse_market(m)
        if not parsed or parsed["volume"] < Scanner.MIN_VOLUME_USD:
            continue
        cat = _kategori(parsed["question"])
        if cat:
            hasil[cat].append(parsed)
    for c in hasil:
        hasil[c].sort(key=lambda x: x["volume"], reverse=True)
    return hasil


def run(top=8):
    print(f"🔭 [scanner] discovery market (vol≥${Scanner.MIN_VOLUME_USD:,.0f}, "
          f"kategori: {', '.join(Scanner.CATEGORIES)})…")
    hasil = scan()
    total = sum(len(v) for v in hasil.values())
    if not total:
        print("✅ Gak ada market lolos filter volume.")
        return hasil

    lines = []
    for cat, markets in hasil.items():
        if not markets:
            continue
        print(f"\n📂 {cat.upper()} ({len(markets)})")
        for mkt in markets[:top]:
            print(f"   {mkt['question'][:60]} — vol ${mkt['volume']:,.0f} "
                  f"liq ${mkt['liquidity']:,.0f}")
            tracker.catat("scanner", "discovery", market=mkt["question"][:60],
                          condition_id=mkt["conditionId"],
                          keterangan=f"{cat} vol ${mkt['volume']:,.0f}")
        lines.append(f"• {cat}: {len(markets)} market (top: {markets[0]['question'][:40]})")
    if lines:
        notify.alert_sinyal(f"🔭 Scanner: {total} market", lines)
    return hasil
