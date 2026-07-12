# Üç tırnak kullanarak değişkeni tanımlıyoruz
haber_str = """```json
[
  {
    "tarih_saat": "2025-02-12 09:00",
    "haber": "NVIDIA, yeni nesil yapay zeka çiplerinin üretiminde %15'lik bir gecikme yaşanacağını resmi olarak açıkladı. Bu durum, küresel AI altyapı projeleri ve veri merkezi genişlemeleri üzerinde önemli bir etki yaratabilir.",
    "etki_edecek_tarih": "2025-02-12",
    "skor_hesabi": " (10 * 10) * 1.0 = 100.0 ",
    "onem_puani": 100
  },
  {
    "tarih_saat": "2025-02-12 09:00",
    "haber": "Apple, Vision Pro karma gerçeklik başlığının üretim hedeflerini %20 artırdığını ve kritik tedarik zinciri sorunlarını başarıyla çözdüğünü duyurdu. Bu gelişme, cihazın pazar penetrasyonunu hızlandırarak şirketin gelir beklentilerini olumlu etkileyebilir.",
    "etki_edecek_tarih": "2025-02-12",
    "skor_hesabi": " (9 * 10) * 1.0 = 90.0 ",
    "onem_puani": 90
  },
  {
    "tarih_saat": "2025-02-12 09:00",
    "haber": "Microsoft, Azure AI hizmetlerinin kapasitesini ve yeteneklerini genişletmek amacıyla önümüzdeki iki yıl içinde 10 milyar dolarlık ek yatırım yapmayı planladığını açıkladı. Bu stratejik hamle, şirketin bulut ve yapay zeka pazarındaki liderliğini pekiştirmeyi hedefliyor.",
    "etki_edecek_tarih": "2025-02-13",
    "skor_hesabi": " (9 * 9) * 0.9 = 72.9 ",
    "onem_puani": 72
  },
  {
    "tarih_saat": "2025-02-12 09:00",
    "haber": "ABD Adalet Bakanlığı, Google'a karşı açtığı antitröst davasında yeni ve önemli kanıtlar sundu ve davanın yargılama sürecinin hızlanacağını belirtti. Bu durum, şirketin reklam ve arama motoru iş modelleri üzerinde potansiyel düzenleyici baskıları artırabilir.",
    "etki_edecek_tarih": "2025-02-12",
    "skor_hesabi": " (8 * 9) * 0.9 = 64.8 ",
    "onem_puani": 64
  },
  {
    "tarih_saat": "2025-02-12 09:00",
    "haber": "Tesla, Cybertruck üretim hedeflerine ulaşılamayacağını ve teslimatların beklenenden daha uzun süre gecikeceğini resmi olmayan kaynaklardan sızan bilgilerle doğruladı. Bu durum, şirketin üretim kapasitesi ve yeni ürün lansman stratejileri hakkında endişelere yol açabilir.",
    "etki_edecek_tarih": "2025-02-12",
    "skor_hesabi": " (9 * 8) * 0.9 = 64.8 ",
    "onem_puani": 64
  },
  {
    "tarih_saat": "2025-02-12 09:00",
    "haber": "TSMC'nin Arizona'daki yeni yarı iletken fabrika inşaatında, beklenenden daha büyük maliyet aşımları ve önemli gecikmeler yaşandığı sektör raporlarında belirtildi. Bu durum, ABD'nin yerel çip üretim kapasitesi hedeflerini ve TSMC'nin küresel stratejisini etkileyebilir.",
    "etki_edecek_tarih": "2025-02-15",
    "skor_hesabi": " (9 * 9) * 0.8 = 64.8 ",
    "onem_puani": 64
  },
  {
    "tarih_saat": "2025-02-12 09:00",
    "haber": "Yarı iletken ekipman üreticisi ASML'nin, yeni nesil EUV (Extreme Ultraviolet) litografi makinelerine olan küresel talebin beklenenden çok daha yüksek olduğu belirtildi. Bu durum, çip üreticilerinin kapasite artırma çabalarını ve ASML'nin gelir beklentilerini olumlu etkileyebilir.",
    "etki_edecek_tarih": "2025-02-13",
    "skor_hesabi": " (8 * 9) * 0.9 = 64.8 ",
    "onem_puani": 64
  },
  {
    "tarih_saat": "2025-02-12 09:00",
    "haber": "Amazon Web Services (AWS), veri merkezleri için özel olarak tasarlanmış yeni nesil ARM tabanlı çiplerini tanıttı ve bu çiplerin enerji verimliliği ile performans avantajları sunduğunu belirtti. Bu hamle, AWS'nin bulut altyapısındaki rekabet gücünü artırarak maliyetleri optimize etmesine yardımcı olabilir.",
    "etki_edecek_tarih": "2025-02-12",
    "skor_hesabi": " (8 * 8) * 1.0 = 64.0 ",
    "onem_puani": 64
  },
  {
    "tarih_saat": "2025-02-12 09:00",
    "haber": "Meta Platforms, metaverse bölümündeki AR-VR donanım geliştirme bütçesini %15 oranında kestiğini resmi olarak duyurdu. Bu karar, şirketin metaverse stratejisinde bir yavaşlama veya yeniden odaklanma sinyali olarak yorumlanıyor ve yatırımcı güvenini etkileyebilir.",
    "etki_edecek_tarih": "2025-02-12",
    "skor_hesabi": " (7 * 9) * 1.0 = 63.0 ",
    "onem_puani": 63
  },
  {
    "tarih_saat": "2025-02-12 09:00",
    "haber": "AMD'nin yeni nesil veri merkezi işlemcilerinin (EPYC serisi) ilk performans testlerinde, rakip ürünleri önemli ölçüde geride bıraktığına dair güvenilir sızıntılar ortaya çıktı. Bu durum, şirketin veri merkezi pazarındaki pazar payını artırma potansiyelini güçlendirebilir.",
    "etki_edecek_tarih": "2025-02-14",
    "skor_hesabi": " (8 * 9) * 0.8 = 57.6 ",
    "onem_puani": 57
  }
]
```
"""

analiz_str = """[
  {
    "gun": "2025-02-12",
    "hafta_ici": "Çarşamba",
    "etki_yonu": "pozitif",
    "tahmin_min": "5.0%",
    "tahmin_max": "9.0%",
    "guven_skoru": "0.9",
    "guven_gerekcesi": "Şirket içi haberler son derece pozitif ve doğrudan gelir beklentilerini artırıyor. Sektörel endişeler kısa vadede ana etkiyi gölgeleyecektir.",
    "analiz_mantigi": "NVIDIA'nın yeni nesil AI çipi 'Blackwell Ultra'nın seri üretiminin hızlanması ve büyük bir bulut hizmeti sağlayıcısıyla milyarlarca dolarlık yeni bir veri merkezi anlaşması imzalaması, şirketin büyüme potansiyelini ve pazar liderliğini güçlendiren çok güçlü şirket içi pozitif haberlerdir. TSMC'den gelen küresel çip üretim kapasitesi endişeleri sektörel bir baskı yaratsa da, bu güçlü pozitif haberlerin kısa vadeli etkisini sınırlı tutacaktır. Son 2 yıllık günlük volatilite aralığı (%0.5 - %10) göz önüne alındığında, bu denli önemli bir haberin hisse senedinde aralığın üst bandına yakın, güçlü bir pozitif tepki yaratması beklenmektedir."
  },
  {
    "gun": "2025-02-13",
    "hafta_ici": "Perşembe",
    "etki_yonu": "pozitif",
    "tahmin_min": "1.5%",
    "tahmin_max": "4.0%",
    "guven_skoru": "0.8",
    "guven_gerekcesi": "İlk günkü güçlü yükselişin ardından momentumun devam etmesi bekleniyor, ancak kar alımları ve haberin sindirilmesiyle hız yavaşlayacaktır.",
    "analiz_mantigi": "İlk günkü güçlü tepkinin ardından, haberin piyasa tarafından daha detaylı değerlendirilmesi, analist raporlarının güncellenmesi ve perakende yatırımcı ilgisinin devam etmesiyle pozitif momentumun sürmesi beklenmektedir. Ancak, ilk günkü kadar keskin bir yükseliş olası değildir, zira bazı erkenyatırımcılar kar alımı yapabilir. Volatilite aralığının orta-üst bandında pozitif bir seyir öngörülmektedir."
  },
  {
    "gun": "2025-02-14",
    "hafta_ici": "Cuma",
    "etki_yonu": "nötr/hafif pozitif",
    "tahmin_min": "-1.0%",
    "tahmin_max": "1.5%",
    "guven_skoru": "0.7",
    "guven_gerekcesi": "Haber etkisi büyük ölçüde fiyatlandıktan sonra piyasa konsolidasyona gidebilir. Uzun hafta sonu öncesi kar alımları ve TSMC endişelerinin daha fazla değerlendirilmesi volatilite yaratabilir.",
    "analiz_mantigi": "Haber etkisi büyük ölçüde fiyatlandıktan sonra, piyasa konsolidasyon eğilimine girebilir. Uzun hafta sonu (Presidents' Day) öncesi bazı yatırımcıların kar alımı yapması veya pozisyonlarını kapatması beklenebilir. TSMC'nin tedarik zinciri üzerindeki potansiyel uzun vadeli etkileri daha fazla tartışılabilir, bu da günü nötr veya hafif pozitif bir aralıkta tutar. Volatilite aralığının alt bandında hafif dalgalanmalar beklenmektedir."
  },
  {
    "gun": "2025-02-18",
    "hafta_ici": "Salı",
    "etki_yonu": "nötr/hafif pozitif",
    "tahmin_min": "-0.8%",
    "tahmin_max": "2.5%",
    "guven_skoru": "0.65",
    "guven_gerekcesi": "Uzun hafta sonu sonrası piyasanın genel eğilimi, haberin uzun vadeli etkilerine dair yeni yorumlar ve olası yeni makroekonomik veriler fiyatlamayı etkileyebilir.",
    "analiz_mantigi": "Uzun hafta sonu, yatırımcılara haberleri ve olası yeni analist raporlarını sindirme fırsatı verdi. Bu durum, hisse senedinin ya hafif bir toparlanma göstermesine ya da daha geniş piyasa dinamiklerine uyum sağlamasına neden olabilir. Haber etkisi hala hissedilse de, genel piyasa koşulları ve sektördeki diğer gelişmeler daha belirleyici olacaktır. Volatilite aralığının alt ve orta bandında bir hareket beklenir."
  },
  {
    "gun": "2025-02-19",
    "hafta_ici": "Çarşamba",
    "etki_yonu": "nötr",
    "tahmin_min": "-1.2%",
    "tahmin_max": "1.2%",
    "guven_skoru": "0.55",
    "guven_gerekcesi": "Haber etkisi büyük ölçüde fiyatlara yansıdığı için, hisse senedi genel piyasa koşulları ve sektördeki diğer gelişmelerle daha uyumlu hareket edecektir.",
    "analiz_mantigi": "Haber etkisi büyük ölçüde fiyatlara yansıdığı ve piyasa tarafından sindirildiği için, hisse senedi artık daha çok genel piyasa koşulları, sektördeki diğer gelişmeler ve makroekonomik verilerle uyumlu hareket edecektir. Volatilite aralığının alt ve orta bandında, nötr bir seyirle konsolidasyonun devam etmesi beklenir."
  }
]"""

haber_listesi_str = """[{'uuid': 'c3d65475-81f6-432b-803c-2ec0311b0589', 'title': 'Asia-Pacific stocks set to rise after soft U.S. inflation report pushes two Wall Street benchmarks up', 'description': 'The\xa0Nasdaq Composite\xa0picked up after the soft inflation report eased concerns about a looming recession and as investors snapped up technology shares.', 'keywords': 'business news', 'snippet': 'Asia-Pacific markets are primed to rise on Thursday after a soft inflation report in the U.S. helped two of the three benchmarks on Wall Street reverse course f...', 'url': 'https://www.cnbc.com/2025/03/13/asia-markets-live-stocks-set-to-rise.html', 'image_url': 'https://image.cnbcfm.com/api/v1/image/108111720-1741222137015-gettyimages-1150967171-dsc00001.jpeg?v=1741222153&w=1920&h=1080', 'language': 'en', 'published_at': '2025-03-12T23:32:38.000000Z', 'source': 'cnbc.com', 'relevance_score': None, 'entities': [{'symbol': 'NVDA', 'name': 'NVIDIA Corporation', 'exchange': None, 'exchange_long': None, 'country': 'us', 'type': 'equity', 'industry': 'Technology', 'match_score': 11.461531, 'sentiment_score': 0.6597, 'highlights': [{'highlight': 'Top performers include <em>Nvidia</em>, [+121 characters]', 'sentiment': 0.6597, 'highlighted_in': 'main_text'}]}, {'symbol': 'TSLA', 'name': 'Tesla, Inc.', 'exchange': None, 'exchange_long': None, 'country': 'us', 'type': 'equity', 'industry': 'Consumer Cyclical', 'match_score': 11.175431, 'sentiment_score': 0.25, 'highlights': [{'highlight': 'Meanwhile, Meta Platforms advanced 2% an[+37 characters]', 'sentiment': 0.25, 'highlighted_in': 'main_text'}]}, {'symbol': 'GAN', 'name': 'GAN Limited', 'exchange': None, 'exchange_long': None, 'country': 'us', 'type': 'equity', 'industry': 'Consumer Cyclical', 'match_score': 14.348455, 'sentiment_score': 0, 'highlights': [{'highlight': 'Viewers can watch the live stream of the[+315 characters]', 'sentiment': 0, 'highlighted_in': 'main_text'}]}, {'symbol': '^AXJO', 'name': 'S&P/ASX 200', 'exchange': None, 'exchange_long': None, 'country': 'au', 'type': 'index', 'industry': 'N/A', 'match_score': 44.528442, 'sentiment_score': -0.4019, 'highlights': [{'highlight': "Australia's <em>S</em>&<em>P</em>/<em>AS[+318 characters]", 'sentiment': -0.4019, 'highlighted_in': 'main_text'}]}, {'symbol': '^HSI', 'name': 'HANG SENG INDEX', 'exchange': None, 'exchange_long': None, 'country': 'hk', 'type': 'index', 'industry': 'N/A', 'match_score': 38.896606, 'sentiment_score': 0.4019, 'highlights': [{'highlight': "Futures for Hong Kong's <em>Hang</em> <e[+288 characters]", 'sentiment': 0.4019, 'highlighted_in': 'main_text'}]}, {'symbol': '^N225', 'name': 'Nikkei 225', 'exchange': None, 'exchange_long': None, 'country': 'jp', 'type': 'index', 'industry': 'N/A', 'match_score': 28.341152, 'sentiment_score': 0.3818, 'highlights': [{'highlight': 'Over in Japan, the benchmark <em>Nikkei<[+314 characters]', 'sentiment': 0.3818, 'highlighted_in': 'main_text'}]}], 'similar': []}, {'uuid': 'c37223b0-a5b0-41db-b2f0-7b4aa6142a93', 'title': 'Wall Street en ordre dispersÃ©, entre inflation rassurante et craintes commerciales', 'description': 'Tendance publiÃ©e le 13/03/2025. NEW YORK (Reuters) -     La Bourse de New York a fini en ordre dispersÃ© mercredi, le S&P-500...', 'keywords': "bourse, information boursière, information financière, actions, cours de bourse, entrepreneurs, actionnaires, interview corporate, recommandation boursière, pédagogie de l'économie, de la finance et la bourse, gestion d'actifs, gérants", 'snippet': 'PrÃ©-ouverture Wall Street en ordre dispersÃ©, entre inflation rassurante et craintes commerciales\n\npublié le 13/03/2025\n\nNEW YORK (Reuters) - La Bourse de...', 'url': 'https://www.easybourse.com/marches/point-tendance/62615/wall-street-en-ordre-disperse-entre-inflation-rassurante-craintes-commerciales.html', 'image_url': 'https://media.easybourse.com/upload/media/image/144000/144081/favicon.jpg', 'language': 'fr', 'published_at': '2025-03-12T23:10:12.000000Z', 'source': 'easybourse.com', 'relevance_score': None, 'entities': [{'symbol': 'AMD', 'name': 'Advanced Micro Devices, Inc.', 'exchange': None, 'exchange_long': None, 'country': 'us', 'type': 'equity', 'industry': 'Technology', 'match_score': 18.25085, 'sentiment_score': None, 'highlights': [{'highlight': 'A noter, cÃ´tÃ© valeurs, Intel a pri[+259 characters]', 'sentiment': 0.0258, 'highlighted_in': 'main_text'}]}, {'symbol': 'AVGO', 'name': 'Broadcom Inc.', 'exchange': None, 'exchange_long': None, 'country': 'us', 'type': 'equity', 'industry': 'Technology', 'match_score': 12.975784, 'sentiment_score': None, 'highlights': [{'highlight': 'A noter, cÃ´tÃ© valeurs, Intel a pri[+241 characters]', 'sentiment': 0.2023, 'highlighted_in': 'main_text'}]}, {'symbol': 'AVGOP', 'name': 'Broadcom Inc.', 'exchange': None, 'exchange_long': None, 'country': 'us', 'type': 'equity', 'industry': 'Technology', 'match_score': 12.975784, 'sentiment_score': None, 'highlights': [{'highlight': 'A noter, cÃ´tÃ© valeurs, Intel a pri[+241 characters]', 'sentiment': 0.2732, 'highlighted_in': 'main_text'}]}, {'symbol': 'NVDA', 'name': 'NVIDIA Corporation', 'exchange': None, 'exchange_long': None, 'country': 'us', 'type': 'equity', 'industry': 'Technology', 'match_score': 11.195826, 'sentiment_score': None, 'highlights': [{'highlight': 'A noter, cÃ´tÃ© valeurs, Intel a pri[+241 characters]', 'sentiment': 0.2732, 'highlighted_in': 'main_text'}]}, {'symbol': 'PEP', 'name': 'PepsiCo, Inc.', 'exchange': None, 'exchange_long': None, 'country': 'us', 'type': 'equity', 'industry': 'Consumer Defensive', 'match_score': 13.128554, 'sentiment_score': None, 'highlights': [{'highlight': '<em>PepsiCo</em> a perdu 2,7% aprÃ¨s q[+83 characters]', 'sentiment': 0.91, 'highlighted_in': 'main_text'}]}], 'similar': []}, {'uuid': '8b08a3ad-0da3-43ea-8e91-0e7d13d30728', 'title': 'Wall Street cierra mixto en jornada marcada por dato de inflación en EU', 'description': 'El Nasdaq subió un 1.22% impulsado por gigantes tecnológicos, mientras que el Dow Jones cayó un 0.20% tras conocerse una baja mayor a la esperada en la inflación de EU', 'keywords': '', 'snippet': 'Wall Street cerró en terreno mixto este miércoles y con el índice tecnológico Nasdaq repuntando un 1.22% a 17,648 puntos, en una jornada marcada por el aliv...', 'url': 'https://forbes.com.mx/wall-street-cierra-mixto-en-jornada-marcada-por-dato-de-inflacion-en-eu/', 'image_url': 'https://cdn.forbes.com.mx/2023/03/wall-street-02.webp', 'language': 'es', 'published_at': '2025-03-12T22:54:29.000000Z', 'source': 'forbes.com.mx', 'relevance_score': None, 'entities': [{'symbol': 'TSLA', 'name': 'Tesla, Inc.', 'exchange': None, 'exchange_long': None, 'country': 'us', 'type': 'equity', 'industry': 'Consumer Cyclical', 'match_score': 13.458532, 'sentiment_score': None, 'highlights': [{'highlight': 'Lee: La mordaz predicción de JPMorgan s[+322 characters]', 'sentiment': 0.0258, 'highlighted_in': 'main_text'}, {'highlight': 'En este contexto, hoy se registró un re[+177 characters]', 'sentiment': 0.0258, 'highlighted_in': 'main_text'}]}, {'symbol': 'REGN', 'name': 'Regeneron Pharmaceuticals, Inc.', 'exchange': None, 'exchange_long': None, 'country': 'us', 'type': 'equity', 'industry': 'Healthcare', 'match_score': 21.486261, 'sentiment_score': None, 'highlights': [{'highlight': 'Lee: EU dice que no negociará aranceles[+279 characters]', 'sentiment': 0.91, 'highlighted_in': 'main_text'}]}, {'symbol': 'NVDA', 'name': 'NVIDIA Corporation', 'exchange': None, 'exchange_long': None, 'country': 'us', 'type': 'equity', 'industry': 'Technology', 'match_score': 11.798909, 'sentiment_score': None, 'highlights': [{'highlight': 'En este contexto, hoy se registró un re[+177 characters]', 'sentiment': 0.6808, 'highlighted_in': 'main_text'}]}], 'similar': []}]"""