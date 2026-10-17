import pandas as pd
import numpy as np
import os

clean = r"E:/Jericho/Private/tasks/PsychoHistory/clean"
root  = r"E:/Jericho/Private/tasks/PsychoHistory"

# загружаем оба датасета
md = pd.read_csv(os.path.join(root, "master_dataset_1y.csv"))
v2 = pd.read_csv(os.path.join(clean, "collapse_v2_10y.csv"))

# из master берём только строки, где collapse_manual размечен (не NaN)
md_cm = md[["t","collapse_manual"]].dropna()
print(f"collapse_manual размечен: {len(md_cm)} точек")
print(f"  collapse=1: {int(md_cm['collapse_manual'].sum())}")

# смотрим пересечение по t (точно, без %)
merged = v2.merge(md_cm, on="t", how="inner")
print(f"\nпересечение по годам: {len(merged)} точек")
both = ((merged["collapse_v2"]==1) & (merged["collapse_manual"]==1)).sum()
only_v2 = ((merged["collapse_v2"]==1) & (merged["collapse_manual"]==0)).sum()
only_md = ((merged["collapse_v2"]==0) & (merged["collapse_manual"]==1)).sum()
neither = ((merged["collapse_v2"]==0) & (merged["collapse_manual"]==0)).sum()
print(f"\n  оба=1:              {both}")
print(f"  только v2=1:        {only_v2}")
print(f"  только manual=1:    {only_md}")
print(f"  ни одного:          {neither}")

print("\n=== ГДЕ ТОЛЬКО MANUAL=1 (v2 не поймал) ===")
om = merged[(merged["collapse_v2"]==0) & (merged["collapse_manual"]==1)]
print(f"точек: {len(om)}")
print(om["t"].tolist())

print("\n=== ГДЕ ТОЛЬКО V2=1 (manual не поймал) ===")
ov = merged[(merged["collapse_v2"]==1) & (merged["collapse_manual"]==0)]
print(f"точек: {len(ov)}")
print(ov["t"].tolist())

print("\n=== ГДЕ ОБА=1 ===")
b = merged[(merged["collapse_v2"]==1) & (merged["collapse_manual"]==1)]
print(f"точек: {len(b)}")
print(b["t"].tolist())

# Сохраняем объединённую таблицу
merged.to_csv(os.path.join(clean, "compare_v2_vs_manual.csv"), index=False)
