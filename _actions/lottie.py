"""Команды для работы с .lotty (Lottie с текстурами)."""
from __future__ import annotations
import os
import sys
import subprocess
from pathlib import Path

from _actions import register

_HERE = Path(__file__).resolve().parent
_ANYJSON = _HERE.parent
_APPLY_TEXTURES = _ANYJSON / "side" / "external" / "lottie" / "apply_textures.py"
_SVG2LOTTY = _ANYJSON / "side" / "external" / "lottie" / "svg2lottie_v6.py"
_RENDER = _ANYJSON / "side" / "external" / "lottie" / "render-lv6.py"


def _run_py(cmd, timeout):
    """Запустить python-скрипт, стримить stdout в консоль в реальном времени."""
    import time as _time
    import sys as _sys
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        env=env, bufsize=1,
    )
    lines = []
    start = _time.time()
    killed_timeout = False
    try:
        for line in proc.stdout:
            # Фильтруем прогресс-бар tqdm: выводим в консоль, но не копим в контекст.
            # tqdm использует \r (возврат каретки), поэтому в lines он не нужен.
            _is_progress = ("%|" in line or "it/s]" in line or "s/it]" in line or "\r" in line)
            if not _is_progress:
                lines.append(line.rstrip("\n"))
            try:
                _sys.stdout.write(line)
                _sys.stdout.flush()
            except Exception:
                pass
            if timeout and (_time.time() - start) > timeout:
                killed_timeout = True
                try: proc.kill()
                except Exception: pass
                break
        try:
            proc.wait(timeout=10)
        except Exception:
            try: proc.kill()
            except Exception: pass
    except Exception as e:
        try: proc.kill()
        except Exception: pass
        lines.append("Error: " + str(e))

    if killed_timeout:
        lines.append("Таймаут " + str(timeout) + " сек")

    result = type("R", (), {})()
    result.returncode = -1 if killed_timeout else (proc.returncode or 0)
    result.stdout = "\n".join(lines)
    result.stderr = ""
    return result


def _resolve_tex(tex, base_dir):
    if not tex:
        return tex
    if tex.startswith(("data:", "asset:")):
        return tex
    p = Path(tex)
    if p.is_absolute():
        return str(p)
    return str((base_dir / p).resolve())


def _build_spec(rule, base_dir):
    """Разобрать одно правило 'шаблон:текстура[:op[:mode]]'.

    Принимает только строку. Поддерживаемые формы:
      "шаблон:текстура"             — op=100, mode=tile
      "шаблон:текстура:80"          — op=80, mode=tile
      "шаблон:текстура:80:cover"    — op=80, mode=cover

    Путь к текстуре может быть абсолютным (C:\\x\\y.png) — двоеточие
    диска не мешает: последние один-два сегмента отделяются с конца,
    причём сегмент mode распознаётся по имени (tile/stretch/cover),
    а сегмент opacity — по float.

    Возвращает строку в native-формате apply_textures.py:
      "шаблон=текстура[:op[:mode]]"
    или None, если правило не разобрано.
    """
    if not isinstance(rule, str):
        return None
    s = rule.strip()
    if not s or ":" not in s:
        return None
    pat, rest = s.split(":", 1)
    pat = pat.strip()
    if not pat:
        return None

    tex = rest
    opacity = None
    mode = None
    _MODES = ("tile", "stretch", "cover")
    parts = rest.rsplit(":", 2)

    if len(parts) >= 2 and parts[-1].strip().lower() in _MODES:
        mode = parts[-1].strip().lower()
        rest2 = ":".join(parts[:-1])
        sub = rest2.rsplit(":", 1)
        if len(sub) == 2:
            try:
                opacity = float(sub[1])
                tex = sub[0]
            except ValueError:
                tex = rest2
        else:
            tex = rest2
    elif len(parts) >= 2:
        try:
            opacity = float(parts[-1])
            tex = ":".join(parts[:-1])
        except ValueError:
            tex = rest

    tex = _resolve_tex(tex.strip(), base_dir)
    spec = pat + "=" + tex
    if opacity is not None:
        spec += ":" + format(opacity, "g")
        if mode:
            spec += ":" + mode
    elif mode:
        spec += ":100:" + mode
    return spec


@register(["текстурировать_лотти", "текстурировать", "наложить_текстуры"])
def texture_lottie(args):
    """Наложить текстуры на слои .lotty по шаблонам имён.

    Читает .lotty (Lottie JSON), добавляет в подходящие слои поля
    texture / texture_opacity / texture_mode. Сами пиксели не вшиваются —
    рендерер (render-lv6.py) читает их при рендере.

    Параметры:
      вход        — исходный .lotty / .json (обязательно)
      выход       — куда сохранить (по умолч. <вход>.tex.lotty)
      текстуры    — строка (или список строк). Формат одного правила:
                    "ШАБЛОН:ТЕКСТУРА[:OP[:MODE]]"
                    Несколько правил — через ';', хвостовой ';' допустим.
                    Пример: "Корпус:metal.png:80:cover;Уши*:dark.png:85"
                    OP — 0..100 (по умолч. 100), MODE — tile|stretch|cover
                    (по умолч. tile). Путь к текстуре может быть
                    абсолютным (C:/.../) — двоеточие диска не мешает.
      таймаут     — секунд (по умолч. 120)

    Шаблон — glob по имени слоя: "Корпус", "Уши*", "*Голова*".
    Прозрачность — 0..100 (по умолч. 100).
    Режим — tile | stretch | cover (по умолч. tile).
    Путь к текстуре может быть абсолютным или относительным
    (относительно папки .lotty). data:URI и asset:ID не трогаются.

    Примеры:
      {"текстурировать_лотти": {"вход": "E:/robot.lotty",
        "правила": [
          {"шаблон": "Корпус", "текстура": "tex_metal.png",
           "прозрачность": 85, "режим": "cover"},
          {"шаблон": "Уши*", "текстура": "tex_dark.png"}]}}
      {"текстурировать_лотти": {"вход": "E:/s.lotty",
                                 "файл_правил": "E:/rules.txt"}}
    """
    src = args.get("вход") or args.get("путь") or args.get("src") or ""
    if not src:
        return "Укажите 'вход' — путь к .lotty."
    src_path = Path(src)
    if not src_path.exists():
        return "Нет файла: " + str(src_path)

    dst = args.get("выход") or args.get("сохранить") or args.get("dst") or ""
    dst_path = (Path(dst) if dst
                else src_path.with_suffix(src_path.suffix + ".tex.lotty"))

    rules_in = args.get("текстуры") or ""
    if not isinstance(rules_in, (str, list)):
        return "Параметр 'текстуры' должен быть строкой или списком строк."
    raw_list = []
    if isinstance(rules_in, str):
        raw_list = [x.strip() for x in rules_in.split(";") if x.strip()]
    else:
        for r in rules_in:
            if isinstance(r, str):
                for x in r.split(";"):
                    if x.strip():
                        raw_list.append(x.strip())

    specs = []
    for sub in raw_list:
        s = _build_spec(sub, src_path.parent)
        if not s:
            return "Ошибка: не разобрано правило: " + sub
        specs.append(s)

    rules_file = (args.get("файл_правил") or args.get("файл_текстур")
                  or args.get("rules_file") or "")
    if rules_file:
        rf = Path(rules_file)
        if not rf.is_absolute():
            rf = (src_path.parent / rf).resolve()
        rules_file = str(rf)

    if not specs and not rules_file:
        return "Нужны 'правила' или 'файл_правил'."

    if not _APPLY_TEXTURES.exists():
        return "Не найден apply_textures.py: " + str(_APPLY_TEXTURES)

    cmd = [sys.executable, str(_APPLY_TEXTURES),
           str(src_path), str(dst_path)]
    for s in specs:
        cmd += ["--texture", s]
    if rules_file:
        cmd += ["--texture-file", rules_file]

    try:
        r = _run_py(cmd, int(args.get("таймаут") or 120))
    except subprocess.TimeoutExpired:
        return "Таймаут (" + str(args.get("таймаут") or 120) + " сек)"
    except Exception as e:
        return "Ошибка запуска: " + str(e)

    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        return ("apply_textures.py упал (rc=" + str(r.returncode) + ")\n"
                + out + "\n" + err).strip()
    return out or ("Готово: " + str(dst_path))


@register(["конвертировать_лотти", "сделать_лотти", "лотти_из"])
def convert_lottie(args):
    """Конвертировать SVG или HTML в .lotty.

    Параметры:
      вход    — .svg или .html (обязательно)
      выход   — .lotty (по умолч. <вход>.lotty)
      ширина  — canvas width для HTML-режима
      высота  — canvas height для HTML-режима
      таймаут — секунд (по умолч. 120)

    Режим (svg или html) определяется по содержимому файла.
    Для HTML желательно задать ширину/высоту — иначе берётся
    значение по умолчанию из скрипта.

    Примеры:
      {"конвертировать_лотти": {"вход": "E:/art.svg",
                                 "выход": "E:/art.lotty"}}
      {"конвертировать_лотти": {"вход": "E:/scene.html",
                                 "ширина": 1080, "высота": 1080}}
    """
    src = args.get("вход") or args.get("путь") or args.get("src") or ""
    if not src:
        return "Укажите 'вход' — путь к .svg или .html."
    src_path = Path(src)
    if not src_path.exists():
        return "Нет файла: " + str(src_path)

    dst = args.get("выход") or args.get("сохранить") or args.get("dst") or ""
    dst_path = Path(dst) if dst else src_path.with_suffix(".lotty")

    if not _SVG2LOTTY.exists():
        return "Не найден svg2lottie_v6.py: " + str(_SVG2LOTTY)

    w = args.get("ширина") or args.get("width") or args.get("canvas_w")
    h = args.get("высота") or args.get("height") or args.get("canvas_h")

    import json as _json
    _call = [_json.dumps(str(src_path)), _json.dumps(str(dst_path))]
    if w:
        _call.append(str(int(w)))
    if h:
        if not w:
            _call.append("None")
        _call.append(str(int(h)))

    code = (
        "import sys; "
        "sys.path.insert(0, " + _json.dumps(str(_SVG2LOTTY.parent)) + "); "
        "from svg2lottie_v6 import main; "
        "main(" + ", ".join(_call) + ")"
    )
    try:
        r = _run_py([sys.executable, "-c", code],
                    int(args.get("таймаут") or 120))
    except subprocess.TimeoutExpired:
        return "Таймаут (" + str(args.get("таймаут") or 120) + " сек)"
    except Exception as e:
        return "Ошибка запуска: " + str(e)
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        return ("svg2lottie_v6.py упал (rc=" + str(r.returncode) + ")\n"
                + out + "\n" + err).strip()
    if not dst_path.exists():
        return "Скрипт завершился, но " + str(dst_path) + " не создан.\n" + out
    return ("Готово: " + str(dst_path) + "\n" + out).strip()


@register(["лотти_в_видео", "лотти_в_mp4", "отрендерить_лотти"])
def lottie_to_video(args):
    """Отрендерить .lotty в MP4 (или PNG-кадр для предпросмотра).

    Параметры:
      вход          — .lotty (обязательно)
      выход         — .mp4 (или .png при кадр: true)
      fps           — кадров в секунду
      ширина        — ширина кадра (px)
      высота        — высота кадра (px)
      масштаб       — множитель размера (по умолч. 1.0)
      фон           — цвет фона (#hex, по умолч. 0x05070f)
      качество      — crf (по умолч. 8, меньше = лучше)
      размытие      — blur-quality (по умолч. 80)
      кадры_маски   — сколько кадров сэмплировать для маски (0 = все)
      потоков_маски — потоков для растеризации (по умолч. 1)
      кэш_альфы     — размер кэша альфы (по умолч. 512)
      кэш_текстур   — размер кэша текстур (по умолч. 256)
      кадр          — true: только PNG-кадр (не MP4)
      номер_кадра   — какой кадр сохранить в режиме кадр (по умолч. 30)
      таймаут       — секунд (по умолч. 600)

    Примеры:
      {"лотти_в_видео": {"вход": "E:/art.lotty", "выход": "E:/art.mp4"}}
      {"лотти_в_видео": {"вход": "E:/art.lotty", "выход": "E:/art.mp4",
                          "fps": 30, "ширина": 1080, "высота": 1080}}
      {"лотти_в_видео": {"вход": "E:/art.lotty", "кадр": true,
                          "выход": "E:/preview.png", "номер_кадра": 45}}
    """
    src = args.get("вход") or args.get("путь") or args.get("src") or ""
    if not src:
        return "Укажите 'вход' — путь к .lotty."
    src_path = Path(src)
    if not src_path.exists():
        return "Нет файла: " + str(src_path)

    dst = args.get("выход") or args.get("сохранить") or args.get("dst") or ""
    if not dst:
        return "Укажите 'выход' — путь к .mp4 или .png."

    if not _RENDER.exists():
        return "Не найден render-lv6.py: " + str(_RENDER)

    is_frame = bool(args.get("кадр") or args.get("test"))
    base_out = str(dst)
    if is_frame and base_out.lower().endswith(".png"):
        base_out = base_out[:-4]

    cmd = [sys.executable, str(_RENDER),
           "--file", str(src_path), "--out", base_out]
    if is_frame:
        cmd.append("--test")
        nf = args.get("номер_кадра") or args.get("test_frame")
        if nf is not None and str(nf) != "":
            cmd += ["--test-frame", str(int(nf))]
        ts = args.get("масштаб_кадра") or args.get("test_scale")
        if ts is not None and str(ts) != "":
            cmd += ["--test-scale", str(ts)]

    def _add(flag, keys):
        for k in keys:
            v = args.get(k)
            if v is not None and str(v) != "":
                cmd.extend([flag, str(v)])
                return

    _add("--fps", ["fps"])
    _add("--width", ["ширина", "width"])
    _add("--height", ["высота", "height"])
    _add("--scale", ["масштаб", "scale"])
    _add("--bg", ["фон", "bg"])
    _add("--quality", ["качество", "quality"])
    _add("--blur-quality", ["размытие", "blur_quality"])
    _add("--mask-frames", ["кадры_маски", "mask_frames"])
    _add("--mask-workers", ["потоков_маски", "mask_workers"])
    _add("--alpha-cache-size", ["кэш_альфы", "alpha_cache_size"])
    _add("--texture-cache-size", ["кэш_текстур", "texture_cache_size"])
    if args.get("отладка") or args.get("debug"):
        cmd.append("--debug")

    try:
        r = _run_py(cmd, int(args.get("таймаут") or 600))
    except subprocess.TimeoutExpired:
        return "Таймаут (" + str(args.get("таймаут") or 600) + " сек)"
    except Exception as e:
        return "Ошибка запуска: " + str(e)
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        return ("render-lv6.py упал (rc=" + str(r.returncode) + ")\n"
                + out + "\n" + err).strip()
    expected = (base_out + ".png") if is_frame else base_out
    if not Path(expected).exists():
        return "Скрипт завершился, но " + expected + " не создан.\n" + out
    _show_debug = bool(args.get("отладка") or args.get("debug"))
    if _show_debug:
        return ("Готово: " + expected + "\n" + out).strip()
    
    _keep_prefixes = ("Ограничение", "оценка на", "⚠ Снижаю", "⚠ Уже", "⚠ Режу", "Обрезка", "fps ")
    _filtered = []
    for line in out.splitlines():
        _l = line.strip()
        if any(_l.startswith(p) for p in _keep_prefixes):
            _filtered.append(line)
    
    if _filtered:
        return "Готово: " + expected + "\n" + "\n".join(_filtered)
    return "Готово: " + expected


# Для AST-парсера anyjson: параметров у лотти_в_видео много,
# они передаются динамически через цикл _add(). Отключаем проверку опечаток.
lottie_to_video._any_params = True
