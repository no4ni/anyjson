import pandas as pd
import numpy as np

p = r"E:/Jericho/Private/tasks/PsychoHistory/master_dataset_1y.csv"
df = pd.read_csv(p)

print("=== РАЗМЕР ===")
print(df.shape)
print()

print("=== КОЛОНКИ ===")
print(list(df.columns))
print()

print("=== ПЕРВЫЕ 3 СТРОКИ ===")
print(df.head(3).to_string())
print()

print("=== ПОСЛЕДНИЕ 3 СТРОКИ ===")
print(df.tail(3).to_string())
print()

print("=== ПО КАЖДОЙ КОЛОНКЕ: NaN / нули / уники / min / max ===")
for c in df.columns:
    s = df[c]
    n_nan = s.isna().sum()
    try:
        n_zero = (s == 0).sum()
        n_unique = s.nunique(dropna=True)
        mn, mx = s.min(), s.max()
    except Exception as e:
        n_zero = n_unique = mn = mx = f"ERR:{e}"
    print(f"{c:30s} NaN={n_nan:6d}  нулей={n_zero:6d}  уников={n_unique:6d}  min={mn}  max={mx}")
print()

print("=== ЦЕЛЕВАЯ collapse_manual ===")
if "collapse_manual" in df.columns:
    print(df["collapse_manual"].value_counts(dropna=False))
    print("сумма положительных:", int(df["collapse_manual"].fillna(0).sum()))
print()

print("=== РАННИЕ ГОДЫ (первые 5) ===")
year_col = [c for c in df.columns if c.lower() in ("t","year","год","years")][:1]
if year_col:
    yc = year_col[0]
    print(f"колонка года: {yc}")
    print(df.nsmallest(5, yc).to_string())
    print()
    print(df.nlargest(5, yc).to_string())
