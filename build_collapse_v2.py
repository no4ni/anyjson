import pandas as pd
import numpy as np
import os

out = r"E:/Jericho/Private/tasks/PsychoHistory/clean"
os.makedirs(out, exist_ok=True)

# === 11 коллапсов (смена режима) ===
# формат: (год_события, начало, конец, тип, название, что сменилось)
events = [
    (-2200, -2200, -2150, "распад",     "Крах Аккада",              "конец шумерской гегемонии, гутии"),
    (-1200, -1200, -1150, "распад",     "Бронзовый коллапс",        "конец бронзового века, народы моря"),
    ( 476,   395,   476, "распад",     "Падение Западного Рима",   "античность → Средневековье"),
    ( 541,   541,   600, "эпидемия",   "Юстинианова чума",         "конец античной экономики"),
    ( 651,   636,   651, "распад",     "Падение Сасанидов",        "исламская экспансия"),
    (1347,  1347,  1353, "эпидемия",   "Чёрная смерть",            "феодализм → Ренессанс"),
    (1453,  1453,  1453, "распад",     "Падение Византии",         "конец тысячелетней империи"),
    (1521,  1519,  1521, "распад",     "Падение Ацтеков",          "конец мезоамериканских цивилизаций"),
    (1918,  1914,  1918, "война",      "Первая мировая",           "распад 4 империй"),
    (1945,  1939,  1945, "война",      "Вторая мировая",           "конец колониальной системы, ООН"),
    (1991,  1991,  1991, "распад",     "Распад СССР",              "конец биполярного мира"),
]

# таблица событий
ev = pd.DataFrame(events, columns=["year","start","end","type","name","regime_change"])
ev.to_csv(os.path.join(out, "collapse_events.csv"), index=False)

print("=== 11 СОБЫТИЙ ===")
print(ev.to_string(index=False))
print(f"\nсохранено → collapse_events.csv")

# === разметка на годовой сетке -8585..2026 ===
years = np.arange(-8585, 2027)
c1 = np.zeros(len(years), dtype=int)
for _, r in ev.iterrows():
    mask = (years >= r["start"]) & (years <= r["end"])
    c1[mask] = 1
grid1 = pd.DataFrame({"t": years, "collapse_v2": c1})
grid1.to_csv(os.path.join(out, "collapse_v2_1y.csv"), index=False)

print(f"\nгодовая сетка: {len(grid1)} строк, положительных {c1.sum()}")
print("интервалы collapse_v2=1 (годовая):")
pos = grid1[grid1["collapse_v2"]==1]["t"].tolist()
if pos:
    runs = []
    s = p = pos[0]
    for t in pos[1:]:
        if t == p+1:
            p = t
        else:
            runs.append((s,p)); s=p=t
    runs.append((s,p))
    for s,e in runs:
        print(f"  {s}..{e}  ({e-s+1} лет)")

# === разметка на 10-летней сетке (для сравнения с collapse_manual) ===
years10 = np.arange(-8585, 2027, 10)
c10 = np.zeros(len(years10), dtype=int)
for _, r in ev.iterrows():
    mask = (years10 >= r["start"]) & (years10 <= r["end"])
    c10[mask] = 1
grid10 = pd.DataFrame({"t": years10, "collapse_v2": c10})
grid10.to_csv(os.path.join(out, "collapse_v2_10y.csv"), index=False)

print(f"\n10-летняя сетка: {len(grid10)} строк, положительных {c10.sum()}")
print("интервалы collapse_v2=1 (10 лет):")
pos10 = grid10[grid10["collapse_v2"]==1]["t"].tolist()
if pos10:
    runs = []
    s = p = pos10[0]
    for t in pos10[1:]:
        if t == p+10:
            p = t
        else:
            runs.append((s,p)); s=p=t
    runs.append((s,p))
    for s,e in runs:
        print(f"  {s}..{e}  ({e-s+10} лет, {(e-s)//10+1} точек)")

# === сравнение с collapse_manual ===
print("\n=== СРАВНЕНИЕ с collapse_manual (10-летняя) ===")
md = pd.read_csv(r"E:/Jericho/Private/tasks/PsychoHistory/master_dataset_1y.csv")
md10 = md[md["t"] % 10 == 0][["t","collapse_manual"]].dropna()
merged = grid10.merge(md10, on="t", how="left")
cm = merged.dropna(subset=["collapse_manual"])
both = ((cm["collapse_v2"]==1) & (cm["collapse_manual"]==1)).sum()
only_v2 = ((cm["collapse_v2"]==1) & (cm["collapse_manual"]==0)).sum()
only_manual = ((cm["collapse_v2"]==0) & (cm["collapse_manual"]==1)).sum()
neither = ((cm["collapse_v2"]==0) & (cm["collapse_manual"]==0)).sum()
print(f"  оба:            {both}")
print(f"  только v2:      {only_v2}")
print(f"  только manual:  {only_manual}")
print(f"  ни одного:      {neither}")
