"""Описание картинки через локальный llama-server (мультимодальная модель)."""
import os
import base64
import io

from _actions import register

try:
    import requests
except Exception:
    requests = None

try:
    from PIL import Image
except Exception:
    Image = None

SERVER_URL = "http://127.0.0.1:8080/v1/chat/completions"


@register(["описать_картинку", "описание_картинки", "describe_image", "что_на_картинке"])
def describe_image_cmd(args):
    """Описать картинку через локальную мультимодальную модель (llama-server).

    Параметры:
      путь         — файл-картинка (обязательный)
      промпт       — что спросить (по умолч. "Опиши изображение подробно")
      пикселей     — макс. разрешение в мегапикселях (по умолч. 0.5)
      макс_токенов — лимит ответа (по умолч. 500)
      таймаут      — сек (по умолч. 120)

    Требует запущенный llama-server на 127.0.0.1:8080 с --mmproj.
    """
    if isinstance(args, str):
        args = {"путь": args}
    path = (args.get("путь") or args.get("файл") or "").strip()
    if not path:
        return "❌ Укажи 'путь' к картинке."
    if not os.path.isfile(path):
        return f"❌ Файл не найден: {path}"
    if requests is None:
        return "❌ Нужен пакет requests (pip install requests)."
    if Image is None:
        return "❌ Нужен Pillow (pip install Pillow)."

    try:
        max_px = int(float(args.get("пикселей") or 0.5) * 1_000_000)
    except (TypeError, ValueError):
        max_px = 500_000

    try:
        img = Image.open(path)
        w, h = img.size
        if w * h > max_px:
            scale = (max_px / (w * h)) ** 0.5
            img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        return f"❌ Не подготовить изображение: {e}"

    prompt = args.get("промпт") or "Опиши изображение подробно и точно."
    try:
        max_tokens = int(args.get("макс_токенов") or 500)
        timeout = int(args.get("таймаут") or 120)
    except (TypeError, ValueError):
        max_tokens, timeout = 500, 120

    payload = {
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            ]
        }],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": False,
    }

    try:
        r = requests.post(SERVER_URL, json=payload, timeout=timeout)
    except requests.exceptions.ConnectionError:
        return ("❌ Сервер не отвечает на 127.0.0.1:8080.\n"
                "Запусти llama-server:\n"
                '  E:\\vikhr-llama\\llama.cpp-cuda124\\llama-server.exe '
                '-m "E:\\vikhr-llama\\Huihui-Qwen3.5-4B-Claude-4.6-Opus-abliterated.i1-Q4_K_M.gguf" '
                '--mmproj "E:\\vikhr-llama\\Huihui-Qwen3.5-4B-Claude-4.6-Opus-abliterated.mmproj-Q8_0.gguf" '
                '-c 2048 -ngl 99 --port 8080')
    except Exception as e:
        return f"❌ Ошибка запроса: {e}"

    if r.status_code != 200:
        return f"❌ Сервер вернул {r.status_code}: {r.text[:300]}"

    try:
        data = r.json()
        text = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"❌ Не разобрать ответ: {e}\n{r.text[:300]}"

    return f"🖼️ {os.path.basename(path)}\n\n{text}"