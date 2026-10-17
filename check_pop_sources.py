import os, json
import pandas as pd

root = r"E:/Jericho/Private/tasks/PsychoHistory"
candidates = [
    "Население.xlsx",
    "НаселениеТемпература.xlsx",
    "НаселениеБудущее.xlsx",
    "Население.csv",
    "psychohistory_import.json",
    "wars/Население.csv",
    "wars/wars.xlsx",
]

for rel in candidates:
    p = os.path.join(root, rel)
    if not os.path.exists(p):
        print(f"[нет] {rel}")
        continue
    sz = os.path.getsize(p)
    print(f"\n=== {rel}  ({sz} байт) ===")
    try:
        if rel.endswith(".xlsx"):
            xl = pd.ExcelFile(p)
            print(f"  листы: {xl.sheet_names}")
            for sh in xl.sheet_names[:3]:
                df = xl.parse(sh, nrows=5)
                print(f"  -- лист '{sh}': {df.shape[1]} колонок")
                print(f"     колонки: {list(df.columns)[:15]}")
                print(f"     первые строки:")
                print(df.head(3).to_string()[:600])
        elif rel.endswith(".csv"):
            df = pd.read_csv(p, nrows=5)
            print(f"  колонки: {list(df.columns)}")
            print(df.head(5).to_string()[:600])
            # сколько всего строк
            full = pd.read_csv(p)
            print(f"  всего строк: {len(full)}")
        elif rel.endswith(".json"):
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                print(f"  ключи: {list(data.keys())[:20]}")
            elif isinstance(data, list):
                print(f"  список, длина: {len(data)}")
                print(f"  первый элемент: {str(data[0])[:300]}")
    except Exception as e:
        print(f"  ошибка: {e}")
