import os

roots = [r"D:\\", r"E:\\"]
patterns = ["dataset_collapse", "collapse_manual"]
max_depth = 8
timeout = 55

import time
t0 = time.time()
found = []

skip = {"$recycle.bin", "windows", "program files", "program files (x86)",
        "programdata", "system volume information", "node_modules",
        ".git", "__pycache__", "appdata"}

for root in roots:
    if not os.path.exists(root):
        print(f"[нет] {root}")
        continue
    base_depth = root.rstrip("\\").count("\\")
    for dirpath, dirs, files in os.walk(root):
        if time.time() - t0 > timeout:
            print("[таймаут]"); break
        d = dirpath.lower()
        if any(s in d for s in skip):
            dirs[:] = []
            continue
        if dirpath.count("\\") - base_depth > max_depth:
            dirs[:] = []
            continue
        for f in files:
            fl = f.lower()
            for pat in patterns:
                if pat in fl:
                    p = os.path.join(dirpath, f)
                    try:
                        sz = os.path.getsize(p)
                    except:
                        sz = -1
                    found.append((p, sz))
                    break

print(f"\nнайдено: {len(found)}")
for p, sz in found:
    print(f"  {p}   {sz} байт")
