import pandas as pd
import numpy as np
import os

p   = r"E:/Jericho/Private/tasks/PsychoHistory/master_dataset_1y.csv"
out = r"E:/Jericho/Private/tasks/PsychoHistory/clean"
os.makedirs(out, exist_ok=True)

df = pd.read_csv(p).sort_values("t").reset_index(drop=True)
cols = [c for c in df.columns if c != "t"]

result = df[["t"]].copy()
stats = []
for c in cols:
    s = df[c]
    prev = s.shift(1)
    both_nan = s.isna() & prev.isna()
    both_val = s.notna() & prev.notna() & (s == prev)
    same = both_nan | both_val
    is_start = ~same
    new = s.where(is_start, np.nan)
    result[c] = new
    nb = int(s.notna().sum())
    na = int(new.notna().sum())
    pct = 100*na/nb if nb else 0
    stats.append((c, nb, na, pct))

result.to_csv(os.path.join(out, "steps_only.csv"), index=False)

print(f"{'колонка':25s} {'было':>7s} {'стало':>7s} {'%':>6s}")
print("-"*60)
for c, b, a, pct in stats:
    print(f"{c:25s} {b:>7d} {a:>7d} {pct:>5.1f}%")

# непрерывные отдельно
cont_cols = ["population", "pop_volatility", "tech_points", "generation", "temp_anomaly"]
result[["t"] + [c for c in cont_cols if c in result.columns]].to_csv(
    os.path.join(out, "steps_continuous.csv"), index=False)

# события отдельно
event_cols = ["cumulative_wars", "has_war_5pct", "has_crisis_5pct",
              "has_epidemic_5pct", "active_wars", "cum_years_of_war",
              "collapse_manual"]
result[["t"] + [c for c in event_cols if c in result.columns]].to_csv(
    os.path.join(out, "steps_events.csv"), index=False)

print()
print("=== population: первые 25 реальных точек после очистки ===")
pop_clean = result[["t","population"]].dropna()
print(pop_clean.head(25).to_string(index=False))
print()
print(f"всего реальных точек population: {len(pop_clean)}")

print()
print("=== collapse_manual: ВСЕ реальные точки после очистки ===")
cm = result[["t","collapse_manual"]].dropna()
print(cm.to_string(index=False))
print(f"\nвсего: {len(cm)}")

print()
print("=== ФАЙЛЫ В clean/ ===")
for f in sorted(os.listdir(out)):
    fp = os.path.join(out, f)
    print(f"  {f:30s} {os.path.getsize(fp):>10d} байт")
