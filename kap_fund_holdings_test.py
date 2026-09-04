"""Geçici doğrulama - SORUN BULUNDU: "Bildirim tipi için seçim yapınız"
(disclosure type) alanı boş bırakıldığı için "Lütfen bildirim tipi
seçimini yapınız..." doğrulama hatası veriyordu. Tarih aralığı zaten
"Son 1 yıl" (365 gün) varsayılanına sahip. Bu turda bildirim tipi
dropdown'ını açıp gerçek seçenekleri listeliyoruz, "Portföy Dağılım
Raporu" (veya en yakın eşleşen) varsa seçip Filtrele'ye basıyoruz ve
sonuç tablosunu okuyoruz.
"""
from playwright.sync_api import sync_playwright

URL = "https://www.kap.org.tr/tr/fon-bildirimleri/tpr-is-portfoy-py-hisse-senedi-tl-ozel-fonu-hisse-senedi-yogun-fon"

captured = []

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()

    def on_response(response):
        if "/tr/api/" in response.url:
            captured.append({"url": response.url, "status": response.status})

    page.on("response", on_response)

    print(f"Navigating to {URL} ...")
    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(2000)

    # Click the "Bildirim tipi için seçim yapınız" combobox to open the MUI dropdown.
    type_combo = page.locator("[aria-label='Bildirim tipi için seçim yapınız']").first
    type_combo.click(timeout=10000)
    page.wait_for_timeout(1000)

    # MUI renders options in a portal - look for any listbox/option elements anywhere.
    options = page.eval_on_selector_all(
        "[role='option'], li[role='option'], ul[role='listbox'] li",
        "els => els.map(e => e.innerText.trim())",
    )
    print(f"Dropdown options found: {len(options)}")
    for o in options:
        print(f"  {o!r}")

    # Try to click an option matching "Portföy Dağılım" (case-insensitive), else the first non-empty one.
    target = None
    for o in options:
        if "portföy dağılım" in o.lower() or "portfoy dagilim" in o.lower():
            target = o
            break
    if not target and options:
        target = options[0]
    print(f"\nSelecting option: {target!r}")

    if target:
        try:
            page.get_by_role("option", name=target, exact=True).click(timeout=5000)
        except Exception as e:
            print(f"Exact role click failed ({e}), trying text click...")
            page.get_by_text(target, exact=True).last.click(timeout=5000)
        page.wait_for_timeout(1500)

    before = len(captured)
    page.locator("[aria-label='Filtrele']").first.click(timeout=10000)
    page.wait_for_timeout(6000)

    print(f"\n/tr/api/ calls after Filtrele: {len(captured) - before}")
    for c in captured[before:]:
        print(f"  {c}")

    rows = page.eval_on_selector_all(
        "table tr", "els => els.map(e => e.innerText.replace(/\\s+/g, ' ').trim())"
    )
    print(f"\n<table tr> rows: {len(rows)}")
    for row in rows[:30]:
        print(f"  {row!r}")

    hrefs = page.eval_on_selector_all(
        "a[href*='Bildirim']", "els => els.map(e => ({href: e.getAttribute('href'), text: e.innerText}))"
    )
    print(f"\n<a href*=Bildirim> links: {len(hrefs)}")
    for h in hrefs[:30]:
        print(f"  {h}")

    browser.close()
