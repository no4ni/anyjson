"""Построение 3D-модели (.glb) и изометрического предпросмотра (.png) из трёх проекций."""
from pathlib import Path
import time

from _actions import register


@register(["создать_3д", "сделать_3д"])
def create_3d_model(args):
    """Построить 3D-модель (.glb) и изометрическое превью (.png) из трёх проекций (.png/.jpg/.svg).

    Параметры:
      верх        — проекция сверху (алиас: top)
      перед       — проекция спереди (алиасы: фронт, front)
      бок         — проекция сбоку (алиасы: лево, left)
      картинки    — альтернатива: список [верх, перед, бок]
      глубина     — масштаб выреза для серых градиентов (по умолч. 0.35, алиас: depth_scale)
      полигоны    — целевое число треугольников low-poly (по умолч. 3000, алиас: faces)
      сглаживание — сигма сглаживания (по умолч. 0.8, алиас: smooth)
      имя         — базовое имя выходных файлов (по умолч. из имени первой картинки)
      папка       — папка для сохранения (по умолч. MY_DIR/mesh_3d)
    """
    if not isinstance(args, dict):
        return 'Укажи три картинки: {"создать_3д": {"верх": "...", "перед": "...", "бок": "..."}}'

    imgs = args.get("картинки") or args.get("проекции") or args.get("images")
    if isinstance(imgs, (list, tuple)) and len(imgs) == 3:
        top_arg, front_arg, left_arg = imgs[0], imgs[1], imgs[2]
    else:
        top_arg = args.get("верх") or args.get("top")
        front_arg = args.get("перед") or args.get("фронт") or args.get("front")
        left_arg = args.get("бок") or args.get("лево") or args.get("left")

    if not (top_arg and front_arg and left_arg):
        return "❌ Нужны три проекции: верх (top), перед (front) и бок (left)."

    try:
        depth_scale = float(args.get("глубина") if args.get("глубина") is not None else args.get("depth_scale", 0.35))
        target_faces = int(args.get("полигоны") if args.get("полигоны") is not None else args.get("faces", 3000))
        smooth_sigma = float(args.get("сглаживание") if args.get("сглаживание") is not None else args.get("smooth", 0.8))
    except (TypeError, ValueError) as e:
        return f"❌ Неверный числовой параметр: {e}"

    top_path = Path(str(top_arg)).expanduser()
    front_path = Path(str(front_arg)).expanduser()
    left_path = Path(str(left_arg)).expanduser()

    out_dir_arg = args.get("папка_выхода") or args.get("куда") or args.get("out_dir") or args.get("папка")
    if out_dir_arg:
        cand = Path(str(out_dir_arg)).expanduser()
        if cand.is_file() or cand.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".svg", ".glb"):
            from _config import MY_DIR
            out_dir = MY_DIR / "mesh_3d"
        else:
            out_dir = cand
    else:
        from _config import MY_DIR
        out_dir = MY_DIR / "mesh_3d"
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = str(args.get("имя") or args.get("name") or top_path.stem).strip() or "model_3d"
    stamp = time.strftime("%H%M%S")
    preview_path = out_dir / f"{stem}_{stamp}_iso.png"
    glb_path = out_dir / f"{stem}_{stamp}.glb"

    try:
        import sys
        from _config import PUBLIC_TOOLS_DIR
        if str(PUBLIC_TOOLS_DIR) not in sys.path:
            sys.path.insert(0, str(PUBLIC_TOOLS_DIR))
        from tools_mesh_3d import _build_solid_voxel, _render_iso_preview, _export_lowpoly_glb
        solid = _build_solid_voxel(top_path, front_path, left_path, depth_scale=depth_scale)
        _render_iso_preview(solid, preview_path)
        faces_cnt = _export_lowpoly_glb(solid, glb_path, target_faces=target_faces, smooth_sigma=smooth_sigma)
    except Exception as e:
        return f"❌ создать_3д: {e}"

    return f"✅ 3D-модель готова ({faces_cnt:,} треугольников):\nПревью: {preview_path}\nМодель: {glb_path}"



@register(["фото_в_3д", "картинка_в_3д", "photo_to_3d", "img_to_3d"])
def photo_to_3d_model(args):
    """Построить цветную рельефную 3D-модель (.glb) и превью (.png) по одной картинке через MiDaS ONNX.

    Параметры:
      путь        — путь к изображению (.png/.jpg/.webp/.svg) (алиасы: фото, картинка, image)
      глубина     — глубина рельефа в долях ширины кадра (по умолч. 0.6, алиас: depth_scale)
      ближние     — процент самых ближних точек (отсекает дальний фон; по умолч. 45, алиас: near_pct)
      гамма       — контраст рельефа (по умолч. auto: сдвигает медиану ровно в (min+max)/2, либо число; алиас: gamma)
      полигоны    — целевое число граней (по умолч. 65000, алиас: faces)
      сглаживание — сигма сглаживания карты глубины (по умолч. 0.8, алиас: smooth)
      имя         — базовое имя выходных файлов (по умолч. из имени картинки)
      папка       — папка для сохранения (по умолч. MY_DIR/mesh_3d)
      модель      — путь к midas .onnx (по умолч. из config.json -> midas_onnx)
    """
    if isinstance(args, str):
        args = {"путь": args}
    if not isinstance(args, dict):
        return 'Укажи картинку: {"фото_в_3д": {"путь": "E:/pic.png"}}'

    img_arg = args.get("путь") or args.get("фото") or args.get("картинка") or args.get("image") or args.get("файл")
    if not img_arg:
        return "❌ Укажи путь к картинке: путь / фото / картинка."

    try:
        depth_scale = float(args.get("глубина") if args.get("глубина") is not None else args.get("depth_scale", 0.6))
        near_pct = float(args.get("ближние") if args.get("ближние") is not None else args.get("near_pct", 45.0))
        raw_gamma = args.get("гамма") if args.get("гамма") is not None else args.get("gamma")
        gamma = None if (raw_gamma is None or str(raw_gamma).lower() in ("auto", "авто", "")) else float(raw_gamma)
        target_faces = int(args.get("полигоны") if args.get("полигоны") is not None else args.get("faces", 65000))
        smooth_sigma = float(args.get("сглаживание") if args.get("сглаживание") is not None else args.get("smooth", 0.8))
    except (TypeError, ValueError) as e:
        return f"❌ Неверный числовой параметр: {e}"

    img_path = Path(str(img_arg)).expanduser()
    out_dir_arg = args.get("папка_выхода") or args.get("куда") or args.get("out_dir") or args.get("папка")
    if out_dir_arg:
        cand = Path(str(out_dir_arg)).expanduser()
        if cand.is_file() or cand.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".svg", ".glb"):
            from _config import MY_DIR
            out_dir = MY_DIR / "mesh_3d"
        else:
            out_dir = cand
    else:
        from _config import MY_DIR
        out_dir = MY_DIR / "mesh_3d"
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = str(args.get("имя") or args.get("name") or img_path.stem).strip() or "photo_3d"
    stamp = time.strftime("%H%M%S")
    preview_path = out_dir / f"{stem}_{stamp}_iso.png"
    glb_path = out_dir / f"{stem}_{stamp}.glb"

    try:
        import _config as _cfg, json as _json
        model_arg = args.get("модель") or args.get("model")
        cand_model = Path(str(model_arg)).expanduser() if model_arg else Path("")
        if not str(cand_model) or str(cand_model) == "." or not cand_model.is_file():
            _disk_cfg = _json.loads(_cfg.CONFIG_PATH.read_text(encoding="utf-8")) if _cfg.CONFIG_PATH.exists() else {}
            raw_cfg_val = (_disk_cfg.get("paths") or {}).get("midas_onnx", "")
            if raw_cfg_val:
                cand_model = _cfg.resolve_path(raw_cfg_val)
        if not str(cand_model) or str(cand_model) == "." or not cand_model.is_file():
            cand_model = Path(__file__).resolve().parents[2] / "models" / "midas_v21_small_256.onnx"
        model_path = cand_model

        import importlib, side.tools_mesh_3d as _tm3d
        importlib.reload(_tm3d)
        faces_cnt = _tm3d._photo_to_3d_glb(
            img_path,
            model_path,
            glb_path,
            preview_path,
            depth_scale=depth_scale,
            target_faces=target_faces,
            smooth_sigma=smooth_sigma,
            near_pct=near_pct,
            gamma=gamma,
        )
    except Exception as e:
        return f"❌ фото_в_3д: {e}"

    return f"✅ 3D-модель по фото готова ({faces_cnt:,} треугольников):\nПревью: {preview_path}\nМодель: {glb_path}"