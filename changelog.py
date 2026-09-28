"""Sistem Değişiklik Günlüğü - 📒 İşlem Günlüğü sayfasının "📝 Değişiklik
Günlüğü" sekmesinde gösterilir (trade_journal_page.py).

Her kayıt: ne sorunu çözdüğü (verideki kanıtıyla), ne değiştiği, kodda
nerede olduğu, hangi ayardan değiştirilebileceği ve etkisinin İşlem
Günlüğü'nde nasıl takip edileceği. Kodda ilgili yerler "[2026-09-28 · Öneri N]"
yorumuyla işaretli - bu etiketle arama yaparak bulunabilir.

Yeni bir değişiklik yapıldığında CHANGES listesinin BAŞINA yeni bir kayıt
eklenmeli.
"""

ANALYSIS_SUMMARY = {
    "date": "2026-09-28",
    "title": "Alım-satım emirleri analizi (24 Ağustos - 24 Eylül 2026)",
    "body": (
        "17 kapanmış işlem: 6 kazanç / 11 kayıp (%35 isabet), net **−122.63$**. Yüzde bazında sistem "
        "kârlı görünüyordu (ort. kazanç +%3.88, ort. kayıp −%1.07, ~3.6:1) ama dolar bazında zarardaydı "
        "(+109$ / −71$, ~1.5:1).\n\n"
        "Bar verisi olan 13 işlemin 12'si pozisyondayken en az +%1.7 kâr görmüştü; bunların 6'sı sıfır "
        "ya da zararla kapandı (MSFT +%3.01 → −%0.39, NOW +%2.36 → −%1.52, SKHY +%4.64 → −%0.07, "
        "NVDA +%2.34 → %0.00, UROY +%1.87 → −%1.76).\n\n"
        "17 çıkışın 7'si 09:30-09:35 ET'de, 3'ü seans dışında; 17 girişin 8'i 09:33-09:36 ET'de oldu. "
        "İşlem başına risk 14$ (MU) ile 282$ (MSFT) arasında değişti. Bir ayda portföy ayarları 24 kez, "
        "strateji kodu 45 commit ile değişti."
    ),
}

CHANGES = [
    {
        "id": "7",
        "date": "2026-09-28",
        "title": "Heikin Ashi Çıkışı: ilk stopa oynaklık (ATR) tabanı; kırmızı mum çıkışı test edildi",
        "problem": (
            "Heikin Ashi ilk stopu sinyal barının low'unun %0.2 altına kuruluyordu. Sinyal mumu alt fitilsiz "
            "yeşil bir mum ve giriş onun kapanışında yapıldığından stop girişin sentler altında kalıyordu: "
            "LAUR (25.09) giriş 37.58 / stop 37.47 = %0.29, LQDA 68.79 / 68.25 = %0.78. Backtestte eski "
            "kuralla medyan ilk stop mesafesi %0.26 - sıradan bir 30 dakikalık dalgalanma pozisyonu kapatıyor."
        ),
        "change": (
            "- İlk stop artık girişten en az 1 x ATR (sinyalin kendi periyodu, HA Gün İçi için 30dk) uzakta: "
            "stop = min(sinyal barı low − %0.2, giriş − 1 x ATR). Taban sadece genişletir.\n"
            "- Çıkış kuralı (ilk kırmızı HA mumu / Stokastik kesişimi -> stop son kapanışın %0.1 altına) "
            "DEĞİŞMEDİ.\n"
            "- Sadece yeni girişleri etkiler; açık pozisyonların stopu gevşetilmez.\n"
            "- **Doğrulama (canlı modülün kurallarıyla simülasyon, 30dk önbellek, 12 hisse, 6 işlem):** taban "
            "sonucu DEĞİŞTİRMEDİ - her varyantta aynı 6 işlem, toplam −%0.5. Bu örneklemde işlemleri kapatan "
            "stop değil, HA çıkışı ve gün sonu kapanışı. (İlk karşılaştırma backtest motoruyla yapılmıştı ve "
            "yanıltıcıydı: motor pozisyonu geceye taşıyor, modülün kendi market çıkışını ve 'kapanışa 60 dk "
            "kala giriş yok' kuralını görmüyordu.) Taban ayardan 0'a çekilerek eski davranışa dönülebilir.\n"
            "- **Çıkış kuralı testi:** 'art arda 2 / 3 kırmızı mumda çık' da denendi (exit_red_candles). Aynı "
            "simülasyonda 1 mum −%0.5, 2 mum −%1.8, 3 mum −%0.9 - beklemek kaybı büyüttü; varsayılan 1 kaldı. "
            "Ayar hem stopun trail'inde hem HA Gün İçi modülünün kendi çıkış kontrolünde aynı değeri kullanır."
        ),
        "where": "stop_algorithms.py (heikin_ashi_initial_stop, HEIKIN_ASHI_MIN_ATR_MULT, "
                 "HEIKIN_ASHI_EXIT_RED_CANDLES), heikin_ashi.py (long_exit_reason), heikin_ashi_intraday_core.py, "
                 "scripts/compare_heikin_ashi_stop.py",
        "settings": "🛡️ Stop Loss Ayarları > Heikin Ashi Çıkışı > İlk Stop Oynaklık Tabanı (0 = kapalı).",
        "track": "İşlem Günlüğü'nde Heikin Ashi Gün İçi işlemlerinde 'Stop: İlk stop' ile kapanan işlemlerin "
                 "payı düşmeli; ortalama kayıp (R değil $) risk bazlı adetle sabit kalmalı.",
    },
    {
        "id": "1",
        "date": "2026-09-28",
        "title": "Stop, girişin zaman diliminde izleniyor; oynaklık (ATR) stopu seçenek olarak eklendi",
        "problem": (
            "Günlük sinyalle alınan hisseler (MSFT, PAYX, MDB `demand_zone` 1 Gün) 30 dakikalık barlarla "
            "izleniyordu: yapısal trail 30 dakikalık swing diplerine göre sıkılaşıyor, pozisyon günlük trendi "
            "taşıyamıyordu. Ayrıca sabit %1.5 stop, bu hisselerin günlük ATR'sinin sadece 0.3-0.7'si "
            "(MSFT %2.0, NOW %4.9, SKHY %5.0, UROY %5.2)."
        ),
        "change": (
            "- **Aktif:** Premium Buy Point hisselerinde stop artık girişin kendi periyodunda izleniyor (günlük "
            "sinyal → günlük bar). RS / ORB / Heikin Ashi modülleri eskisi gibi 30 dakikalık.\n"
            "- **Seçenek (varsayılan DEĞİL):** Yeni stop algoritması **Oynaklık (ATR) Stop + R Bazlı Breakeven** "
            "(`atr_volatility`): ilk stop = giriş − 2×ATR (en fazla %12), +1R kapanışta breakeven, +2R'den sonra "
            "chandelier trail. Önce 13 hisse bu algoritmaya geçirildi, ancak doğrulama backtestinde (aşağıdaki "
            "'Test ve doğrulama sonuçları') mevcut Breakeven + Yapısal Trail'in günlük barlarda izlenen hali "
            "+237R, ATR stopu ise −0.7R (genişletilmiş trail ile +8.7R) verdi: ATR stopu büyük trendlerden erken "
            "çıkıyor. Bu yüzden hisselerin stop algoritması DEĞİŞTİRİLMEDİ; ATR stopu hisse bazında "
            "seçilebilir halde bırakıldı.\n"
            "- Backtest motoru canlıyla aynı bilgileri (ilk stop, giriş öncesi ATR penceresi) kullanıyor."
        ),
        "where": "stop_algorithms.py (atr_volatility_*), alpaca_trailing_stop.py (resolve_stop_timeframe, "
                 "_stop_bars_for_timeframe, get_initial_stop_price), backtest_engine.py",
        "settings": "🛡️ Stop Loss Ayarları > Oynaklık (ATR) Stop; 🎯 Premium Buy Point > Giriş Zamanlaması ve "
                    "Stop Periyodu > Stop mum periyodu; hisse bazlı stop algoritması seçimi.",
        "track": "İşlem Günlüğü'nde günlük sinyalli hisselerin (PBP: ... (1Day)) ortalama tutma süresi ve en "
                 "büyük R değerleri artmalı; 'Stop: Yapısal trail' ile kapanan kârlı işlemlerin payı yükselmeli.",
    },
    {
        "id": "2",
        "date": "2026-09-28",
        "title": "Breakeven 1R'de ve tamponlu",
        "problem": (
            "Stop, fiyat +%1'e çıkınca tam giriş fiyatına çekiliyordu - %1.5 risk alınan işlemde bu 0.67R. "
            "Normal dalgalanma başa baş stopu tetikliyor, açılış boşluğunda da başa baş zarara dönüyordu "
            "(MSFT: giriş 494.44, stop satışı 09:32'de 492.52)."
        ),
        "change": (
            "- (Seçenek olan `atr_volatility`'de: bir bar KAPANIŞI giriş + 1R'yi geçince stop giriş + 0.1×ATR'ye "
            "çekilir.)\n"
            "- Sabit-% algoritmalarda breakeven tetiği %1 → %1.5 (Breakeven + Yapısal Trail'de 1R), Beklemeli "
            "ve İz Süren Stop'ta %1 → %4 (1R); breakeven stopu girişin %0.2 üstüne kurulur.\n"
            "- Not: kod varsayılanı değiştiği için ORB (Açılış Aralığı) stopunun breakeven tetiği de %1 → %1.5 oldu.\n"
            "- Doğrulama: 11 hisselik backtestte toplam R neredeyse aynı (+240.4R → +237.5R), isabet %10 → %33. "
            "Etki küçük; asıl amaç normal dalgalanmada başa baş çıkışları azaltmak."
        ),
        "where": "stop_algorithms.py (_breakeven_candidate, BREAKEVEN_TRIGGER_PCT, BREAKEVEN_BUFFER_PCT)",
        "settings": "🛡️ Stop Loss Ayarları > Breakeven Tetik %, Breakeven Tamponu %, ATR Stop'ta Breakeven Tetiği (R).",
        "track": "'Stop: Breakeven' ile kapanan işlemlerin sonucu artık +%0.2 civarında olmalı (eskiden −%0.4'e "
                 "kadar); işlemlerin görülen en yüksek kârı ile çıkış arasındaki fark azalmalı.",
    },
    {
        "id": "3",
        "date": "2026-09-28",
        "title": "Açılış kalkanı ve seans dışı trail'in kapatılması",
        "problem": (
            "17 çıkışın 7'si 09:30-09:35 ET'de, 2'si 18:51 ET'de (after-hours), 1'i pre-market'te gerçekleşti. "
            "Düz stop emri açılış fiyatlamasının oynaklığında piyasa fiyatından doluyordu; seans dışı guard "
            "da stopu işlem hacmi düşük pre-market barlarıyla sıkılaştırıp açılışta tetiklenmesine yol açıyordu."
        ),
        "change": (
            "- Seans dışında guard, resting stopu asıl seviyenin %4 altındaki bir *felaket stopuna* çeker; asıl "
            "seviye emrin `client_order_id` etiketinde saklanır (`shield-<SEMBOL>-<cent>-<zaman>`).\n"
            "- Seans açılışından 15 dk sonra stop asıl seviyeye döner; fiyat o seviyenin altındaysa pozisyon "
            "market emriyle kapatılır. Guard seans dışında çalışamadıysa kalkan ilk 15 dk'da kurulur.\n"
            "- Seans dışı trail (`_extended_hours_trail`) varsayılan olarak kapalı.\n"
            "- Tüm modüllerin (PBP, ORB, RS, HA) gece taşınan pozisyonlarına uygulanır. Dashboard'da "
            "'Açılış Kalkanı' sütunu asıl stopu gösterir."
        ),
        "where": "alpaca_trailing_stop.py (EXECUTION_DEFAULTS, apply_opening_shield, restore_from_shield, "
                 "manage_position, guard_position), stop_tags.py, alpaca_dashboard.py",
        "settings": "🛡️ Stop Loss Ayarları > Emir Yürütme: Açılış Kalkanı.",
        "track": "İşlem Günlüğü > 'Çıkış seans dilimi' tablosunda 'Açılış (ilk 15 dk)' payı sıfıra yakın "
                 "olmalı; 'Açılış kalkanı sonrası çıkış' satırları asıl stopun gerçekten kırıldığı günleri gösterir.",
    },
    {
        "id": "4",
        "date": "2026-09-28",
        "title": "Açılış öncesi pullback limit iptali ve ilk 15 dakikada alım yok",
        "problem": (
            "17 girişin 8'i 09:33-09:36 ET'de doldu. Pullback algoritmalarının GTC limit alışları, hisse "
            "açılışta o seviyenin altına boşlukla açılınca - yani TAM fiyat düştüğü için - doluyordu "
            "(ters seçim / adverse selection)."
        ),
        "change": (
            "- Seans dışında (after-hours ve pre-market) bu sistemin normal seans için bıraktığı limit alışlar "
            "iptal edilir; pre-market'te yeni giriş yapılmaz. After-hours girişleri (day + extended-hours, "
            "20:00'de düşer) aynen çalışır.\n"
            "- Seansın ilk 15 dakikasında hiçbir yeni alım (limit, kırılım market emri, ilave alım) yapılmaz; "
            "sinyal 09:45'ten sonra yeniden değerlendirilip emir yeniden konur.\n"
            "- ORB modülü etkilenmez (market emriyle, açılış aralığı oluştuktan sonra alır)."
        ),
        "where": "alpaca_buy_points.py (ENTRY_TIMING_DEFAULTS, cancel_pullback_limit_buys, check_symbol "
                 "in_entry_guard, run_extended_hours_entry_scan)",
        "settings": "🎯 Premium Buy Point > Giriş Zamanlaması ve Stop Periyodu.",
        "track": "İşlem Günlüğü > 'Giriş seans dilimi' tablosunda 'Açılış (ilk 15 dk)' girişleri sıfır olmalı.",
    },
    {
        "id": "5",
        "date": "2026-09-28",
        "title": "R bazlı pozisyon büyüklüğü ve portföy ısısı",
        "problem": (
            "Adet = bütçe × ağırlık / fiyat idi; stop mesafesi hesaba girmiyordu. İşlem başına risk 14$ ile "
            "282$ arasında (20 kat) değişti; en iyi iki işlem (MU +4.3R, SKHY +7.9R) en küçük riskle açıldı. "
            "17 işlem eşit riskle açılsaydı toplam +7.7R (işlem başına 500$ riskle ~+3.800$) olurdu - ama bu "
            "iki işlem çıkarılırsa −4.5R; yani örneklem küçük, yine de sonucun işareti sadece büyüklük "
            "yüzünden ters dönmüştü. NXPI tek başına −542$ ile net zarardan büyüktü."
        ),
        "change": (
            "- Adet = (özsermaye × %0.5) / (giriş − stop). Ağırlık bütçesi, nakit ve tek pozisyon tavanı (%20) "
            "sadece üst sınır.\n"
            "- Portföy ısısı: açık pozisyonların (ortalama − stop) × adet toplamı özsermayenin %5'ini aşamaz; "
            "bekleyen her giriş emri 1 risk birimi sayılır, breakeven'deki pozisyon 0.\n"
            "- İlave alımda pozisyonun toplam riski 1 risk birimini aşamaz.\n"
            "- ORB, Relative Strength ve Heikin Ashi girişlerine aynı tavan uygulanır (modülün kendi nakit payından "
            "hesaplanan adetle risk bazlı adetin küçüğü; her modülün son koşu özetinde kısılan girişler "
            "'📐 Risk tavanı' satırında görünür)."
        ),
        "where": "risk_sizing.py (risk_based_qty, apply_risk_cap), alpaca_buy_points.py (build_risk_context, "
                 "load_module_risk_context, check_symbol), orb_core.py, relative_strength_core.py, heikin_ashi_intraday_core.py",
        "settings": "🎯 Premium Buy Point > Risk Bazlı Pozisyon Büyüklüğü.",
        "track": "İşlem Günlüğü'nde kayıpların dolar tutarı birbirine yakın olmalı (~özsermaye × %0.5); "
                 "'Toplam R' ve 'Beklenen değer (R)' sistemin gerçek performansını gösterir.",
    },
    {
        "id": "6",
        "date": "2026-09-28",
        "title": "İşlem günlüğü, çıkış sebebi etiketleri ve kural dondurma",
        "problem": (
            "Stopun hangi sebeple tetiklendiği hiçbir yerde kaydedilmiyordu; analiz fill önbelleği, git "
            "geçmişi ve bar önbellekleri elle birleştirilerek yapıldı. Kurallar o kadar sık değişti ki hiçbir "
            "kural seti ölçülemedi."
        ),
        "change": (
            "- Her stop emri etiketleniyor (`stop-<sebep>-<SEMBOL>-<zaman>`): initial, breakeven, structure, "
            "chandelier, profitlock, haexit, topup, restore.\n"
            "- Bu sayfa (📒 İşlem Günlüğü): Alpaca emir geçmişinden kapanmış işlemleri, R çarpanını, çıkış "
            "sebebini ve seans dilimini gösterir.\n"
            "- Kural sürümü: Premium Buy Point kaydedildiğinde algoritma/stop/risk/zamanlama ayarları "
            "değiştiyse yeni bir kural sürümü başlar; bu sayfa o tarihten beri kapanan işlemleri sayar ve "
            "30 işlem birikmeden değişiklik yapılmaması için uyarır. Bütçe ve ağırlık değişiklikleri sürümü "
            "sıfırlamaz."
        ),
        "where": "trade_journal.py, trade_journal_page.py, stop_tags.py, rules_version.py, changelog.py",
        "settings": "—",
        "track": "Bu sayfanın kendisi.",
    },
]

# tests/ klasöründeki birim testleri ve scripts/compare_stop_algorithms.py
# çıktısının özeti - trade_journal_page.py'de gösterilir.
VERIFICATION_NOTES = "**Birim ve entegrasyon testleri** (`python -m unittest discover -s tests -t .`): 53 test, hepsi geçti - ATR stop ve breakeven kuralları, risk bazlı adet/portföy ısısı, stop etiketleri, kural sürümü, açılış kalkanı (guard genişletme, ilk 15 dk bekleme, geri dönüş, kırılmışsa market çıkışı; sahte Alpaca istemcisiyle), açılış öncesi limit iptali, ORB/RS/Heikin Ashi girişlerinde risk tavanı ve işlem günlüğü (OTO bacağı, ilave alım, eşleşmeyen satış).\n\n**Karşılaştırma** (`python scripts/compare_stop_algorithms.py`, repo içindeki günlük bar önbelleğiyle, 28.09.2026):\n\n**1) Gerçek girişlerin yeniden oynatılması** (günlük bar; R = (çıkış − giriş) / (giriş − ilk stop); 'açık' = önbellek sonunda hâlâ açık, son kapanıştan)\n\n| Hisse | Giriş | Gerçekleşen K/Z | Aktif kural (günlük, 1R BE) | ATR stop |\n|---|---|---|---|---|\n| AMZN | 2026-09-01 @ 253.71 | +6.55$ (+0.43R) | 252.85 stop (-0.23R) | 249.95 açık (-0.32R) |\n| NOW | 2026-09-02 @ 138.95 | -16.85$ (-1.01R) | 139.23 stop (+0.13R) | 140.75 açık (+0.14R) |\n| MU | 2026-09-03 @ 930.63 | +59.49$ (+4.26R) | 906.05 stop (-1.76R) | 906.05 stop (-0.27R) |\n| SKHY | 2026-09-03 @ 158.30 | +131.75$ (+7.93R) | 193.57 açık (+14.86R) | 175.06 stop (+1.07R) |\n| NOW | 2026-09-08 @ 135.29 | -18.45$ (-1.01R) | 133.26 stop (-1.00R) | 140.75 açık (+0.41R) |\n| MU | 2026-09-14 @ 910.92 | +185.56$ (+1.13R) | 1082.93 açık (+12.59R) | 1082.93 açık (+1.97R) |\n| PAYX | 2026-09-17 @ 115.89 | -77.55$ (-0.99R) | 114.15 stop (-1.00R) | 109.44 stop (-1.29R) |\n| MSFT | 2026-09-18 @ 494.44 | -73.11$ (-0.26R) | 515.80 açık (+2.88R) | 515.80 açık (+1.06R) |\n| AMZN | 2026-09-18 @ 254.20 | +14.62$ (+0.21R) | 254.71 stop (+0.13R) | 249.95 açık (-0.35R) |\n| SKHY | 2026-09-18 @ 187.68 | -0.65$ (-0.05R) | 193.57 açık (+2.09R) | 193.57 açık (+0.32R) |\n| AMAT | 2026-09-24 @ 464.93 | -22.56$ (-0.40R) | 484.83 açık (+2.85R) | 484.83 açık (+0.53R) |\n\n11 işlem toplamı: gerçekleşen **+10.24R**, aktif kural **+31.55R**, ATR stop **+3.27R** (işlem başına 500$ riskle R × 500$).\n\n**2) Backtest motoru** - demand_zone (1 Gün) sinyali, stop günlük barlarda, 11 hisse (AMZN, MSFT, MU, NOW, PAYX, UROY, CUZ, GBCI, TREX, NHI, OMCL), ~6-13 aylık günlük önbellek. R bazlı (canlıdaki risk bazlı adetle karşılaştırılabilir):\n\n| Stop kuralı | İşlem | İsabet | Toplam R | En büyük 3 işlem hariç R | En büyük işlem |\n|---|---|---|---|---|---|\n| Eski: Breakeven+Yapısal (%1 BE, tampon yok) | 86 | %10 | +240.4R | -27.1R | +220.0R |\n| Yeni (aktif): Breakeven+Yapısal (%1.5 BE = 1R, %0.2 tampon) | 85 | %33 | +237.5R | -30.0R | +220.0R |\n| Opsiyonel: Oynaklık (ATR) Stop (2×ATR, 3×ATR chandelier) | 63 | %46 | -0.7R | -11.8R | +4.5R |\n| Opsiyonel: ATR Stop, geniş trail (8×ATR, 4R'den) | 41 | %51 | +8.7R | -11.4R | +10.5R |\n\n**Yorum ve sınırlar:**\n- Günlük barlarda izlenen mevcut kural, büyük trendleri (MU 230→990, MSFT, TREX, NOW) taşıyarak toplam R'nin neredeyse tamamını birkaç işlemden kazanıyor; en büyük 3 işlem hariç tüm kurallar negatif. Yani sistem 'çok sayıda küçük kayıp + nadir büyük kazanç' yapısında - bu yüzden (a) büyük kazancı erken kesen ATR chandelier varsayılan yapılmadı, (b) risk bazlı adet ile her küçük kaybın dolar tutarı sabitlendi.\n- Veri 2026'daki güçlü yükseliş dönemini kapsıyor (~6-13 ay, 11 hisse) ve sadece günlük barlarla; gün içi sıralama, açılış kalkanı ve komisyon/kayma modellenmedi. Sonuçlar yön göstericidir, kesin değildir - kural sürümü 30 işleme ulaşınca İşlem Günlüğü'ndeki gerçek sonuçlarla yeniden değerlendirilmeli.\n- Gerçek işlemlerin yeniden oynatılmasında 'açık' satırlar önbellek sonundaki kapanıştan değerlendi (gerçekleşmemiş kâr/zarar)."
