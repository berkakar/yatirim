"""Geçici doğrulama - fon-bildirimleri sayfasında bulunan tablo başlığı
(# Tarih Kod Fon Tip Konu Özet Bilgi ... Yıl Periyot İşlemler) doğru
sayfayı bulduğumuzu kanıtlıyor, ama satırlar ilk yüklemede boş geldi.
Bu turda kullanıcının tarif ettiği gerçek etkileşimi taklit ediyoruz:
bir arama kutusuna fon kodunu yazıp büyüteç/ara düğmesine basmak (ya da
Enter). Ayrıca daha uzun bekleme süresi ve tüm ağ isteklerini (sadece
/tr/api/ değil, TÜM istekleri) yakalıyoruz.
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
    page.wait_for_timeout(5000)
    print(f"After initial load + 5s wait: {len(captured)} total responses captured.")

    # Look for any search/filter input on the page.
    inputs = page.eval_on_selector_all(
        "input", "els => els.map(e => ({type: e.type, placeholder: e.placeholder, name: e.name, id: e.id}))"
    )
    print(f"\n<input> elements found: {len(inputs)}")
    for inp in inputs[:20]:
        print(f"  {inp}")

    buttons = page.eval_on_selector_all(
        "button", "els => els.map(e => ({text: e.innerText.trim(), aria: e.getAttribute('aria-label')}))"
    )
    print(f"\n<button> elements found: {len(buttons)}")
    for b in buttons[:20]:
        print(f"  {b}")

    before_count = len(captured)
    # Try typing "TPR" into the most likely search input and pressing Enter.
    try:
        search_input = page.locator("input[type='text'], input[type='search']").first
        search_input.click(timeout=5000)
        search_input.fill("TPR")
        search_input.press("Enter")
        print("\nTyped 'TPR' into first text/search input and pressed Enter.")
    except Exception as e:
        print(f"\nCould not fill a search input: {e}")

    page.wait_for_timeout(6000)
    print(f"Responses after search attempt: {len(captured)} (was {before_count})")
    for c in captured[before_count:]:
        print(f"  {c['method']} {c['status']} {c['url']}")

    hrefs = page.eval_on_selector_all(
        "a[href*='Bildirim']", "els => els.map(e => ({href: e.getAttribute('href'), text: e.innerText}))"
    )
    print(f"\nRendered <a href*=Bildirim> links after search: {len(hrefs)}")
    for h in hrefs[:30]:
        print(f"  {h}")

    body_text = page.evaluate("document.body.innerText")
    idx = body_text.lower().find("portföy dağılım")
    print(f"\n'Portföy Dağılım' found in final rendered text: {idx != -1}")
    if idx != -1:
        print(body_text[max(0, idx - 200):idx + 800])

    browser.close()
