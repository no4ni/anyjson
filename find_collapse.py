import os
from pathlib import Path

roots = [
    r"E:/Jericho",
    r"E:/Jericho/Private",
    r"E:/Jericho/Private/tasks/PsychoHistory",
    r"E:/Jericho/Private/tasks/PsychoHistory/_backup_20261007_205426",
    r"E:/Jericho/Private/tasks/PsychoHistory/_quarantine_20261007_205935",
    r"E:/Jericho/Private/tasks/PsychoHistory/_quarantine_20261007_210500",
]

patterns = [
    "dataset_collapse_manual.json",
    "dataset_collapse_manual_new_features.json",
    "dataset_collapse.json",
    "collapse_manual",
]

print("=== ПОИСК ФАЙЛОВ ===")
seen = set()
for root in roots:
    if not os.path.isdir(root): 
        print(f"нет папки: {root}")
        continue
    for dirpath, dirs, files in os.walk(root):
        # не лезть в системные
        if any(x in dirpath for x in [".git", "__pycache__", "node_modules"]):
            continue
        for f in files:
            fl = f.lower()
            for pat in patterns:
                if pat.lower() in fl:
                    p = os.path.join(dirpath, f)
                    if p in seen: continue
                    seen.add(p)
                    try:
                        sz = os.path.getsize(p)
                    except: sz = -1
                    print(f"  {p}   {sz} байт")
                    break

print()
print(f"найдено: {len(seen)}")
