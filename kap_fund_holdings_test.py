"""Geçici doğrulama - gerçek tarayıcı (Playwright/Chromium) ile KAP fon
özet sayfasını açıp "Bildirimler" sekmesine tıklayarak, sayfanın o an
gerçekten hangi API'yi çağırdığını ağ trafiğinden yakalıyoruz. Önceki
turlarda düz requests.get() ile denenen her uç nokta (member/filter,
byCriteria + çeşitli OID kombinasyonları, batch-news, fon-bildirimleri
sayfası) ya yanlış veri döndürdü ya da boş kaldı - bu, gerçek listenin
sayfa yüklendikten SONRA istemci tarafı JS ile çekildiğini düşündürüyor,
ki bu düz HTTP isteğiyle hiç görülemez.
"""
import json

from playwright.sync_api import sync_playwright

URL = "https://www.kap.org.tr/tr/fon-bilgileri/ozet/tpr-is-portfoy-py-hisse-senedi-tl-ozel-fonu-hisse-senedi-yogun-fon"

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
    print(f"Initial load captured {len(captured)} /tr/api/ calls.")

    # Try to find and click a "Bildirimler" tab/link.
    clicked = False
    for text in ["Bildirimler", "Bildirim", "Duyurular"]:
        try:
            locator = page.get_by_text(text, exact=False).first
            if locator.count() > 0:
                locator.click(timeout=5000)
                clicked = True
                print(f"Clicked element with text matching {text!r}.")
                page.wait_for_timeout(4000)
                break
        except Exception as e:
            print(f"Could not click {text!r}: {e}")

    if not clicked:
        print("No 'Bildirimler'-like tab found to click - scrolling instead.")
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(3000)

    print(f"\nTotal /tr/api/ calls captured: {len(captured)}")
    for c in captured:
        print(f"  {c['method']} {c['status']} {c['url']}")

    # For any call whose URL looks disclosure/fund-related, fetch and print its body.
    for c in captured:
        if any(k in c["url"].lower() for k in ["bildirim", "disclosure", "notification", "fund", "fon"]):
            try:
                resp = page.request.get(c["url"])
                body = resp.text()
                print(f"\n--- Body of {c['url']} (first 2000 chars) ---")
                print(body[:2000])
            except Exception as e:
                print(f"Could not refetch {c['url']}: {e}")

    browser.close()
