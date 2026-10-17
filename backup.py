import shutil, os, hashlib
from datetime import datetime

src_dir = r"E:/Jericho/Private/tasks/PsychoHistory"
bak_dir = r"E:/Jericho/Private/tasks/PsychoHistory/_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S")
os.makedirs(bak_dir, exist_ok=True)

# что бэкапим — ключевые файлы, которые будем трогать
files = [
    "master_dataset_1y.csv",
    "event_participation.csv",
    "events_1y.csv",
    "event_durations.csv",
    "thucydides_by_year.csv",
    "thucydides_full.csv",
    "STATUS.txt",
    "TODO.txt",
    "pipeline.py",
    "pipeline_v2.py",
]

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

print(f"Бэкап → {bak_dir}")
print()
print(f"{'файл':30s} {'размер':>10s}  sha256[:12]")
print("-"*70)

manifest = []
for f in files:
    src = os.path.join(src_dir, f)
    if not os.path.exists(src):
        print(f"{f:30s} {'НЕТ':>10s}")
        continue
    dst = os.path.join(bak_dir, f)
    shutil.copy2(src, dst)
    size = os.path.getsize(src)
    h = sha256(src)
    manifest.append((f, size, h))
    print(f"{f:30s} {size:>10d}  {h[:12]}")

# манифест для проверки целостности
with open(os.path.join(bak_dir, "_MANIFEST.txt"), "w", encoding="utf-8") as fh:
    fh.write(f"Backup: {datetime.now().isoformat()}\n")
    fh.write(f"Source: {src_dir}\n\n")
    for f, size, h in manifest:
        fh.write(f"{h}  {size:>10d}  {f}\n")

# ещё — рекурсивный бэкап подпапок wars/ и corell_apparat/
for sub in ["wars", "corell_apparat", "method", "oracul", "PRIVATE_wars"]:
    s = os.path.join(src_dir, sub)
    if os.path.isdir(s):
        d = os.path.join(bak_dir, sub)
        shutil.copytree(s, d)
        n = sum(len(fs) for _,_,fs in os.walk(d))
        print(f"  папка {sub}/: скопировано {n} файлов")

print()
print(f"ИТОГО: бэкап создан в {bak_dir}")
