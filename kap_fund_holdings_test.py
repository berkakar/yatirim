"""Geçici doğrulama - /tr/fon-bildirimleri/<slug> sayfasını gerçek
tarayıcıyla (Playwright) açıp: (1) sayfa yüklendikten sonra hangi
/tr/api/ çağrılarının yapıldığını, (2) JS çalıştıktan sonra sayfanın
görünen metninde "Portföy Dağılım Raporu" geçen bir tablo olup olmadığını
kontrol ediyoruz. ozet sayfasında (önceki tur) ayrı bir bildirim-listesi
API çağrısı YOKTU - sadece birkaç dosya önizleme isteği vardı; bu yüzden
doğrudan "fon-bildirimleri" (fon disclosures) sayfasına gidiyoruz.
"""
from playwright.sync_api import sync_playwright

URL = "https://www.kap.org.tr/tr/fon-bildirimleri/tpr-is-portfoy-py-hisse-senedi-tl-ozel-fonu-hisse-senedi-yogun-fon"

captured = []

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()

    def on_response(response):
        url = response.url
        if "/tr/api/" in url:
            captured.append({"url": url, "status": response.status, "method": response.request.method})

    page.on("response", on_response)

    print(f"Navigating to {URL} ...")
    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)

    print(f"\nTotal /tr/api/ calls captured: {len(captured)}")
    for c in captured:
        print(f"  {c['method']} {c['status']} {c['url']}")

    body_text = page.evaluate("document.body.innerText")
    print(f"\nRendered body text length: {len(body_text)}")
    idx = body_text.lower().find("portföy dağılım")
    if idx == -1:
        idx = body_text.lower().find("portfoy dagilim")
    if idx != -1:
        print(f"Found 'Portföy Dağılım' in rendered text at offset {idx}:")
        print(body_text[max(0, idx - 300):idx + 1000])
    else:
        print("'Portföy Dağılım' NOT found in rendered text.")
        print("First 2000 chars of rendered body text:")
        print(body_text[:2000])

    # Grab any anchor hrefs on the fully-rendered page pointing to a Bildirim.
    hrefs = page.eval_on_selector_all("a[href*='Bildirim']", "els => els.map(e => ({href: e.getAttribute('href'), text: e.innerText}))")
    print(f"\nRendered <a href*=Bildirim> links: {len(hrefs)}")
    for h in hrefs[:30]:
        print(f"  {h}")

    browser.close()
