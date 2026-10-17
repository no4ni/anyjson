import pandas as pd
import numpy as np

p = r"E:/Jericho/Private/tasks/PsychoHistory/master_dataset_1y.csv"
df = pd.read_csv(p)

pop = df[["t", "population"]].dropna().reset_index(drop=True)

print("=== ПЕРВЫЕ 30 СТРОК ===")
print(pop.head(30).to_string())
print()

print("=== ДЛИНЫ ПОСЛЕДОВАТЕЛЬНЫХ ПОВТОРОВ (runs) ===")
vals = pop["population"].values
runs = []
cur = 1
for i in range(1, len(vals)):
    if vals[i] == vals[i-1]:
        cur += 1
    else:
        runs.append(cur)
        cur = 1
runs.append(cur)
runs = np.array(runs)
print(f"всего 'ступенек': {len(runs)}")
print(f"уникальных значений population: {pop['population'].nunique()}")
print(f"длина шага: min={runs.min()}, медиана={int(np.median(runs))}, max={runs.max()}, среднее={runs.mean():.1f}")
print()

print("=== ПРИМЕРЫ ДЛИННЫХ СТУПЕНЕК ===")
# найти где значение повторяется >5 раз
s = pop["population"]
prev = s.shift(1)
same = s == prev
# группировка
grp = (~same).cumsum()
groups = pop.groupby(grp).agg(t_min=("t","min"), t_max=("t","max"),
                               val=("population","first"), n=("t","size"))
long_runs = groups[groups["n"] > 5].sort_values("n", ascending=False)
print(long_runs.head(20).to_string())
print()

print("=== СРАВНЕНИЕ: НАСЕЛЕНИЕ ПО ШАГАМ В РАЗНЫХ ЭПОХАХ ===")
for year in [-8000, -5000, -2000, -500, 0, 500, 1000, 1500, 1800, 1900, 1950, 2000, 2020]:
    row = pop[pop["t"] == year]
    if len(row):
        print(f"t={year:>6d}  population={row['population'].values[0]:.0f}")

print()
print("=== РАЗНИЦА МЕЖДУ СОСЕДНИМИ ГОДАМИ (дифф) ===")
diff = pop["population"].diff()
nz = (diff != 0) & diff.notna()
print(f"лет, где population ИЗМЕНИЛСЯ: {nz.sum()} из {len(pop)-1}")
print(f"лет, где population НЕ изменился: {(~nz & diff.notna()).sum()}")
