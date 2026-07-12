import yfinance as yf
import pandas as pd

def get_atr_analysis(ticker: str, period: int = 14) -> dict:
    """
    Belirtilen hisse senedi için ATR hesaplar ve x2/x3 stop-loss seviyelerini döner.
    
    Args:
        ticker (str): Hisse senedi sembolü (örn: 'NVDA', 'THYAO.IS')
        period (int): ATR için baz alınacak gün sayısı (Varsayılan: 14)
        
    Returns:
        dict: Hisse bilgileri, güncel fiyat ve hesaplanan stop seviyeleri.
    """
    try:
        # Veri çekme (Hata yönetimi ile)
        ticker_obj = yf.Ticker(ticker)
        df = ticker_obj.history(period='6mo') # 6 aylık veri yeterli
        
        if df.empty:
            return {"error": f"'{ticker}' için veri bulunamadı. Sembolü kontrol edin."}

        # True Range hesaplama
        high = df['High']
        low = df['Low']
        close = df['Close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # ATR Hesaplama (Wilder's Smoothing)
        atr = tr.ewm(alpha=1/period, adjust=False).mean()
        
        last_price = close.iloc[-1]
        last_atr = atr.iloc[-1]
        
        return {
            "ticker": ticker.upper(),
            "last_price": round(last_price, 2),
            "atr_14": round(last_atr, 2),
            "sl_2x": round(last_price - (2 * last_atr), 2),
            "sl_3x": round(last_price - (3 * last_atr), 2)
        }
        
    except Exception as e:
        return {"error": f"Bir hata oluştu: {str(e)}"}

# --- Kullanım Örneği ---
if __name__ == "__main__":
    # İstediğin hisseyi buraya yazabilirsin
    hisseler = ['TTWO', 'NVDA', 'LLY', 'TEM', 'MU', 'CRWD', 'XLV', 'DVA', 'CRWV', 'NKE', 'ANET', 'SPCX', 'RKLB', 'ONTO', 'XLF','AMD', 'NOK', 'BE', 'DVA'] 
    
    for h in hisseler:
        analiz = get_atr_analysis(h)
        print(f"--- {analiz.get('ticker', 'HATA')} Analizi ---")
        if 'error' in analiz:
            print(analiz['error'])
        else:
            print(f"Fiyat: {analiz['last_price']} | ATR: {analiz['atr_14']} | Stop (2x): {analiz['sl_2x']} | Stop (3x): {analiz['sl_3x']}\n")
