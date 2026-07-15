
MARKETAUX_API_KEY = "T5qEhQJkLDUc5muOxmv3FGoCo0trEwLatjUJF37k"

from urllib import response

import requests
from typing import List
import json

import testData
import streamlit as st
  
MARKETAUX_API_KEY = st.secrets["MARKETAUX_API_KEY"]


# Makro kategorilerini ve anahtar kelimelerini tanımla
macro_queries = {
    "monetary_policy": "('Federal Reserve' OR 'FOMC' OR 'Interest Rates')",
    "inflation": "('CPI' OR 'Inflation' OR 'PPI')",
}


def haberleri_cek_hibrit(published_on="2025-03-12", symbols="NVDA", limit=50, kanal="1",category="monetary_policy"):
    # 0. Kanal: Global Economic Values (Buraya dilediğin anahtar kelimeyi ekle)
    query = macro_queries[category]
    url_global = f"https://api.marketaux.com/v1/news/all?search={query}&published_on={published_on}&limit={limit}&api_token={MARKETAUX_API_KEY}"

    # 1. Kanal: Şirket odaklı
    symbols = "NVDA"

    # 2. Kanal: Sektör ve Makro odaklı (Buraya dilediğin anahtar kelimeyi ekle)
    sectoral = "semiconductor+(China|demand)|hardware technology"
    
    # İki farklı sorgu atalım (veya Marketaux destekliyorsa tek sorguda birleştirelim)
    url_symbols = f"https://api.marketaux.com/v1/news/all?symbols={symbols}&published_on={published_on}&limit={limit}&api_token={MARKETAUX_API_KEY}"
    url_sectoral = f"https://api.marketaux.com/v1/news/all?search={sectoral}&published_on={published_on}&limit={limit}&api_token={MARKETAUX_API_KEY}"   
  
    print(f"Symbols URL: {url_symbols}")
    print(f"Keywords URL: {url_sectoral}") 
    
    test_mode = False # Gerçek veri çekmek için False yapın, test verisi kullanmak için True yapın
    if test_mode == False:
        if kanal == "0":
            response = requests.get(url_global)
        elif kanal == "1":
            response = requests.get(url_symbols)
        elif kanal == "2":
            response = requests.get(url_sectoral)    
            
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"API Hatası: {response.status_code}")
   

def ayristir_marketaux_verisi(json_input):
    """
    Marketaux verisini alır, entities ve sentiment skorlarını 
    haberlerle eşleştirip düz bir liste döndürür.
    """
    # Girdi string ise dict'e çevir, değilse olduğu gibi kullan
    data = json.loads(json_input) if isinstance(json_input, str) else json_input
    
    haberler = data.get('data', [])
    duzlestirilmis_liste = []

    for haber in haberler:
        # Ortak bilgileri al
        base_info = {
            "title": haber.get("title"),
            "published_at": haber.get("published_at"),
            "source": haber.get("source"),
            "url": haber.get("url")
        }
        
        # Entities listesini kontrol et
        entities = haber.get("entities", [])
        
        if not entities:
            # Hiç entity yoksa haberi yine de ekle ama entity bilgilerini None yap
            base_info.update({"symbol": None, "name": None, "sentiment_score": None})
            duzlestirilmis_liste.append(base_info)
        else:
            # Her bir entity için ayrı bir kayıt oluştur (Haber başına birden fazla varlık olabilir)
            for entity in entities:
                entry = base_info.copy()
                entry.update({
                    "symbol": entity.get("symbol"),
                    "name": entity.get("name"),
                    "sentiment_score": entity.get("sentiment_score")
                })
                duzlestirilmis_liste.append(entry)
                
    return json.dumps(duzlestirilmis_liste, indent=4, ensure_ascii=False)


import pandas as pd
import json

def json_to_markdown_table(json_input):
    """
    JSON verisini alır ve Markdown tablosu olarak döner.
    Input: JSON string veya Python list/dict
    Output: Markdown formatlı tablo string'i
    """
    try:
        # 1. Eğer input string ise, önce JSON nesnesine çevir
        if isinstance(json_input, str):
            data = json.loads(json_input)
        else:
            data = json_input
            
        # 2. Veri boş mu kontrol et
        if not data:
            return ""
            
        # 3. Pandas DataFrame oluştur
        df = pd.DataFrame(data)
        
        # 4. Markdown formatına çevir (index=False ile sayıları gizleriz)
        return df.to_markdown(index=False)
        
    except json.JSONDecodeError:
        return "Hata: Geçersiz JSON formatı."
    except Exception as e:
        return f"Bir hata oluştu: {str(e)}"
    

