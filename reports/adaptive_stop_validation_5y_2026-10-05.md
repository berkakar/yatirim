# Akıllı Dinamik Stop doğrulaması (05.10.2026)

Kayma %0.05/taraf; özsermaye % = risk bazlı adet (%0.5 risk, %20 pozisyon tavanı) ile işlemin özsermayeye katkısı.

## Günlük bar (veri paketi)

30 hisse, 36210 bar; bölme: **hisse başına veri ortası (medyan 2024-05-03)** (eğitim 7136 giriş, test 7412 giriş).

**Eğitimde taranan 216 ayar** - seçilen (komşularıyla birlikte sağlam): `{'trail_atr_mult': 7.0, 'trail_start_r': 2.5, 'tighten_per_r': 0.25, 'er_weight': 0.0, 'breakeven_r': 0.0, 'max_atr_mult': 3.0}`; eğitimdeki en iyi tekil hücre: `{'trail_atr_mult': 7.0, 'trail_start_r': 2.5, 'tighten_per_r': 0.25, 'er_weight': 0.0, 'breakeven_r': 0.0, 'max_atr_mult': 3.0}` (ort. özs. +0.126%).

**Yıllara göre ortalama özsermaye katkısı (% / işlem, tüm girişler)**

| Yıl | İşlem | Breakeven+Yapısal (kayıtlı ayar, canlı) | Beklemeli ve İz Süren (kayıtlı) | Oynaklık (ATR) Stop (kayıtlı) | Akıllı Dinamik - kod varsayılanı |
|---|---|---|---|---|---|
| 2021 | 194 | -0.212 | -0.084 | -0.166 | -0.267 |
| 2022 | 2654 | -0.092 | -0.062 | -0.087 | -0.108 |
| 2023 | 3041 | +0.253 | +0.219 | +0.170 | +0.274 |
| 2024 | 3150 | +0.194 | +0.162 | +0.057 | +0.179 |
| 2025 | 3034 | +0.410 | +0.267 | +0.097 | +0.133 |
| 2026 | 2475 | +0.194 | +0.147 | +0.160 | +0.298 |

**TEST dönemi - Tüm girişler** (7412 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 7409 | %32 | +0.96 | +0.289 | +2142.4 | +1789.0 | 2.00 | 220.6 | %1.5 | %61 | 7.7 |
| Beklemeli ve İz Süren (kayıtlı) | 7409 | %44 | +0.42 | +0.208 | +1538.0 | +1334.5 | 1.66 | 174.8 | %4.0 | %54 | 11.0 |
| Oynaklık (ATR) Stop (kayıtlı) | 7409 | %44 | +0.23 | +0.109 | +807.9 | +778.8 | 1.37 | 227.9 | %4.9 | %51 | 12.2 |
| Akıllı Dinamik - kod varsayılanı | 7409 | %33 | +0.45 | +0.217 | +1606.3 | +1569.1 | 1.63 | 190.1 | %5.4 | %58 | 19.5 |
| Akıllı Dinamik - eğitimde seçilen | 7409 | %31 | +0.56 | +0.271 | +2004.2 | +1957.3 | 1.74 | 290.7 | %6.2 | %65 | 31.2 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **-0.072%** (%95 GA -0.209 … +0.037, anlamlı değil); R farkı -0.51 (-0.99 … -0.14)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **-0.019%** (%95 GA -0.136 … +0.081, anlamlı değil); R farkı -0.40 (-0.82 … -0.06)

**TEST dönemi - Trend girişleri** (1674 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 1674 | %32 | +0.96 | +0.286 | +479.5 | +277.6 | 2.05 | 57.0 | %1.5 | %60 | 7.7 |
| Beklemeli ve İz Süren (kayıtlı) | 1674 | %46 | +0.48 | +0.239 | +400.5 | +253.7 | 1.80 | 37.4 | %4.0 | %51 | 11.2 |
| Oynaklık (ATR) Stop (kayıtlı) | 1674 | %44 | +0.24 | +0.110 | +184.3 | +160.8 | 1.39 | 50.3 | %4.6 | %50 | 11.8 |
| Akıllı Dinamik - kod varsayılanı | 1674 | %33 | +0.41 | +0.201 | +336.2 | +311.0 | 1.60 | 45.0 | %5.5 | %58 | 19.7 |
| Akıllı Dinamik - eğitimde seçilen | 1674 | %30 | +0.40 | +0.196 | +327.4 | +291.4 | 1.54 | 75.6 | %6.6 | %64 | 31.8 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **-0.086%** (%95 GA -0.308 … +0.089, anlamlı değil); R farkı -0.54 (-1.31 … +0.05)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **-0.091%** (%95 GA -0.308 … +0.095, anlamlı değil); R farkı -0.55 (-1.32 … +0.04)

**TEST dönemi - Rastgele girişler** (3497 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 3497 | %33 | +0.79 | +0.238 | +833.0 | +562.5 | 1.88 | 109.7 | %1.5 | %60 | 7.8 |
| Beklemeli ve İz Süren (kayıtlı) | 3497 | %44 | +0.38 | +0.190 | +666.1 | +511.2 | 1.62 | 89.2 | %4.0 | %53 | 11.3 |
| Oynaklık (ATR) Stop (kayıtlı) | 3497 | %44 | +0.21 | +0.099 | +347.5 | +319.2 | 1.34 | 127.2 | %4.9 | %51 | 12.7 |
| Akıllı Dinamik - kod varsayılanı | 3497 | %34 | +0.45 | +0.217 | +759.1 | +722.0 | 1.64 | 101.5 | %5.4 | %57 | 20.2 |
| Akıllı Dinamik - eğitimde seçilen | 3497 | %31 | +0.59 | +0.281 | +981.3 | +935.5 | 1.77 | 140.4 | %6.1 | %65 | 31.9 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **-0.021%** (%95 GA -0.146 … +0.085, anlamlı değil); R farkı -0.34 (-0.75 … +0.00)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.042%** (%95 GA -0.064 … +0.139, anlamlı değil); R farkı -0.21 (-0.58 … +0.11)

**TEST dönemi - Canlı sinyal girişleri** (2241 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 2238 | %32 | +1.24 | +0.371 | +829.9 | +509.2 | 2.12 | 84.4 | %1.5 | %61 | 7.8 |
| Beklemeli ve İz Süren (kayıtlı) | 2238 | %42 | +0.42 | +0.211 | +471.4 | +322.6 | 1.62 | 66.0 | %4.0 | %57 | 10.4 |
| Oynaklık (ATR) Stop (kayıtlı) | 2238 | %44 | +0.26 | +0.123 | +276.2 | +247.2 | 1.41 | 69.3 | %5.1 | %52 | 12.0 |
| Akıllı Dinamik - kod varsayılanı | 2238 | %33 | +0.47 | +0.228 | +510.9 | +478.0 | 1.65 | 69.1 | %5.3 | %59 | 18.2 |
| Akıllı Dinamik - eğitimde seçilen | 2238 | %30 | +0.64 | +0.311 | +695.4 | +651.3 | 1.83 | 108.4 | %5.8 | %65 | 29.5 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **-0.143%** (%95 GA -0.404 … +0.054, anlamlı değil); R farkı -0.76 (-1.64 … -0.09)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **-0.060%** (%95 GA -0.289 … +0.114, anlamlı değil); R farkı -0.60 (-1.41 … +0.02)

## 30 dakikalık bar (veri paketi, normal seans)

30 hisse, 77945 bar; bölme: **hisse başına veri ortası (medyan 2026-04-13)** (eğitim 12992 giriş, test 13709 giriş).

**Eğitimde taranan 216 ayar** - seçilen (komşularıyla birlikte sağlam): `{'trail_atr_mult': 3.5, 'trail_start_r': 1.0, 'tighten_per_r': 0.5, 'er_weight': 0.5, 'breakeven_r': 0.0, 'max_atr_mult': 2.0}`; eğitimdeki en iyi tekil hücre: `{'trail_atr_mult': 3.5, 'trail_start_r': 1.0, 'tighten_per_r': 0.25, 'er_weight': 0.5, 'breakeven_r': 0.0, 'max_atr_mult': 2.0}` (ort. özs. -0.001%).

**Yıllara göre ortalama özsermaye katkısı (% / işlem, tüm girişler)**

| Yıl | İşlem | Breakeven+Yapısal (kayıtlı ayar, canlı) | Beklemeli ve İz Süren (kayıtlı) | Oynaklık (ATR) Stop (kayıtlı) | Akıllı Dinamik - kod varsayılanı |
|---|---|---|---|---|---|
| 2025 | 5486 | -0.013 | +0.055 | -0.001 | -0.001 |
| 2026 | 21215 | +0.015 | +0.033 | +0.016 | +0.053 |

**TEST dönemi - Tüm girişler** (13709 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 13708 | %37 | +0.10 | +0.031 | +424.2 | +375.8 | 1.13 | 322.5 | %1.5 | %52 | 19.3 |
| Beklemeli ve İz Süren (kayıtlı) | 13708 | %47 | +0.14 | +0.068 | +938.3 | +889.9 | 1.24 | 407.0 | %4.0 | %49 | 48.4 |
| Oynaklık (ATR) Stop (kayıtlı) | 13708 | %34 | +0.06 | +0.028 | +378.5 | +321.9 | 1.13 | 261.3 | %1.3 | %54 | 11.6 |
| Akıllı Dinamik - kod varsayılanı | 13708 | %31 | +0.21 | +0.082 | +1122.5 | +1043.4 | 1.32 | 259.9 | %1.5 | %60 | 18.0 |
| Akıllı Dinamik - eğitimde seçilen | 13708 | %38 | +0.08 | +0.036 | +488.9 | +410.7 | 1.16 | 218.7 | %1.5 | %51 | 11.2 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.051%** (%95 GA +0.013 … +0.093, anlamlı ✅); R farkı +0.10 (-0.00 … +0.22)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.005%** (%95 GA -0.027 … +0.036, anlamlı değil); R farkı -0.02 (-0.13 … +0.08)

**TEST dönemi - Trend girişleri** (3567 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 3567 | %37 | +0.06 | +0.019 | +66.7 | +40.9 | 1.08 | 88.0 | %1.5 | %52 | 19.2 |
| Beklemeli ve İz Süren (kayıtlı) | 3567 | %47 | +0.15 | +0.074 | +264.2 | +216.7 | 1.26 | 117.5 | %4.0 | %49 | 48.7 |
| Oynaklık (ATR) Stop (kayıtlı) | 3567 | %34 | +0.05 | +0.026 | +91.8 | +68.0 | 1.12 | 84.5 | %1.3 | %54 | 11.6 |
| Akıllı Dinamik - kod varsayılanı | 3567 | %30 | +0.15 | +0.070 | +248.2 | +186.9 | 1.26 | 79.2 | %1.5 | %60 | 18.4 |
| Akıllı Dinamik - eğitimde seçilen | 3567 | %37 | +0.05 | +0.032 | +113.2 | +68.6 | 1.14 | 73.1 | %1.5 | %51 | 11.5 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.051%** (%95 GA +0.000 … +0.106, anlamlı ✅); R farkı +0.09 (-0.05 … +0.23)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.013%** (%95 GA -0.027 … +0.051, anlamlı değil); R farkı -0.01 (-0.14 … +0.11)

**TEST dönemi - Rastgele girişler** (7667 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 7667 | %37 | +0.11 | +0.032 | +245.3 | +197.2 | 1.14 | 168.2 | %1.5 | %52 | 20.0 |
| Beklemeli ve İz Süren (kayıtlı) | 7667 | %48 | +0.14 | +0.071 | +541.8 | +493.6 | 1.26 | 201.8 | %4.0 | %48 | 51.2 |
| Oynaklık (ATR) Stop (kayıtlı) | 7667 | %34 | +0.04 | +0.024 | +183.9 | +142.8 | 1.11 | 149.8 | %1.3 | %54 | 11.6 |
| Akıllı Dinamik - kod varsayılanı | 7667 | %31 | +0.20 | +0.077 | +593.0 | +515.1 | 1.32 | 156.1 | %1.4 | %60 | 17.8 |
| Akıllı Dinamik - eğitimde seçilen | 7667 | %38 | +0.07 | +0.031 | +236.8 | +173.6 | 1.14 | 125.4 | %1.4 | %51 | 11.1 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.045%** (%95 GA +0.012 … +0.084, anlamlı ✅); R farkı +0.09 (-0.01 … +0.19)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **-0.001%** (%95 GA -0.032 … +0.030, anlamlı değil); R farkı -0.03 (-0.14 … +0.06)

**TEST dönemi - Canlı sinyal girişleri** (2475 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 2474 | %38 | +0.15 | +0.045 | +112.1 | +80.1 | 1.19 | 77.1 | %1.5 | %53 | 17.2 |
| Beklemeli ve İz Süren (kayıtlı) | 2474 | %46 | +0.11 | +0.053 | +132.3 | +93.5 | 1.18 | 92.6 | %4.0 | %50 | 39.4 |
| Oynaklık (ATR) Stop (kayıtlı) | 2474 | %37 | +0.11 | +0.042 | +102.8 | +61.9 | 1.18 | 63.8 | %1.6 | %54 | 11.6 |
| Akıllı Dinamik - kod varsayılanı | 2474 | %32 | +0.31 | +0.114 | +281.3 | +234.2 | 1.42 | 54.2 | %1.8 | %59 | 18.0 |
| Akıllı Dinamik - eğitimde seçilen | 2474 | %39 | +0.14 | +0.056 | +138.9 | +109.4 | 1.24 | 49.7 | %1.8 | %50 | 11.2 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.068%** (%95 GA +0.015 … +0.126, anlamlı ✅); R farkı +0.16 (-0.03 … +0.34)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.011%** (%95 GA -0.037 … +0.053, anlamlı değil); R farkı -0.01 (-0.18 … +0.14)

_Süre: 1433 sn_

