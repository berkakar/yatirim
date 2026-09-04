"""Geçici doğrulama - Filtrele düğmesine basınca tablo hâlâ boş kaldı.
Bu son turda: iki "custom-select" alanının (muhtemelen Yıl/Periyot ya da
tarih aralığı) yakın çevresindeki etiket metnini ve olası <option>
değerlerini çıkarıyoruz, ayrıca Filtrele sonrası herhangi bir doğrulama/
hata mesajı belirip belirmediğine bakıyoruz - tarih alanları boş
bırakıldığı için sorgunun sessizce reddedilmiş olması ihtimaline karşı.
"""
from playwright.sync_api import sync_playwright

URL = "https://www.kap.org.tr/tr/fon-bildirimleri/tpr-is-portfoy-py-hisse-senedi-tl-ozel-fonu-hisse-senedi-yogun-fon"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    print(f"Navigating to {URL} ...")
    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)

    # Dump the full filter-area HTML (a reasonably sized chunk around the
    # custom-select elements) so we can see labels, option lists, and any
    # surrounding structure without guessing selectors blindly.
    try:
        filter_area = page.locator("#custom-select").first
        container_html = filter_area.evaluate(
            "el => { let n = el; for (let i = 0; i < 5 && n.parentElement; i++) n = n.parentElement; return n.outerHTML; }"
        )
        print(f"Filter area HTML (~{len(container_html)} chars):")
        print(container_html[:6000])
    except Exception as e:
        print(f"Could not extract filter area HTML: {e}")

    # Any <select> elements and their options.
    selects = page.eval_on_selector_all(
        "select",
        "els => els.map(e => ({id: e.id, name: e.name, options: Array.from(e.options).map(o => o.text + '=' + o.value)}))",
    )
    print(f"\n<select> elements: {len(selects)}")
    for s in selects:
        print(f"  {s}")

    # Click Filtrele and look for any error/validation text appearing.
    try:
        page.locator("[aria-label='Filtrele']").first.click(timeout=10000)
        page.wait_for_timeout(3000)
    except Exception as e:
        print(f"Filtrele click failed: {e}")

    body_text = page.evaluate("document.body.innerText")
    for kw in ["zorunlu", "seçiniz", "hata", "gerekli", "lütfen", "sonuç bulunamadı", "kayıt bulunamadı"]:
        if kw in body_text.lower():
            idx = body_text.lower().find(kw)
            print(f"\nFound keyword {kw!r} in body text near: ...{body_text[max(0,idx-100):idx+200]}...")

    rows = page.eval_on_selector_all(
        "table tr", "els => els.map(e => e.innerText.replace(/\\s+/g, ' ').trim())"
    )
    print(f"\n<table tr> rows after Filtrele: {len(rows)}")
    for row in rows:
        print(f"  {row!r}")

    browser.close()
