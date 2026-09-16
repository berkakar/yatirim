"""Geçici tanı scripti: fon_hisse_uyari.py'nin hesapladığı günlük değişim
yüzdelerinin BIST'in ~%10 fiyat marjı bandını aşması üzerine, yfinance'in
gerçekte hangi tarihli kapanışları karşılaştırdığını görmek için - hiçbir
şey kaydetmez, sadece yazdırır. İş bitince kaldırılacak.
"""
import yfinance as yf

TICKERS = [
    "ANELE", "SELEC", "TKNKA", "GESAN", "YKBNK", "TEHOL",
    "TRHOL", "ALKLC", "TERA", "KARCL", "THYAO", "DSTKF",
]

for t in TICKERS:
    print(f"\n=== {t}.IS ===")
    try:
        hist = yf.Ticker(f"{t}.IS").history(period="10d", interval="1d")
    except Exception as e:
        print(f"HATA: {e}")
        continue
    if hist is None or hist.empty:
        print("Boş veri.")
        continue
    closes = hist["Close"].dropna()
    for idx, val in closes.items():
        print(f"  {idx.date()} : {val:.4f}")
    if len(closes) >= 2:
        prev_close, last_close = float(closes.iloc[-2]), float(closes.iloc[-1])
        pct = (last_close - prev_close) / prev_close * 100
        print(f"  -> son iki satır: {closes.index[-2].date()} ({prev_close:.4f}) -> {closes.index[-1].date()} ({last_close:.4f}) = %{pct:.2f}")
