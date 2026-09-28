# yatirim
Kendi çapımda yatırım araçları. Fincan Kulp ve Stop Loss

## Testler

```bash
python -m unittest discover -s tests -t .       # birim + sahte Alpaca istemcili entegrasyon testleri
python scripts/compare_stop_algorithms.py       # stop kurallarının günlük önbellekle karşılaştırması
```

## Değişiklik günlüğü

Sistemdeki davranış değişiklikleri (gerekçe, kod yeri, ayar, takip ölçütü) uygulamada
**🤖 Algoritmik Ticaret > 📒 İşlem Günlüğü > 📝 Değişiklik Günlüğü** sekmesinde, kaynağı
`changelog.py` dosyasında. Kodda ilgili yerler `[2026-09-28 · Öneri N]` yorumuyla işaretli.
