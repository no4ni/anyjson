# -*- coding: utf-8 -*-
"""
convert_to_fp16.py — конвертация rut5-squeeze в FP16 на диске.
Запускать ОДИН РАЗ. Создаёт папку rut5-squeeze-fp16 рядом с оригиналом.
"""
import sys
import json
import shutil
from pathlib import Path
import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer, AutoConfig

sys.stdout.reconfigure(encoding='utf-8')

HERE = Path(__file__).resolve().parent
SRC = HERE / "rut5-squeeze"
DST = HERE / "rut5-squeeze-fp16"

if DST.exists():
    print(f"[!] {DST.name} уже существует. Удаляю и пересоздаю...")
    shutil.rmtree(DST)

print(f"[1/4] Копирую файлы модели (токенизатор, конфиг, vocab)...")
shutil.copytree(SRC, DST, ignore=shutil.ignore_patterns("*.safetensors", "*.bin", "*.pt"))

print(f"[2/4] Загружаю FP32-веса (пик ~260 МБ)...")
model = AutoModelForSeq2SeqLM.from_pretrained(SRC, torch_dtype=torch.float32)

print(f"[3/4] Конвертирую в FP16 и сохраняю...")
model = model.half()  # float32 -> float16
model.save_pretrained(DST, safe_serialization=True)

print(f"[4/4] Обновляю config.json (dtype: float16)...")
config_path = DST / "config.json"
with open(config_path, 'r', encoding='utf-8') as f:
    cfg = json.load(f)
cfg["dtype"] = "float16"
cfg["torch_dtype"] = "float16"
with open(config_path, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)

# Проверяем размер
src_size = sum(f.stat().st_size for f in SRC.rglob("*") if f.is_file()) / 1024 / 1024
dst_size = sum(f.stat().st_size for f in DST.rglob("*") if f.is_file()) / 1024 / 1024
print(f"\n✅ Готово!")
print(f"   Оригинал (FP32): {src_size:.1f} МБ")
print(f"   Копия (FP16):    {dst_size:.1f} МБ")
print(f"   Экономия:        {src_size - dst_size:.1f} МБ")