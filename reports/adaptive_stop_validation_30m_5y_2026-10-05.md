# Akıllı Dinamik Stop doğrulaması (05.10.2026)

Kayma %0.05/taraf; özsermaye % = risk bazlı adet (%0.5 risk, %20 pozisyon tavanı) ile işlemin özsermayeye katkısı.

## 30 dakikalık bar (veri paketi, normal seans)

30 hisse, 384199 bar; bölme: **hisse başına veri ortası (medyan 2024-04-17)** (eğitim 29124 giriş, test 29980 giriş).

**Eğitimde taranan 216 ayar** - seçilen (komşularıyla birlikte sağlam): `{'trail_atr_mult': 3.5, 'trail_start_r': 2.5, 'tighten_per_r': 0.5, 'er_weight': 0.5, 'breakeven_r': 0.0, 'max_atr_mult': 2.0}`; eğitimdeki en iyi tekil hücre: `{'trail_atr_mult': 3.5, 'trail_start_r': 2.5, 'tighten_per_r': 0.5, 'er_weight': 0.5, 'breakeven_r': 0.0, 'max_atr_mult': 2.0}` (ort. özs. +0.000%).

**Yıllara göre ortalama özsermaye katkısı (% / işlem, tüm girişler)**

| Yıl | İşlem | Breakeven+Yapısal (kayıtlı ayar, canlı) | Beklemeli ve İz Süren (kayıtlı) | Oynaklık (ATR) Stop (kayıtlı) | Akıllı Dinamik - kod varsayılanı |
|---|---|---|---|---|---|
| 2021 | 1770 | -0.033 | -0.048 | -0.015 | -0.017 |
| 2022 | 11988 | -0.036 | -0.063 | -0.032 | -0.042 |
| 2023 | 11759 | +0.031 | +0.045 | +0.020 | +0.017 |
| 2024 | 11607 | +0.023 | +0.045 | +0.014 | +0.037 |
| 2025 | 12050 | -0.008 | +0.005 | +0.011 | +0.000 |
| 2026 | 9930 | +0.022 | +0.037 | +0.022 | +0.064 |

**TEST dönemi - Tüm girişler** (29980 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 29979 | %38 | +0.05 | +0.014 | +434.3 | +368.4 | 1.06 | 491.6 | %1.5 | %51 | 21.0 |
| Beklemeli ve İz Süren (kayıtlı) | 29979 | %47 | +0.07 | +0.034 | +1017.2 | +969.0 | 1.12 | 696.8 | %4.0 | %50 | 57.4 |
| Oynaklık (ATR) Stop (kayıtlı) | 29979 | %33 | +0.04 | +0.018 | +532.0 | +470.3 | 1.09 | 326.5 | %1.2 | %54 | 11.8 |
| Akıllı Dinamik - kod varsayılanı | 29979 | %30 | +0.08 | +0.036 | +1078.0 | +999.5 | 1.15 | 466.5 | %1.4 | %60 | 18.4 |
| Akıllı Dinamik - eğitimde seçilen | 29979 | %32 | +0.06 | +0.031 | +936.5 | +858.1 | 1.12 | 416.6 | %1.4 | %67 | 17.5 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.021%** (%95 GA +0.008 … +0.036, anlamlı ✅); R farkı +0.03 (-0.01 … +0.08)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.017%** (%95 GA +0.001 … +0.033, anlamlı ✅); R farkı +0.01 (-0.04 … +0.06)

**TEST dönemi - Trend girişleri** (6536 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 6536 | %38 | +0.02 | +0.005 | +34.4 | -4.5 | 1.02 | 178.9 | %1.5 | %49 | 22.1 |
| Beklemeli ve İz Süren (kayıtlı) | 6536 | %46 | +0.05 | +0.024 | +159.2 | +118.2 | 1.08 | 170.4 | %4.0 | %51 | 63.0 |
| Oynaklık (ATR) Stop (kayıtlı) | 6536 | %31 | +0.01 | +0.013 | +85.0 | +61.3 | 1.07 | 75.0 | %1.1 | %54 | 11.6 |
| Akıllı Dinamik - kod varsayılanı | 6536 | %30 | +0.03 | +0.030 | +195.3 | +150.3 | 1.13 | 118.6 | %1.3 | %60 | 18.9 |
| Akıllı Dinamik - eğitimde seçilen | 6536 | %31 | +0.00 | +0.017 | +109.9 | +66.9 | 1.07 | 130.1 | %1.3 | %69 | 18.2 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.025%** (%95 GA +0.005 … +0.045, anlamlı ✅); R farkı +0.02 (-0.05 … +0.09)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.012%** (%95 GA -0.011 … +0.035, anlamlı değil); R farkı -0.01 (-0.09 … +0.06)

**TEST dönemi - Rastgele girişler** (12766 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 12766 | %39 | +0.08 | +0.023 | +289.5 | +239.1 | 1.11 | 168.1 | %1.5 | %50 | 22.5 |
| Beklemeli ve İz Süren (kayıtlı) | 12766 | %48 | +0.08 | +0.040 | +512.7 | +472.4 | 1.14 | 314.3 | %4.0 | %50 | 62.3 |
| Oynaklık (ATR) Stop (kayıtlı) | 12766 | %33 | +0.05 | +0.020 | +260.6 | +221.4 | 1.11 | 136.8 | %1.1 | %54 | 12.0 |
| Akıllı Dinamik - kod varsayılanı | 12766 | %30 | +0.09 | +0.033 | +426.6 | +365.3 | 1.15 | 191.3 | %1.2 | %60 | 18.1 |
| Akıllı Dinamik - eğitimde seçilen | 12766 | %32 | +0.06 | +0.029 | +364.8 | +290.0 | 1.12 | 179.5 | %1.2 | %67 | 17.0 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.011%** (%95 GA -0.003 … +0.025, anlamlı değil); R farkı +0.01 (-0.04 … +0.06)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.006%** (%95 GA -0.010 … +0.022, anlamlı değil); R farkı -0.02 (-0.07 … +0.03)

**TEST dönemi - Canlı sinyal girişleri** (10678 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 10677 | %37 | +0.03 | +0.010 | +110.5 | +57.0 | 1.04 | 192.3 | %1.5 | %53 | 18.6 |
| Beklemeli ve İz Süren (kayıtlı) | 10677 | %47 | +0.06 | +0.032 | +345.4 | +300.9 | 1.11 | 227.9 | %4.0 | %51 | 48.2 |
| Oynaklık (ATR) Stop (kayıtlı) | 10677 | %34 | +0.05 | +0.017 | +186.4 | +137.6 | 1.08 | 141.2 | %1.4 | %55 | 11.8 |
| Akıllı Dinamik - kod varsayılanı | 10677 | %31 | +0.11 | +0.043 | +456.0 | +405.2 | 1.16 | 201.7 | %1.6 | %60 | 18.5 |
| Akıllı Dinamik - eğitimde seçilen | 10677 | %32 | +0.09 | +0.043 | +461.8 | +399.1 | 1.16 | 167.0 | %1.6 | %67 | 17.7 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.032%** (%95 GA +0.012 … +0.053, anlamlı ✅); R farkı +0.07 (+0.01 … +0.14)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.033%** (%95 GA +0.011 … +0.057, anlamlı ✅); R farkı +0.06 (-0.01 … +0.13)

_Süre: 2236 sn_


## Premium Buy Point'in canlı kuralına (Oynaklık (ATR) Stop) göre eşleştirilmiş fark (test dönemi)

| Periyot | Breakeven+Yapısal − ATR Stop | Akıllı Dinamik − ATR Stop |
|---|---|---|
| Günlük (2024-05 → 2026-10, 7412 giriş) | **+0.180%** (%95 GA +0.060 … +0.331) ✅ | **+0.108%** (+0.063 … +0.155) ✅ |
| 30dk normal seans (2024-04 → 2026-10, 29980 giriş) | −0.003% (−0.017 … +0.011) | **+0.018%** (+0.006 … +0.032) ✅ |
