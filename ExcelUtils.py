import os
import pandas as pd
from datetime import datetime

class ExcelUtils:
    def __init__(self, dosya_adi="haberlerVeAnalizler.xlsx"):
        self.dosya_adi = dosya_adi

    def _yaz(self, haber_verisi, analiz_verisi):
        """Haber ve Analiz verisini aynı sayfada birleştirip yazar."""
        
        # 1. Haber DataFrame

        if isinstance(haber_verisi, dict):
            df_haber = pd.DataFrame([haber_verisi])
        else:
            df_haber = pd.DataFrame(haber_verisi)

        df_haber['tarih_saat'] = pd.to_datetime(df_haber['tarih_saat'])
        
        df_haber = df_haber.rename(columns={"tarih_saat": "Tarih", 
            "haber": "Haber Özeti", 
            "etkiEdecekTarih": "Etki Edecek Tarih", 
            "onemPuani": "Önem Puanı", 
            "etkiYuzdesi": "Etki Yüzdesi"})
        
        
        # 2. Analiz DataFrame
         # Verinin liste mi yoksa dict mi olduğuna bakmaksızın çalışan güvenli yol:
        if isinstance(analiz_verisi, dict):
            df_analiz = pd.DataFrame([analiz_verisi]) # Listeye çevirerek index sorununu çözdük
        else:
            df_analiz = pd.DataFrame(analiz_verisi)

        # Sütunları yeniden isimlendirirken artık 'gun' sütununu kullanacağız
        df_analiz = df_analiz.rename(columns={"hisseAdi": "Hisse Adı", 
            "gun": "Gun",
            "hafta_ici": "Hafta İçi",
            "etki_yonu": "Etki Yönü",
            "tahmin_min": "Tahmin Min (%)",
            "tahmin_max": "Tahmin Max (%)",
            "guven_skoru": "Güven Skoru",
            "guven_gerekcesi": "Güven Gerekçesi",
            "analiz_mantigi" : "Analiz Mantığı"
        })

        sayfa_adi = f"Rapor_{datetime.now().strftime('%m%d')}"

        try:
            # Mode 'w' (yeni dosya) veya 'a' (mevcut dosyaya ekleme)
            mode = "a" if os.path.exists(self.dosya_adi) else "w"
            
            with pd.ExcelWriter(self.dosya_adi, mode=mode, engine="openpyxl", if_sheet_exists="overlay") as writer:
                # Haberleri yaz
                df_haber.to_excel(writer, sheet_name=sayfa_adi, index=False, startrow=0)
                
                # Analizleri haberlerin altına yaz (Haber sayısı kadar boşluk bırak)
                baslangic_satiri = len(df_haber) + 2 
                df_analiz.to_excel(writer, sheet_name=sayfa_adi, index=False, startrow=baslangic_satiri)
            
            print(f"✅ Haberler ve Analizler '{sayfa_adi}' sayfasına başarıyla birleştirildi.")
            
        except Exception as e:
            print(f"❌ Hata: {e}")

# Kullanım:
# excel = ExcelUtils()
# excel._yaz(haber_listesi, analiz_listesi)