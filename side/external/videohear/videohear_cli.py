#!/usr/bin/env python3
"""CLI: кадр видео на указанной секунде (+ опц. slit-scan) -> буфер обмена.
Использование:
    python videohear_cli.py VIDEO --секунда 12.5 [--slit-scan] [--окно 3.0] [--без-субтитра]
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
from PIL import Image, ImageDraw, ImageFont

try:
    import win32clipboard
    import win32con
    HAS_PYWIN32 = True
except ImportError:
    HAS_PYWIN32 = False

try:
    import pysubs2
    HAS_PYSUBS2 = True
except ImportError:
    HAS_PYSUBS2 = False


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
            print(f"[clip] fail: {e}", file=sys.stderr)
    return False


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


def clean_subtitle_text(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ")
    text = text.replace("\\N", "\n").replace("\\n", "\n")
    return text


def load_subtitles(video_path):
    if not HAS_PYSUBS2:
        return [], None
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


def get_active_sub(subtitles, time_sec):
    if not subtitles:
        return ""
    ms = time_sec * 1000.0
    active = [s for s in subtitles if s.start <= ms <= s.end]
    if not active:
        return ""
    best = max(active, key=lambda s: len(clean_subtitle_text(s.text).strip()))
    text = clean_subtitle_text(best.text)
    text = re.sub(r"\s+", " ", text.replace("\\N", " ").replace("\n", " ")).strip()
    return text


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


def read_frame_at(video_path, time_sec):
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
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def slit_scan_render(video_path, time_sec, window_sec=3.0,
                     out_width=780, out_height=265):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0 or fps > 100:
        fps = 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    t_start = max(0.0, time_sec - window_sec)
    t_end = time_sec

    first_frame = int(round(t_start * fps))
    last_frame = int(round(t_end * fps))
    if last_frame >= total:
        last_frame = total - 1
    if first_frame > last_frame:
        cap.release()
        return None
    cap.set(cv2.CAP_PROP_POS_FRAMES, first_frame)

    frames = []
    for _ in range(first_frame, last_frame + 1):
        ret, frame = cap.read()
        if not ret:
            break
        frame = cv2.resize(frame, (out_width, out_height),
                           interpolation=cv2.INTER_AREA)
        frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    cap.release()

    if not frames:
        return None

    stack = np.stack(frames, axis=0).astype(np.float32)  # (N, H, W, 3)
    n = stack.shape[0]

    x_indices = np.arange(out_width, dtype=np.float32)
    t_map = t_start + window_sec * (x_indices / max(1, out_width - 1))
    frame_pos = t_map * fps - first_frame
    idx_left = np.floor(frame_pos).astype(np.int32)
    idx_right = idx_left + 1
    alpha = (frame_pos - idx_left).astype(np.float32)

    np.clip(idx_left, 0, n - 1, out=idx_left)
    np.clip(idx_right, 0, n - 1, out=idx_right)

    out = np.zeros((out_height, out_width, 3), dtype=np.float32)
    for x in range(out_width):
        a = alpha[x]
        fl = stack[idx_left[x], :, x, :]
        fr = stack[idx_right[x], :, x, :]
        out[:, x, :] = fl * (1.0 - a) + fr * a

    out = np.clip(out, 0, 255).astype(np.uint8)
    return Image.fromarray(out)


def main():
    p = argparse.ArgumentParser(description="Кадр видео на секунде -> буфер.")
    p.add_argument("video", help="путь к видеофайлу")
    p.add_argument("--секунда", "--sec", dest="sec", type=float, required=True)
    p.add_argument("--slit-scan", dest="slit_scan", action="store_true",
                   help="применить slit-scan эффект (по умолч. ВЫКЛ)")
    p.add_argument("--окно", dest="window", type=float, default=3.0,
                   help="ширина окна slit-scan в секундах (по умолч. 3.0)")
    p.add_argument("--без-субтитра", dest="no_subs", action="store_true",
                   help="НЕ накладывать субтитр")
    p.add_argument("--сохранить", "--save", dest="save", default=None)
    p.add_argument("--no-clip", "--no_clip", dest="no_clip", action="store_true",
                   help="не класть в буфер обмена")
    args = p.parse_args()

    video = args.video
    if not os.path.isfile(video):
        print(f"ERR: файл не найден: {video}")
        return 1

    if args.slit_scan:
        img = slit_scan_render(video, args.sec, window_sec=args.window)
    else:
        img = read_frame_at(video, args.sec)

    if img is None:
        print("ERR: не прочитать кадр")
        return 5

    if not args.no_subs:
        subs, _ = load_subtitles(video)
        text = get_active_sub(subs, args.sec)
        if text:
            img = overlay_text(img, text)
            print(f"SUB: {text}")

    if args.no_clip:
        print(f"OK: {img.size[0]}x{img.size[1]}")
    elif copy_image_to_clipboard(img):
        print(f"CLIP: {img.size[0]}x{img.size[1]} -> буфер обмена")
    else:
        print("WARN: не положить в буфер")

    if args.save:
        try:
            img.save(args.save)
            print(f"SAVE: {args.save}")
        except Exception as e:
            print(f"WARN: не сохранить: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
