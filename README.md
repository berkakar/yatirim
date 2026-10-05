# yatirim
Kendi çapımda yatırım araçları. Fincan Kulp ve Stop Loss

## Testler

```bash
python -m unittest discover -s tests -t .       # birim + sahte Alpaca istemcili entegrasyon testleri
python scripts/compare_stop_algorithms.py       # stop kurallarının günlük önbellekle karşılaştırması
python scripts/backtest_adaptive_stop.py        # Akıllı Dinamik Stop walk-forward doğrulaması (--intraday, --yahoo)
# Uzun geçmişle: uygulamada BackTest > 📦 Backtest Veri Paketi, sonra
git fetch origin backtest-data && git checkout origin/backtest-data -- backtest_data
python scripts/backtest_adaptive_stop.py --pack backtest_data --intraday
```

## Değişiklik günlüğü

Sistemdeki davranış değişiklikleri (gerekçe, kod yeri, ayar, takip ölçütü) uygulamada
**🤖 Algoritmik Ticaret > 🧠 Algo Analiz > 📝 Değişiklik Günlüğü** sekmesinde, kaynağı
`changelog.py` dosyasında. Kodda ilgili yerler `[2026-09-28 · Öneri N]` yorumuyla işaretli.
