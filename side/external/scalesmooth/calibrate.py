"""Калибровка ScaleSmooth: время метода от площади входа и точности.

Запускать в обычном PowerShell, НЕ через {"питон"} — guard блокирует subprocess:
    cd E:\\Jericho\\Private\\anyjson
    py side\\external\\scalesmooth\\calibrate.py

Адаптивный режим: для каждой (метод, картинка) идёт вниз по лестнице
точности [100, 50, 25, 12, 6, 3, 1, 0] и останавливается на первой,
что уложилась в бюджет (по умолч. 30 с). Прогоны выше бюджета и таймауты
тоже пишутся — для интерполяции при авто-выборе.

Результат: side/external/scalesmooth/timings.json, мержится.
Повтор точки (method, px, acc, scale) без --force пропускается.
"""
import argparse, ast, json, os, sys, tempfile, time, subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE_DIR = HERE.parents[2]          # anyjson/
EXE = HERE / "ScaleSmooth.exe"
OUT_DEFAULT = HERE / "timings.json"


def _read_methods():
    src = (BASE_DIR / "_actions" / "image_upscale.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if getattr(t, "id", None) == "_METHOD_MAP":
                    return ast.literal_eval(node.value)
    raise RuntimeError("_METHOD_MAP не найден в _actions/image_upscale.py")


def _load(path):
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[!] битый {path}: {e}; начинаю с пустого")
    return {"meta": {}, "methods": {}}


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _find(lst, px, acc, scale):
    for e in lst:
        if e.get("px") == px and e.get("acc") == acc \
                and int(e.get("scale", scale)) == scale:
            return e
    return None


def _upsert(lst, entry, px, acc, scale):
    for i, e in enumerate(lst):
        if e.get("px") == px and e.get("acc") == acc \
                and int(e.get("scale", scale)) == scale:
            lst[i] = entry
            return
    lst.append(entry)


def _known_too_slow(lst, px, acc, scale):
    """True, если на меньшем px тот же (или больший) acc уже упал по таймауту."""
    for e in lst:
        if int(e.get("scale", scale)) != scale:
            continue
        if e.get("ok"):
            continue
        if "timeout" not in e.get("error", ""):
            continue
        if e["px"] < px and e["acc"] == acc:
            return True
    return False


def _make_noise(path, size, color=False):
    from PIL import Image
    if color:
        import os
        im = Image.frombytes("RGB", (size, size),
                             os.urandom(size * size * 3))
    else:
        im = Image.effect_noise((size, size), 50).convert("RGB")
    im.save(path)


def main():
    ap = argparse.ArgumentParser(
        description="Калибровка ScaleSmooth (время метода от px и acc)")
    ap.add_argument("--sizes", nargs="+", type=int, default=[512, 1024, 2048],
                    help="стороны синтетических квадратов, по умолч. 512 1024 2048")
    ap.add_argument("--ladder", default="100,50,25,12,6,3,1,0",
                    help="лестница точности сверху вниз (по умолч. 100,50,25,12,6,3,1,0)")
    ap.add_argument("--budget", type=int, default=30,
                    help="целевой бюджет одного апскейла, сек (по умолч. 30)")
    ap.add_argument("--scale", type=int, default=2, help="масштаб (по умолч. 2)")
    ap.add_argument("--methods", nargs="+", default=["all"],
                    help="методы или 'all' (по умолч. все 30)")
    ap.add_argument("--timeout", type=int, default=300,
                    help="жёсткий потолок на прогон, сек (по умолч. 300)")
    ap.add_argument("--force", action="store_true",
                    help="перезамерить уже записанные точки")
    ap.add_argument("--keep", action="store_true",
                    help="не удалять временную папку с синтетикой")
    ap.add_argument("--over-budget", action="store_true",
                    help="не резать прогон по бюджету+10 — мерить реальное время "
                         "(когда acc=0 уже не укладывается)")
    ap.add_argument("--out", default=str(OUT_DEFAULT), help="куда писать timings.json")
    ap.add_argument("--images", nargs="+", default=None,
                    help="свои картинки вместо синтетики")
    args = ap.parse_args()

    if not EXE.is_file():
        print(f"[!] нет {EXE}")
        return 2
    method_map = _read_methods()
    all_methods = list(method_map)
    if args.methods == ["all"]:
        methods = all_methods
    else:
        methods = [m for m in args.methods if m in all_methods]
        bad = [m for m in args.methods if m not in all_methods]
        if bad:
            print(f"[!] неизвестные методы: {', '.join(bad)}")

    try:
        ladder = [int(x.strip()) for x in args.ladder.split(",") if x.strip() != ""]
    except ValueError:
        print("[!] --ladder: ожидаю список чисел через запятую")
        return 2
    if not ladder:
        print("[!] --ladder пуст")
        return 2
    ladder = sorted(set(ladder), reverse=True)
    if ladder[-1] != 0:
        ladder = ladder + [0]

    out_path = Path(args.out)
    data = _load(out_path)

    use_color = bool(methods) and all("color" in m.lower() for m in methods)
    if use_color:
        print("[i] цветной шум (все методы — color-варианты)")

    tmpdir = None
    if args.images:
        images = [Path(p) for p in args.images]
        for p in images:
            if not p.is_file():
                print(f"[!] нет файла {p}")
                return 2
    else:
        tmpdir = Path(tempfile.mkdtemp(prefix="scalesmooth_cal_"))
        images = []
        for s in args.sizes:
            p = tmpdir / f"noise_{s}.png"
            _make_noise(p, s, color=use_color)
            images.append(p)
        print(f"[i] синтетика: {tmpdir}")

    from PIL import Image as _PIL
    px_of = []
    for p in images:
        with _PIL.open(p) as im:
            px_of.append(im.size[0] * im.size[1])

    run_timeout = (max(5, args.timeout) if args.over_budget
                   else min(args.timeout, max(5, args.budget + 10)))
    print(f"[i] методы: {len(methods)}")
    print(f"[i] картинки: {[(p.name, px) for p, px in zip(images, px_of)]}")
    print(f"[i] лестница: {ladder}, бюджет: {args.budget}s, "
          f"масштаб: {args.scale}, потолок: {run_timeout}s")

    data.setdefault("meta", {})
    data["meta"].update({
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "host": os.environ.get("COMPUTERNAME", "?"),
        "scale": args.scale,
        "sizes": args.sizes,
        "ladder": ladder,
        "budget": args.budget,
    })
    data.setdefault("methods", {})

    if args.over_budget:
        run_timeout = max(5, args.timeout)
    else:
        run_timeout = min(args.timeout, max(5, args.budget + 10))
    t_all0 = time.perf_counter()
    n_runs = 0

    for method in methods:
        lst = data["methods"].setdefault(method, [])
        for img, px in zip(images, px_of):
            found_acc = None
            for acc in ladder:
                existing = _find(lst, px, acc, args.scale)
                if existing and not args.force:
                    if existing.get("ok") and existing.get("sec", 1e9) <= args.budget:
                        print(f"  [found]  {method:24s} {px:>9}px "
                              f"acc={acc:3d}  prev={existing['sec']:.2f}s")
                        found_acc = acc
                        break
                    prev = existing.get("sec")
                    ok = existing.get("ok")
                    print(f"  [skip]   {method:24s} {px:>9}px "
                          f"acc={acc:3d}  prev={prev}s ok={ok}")
                    continue

                if _known_too_slow(lst, px, acc, args.scale):
                    entry = {"px": px, "acc": acc, "scale": args.scale,
                             "sec": 0.0, "ok": False,
                             "error": "inferred: timeout at smaller px"}
                    _upsert(lst, entry, px, acc, args.scale)
                    _save(out_path, data)
                    print(f"  [infer]  {method:24s} {px:>9}px acc={acc:3d} "
                          f"x{args.scale}  (skip: smaller px timed out)",
                          flush=True)
                    continue

                if tmpdir is not None:
                    out_img = tmpdir / f"out_{method}_{px}_{acc}_{args.scale}.png"
                else:
                    out_img = (Path(tempfile.gettempdir())
                               / f"ss_{os.getpid()}_{method}_{px}_{acc}_{args.scale}.png")
                cmd = [str(EXE), str(img), str(out_img),
                       str(args.scale), method_map[method], str(acc)]
                t0 = time.perf_counter()
                ok = False
                err = ""
                _out_size = None
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace",
                                       timeout=run_timeout)
                    ok = (r.returncode == 0 and out_img.is_file())
                    tail = ((r.stdout or "") + (r.stderr or ""))[-300:]
                    if not ok:
                        err = (f"code {r.returncode}: {tail}"
                               if r.returncode else f"no-file: {tail}")
                    else:
                        # exe у BA-методов на fast-path может вернуть
                        # Bitmap 1x1 вместо картинки (acc=100/25 на
                        # 140k-4M). Файл есть, returncode 0 — но битый.
                        try:
                            with _PIL.open(img) as _sim:
                                _sw, _sh = _sim.size
                            _exp_w = _sw * args.scale
                            _exp_h = _sh * args.scale
                            with _PIL.open(out_img) as _oim:
                                _ow, _oh = _oim.size
                            _out_size = [_ow, _oh]
                            if (_ow, _oh) != (_exp_w, _exp_h):
                                ok = False
                                err = ("bad-size " + str(_ow) + "x"
                                       + str(_oh) + " (expected "
                                       + str(_exp_w) + "x" + str(_exp_h) + ")")
                        except Exception as _e:
                            ok = False
                            err = ("bad-open: " + type(_e).__name__
                                   + ": " + str(_e)[:150])
                except subprocess.TimeoutExpired:
                    err = f"timeout {run_timeout}s"
                except FileNotFoundError:
                    err = "exe not found"
                dt = time.perf_counter() - t0
                entry = {"px": px, "acc": acc, "scale": args.scale,
                         "sec": round(dt, 3), "ok": bool(ok)}
                if _out_size:
                    entry["out_size"] = _out_size
                if not ok:
                    entry["error"] = err
                _upsert(lst, entry, px, acc, args.scale)
                _save(out_path, data)
                n_runs += 1
                status = "ok" if ok else "FAIL"
                print(f"  {method:24s} {px:>9}px acc={acc:3d} x{args.scale}  "
                      f"{dt:7.2f}s {status}"
                      + ("" if ok else f"  ({err})"), flush=True)
                try:
                    if out_img.is_file():
                        out_img.unlink()
                except OSError:
                    pass
                if ok and dt <= args.budget:
                    found_acc = acc
                    break
            if found_acc is None:
                print(f"  [!] {method} {px}px: даже acc={ladder[-1]} "
                      f"не влез в {args.budget}s (или таймаут)")

    dt_all = time.perf_counter() - t_all0
    print(f"\n[i] готово. Замеров за запуск: {n_runs}, "
          f"время {dt_all/60:.1f} мин")
    print(f"[i] файл: {out_path} ({out_path.stat().st_size} Б)")

    if tmpdir is not None and not args.keep:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    elif tmpdir is not None:
        print(f"[i] временная папка оставлена: {tmpdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())