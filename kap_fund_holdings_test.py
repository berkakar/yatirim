"""Geçici doğrulama - önceki tur, "all-search" kutusunun site geneli arama
kutusu olduğunu (tabloyla ilgisiz - Google Analytics form_start olayı
tetikledi, tablo verisi çekmedi) ve ayrı bir "Filtrele" (aria-label)
düğmesi ile "custom-select" alanları olduğunu gösterdi. Zaten bu fonun
kendi sayfasındayız (/tr/fon-bildirimleri/<TPR-slug>) - muhtemelen tablo
bu fona zaten scoped, sadece "Filtrele"ye basmak (varsayılan tarih
aralığıyla) gerçek veriyi tetikleyecektir.
"""
from playwright.sync_api import sync_playwright

URL = "https://www.kap.org.tr/tr/fon-bildirimleri/tpr-is-portfoy-py-hisse-senedi-tl-ozel-fonu-hisse-senedi-yogun-fon"

captured = []

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()

    def on_response(response):
        captured.append({"url": response.url, "status": response.status, "method": response.request.method})

    page.on("response", on_response)

    print(f"Navigating to {URL} ...")
    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)
    before = len(captured)
    print(f"Before clicking Filtrele: {before} responses captured.")

    try:
        filtrele = page.get_by_label("Filtrele", exact=False)
        if filtrele.count() == 0:
            filtrele = page.locator("[aria-label='Filtrele']")
        filtrele.first.click(timeout=10000)
        print("Clicked the 'Filtrele' button.")
    except Exception as e:
        print(f"Could not click Filtrele: {e}")

    page.wait_for_timeout(6000)
    print(f"\nResponses after clicking Filtrele: {len(captured)} (was {before})")
    for c in captured[before:]:
        if "google-analytics" not in c["url"] and "gifload" not in c["url"]:
            print(f"  {c['method']} {c['status']} {c['url']}")

    hrefs = page.eval_on_selector_all(
        "a[href*='Bildirim']", "els => els.map(e => ({href: e.getAttribute('href'), text: e.innerText}))"
    )
    print(f"\nRendered <a href*=Bildirim> links after Filtrele: {len(hrefs)}")
    for h in hrefs[:40]:
        print(f"  {h}")

    # Dump any <table> rows now visible.
    rows = page.eval_on_selector_all(
        "table tr", "els => els.slice(0, 40).map(e => e.innerText.replace(/\\s+/g, ' ').trim())"
    )
    print(f"\n<table tr> rows found: {len(rows)}")
    for row in rows:
        print(f"  {row}")

    body_text = page.evaluate("document.body.innerText")
    idx = body_text.lower().find("portföy dağılım")
    print(f"\n'Portföy Dağılım' found in final rendered text: {idx != -1}")
    if idx != -1:
        print(body_text[max(0, idx - 200):idx + 1000])

    browser.close()
