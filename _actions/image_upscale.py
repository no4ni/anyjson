"""Апскейл изображений через ScaleSmooth.exe (C#/WinForms)."""
import subprocess
import time
from pathlib import Path

from _actions import register

_SCALESMOOTH_EXE = Path(__file__).resolve().parent.parent / "side" / "external" / "scalesmooth" / "ScaleSmooth.exe"
_SHARPENED_BILINEAR_EXE = Path(__file__).resolve().parent.parent / "side" / "external" / "sharpened_bilinear" / "sharpened_bilinear.exe"

_METHOD_MAP = {
    "smooth": "ScaleSmoothGray",
    "smoothcolor": "ScaleSmoothColor",
    "furry": "ScaleFurryGray",
    "furrycolor": "ScaleFurryColor",
    "rough": "ScaleRoughGray",
    "roughcolor": "ScaleRoughColor",
    "bold": "BoldScaleGray",
    "boldcolor": "BoldScaleColor",
    "contrastbold": "ContrastBoldScaleGray",
    "contrastboldcolor": "ContrastBoldScaleColor",
    "separate": "ScaleSeparateGray",
    "separatecolor": "ScaleSeparateColor",
    "ba": "ScaleBilinearApproximationGrayAuto",
    "bacolor": "ScaleBilinearApproximationColorAuto",
    "bacontrast": "ScaleBAContrastGrayAuto",
    "bacontrastcolor": "ScaleBAContrastColorAuto",
    "ba255": "Scale255BAGrayAuto",
    "ba255color": "Scale255BAColorAuto",
    "bathin255": "ScaleThin255BAGrayAuto",
    "bathin255color": "ScaleThin255BAColorAuto",
    "basmoothcontrast": "ScaleBASmoothContrastGrayAuto",
    "basmoothcontrastcolor": "ScaleBASmoothContrastColorAuto",
    "baextremum": "ScaleBAExtremumGrayAuto",
    "baextremumcolor": "ScaleBAExtremumColorAuto",
    "baderivative": "ScaleDerivativeBAGrayAuto",
    "baderivativecolor": "ScaleDerivativeBAColorAuto",
    "bamonochrome": "ScaleBAMonochromeGrayAuto",
    "bamonochrome2": "ScaleBAMonochrome2GrayAuto",
    "bamonochromecolor": "ScaleBAMonochromeColorAuto",
    "bamonochrome2color": "ScaleBAMonochrome2ColorAuto",
}


_PIL_METHODS = {"bilinear", "bicubic", "lanczos"}
_SHARPENED_METHODS = {"sharpened_bilinear", "sharpened"}
_FORGE_PREFIX = "forge:"


_BA_METHODS = {
    "ba", "bacolor", "bacontrast", "bacontrastcolor", "ba255", "ba255color",
    "bathin255", "bathin255color", "basmoothcontrast", "basmoothcontrastcolor",
    "baextremum", "baextremumcolor", "baderivative", "baderivativecolor",
    "bamonochrome", "bamonochrome2", "bamonochromecolor", "bamonochrome2color",
}


_AUTO_BA_NODES = [
    (2500, 100),
    (10000, 48),
    (40000, 21),
    (250000, 6),
    (480000, 3),
    (1000000, 1),
    (1200000, 0),
]


def _auto_accuracy_ba(w, h):
    """Максимальная точность BA, укладывающаяся в ~30 сек.

    Кусочно-линейная интерполяция по log10(px) между узлами эмпирики
    (i3-10100F + 1050 Ti, Auto -> CPU-fallback):
      2.5k px -> acc 100 (0.5с)    40k px  -> acc 21  (27с)
      10k px  -> acc 48  (17.4с)   250k px -> acc 6   (25.6с)
      480k px -> acc 3   (15.9с)   1M px   -> acc 1   (19.3с)
      >1.2M px -> acc 0
    """
    px = max(1, w * h)
    if px <= _AUTO_BA_NODES[0][0]:
        return _AUTO_BA_NODES[0][1]
    if px >= _AUTO_BA_NODES[-1][0]:
        return _AUTO_BA_NODES[-1][1]
    import math
    _lp = math.log10(px)
    for _i in range(len(_AUTO_BA_NODES) - 1):
        _n0, _a0 = _AUTO_BA_NODES[_i]
        _n1, _a1 = _AUTO_BA_NODES[_i + 1]
        if _n0 <= px <= _n1:
            _t = ((_lp - math.log10(_n0))
                  / (math.log10(_n1) - math.log10(_n0)))
            return int(round(_a0 + (_a1 - _a0) * _t))
    return 0


_SCALE_BETA = 0.724


def _auto_accuracy_ba_scaled(w, h, scale):
    """Точность BA с поправкой на масштаб.

    Базовая таблица _AUTO_BA_NODES снята на ×2. При scale > 2 время
    растёт как (scale/2)^0.724 (замер: ba 512² acc=6 ×2→25.2с, ×8→68.7с),
    поэтому точность делим на тот же коэффициент с округлением вверх
    (ceil — чтобы недобор бюджета был минимальным). Для scale ≤ 2 — как есть.

    Проверено на сыром шуме (отличие <5%), контент-независим.
    """
    import math
    acc = _auto_accuracy_ba(w, h)
    try:
        s = int(scale)
    except (TypeError, ValueError):
        return acc
    if s <= 2:
        return acc
    factor = (s / 2.0) ** _SCALE_BETA
    return max(0, min(100, int(math.ceil(acc / factor))))


_AUTO_SMOOTH_NODES = [
    (1000000, 100),
    (4200000, 100),
    (16800000, 90),
    (67000000, 15),
    (268000000, 3),
    (1072000000, 1),
    (4295000000, 0),
    (10000000000, 0),
]


def _auto_accuracy_smooth(w, h, scale):
    """Максимальная точность smooth, укладывающаяся в ~30 сек.

    Время smooth ~ линейно по px_out = w*h*scale^2, зависимость от
    масштаба при равном px_out слабая (x8/512² 32.5с vs x2/1024² 10.2с
    при px_out=1M/4.2M — x8 даже чуть быстрее). Замеры (CPU i3-10100F,
    сырой шум без блюра):
        1M px_out (x2/512², x8/128²)  -> 2.8с / 2.3с acc=100
        4.2M px_out (x2/1024², x8/256²) -> 10.2с / 8.3с acc=100
        16.8M px_out (x2/2048², x8/512²) -> >40с / 32.5с acc=100
        67M px_out (x8/1024²) -> 24.7с acc=12
        268M px_out (x2/4096², x8/1024²) -> >40с даже при acc=0
    """
    import math
    try:
        s = int(scale)
    except (TypeError, ValueError):
        s = 2
    if s < 2:
        s = 2
    px_in = max(1, w * h)
    eff = px_in * (s * s)
    if eff <= _AUTO_SMOOTH_NODES[0][0]:
        return _AUTO_SMOOTH_NODES[0][1]
    if eff >= _AUTO_SMOOTH_NODES[-1][0]:
        return _AUTO_SMOOTH_NODES[-1][1]
    _lp = math.log10(eff)
    for _i in range(len(_AUTO_SMOOTH_NODES) - 1):
        _n0, _a0 = _AUTO_SMOOTH_NODES[_i]
        _n1, _a1 = _AUTO_SMOOTH_NODES[_i + 1]
        if _n0 <= eff <= _n1:
            _t = ((_lp - math.log10(_n0))
                  / (math.log10(_n1) - math.log10(_n0)))
            return int(round(_a0 + (_a1 - _a0) * _t))
    return 0


_SC_NODES = [
    (1700000, 100, 100),
    (2360000, 60, 50),
    (3500000, 45, 35),
    (4200000, 40, 15),
    (4500000, 35, 10),
]


def _auto_accuracy_smoothcolor(w, h, scale):
    """Максимальная точность smoothcolor, укладывающаяся в ~30 сек.

    Время smoothcolor ~ линейно по px_out = w*h*scale^2, но фикс. стоимость
    зависит и от px_in (большой вход = подготовка, не снижается точностью).
    Замеры (цветной шум, i3-10100F):
        1.64M px_out (x2/640², вход 410k) -> 27.9с acc=100
        2.36M (x2/768², вход 589k) -> 39.7с acc=100; 20.3с acc=50
        2.36M (x3/512², вход 262k) -> 43.1с acc=100; 15.3с acc=0
        4.19M (x4/512², вход 262k) -> 76.6с acc=100; 20.9с acc=0
        4.2M (x8/256², вход 65k)  -> ~67с acc=100; 19.3с acc=25
    Порог бюджета — между 1.7M и 2.5M, за 4.5M — отказ (даже acc=0
    не влезает на среднем входе).
    """
    import math
    try:
        s = int(scale)
    except (TypeError, ValueError):
        s = 2
    if s < 2:
        s = 2
    px_in = max(1, w * h)
    px_out = px_in * s * s
    small_in = px_in <= 100000
    if px_out <= _SC_NODES[0][0]:
        return _SC_NODES[0][1] if small_in else _SC_NODES[0][2]
    if px_out >= _SC_NODES[-1][0]:
        return _SC_NODES[-1][1] if small_in else _SC_NODES[-1][2]
    _lp = math.log10(px_out)
    for _i in range(len(_SC_NODES) - 1):
        _n0, _a0s, _a0b = _SC_NODES[_i]
        _n1, _a1s, _a1b = _SC_NODES[_i + 1]
        if _n0 <= px_out <= _n1:
            _t = ((_lp - math.log10(_n0))
                  / (math.log10(_n1) - math.log10(_n0)))
            _a0 = _a0s if small_in else _a0b
            _a1 = _a1s if small_in else _a1b
            return int(round(_a0 + (_a1 - _a0) * _t))
    return 0


_FURRY_EXP = 2.8
_FURRY_NODES = [
    (30000, 100),
    (150000, 50),
    (500000, 6),
]
_FURRY_ZERO_MAX = 13000000


def _auto_accuracy_furry(w, h, scale):
    """Максимальная точность furry, укладывающаяся в ~30 сек.

    furry — очень тяжёлый, время растёт как px^1.475 (при acc>0) и сильно
    зависит от масштаба (γ_scale ≈ 2.8). Работает по эффективной площади
        eff = px_in * scale^2.8
    На малых eff (≤500k) α≈0.96 (как у smooth). При 500k<eff≤5.6M —
    acc=1 (замер: 29–30с). Выше 5.6M — только acc=0 (быстрый режим).
    Замеры (серый шум, i3-10100F):
        x2/64²  (4k,   eff=28k)  → acc=100, 4.6с
        x2/128² (16k,  eff=111k) → acc=50, 18.5с; acc=100, 36.0с
        x2/256² (65k,  eff=453k) → acc=6, 18.6с;  acc=12, 36.1с
        x2/512² (262k, eff=1.8M) → acc=0, 5.7с;   acc=3 timeout >40с
        x2/1024²(1M,   eff=7.0M) → acc=0, 22.0с
        x8/128² (16k,  eff=4.4M) → acc=0, 16.4с
        x11/100²(10k,  eff=8.9M) → acc=0, 28.0с
        x2/2048²(4.2M, eff=29M)  → timeout
    Возвращает int 0..100 или None (= не влезает даже при acc=0).
    """
    import math
    try:
        s = max(2, int(scale))
    except (TypeError, ValueError):
        s = 2
    px_in = max(1, w * h)
    eff = px_in * (s ** _FURRY_EXP)
    if eff > _FURRY_ZERO_MAX:
        return None
    if eff > 5600000:
        return 0
    if eff > _FURRY_NODES[-1][0]:
        return 1
    if eff <= _FURRY_NODES[0][0]:
        return _FURRY_NODES[0][1]
    _lp = math.log10(eff)
    for _i in range(len(_FURRY_NODES) - 1):
        _n0, _a0 = _FURRY_NODES[_i]
        _n1, _a1 = _FURRY_NODES[_i + 1]
        if _n0 <= eff <= _n1:
            _t = ((_lp - math.log10(_n0))
                  / (math.log10(_n1) - math.log10(_n0)))
            return int(round(_a0 + (_a1 - _a0) * _t))
    return 0


_FURRYCOLOR_EXP = 2.8
_FURRYCOLOR_100_MAX = 50000
_FURRYCOLOR_12_MAX = 200000
_FURRYCOLOR_ZERO_MAX = 2200000


def _auto_accuracy_furrycolor(w, h, scale):
    """Максимальная точность furrycolor, укладывающаяся в ~30 сек.

    furrycolor — color-версия furry, в ~3–4 раза медленнее. Модель eff та же
    (γ_scale=2.8):
        eff = px_in * scale^2.8
    Но α гораздо выше: на eff>50k acc=100 недоступен (>40с), на eff>200k
    недоступен и acc=12 — спасает только acc=0.
    Замеры (цветной шум, i3-10100F):
        x2/64²  (4k,   eff=28k)  → acc=100, 20.5с
        x2/128² (16k,  eff=111k) → acc=12, 20.8с; 25/50/100 timeout
        x2/256² (65k,  eff=453k) → acc=0, 6.2с;   3/6/12 timeout
        x2/512² (262k, eff=1.8M) → acc=0, 24.1с
        x8/64²  (4k,   eff=1.36M) → acc=0, 17.4с; acc=3 32.7с
        x8/128² (16k,  eff=5.4M) → timeout даже при acc=0
    Возвращает int 0..100 или None (= не влезает даже при acc=0).
    """
    try:
        s = max(2, int(scale))
    except (TypeError, ValueError):
        s = 2
    px_in = max(1, w * h)
    eff = px_in * (s ** _FURRYCOLOR_EXP)
    if eff > _FURRYCOLOR_ZERO_MAX:
        return None
    if eff > _FURRYCOLOR_12_MAX:
        return 0
    if eff > _FURRYCOLOR_100_MAX:
        return 12
    return 100


_ROUGH_EXP = 2.65
_ROUGH_NODES = [
    (400000, 100),
    (800000, 80),
    (1600000, 40),
    (3800000, 12),
]
_ROUGH_ZERO_MAX = 5000000


def _auto_accuracy_rough(w, h, scale):
    """Максимальная точность rough, укладывающаяся в ~30 сек.

    rough тяжелее smooth, γ_scale=2.65 (у smooth ~0, у furry 2.8). Модель:
        eff = px_in * scale^2.65
    acc=100 доступен до eff≈400k; дальше ступени вниз; 3.8M<eff≤5M —
    acc=1 (12с); выше 5M — отказ (даже acc=0 на 15M не влезает).
    Замеры (серый шум, i3-10100F):
        x2/64²  (4k,  eff=25k)   → acc=100, 1.15с
        x2/128² (16k, eff=100k)  → acc=100, 3.90с
        x2/256² (65k, eff=405k)  → acc=100, 14.90с
        x2/512² (262k,eff=1.63M) → acc=50, 31.6с; acc=25, 18.9с
        x8/64²  (4k,  eff=956k)  → acc=100, 36.1с; acc=50, 19.5с
        x8/128² (16k, eff=4.04M) → acc=0, 12.5с (eff за последним узлом)
        x8/256² (65k, eff=15.5M) → timeout даже при acc=0
    Возвращает int 0..100 или None (= не влезает даже при acc=0).
    """
    import math
    try:
        s = max(2, int(scale))
    except (TypeError, ValueError):
        s = 2
    px_in = max(1, w * h)
    eff = px_in * (s ** _ROUGH_EXP)
    if eff > _ROUGH_ZERO_MAX:
        return None
    if eff <= _ROUGH_NODES[0][0]:
        return _ROUGH_NODES[0][1]
    if eff > _ROUGH_NODES[-1][0]:
        return 1
    _lp = math.log10(eff)
    for _i in range(len(_ROUGH_NODES) - 1):
        _n0, _a0 = _ROUGH_NODES[_i]
        _n1, _a1 = _ROUGH_NODES[_i + 1]
        if _n0 <= eff <= _n1:
            _t = ((_lp - math.log10(_n0))
                  / (math.log10(_n1) - math.log10(_n0)))
            return int(round(_a0 + (_a1 - _a0) * _t))
    return 0


_ROUGHCOLOR_EXP = 2.52
_ROUGHCOLOR_NODES = [
    (100000, 100),
    (400000, 45),
    (800000, 12),
    (1500000, 6),
]
_ROUGHCOLOR_ZERO_MAX = 2000000


def _auto_accuracy_roughcolor(w, h, scale):
    """Максимальная точность roughcolor, укладывающаяся в ~30 сек.

    roughcolor — color-версия rough, в ~3-4 раза медленнее. Модель:
        eff = px_in * scale^2.52
    acc=100 только до eff≈100k; после 400k acc<45; после 800k acc<12;
    после 1.5M — только acc=6; >2M — отказ (даже acc=0 timeout).
    Замеры (цветной шум, i3-10100F):
        x2/64²  (4k,  eff=23k)  → acc=100, 4.06с
        x2/128² (16k, eff=92k)  → acc=100, 15.69с
        x2/256² (65k, eff=375k) → acc=50, 35.13с; acc=25, 19.82с
        x2/512² (262k,eff=1.5M) → acc=6, 19.53с;  acc=12, 39.88с
        x8/64²  (4k,  eff=756k) → acc=12, 21.13с; acc=25, 39.49с
        x8/128² (16k, eff=3.02M)→ timeout даже при acc=0
    Возвращает int 0..100 или None (= не влезает даже при acc=0).
    """
    import math
    try:
        s = max(2, int(scale))
    except (TypeError, ValueError):
        s = 2
    px_in = max(1, w * h)
    eff = px_in * (s ** _ROUGHCOLOR_EXP)
    if eff > _ROUGHCOLOR_ZERO_MAX:
        return None
    if eff > _ROUGHCOLOR_NODES[-1][0]:
        return 0
    if eff <= _ROUGHCOLOR_NODES[0][0]:
        return _ROUGHCOLOR_NODES[0][1]
    _lp = math.log10(eff)
    for _i in range(len(_ROUGHCOLOR_NODES) - 1):
        _n0, _a0 = _ROUGHCOLOR_NODES[_i]
        _n1, _a1 = _ROUGHCOLOR_NODES[_i + 1]
        if _n0 <= eff <= _n1:
            _t = ((_lp - math.log10(_n0))
                  / (math.log10(_n1) - math.log10(_n0)))
            return int(round(_a0 + (_a1 - _a0) * _t))
    return 0


def _auto_accuracy_bold(w, h, scale, mid_acc=1):
    """Максимальная точность bold, укладывающаяся в ~30 сек.

    bold — сложный: acc>0 суперлинеен по px_in (×4 → ×7.8с), на больших
    px_in катастрофичен. При acc=0 есть быстрый режим, почти линейный
    по px_out (~0.4M px_out/с). Модель двухосевая:
      - px_out ≤ 66k        → acc=100 (работает на малых входах)
      - px_in ≤ 65k, px_out ≤ 400k → acc=12 (средний вход + умеренный выход)
      - px_out ≤ 7M         → acc=0 (быстрый режим)
      - иначе — отказ
    Замеры (серый шум, i3-10100F):
        x2/64²  (4k,  px_out=16k)  → acc=100, 3.02с
        x2/128² (16k, px_out=64k)  → acc=100, 23.57с
        x2/256² (65k, px_out=260k) → acc=12, 22.33с;  25/50/100 timeout
        x2/512² (262k,px_out=1.05M)→ acc=0, 3.33с;   3/6/12 timeout
        x8/64²  (4k,  px_out=256k) → acc=12, 17.81с; 25, 36.89с
        x8/128² (16k, px_out=1.05M)→ acc=0, 2.95с;   3, 36.62с
        x8/256² (65k, px_out=4.2M) → acc=0, 10.10с;  3, timeout
    Возвращает int 0..100 или None (= не влезает даже при acc=0).
    """
    try:
        s = max(2, int(scale))
    except (TypeError, ValueError):
        s = 2
    px_in = max(1, w * h)
    px_out = px_in * s * s
    if px_out <= 66000:
        return 100
    if px_in <= 65536 and px_out <= 400000:
        return 12
    if mid_acc > 0 and px_out <= 2000000:
        return mid_acc
    if px_out <= 7000000:
        return 0
    return None


def _auto_accuracy_boldcolor(w, h, scale):
    """Максимальная точность boldcolor, укладывающаяся в ~30 сек.

    boldcolor — color-версия bold. Модель двухосевая (px_in и px_out):
      - px_out ≤ 20k              → acc=100 (только очень малые выходы)
      - px_in ≤ 65k, px_out ≤ 200k → acc=12
      - px_in ≤ 65k, px_out ≤ 400k → acc=3
      - scale=2, px_in ≤ 300k     → acc=0 (быстрый режим x2)
      - scale≥4, px_in ≤ 20k и px_out ≤ 1.2M → acc=0 (быстрый режим x4+)
      - иначе — отказ
    Замеры (цветной шум, i3-10100F):
        x2/64²  (4k,  px_out=16k)  → acc=100, 16.64с
        x2/128² (16k, px_out=65k)  → acc=12, 16.00с;  25, 32.60с
        x2/256² (65k, px_out=262k) → acc=3, 32.30с;   0, 4.90с
        x2/512² (262k,px_out=1.05M)→ acc=0, 18.38с;   3, timeout
        x8/64²  (4k,  px_out=262k) → acc=3, 22.55с;   0, 14.02с
        x8/128² (16k, px_out=1.05M)→ acc=0, 14.02с;   3, timeout
        x8/256² (65k, px_out=4.2M) → timeout даже при acc=0
    Возвращает int 0..100 или None (= не влезает даже при acc=0).
    """
    try:
        s = max(2, int(scale))
    except (TypeError, ValueError):
        s = 2
    px_in = max(1, w * h)
    px_out = px_in * s * s
    if px_out <= 20000:
        return 100
    if px_in <= 65536 and px_out <= 200000:
        return 12
    if px_in <= 65536 and px_out <= 400000:
        return 3
    if s == 2 and px_in <= 300000:
        return 0
    if s >= 4 and px_in <= 20000 and px_out <= 1200000:
        return 0
    return None


def _auto_accuracy_contrastboldcolor(w, h, scale):
    """Максимальная точность contrastboldcolor, укладывающаяся в ~30 сек.

    contrastboldcolor — color-версия contrastbold, ~×3 медленнее. Двухосевая:
      - px_out ≤ 20k                         → 100 (только иконки)
      - px_in ≤ 70k, px_out ≤ 400k           → 2 (быстрый режим для средних)
      - scale=2, px_in ≤ 300k                → 0 (color x2 быстрый режим)
      - scale≥4, px_in ≤ 20k, px_out ≤ 1.2M  → 0
      - иначе — отказ
    Замеры (цветной шум, i3-10100F):
        x2/64²  (4k,  px_out=16k)  → acc=100, 17.06с
        x2/128² (16k, px_out=65k)  → acc=2, 3.37с
        x2/256² (65k, px_out=262k) → acc=2, 24.79с
        x2/512² (262k,px_out=1.05M)→ acc=0, 18.77с; 1/2 timeout
        x8/64²  (4k,  px_out=262k) → acc=2, 17.82с
        x8/128² (16k, px_out=1.05M)→ acc=0, 14.37с; 1/2 timeout
        x8/256² (65k, px_out=4.2M) → timeout даже при acc=0
    Возвращает int 0..100 или None (= не влезает даже при acc=0).
    """
    try:
        s = max(2, int(scale))
    except (TypeError, ValueError):
        s = 2
    px_in = max(1, w * h)
    px_out = px_in * s * s
    if px_out <= 20000:
        return 100
    if px_in <= 70000 and px_out <= 400000:
        return 2
    if s == 2 and px_in <= 300000:
        return 0
    if s >= 4 and px_in <= 20000 and px_out <= 1200000:
        return 0
    return None


def _auto_accuracy_separate(w, h, scale):
    """Максимальная точность separate, укладывающаяся в ~30 сек.

    separate — лёгкий метод, время ~ линейно по px_out (при фиксированном
    масштабе). Но масштаб тоже важен: x8 вдвое медленнее x2 при равном
    px_in (при равном px_out — почти одинаково). Чище работает ось
        eff = px_in * scale^0.51
    Замеры (серый шум, i3-10100F):
        x2/512²   (262k, eff=262k)  → acc=100, 2.97с
        x8/512²   (262k, eff=532k)  → acc=100, 6.02с
        x2/1024²  (1M,   eff=1M)    → acc=100, 9.22с
        x8/1024²  (1M,   eff=2.03M) → acc=100, 22.09с
        x2/2048²  (4.2M, eff=4.2M)  → acc=25, 20.55с; acc=0, 7.57с
        x2/4096²  (16.8M,eff=16.8M) → acc=1, 27.62с;  acc=0, 27.90с
    Порог бюджета (30с) — eff ≈ 3.5M; дальше acc=25 до 7M,
    acc=1 до 17M, выше — отказ (даже acc=0 не влезает).
    Возвращает int 0..100 или None (= не влезает даже при acc=1).
    """
    try:
        s = max(2, int(scale))
    except (TypeError, ValueError):
        s = 2
    px_in = max(1, w * h)
    if s > 2:
        eff = px_in * (s ** 0.51)
    else:
        eff = px_in
    if eff <= 3500000:
        return 100
    if eff <= 7000000:
        return 25
    if eff <= 17000000:
        return 1
    return None


def _auto_accuracy_separatecolor(w, h, scale):
    """Максимальная точность separatecolor, укладывающаяся в ~30 сек.

    separatecolor — color-версия separate, но почти такая же лёгкая
    (×1.1–1.3, не ×3). Ось та же:
        eff = px_in * scale^0.51
    Пороги сдвинуты в ~1.25 раза относительно separate.
    Замеры (цветной шум, i3-10100F):
        x2/64²   (4k,   eff=4k)   → acc=100, 0.26с
        x2/128²  (16k,  eff=16k)  → acc=100, 0.46с
        x2/256²  (65k,  eff=65k)  → acc=100, 1.07с
        x2/512²  (262k, eff=262k) → acc=100, 3.20с
        x8/64²   (4k,   eff=11k)  → acc=100, 0.38с
        x8/128²  (16k,  eff=45k)  → acc=100, 0.96с
        x8/256²  (65k,  eff=184k) → acc=100, 2.82с
        x8/1024² (1M,   eff=2.97M)→ acc=100, 29.38с (на границе, влезает)
    Возвращает int 0..100 или None (= не влезает даже при acc=1).
    """
    try:
        s = max(2, int(scale))
    except (TypeError, ValueError):
        s = 2
    px_in = max(1, w * h)
    if s > 2:
        eff = px_in * (s ** 0.51)
    else:
        eff = px_in
    if eff <= 3100000:
        return 100
    if eff <= 5600000:
        return 25
    if eff <= 13600000:
        return 1
    return None


_BACOLOR_EXP = 0.5
_BACOLOR_NODES = [
    (4000, 100),
    (11000, 50),
    (16000, 25),
    (65000, 6),
    (262000, 1),
]


def _auto_accuracy_bacolor(w, h, scale):
    """Максимальная точность bacolor, укладывающаяся в ~30 сек.

    bacolor — цветная версия ba, но в 10–50 раз тяжелее на малых входах.
    Ось eff = px_in * scale^0.5 (β=0.5, проверено по x2/x8).
    Замеры (цветной шум, i3-10100F):
        x2/4k  (eff=4k)   → acc=100, 28.18с
        x2/16k (eff=16k)  → acc=25, 30.36с; acc=12, 8.01с
        x2/65k (eff=65k)  → acc=6, 33.06с; acc=3, 8.90с
        x2/262k(eff=262k)→ acc=1, 19.08с
        x8/4k  (eff=11k)  → acc=50, 24.88с (100 timeout)
        x8/16k (eff=45k)  → acc=12, 27.14с
        x8/65k (eff=184k) → acc=2, 18.77с; acc=3, 32.44с
        x8/262k(eff=740k)→ timeout даже при acc=0
    Возвращает int 0..100 или None (= не влезает).
    """
    import math
    try:
        s = max(2, int(scale))
    except (TypeError, ValueError):
        s = 2
    px_in = max(1, w * h)
    eff = px_in * (s ** _BACOLOR_EXP)
    if eff > _BACOLOR_NODES[-1][0]:
        return None
    if eff <= _BACOLOR_NODES[0][0]:
        return _BACOLOR_NODES[0][1]
    _lp = math.log10(eff)
    for _i in range(len(_BACOLOR_NODES) - 1):
        _n0, _a0 = _BACOLOR_NODES[_i]
        _n1, _a1 = _BACOLOR_NODES[_i + 1]
        if _n0 <= eff <= _n1:
            _t = ((_lp - math.log10(_n0))
                  / (math.log10(_n1) - math.log10(_n0)))
            return int(round(_a0 + (_a1 - _a0) * _t))
    return 0


_BACONTRAST_SMALL = [
    # scale ≤ 4 (x2, x3, x4) — px_in → acc
    (4096, 100),
    (16384, 35),
    (65536, 15),
    (262144, 4),
]
_BACONTRAST_BIG = [
    # scale ≥ 5 (x5..x8) — px_in → acc
    (4096, 50),
    (16384, 27),
    (65536, 8),
    (262144, 6),
]


def _auto_accuracy_bacontrast(w, h, scale):
    """Максимальная точность bacontrast, укладывающаяся в ~30 сек.

    bacontrast — двухрежимный: на x2 (scale≤4) при px_in>~100k acc 4-25
    валит GPU (illegal memory access), выживает только acc=3 (fast path).
    На x8 (scale≥5) алгоритм тесселирует вход, VRAM хватает, но acc выше
    6 не влезает по времени.
    Замеры (серый шум, i3-10100F + 1050 Ti):
        x2/64²   (4k,   acc=100) → 24.93с
        x2/128²  (16k,  acc=35)  → ~22с    (40=33.7с, 50 timeout)
        x2/256²  (65k,  acc=15)  → 28.36с  (25 timeout)
        x2/512²  (262k, acc=4)   → 2.32с   (5 crash GPU; 6,12 crash; 25+ timeout)
        x8/64²   (4k,   acc=50)  → 17.90с  (100 timeout)
        x8/128²  (16k,  acc=27)  → ~29с    (30=33.5с, 40 timeout)
        x8/256²  (65k,  acc=8)   → 12.79с  (12=33с, 25 timeout)
        x8/512²  (262k, acc=6)   → 23.15с  (12 timeout)
    Возвращает int 0..100 или None (= не влезает).
    """
    import math
    try:
        s = max(2, int(scale))
    except (TypeError, ValueError):
        s = 2
    px_in = max(1, w * h)
    nodes = _BACONTRAST_SMALL if s <= 4 else _BACONTRAST_BIG
    if px_in > nodes[-1][0]:
        return None
    if px_in <= nodes[0][0]:
        return nodes[0][1]
    _lp = math.log10(px_in)
    for _i in range(len(nodes) - 1):
        _n0, _a0 = nodes[_i]
        _n1, _a1 = nodes[_i + 1]
        if _n0 <= px_in <= _n1:
            _t = ((_lp - math.log10(_n0))
                  / (math.log10(_n1) - math.log10(_n0)))
            return int(round(_a0 + (_a1 - _a0) * _t))
    return nodes[-1][1]


_BACONTRASTCOLOR_SMALL = [
    # scale ≤ 4 — px_in → acc
    (4096, 100),
    (16384, 100),
    (65536, 38),
    (262144, 14),
]
_BACONTRASTCOLOR_BIG = [
    # scale ≥ 5 — px_in → acc
    (4096, 100),
    (16384, 100),
    (65536, 24),
]
_BACONTRASTCOLOR_BIG_MAX_PX = 131072  # >65k+запас = 1x1 bug


def _auto_accuracy_bacontrastcolor(w, h, scale):
    """Максимальная точность bacontrastcolor, укладывающаяся в ~30 сек.

    Двухрежимный, как bacontrast, но БЕЗ GPU-crash зоны — зато с багом exe:
    на scale≥5 при px_in ≥ ~130k (наблюдалось на 262k) ScaleBAContrastColorAuto
    возвращает Bitmap 1x1 при ЛЮБОМ acc (100/27/15/0). Это не timeout,
    а битый результат — метод сломан на этой комбинации.
    Замеры (цветной шум, i3-10100F):
        x2/4k   (4k,   acc=100) → 2.93с
        x2/16k  (16k,  acc=100) → 18.45с
        x2/65k  (65k,  acc=38)  → ~27с    (35=17.2, 40=36.0, 45=36.5)
        x2/262k (262k, acc=14)  → ~29с    (8=14.5, 12=24.0, 15=37.1)
        x8/4k   (4k,   acc=100) → 5.00с
        x8/16k  (16k,  acc=100) → 24.32с
        x8/65k  (65k,  acc=24)  → ~29с    (15=15.8, 22=25.8, 27=39.8)
        x8/262k (262k) → 1x1 BUG при любом acc
    Возвращает int 0..100 или None (не влезает / баг 1x1).
    """
    import math
    try:
        s = max(2, int(scale))
    except (TypeError, ValueError):
        s = 2
    px_in = max(1, w * h)
    nodes = (_BACONTRASTCOLOR_SMALL if s <= 4
             else _BACONTRASTCOLOR_BIG)
    if s >= 5 and px_in > _BACONTRASTCOLOR_BIG_MAX_PX:
        return None  # 1x1 bug
    if px_in > nodes[-1][0]:
        return None
    if px_in <= nodes[0][0]:
        return nodes[0][1]
    _lp = math.log10(px_in)
    for _i in range(len(nodes) - 1):
        _n0, _a0 = nodes[_i]
        _n1, _a1 = nodes[_i + 1]
        if _n0 <= px_in <= _n1:
            _t = ((_lp - math.log10(_n0))
                  / (math.log10(_n1) - math.log10(_n0)))
            return int(round(_a0 + (_a1 - _a0) * _t))
    return nodes[-1][1]


# --- Таблицы автоточности для 7 серых BA-методов ---
# Данные: calibrate.py (19.10.2026, i3-10100F + 1050 Ti).
# Формат: метод -> {"s2": [(px_in, acc), ...], "s8": [...],
#                   "max_px_s2": ..., "max_px_s8": ...}
# s2 — данные scale=2 (применяем для s≤2). s8 — данные scale=8
# (применяем для s≥8). Для 3≤s≤7 — min(обе таблицы), консервативно.
# За max_px_* даже acc=0 не влезает в ~30с → отказ.
_BA_TABLES = {
    "ba255": {
        "s2": [],  # не используется, см. _BA255_S2_ZONES
        "s8": [(1024, 25), (4096, 12), (16384, 6), (65536, 3)],
        "max_px_s2": 140000, "max_px_s8": 65536,
    },
    "bathin255": {
        "s2": [(16384, 12), (65536, 6), (262144, 3)],
        "s8": [(1024, 12), (4096, 6), (16384, 3), (65536, 1)],
        "max_px_s2": 262144, "max_px_s8": 65536,
    },
    "basmoothcontrast": {
        "s2": [(16384, 25), (65536, 6), (262144, 3), (1048576, 1)],
        "s8": [(1024, 100), (4096, 50), (16384, 12), (65536, 3)],
        "max_px_s2": 1048576, "max_px_s8": 65536,
    },
    "baextremum": {
        "s2": [(16384, 100), (65536, 6), (262144, 1), (1048576, 1)],
        "s8": [(1024, 100), (4096, 100), (16384, 100), (65536, 25)],
        "max_px_s2": 1048576, "max_px_s8": 65536,
    },
    "baderivative": {
        "s2": [(16384, 12), (65536, 6), (262144, 1), (1048576, 1)],
        "s8": [(1024, 100), (4096, 25), (16384, 12), (65536, 12)],
        "max_px_s2": 1048576, "max_px_s8": 65536,
    },
    "bamonochrome": {
        "s2": [(16384, 100), (65536, 50), (262144, 3), (1048576, 0)],
        "s8": [(1024, 100), (4096, 100), (16384, 6), (65536, 1)],
        "max_px_s2": 1048576, "max_px_s8": 65536,
    },
    "bamonochrome2": {
        "s2": [(16384, 100), (65536, 3), (262144, 1), (1048576, 1)],
        "s8": [(1024, 100), (4096, 100), (16384, 6), (65536, 6)],
        "max_px_s2": 1048576, "max_px_s8": 65536,
    },
    "ba255color": {
        "s2": [(16384, 10), (65536, 6), (262144, 1)],
        "s8": [(1024, 25), (4096, 12), (16384, 6), (65536, 1)],
        "max_px_s2": 262144, "max_px_s8": 65536,
    },
    "bathin255color": {
        "s2": [(16384, 12), (65536, 3), (262144, 1), (1048576, 0), (4194304, 0)],
        "s8": [(1024, 25), (4096, 6), (16384, 1), (65536, 0)],
        "max_px_s2": 4194304, "max_px_s8": 65536,
    },
    "basmoothcontrastcolor": {
        "s2": [(16384, 12), (65536, 3), (262144, 1)],
        "s8": [(1024, 100), (4096, 50), (16384, 12), (65536, 3)],
        "max_px_s2": 262144, "max_px_s8": 65536,
    },
    "baextremumcolor": {
        "s2": [(16384, 12), (65536, 3), (262144, 1)],
        "s8": [(1024, 100), (4096, 50), (16384, 12)],
        "max_px_s2": 262144, "max_px_s8": 16384,
    },
    "baderivativecolor": {
        "s2": [(16384, 100), (65536, 3)],
        "s8": [(1024, 100), (4096, 100), (16384, 100), (65536, 12)],
        "max_px_s2": 65536, "max_px_s8": 65536,
    },
    "bamonochromecolor": {
        "s2": [(16384, 100), (65536, 25), (262144, 12)],
        "s8": [(1024, 100), (4096, 100), (16384, 25), (65536, 6)],
        "max_px_s2": 262144, "max_px_s8": 65536,
    },
    "bamonochrome2color": {
        "s2": [(16384, 100), (65536, 3), (262144, 1)],
        "s8": [(1024, 100), (4096, 100), (16384, 6), (65536, 6)],
        "max_px_s2": 262144, "max_px_s8": 65536,
    },
}


def _interp_acc(nodes, px):
    """Линейная интерполяция acc по log10(px) между узлами (px, acc)."""
    import math
    if px <= nodes[0][0]:
        return nodes[0][1]
    if px >= nodes[-1][0]:
        return nodes[-1][1]
    _lp = math.log10(px)
    for _i in range(len(nodes) - 1):
        _n0, _a0 = nodes[_i]
        _n1, _a1 = nodes[_i + 1]
        if _n0 <= px <= _n1:
            _t = ((_lp - math.log10(_n0))
                  / (math.log10(_n1) - math.log10(_n0)))
            return int(round(_a0 + (_a1 - _a0) * _t))
    return nodes[-1][1]


# ba255 s2 — зоны (данные: ladder 19.10.2026, i3-10100F+1050Ti).
# 0-16k: 21 (8.9s); 16-25.6k: 22 (23.7s); 25.6-40k: 16 (24.1s);
# 40-50k: 12 (10s на 40k, ~23s на 50k); 50-140k: 0 (1.6-1.9s).
# >140k: отказ — fast path (acc=100) возвращает битый Bitmap 1x1.
# CUDA-краш при acc>=22 на 16k — поэтому не поднимаем выше 21.
_BA255_S2_ZONES = [
    (0,       16384,   21),
    (16384,   25600,   22),
    (25600,   40000,   16),
    (40000,   50000,   12),
    (50000,   140000,  0),
]
_BA255_MAX_PX_S2 = 140000


def _auto_accuracy_ba255_s2(px_in):
    for lo, hi, acc in _BA255_S2_ZONES:
        if lo <= px_in < hi:
            return acc
    return None


def _auto_accuracy_ba255(w, h, scale):
    """ba255: s2 — зоны, s8 — интерполяция, 3-7 — min обеих."""
    try:
        s = max(2, int(scale))
    except (TypeError, ValueError):
        s = 2
    px_in = max(1, w * h)
    tbl = _BA_TABLES["ba255"]
    if s <= 2:
        if px_in > _BA255_MAX_PX_S2:
            return None
        return _auto_accuracy_ba255_s2(px_in)
    if s >= 8:
        if px_in > tbl["max_px_s8"]:
            return None
        return _interp_acc(tbl["s8"], px_in)
    a2 = (_auto_accuracy_ba255_s2(px_in)
          if px_in <= _BA255_MAX_PX_S2 else None)
    a8 = (_interp_acc(tbl["s8"], px_in)
          if px_in <= tbl["max_px_s8"] else None)
    if a2 is None and a8 is None:
        return None
    if a2 is None:
        return a8
    if a8 is None:
        return a2
    return min(a2, a8)


def _auto_accuracy_ba_table(method, w, h, scale):
    """Автоточность для 7 серых BA-методов по таблице (не формулой).

    s ≤ 2 — таблица s=2, s ≥ 8 — таблица s=8.
    3 ≤ s ≤ 7 — min(обе таблицы), консервативно.
    px_in > max_px — None (даже acc=0 не влезает в ~30с).
    """
    if method == "ba255":
        return _auto_accuracy_ba255(w, h, scale)
    try:
        s = max(2, int(scale))
    except (TypeError, ValueError):
        s = 2
    tbl = _BA_TABLES.get(method)
    if not tbl:
        return 100
    px_in = max(1, w * h)
    s2 = tbl["s2"]
    s8 = tbl["s8"]
    max_s2 = tbl["max_px_s2"]
    max_s8 = tbl["max_px_s8"]
    if s <= 2:
        if px_in > max_s2:
            return None
        return _interp_acc(s2, px_in)
    if s >= 8:
        if px_in > max_s8:
            return None
        return _interp_acc(s8, px_in)
    a2 = _interp_acc(s2, px_in) if px_in <= max_s2 else None
    a8 = _interp_acc(s8, px_in) if px_in <= max_s8 else None
    if a2 is None and a8 is None:
        return None
    if a2 is None:
        return a8
    if a8 is None:
        return a2
    return min(a2, a8)


_PROGRESS_BAR_WIDTH = 40


def _progress_schedule():
    """Моменты (сек) появления каждого символа прогресс-бара.

    Первые 20 — по секунде. Дальше интервал растёт на 1с:
    #21 через 2с после #20, #22 через 3с после #21, ...
    Полная шкала ≈ 250с — оператору хватает «движения» на любой
    реальный таймаут, при этом бар не врёт (не доходит до 100%).
    """
    sched = []
    for i in range(_PROGRESS_BAR_WIDTH):
        if i == 0:
            sched.append(1.0)
        elif i < 20:
            sched.append(float(i + 1))
        else:
            sched.append(sched[i - 1] + (i - 18))
    return sched


def _render_progress(k):
    k = max(0, min(_PROGRESS_BAR_WIDTH, k))
    if k == 0:
        return "[" + "." * _PROGRESS_BAR_WIDTH + "]"
    return ("[" + "=" * (k - 1) + ">" + "." * (_PROGRESS_BAR_WIDTH - k) + "]")


def _run_with_progress(cmd, timeout_s):
    """subprocess.run с визуальным прогресс-баром в stdout (перерисовка \\r).

    Для внешних CLI без обратной связи (ScaleSmooth, sharpened_bilinear).
    Возвращает CompletedProcess как обычный subprocess.run. При отсутствии
    TTY (перенаправленный вывод) — просто subprocess.run без бара.
    TimeoutExpired пробрасывается.
    """
    import sys as _sys
    import threading as _th
    import time as _t

    try:
        _is_tty = bool(getattr(_sys.stdout, "isatty", lambda: False)())
    except Exception:
        _is_tty = False
    if not _is_tty:
        return subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_s,
        )

    sched = _progress_schedule()
    stop = _th.Event()
    state = {"k": -1}

    def _draw():
        t0 = _t.time()
        try:
            while not stop.is_set():
                elapsed = _t.time() - t0
                k = 0
                for i, t in enumerate(sched):
                    if elapsed >= t:
                        k = i + 1
                    else:
                        break
                if k != state["k"]:
                    state["k"] = k
                    _sys.stdout.write("\r" + _render_progress(k))
                    _sys.stdout.flush()
                _t.sleep(0.08)
        except Exception:
            pass

    th = _th.Thread(target=_draw, daemon=True)
    th.start()
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_s,
        )
    finally:
        stop.set()
        th.join(timeout=2.0)
        try:
            _sys.stdout.write("\r" + " " * (2 + _PROGRESS_BAR_WIDTH) + "\r")
            _sys.stdout.flush()
        except Exception:
            pass


def _run_sharpened_bilinear(args, ip, scale):
    """Апскейл через внешний CLI sharpened_bilinear (Rust).

    CLI: sharpened_bilinear <INPUT> --output <PATH> --factor <N>
    Без автоточности (алгоритм фиксированный), только резкий
    bilinear + шарпинг. Exe ожидается в
    side/external/sharpened_bilinear/sharpened_bilinear.exe.
    """
    if not _SHARPENED_BILINEAR_EXE.is_file():
        return ("Ошибка: sharpened_bilinear.exe не найден: "
                + str(_SHARPENED_BILINEAR_EXE)
                + " (положи exe в side/external/sharpened_bilinear/)")

    out_arg = (args.get("сохранить") or args.get("куда")
               or args.get("выход") or args.get("путь"))
    if out_arg:
        out_path = Path(out_arg)
    else:
        out_path = ip.with_name(
            ip.stem + "_sharpened_bilinear_x" + str(scale) + ".png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    overwrite = bool(args.get("перезаписать", False))
    if out_path.is_file() and not overwrite:
        return ("Ошибка: файл уже существует: " + str(out_path)
                + " (перезаписать: true)")

    try:
        _timeout_s = int(args.get("таймаут") or 60)
    except (TypeError, ValueError):
        _timeout_s = 60
    if _timeout_s < 5:
        _timeout_s = 5
    if _timeout_s > 600:
        _timeout_s = 600

    cmd = [str(_SHARPENED_BILINEAR_EXE), str(ip),
           "--output", str(out_path), "--factor", str(scale)]
    _t0 = time.time()
    try:
        r = _run_with_progress(cmd, _timeout_s)
    except FileNotFoundError:
        return "Ошибка: не запустить sharpened_bilinear.exe"
    except subprocess.TimeoutExpired:
        return ("Ошибка: таймаут " + str(_timeout_s) + " сек "
                "(sharpened_bilinear не уложился)")
    _elapsed = time.time() - _t0

    out_text = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        return ("Ошибка sharpened_bilinear (код "
                + str(r.returncode) + "):\n" + out_text[-500:])
    if not out_path.is_file():
        return ("Ошибка: файл не создан. Вывод: " + out_text[-300:])

    size = out_path.stat().st_size
    try:
        from PIL import Image
        with Image.open(str(out_path)) as im:
            w, h = im.size
    except Exception:
        w, h = "?", "?"
    return ("Готово: " + str(out_path)
            + " (" + str(w) + "x" + str(h)
            + ", метод sharpened_bilinear, ×" + str(scale)
            + ", " + ("%.1f" % _elapsed) + "с"
            + ", " + str(size // 1024) + " КБ)")


@register(["апскейл", "увеличить_картинку", "апскейл_изображения"])
def upscale(args: dict) -> str:
    """Апскейл изображения через ScaleSmooth.exe.

    Методы работают только с целочисленным масштабом (2, 3, 4, ...).

    Параметры:
      картинка — вход (обязательно)
      масштаб  — целое число ≥ 2 (обязательно)
      метод    — методы (обязательно, без него — ошибка и справка):
                 smooth, smoothcolor, furry, furrycolor,
                 rough, roughcolor, bold, boldcolor, contrastbold,
                 contrastboldcolor, separate, separatecolor.
                 ba, bacolor,
                 bacontrast, bacontrastcolor, ba255, ba255color,
                 bathin255, bathin255color, basmoothcontrast,
                 basmoothcontrastcolor, baextremum, baextremumcolor,
                 baderivative, baderivativecolor, bamonochrome,
                 bamonochrome2, bamonochromecolor, bamonochrome2color.
                 sharpened_bilinear (алиас: sharpened) — резкий bilinear
                 + шарпинг, быстрый (~0.2с). По характеру близок к
                 lanczos: резкие контуры, но на контрастных переходах
                 (мелкий текст, границы фигур) даёт ореолы/звон.
                 Для текста и иконок лучше bicubic/smoothcolor; для
                 текстур — на уровне bacolor, но в разы быстрее.
                 Pillow:
                 bilinear, bicubic, lanczos.
                 Forge (нейросети, через Forge API, префикс forge:):
                 forge:RealESRGAN_x4plus, forge:RealESRGAN_x4plus_anime_6B
                 и любые другие из /sdapi/v1/upscalers.
                 Ограничение Forge: точные размеры выхода не гарантированы
                 (RealESRGAN работает ×4, потом ужимается с округлением/
                 обрезкой). На визуал не влияет.

    Краткие характеристики:
      — Фото/портреты: smoothcolor (лучший, без звона), bacolor (резче,
        умеренный звон), smooth (мыло, безопасно).
      — Коллажи/текст+графика: bacolor (баланс), smoothcolor (гладко),
        bold (только для ч/б логотипов, цветной boldcolor даёт крупу).
      — Пиксель-арт/ч/б контуры: smooth, furry (без color) — сохраняют
        квадратные пиксели; rough — рваные края; separate — чёрные точки.
      — Иконки/антиалиасинг: bacolor (лучший из плохих), smoothcolor
        (мыло), separatecolor (цветной звон).
      — Мех/текстуры: smoothcolor (размывает, но без шума); furrycolor
        — цифровой шум (не использовать).
      — Не для лиц/фото: furrycolor (шум), boldcolor/contrastboldcolor
        (красная крупа/ореолы), ba255color (полосы, пересветы),
        bathin255color (шахматная доска), bamonochrome/2 (выжигают
        до белого), basmoothcontrastcolor (выцветание).
      — Специфические: rough/roughcolor (только для текстур с зерном),
        baderivative/baderivativecolor (акварель, стилизация),
        separate/separatecolor (резкость ценой звона).
      — Базовые (Pillow, без exe): bilinear — мыло, безопасно;
        bicubic — резче, умеренный звон; lanczos — самый резкий,
        сильный звон на контурах.
      — Forge (нейросети): ЛУЧШИЕ для текстур (мех, волосы — 10/10),
        портретов (при исходнике ≥256px) и аниме/пиксель-арта
        (anime_6B). КАТЕГОРИЧЕСКИ НЕ для текста и коллажей с текстом —
        RealESRGAN галлюцинирует буквы в «эльфийский». Для текста
        используйте bacolor/bicubic/lanczos.

      сохранить — куда (алиасы: куда, выход, путь). Иначе рядом
                 с исходником: <имя>_<метод>_x<масштаб>.png
      перезаписать — false: не трогать существующий файл
      таймаут  — сек, максимум времени на один апскейл (по умолч. 60)

    Внешние CLI (ScaleSmooth, sharpened_bilinear) показывают визуальный
    прогресс-бар в консоли: [===>.....], первые 20 символов — по секунде,
    дальше интервал растёт. Pillow-методы и Forge — без бара.
    """
    _early_method = str(args.get("метод") or "").strip().lower()
    if not _early_method:
        return ("Ошибка: не указан метод (smoothcolor, bicubic, "
                "forge:RealESRGAN_x4plus и др.) — см. справку ниже.")
    if _early_method in _SHARPENED_METHODS:
        if not _SHARPENED_BILINEAR_EXE.is_file():
            return ("Ошибка: sharpened_bilinear.exe не найден: "
                    + str(_SHARPENED_BILINEAR_EXE))
    elif (not _early_method.startswith(_FORGE_PREFIX)
            and _early_method not in _PIL_METHODS
            and not _SCALESMOOTH_EXE.is_file()):
        return ("Ошибка: ScaleSmooth.exe не найден: "
                + str(_SCALESMOOTH_EXE)
                + " (собери проект: dotnet build в папке scaleSmooth)")

    img = args.get("картинка") or args.get("изображение") or args.get("image")
    if not img:
        return "Ошибка: не указана картинка"
    ip = Path(img)
    if not ip.is_file():
        return "Ошибка: нет картинки " + str(ip)

    try:
        scale = int(args.get("масштаб") or args.get("scale") or 0)
    except (TypeError, ValueError):
        return "Ошибка: масштаб должен быть целым числом"
    if scale < 2:
        return "Ошибка: масштаб должен быть ≥ 2 (только целые: 2, 3, 4...)"

    method_raw = str(args.get("метод") or "").strip()
    method = method_raw.lower()
    is_forge = method_raw.lower().startswith(_FORGE_PREFIX)
    forge_upscaler = ""
    if is_forge:
        forge_upscaler = method_raw[len(_FORGE_PREFIX):].strip()
        if not forge_upscaler:
            return ("Ошибка: укажи имя апскейлера после 'forge:', "
                    "например forge:RealESRGAN_x4plus")
    elif (method not in _METHOD_MAP and method not in _PIL_METHODS
          and method not in _SHARPENED_METHODS):
        return ("Ошибка: метод " + method + " не поддерживается. "
                "Доступны: " + ", ".join(list(_METHOD_MAP) + list(_PIL_METHODS)
                                          + list(_SHARPENED_METHODS))
                + ", а также forge:<имя> (см. /sdapi/v1/upscalers)")

    if method in _SHARPENED_METHODS:
        return _run_sharpened_bilinear(args, ip, scale)

    _sc_auto_acc = None
    if method == "smoothcolor":
        try:
            from PIL import Image as _PIL
            with _PIL.open(ip) as _im:
                _sc_w, _sc_h = _im.size
            _sc_px_in = _sc_w * _sc_h
            _sc_px_out = _sc_px_in * scale * scale
            if _sc_px_out > 4500000:
                return ("Ошибка: smoothcolor на входе "
                        + str(_sc_w) + "x" + str(_sc_h)
                        + " (×" + str(scale) + ", выход "
                        + str(_sc_px_out // 1000000) + "M px) — "
                        "время не влезает в 30с, и снижение точности не "
                        "помогает (фикс. стоимость на большом входе). "
                        "Возьми smooth (быстрее ~6 раз), уменьши "
                        "вход или масштаб.")
            _sc_auto_acc = _auto_accuracy_smoothcolor(_sc_w, _sc_h, scale)
        except Exception:
            pass

    _fur_auto_acc = None
    if method == "furry":
        try:
            from PIL import Image as _PIL
            with _PIL.open(ip) as _im:
                _fur_w, _fur_h = _im.size
            _fur_auto_acc = _auto_accuracy_furry(_fur_w, _fur_h, scale)
            if _fur_auto_acc is None:
                return ("Ошибка: furry на входе "
                        + str(_fur_w) + "x" + str(_fur_h)
                        + " (×" + str(scale) + ") не влезает в ~30с даже "
                        "при точности 0 — время растёт как px^1.5 и "
                        "scale^2.8. Возьми smooth/smoothcolor, уменьши "
                        "вход или масштаб.")
        except Exception:
            pass

    _furc_auto_acc = None
    if method == "furrycolor":
        try:
            from PIL import Image as _PIL
            with _PIL.open(ip) as _im:
                _furc_w, _furc_h = _im.size
            _furc_auto_acc = _auto_accuracy_furrycolor(_furc_w, _furc_h, scale)
            if _furc_auto_acc is None:
                return ("Ошибка: furrycolor на входе "
                        + str(_furc_w) + "x" + str(_furc_h)
                        + " (×" + str(scale) + ") не влезает в ~30с даже "
                        "при точности 0 — color-версия furry в ~3-4 раза "
                        "медленнее, время растёт как scale^2.8. Возьми "
                        "smooth/smoothcolor, уменьши вход или масштаб.")
        except Exception:
            pass

    _rough_auto_acc = None
    if method == "rough":
        try:
            from PIL import Image as _PIL
            with _PIL.open(ip) as _im:
                _rough_w, _rough_h = _im.size
            _rough_auto_acc = _auto_accuracy_rough(_rough_w, _rough_h, scale)
            if _rough_auto_acc is None:
                return ("Ошибка: rough на входе "
                        + str(_rough_w) + "x" + str(_rough_h)
                        + " (×" + str(scale) + ") не влезает в ~30с даже "
                        "при точности 0 — время растёт как px·scale^2.65. "
                        "Возьми smooth, уменьши вход или масштаб.")
        except Exception:
            pass

    _roughc_auto_acc = None
    if method == "roughcolor":
        try:
            from PIL import Image as _PIL
            with _PIL.open(ip) as _im:
                _roughc_w, _roughc_h = _im.size
            _roughc_auto_acc = _auto_accuracy_roughcolor(_roughc_w, _roughc_h, scale)
            if _roughc_auto_acc is None:
                return ("Ошибка: roughcolor на входе "
                        + str(_roughc_w) + "x" + str(_roughc_h)
                        + " (×" + str(scale) + ") не влезает в ~30с даже "
                        "при точности 0 — color-версия rough в ~3-4 раза "
                        "медленнее, время растёт как scale^2.52. Возьми "
                        "smooth/smoothcolor, уменьши вход или масштаб.")
        except Exception:
            pass

    _bold_auto_acc = None
    if method in ("bold", "contrastbold"):
        try:
            from PIL import Image as _PIL
            with _PIL.open(ip) as _im:
                _bold_w, _bold_h = _im.size
            _bold_auto_acc = _auto_accuracy_bold(_bold_w, _bold_h, scale)
            if _bold_auto_acc is None:
                return ("Ошибка: " + method + " на входе "
                        + str(_bold_w) + "x" + str(_bold_h)
                        + " (×" + str(scale) + ") не влезает в ~30с даже "
                        "при точности 0 — время резко растёт с px_in. "
                        "Возьми smooth, уменьши вход или масштаб.")
        except Exception:
            pass

    _boldc_auto_acc = None
    if method == "boldcolor":
        try:
            from PIL import Image as _PIL
            with _PIL.open(ip) as _im:
                _boldc_w, _boldc_h = _im.size
            _boldc_auto_acc = _auto_accuracy_boldcolor(_boldc_w, _boldc_h, scale)
            if _boldc_auto_acc is None:
                return ("Ошибка: boldcolor на входе "
                        + str(_boldc_w) + "x" + str(_boldc_h)
                        + " (×" + str(scale) + ") не влезает в ~30с даже "
                        "при точности 0 — color-версия bold резко тяжелеет "
                        "с px_in. Возьми smooth/smoothcolor, уменьши "
                        "вход или масштаб.")
        except Exception:
            pass

    _cbc_auto_acc = None
    if method == "contrastboldcolor":
        try:
            from PIL import Image as _PIL
            with _PIL.open(ip) as _im:
                _cbc_w, _cbc_h = _im.size
            _cbc_auto_acc = _auto_accuracy_contrastboldcolor(_cbc_w, _cbc_h, scale)
            if _cbc_auto_acc is None:
                return ("Ошибка: contrastboldcolor на входе "
                        + str(_cbc_w) + "x" + str(_cbc_h)
                        + " (×" + str(scale) + ") не влезает в ~30с даже "
                        "при точности 0 — color-версия contrastbold резко "
                        "тяжелеет с px_in. Возьми smooth/smoothcolor, "
                        "уменьши вход или масштаб.")
        except Exception:
            pass

    _sep_auto_acc = None
    if method == "separate":
        try:
            from PIL import Image as _PIL
            with _PIL.open(ip) as _im:
                _sep_w, _sep_h = _im.size
            _sep_auto_acc = _auto_accuracy_separate(_sep_w, _sep_h, scale)
            if _sep_auto_acc is None:
                return ("Ошибка: separate на входе "
                        + str(_sep_w) + "x" + str(_sep_h)
                        + " (×" + str(scale) + ") не влезает в ~30с даже "
                        "при точности 1 — выход слишком большой. "
                        "Возьми smooth, уменьши вход или масштаб.")
        except Exception:
            pass

    _sepc_auto_acc = None
    if method == "separatecolor":
        try:
            from PIL import Image as _PIL
            with _PIL.open(ip) as _im:
                _sepc_w, _sepc_h = _im.size
            _sepc_auto_acc = _auto_accuracy_separatecolor(_sepc_w, _sepc_h, scale)
            if _sepc_auto_acc is None:
                return ("Ошибка: separatecolor на входе "
                        + str(_sepc_w) + "x" + str(_sepc_h)
                        + " (×" + str(scale) + ") не влезает в ~30с даже "
                        "при точности 1 — вход слишком большой. "
                        "Возьми smooth/smoothcolor, уменьши вход или масштаб.")
        except Exception:
            pass

    _bac_auto_acc = None
    if method == "bacolor":
        try:
            from PIL import Image as _PIL
            with _PIL.open(ip) as _im:
                _bac_w, _bac_h = _im.size
            _bac_auto_acc = _auto_accuracy_bacolor(_bac_w, _bac_h, scale)
            if _bac_auto_acc is None:
                return ("Ошибка: bacolor на входе "
                        + str(_bac_w) + "x" + str(_bac_h)
                        + " (×" + str(scale) + ") не влезает в ~30с даже "
                        "при точности 1 — bacolor тяжелее ba в 10-50 раз "
                        "на малых входах. Возьми ba или smooth, уменьши "
                        "вход или масштаб.")
        except Exception:
            pass

    _bct_auto_acc = None
    if method == "bacontrast":
        try:
            from PIL import Image as _PIL
            with _PIL.open(ip) as _im:
                _bct_w, _bct_h = _im.size
            _bct_auto_acc = _auto_accuracy_bacontrast(_bct_w, _bct_h, scale)
            if _bct_auto_acc is None:
                return ("Ошибка: bacontrast на входе "
                        + str(_bct_w) + "x" + str(_bct_h)
                        + " (×" + str(scale) + ") не влезает в ~30с даже "
                        "при точности 3 (на x2) или 6 (на x8). Возьми "
                        "ba/smooth, уменьши вход или масштаб.")
        except Exception:
            pass

    _bctc_auto_acc = None
    if method == "bacontrastcolor":
        try:
            from PIL import Image as _PIL
            with _PIL.open(ip) as _im:
                _bctc_w, _bctc_h = _im.size
            _bctc_auto_acc = _auto_accuracy_bacontrastcolor(_bctc_w, _bctc_h, scale)
            if _bctc_auto_acc is None:
                _bctc_s = max(2, int(scale))
                if _bctc_s >= 5 and _bctc_w * _bctc_h > 131072:
                    return ("Ошибка: bacontrastcolor на входе "
                            + str(_bctc_w) + "x" + str(_bctc_h)
                            + " (×" + str(scale) + ") — в exe баг: при "
                            "scale≥5 и таком входе метод возвращает "
                            "пустой Bitmap 1x1 при любом acc. Возьми "
                            "меньший scale или x2.")
                return ("Ошибка: bacontrastcolor на входе "
                        + str(_bctc_w) + "x" + str(_bctc_h)
                        + " (×" + str(scale) + ") не влезает в ~30с "
                        "даже при низкой точности. Возьми ba/smooth, "
                        "уменьши вход или масштаб.")
        except Exception:
            pass

    # Точность всегда авто: ручной ввод убран.
    _acc_raw = None
    if _acc_raw is None or _acc_raw == "":
        if method == "ba":
            try:
                from PIL import Image as _PIL
                with _PIL.open(ip) as _im:
                    _src_w, _src_h = _im.size
                accuracy = _auto_accuracy_ba_scaled(_src_w, _src_h, scale)
            except Exception:
                accuracy = 100
        elif method == "smooth":
            try:
                from PIL import Image as _PIL
                with _PIL.open(ip) as _im:
                    _src_w, _src_h = _im.size
                accuracy = _auto_accuracy_smooth(_src_w, _src_h, scale)
            except Exception:
                accuracy = 100
        elif method == "smoothcolor" and _sc_auto_acc is not None:
            accuracy = _sc_auto_acc
        elif method == "furry" and _fur_auto_acc is not None:
            accuracy = _fur_auto_acc
        elif method == "furrycolor" and _furc_auto_acc is not None:
            accuracy = _furc_auto_acc
        elif method == "rough" and _rough_auto_acc is not None:
            accuracy = _rough_auto_acc
        elif method == "roughcolor" and _roughc_auto_acc is not None:
            accuracy = _roughc_auto_acc
        elif method in ("bold", "contrastbold") and _bold_auto_acc is not None:
            accuracy = _bold_auto_acc
        elif method == "boldcolor" and _boldc_auto_acc is not None:
            accuracy = _boldc_auto_acc
        elif method == "contrastboldcolor" and _cbc_auto_acc is not None:
            accuracy = _cbc_auto_acc
        elif method == "separate" and _sep_auto_acc is not None:
            accuracy = _sep_auto_acc
        elif method == "separatecolor" and _sepc_auto_acc is not None:
            accuracy = _sepc_auto_acc
        elif method == "bacolor" and _bac_auto_acc is not None:
            accuracy = _bac_auto_acc
        elif method == "bacontrast" and _bct_auto_acc is not None:
            accuracy = _bct_auto_acc
        elif method == "bacontrastcolor" and _bctc_auto_acc is not None:
            accuracy = _bctc_auto_acc
        elif method in _BA_TABLES:
            try:
                from PIL import Image as _PIL
                with _PIL.open(ip) as _im:
                    _tbl_w, _tbl_h = _im.size
                _tbl_acc = _auto_accuracy_ba_table(
                    method, _tbl_w, _tbl_h, scale)
                if _tbl_acc is None:
                    return ("Ошибка: " + method + " на входе "
                            + str(_tbl_w) + "x" + str(_tbl_h)
                            + " (×" + str(scale) + ") не влезает в ~30с "
                            "даже при минимальной точности. "
                            "Уменьши вход или масштаб.")
                accuracy = _tbl_acc
            except Exception:
                accuracy = 100
        else:
            accuracy = 100

    accuracy = max(0, min(100, accuracy))

    try:
        _timeout_s = int(args.get("таймаут") or 60)
    except (TypeError, ValueError):
        _timeout_s = 60
    if _timeout_s < 5:
        _timeout_s = 5
    if _timeout_s > 600:
        _timeout_s = 600

    out_arg = (args.get("сохранить") or args.get("куда")
               or args.get("выход") or args.get("путь"))
    if out_arg:
        out_path = Path(out_arg)
    else:
        out_path = ip.with_name(
            ip.stem + "_" + method + "_x" + str(scale) + ".png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    overwrite = bool(args.get("перезаписать", False))
    if out_path.is_file() and not overwrite:
        return ("Ошибка: файл уже существует: " + str(out_path)
                + " (перезаписать: true)")

    if method in _PIL_METHODS:
        try:
            from PIL import Image as _PIL_Image
            _pil_resample = {
                "bilinear": _PIL_Image.BILINEAR,
                "bicubic": _PIL_Image.BICUBIC,
                "lanczos": _PIL_Image.LANCZOS,
            }[method]
            with _PIL_Image.open(ip) as _pim:
                _new_w = _pim.width * scale
                _new_h = _pim.height * scale
                _pim_out = _pim.resize((_new_w, _new_h), _pil_resample)
                _pim_out.save(out_path)
        except Exception as _e:
            return "Ошибка PIL (" + method + "): " + str(_e)
        size = out_path.stat().st_size
        return ("Готово: " + str(out_path)
                + " (" + str(_new_w) + "x" + str(_new_h)
                + ", метод " + method + ", ×" + str(scale)
                + ", " + str(size // 1024) + " КБ)")

    if is_forge:
        import base64 as _b64_forge
        import io as _io_forge
        import _actions.stabledif as _sd_forge
        if not _sd_forge._is_alive():
            _startup = _sd_forge._ensure_forge()
            if not _sd_forge._is_alive():
                return "Не удалось поднять Forge: " + _startup
        try:
            _, _upscalers = _sd_forge._http(
                "GET", "/sdapi/v1/upscalers", None, timeout=15)
        except Exception as _e:
            return "Ошибка запроса списка апскейлеров: " + str(_e)
        _names = [u.get("name") for u in (_upscalers or [])
                  if isinstance(u, dict) and u.get("name")]
        if forge_upscaler not in _names:
            return ("Ошибка: апскейлер " + forge_upscaler
                    + " не найден. Доступны: " + ", ".join(_names))
        try:
            with open(str(ip), "rb") as _f:
                _b64_img = _b64_forge.b64encode(_f.read()).decode("ascii")
        except Exception as _e:
            return "Ошибка чтения файла: " + str(_e)
        _body = {
            "image": _b64_img,
            "resize_mode": 0,
            "upscaling_resize": scale,
            "upscaler_1": forge_upscaler,
            "upscaler_2": "None",
            "extras_upscaler_2_visibility": 0,
            "image_format": "png",
        }
        try:
            _, _resp = _sd_forge._http(
                "POST", "/sdapi/v1/extra-single-image", _body,
                timeout=_timeout_s)
        except Exception as _e:
            return "Ошибка запроса апскейла: " + str(_e)
        _img_b64 = _resp.get("image") if isinstance(_resp, dict) else None
        if not _img_b64:
            return "Forge вернул без image: " + str(_resp)[:300]
        try:
            _raw = _b64_forge.b64decode(_img_b64)
            from PIL import Image as _PIL_Forge
            _img = _PIL_Forge.open(_io_forge.BytesIO(_raw))
            _img.load()
            _img.save(str(out_path))
        except Exception as _e:
            return "Ошибка декодирования ответа: " + str(_e)
        size = out_path.stat().st_size
        return ("Готово: " + str(out_path)
                + " (" + str(_img.width) + "x" + str(_img.height)
                + ", forge:" + forge_upscaler + ", ×" + str(scale)
                + ", " + str(size // 1024) + " КБ)")

    cmd = [str(_SCALESMOOTH_EXE), str(ip), str(out_path),
           str(scale), _METHOD_MAP[method], str(accuracy)]
    _t0 = time.time()
    try:
        r = _run_with_progress(cmd, _timeout_s)
    except FileNotFoundError:
        return "Ошибка: не запустить ScaleSmooth.exe"
    except subprocess.TimeoutExpired:
        return ("Ошибка: таймаут " + str(_timeout_s) + " сек "
                "(метод " + method + " не уложился; "
                "попробуй другой метод или больше таймаут)")
    _elapsed = time.time() - _t0

    out_text = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        return ("Ошибка ScaleSmooth (код " + str(r.returncode) + "):\n"
                + out_text[-500:])

    if not out_path.is_file():
        return ("Ошибка: файл не создан. Вывод: "
                + out_text[-300:])

    size = out_path.stat().st_size
    try:
        from PIL import Image
        with Image.open(str(ip)) as _sim:
            _sw, _sh = _sim.size
        _exp_w = _sw * scale
        _exp_h = _sh * scale
        with Image.open(str(out_path)) as im:
            w, h = im.size
        if (w, h) != (_exp_w, _exp_h):
            try:
                out_path.unlink()
            except Exception:
                pass
            return ("Ошибка: exe вернул битую картинку "
                    + str(w) + "x" + str(h) + " (ожидалось "
                    + str(_exp_w) + "x" + str(_exp_h) + "). "
                    "Это баг fast-path на этом (px, acc) — попробуй "
                    "другую точность или acc=0.")
    except Exception:
        w, h = "?", "?"
    return ("Готово: " + str(out_path)
            + " (" + str(w) + "x" + str(h)
            + ", метод " + method + ", ×" + str(scale)
            + ", " + ("%.1f" % _elapsed) + "с"
            + ", " + str(size // 1024) + " КБ)")