"""Geçici son doğrulama - fon kodu -> fundOid zincirinin SON halkası:
TEFAS "Fon Adı" alanından KAP slug'ını (kod-transliterated-isim biçiminde)
üretip, bu slug'lı sayfanın gerçekten doğru fundOid'i içerdiğini KYA için
de (TPR dışında, farklı bir fon/yönetici) doğruluyoruz - üretim kodunda
kullanılacak transliterasyon fonksiyonunun genellenebilir olduğunu
kanıtlamak için.
"""
import re

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}

TR_MAP = str.maketrans({
    "İ": "i", "I": "i", "ı": "i", "Ş": "s", "ş": "s", "Ğ": "g", "ğ": "g",
    "Ü": "u", "ü": "u", "Ö": "o", "ö": "o", "Ç": "c", "ç": "c",
})


def slugify(code: str, fund_name: str) -> str:
    name = fund_name.translate(TR_MAP).lower()
    name = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    return f"{code.lower()}-{name}"


# Gerçek KAP companyTitle'ları (disclosure JSON'undan doğrulanmış):
CASES = [
    ("TPR", "İŞ PORTFÖY PY HİSSE SENEDİ (TL) ÖZEL FONU (HİSSE SENEDİ YOĞUN FON)", "33E5FED7ECE300EAE0530A4A622B2AEA"),
    ("KYA", "KARE PORTFÖY HİSSE SENEDİ FONU (HİSSE SENEDİ YOĞUN FON)", "33E5FED7E5D300EAE0530A4A622B2AEA"),
]

for code, name, known_oid in CASES:
    slug = slugify(code, name)
    url = f"https://www.kap.org.tr/tr/fon-bilgileri/genel/{slug}"
    r = requests.get(url, headers=HEADERS, timeout=20)
    found = known_oid in r.text
    print(f"{code}: slug={slug!r}")
    print(f"  -> {url}")
    print(f"  status={r.status_code}, len={len(r.text)}, correct_fundOid_present={found}")
