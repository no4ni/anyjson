#!/usr/bin/env python3
"""apply_textures.py — добавляет texture/texture_opacity/texture_mode
в слои .lotty по шаблонам имён.

Правила передаются через --texture (можно повторять):
    --texture "Корпус=tex_metal_light.png:85:cover"
    --texture "Уши *=tex_metal_dark.png:80:cover"

Формат:  ШАБЛОН=ТЕКСТУРА[:OPACITY[:MODE]]
  ШАБЛОН   — имя слоя, "prefix*", "*suffix" или "*substring*"
  ТЕКСТУРА — путь к файлу / data:URI / asset:ID
  OPACITY  — 0..100 (по умолч. 100)
  MODE     — tile | stretch | cover (по умолч. tile)

Также можно взять правила из файла (одна строка = одно правило):
    --texture-file rules.txt
"""
from __future__ import annotations
import argparse, pathlib, sys


TEXTURES_HELP = """apply_textures.py — наложение текстур на слои .lotty

ИСПОЛЬЗОВАНИЕ:
    apply_textures.py <src.lotty> [dst.lotty] [опции]
    apply_textures.py --help

ЧТО ДЕЛАЕТ:
    Читает .lotty, добавляет в каждый слой, чьё имя матчит шаблон,
    поля texture / texture_opacity / texture_mode. Сами пиксели текстуры
    НЕ вшиваются в .lotty — рендерер (render-lv6.py) читает их при рендере.

    Для каждого слоя ищется первое правило, чей шаблон совпал; поля
    перезаписываются. Слои без совпадения остаются как есть.

ПРАВИЛА ТЕКСТУРИРОВАНИЯ:
    --texture "ШАБЛОН=ТЕКСТУРА[:OPACITY[:MODE]]"
    --texture-file rules.txt

    ШАБЛОН   — glob-паттерн имени слоя. * и ? работают.
               Регистр учитывается. Примеры: "Корпус", "Руки*", "*Голова*"
    ТЕКСТУРА — путь к .png / .jpg. Относительно текущей папки.
               Можно повторять для разных слоёв.
    OPACITY  — 0..100, по умолчанию 100. Как сильно текстура перекрывает
               исходный цвет слоя: 0 = слой без изменений, 100 = текстура
               полностью заменяет.
    MODE     — "tile" | "stretch" | "cover" (по умолчанию "tile"):
                 tile     — повторять с сохранением размера (швы видны)
                 stretch  — растянуть на весь bbox фигуры
                 cover    — заполнить bbox с сохранением пропорций
                            (обрезка по краям)

РЕЖИМЫ В ДЕТАЛЯХ:
    tile     Хорош для кирпича, металла, воды, неба — повторяющихся
             текстур. Размер клетки = размер текстуры, повторяется
             от центра bbox.
    stretch  Хорош для градиентов (небо→земля), ржавчины, «размазанных»
             текстур. Может исказить пропорции, если bbox не квадратный.
    cover    Компромисс: сохраняет пропорции, но обрезает края.
             Хорош для фотографий, лиц, звёздного неба.

АЛГОРИТМ (что делает рендерер при применении):
    1. Для каждого слоя с texture: вычисляется bbox фигуры в локальных
       координатах (local_shape_bbox).
    2. Строится локальная текстура нужного размера в выбранном mode.
    3. Локальная текстура трансформируется в мировые координаты через
       ks слоя на текущем кадре (get_layer_transform).
    4. Готовый RGBA-блок текстуры смешивается с блоком по alpha слоя,
       умноженной на texture_opacity/100.

    Всё это кэшируется:
      • texture_cache хранит готовую world-текстуру по (id слоя, кадр,
        bbox, mode, w, h);
      • alpha_cache хранит альфа-канал слоя по (id слоя, кадр).

    Итоговый слой на кадре = mix(original_color, texture_rgb, texture_a)
    внутри bbox фигуры.

ПРИМЕРЫ:
    apply_textures.py robot.lotty robot_tex.lotty \\
        --texture "Корпус=tex_metal_light.png:85:cover" \\
        --texture "Уши*=tex_metal_dark.png:80:cover" \\
        --texture "Пол*=tex_rock.png:45:tile" \\
        --texture "Руки*=tex_chrome.png:80:cover"

    apply_textures.py scene.lotty scene_tex.lotty --texture-file rules.txt

ФОРМАТ rules.txt:
    # комментарий
    Корпус=tex_metal_light.png:85:cover
    Уши*=tex_metal_dark.png:80:cover
    Пол*=tex_rock.png:45:tile
    Руки*=tex_chrome.png:80:cover

ВЫХОД:
    .lotty — копия src с добавленными полями texture/texture_opacity/
             texture_mode у подходящих слоёв.

СМ. ТАКЖЕ:
    render-lv6.py --help  — как рендерер применяет эти поля
"""


def parse_args():
    p = argparse.ArgumentParser(
        prog="apply_textures.py",
        description="Наложение текстур на слои .lotty",
        epilog=TEXTURES_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    p.add_argument("--help", action="store_true",
                   help="показать полное описание формата правил")
    p.add_argument("src", nargs="?", default=None,
                   help="исходный .lotty")
    p.add_argument("dst", nargs="?", default=None,
                   help="куда сохранить (по умолч. <src>.tex.lotty)")
    p.add_argument("--texture", action="append", default=[],
                   metavar="ПАТТЕРН=ТЕКСТУРА[:OPACITY[:MODE]]",
                   help="правило текстурирования (можно повторять)")
    p.add_argument("--texture-file", default=None,
                   help="файл с правилами (по одному на строку)")
    a = p.parse_args()
    if a.help:
        print(TEXTURES_HELP)
        sys.exit(0)
    if not a.src:
        print("Ошибка: нужен исходный .lotty. --help для справки.")
        sys.exit(1)
    return a


def parse_rule(spec):
    """'Корпус=tex.png:85:cover' -> ('Корпус', 'tex.png', 85.0, 'cover')"""
    if "=" not in spec:
        return None
    pat, rest = spec.split("=", 1)
    
    # Сплит справа: позволяет использовать пути с двоеточиями (C:\...).
    # Если после сплита вторая часть не float, значит это не op/mode,
    # а часть пути или data:URI — тогда считаем весь rest путём.
    parts = rest.rsplit(":", 2)
    if len(parts) >= 2:
        try:
            float(parts[1])
        except ValueError:
            parts = [rest]
            
    tex = parts[0].strip()
    try:
        opacity = float(parts[1]) if len(parts) > 1 else 100.0
    except ValueError:
        opacity = 100.0
    mode = parts[2].strip() if len(parts) > 2 else "tile"
    return (pat.strip(), tex, opacity, mode)


def match(name, pattern):
    if pattern.startswith("*") and pattern.endswith("*"):
        return pattern[1:-1] in name
    if pattern.endswith("*"):
        return name.startswith(pattern[:-1])
    if pattern.startswith("*"):
        return name.endswith(pattern[1:])
    return name == pattern


def main():
    a = parse_args()
    src = pathlib.Path(a.src)
    if not src.exists():
        sys.exit(f"Нет файла: {src}")
    if a.dst is None:
        dst = src.with_suffix(src.suffix + ".tex.lotty")
    else:
        dst = pathlib.Path(a.dst)

    # Собираем правила (одно значение --texture может содержать несколько через ';')
    rules = []
    for chunk in a.texture:
        for spec in chunk.split(";"):
            spec = spec.strip()
            if not spec: continue
            r = parse_rule(spec)
            if r is None:
                print(f"  ⚠ пропущено (нет '='): {spec}")
                continue
            rules.append(r)
    if a.texture_file:
        for line in pathlib.Path(a.texture_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"): continue
            r = parse_rule(line)
            if r is None:
                print(f"  ⚠ пропущено (нет '='): {line}")
                continue
            rules.append(r)

    if not rules:
        sys.exit("Нет правил. Используйте --texture или --rule-file.")

    doc = json_loads(src)
    n_applied = 0
    matched_layers = {}
    for L in doc.get("layers", []):
        nm = L.get("nm", "")
        for pat, tex, op, mode in rules:
            if match(nm, pat):
                L["texture"] = tex
                L["texture_opacity"] = op
                L["texture_mode"] = mode
                n_applied += 1
                matched_layers.setdefault(nm, []).append((pat, tex, op, mode))
                break

    dst.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    print(f"OK: {n_applied} слоёв текстурированы -> {dst}")
    for nm, ms in sorted(matched_layers.items()):
        pat, tex, op, mode = ms[0]
        print(f"      {nm[:35]:35s} <- {tex} ({op}%, {mode})")

    # какие правила не сработали
    unused = [r[0] for r in rules if not any(
        r[0] in ms[0] for ms in matched_layers.values())]
    if unused:
        print(f"    не использованы: {unused}")


def json_loads(path):
    import json
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


import json


if __name__ == "__main__":
    main()
