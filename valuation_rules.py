"""
Ucuzluk Skoru (Nihai Skor) puanlama kuralları - Değerleme modülü
(valuation.py), piyasa servisleri, veritabanındaki kayıtların yeniden
puanlanması (valuation_db.py) ve geçmişin bilançolardan yeniden hesabı
(valuation_history.py) aynı kuralları kullanır. Streamlit'e bağımlı değildir.

SCORE_VERSION kurallar değiştiğinde artırılır; valuation_db kayıtlı skorları
(valuation_scores, valuation_scores_daily) saklanan kriter değerlerinden bir
kez yeniden hesaplar.

Sürüm 2 (2026-10-08): PEG skordan çıkarıldı. Yahoo'nun PEG'i analist
beklentisine dayanır ve geçmişi yoktur. Kalan 13 kriter en fazla 90 puan
verir; Nihai Skor 0-100 ölçeğinde kalsın (70+ / 40- eşikleri) diye ham puan
100 / 90 ile ölçeklenip yuvarlanır. PEG tabloda bilgi olarak gösterilir.
"""

import pandas as pd

SCORE_VERSION = 2
RAW_MAX = 90

# Alt sektörde (iş modelinde) bu kadar veya daha az hisse varsa sektör medyanı/iskontosu
# anlamsız sayılır: hücrede "U" gösterilir, iskonto hesaba SMALL_SECTOR_DISCOUNT olarak girer.
SMALL_SECTOR_MAX = 3
SMALL_SECTOR_DISCOUNT = 1.0


def raw_points(row) -> int:
    """Kriter puanlarının toplamı (en fazla RAW_MAX). row: dict ya da pandas
    satırı; eksik değer o kriterden puan almaz."""
    score = 0

    # 1. Alt Sektör İskontosu (Ağırlık: 15 Puan)
    disc = row.get('Alt Sektör İskontosu %')
    if pd.notna(disc):
        if disc >= 30: score += 15
        elif 15 <= disc < 30: score += 10
        elif 0 <= disc < 15: score += 5

    # 2. EPS Büyümesi % (10 Puan)
    eps_g = row.get('EPS Büyümesi %')
    if pd.notna(eps_g):
        if eps_g >= 10.0: score += 10
        elif 5.0 <= eps_g < 10.0: score += 5

    # 3. Gelir Büyümesi % (10 Puan)
    rev_g = row.get('Gelir Büyümesi %')
    if pd.notna(rev_g):
        if rev_g >= 10.0: score += 10
        elif 5.0 <= rev_g < 10.0: score += 5

    # 4. Öz Sermaye Getirisi (ROE) % (10 Puan)
    roe = row.get('Öz Sermaye Getirisi (ROE) %')
    if pd.notna(roe):
        if roe >= 10.0: score += 10
        elif 5.0 <= roe < 10.0: score += 5

    # 5. Net Kar Marjı % (8 Puan)
    nm = row.get('Net Kar Marjı %')
    if pd.notna(nm):
        if nm >= 15.0: score += 8
        elif 8.0 <= nm < 15.0: score += 4

    # 6. Brüt Kar Marjı % (7 Puan)
    gm = row.get('Brüt Kar Marjı %')
    if pd.notna(gm):
        if 30.0 <= gm <= 60.0: score += 7
        elif gm > 60.0: score += 5

    # 7. Faiz Karşılama Oranı (7 Puan)
    ic = row.get('Faiz Karşılama Oranı')
    if pd.notna(ic):
        if ic >= 3.0: score += 7
        elif 1.5 <= ic < 3.0: score += 3

    # 8. Varlık Getirisi (ROA) % (6 Puan)
    roa = row.get('Varlık Getirisi (ROA) %')
    if pd.notna(roa):
        if 5.0 <= roa <= 10.0 or roa > 10.0: score += 6
        elif 2.0 <= roa < 5.0: score += 3

    # 9. Borç / Özsermaye (5 Puan)
    # Negatif oran, özsermayenin eksiye düştüğü (borcun varlıklardan fazla olduğu)
    # anlamına gelir - bu ciddi bir risk sinyalidir, "düşük borç" olarak ödüllendirilmemeli.
    de = row.get('Borç / Özsermaye')
    if pd.notna(de):
        if 0 <= de <= 0.5: score += 5
        elif 0.5 < de <= 1.0: score += 3

    # 10. Borç / Varlık % (4 Puan)
    da = row.get('Borç / Varlık %')
    if pd.notna(da):
        if da <= 50.0: score += 4
        elif 50.0 < da <= 70.0: score += 2

    # 11. Cari Oran (3 Puan)
    cr = row.get('Cari Oran')
    if pd.notna(cr):
        if 1.0 <= cr <= 2.0: score += 3
        elif cr > 2.0: score += 2

    # 12. Likidite Oranı (3 Puan)
    qr = row.get('Likidite Oranı')
    if pd.notna(qr):
        if qr >= 1.0: score += 3

    # 13. Varlık Devir Hızı (2 Puan)
    at = row.get('Varlık Devir Hızı')
    if pd.notna(at):
        if 1.0 <= at <= 2.0: score += 2
        elif at > 2.0: score += 1
    return score


def score_row(row) -> int:
    """Nihai Skor (0-100): kriter puanlarının 100 üzerinden karşılığı."""
    return int(round(raw_points(row) * 100 / RAW_MAX))
