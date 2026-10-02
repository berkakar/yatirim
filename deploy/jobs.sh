# shellcheck shell=bash disable=SC2034
# Droplet'te çalışan zamanlanmış işlerin tanımları - run_job.sh tarafından
# `source` edilir. Her iş, eskiden .github/workflows/ altında aynı adlı
# workflow'un yaptığının birebir karşılığıdır:
#
#   JOB_CMDS    - sırayla çalıştırılacak komutlar (repo kökünde, venv'in
#                 python'u PATH'te)
#   JOB_FILES   - komutlar bittikten sonra değişmişse commit'lenecek state
#                 dosyaları (workflow'lardaki "Commit and push" adımı)
#   JOB_MSG     - commit mesajı ([skip ci] run_job.sh tarafından eklenir)
#   JOB_TIMEOUT - komut başına saniye cinsinden üst sınır; aşılırsa komut
#                 öldürülür ve Telegram'a hata bildirimi gider
#
# Zamanlamalar burada değil, deploy/systemd/yatirim-<iş>.timer dosyalarında.

ALPACA_STATE_FILES=(
  alpaca_realized_pnl_cache_berkakar.json
  alpaca_daily_bars_cache_berkakar.json
  alpaca_intraday_bars_cache_berkakar.json
  alpaca_position_management_cache_berkakar.json
  buy_stop_rebuy_state_berkakar.json
  orb_scan_holdings_berkakar.json
  relative_strength_holdings_berkakar.json
  ha_intraday_holdings_berkakar.json
)

job_define() {
  JOB_CMDS=()
  JOB_FILES=()
  JOB_MSG=""
  JOB_TIMEOUT=900

  case "$1" in
    trailing-stop)  # alpaca_trailing_stop.yml
      JOB_CMDS=(
        "python alpaca_buy_points.py --once"
        "python alpaca_trailing_stop.py --once"
        "python buy_stop_rebuy.py --once"
      )
      JOB_FILES=("${ALPACA_STATE_FILES[@]}")
      JOB_MSG="Update Alpaca automation state cache"
      ;;
    ext-hours-guard)  # alpaca_extended_hours_guard.yml (commit adımı yoktu)
      JOB_CMDS=("python alpaca_trailing_stop.py --extended-hours-guard")
      JOB_TIMEOUT=540
      ;;
    ext-hours-entries)  # alpaca_extended_hours_entries.yml
      JOB_CMDS=("python alpaca_buy_points.py --extended-hours-entries")
      JOB_FILES=(
        alpaca_realized_pnl_cache_berkakar.json
        alpaca_daily_bars_cache_berkakar.json
        alpaca_intraday_bars_cache_berkakar.json
      )
      JOB_MSG="Update Alpaca automation state cache"
      JOB_TIMEOUT=540
      ;;
    heikin-ashi)  # heikin_ashi_intraday.yml
      JOB_CMDS=("python heikin_ashi_intraday_runner.py --once")
      JOB_FILES=(ha_intraday_config_berkakar.json ha_intraday_holdings_berkakar.json)
      JOB_MSG="Update Heikin Ashi Gün İçi state"
      ;;
    orb-scan)  # orb_scan.yml
      JOB_CMDS=("python orb_scan_runner.py --once")
      JOB_FILES=(orb_scan_config_berkakar.json orb_scan_holdings_berkakar.json)
      JOB_MSG="Update Açılış Aralığı Kırılımı (ORB) state"
      JOB_TIMEOUT=1500
      ;;
    otomatik-alim-satim)  # otomatik_alim_satim.yml
      JOB_CMDS=("python otomatik_alim_satim_runner.py --once")
      JOB_FILES=(
        otomatik_alim_satim_config_berkakar.json
        portfolio_config_berkakar.json
        backtest_results_berkakar.json
      )
      JOB_MSG="Update Otomatik Alım/Satım state"
      JOB_TIMEOUT=3600
      ;;
    relative-strength)  # relative_strength.yml
      JOB_CMDS=("python relative_strength_runner.py --once")
      JOB_FILES=(relative_strength_config_berkakar.json relative_strength_holdings_berkakar.json)
      JOB_MSG="Update Relative Strength Rotasyonu state"
      JOB_TIMEOUT=1800
      ;;
    tefas)  # tefas_fonlari.yml
      JOB_CMDS=("python tefas_fetch.py --once")
      JOB_FILES=(tefas_fonlari_cache.json)
      JOB_MSG="Update Türk Fonları cache"
      ;;
    fon-hisse-uyari)  # fon_hisse_uyari.yml - eskiden job içinde 5 dk'lık sleep
                      # döngüsüyle ~4 saat canlı kalıyordu; artık timer 5 dk'da
                      # bir tek seferlik çalıştırıyor.
      JOB_CMDS=("python fon_hisse_uyari.py --once")
      JOB_FILES=(bildirim_durumu.json)
      JOB_MSG="Update bildirim durumu"
      JOB_TIMEOUT=240
      ;;
    russell2000)  # update_russell2000.yml - install.sh --with-playwright gerektirir
      JOB_CMDS=("python russell2000_runner.py")
      JOB_FILES=(custom_tickers_berkakar.json)
      JOB_MSG="Update Russell 2000 ticker list"
      JOB_TIMEOUT=1800
      ;;
    *)
      return 1
      ;;
  esac
}

ALL_JOBS=(
  trailing-stop
  ext-hours-guard
  ext-hours-entries
  heikin-ashi
  orb-scan
  otomatik-alim-satim
  relative-strength
  tefas
  fon-hisse-uyari
  russell2000
)
