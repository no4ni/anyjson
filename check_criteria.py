import pandas as pd

p = r"E:/Jericho/Private/tasks/PsychoHistory/master_dataset_1y.csv"
df = pd.read_csv(p)

# что collapse_manual говорит про конкретные годы
years = {
    -1200: "Бронзовый коллапс",
    -1150: "Бронзовый коллапс (конец)",
    -905:  "конец 1-го периода",
    541:   "Юстинианова чума",
    632:   "Арабские завоевания (начало)",
    750:   "Арабские завоевания (конец)",
    1347:  "Чёрная смерть",
    1914:  "Первая мировая",
    1918:  "Первая мировая (конец)",
    1939:  "Вторая мировая",
    1945:  "Вторая мировая (конец)",
    476:   "Падение Западного Рима",
    1453:  "Падение Константинополя",
    1917:  "Революция в России",
    1991:  "Распад СССР",
    2020:  "COVID-19",
}

print(f"{'год':>7s}  {'collapse_manual':>16s}  событие")
print("-"*70)
for y, name in years.items():
    row = df[df["t"] == y]
    if len(row):
        v = row["collapse_manual"].values[0]
        print(f"{y:>7d}  {str(v):>16s}  {name}")
    else:
        print(f"{y:>7d}  {'(нет строки)':>16s}  {name}")

print()
print("=== ВСЕ 70 положительных точек collapse_manual ===")
pos = df[df["collapse_manual"] == 1][["t", "collapse_manual"]]
print(f"всего: {len(pos)}")
# группировка по непрерывным интервалам
ts = sorted(pos["t"].tolist())
groups = []
start = prev = ts[0]
for t in ts[1:]:
    if t - prev <= 10:
        prev = t
    else:
        groups.append((start, prev))
        start = prev = t
groups.append((start, prev))
print("интервалы:")
for a, b in groups:
    print(f"  {a}..{b}  ({b-a} лет)")
