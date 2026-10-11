"""
Yapay Zeka Analiz Modülü - eğitim verisi (ai_dataset.training_frame) üzerinde
çok değişkenli zaman serisi tahmini: Factorized Self-Attention, kanal bağımsız
(Channel-Independent), RevIN ve temporal embedding.

Veri hazırlığı (prepare_data):
    1. Eğitim verisi alınır; takvim sütunları (month, day_of_month, day_of_week,
       is_month_start, is_month_end) kanal değil, temporal embedding girdisidir.
       Diğer sayısal sütunlar kanaldır (her biri ayrı bir zaman serisi).
    2. Boş veri kontrolü: kalan boş hücreler zamana göre doğrusal interpolasyonla,
       uçlarda en yakın değerle doldurulur; tamamen boş kanal çıkarılır. Rapor edilir.
    3. Kronolojik bölme: ilk %80 eğitim, son %20 test (karıştırılmaz).
    4. Şok sıçramaların baskılanması - IQR (Tukey) eşikleme, k = 3: her kanal için
       [Q1 - 3·IQR, Q3 + 3·IQR] dışındaki değerler sınıra çekilir. Sınırlar YALNIZCA
       eğitim döneminden hesaplanır (test dönemine bakılmaz, sızıntı yok).
       Z-score yerine IQR: ortalama ve standart sapma şokların kendisinden etkilenir
       (finansal getiriler kalın kuyrukludur), çeyrekler etkilenmez. k = 3 yalnızca
       uç şokları keser; 1,5 olağan piyasa hareketlerini de kırpardı.
       Fiyat seviyesi (`close`) ve ikili / az değerli kanallar kırpılmaz: trend
       eden seviyede eğitim dönemi sınırı test dönemindeki meşru yükselişi keserdi.
       Bilanço oranları (valuation_*) ve 0-100 sınırlı göstergeler de kırpılmaz (NO_CLIP).

Model (FactorizedTransformer):
    - RevIN: her pencere ve kanal kendi ortalama / standart sapmasıyla normalize
      edilir (öğrenilen ölçek / kaydırma ile), tahmin aynı istatistiklerle geri
      çevrilir - yerel seviye ve oynaklık korunur, dağılım kayması azalır.
    - Kanal bağımsız: her kanal ayrı bir dizi olarak, ORTAK ağırlıklarla işlenir
      (kanallar arası dikkat yok) - az veride aşırı öğrenmeyi azaltır.
    - Token: her gün bir token; değer gömmesi (1 -> d_model) + öğrenilen konum
      gömmesi + temporal embedding (ay, ayın günü, haftanın günü, ay başı / sonu
      için ayrı öğrenilen gömmelerin toplamı). d_model = 128.
    - Factorized self-attention (Child ve ark., 2019, Sparse Transformer): tam
      L x L dikkat yerine iki adım - (1) yerel: B günlük bloklar içinde, (2) adımlı
      (strided): blokların aynı sıradaki günleri arasında. İki adım sonunda her gün
      dolaylı olarak tüm pencereyi görür; maliyet O(L^2) yerine O(L·(B + L/B)).
    - Çıkışlar: (1) her kanalın H günlük gelecek değeri (ortak doğrusal katman,
      yardımcı görev); kapanış kanalının temsilinden (2) h = 1..H günlük getiri
      ve (3) yön - yükseliş olasılığı (sigmoid). Fiyat tahmini getiriden
      üretilir: son kapanış × (1 + getiri).

Eğitim: kanallar eğitim döneminin ortalama / sapmasıyla standartlaştırılır.
Kayıp = w_channels · MSE(kanallar) + w_return · MSE(standartlaştırılmış getiri)
+ w_direction · BCE(yön); kapanış görevleri daha ağır (varsayılan 1 / 3 / 1). Erken durdurma için eğitim pencerelerinin son %10'u doğrulama
olarak ayrılır (test verisine bakılmaz). Değerlendirme test döneminde: fiyat hatası
"son fiyat değişmez" (naive), yön isabeti "her zaman yükseliş" ile karşılaştırılır;
yalnızca emin olunan tahminlerdeki (olasılık >= 0,6 veya <= 0,4) isabet ayrıca verilir.

Ağırlıklar ve sonuçlar veritabanında `ai_models` tablosunda tutulur.
"""

import io
import json
import math
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import storage

CALENDAR_COLS = ("month", "day_of_month", "day_of_week", "is_month_start", "is_month_end")
# Gömme tablosu boyutları (indeks aralığı + 1).
CALENDAR_SIZES = {"month": 13, "day_of_month": 32, "day_of_week": 7, "is_month_start": 2, "is_month_end": 2}
DROP_COLS = ("time_idx",)          # konum, öğrenilen konum gömmesiyle verilir
TARGET = "close"
# IQR ile kırpılmayanlar: trend eden seviye (close); bilanço oranları (çeyreklik basamaklarla
# değişir - yeni çeyreğin gerçek seviye değişimi eğitim aralığının dışında kalınca "şok" sanılıp
# kesilirdi); yapısı gereği 0-100 sınırlı göstergeler (yüzdelik sıralar, RSI, genişlik) - şok
# sıçraması olamaz.
NO_CLIP = ("close",)
NO_CLIP_PREFIXES = ("valuation_",)
BOUNDED_0_100 = ("rsi14", "sent_momentum", "sent_volatility", "nasdaq_100__momentum", "nasdaq_100__volatility",
                 "nasdaq_100__breadth", "nasdaq_100__breadth200", "nasdaq_100__highs_lows",
                 "nasdaq_100__safe_haven")


def is_clippable(col: str) -> bool:
    return not (col in NO_CLIP or col in BOUNDED_0_100 or col.startswith(NO_CLIP_PREFIXES))

DEFAULTS = {
    "lookback": 64,       # girdi penceresi (gün); block_size'ın katı olmalı
    "horizon": 5,         # tahmin ufku (gün)
    "block_size": 8,      # factorized attention yerel blok boyu
    "d_model": 128,       # gömme boyutu
    "n_heads": 8,
    "n_layers": 2,
    "d_ff": 256,
    "dropout": 0.1,
    "train_ratio": 0.8,
    "val_ratio": 0.1,     # eğitim pencerelerinin son kısmı - erken durdurma
    "iqr_k": 3.0,
    "epochs": 30,
    "patience": 5,
    "batch_size": 32,
    # Doğrulama / test pencere sayısı (tüm kanallarla). Ara tensörler pencere × kanal × gün ×
    # d_model büyür: 256'da tepe bellek ~5 GB'tı (1 GB sunucuda OOM). 8'de ~0,7 GB.
    "eval_batch_size": 8,
    "channels_per_batch": 12,   # eğitimde adım başına rastgele kanal sayısı (close her zaman dahil)
    # Kayıp ağırlıkları: kanalların gelecek değerleri (yardımcı görev) + kapanışın h günlük
    # getirisi + yön (yükselir / düşer olasılığı). Kapanış görevleri daha ağır.
    "w_channels": 1.0,
    "w_return": 3.0,
    "w_direction": 1.0,
    "lr": 1e-3,
    "weight_decay": 1e-4,
    "seed": 42,
}

MODEL_TABLE = "ai_models"
_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {MODEL_TABLE} (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    config      TEXT NOT NULL,
    prep        TEXT NOT NULL,
    metrics     TEXT NOT NULL,
    result      TEXT NOT NULL,
    weights     BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS {MODEL_TABLE}_ticker ON {MODEL_TABLE} (ticker, id);
"""
_initialized_paths = set()


def torch_available() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


# Düşük bellekli sunucu (ör. 1 GB droplet): Streamlit ~0,7 GB kullanırken eğitim ayrıca ~0,5-1 GB
# ister. Toplam RAM LOW_MEMORY_MB altındaysa (ve ayar açıkça verilmemişse) küçük adımlarla eğitilir;
# UI_MIN_MEMORY_MB altındaysa arayüzden eğitim kapatılır - Linux bellek dolunca siteyi öldürüyordu.
LOW_MEMORY_MB = 3000
UI_MIN_MEMORY_MB = 2000
LOW_MEMORY_PROFILE = {"batch_size": 16, "channels_per_batch": 8, "eval_batch_size": 4}


def total_memory_mb() -> float | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**20
    except (ValueError, OSError, AttributeError):
        return None


def effective_config(config: dict | None = None, memory_mb: float | None = None) -> dict:
    """Varsayılanlar + düşük bellek profili (gerekirse) + verilen ayarlar."""
    memory_mb = total_memory_mb() if memory_mb is None else memory_mb
    low = memory_mb is not None and memory_mb < LOW_MEMORY_MB
    cfg = {**DEFAULTS, **(LOW_MEMORY_PROFILE if low else {}), **(config or {})}
    cfg["low_memory"] = low
    return cfg


INSTALL_HINT = ("PyTorch kurulu değil. Sunucuda (CPU sürümü, ~200 MB): "
                "sudo -u yatirim /opt/yatirim/venv/bin/pip install torch "
                "--index-url https://download.pytorch.org/whl/cpu")


# ------------------------------------------------------------------------------
# Veri hazırlığı (torch gerektirmez)
# ------------------------------------------------------------------------------

def prepare_data(train_df: pd.DataFrame, train_ratio: float = DEFAULTS["train_ratio"],
                 iqr_k: float = DEFAULTS["iqr_k"]) -> dict:
    """Eğitim verisini modele hazırlar. Döner: values (n, C), calendar (n, 5),
    dates, channels, n_train ve bir rapor (boş veri, kırpma, bölme)."""
    df = train_df.sort_index()
    # Takvim: yalnızca embedding indeksleri (eğitim verisi dışında bir tablo verilirse de).
    import ai_dataset

    df = df.drop(columns=[c for c in df.columns if c in DROP_COLS
                          or (c in ai_dataset.TEMPORAL_COLS and c not in CALENDAR_COLS)])
    missing_cal = [c for c in CALENDAR_COLS if c not in df.columns]
    if missing_cal:
        raise ValueError(f"Takvim sütunları eksik: {', '.join(missing_cal)} - veri setini yeniden hazırlayın")
    calendar = df[list(CALENDAR_COLS)]
    channels_df = df.drop(columns=list(CALENDAR_COLS)).select_dtypes("number").astype(float)
    if TARGET not in channels_df.columns:
        raise ValueError(f"Hedef sütun '{TARGET}' eğitim verisinde yok")

    # 1) Boş veri kontrolü ve interpolasyon
    nan_before = channels_df.isna().sum()
    all_nan = [c for c in channels_df.columns if channels_df[c].isna().all()]
    channels_df = channels_df.drop(columns=all_nan)
    filled = channels_df.interpolate(method="time", limit_area="inside").ffill().bfill()
    nan_filled = {c: int(n) for c, n in nan_before.items() if n and c not in all_nan}
    cal_nan = int(calendar.isna().sum().sum())
    if cal_nan:
        calendar = calendar.ffill().bfill()
    calendar = calendar.astype(int)
    if filled.isna().any().any():
        raise ValueError("Interpolasyondan sonra boş hücre kaldı")

    # 2) Kronolojik bölme
    n = len(filled)
    n_train = int(math.floor(n * train_ratio))

    # 3) IQR eşikleme - sınırlar yalnızca eğitim döneminden
    clipped, bounds = {}, {}
    train_part = filled.iloc[:n_train]
    for c in filled.columns:
        if not is_clippable(c) or train_part[c].nunique() <= 3:
            continue
        q1, q3 = train_part[c].quantile([0.25, 0.75])
        iqr = q3 - q1
        if not np.isfinite(iqr) or iqr <= 0:
            continue
        lo, hi = q1 - iqr_k * iqr, q3 + iqr_k * iqr
        col = filled[c]
        out = (col < lo) | (col > hi)
        if out.any():
            clipped[c] = {"train": int(out.iloc[:n_train].sum()), "test": int(out.iloc[n_train:].sum())}
            filled[c] = col.clip(lo, hi)
        bounds[c] = (float(lo), float(hi))

    # 4) Genel standartlaştırma (eğitim dönemi ortalama / sapması): kanalların kayıptaki
    #    ağırlığını eşitler. Pencere bazında RevIN bunun üzerine uygulanır. Uzun süre sabit
    #    kalıp sıçrayan kanallarda (ör. değerleme skoru) pencere sapması ~0 olduğundan kayıp
    #    pencere ölçeğinde hesaplanamaz - genel ölçekte hesaplanır.
    mean = filled.iloc[:n_train].mean()
    std = filled.iloc[:n_train].std(ddof=0)
    std = std.where(std > 1e-12, 1.0)
    scaled = (filled - mean) / std

    dates = pd.DatetimeIndex(filled.index)
    report = {
        "rows": n,
        "channels": len(filled.columns),
        "nan_filled": nan_filled,
        "nan_filled_total": int(sum(nan_filled.values())),
        "dropped_all_nan": all_nan,
        "calendar_nan_filled": cal_nan,
        "iqr_k": iqr_k,
        "clipped": clipped,
        "clipped_total": int(sum(v["train"] + v["test"] for v in clipped.values())),
        "n_train": n_train,
        "n_test": n - n_train,
        "train_range": [dates[0].strftime("%Y-%m-%d"), dates[n_train - 1].strftime("%Y-%m-%d")],
        "test_range": [dates[n_train].strftime("%Y-%m-%d"), dates[-1].strftime("%Y-%m-%d")] if n > n_train else None,
    }
    return {
        "values": scaled.to_numpy(dtype=np.float32, copy=True),
        "raw": filled.to_numpy(dtype=np.float64, copy=True),
        "mean": mean.to_numpy(dtype=np.float64),
        "std": std.to_numpy(dtype=np.float64),
        "calendar": calendar.to_numpy(dtype=np.int64, copy=True),
        "dates": dates,
        "channels": list(filled.columns),
        "n_train": n_train,
        "bounds": bounds,
        "report": report,
    }


def make_windows(n: int, n_train: int, lookback: int, horizon: int, val_ratio: float) -> dict:
    """Pencere başlangıçları (tahminin ilk günü t; girdi [t-L, t), hedef [t, t+H)).
    Eğitim: hedefi tamamen eğitim döneminde olanlar; son val_ratio'su doğrulama.
    Test: tahmini test döneminde başlayanlar (girdi eğitim dönemine uzanabilir -
    geçmiş bilgi, sızıntı değil)."""
    train_t = [t for t in range(lookback, n - horizon + 1) if t + horizon <= n_train]
    test_t = [t for t in range(max(lookback, n_train), n - horizon + 1)]
    n_val = max(1, int(round(len(train_t) * val_ratio))) if len(train_t) > 10 else 0
    return {"train": train_t[:len(train_t) - n_val], "val": train_t[len(train_t) - n_val:], "test": test_t}


# ------------------------------------------------------------------------------
# Model (torch)
# ------------------------------------------------------------------------------

def _build_model_classes():
    import torch
    from torch import nn

    class RevIN(nn.Module):
        """Reversible Instance Normalization (Kim ve ark., 2022)."""

        def __init__(self, num_features: int, eps: float = 1e-5):
            super().__init__()
            self.eps = eps
            self.weight = nn.Parameter(torch.ones(num_features))
            self.bias = nn.Parameter(torch.zeros(num_features))

        def affine(self, ch):
            return (self.weight, self.bias) if ch is None else (self.weight[ch], self.bias[ch])

        def stats(self, x):                       # x: [B, L, C]
            mean = x.mean(dim=1, keepdim=True).detach()
            std = torch.sqrt(x.var(dim=1, keepdim=True, unbiased=False) + self.eps).detach()
            return mean, std

        def norm(self, x, mean, std, ch=None):
            w, b = self.affine(ch)
            return (x - mean) / std * w + b

        def denorm(self, y, mean, std, ch=None):  # y: [B, H, C]
            w, b = self.affine(ch)
            return (y - b) / (w + self.eps) * std + mean

    class TemporalEmbedding(nn.Module):
        def __init__(self, d_model: int):
            super().__init__()
            self.tables = nn.ModuleList(nn.Embedding(CALENDAR_SIZES[c], d_model) for c in CALENDAR_COLS)

        def forward(self, cal):                   # cal: [B, L, 5] -> [B, L, d]
            return sum(table(cal[..., i]) for i, table in enumerate(self.tables))

    class FactorizedBlock(nn.Module):
        """Pre-norm Transformer bloğu; dikkat iki faktöre ayrılmış: yerel blok
        içi + bloklar arası adımlı."""

        def __init__(self, d_model, n_heads, d_ff, dropout, block_size):
            super().__init__()
            self.b = block_size
            self.norm_local = nn.LayerNorm(d_model)
            self.norm_strided = nn.LayerNorm(d_model)
            self.norm_ff = nn.LayerNorm(d_model)
            # Dikkat ağırlıklarında dropout yok: CPU'da hızlı SDPA çekirdeğini kapatıyor ve
            # adımın ~%25'ini rastgele maske üretimine harcatıyordu; dropout artıklarda.
            self.local = nn.MultiheadAttention(d_model, n_heads, batch_first=True)
            self.strided = nn.MultiheadAttention(d_model, n_heads, batch_first=True)
            self.ff = nn.Sequential(nn.Linear(d_model, d_ff), nn.GELU(), nn.Dropout(dropout),
                                    nn.Linear(d_ff, d_model))
            self.drop = nn.Dropout(dropout)

        def forward(self, x):                     # x: [N, L, d], L = nb * b
            n, length, d = x.shape
            nb = length // self.b
            h = self.norm_local(x).reshape(n * nb, self.b, d)          # blok içi
            h, _ = self.local(h, h, h, need_weights=False)
            x = x + self.drop(h.reshape(n, length, d))
            h = self.norm_strided(x).reshape(n, nb, self.b, d).transpose(1, 2).reshape(n * self.b, nb, d)
            h, _ = self.strided(h, h, h, need_weights=False)             # bloklar arası
            h = h.reshape(n, self.b, nb, d).transpose(1, 2).reshape(n, length, d)
            x = x + self.drop(h)
            return x + self.drop(self.ff(self.norm_ff(x)))

    class FactorizedTransformer(nn.Module):
        def __init__(self, n_channels, lookback, horizon, d_model, n_heads, n_layers, d_ff, dropout,
                     block_size, target_idx=0, **_):
            super().__init__()
            self.target_idx = target_idx
            if lookback % block_size:
                raise ValueError("lookback, block_size'ın katı olmalı")
            self.revin = RevIN(n_channels)
            self.value = nn.Linear(1, d_model)
            self.position = nn.Parameter(torch.zeros(1, lookback, d_model))
            nn.init.normal_(self.position, std=0.02)
            self.temporal = TemporalEmbedding(d_model)
            self.blocks = nn.ModuleList(FactorizedBlock(d_model, n_heads, d_ff, dropout, block_size)
                                        for _ in range(n_layers))
            self.norm = nn.LayerNorm(d_model)
            self.head = nn.Sequential(nn.Flatten(1), nn.Dropout(dropout), nn.Linear(lookback * d_model, horizon))
            # Kapanış kanalının temsilinden: h = 1..H günlük getiri (standartlaştırılmış) ve
            # yükseliş olasılığı (logit). Kanal bağımsız omurga ortak; bu başlıklar yalnız kapanışta.
            self.return_head = nn.Sequential(nn.Flatten(1), nn.Dropout(dropout), nn.Linear(lookback * d_model, horizon))
            self.direction_head = nn.Sequential(nn.Flatten(1), nn.Dropout(dropout),
                                                nn.Linear(lookback * d_model, horizon))

        def forward(self, x, cal, ch_idx=None):
            """x: [B, L, C], cal: [B, L, 5] -> (kanal tahmini [B, H, C], getiri [B, H],
            yön logiti [B, H]). ch_idx: x yalnızca bu kanalları içeriyorsa indeksleri
            (RevIN'in kanal başına ölçeği ve kapanışın yeri için) - eğitimde kanal alt
            kümesi; kapanış her zaman içinde olmalı."""
            bsz, length, ch = x.shape
            mean, std = self.revin.stats(x)
            z = self.revin.norm(x, mean, std, ch_idx)
            z = z.permute(0, 2, 1).reshape(bsz * ch, length, 1)          # kanal bağımsız
            t = self.temporal(cal).unsqueeze(1).expand(bsz, ch, length, -1).reshape(bsz * ch, length, -1)
            h = self.value(z) + self.position + t
            for block in self.blocks:
                h = block(h)
            h = self.norm(h)
            out = self.head(h).reshape(bsz, ch, -1).permute(0, 2, 1)   # [B, H, C]
            if ch_idx is None:
                tpos = self.target_idx
            else:
                tpos = int((ch_idx == self.target_idx).nonzero()[0, 0])
            ht = h.reshape(bsz, ch, length, -1)[:, tpos]                # kapanış kanalı [B, L, d]
            return self.revin.denorm(out, mean, std, ch_idx), self.return_head(ht), self.direction_head(ht)

    return FactorizedTransformer


def build_model(n_channels: int, config: dict, target_idx: int = 0):
    return _build_model_classes()(n_channels, target_idx=target_idx, **config)


def close_returns(raw_close: np.ndarray, starts, horizon: int) -> np.ndarray:
    """Pencere başına kapanışın h = 1..H günlük getirisi (%): son girdi gününün
    (t-1) kapanışına göre. [N, H]."""
    starts = np.asarray(starts)
    base = raw_close[starts - 1][:, None]
    fut = np.stack([raw_close[starts + h] for h in range(horizon)], axis=1)
    return (fut / base - 1) * 100


def _batches(data, cal, starts, lookback, horizon, batch_size, shuffle, rng):
    import torch

    order = np.array(starts)
    if shuffle:
        order = rng.permutation(order)
    for i in range(0, len(order), batch_size):
        ts = order[i:i + batch_size]
        x = np.stack([data[t - lookback:t] for t in ts])
        y = np.stack([data[t:t + horizon] for t in ts])
        c = np.stack([cal[t - lookback:t] for t in ts])
        yield torch.from_numpy(x), torch.from_numpy(c), torch.from_numpy(y), ts


def _mse(pred, y):
    import torch

    return torch.mean((pred - y) ** 2)


# ------------------------------------------------------------------------------
# Eğitim ve değerlendirme
# ------------------------------------------------------------------------------

def train(train_df: pd.DataFrame, config: dict | None = None, progress=None) -> dict:
    """Modeli eğitir ve test döneminde değerlendirir. Döner: sonuç sözlüğü
    (config, prep raporu, metrikler, kayıp geçmişi, test tahminleri, sonraki
    günlerin tahmini) ve 'model' (torch modülü).

    Kayıp = w_channels · MSE(kanalların gelecek değerleri) + w_return · MSE(kapanışın
    h günlük getirisi, standartlaştırılmış) + w_direction · BCE(yükseldi mi)."""
    import torch

    cfg = effective_config(config)
    if cfg["lookback"] % cfg["block_size"]:
        raise ValueError(f"Pencere ({cfg['lookback']}), blok boyunun ({cfg['block_size']}) katı olmalı")
    step = progress or (lambda *a, **k: None)
    torch.set_num_threads(max(1, os.cpu_count() or 1))
    torch.manual_seed(cfg["seed"])
    rng = np.random.default_rng(cfg["seed"])

    prep = prepare_data(train_df, cfg["train_ratio"], cfg["iqr_k"])
    data, cal = prep["values"], prep["calendar"]
    n, n_ch = data.shape
    L, H = cfg["lookback"], cfg["horizon"]
    target_i = prep["channels"].index(TARGET)
    raw_close = prep["raw"][:, target_i]
    k = min(int(cfg["channels_per_batch"] or n_ch), n_ch)
    others = np.array([i for i in range(n_ch) if i != target_i])
    win = make_windows(n, prep["n_train"], L, H, cfg["val_ratio"])
    if len(win["train"]) < 20 or not win["test"]:
        raise ValueError(f"Yetersiz veri: {n} gün, pencere {L} + ufuk {H} ile {len(win['train'])} eğitim, "
                         f"{len(win['test'])} test penceresi")

    # Getiri hedefinin ölçeği: eğitim pencerelerinden, ufuk başına.
    train_ret = close_returns(raw_close, win["train"], H)
    ret_mean, ret_std = train_ret.mean(axis=0), train_ret.std(axis=0)
    ret_std = np.where(ret_std > 1e-9, ret_std, 1.0)
    prep["ret_mean"], prep["ret_std"] = ret_mean, ret_std
    rm, rs = torch.tensor(ret_mean, dtype=torch.float32), torch.tensor(ret_std, dtype=torch.float32)

    model = build_model(n_ch, cfg, target_i)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg["lr"], weight_decay=cfg["weight_decay"])
    bce = torch.nn.BCEWithLogitsLoss()
    history, best, best_state, bad = [], float("inf"), None, 0

    def losses(pred, ret, logit, y, ts, ch=None):
        r = torch.tensor(close_returns(raw_close, ts, H), dtype=torch.float32)
        l_ch = _mse(pred, y)
        l_ret = _mse(ret, (r - rm) / rs)
        l_dir = bce(logit, (r > 0).float())
        total = cfg["w_channels"] * l_ch + cfg["w_return"] * l_ret + cfg["w_direction"] * l_dir
        return total, (l_ch.item(), l_ret.item(), l_dir.item())

    def evaluate(starts):
        model.eval()
        total, parts, count = 0.0, np.zeros(3), 0
        with torch.no_grad():
            for x, c, y, ts in _batches(data, cal, starts, L, H, cfg["eval_batch_size"], False, rng):
                loss, p = losses(*model(x, c), y, ts)
                total += loss.item() * len(x)
                parts += np.array(p) * len(x)
                count += len(x)
        return total / max(count, 1), parts / max(count, 1)

    for epoch in range(1, cfg["epochs"] + 1):
        model.train()
        total, count = 0.0, 0
        for x, c, y, ts in _batches(data, cal, win["train"], L, H, cfg["batch_size"], True, rng):
            # Kanal bağımsız: adım başına kanalların rastgele bir alt kümesi (ağırlıklar ortak).
            ch = np.sort(np.append(rng.choice(others, k - 1, replace=False), target_i)) if k < n_ch else None
            if ch is not None:
                ch = torch.from_numpy(ch)
                x, y = x[..., ch], y[..., ch]
            loss, _ = losses(*model(x, c, ch), y, ts)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            total += loss.item() * len(x)
            count += len(x)
        train_loss = total / count
        val_loss, val_parts = evaluate(win["val"]) if win["val"] else (train_loss, np.full(3, np.nan))
        history.append({"epoch": epoch, "train": train_loss, "val": val_loss,
                        "val_channels": float(val_parts[0]), "val_return": float(val_parts[1]),
                        "val_direction": float(val_parts[2])})
        step(epoch, cfg["epochs"], train_loss, val_loss)
        if val_loss < best - 1e-6:
            best, bad = val_loss, 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= cfg["patience"]:
                break
    if best_state is not None:
        model.load_state_dict(best_state)

    result = evaluate_test(model, prep, win["test"], cfg)
    result.update({
        "config": cfg,
        "prep": prep["report"],
        "channels": prep["channels"],
        "history": history,
        "best_epoch": int(min(history, key=lambda h: h["val"])["epoch"]),
        "windows": {k: len(v) for k, v in win.items()},
        "forecast": forecast_next(model, prep, cfg),
        "params": int(sum(p.numel() for p in model.parameters())),
        "scale": {"mean": prep["mean"].tolist(), "std": prep["std"].tolist(),
                  "ret_mean": ret_mean.tolist(), "ret_std": ret_std.tolist()},
    })
    result["model"] = model
    return result


CONFIDENT_PROB = 0.6   # "emin olunan" tahmin: yükseliş olasılığı >= 0,6 ya da <= 0,4


def evaluate_test(model, prep: dict, test_starts: list, cfg: dict) -> dict:
    """Test dönemi metrikleri: tüm kanallarda standartlaştırılmış MSE (model ve
    naive) ve kapanış için ufuk bazında: getiri başlığından fiyat MAE / MAPE,
    yön başlığından isabet, "her zaman yükseliş" karşılaştırması ve yalnızca emin
    olunan tahminlerdeki isabet."""
    import torch

    data, cal, dates = prep["values"], prep["calendar"], prep["dates"]
    L, H = cfg["lookback"], cfg["horizon"]
    ti = prep["channels"].index(TARGET)
    raw_close = prep["raw"][:, ti]
    rets, probs, mses, naive_mses, starts = [], [], [], [], []
    model.eval()
    with torch.no_grad():
        for x, c, y, ts in _batches(data, cal, test_starts, L, H, cfg["eval_batch_size"], False, np.random.default_rng(0)):
            pred, ret, logit = model(x, c)
            naive = x[:, -1:, :].expand_as(y)
            mses.append((pred - y).pow(2).mean(dim=(1, 2)).numpy())
            naive_mses.append((naive - y).pow(2).mean(dim=(1, 2)).numpy())
            rets.append(ret.numpy())
            probs.append(torch.sigmoid(logit).numpy())
            starts += list(ts)
    starts = np.array(starts)
    pred_ret = np.concatenate(rets).astype(np.float64) * prep["ret_std"] + prep["ret_mean"]   # %
    up_prob = np.concatenate(probs).astype(np.float64)
    actual_ret = close_returns(raw_close, starts, H)
    last = raw_close[starts - 1]
    actual = last[:, None] * (1 + actual_ret / 100)
    pred = last[:, None] * (1 + pred_ret / 100)
    per_h = []
    for h in range(H):
        err = np.abs(pred[:, h] - actual[:, h])
        naive_err = np.abs(last - actual[:, h])
        up = actual_ret[:, h] > 0
        hit = (up_prob[:, h] > 0.5) == up
        sure = (up_prob[:, h] >= CONFIDENT_PROB) | (up_prob[:, h] <= 1 - CONFIDENT_PROB)
        per_h.append({
            "horizon": h + 1,
            "mae": float(err.mean()), "naive_mae": float(naive_err.mean()),
            "mape": float((err / np.abs(actual[:, h])).mean() * 100),
            "naive_mape": float((naive_err / np.abs(actual[:, h])).mean() * 100),
            "direction_acc": float(hit.mean() * 100),
            "return_sign_acc": float(((pred_ret[:, h] > 0) == up).mean() * 100),
            "up_baseline": float(up.mean() * 100),          # "her zaman yükseliş" isabeti
            "confident_acc": float(hit[sure].mean() * 100) if sure.any() else None,
            "confident_share": float(sure.mean() * 100),
        })
    test_pred = pd.DataFrame({
        "date": dates[starts].strftime("%Y-%m-%d"),
        "actual": actual[:, 0], "pred_h1": pred[:, 0], "up_prob_h1": up_prob[:, 0],
        f"actual_h{H}": actual[:, -1], f"pred_h{H}": pred[:, -1],
        "date_h_last": dates[starts + H - 1].strftime("%Y-%m-%d"),
    })
    return {
        "metrics": {
            "test_windows": int(len(starts)),
            "norm_mse": float(np.concatenate(mses).mean()),
            "naive_norm_mse": float(np.concatenate(naive_mses).mean()),
            "close": per_h,
        },
        "test_predictions": test_pred.to_dict("list"),
    }


def forecast_next(model, prep: dict, cfg: dict) -> dict:
    """Son `lookback` günle sonraki `horizon` işlem günü için kapanış tahmini
    (getiri başlığından) ve yükseliş olasılığı."""
    import torch

    L, H = cfg["lookback"], cfg["horizon"]
    data, cal = prep["values"], prep["calendar"]
    ti = prep["channels"].index(TARGET)
    model.eval()
    with torch.no_grad():
        _, ret, logit = model(torch.from_numpy(data[-L:][None]), torch.from_numpy(cal[-L:][None]))
    ret = ret[0].numpy().astype(np.float64) * prep["ret_std"] + prep["ret_mean"]
    last_close = float(prep["raw"][-1, ti])
    last_date = prep["dates"][-1]
    days = pd.bdate_range(last_date + pd.Timedelta(days=1), periods=H)
    return {"last_date": last_date.strftime("%Y-%m-%d"), "last_close": last_close,
            "dates": [d.strftime("%Y-%m-%d") for d in days],
            "return_pct": [float(r) for r in ret],
            "close": [float(last_close * (1 + r / 100)) for r in ret],
            "up_prob": [float(p) for p in torch.sigmoid(logit[0]).numpy()]}


# ------------------------------------------------------------------------------
# Kayıt
# ------------------------------------------------------------------------------

def _connect():
    path = storage.db_path()
    if path not in _initialized_paths:
        with storage.connection() as conn:
            conn.executescript(_SCHEMA)
        _initialized_paths.add(path)
    return storage.connection()


def save_run(ticker: str, result: dict) -> int:
    import torch

    buf = io.BytesIO()
    torch.save(result["model"].state_dict(), buf)
    payload = {k: v for k, v in result.items() if k not in ("model", "config", "prep", "metrics")}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _connect() as conn, storage.write_transaction(conn):
        cur = conn.execute(
            f"INSERT INTO {MODEL_TABLE} (ticker, created_at, config, prep, metrics, result, weights) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ticker, now, json.dumps(result["config"]), json.dumps(result["prep"], ensure_ascii=False),
             json.dumps(result["metrics"]), json.dumps(payload, ensure_ascii=False, default=str), buf.getvalue()))
        return int(cur.lastrowid)


def list_runs(ticker: str) -> list:
    with _connect() as conn:
        rows = conn.execute(f"SELECT id, created_at, config, prep, metrics, result FROM {MODEL_TABLE} "
                            "WHERE ticker = ? ORDER BY id DESC", (ticker,)).fetchall()
    return [{"id": i, "created_at": c, "config": json.loads(cfg), "prep": json.loads(p),
             "metrics": json.loads(m), **json.loads(r)} for i, c, cfg, p, m, r in rows]


def load_model(run_id: int):
    """Kayıtlı çalıştırmanın modelini (eval modunda) ve kanal listesini döner."""
    import torch

    with _connect() as conn:
        row = conn.execute(f"SELECT config, result, weights FROM {MODEL_TABLE} WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(f"model {run_id} yok")
    cfg, res = json.loads(row[0]), json.loads(row[1])
    model = build_model(len(res["channels"]), cfg, res["channels"].index(TARGET))
    model.load_state_dict(torch.load(io.BytesIO(row[2]), weights_only=True))
    model.eval()
    return model, res["channels"], cfg


def delete_run(run_id: int) -> None:
    with _connect() as conn, storage.write_transaction(conn):
        conn.execute(f"DELETE FROM {MODEL_TABLE} WHERE id = ?", (run_id,))


# ------------------------------------------------------------------------------
# Komut satırı
# ------------------------------------------------------------------------------

def main(argv=None):
    import argparse
    import sys

    import ai_dataset

    parser = argparse.ArgumentParser(description="Factorized Self-Attention modelini kayıtlı veri setiyle eğitir")
    sub = parser.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train", help="eğit, test et ve kaydet")
    t.add_argument("--ticker", required=True)
    for key in ("lookback", "horizon", "epochs"):
        t.add_argument(f"--{key}", type=int, default=DEFAULTS[key])
    t.add_argument("--iqr-k", type=float, default=DEFAULTS["iqr_k"])
    t.add_argument("--batch-size", type=int, help="adım başına pencere (bellek yetmezse küçültün)")
    t.add_argument("--channels-per-batch", type=int, help="adım başına kanal (bellek yetmezse küçültün)")
    s = sub.add_parser("status", help="kayıtlı modeller")
    s.add_argument("--ticker", required=True)
    args = parser.parse_args(argv)
    ticker = ai_dataset.normalize_ticker(args.ticker)

    if args.cmd == "train":
        if not torch_available():
            print(INSTALL_HINT, file=sys.stderr)
            return 2
        df = ai_dataset.load_dataset(ticker)
        if df.empty:
            print(f"{ticker} için kayıtlı veri seti yok", file=sys.stderr)
            return 1
        cfg = {"lookback": args.lookback, "horizon": args.horizon, "epochs": args.epochs, "iqr_k": args.iqr_k}
        if args.batch_size:
            cfg["batch_size"] = args.batch_size
        if args.channels_per_batch:
            cfg["channels_per_batch"] = args.channels_per_batch
        eff = effective_config(cfg)
        ai_dataset.log(f"{ticker}: eğitim başlıyor - {len(df)} gün, adım {eff['batch_size']} pencere × "
                       f"{eff['channels_per_batch']} kanal" + (" (düşük bellek profili)" if eff["low_memory"] else ""))
        res = train(ai_dataset.training_frame(df), cfg,
                    progress=lambda e, n, tr, va: ai_dataset.log(f"epoch {e}/{n} eğitim {tr:.4f} doğrulama {va:.4f}"))
        run_id = save_run(ticker, res)
        m = res["metrics"]
        ai_dataset.log(f"{ticker}: model #{run_id} kaydedildi - normalize MSE {m['norm_mse']:.3f} "
                       f"(naive {m['naive_norm_mse']:.3f}), kapanış MAE 1. gün {m['close'][0]['mae']:.2f} "
                       f"(naive {m['close'][0]['naive_mae']:.2f})")
        return 0
    for r in list_runs(ticker):
        m = r["metrics"]
        print(f"#{r['id']} {r['created_at']} pencere {r['config']['lookback']} ufuk {r['config']['horizon']} "
              f"MSE {m['norm_mse']:.3f} / naive {m['naive_norm_mse']:.3f}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
