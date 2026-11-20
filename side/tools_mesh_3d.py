"""Построение 3D-модели (.glb) и изометрического превью (.png) из трёх проекций."""
import io
from pathlib import Path
import time

from _actions import register

_N = 256
_BLACK_THRESH = 15
_WHITE_THRESH = 235


def _open_projection_rgb(path: Path, size: int = _N):
    """Открыть проекцию (.png, .jpg, .svg) как RGB-изображение size×size на белом фоне."""
    from PIL import Image
    if not path.is_file():
        raise FileNotFoundError(f"Файл не найден: {path}")

    if path.suffix.lower() == ".svg":
        try:
            import resvg_py
            png_bytes = resvg_py.svg_to_bytes(svg_path=str(path), width=size, height=size)
            rgba = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        except ImportError:
            from svglib.svglib import svg2rlg
            from reportlab.graphics import renderPM
            drawing = svg2rlg(str(path))
            if drawing is None:
                raise ValueError(f"Не удалось прочитать SVG: {path}")
            if drawing.width and drawing.height:
                drawing.scale(size / drawing.width, size / drawing.height)
                drawing.width = size
                drawing.height = size
            rgba = renderPM.drawToPIL(drawing, bg=0xFFFFFF).convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, rgba).convert("RGB")
    else:
        raw = Image.open(str(path))
        if raw.mode in ("RGBA", "LA") or ("transparency" in raw.info):
            rgba = raw.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            img = Image.alpha_composite(bg, rgba).convert("RGB")
        else:
            img = raw.convert("RGB")

    if img.size != (size, size):
        img = img.resize((size, size), Image.Resampling.LANCZOS)
    return img


def _load_cut_depth(path: Path, depth_scale: float):
    import numpy as np
    rgb_img = _open_projection_rgb(path, _N)
    rgb = np.array(rgb_img, dtype=np.int32)
    is_colored = (rgb.max(axis=2) - rgb.min(axis=2)) > 30

    arr = np.array(rgb_img.convert("L"), dtype=np.float32)
    cut = np.rint((arr / 255.0) * _N * depth_scale).astype(np.int32)
    cut[arr <= _BLACK_THRESH] = 0
    cut[(arr >= _WHITE_THRESH) | is_colored] = _N
    return np.clip(cut, 0, _N)


def _line_mask(cut_2d, from_max: bool = True):
    import numpy as np
    idx = np.arange(_N, dtype=np.int32)[None, None, :]
    if from_max:
        return idx < (_N - cut_2d[:, :, None])
    return idx >= cut_2d[:, :, None]


def _build_solid_voxel(top_path: Path, front_path: Path, left_path: Path, depth_scale: float):
    top_cut = _load_cut_depth(top_path, depth_scale)
    front_cut = _load_cut_depth(front_path, depth_scale)
    left_cut = _load_cut_depth(left_path, depth_scale)

    top_xyz = _line_mask(top_cut.T, from_max=True)
    front_xyz = _line_mask(front_cut.T[:, ::-1], from_max=True).transpose(0, 2, 1)
    left_xyz = _line_mask(left_cut.T[:, ::-1], from_max=True).transpose(2, 0, 1)
    return top_xyz & front_xyz & left_xyz


def _render_iso_preview(voxel, out_path: Path, azimuth: float = 45.0, elevation: float = 35.264):
    import numpy as np
    from PIL import Image

    az = float(np.deg2rad(azimuth))
    el = float(np.deg2rad(elevation))
    cam = np.array([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)], dtype=np.float32)
    cam /= np.linalg.norm(cam)

    up_world = np.array([0.0, 0.0, 1.0], dtype=np.float32)
    if abs(float(np.dot(cam, up_world))) > 0.999:
        up_world = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    right = np.cross(cam, up_world)
    right /= np.linalg.norm(right)
    up_img = np.cross(right, cam)

    xs, ys, zs = np.nonzero(voxel)
    if len(xs) == 0:
        raise RuntimeError("Модель пуста (0 вокселей) — проверьте входные изображения")

    best_dot = np.full(len(xs), -1.0, dtype=np.float32)
    best_axis = np.full(len(xs), -1, dtype=np.int8)
    for axis in (2, 0, 1):
        for sgn in (1, -1):
            nb = np.roll(voxel, -sgn, axis=axis)
            if sgn > 0:
                nb[-1] = False
            else:
                nb[0] = False
            exposed = voxel & ~nb
            fd = float(abs(cam[axis])) if sgn * cam[axis] > 0 else 0.0
            m = exposed[xs, ys, zs] & (fd > best_dot + 1e-4)
            best_dot[m] = fd
            best_axis[m] = axis

    keep = best_axis >= 0
    xs, ys, zs = xs[keep], ys[keep], zs[keep]

    base_rgb = np.array([110, 90, 60], dtype=np.float32)
    rgb = np.broadcast_to(base_rgb, (len(xs), 3)).copy()

    px_f = (xs * right[0] + ys * right[1] + zs * right[2]).astype(np.float32)
    py_f = (xs * up_img[0] + ys * up_img[1] + zs * up_img[2]).astype(np.float32)
    depth = (xs * cam[0] + ys * cam[1] + zs * cam[2]).astype(np.float32)

    _d_min, _d_max = float(depth.min()), float(depth.max())
    _d_norm = (depth - _d_min) / max(_d_max - _d_min, 1e-6)
    _fog = (0.55 * (1.0 - _d_norm))[:, None].astype(np.float32)
    _bg = np.array([255, 255, 255], dtype=np.float32)
    rgb = (rgb * (1.0 - _fog) + _bg[None, :] * _fog).clip(0, 255).astype(np.uint8)

    SS, SCALE, SPLAT_R = 3, 3, 6
    px_f = (px_f - px_f.min()) * SCALE * SS
    py_f = (py_f - py_f.min()) * SCALE * SS
    py_f = py_f.max() - py_f

    px = np.round(px_f + SPLAT_R * 2).astype(np.int32)
    py = np.round(py_f + SPLAT_R * 2).astype(np.int32)
    W = int(px.max()) + SPLAT_R * 2 + 2
    H = int(py.max()) + SPLAT_R * 2 + 2

    d_off = np.array([(dx, dy) for dy in range(-SPLAT_R, SPLAT_R + 1) for dx in range(-SPLAT_R, SPLAT_R + 1)])
    n_off = len(d_off)
    all_px = (px[:, None] + d_off[None, :, 0]).ravel()
    all_py = (py[:, None] + d_off[None, :, 1]).ravel()
    all_depth = np.repeat(depth, n_off)
    all_rgb = np.repeat(rgb, n_off, axis=0)

    lin = all_py.astype(np.int64) * W + all_px.astype(np.int64)
    order = np.lexsort((-all_depth, lin))
    ls = lin[order]
    kp = np.ones(len(ls), dtype=bool)
    kp[1:] = ls[1:] != ls[:-1]
    idx = order[kp]

    img = np.full((H, W, 3), 240, dtype=np.uint8)
    img[all_py[idx], all_px[idx]] = all_rgb[idx]

    H_c, W_c = (H // SS) * SS, (W // SS) * SS
    blocks = img[:H_c, :W_c].reshape(H_c // SS, SS, W_c // SS, SS, 3)
    blocks = blocks.transpose(0, 2, 1, 3, 4).reshape(H_c // SS, W_c // SS, SS * SS, 3)
    brightness = blocks.astype(np.int32).sum(axis=3)
    is_bg = np.all(blocks >= 239, axis=3)
    brightness = np.where(is_bg, 10**6, brightness)
    idx = brightness.argmin(axis=2)
    h, w = idx.shape
    out = blocks[np.arange(h)[:, None], np.arange(w)[None, :], idx]
    Image.fromarray(out.astype(np.uint8)).save(str(out_path))


def _export_lowpoly_glb(solid_voxel, glb_path: Path, target_faces: int = 3000, smooth_sigma: float = 0.8) -> int:
    import numpy as np
    from scipy import ndimage
    from skimage import measure
    import trimesh

    padded = np.pad(solid_voxel, 1, mode="constant", constant_values=False).astype(np.float32)
    if smooth_sigma > 0:
        padded = ndimage.gaussian_filter(padded, sigma=smooth_sigma)

    verts, faces, normals, _ = measure.marching_cubes(padded, level=0.5)
    verts -= 1.0

    scale = float(max(solid_voxel.shape))
    verts[:, 0] = (verts[:, 0] - (solid_voxel.shape[0] - 1.0) / 2.0) / scale
    verts[:, 1] = (verts[:, 1] - (solid_voxel.shape[1] - 1.0) / 2.0) / scale
    verts[:, 2] = (verts[:, 2] - verts[:, 2].min()) / scale
    verts = np.column_stack([verts[:, 0], verts[:, 2], -verts[:, 1]])
    normals = np.column_stack([normals[:, 0], normals[:, 2], -normals[:, 1]])

    mesh = trimesh.Trimesh(vertices=verts, faces=faces, vertex_normals=normals, process=True)
    if target_faces > 0 and len(mesh.faces) > target_faces:
        mesh = mesh.simplify_quadric_decimation(face_count=target_faces)
    mesh.fix_normals()
    mesh.export(str(glb_path))
    return len(mesh.faces)





def _photo_to_3d_glb(image_path: Path, model_path: Path, glb_path: Path, preview_path: Path, depth_scale: float = 0.6, target_faces: int = 65000, smooth_sigma: float = 0.8, near_pct: float = 45.0, gamma=None) -> int:
    """Построить цветной рельефный .glb и изометрическое превью по одной картинке через MiDaS ONNX."""
    import numpy as np
    from PIL import Image, ImageDraw
    from scipy import ndimage
    import onnxruntime as ort
    import trimesh

    if not image_path.is_file():
        raise FileNotFoundError(f"Картинка не найдена: {image_path}")
    if not model_path or not Path(model_path).is_file():
        raise FileNotFoundError(f"ONNX-модель MiDaS не найдена: {model_path}")

    rgb_img = _open_projection_rgb(image_path, _N)
    rgb_np = np.array(rgb_img, dtype=np.float32) / 255.0
    inp = rgb_np.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)

    avail = set(ort.get_available_providers())
    pref = [p for p in ("DmlExecutionProvider", "CPUExecutionProvider") if p in avail] or ["CPUExecutionProvider"]
    sess = None
    for prov in pref:
        try:
            sess = ort.InferenceSession(str(model_path), providers=[prov])
            break
        except Exception:
            pass
    if sess is None:
        sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])

    in_name = sess.get_inputs()[0].name
    raw_depth = np.asarray(sess.run(None, {in_name: inp})[0], dtype=np.float32).squeeze()
    if raw_depth.shape != (_N, _N):
        d_pil = Image.fromarray(raw_depth).resize((_N, _N), Image.Resampling.BILINEAR)
        raw_depth = np.array(d_pil, dtype=np.float32)

    if smooth_sigma > 0:
        raw_depth = ndimage.gaussian_filter(raw_depth, sigma=smooth_sigma)

    d_min, d_max = float(raw_depth.min()), float(raw_depth.max())
    depth_norm = (raw_depth - d_min) / (d_max - d_min) if (d_max - d_min) > 1e-6 else np.zeros_like(raw_depth)

    np_pct = float(np.clip(near_pct, 0.1, 100.0))
    if np_pct < 100.0:
        thr = float(np.percentile(depth_norm, 100.0 - np_pct))
        denom = max(1.0 - thr, 1e-6)
        depth_norm = np.clip((depth_norm - thr) / denom, 0.0, 1.0)
    if gamma is None or str(gamma).lower() in ("auto", "авто", "none", ""):
        pos = depth_norm[depth_norm > 1e-5]
        med = float(np.median(pos)) if pos.size > 0 else 0.5
        med = float(np.clip(med, 0.05, 0.95))
        g_val = float(np.clip(np.log(0.5) / np.log(med), 0.25, 4.0))
    else:
        g_val = max(float(gamma), 0.1)
    depth_norm = np.power(np.clip(depth_norm, 0.0, 1.0), g_val)

    step = 2 if (0 < target_faces <= 32000) else 1
    d_sub = depth_norm[::step, ::step]
    c_sub = np.array(rgb_img, dtype=np.uint8)[::step, ::step]
    H, W = d_sub.shape

    orig_pil = Image.open(str(image_path)).convert("RGB")
    aspect = float(orig_pil.width) / max(float(orig_pil.height), 1.0)
    xs = np.linspace(-0.5 * aspect, 0.5 * aspect, W, dtype=np.float32)
    ys = np.linspace(0.5, -0.5, H, dtype=np.float32)
    gx, gy = np.meshgrid(xs, ys)
    gz = d_sub * float(depth_scale)

    front_verts = np.column_stack([gx.ravel(), gy.ravel(), gz.ravel()])
    back_verts = np.column_stack([gx.ravel(), gy.ravel(), np.full(H * W, -0.02, dtype=np.float32)])
    verts = np.vstack([front_verts, back_verts])
    u_grid = np.linspace(0.0, 1.0, W, dtype=np.float32)
    v_grid = np.linspace(1.0, 0.0, H, dtype=np.float32)
    gu, gv = np.meshgrid(u_grid, v_grid)
    front_uv = np.column_stack([gu.ravel(), gv.ravel()])
    back_uv = np.zeros((H * W, 2), dtype=np.float32)
    v_uv = np.vstack([front_uv, back_uv])

    fg_pt = d_sub > 1e-5
    if not np.any(fg_pt):
        fg_pt = np.ones_like(d_sub, dtype=bool)

    cell_mask = fg_pt[:-1, :-1] & fg_pt[:-1, 1:] & fg_pt[1:, :-1] & fg_pt[1:, 1:]
    if not np.any(cell_mask):
        cell_mask = np.ones((H - 1, W - 1), dtype=bool)

    idx = np.arange(H * W, dtype=np.int32).reshape(H, W)
    cm_flat = cell_mask.ravel()
    v00 = idx[:-1, :-1].ravel()[cm_flat]
    v01 = idx[:-1, 1:].ravel()[cm_flat]
    v10 = idx[1:, :-1].ravel()[cm_flat]
    v11 = idx[1:, 1:].ravel()[cm_flat]
    f_front = np.vstack([np.column_stack([v00, v10, v11]), np.column_stack([v00, v11, v01])])

    off = H * W
    b00, b01, b10, b11 = v00 + off, v01 + off, v10 + off, v11 + off
    f_back = np.vstack([np.column_stack([b00, b11, b10]), np.column_stack([b00, b01, b11])])

    # Строим замкнутые боковые стенки строго по внешнему контуру вырезанной маски
    side_faces = []
    pad_c = np.pad(cell_mask, 1, mode="constant", constant_values=False)
    top_b = cell_mask & (~pad_c[:-2, 1:-1])
    bot_b = cell_mask & (~pad_c[2:, 1:-1])
    left_b = cell_mask & (~pad_c[1:-1, :-2])
    right_b = cell_mask & (~pad_c[1:-1, 2:])

    for mask_b, e0_grid, e1_grid in (
        (top_b, idx[:-1, :-1], idx[:-1, 1:]),
        (bot_b, idx[1:, 1:], idx[1:, :-1]),
        (left_b, idx[1:, :-1], idx[:-1, :-1]),
        (right_b, idx[:-1, 1:], idx[1:, 1:]),
    ):
        m_flat = mask_b.ravel()
        if np.any(m_flat):
            e0, e1 = e0_grid.ravel()[m_flat], e1_grid.ravel()[m_flat]
            side_faces.append(np.column_stack([e0, e0 + off, e1 + off]))
            side_faces.append(np.column_stack([e0, e1 + off, e1]))

    faces = np.vstack([f_front, f_back] + side_faces)
    used_verts, inv_idx = np.unique(faces.ravel(), return_inverse=True)
    verts_clean = verts[used_verts]
    uv_clean = v_uv[used_verts]
    faces_clean = inv_idx.reshape(-1, 3)

    from trimesh.visual.texture import TextureVisuals
    from trimesh.visual.material import PBRMaterial
    mat = PBRMaterial(baseColorTexture=orig_pil, metallicFactor=0.0, roughnessFactor=0.85)
    vis = TextureVisuals(uv=uv_clean, material=mat)
    mesh = trimesh.Trimesh(vertices=verts_clean, faces=faces_clean, visual=vis, process=False)
    mesh.export(str(glb_path))

    # Рендерим изометрическое цветное превью только по вырезанным передним объектам
    az, el = np.radians(32.0), np.radians(24.0)
    rx = gx * np.cos(az) - gz * np.sin(az)
    rz = gx * np.sin(az) + gz * np.cos(az)
    ry = gy * np.cos(el) - rz * np.sin(el)
    z_ord = gy * np.sin(el) + rz * np.cos(el)

    S = 512
    px = np.clip(((rx - rx.min()) / max(float(rx.max() - rx.min()), 1e-6) * (S - 40) + 20).astype(np.int32), 0, S - 1)
    py = np.clip(((1.0 - (ry - ry.min()) / max(float(ry.max() - ry.min()), 1e-6)) * (S - 40) + 20).astype(np.int32), 0, S - 1)
    fg_flat = fg_pt.ravel()
    px_fg, py_fg, z_fg, col_fg = px.ravel()[fg_flat], py.ravel()[fg_flat], z_ord.ravel()[fg_flat], c_sub.reshape(-1, 3)[fg_flat]
    order = np.argsort(z_fg)
    canvas = np.full((S, S, 3), 242, dtype=np.uint8)
    px_f, py_f, col_f = px_fg[order], py_fg[order], col_fg[order]
    rad = 2 if step == 1 else 3
    for dy in range(-rad, rad + 1):
        for dx in range(-rad, rad + 1):
            yy = np.clip(py_f + dy, 0, S - 1)
            xx = np.clip(px_f + dx, 0, S - 1)
            canvas[yy, xx] = col_f
    Image.fromarray(canvas).save(str(preview_path))
    return len(faces)
