"""Мультимодальный анализ: изображения, экран.

Использует llama-server, запускаемый автоматически при необходимости.
"""
import io
import os
import time
import base64
import tempfile
from pathlib import Path

import psutil
import requests
from PIL import Image, ImageGrab

from _actions import _tools_core


# ---------- пути к llama-server ----------
LLAMA_SERVER_PATH = r"E:\vikhr-llama\llama.cpp-cuda124\llama-server.exe"
MM_MODEL_PATH = r"E:\vikhr-llama\Huihui-Qwen3.5-4B-Claude-4.6-Opus-abliterated.i1-Q4_K_M.gguf"
MMPROJ_PATH = r"E:\vikhr-llama\Huihui-Qwen3.5-4B-Claude-4.6-Opus-abliterated.mmproj-Q8_0.gguf"

_multimodal_server_started = False


def ensure_multimodal_server():
    """Запускает llama-server, если ещё не запущен. Возвращает True/False."""
    global _multimodal_server_started

    def is_ready():
        try:
            r = requests.get("http://127.0.0.1:8080/health", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    if is_ready():
        return True

    # Проверяем, не запущен ли уже процесс
    for proc in psutil.process_iter(["pid", "name"]):
        if proc.info["name"] == "llama-server.exe":
            print("Сервер уже запущен, ожидаем готовности...")
            start = time.time()
            while time.time() - start < 120:
                if is_ready():
                    print("Сервер готов.")
                    return True
                time.sleep(3)
            print("Таймаут ожидания сервера.")
            return False

    # Запускаем сервер
    try:
        import subprocess
        cmd = [
            LLAMA_SERVER_PATH,
            "-m", MM_MODEL_PATH,
            "--mmproj", MMPROJ_PATH,
            "-ngl", "28",
            "-c", "20000",
            "--temp", "0.8",
            "--repeat-penalty", "1.2",
            "--host", "127.0.0.1",
            "--port", "8080",
            "--log-disable",
        ]
        subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
        print("Сервер запущен, ожидаем загрузки модели (до 2 минут)...")
        start = time.time()
        while time.time() - start < 120:
            if is_ready():
                print("Сервер готов.")
                return True
            time.sleep(3)
        print("Таймаут ожидания сервера.")
        return False
    except Exception as e:
        print(f"Ошибка запуска сервера: {e}")
        return False


def analyze_image(image_path, question="Опиши изображение"):
    """Анализ одного изображения мультимодальной моделью."""
    if not os.path.exists(image_path):
        return f"Ошибка: файл {image_path} не найден"
    try:
        img = Image.open(image_path)
    except Exception as e:
        return f"Ошибка открытия изображения: {e}"
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
    quality = 90
    img.save(tmp_path, "JPEG", quality=quality)
    size_kb = os.path.getsize(tmp_path) / 1024
    if size_kb > 40:
        ratio = (40 * 1024 / size_kb) ** 0.5
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img.thumbnail(new_size, Image.Resampling.LANCZOS)
        img.save(tmp_path, "JPEG", quality=quality)
        size_kb = os.path.getsize(tmp_path) / 1024
        if size_kb > 40:
            img.save(tmp_path, "JPEG", quality=60)
    with open(tmp_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    os.unlink(tmp_path)

    if not ensure_multimodal_server():
        return "Мультимодальный сервер не отвечает."

    url = "http://127.0.0.1:8080/v1/chat/completions"
    payload = {
        "model": "qwen3.5",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": question},
            ],
        }],
        "max_tokens": 2048,
        "stream": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=180)
        if resp.status_code != 200:
            error_text = resp.text if resp.text is not None else ""
            return f"Ошибка анализа: {resp.status_code} {error_text}"
        data = resp.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "Нет ответа")
    except Exception as e:
        return f"Ошибка при запросе к модели: {e}"


def capture_screen(region=None, fit="crop"):
    """Захват экрана. fit='crop' — обрезать, 'all' — весь и сжать."""
    try:
        full_screen = ImageGrab.grab()
    except Exception as e:
        return None, f"Ошибка при захвате экрана: {e}"

    MAX_AREA = 40000
    width, height = full_screen.size

    if fit == "all":
        area = width * height
        if area > MAX_AREA:
            scale = (MAX_AREA / area) ** 0.5
            new_size = (int(width * scale), int(height * scale))
            full_screen.thumbnail(new_size, Image.Resampling.LANCZOS)
        return full_screen, None

    if region is None:
        return full_screen, None

    if isinstance(region, str):
        r = region.lower()
        if r in ("taskbar", "bottom", "низ"):
            max_h = MAX_AREA // width
            max_h = max(1, max_h)
            bbox = (0, max(0, height - max_h), width, height)
        elif r in ("top", "header", "верх"):
            max_h = MAX_AREA // width
            max_h = max(1, max_h)
            bbox = (0, 0, width, max_h)
        elif r in ("left", "list", "лево"):
            max_w = MAX_AREA // height
            max_w = max(1, max_w)
            bbox = (0, 0, max_w, height)
        elif r in ("right", "side", "право"):
            max_w = MAX_AREA // height
            max_w = max(1, max_w)
            bbox = (max(0, width - max_w), 0, width, height)
        elif r in ("center", "центр", "окно"):
            max_w = min(width, int((MAX_AREA * width / height) ** 0.5))
            max_h = min(height, int((MAX_AREA * height / width) ** 0.5))
            left = (width - max_w) // 2
            top = (height - max_h) // 2
            bbox = (left, top, left + max_w, top + max_h)
        else:
            return None, f"Неизвестное имя области: {region}"
        return full_screen.crop(bbox), None
    elif isinstance(region, (list, tuple)) and len(region) == 4:
        x, y, w, h = region
        if w * h > MAX_AREA:
            return None, f"Площадь области ({w * h}) превышает лимит {MAX_AREA}"
        bbox = (x, y, x + w, y + h)
        return full_screen.crop(bbox), None
    else:
        return None, "Область должна быть строкой или списком [x,y,w,h]"


def analyze_screen(region=None, question=None, fit="crop", history=None):
    """OCR-анализ (без question) или мультимодальный анализ (с question)."""
    from _actions.tools_files import run_sensor

    screenshot, err = capture_screen(region, fit)
    if err:
        return err
    if question is None:
        my_dir = _tools_core.get_my_dir()
        temp_path = my_dir / "screen_temp.png"
        screenshot.save(str(temp_path))
        return run_sensor(str(temp_path), lang="rus+eng",
                          config="--psm 6 --oem 3")

    if not ensure_multimodal_server():
        return "Не удалось запустить мультимодальный сервер."

    MAX_AREA = 40000
    width, height = screenshot.size
    area = width * height
    if area > MAX_AREA:
        scale = (MAX_AREA / area) ** 0.5
        new_size = (int(width * scale), int(height * scale))
        screenshot.thumbnail(new_size, Image.Resampling.LANCZOS)

    buffered = io.BytesIO()
    screenshot.save(buffered, format="JPEG", quality=85)
    img_b64 = base64.b64encode(buffered.getvalue()).decode()
    url = "http://127.0.0.1:8080/v1/chat/completions"
    payload = {
        "model": "qwen3.5",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": question},
            ],
        }],
        "max_tokens": 512,
        "stream": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=180)
        if resp.status_code != 200:
            error_text = resp.text if resp.text is not None else ""
            return f"Ошибка анализа: {resp.status_code} {error_text}"
        data = resp.json()

        for proc in psutil.process_iter(["pid", "name"]):
            if proc.info["name"] == "llama-server.exe":
                proc.terminate()
                time.sleep(2)
                if proc.is_running():
                    proc.kill()

        return data.get("choices", [{}])[0].get("message", {}).get("content", "Нет ответа")
    except Exception as e:
        return f"Ошибка при запросе к модели: {e}"
