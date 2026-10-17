import pandas as pd
import numpy as np

p = r"E:/Jericho/Private/tasks/PsychoHistory/master_dataset_1y.csv"
df = pd.read_csv(p)

def run_stats(s):
    """Сколько ступенек и какова медианная длина полки."""
    v = s.dropna().values
    if len(v) < 2:
        return 0, 0, 0, len(v)
    runs = []
    cur = 1
    for i in range(1, len(v)):
        if v[i] == v[i-1]:
            cur += 1
        else:
            runs.append(cur); cur = 1
    runs.append(cur)
    runs = np.array(runs)
    return len(runs), int(np.median(runs)), int(runs.max()), len(v)

print(f"{'колонка':25s} {'real_pts':>9s} {'runs':>6s} {'med_run':>8s} {'max_run':>8s} {'тип':>20s}")
print("-" * 90)

for c in df.columns:
    if c == "t": continue
    n_runs, med, mx, n = run_stats(df[c])
    # тип
    if n == 0:
        typ = "пусто"
    elif mx <= 1:
        typ = "погодовой реальный"
    elif mx <= 5:
        typ = "реальный (~год)"
    elif mx <= 50:
        typ = "разрежённый"
    elif mx <= 500:
        typ = "грубый"
    else:
        typ = "ФЕЙК (forward-fill)"
    print(f"{c:25s} {n:>9d} {n_runs:>6d} {med:>8d} {mx:>8d} {typ:>20s}")
