# Akıllı Dinamik Stop doğrulaması (05.10.2026)

Kayma %0.05/taraf; özsermaye % = risk bazlı adet (%0.5 risk, %20 pozisyon tavanı) ile işlemin özsermayeye katkısı.

## Günlük bar (Alpaca önbelleği)

30 hisse, 4599 bar; bölme: **hisse başına veri ortası (medyan 2026-07-22)** (eğitim 1100 giriş, test 1081 giriş).

**Eğitimde taranan 486 ayar** - seçilen (komşularıyla birlikte sağlam): `{'trail_atr_mult': 5.0, 'trail_start_r': 1.5, 'tighten_per_r': 0.25, 'er_weight': 0.0, 'breakeven_r': 0.0, 'max_atr_mult': 2.0}`; eğitimdeki en iyi tekil hücre: `{'trail_atr_mult': 5.0, 'trail_start_r': 1.5, 'tighten_per_r': 0.25, 'er_weight': 0.5, 'breakeven_r': 0.0, 'max_atr_mult': 2.0}` (ort. özs. +0.216%).

**TEST dönemi - Tüm girişler** (1081 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 1077 | %33 | +0.79 | +0.238 | +256.5 | +152.1 | 1.76 | 216.2 | %1.5 | %59 | 5.3 |
| Beklemeli ve İz Süren (kayıtlı) | 1077 | %41 | +0.39 | +0.194 | +208.4 | +114.7 | 1.62 | 152.9 | %4.0 | %50 | 8.0 |
| Oynaklık (ATR) Stop (kayıtlı) | 1077 | %48 | +0.30 | +0.156 | +167.5 | +138.5 | 1.61 | 65.2 | %5.7 | %43 | 9.9 |
| Akıllı Dinamik - kod varsayılanı | 1077 | %42 | +0.53 | +0.272 | +293.1 | +251.2 | 1.99 | 75.7 | %6.2 | %47 | 14.3 |
| Akıllı Dinamik - eğitimde seçilen | 1077 | %42 | +0.53 | +0.272 | +293.1 | +251.2 | 1.99 | 75.7 | %6.2 | %47 | 14.3 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.034%** (%95 GA -0.158 … +0.217, anlamlı değil); R farkı -0.27 (-0.93 … +0.29)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.034%** (%95 GA -0.158 … +0.217, anlamlı değil); R farkı -0.27 (-0.93 … +0.29)

**TEST dönemi - Trend girişleri** (297 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 297 | %31 | +0.96 | +0.288 | +85.4 | +20.9 | 1.93 | 66.2 | %1.5 | %60 | 5.4 |
| Beklemeli ve İz Süren (kayıtlı) | 297 | %41 | +0.36 | +0.178 | +53.0 | -0.3 | 1.54 | 48.4 | %4.0 | %50 | 8.5 |
| Oynaklık (ATR) Stop (kayıtlı) | 297 | %47 | +0.18 | +0.102 | +30.3 | +10.5 | 1.39 | 25.0 | %5.6 | %44 | 9.6 |
| Akıllı Dinamik - kod varsayılanı | 297 | %40 | +0.36 | +0.190 | +56.4 | +38.0 | 1.67 | 34.8 | %6.6 | %47 | 15.1 |
| Akıllı Dinamik - eğitimde seçilen | 297 | %40 | +0.36 | +0.190 | +56.4 | +38.0 | 1.67 | 34.8 | %6.6 | %47 | 15.1 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **-0.098%** (%95 GA -0.501 … +0.258, anlamlı değil); R farkı -0.60 (-2.03 … +0.55)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **-0.098%** (%95 GA -0.501 … +0.258, anlamlı değil); R farkı -0.60 (-2.03 … +0.55)

**TEST dönemi - Rastgele girişler** (555 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 555 | %33 | +0.68 | +0.205 | +114.0 | +25.7 | 1.67 | 110.9 | %1.5 | %59 | 5.0 |
| Beklemeli ve İz Süren (kayıtlı) | 555 | %41 | +0.44 | +0.218 | +120.9 | +27.2 | 1.73 | 69.1 | %4.0 | %49 | 7.9 |
| Oynaklık (ATR) Stop (kayıtlı) | 555 | %48 | +0.32 | +0.165 | +91.4 | +63.7 | 1.65 | 31.3 | %5.7 | %43 | 10.1 |
| Akıllı Dinamik - kod varsayılanı | 555 | %42 | +0.57 | +0.295 | +163.5 | +125.5 | 2.09 | 40.1 | %6.2 | %46 | 14.3 |
| Akıllı Dinamik - eğitimde seçilen | 555 | %42 | +0.57 | +0.295 | +163.5 | +125.5 | 2.09 | 40.1 | %6.2 | %46 | 14.3 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.089%** (%95 GA -0.072 … +0.256, anlamlı değil); R farkı -0.12 (-0.70 … +0.41)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.089%** (%95 GA -0.072 … +0.256, anlamlı değil); R farkı -0.12 (-0.70 … +0.41)

**TEST dönemi - Canlı sinyal girişleri** (229 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 225 | %35 | +0.85 | +0.254 | +57.1 | +22.0 | 1.76 | 44.9 | %1.5 | %58 | 5.8 |
| Beklemeli ve İz Süren (kayıtlı) | 225 | %41 | +0.31 | +0.154 | +34.5 | +11.4 | 1.48 | 38.7 | %4.0 | %52 | 7.6 |
| Oynaklık (ATR) Stop (kayıtlı) | 225 | %48 | +0.40 | +0.203 | +45.8 | +23.3 | 1.81 | 16.6 | %6.0 | %44 | 9.9 |
| Akıllı Dinamik - kod varsayılanı | 225 | %42 | +0.64 | +0.325 | +73.2 | +45.0 | 2.16 | 20.7 | %5.8 | %48 | 13.3 |
| Akıllı Dinamik - eğitimde seçilen | 225 | %42 | +0.64 | +0.325 | +73.2 | +45.0 | 2.16 | 20.7 | %5.8 | %48 | 13.3 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.072%** (%95 GA -0.205 … +0.348, anlamlı değil); R farkı -0.20 (-1.08 … +0.60)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **+0.072%** (%95 GA -0.205 … +0.348, anlamlı değil); R farkı -0.20 (-1.08 … +0.60)

## 30 dakikalık bar (Alpaca önbelleği)

11 hisse, 7446 bar; bölme: **hisse başına veri ortası (medyan 2026-08-26)** (eğitim 1897 giriş, test 1770 giriş).

**Eğitimde taranan 486 ayar** - seçilen (komşularıyla birlikte sağlam): `{'trail_atr_mult': 5.0, 'trail_start_r': 2.5, 'tighten_per_r': 0.0, 'er_weight': 1.0, 'breakeven_r': 2.0, 'max_atr_mult': 3.0}`; eğitimdeki en iyi tekil hücre: `{'trail_atr_mult': 5.0, 'trail_start_r': 2.5, 'tighten_per_r': 0.0, 'er_weight': 1.0, 'breakeven_r': 2.0, 'max_atr_mult': 3.0}` (ort. özs. +0.200%).

**TEST dönemi - Tüm girişler** (1770 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 1770 | %41 | -0.17 | -0.052 | -92.5 | -101.9 | 0.74 | 181.7 | %1.5 | %51 | 21.0 |
| Beklemeli ve İz Süren (kayıtlı) | 1770 | %39 | -0.23 | -0.117 | -207.5 | -213.5 | 0.59 | 238.8 | %4.0 | %44 | 57.1 |
| Oynaklık (ATR) Stop (kayıtlı) | 1770 | %29 | -0.25 | -0.056 | -99.7 | -107.4 | 0.67 | 135.0 | %1.0 | %57 | 11.0 |
| Akıllı Dinamik - kod varsayılanı | 1770 | %26 | -0.31 | -0.082 | -144.3 | -152.2 | 0.62 | 163.8 | %1.1 | %64 | 15.1 |
| Akıllı Dinamik - eğitimde seçilen | 1770 | %26 | -0.23 | -0.070 | -123.7 | -133.5 | 0.72 | 213.8 | %1.3 | %67 | 19.2 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **-0.029%** (%95 GA -0.072 … +0.016, anlamlı değil); R farkı -0.13 (-0.28 … +0.06)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **-0.018%** (%95 GA -0.070 … +0.038, anlamlı değil); R farkı -0.06 (-0.23 … +0.14)

**TEST dönemi - Trend girişleri** (360 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 360 | %38 | -0.20 | -0.061 | -21.8 | -27.4 | 0.70 | 43.8 | %1.5 | %53 | 19.7 |
| Beklemeli ve İz Süren (kayıtlı) | 360 | %35 | -0.39 | -0.195 | -70.0 | -73.5 | 0.43 | 71.6 | %4.0 | %50 | 45.1 |
| Oynaklık (ATR) Stop (kayıtlı) | 360 | %32 | -0.13 | -0.036 | -13.0 | -17.4 | 0.78 | 26.8 | %1.0 | %55 | 11.2 |
| Akıllı Dinamik - kod varsayılanı | 360 | %25 | -0.31 | -0.096 | -34.6 | -40.2 | 0.58 | 40.7 | %1.2 | %66 | 14.5 |
| Akıllı Dinamik - eğitimde seçilen | 360 | %25 | -0.20 | -0.085 | -30.5 | -37.6 | 0.71 | 58.8 | %1.6 | %69 | 20.5 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **-0.036%** (%95 GA -0.077 … +0.014, anlamlı değil); R farkı -0.11 (-0.24 … +0.05)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **-0.024%** (%95 GA -0.107 … +0.045, anlamlı değil); R farkı -0.00 (-0.20 … +0.18)

**TEST dönemi - Rastgele girişler** (1165 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 1165 | %42 | -0.15 | -0.046 | -53.8 | -63.2 | 0.77 | 117.7 | %1.5 | %51 | 21.6 |
| Beklemeli ve İz Süren (kayıtlı) | 1165 | %41 | -0.17 | -0.087 | -100.9 | -106.9 | 0.68 | 137.8 | %4.0 | %42 | 60.4 |
| Oynaklık (ATR) Stop (kayıtlı) | 1165 | %29 | -0.28 | -0.058 | -67.0 | -74.7 | 0.67 | 92.6 | %1.0 | %58 | 10.9 |
| Akıllı Dinamik - kod varsayılanı | 1165 | %26 | -0.32 | -0.075 | -87.8 | -95.6 | 0.64 | 105.9 | %1.1 | %64 | 15.0 |
| Akıllı Dinamik - eğitimde seçilen | 1165 | %27 | -0.25 | -0.064 | -75.1 | -84.9 | 0.73 | 129.5 | %1.2 | %67 | 18.6 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **-0.029%** (%95 GA -0.082 … +0.021, anlamlı değil); R farkı -0.16 (-0.35 … +0.05)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **-0.018%** (%95 GA -0.069 … +0.033, anlamlı değil); R farkı -0.10 (-0.28 … +0.11)

**TEST dönemi - Canlı sinyal girişleri** (245 giriş, hiç görülmemiş veri)

| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakeven+Yapısal (kayıtlı ayar, canlı) | 245 | %42 | -0.23 | -0.069 | -16.8 | -22.9 | 0.62 | 21.0 | %1.5 | %46 | 20.0 |
| Beklemeli ve İz Süren (kayıtlı) | 245 | %34 | -0.30 | -0.149 | -36.6 | -41.0 | 0.49 | 39.6 | %4.0 | %45 | 59.2 |
| Oynaklık (ATR) Stop (kayıtlı) | 245 | %29 | -0.26 | -0.080 | -19.6 | -22.6 | 0.52 | 21.2 | %1.0 | %53 | 11.0 |
| Akıllı Dinamik - kod varsayılanı | 245 | %27 | -0.24 | -0.089 | -21.9 | -27.9 | 0.58 | 22.5 | %1.2 | %63 | 16.0 |
| Akıllı Dinamik - eğitimde seçilen | 245 | %26 | -0.19 | -0.074 | -18.1 | -25.9 | 0.70 | 25.5 | %1.4 | %65 | 20.3 |

- Akıllı Dinamik - kod varsayılanı − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **-0.021%** (%95 GA -0.071 … +0.043, anlamlı değil); R farkı -0.01 (-0.26 … +0.34)
- Akıllı Dinamik - eğitimde seçilen − Breakeven+Yapısal (kayıtlı ayar, canlı): işlem başına özs. **-0.005%** (%95 GA -0.076 … +0.083, anlamlı değil); R farkı +0.03 (-0.18 … +0.33)

_Süre: 284 sn_
