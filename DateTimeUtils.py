import pandas as pd
from datetime import datetime, timedelta

def tr_tarihi_formatla(tarih_str):
    """
    '12 şubat 2023' veya '5 ocak 2024' gibi metinleri '2023-02-12' formatına çevirir.
    """
    aylar = {
        "ocak": "01", "şubat": "02", "mart": "03", "nisan": "04",
        "mayıs": "05", "haziran": "06", "temmuz": "07", "ağustos": "08",
        "eylül": "09", "ekim": "10", "kasım": "11", "aralık": "12"
    }
    try:
        parcalar = tarih_str.lower().split()
        gun = parcalar[0].zfill(2)
        ay_isim = parcalar[1]
        yil = parcalar[2]
        return f"{yil}-{aylar[ay_isim]}-{gun}"
    except (IndexError, KeyError):
        return None

def is_is_gunu(tarih_str):
    """
    Verilen 'YYYY-MM-DD' formatındaki tarihin iş günü olup olmadığını kontrol eder.
    """
    tarih = datetime.strptime(tarih_str, "%Y-%m-%d")
    return tarih.weekday() < 5

def is_gunu_ekle(baslangic_tarihi_str, x_gun):
    """
    Verilen 'YYYY-MM-DD' formatındaki tarihe X iş günü ekler.
    """
    tarih = datetime.strptime(baslangic_tarihi_str, "%Y-%m-%d")
    eklenen_gun = 0
    
    while eklenen_gun < x_gun:
        tarih += timedelta(days=1)
        if tarih.weekday() < 5:
            eklenen_gun += 1
            
    return tarih.strftime("%Y-%m-%d")

def filtrele_sadece_is_gunleri(veri_listesi):
    """
    Gemini'dan gelen JSON listesindeki verileri, sadece iş günü olanlarla filtreler.
    """
    return [satir for satir in veri_listesi if satir.get('gun') and is_is_gunu(satir.get('gun'))]

def tahmini_fiyat_hesapla_aralik(onceki_fiyat, tahmin_min, tahmin_max):
    """
    Yüzdelik değişim tahminlerini temizler ve gerçek fiyat aralığına çevirir.
    """
    if onceki_fiyat is None:
        return None, None
    
    # 1. Temizleme: % işaretini sil ve boşlukları kırp
    # str(tahmin_min) ile önce string olduğundan emin oluyoruz
    min_val = float(str(tahmin_min).replace('%', '').strip())
    max_val = float(str(tahmin_max).replace('%', '').strip())
    
    # 2. Hesaplama
    fiyat_min = onceki_fiyat * (1 + (min_val / 100))
    fiyat_max = onceki_fiyat * (1 + (max_val / 100))
    
    return round(fiyat_min, 4), round(fiyat_max, 4)

def analiz_sonucu_zenginlestir(veri_listesi, df_gercek_veriler, tarih_sutunu='Date', fiyat_sutunu='Close'):
    # 3. İndeksi (Date) normal bir sütuna çevir
    df_gercek_veriler = df_gercek_veriler.reset_index()

    for satir in veri_listesi:
        tarih_kilit = satir.get('gun')
        
        # 1. Önceki fiyatı bul
        gecmis_veriler = df_gercek_veriler[df_gercek_veriler[tarih_sutunu] < tarih_kilit]
        onceki_gercek_fiyat = float(gecmis_veriler.iloc[-1][fiyat_sutunu]) if not gecmis_veriler.empty else None
        
        # 2. Tahmin aralığını hesapla (YENİ)
        t_min, t_max = tahmini_fiyat_hesapla_aralik(
            onceki_gercek_fiyat, 
            satir.get('tahmin_min', 0), 
            satir.get('tahmin_max', 0)
        )
        satir['tahmin_fiyat_min'] = t_min
        satir['tahmin_fiyat_max'] = t_max
        
        # 3. Gerçek fiyatı eşleştir
        eslesme = df_gercek_veriler[df_gercek_veriler[tarih_sutunu] == tarih_kilit]
        gercek_fiyat = float(eslesme.iloc[0][fiyat_sutunu]) if not eslesme.empty else None
        satir['gercek_fiyat'] = gercek_fiyat
        
        # 4. Başarı Durumunu Belirleme (YENİ ARALIK MANTIĞI)
        guven_skoru = float(satir.get('guven_skoru', 0))
        
        if gercek_fiyat is not None and t_min is not None and t_max is not None:
            # Aralık kesişimi veya içinde olma kontrolü
            if t_min <= gercek_fiyat <= t_max:
                durum = 'Başarılı'
            else:
                durum = 'Başarısız'
            
            # Güven skoruna göre uyarı (Eğer güven düşükse başarı olsa bile işaretle)
            if guven_skoru < 0.5:
                durum = f"{durum} (Düşük Güven)"
                
            satir['tahmin_durumu'] = durum
        else:
            satir['tahmin_durumu'] = 'Hesaplanamadı'
            
    return veri_listesi

def bir_onceki_is_gunu(tarih_str):
    """
    Verilen tarihten önceki ilk iş gününü bulur. 
    Eğer tarih None ise None döner.
    """
    if tarih_str is None:
        return None
    
    try:
        dt = datetime.strptime(tarih_str, "%Y-%m-%d")
        
        # Bir gün çıkar
        dt = dt - timedelta(days=1)
        
        # Hafta sonu ise (Cumartesi veya Pazar), iş günü bulana kadar geriye git
        while dt.weekday() >= 5: # 5=Cumartesi, 6=Pazar
            dt = dt - timedelta(days=1)
            
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None

    return tarih_baslangic