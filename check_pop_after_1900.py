import pandas as pd
import json, os

root = r"E:/Jericho/Private/tasks/PsychoHistory"

# 1. master_dataset_1y.csv
print("=== master_dataset_1y.csv: population 1900-2026 ===")
md = pd.read_csv(os.path.join(root, "master_dataset_1y.csv"))
sub = md[(md["t"] >= 1900) & (md["t"] <= 2026)][["t","population"]].dropna()
print(sub.to_string(index=False))
print(f"\nвсего точек: {len(sub)}")

# 2. НаселениеТемпература.xlsx — лист Население
print("\n=== НаселениеТемпература.xlsx / лист 'Население' ===")
p2 = os.path.join(root, "НаселениеТемпература.xlsx")
df2 = pd.read_excel(p2, sheet_name="Население")
print(f"колонки: {list(df2.columns)}")
print(f"строк: {len(df2)}")
print(df2.tail(40).to_string())

# 3. psychohistory_import.json — ключи про население
print("\n=== psychohistory_import.json / население ===")
p3 = os.path.join(root, "psychohistory_import.json")
with open(p3, encoding="utf-8") as f:
    data = json.load(f)
for k in data:
    if "асен" in k or "opulation" in k.lower() or "Pop" in k:
        v = data[k]
        print(f"\n-- {k} (тип {type(v).__name__}) --")
        if isinstance(v, dict):
            print(f"   ключи: {list(v.keys())[:20]}")
        elif isinstance(v, list):
            print(f"   длина: {len(v)}")
            print(f"   первый: {str(v[0])[:200] if v else 'пусто'}")
            print(f"   последний: {str(v[-1])[:200] if v else 'пусто'}")
        else:
            print(f"   {str(v)[:300]}")

# 4. Населениe.csv — 71 строка
print("\n=== Население.csv ===")
df4 = pd.read_csv(os.path.join(root, "Население.csv"), sep=";")
print(df4.tail(20).to_string())
