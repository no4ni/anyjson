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

    out_dir_arg = args.get("папка") or args.get("dir")
    if out_dir_arg:
        out_dir = Path(str(out_dir_arg)).expanduser()
    else:
        from _config import MY_DIR
        out_dir = MY_DIR / "mesh_3d"
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = str(args.get("имя") or args.get("name") or top_path.stem).strip() or "model_3d"
    stamp = time.strftime("%H%M%S")
    preview_path = out_dir / f"{stem}_{stamp}_iso.png"
    glb_path = out_dir / f"{stem}_{stamp}.glb"

    try:
        from _actions.tools_mesh_3d import _build_solid_voxel, _render_iso_preview, _export_lowpoly_glb
        solid = _build_solid_voxel(top_path, front_path, left_path, depth_scale=depth_scale)
        _render_iso_preview(solid, preview_path)
        faces_cnt = _export_lowpoly_glb(solid, glb_path, target_faces=target_faces, smooth_sigma=smooth_sigma)
    except Exception as e:
        return f"❌ создать_3д: {e}"

    return f"✅ 3D-модель готова ({faces_cnt:,} треугольников):\nПревью: {preview_path}\nМодель: {glb_path}"

