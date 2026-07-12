import os
from google import genai
from google.genai import types
import requests

client = genai.Client(api_key="AIzaSyDBDBUrZ47UM6GFWAGyByGotrpS8RoRRfA")

configVeritabanci = types.GenerateContentConfig(
    system_instruction="Sen deneyimli bir veritabacısn ve doğru veriyle ilgili sorulara yanıt verirsin.",   
)

# 1. Haber Bulma Ajanı Fonksiyonu
def yapayZekayaSoruSor(soru):
    prompt = f"""
    {soru}

    """
    response = client.models.generate_content(
    model='gemini-2.5-flash',
    contents=prompt,
    config=configVeritabanci   
    )
    return response.text

# 1. ADIM: GDELT'ten veri çeken fonksiyon (Yapay zekanın kullanacağı araç)
def gdelt_haber_ara(anahtar_kelime, limit=5):
    url = f"https://api.gdeltproject.org/api/v2/doc/doc?query={anahtar_kelime}&mode=artlist&maxrecords={limit}&format=json"
    response = requests.get(url).json()
    articles = response.get("articles", [])
    
    # Sadece başlık ve kaynakları metin olarak birleştirelim
    haber_metni = ""
    for art in articles:
        haber_metni += f"Başlık: {art['title']}\nKaynak: {art['domain']}\n---\n"
    return haber_metni

# 2. ADIM: Sizin doğal dildeki sorunuz
tarih = "12 Mart 2025"
kullanici_sorusu = f"Bana küresel ekonomi ve finans ile ilgili {tarih} tarihindeki haberleri özetle."

# 3. ADIM: Yapay zekaya sorudan anahtar kelime çıkarma görevi veriyoruz
# (Gelişmiş projelerde bunu 'Function Calling / Tool Use' ile otomatik yaparsınız)
llm_prompt = f"Kullanıcı şu soruyu sordu: '{kullanici_sorusu}'. Bu soruya cevap bulabilmek için GDELT sisteminde aranacak en mantıklı İngilizce anahtar kelimeleri yaz."

cevap = yapayZekayaSoruSor(llm_prompt) 

print(f"Yapay zekanın çıkardığı anahtar kelime: {cevap}")

# 4. ADIM: GDELT'ten ham veriyi çekiyoruz
#ham_haberler = gdelt_haber_ara(aranacak_kelime)

# 5. ADIM: Yapay zekaya ham veriyi analiz ettiriyoruz
#analiz_prompt = f"Sana GDELT'ten gelen son haber listesini veriyorum. Bunları analiz et ve kullanıcının '{kullanici_sorusu}' sorusuna yanıt verecek şekilde Türkçe bir özet çıkar.\n\nHaberler:\n{ham_haberler}"

# LLM bu aşamada ham veriyi alır ve size tıpkı bir analist gibi cevap üretir.
#print(llm.generate(analiz_prompt))



