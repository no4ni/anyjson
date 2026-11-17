# -*- coding: utf-8 -*-
"""
Команда сжать_историю — сжатие JSON-лога диалога через rut5-squeeze.
"""

from _actions import register
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "side" / "external" / "squeeze" / "compress_history.py"


@register(["сжать_историю", "compress_history"])
def compress_history_cmd(args: dict) -> str:
    """Сжать историю диалога (JSON-лог) локальной моделью rut5-squeeze.

    Алгоритм:
    1. Найти точку отсечки: после какого сообщения превышено 75% символов.
    2. Первые 75% — обрабатываем:
       - role: system — оставляем только первый, остальные удаляем.
       - role: tool — удаляем "🔎 Найдено команд по...", "Ничего не найдено..."
       - role: tool с 📁 — обрезаем наполовину + "..."
       - Выкидываем base64-картинки из content.
       - Разбиваем content на чанки по 2 предложения.
       - Прогоняем через rut5-squeeze для сжатия.
       - Дедуплицируем tool-сообщения после сжатия.
    3. Хвост 25% — не трогаем.
    4. Склейка: обработанные 75% + хвост.

    Параметры:
      вход  — путь к JSON-логу (обязательно)
      выход — путь к результату (опционально, по умолчанию: вход_compressed.json)
      порог — доля первых сообщений для обработки (0.75 = 75%, по умолчанию)
      max_in — лимит токенов входа (512, по умолчанию)
      max_out — лимит токенов выхода (128, по умолчанию)
      beams — количество лучей (4, по умолчанию)

    Пример:
      {"сжать_историю": {"вход": "E:/Jericho/Private/log.json"}}
      {"сжать_историю": {"вход": "E:/log.json", "выход": "E:/log_small.json", "порог": 0.7}}
    """
    input_path = args.get("вход") or args.get("input")
    if not input_path:
        return "❌ сжать_историю: не указан параметр «вход»"
    
    output_path = args.get("выход") or args.get("output")
    threshold = args.get("порог", 0.75)
    max_in = args.get("max_in", 512)
    max_out = args.get("max_out", 128)
    beams = args.get("beams", 4)
    
    if not SCRIPT.exists():
        return f"❌ Скрипт не найден: {SCRIPT}"
    
    cmd = [sys.executable, str(SCRIPT), input_path]
    if output_path:
        cmd.append(output_path)
    cmd.extend(["--порог", str(threshold), "--max-in", str(max_in), "--max-out", str(max_out), "--beams", str(beams)])
    
    try:
        # errors='replace' предотвращает падение на не-UTF-8 символах в выводе (например, cp1251 из Windows)
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            encoding='utf-8', 
            errors='replace', 
            timeout=600
        )
        if result.returncode != 0:
            return f"❌ Ошибка сжатия:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        return result.stdout
    except subprocess.TimeoutExpired:
        return "❌ Таймаут (600 сек) — история слишком большая или модель зависла"
    except Exception as e:
        return f"❌ Ошибка запуска: {e}"