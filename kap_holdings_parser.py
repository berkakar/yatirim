"""KAP "Portföy Dağılım Raporu" PDF'lerinden en yüksek ağırlıklı hisse
senedi pozisyonlarını çıkarır.

Raporun tam tablo düzeni fon yönetim şirketine göre değişebildiği için
sezgisel bir yaklaşım kullanılıyor: PDF'teki tüm tablolar taranır, başlık
satırında hem bir "hisse/pay" hem de bir "%/oran/ağırlık" ifadesi geçen
tablo(lar) aday olarak işaretlenir, sayısal bir yüzde değeri ayrıştırılabilen
satırlar tutulur ve en yüksek N tanesi döndürülür.
"""
import io

import pdfplumber

_NAME_HEADER_MARKERS = ("hisse", "pay adı", "menkul kıymet", "varlık")
_PCT_HEADER_MARKERS = ("%", "oran", "ağırlık")
_TOTAL_ROW_MARKERS = ("toplam", "genel toplam", "ara toplam")


def _tr_lower(s: str) -> str:
    return (s or "").replace("İ", "i").replace("I", "ı").lower()


def _parse_percent(raw) -> float | None:
    text = str(raw or "").strip().replace("%", "").strip()
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        return None
    return value if 0 < value <= 100 else None


def _find_header_columns(header_row: list) -> tuple[int, int] | None:
    name_col = pct_col = None
    for i, cell in enumerate(header_row):
        text = _tr_lower(str(cell or ""))
        if name_col is None and any(m in text for m in _NAME_HEADER_MARKERS):
            name_col = i
        if pct_col is None and any(m in text for m in _PCT_HEADER_MARKERS):
            pct_col = i
    if name_col is not None and pct_col is not None and name_col != pct_col:
        return name_col, pct_col
    return None


def parse_top_holdings(pdf_bytes: bytes, top_n: int = 6) -> list[dict]:
    rows: list[tuple[str, float]] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                if not table or len(table) < 2:
                    continue
                cols = _find_header_columns(table[0])
                if not cols:
                    continue
                name_col, pct_col = cols
                for data_row in table[1:]:
                    if name_col >= len(data_row) or pct_col >= len(data_row):
                        continue
                    name = str(data_row[name_col] or "").strip()
                    pct = _parse_percent(data_row[pct_col])
                    if not name or pct is None:
                        continue
                    if _tr_lower(name).startswith(_TOTAL_ROW_MARKERS):
                        continue
                    rows.append((name, pct))

    if not rows:
        return []

    # Aynı hisse birden fazla tabloda/sayfada çıkabilir (ör. özet + detay) -
    # en yüksek oranı tutulur.
    best: dict[str, float] = {}
    for name, pct in rows:
        if name not in best or pct > best[name]:
            best[name] = pct

    ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    return [{"Hisse": name, "Oran %": round(pct, 2)} for name, pct in ranked[:top_n]]
