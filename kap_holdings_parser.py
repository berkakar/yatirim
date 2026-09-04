"""KAP "Portföy Dağılım Raporu" PDF'lerinden en yüksek ağırlıklı hisse
senedi pozisyonlarını çıkarır.

Rapor tablosunun sütun başlıkları pdfplumber'ın extract_tables()'ında tek
bir karışık blok halinde geliyor (çok satırlı, sarmalanmış başlıklar) -
bu yüzden başlık/sütun eşleştirmesi yerine ham metin üzerinde satır bazlı
bir düzenli ifade kullanılıyor. Gerçek rapor satırları gözlemlenen biçimde
her zaman "<HİSSE KODU> TL <İhraçcı adı...> <ISIN> <sayısal alanlar...>"
şeklinde tek satırda başlıyor (ihraçcı adı sonraki satırlara taşabilir,
ama o taşan kısımlar yeni bir satır regex'iyle eşleşmediği için otomatik
atlanıyor). Satırdaki son sayısal değer "(FTD'YE GÖRE)" yüzdesi - fonun
TOPLAM değerine göre ağırlık, en standart/karşılaştırılabilir ölçüt. Bu
düzen İş Portföy ve Kare Portföy'ün gerçek raporlarında (iki farklı
yönetici) doğrulandı - SPK'nın standart aylık rapor şablonu olduğu için
diğer yöneticilerde de aynı olması bekleniyor.
"""
import io
import re

import pdfplumber

_ROW_RE = re.compile(
    r"^(?P<ticker>[A-ZÇĞİÖŞÜ0-9]{2,6})\s+[A-Z]{2,4}\s+.*?\s(?P<isin>TR[A-Z0-9]{10})\s+(?P<rest>[\d.,\-\s/]+)$"
)


def _parse_tr_number(token: str) -> float | None:
    token = token.strip()
    if not token or "/" in token:
        return None
    token = token.replace(".", "").replace(",", ".")
    try:
        return float(token)
    except ValueError:
        return None


def parse_top_holdings(pdf_bytes: bytes, top_n: int = 6) -> list[dict]:
    best: dict[str, float] = {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.split("\n"):
                m = _ROW_RE.match(line.strip())
                if not m:
                    continue
                numbers = [n for t in m.group("rest").split() if (n := _parse_tr_number(t)) is not None]
                if len(numbers) < 3:
                    continue
                pct = numbers[-1]
                if not (0 < abs(pct) <= 100):
                    continue
                ticker = m.group("ticker")
                if ticker not in best or pct > best[ticker]:
                    best[ticker] = pct

    ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    return [{"Hisse": name, "Oran %": round(pct, 2)} for name, pct in ranked[:top_n]]
