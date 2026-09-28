#!/usr/bin/env python3
"""CLI: кадр видео на середине субтитра + стрелки движения + буфер обмена.
Использование:
    python watchtogether_cli.py VIDEO [--номер N] [--стрелки] [--сохранить PATH] [--список]
"""
import argparse
import io
import os
import re
import platform
import subprocess
import sys
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
except Exception:
    pass
from shutil import which

import cv2
import numpy as np
import pysubs2
from PIL import Image, ImageDraw, ImageFont

try:
    import win32clipboard
    import win32con
    HAS_PYWIN32 = True
except ImportError:
    HAS_PYWIN32 = False


# ============================================================
#  Буфер обмена
# ============================================================
def copy_image_to_clipboard(pil_image):
    if pil_image is None:
        return False
    if platform.system() == "Windows" and HAS_PYWIN32:
        try:
            buf = io.BytesIO()
            pil_image.convert("RGB").save(buf, "BMP")
            data = buf.getvalue()[14:]
            buf.close()
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_DIB, data)
            win32clipboard.CloseClipboard()
            return True
        except Exception as e:
            print(f"[clip] pywin32 fail: {e}", file=sys.stderr)
    return False


# ============================================================
#  Субтитры
# ============================================================
def clean_subtitle_text(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ")
    text = text.replace("\\N", "\n").replace("\\n", "\n")
    return text


def subtitle_score(text):
    t = clean_subtitle_text(text).strip()
    if t == "":
        return 0
    if all(ch in "-—–_ \n" for ch in t):
        return 1
    return 2


def find_subtitle_file(video_path):
    base = os.path.splitext(video_path)[0]
    for ext in (".srt", ".SRT", ".vtt", ".VTT", ".webvtt", ".WEBVTT"):
        p = base + ext
        if os.path.exists(p):
            return p
    return None


def extract_subtitle_via_ffmpeg(video_path):
    if not which("ffmpeg"):
        return None
    tmp_srt = os.path.join(os.path.dirname(video_path), "_temp_subs.srt")
    cmd = ["ffmpeg", "-i", video_path, "-map", "0:s:0",
           "-c:s", "srt", "-y", tmp_srt]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return tmp_srt
    except Exception:
        return None


def load_subtitles(video_path):
    sub_path = find_subtitle_file(video_path) or extract_subtitle_via_ffmpeg(video_path)
    if not sub_path:
        return [], None
    try:
        subs = list(pysubs2.load(sub_path, encoding="utf-8-sig"))
    except Exception:
        try:
            subs = list(pysubs2.load(sub_path, encoding="utf-8-sig", format_="vtt"))
        except Exception:
            subs = []
    return subs, sub_path


def build_clusters(subtitles):
    meaningful = []
    for s in subtitles:
        if (s.end - s.start) < 100:
            continue
        text = clean_subtitle_text(s.text)
        text = re.sub(r"\s+", " ", text.replace("\\N", " ").replace("\n", " ")).strip()
        if subtitle_score(text) <= 1:
            continue
        meaningful.append((s.start, s.end, text))

    clusters = []
    for start_ms, end_ms, text in meaningful:
        if clusters:
            last = clusters[-1]
            last_start, last_end, last_text = last
            if last_text in text or text in last_text:
                new_text = text if len(text) >= len(last_text) else last_text
                clusters[-1] = [last_start, max(last_end, end_ms), new_text]
                continue
            if start_ms < last_end:
                clusters[-1] = [last_start, max(last_end, end_ms), last_text]
                continue
        clusters.append([start_ms, end_ms, text])
    return clusters


# ============================================================
#  Кадр + эффекты
# ============================================================
def read_frame_at(video_path, time_sec, target_height=None):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0 or fps > 100:
        fps = 25.0
    target_frame = int(round(time_sec * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return None
    if target_height is not None:
        h, w = frame.shape[:2]
        if h > target_height:
            new_w = max(1, int(round(w * target_height / h)))
            frame = cv2.resize(frame, (new_w, target_height),
                               interpolation=cv2.INTER_AREA)
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def log_blur_trail(video_path, time_sec, buffer_size=15, exp_factor=3,
                   scene_cut_threshold=35.0, target_height=480):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0 or fps > 100:
        fps = 25.0
    target_frame = int(round(time_sec * fps))
    start_frame = max(0, target_frame - buffer_size + 1)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    buffer_frames = []
    for _ in range(start_frame, target_frame + 1):
        ret, frame = cap.read()
        if not ret:
            break
        if target_height is not None:
            h, w = frame.shape[:2]
            if h > target_height:
                new_w = max(1, int(round(w * target_height / h)))
                frame = cv2.resize(frame, (new_w, target_height),
                                   interpolation=cv2.INTER_AREA)
        if buffer_frames:
            mean_diff = np.mean(np.abs(frame.astype(np.float32) - buffer_frames[-1]))
            if mean_diff > scene_cut_threshold:
                buffer_frames.clear()
        buffer_frames.append(frame.astype(np.float32))
    cap.release()

    if not buffer_frames:
        return None
    n = len(buffer_frames)
    if n < 2:
        return Image.fromarray(cv2.cvtColor(buffer_frames[0].astype(np.uint8),
                                             cv2.COLOR_BGR2RGB))

    weights = np.exp(np.arange(1, n + 1) / n * exp_factor).astype(np.float32)
    weights_sum = float(np.sum(weights))
    stack = np.stack(buffer_frames)
    blended = np.sum(stack * weights.reshape(-1, 1, 1, 1), axis=0) / weights_sum
    blended = blended.astype(np.uint8)

    last_frame = buffer_frames[-1].astype(np.uint8)
    diff = np.abs(last_frame.astype(np.float32) - blended.astype(np.float32))
    diff_intensity = np.mean(diff, axis=2)
    mask_motion = diff_intensity > 20
    blended_red = blended.copy()
    blended_red[:, :, 2] = np.where(
        mask_motion,
        np.clip(blended_red[:, :, 2] + diff_intensity, 0, 255),
        blended_red[:, :, 2])

    H, W = blended.shape[:2]
    yy, xx = np.indices((H, W))
    checker = (yy + xx) % 2 == 0
    final_bgr = np.where(checker[:, :, None], blended_red, last_frame)
    final_rgb = cv2.cvtColor(final_bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(final_rgb)


def feynman_filter(pil_img):
    img = np.array(pil_img).astype(np.float32)
    H, W, _ = img.shape
    if H < 3 or W < 3:
        return pil_img
    out = img.copy()

    up = img[0:H-2, 1:W-1]
    down = img[2:H, 1:W-1]
    left = img[1:H-1, 0:W-2]
    right = img[1:H-1, 2:W]

    mean = (up + down + left + right) / 4.0
    center = img[1:H-1, 1:W-1]

    d1 = np.sqrt(np.sum((center - mean) ** 2, axis=2))
    d_up = np.sqrt(np.sum((up - mean) ** 2, axis=2))
    d_down = np.sqrt(np.sum((down - mean) ** 2, axis=2))
    d_left = np.sqrt(np.sum((left - mean) ** 2, axis=2))
    d_right = np.sqrt(np.sum((right - mean) ** 2, axis=2))

    max_d = np.maximum(np.maximum(d_up, d_down), np.maximum(d_left, d_right))
    value = np.clip(d1 - max_d, 0, 255)

    out[1:H-1, 1:W-1, 0] = value
    out[1:H-1, 1:W-1, 1] = value
    out[1:H-1, 1:W-1, 2] = value
    return Image.fromarray(out.astype(np.uint8))


def monotonic_lengths_h(gray):
    H, W = gray.shape
    diff = np.diff(gray, axis=1)
    sign = np.sign(diff).astype(int)
    sign_padded = np.zeros((H, W), dtype=int)
    sign_padded[:, :-1] = sign

    left = np.zeros((H, W), dtype=int)
    right = np.zeros((H, W), dtype=int)

    for i in range(1, W):
        cur = sign_padded[:, i]
        prev = sign_padded[:, i - 1]
        mask = (cur != 0) & (cur == prev)
        left[:, i] = np.where(mask, left[:, i - 1] + 1, 0)
        mask_start = (cur != 0) & (prev == 0)
        left[:, i] = np.where(mask_start, 1, left[:, i])

    for i in range(W - 2, -1, -1):
        cur = sign_padded[:, i]
        nxt = sign_padded[:, i + 1]
        mask = (cur != 0) & (cur == nxt)
        right[:, i] = np.where(mask, right[:, i + 1] + 1, 0)
        mask_start = (cur != 0) & (nxt == 0)
        right[:, i] = np.where(mask_start, 1, right[:, i])

    length = left + right - 1
    length[length < 0] = 0
    length[sign_padded == 0] = 0
    return length


def monotonic_lengths_v(gray):
    H, W = gray.shape
    diff = np.diff(gray, axis=0)
    sign = np.sign(diff).astype(int)
    sign_padded = np.zeros((H, W), dtype=int)
    sign_padded[:-1, :] = sign

    up = np.zeros((H, W), dtype=int)
    down = np.zeros((H, W), dtype=int)

    for i in range(1, H):
        cur = sign_padded[i, :]
        prev = sign_padded[i - 1, :]
        mask = (cur != 0) & (cur == prev)
        up[i, :] = np.where(mask, up[i - 1, :] + 1, 0)
        mask_start = (cur != 0) & (prev == 0)
        up[i, :] = np.where(mask_start, 1, up[i, :])

    for i in range(H - 2, -1, -1):
        cur = sign_padded[i, :]
        nxt = sign_padded[i + 1, :]
        mask = (cur != 0) & (cur == nxt)
        down[i, :] = np.where(mask, down[i + 1, :] + 1, 0)
        mask_start = (cur != 0) & (nxt == 0)
        down[i, :] = np.where(mask_start, 1, down[i, :])

    length = up + down - 1
    length[length < 0] = 0
    length[sign_padded == 0] = 0
    return length


def monotonic_intensity(gray):
    return np.maximum(monotonic_lengths_h(gray), monotonic_lengths_v(gray))


def angle_to_color(angle_deg):
    angle = angle_deg % 360.0
    pts = [(0, (0, 0, 255)), (90, (0, 255, 0)), (180, (255, 0, 0)),
           (270, (255, 255, 255)), (360, (0, 0, 255))]
    for i in range(len(pts) - 1):
        a1, c1 = pts[i]
        a2, c2 = pts[i + 1]
        if a1 <= angle <= a2:
            t = (angle - a1) / (a2 - a1)
            return (int(c1[0] + (c2[0] - c1[0]) * t),
                    int(c1[1] + (c2[1] - c1[1]) * t),
                    int(c1[2] + (c2[2] - c1[2]) * t))
    return (0, 0, 255)


def compute_vectors(processed_img, vector_scale=1.0, vector_min_length=3.0, max_length=20.0):
    gray = np.array(processed_img)[:, :, 0].astype(np.float32)
    gray = cv2.blur(gray, (3, 3))
    min_gray = cv2.erode(gray, np.ones((3, 3), np.uint8))
    mask_dark = (min_gray < 20).astype(np.uint8) * 255

    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    gx[mask_dark > 0] = 0
    gy[mask_dark > 0] = 0

    mag = np.sqrt(gx ** 2 + gy ** 2)
    _, mask = cv2.threshold(mag.astype(np.uint8), 15, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    mono_len = monotonic_intensity(gray).astype(np.float32)
    mono_len[mask_dark > 0] = 0

    points_list = []
    for i in range(1, num_labels):
        if stats[i, cv2.CC_STAT_AREA] <= 20:
            continue
        cx, cy = int(centroids[i][0]), int(centroids[i][1])
        vx = gx[cy, cx]
        vy = gy[cy, cx]
        if vx == 0 and vy == 0:
            vx = np.mean(gx[labels == i])
            vy = np.mean(gy[labels == i])
        v_mag = np.hypot(vx, vy)
        if v_mag == 0:
            continue
        intensity = mono_len[cy, cx]
        if intensity <= 1:
            continue
        length = min(vector_scale * intensity, max_length)
        if length < vector_min_length:
            continue
        ux = vx / v_mag * length
        uy = vy / v_mag * length
        angle_deg = (np.rad2deg(np.arctan2(vy, vx)) + 90) % 360
        points_list.append((cx, cy, ux, uy, angle_to_color(angle_deg)))

    radius = 25
    cos_thresh = np.cos(np.deg2rad(90))
    invalid = set()
    for i in range(len(points_list)):
        x1, y1, ux1, uy1, _ = points_list[i]
        for j in range(i + 1, len(points_list)):
            x2, y2, ux2, uy2, _ = points_list[j]
            if np.hypot(x1 - x2, y1 - y2) <= radius:
                dot = ux1 * ux2 + uy1 * uy2
                m1 = np.hypot(ux1, uy1)
                m2 = np.hypot(ux2, uy2)
                if m1 > 0 and m2 > 0 and dot / (m1 * m2) < cos_thresh:
                    invalid.add(i)
                    invalid.add(j)
    return [p for idx, p in enumerate(points_list) if idx not in invalid]


def draw_vectors(pil_img, points_list):
    np_img = np.array(pil_img)
    for cx, cy, ux, uy, dot_color in points_list:
        end_x = round(cx + ux)
        end_y = round(cy + uy)
        v_len = np.hypot(ux, uy)
        if v_len < 1.0:
            continue
        thickness = 1 if v_len < 15 else 2
        dir_x = ux / v_len
        dir_y = uy / v_len
        head_len = max(3.0, v_len * 0.4)
        base_x = round(end_x - dir_x * head_len)
        base_y = round(end_y - dir_y * head_len)
        perp_x = -dir_y
        perp_y = dir_x
        hw = head_len * 0.6
        p1_x = round(base_x + perp_x * hw)
        p1_y = round(base_y + perp_y * hw)
        p2_x = round(base_x - perp_x * hw)
        p2_y = round(base_y - perp_y * hw)
        cv2.line(np_img, (cx, cy), (end_x, end_y), dot_color, thickness, lineType=cv2.LINE_AA)
        cv2.line(np_img, (end_x, end_y), (p1_x, p1_y), dot_color, thickness, lineType=cv2.LINE_AA)
        cv2.line(np_img, (end_x, end_y), (p2_x, p2_y), dot_color, thickness, lineType=cv2.LINE_AA)
    return Image.fromarray(np_img)


def overlay_text(pil_img, text):
    if not text:
        return pil_img
    width, height = pil_img.size
    draw = ImageDraw.Draw(pil_img)
    font_size = max(18, int(height * 0.045))
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
    max_width = width - 40
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = current + (" " if current else "") + word
        try:
            bbox = font.getbbox(test)
            w = bbox[2] - bbox[0]
        except AttributeError:
            bbox = draw.textbbox((0, 0), test, font=font)
            w = bbox[2] - bbox[0]
        if w <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    line_height = font_size + 8
    total_h = len(lines) * line_height
    y = height - total_h - 30
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_width = bbox[2] - bbox[0]
        x = (width - text_width) // 2
        draw.text((x, y), line, font=font, fill="white",
                  stroke_width=2, stroke_fill="black")
        y += line_height
    return pil_img


# ============================================================
#  main
# ============================================================
def main():
    p = argparse.ArgumentParser(
        description="Кадр видео на середине субтитра. Копирует в буфер.")
    p.add_argument("video", help="путь к видеофайлу")
    p.add_argument("--номер", "--index", type=int, default=0,
                   help="индекс реплики (0-based)")
    p.add_argument("--без_стрелок", dest="без_стрелок", action="store_true",
                   help="НЕ накладывать стрелки — маркеры направления "
                        "градиента яркости движения (по умолч. стрелки ВКЛ)")
    p.add_argument("--сохранить", "--save", default=None,
                   help="дополнительно сохранить PNG")
    p.add_argument("--высота", type=int, default=480,
                   help="высота для расчёта эффектов (по умолч. 480)")
    p.add_argument("--список", action="store_true",
                   help="показать список реплик без картинки")
    p.add_argument("--no-clip", "--no_clip", dest="no_clip", action="store_true",
                   help="не класть в буфер обмена")
    args = p.parse_args()

    video = args.video
    if not os.path.isfile(video):
        print(f"ERR: файл не найден: {video}")
        return 1

    subtitles, sub_path = load_subtitles(video)
    if not subtitles:
        print(f"WARN: субтитры не найдены рядом с {video}")
        return 2

    print(f"SUBS: {sub_path}")
    clusters = build_clusters(subtitles)
    if not clusters:
        print("WARN: нет содержательных реплик")
        return 3

    if args.список:
        print(f"TOTAL: {len(clusters)}")
        for i, (start, end, text) in enumerate(clusters):
            mid = (start + end) / 2000.0
            print(f"  {i:3d}  {mid:7.2f}s  {text[:80]}")
        return 0

    idx = args.номер
    if idx < 0 or idx >= len(clusters):
        print(f"ERR: индекс {idx} вне 0..{len(clusters)-1}")
        return 4

    start_ms, end_ms, text = clusters[idx]
    mid_sec = (start_ms + end_ms) / 2000.0
    print(f"SHOT: {idx}/{len(clusters)-1}  |  {mid_sec:.2f}s")
    print(f"TEXT: {text}")

    raw = read_frame_at(video, mid_sec)
    if raw is None:
        print("ERR: не прочитать кадр")
        return 5

    if not args.без_стрелок:
        processed = log_blur_trail(video, mid_sec, target_height=args.высота)
        if processed is None:
            processed = raw
        fx_scale = raw.size[1] / float(processed.size[1])
        filtered = feynman_filter(processed)
        points_small = compute_vectors(filtered)
        if fx_scale != 1.0:
            points = [
                (int(round(cx * fx_scale)), int(round(cy * fx_scale)),
                 ux * fx_scale, uy * fx_scale, color)
                for cx, cy, ux, uy, color in points_small
            ]
        else:
            points = points_small
        final = draw_vectors(raw, points)
        print(f"ARROWS: {len(points)}")
    else:
        final = raw

    final = overlay_text(final, text)

    if args.no_clip:
        print(f"OK: {final.size[0]}x{final.size[1]}")
    elif copy_image_to_clipboard(final):
        print(f"CLIP: {final.size[0]}x{final.size[1]} -> буфер обмена")
    else:
        print("WARN: не положить в буфер")

    if args.сохранить:
        try:
            final.save(args.сохранить)
            print(f"SAVE: {args.сохранить}")
        except Exception as e:
            print(f"WARN: не сохранить: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
