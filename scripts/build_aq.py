"""空品資料整理：12 個月 .ods → 測站 × 季節 × 日型 × 24 小時 平均值與樣本數，輸出 data/web/aq_data.js

規則（寫進 README/QA）：
- 污染物 '-' 視為缺測；AQI = 0 視為缺測（原檔 90 列，其中 55 列所有污染物皆為 '-'）。
- 小時 h＝資料時間 h:00 那一筆（環保局逐時資料）。
- 日型：2026 年依人事總處 115 年辦公日曆（是否放假=2 為假日，含補班日）；
  2025-11、12 月缺 114 年日曆，暫以週六日為假日（已標示）。
- 季節：冬 12–2、春 3–5、夏 6–8、秋 9–11；另加「全年」。
- 每格（站×季節×日型×小時）記錄有效樣本數 n；n < MIN_N 的格子輸出 null，網頁標「資料較少」。
- 路邊加成：交通站 − 3 km 內一般站的平均，「同一小時配對相減」後，再依季節×日型×小時平均。
"""
import glob, json, math
import pandas as pd
from pyproj import Transformer

RAW = 'data/raw/'
POLS = ['AQI', 'PM2.5', 'NO2', 'O3', 'PM10', 'CO']
SEASON = {12: 'winter', 1: 'winter', 2: 'winter', 3: 'spring', 4: 'spring', 5: 'spring',
          6: 'summer', 7: 'summer', 8: 'summer', 9: 'autumn', 10: 'autumn', 11: 'autumn'}
SEASONS = ['all', 'winter', 'spring', 'summer', 'autumn']
DAYS = ['weekday', 'holiday']
MIN_N = 10

files = sorted(glob.glob(RAW + '20*_%e5%b0%8f*.ods'))
assert len(files) == 12, files
df = pd.concat([pd.read_excel(f, engine='odf') for f in files], ignore_index=True)
df['t'] = pd.to_datetime(df['日期'], format='%Y/%m/%d %H:%M')
for c in POLS:
    df[c] = pd.to_numeric(df[c], errors='coerce')
df.loc[df['AQI'] == 0, 'AQI'] = float('nan')

cal = pd.read_csv(RAW + 'dgpa/calendar_115.csv', dtype={'西元日期': str})
hol = set(pd.to_datetime(cal.loc[cal['是否放假'] == 2, '西元日期']).dt.date)
work = set(pd.to_datetime(cal.loc[cal['是否放假'] == 0, '西元日期']).dt.date)
d = df['t'].dt.date
is_2026 = df['t'].dt.year == 2026
df['daytype'] = 'weekday'
df.loc[is_2026 & d.isin(hol), 'daytype'] = 'holiday'
df.loc[~is_2026 & (df['t'].dt.day_of_week >= 5), 'daytype'] = 'holiday'
assert not (is_2026 & ~d.isin(hol | work)).any()
df['hour'] = df['t'].dt.hour
df['season'] = df['t'].dt.month.map(SEASON)
s = pd.concat([df, df.assign(season='all')])

g = s.groupby(['測站', 'season', 'daytype', 'hour'])
mean, cnt = g[POLS].mean(), g[POLS].count()

st = pd.read_csv(RAW + '臺北市空氣品質監測站基本資料.csv', encoding='big5')
st = st[st['station_type_測站種類'] == '自動測站']
stations = [{'name': r['station_name_測站名稱'], 'type': '交通站' if r['classification_測站分類'] == '交通測站' else '一般站',
             'addr': r['station_address_測站地址'], 'lon': r['longitude_經度'], 'lat': r['latitude_緯度']} for _, r in st.iterrows()]
assert len(stations) == 12
rnd = lambda p, v: None if v is None or pd.isna(v) else round(float(v), 2 if p == 'CO' else 1)

# AQ_H[pol][season][daytype] = { v: [24 小時][12 站], n: [24][12] }
out = {p: {se: {dt: {'v': [], 'n': []} for dt in DAYS} for se in SEASONS} for p in POLS}
low = 0
for p in POLS:
    for se in SEASONS:
        for dt in DAYS:
            for h in range(24):
                vs, ns = [], []
                for stn in stations:
                    k = (stn['name'], se, dt, h); n = int(cnt[p].get(k, 0)); v = mean[p].get(k)
                    ns.append(n); vs.append(rnd(p, v) if n >= MIN_N else None)
                    low += n < MIN_N
                out[p][se][dt]['v'].append(vs); out[p][se][dt]['n'].append(ns)

# ---------- 路邊加成（逐小時） ----------
_t = Transformer.from_crs(4326, 3826, always_xy=True)
xy = {x['name']: _t.transform(x['lon'], x['lat']) for x in stations}
NEAR_KM = 3
traffic = [x['name'] for x in stations if x['type'] == '交通站']
general = [x['name'] for x in stations if x['type'] == '一般站']
neigh = {tn: [gg for gg in general if math.dist(xy[tn], xy[gg]) <= NEAR_KM * 1000] for tn in traffic}
keys = df.drop_duplicates('t').set_index('t')[['daytype', 'hour', 'season']]
rs = {}
for p in POLS:
    wide = df.pivot_table(index='t', columns='測站', values=p)
    diffs = {tn: (wide[tn] - wide[neigh[tn]].mean(axis=1, skipna=True)) for tn in traffic}
    rs[p] = {}
    for se in SEASONS:
        rs[p][se] = {}
        for dt in DAYS:
            M, PER, N = [], [], []
            for h in range(24):
                m = (keys['daytype'] == dt) & (keys['hour'] == h) & ((keys['season'] == se) if se != 'all' else True)
                idx = keys.index[m]
                per, ns = [], []
                for tn in traffic:
                    dd = diffs[tn].reindex(idx).dropna()
                    per.append(round(float(dd.mean()), 2) if len(dd) >= MIN_N else None); ns.append(int(len(dd)))
                ok = [v for v in per if v is not None]
                M.append(round(sum(ok) / len(ok), 2) if ok else None); PER.append(per); N.append(ns)
            rs[p][se][dt] = {'mean': M, 'per': PER, 'n': N}
roadside = {'traffic': traffic, 'neighbors': neigh, 'near_km': NEAR_KM, 'values': rs}

# ---------- 舊版 5 時段彙總（相容階段二網頁） ----------
SLOTS = {'dawn': (5, 7), 'am_peak': (7, 9), 'noon': (11, 13), 'pm_peak': (17, 19), 'night': (19, 22)}
df['slot'] = None
for k, (a, b) in SLOTS.items():
    df.loc[(df.hour >= a) & (df.hour < b), 'slot'] = k
ss = df.dropna(subset=['slot']); ss = pd.concat([ss, ss.assign(season='all')])
gs = ss.groupby(['測站', 'season', 'daytype', 'slot']); ms, cs = gs[POLS].mean(), gs[POLS].count()
slot_vals = {p: {se: {dt: {sl: {'v': [rnd(p, ms[p].get((x['name'], se, dt, sl))) if cs[p].get((x['name'], se, dt, sl), 0) >= MIN_N else None for x in stations],
                                 'n': [int(cs[p].get((x['name'], se, dt, sl), 0)) for x in stations]} for sl in SLOTS} for dt in DAYS} for se in SEASONS} for p in POLS}
keys_s = df.drop_duplicates('t').set_index('t')[['daytype', 'slot', 'season']]
srs = {}
for p in POLS:
    wide = df.pivot_table(index='t', columns='測站', values=p)
    diffs = {tn: (wide[tn] - wide[neigh[tn]].mean(axis=1, skipna=True)) for tn in traffic}
    srs[p] = {se: {dt: {} for dt in DAYS} for se in SEASONS}
    for se in SEASONS:
        for dt in DAYS:
            for sl in SLOTS:
                m = (keys_s['daytype'] == dt) & (keys_s['slot'] == sl) & ((keys_s['season'] == se) if se != 'all' else True)
                idx = keys_s.index[m]; per, ns = [], []
                for tn in traffic:
                    dd = diffs[tn].reindex(idx).dropna(); per.append(round(float(dd.mean()), 2) if len(dd) >= MIN_N else None); ns.append(int(len(dd)))
                ok = [v for v in per if v is not None]
                srs[p][se][dt][sl] = {'per': per, 'n': ns, 'mean': round(sum(ok) / len(ok), 2) if ok else None}
slot_rs = {'traffic': traffic, 'neighbors': neigh, 'near_km': NEAR_KM, 'values': srs}

meta = {'period': f"{df['t'].min():%Y-%m-%d} – {df['t'].max():%Y-%m-%d}", 'rows': int(len(df)), 'aqi_zero_as_missing': 90, 'min_n': MIN_N,
        'holiday_note': '2026 年依人事總處 115 年辦公日曆；2025-11、12 月缺 114 年日曆，暫以週六日為假日',
        'days': {dt: int(df.loc[df.daytype == dt, 't'].dt.date.nunique()) for dt in DAYS}, 'low_cells': int(low)}
with open('data/web/aq_data.js', 'w', encoding='utf-8') as f:
    f.write('// 由 scripts/build_aq.py 產生，勿手改。AQ_H[指標][季節][日型] = { v:[24 小時][12 站], n:[24][12] }\n')
    f.write('window.AQ_META=' + json.dumps(meta, ensure_ascii=False) + ';\n')
    f.write('window.AQ_STATIONS=' + json.dumps(stations, ensure_ascii=False) + ';\n')
    f.write('window.AQ_H=' + json.dumps(out, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + ';\n')
    f.write('window.AQ_ROADSIDE_H=' + json.dumps(roadside, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + ';\n')
    # ---- 舊版 5 時段格式（相容階段二網頁）：同一套資料，依時段（含起、不含迄）彙總 ----
    f.write('window.AQ_VALUES=' + json.dumps(slot_vals, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + ';\n')
    f.write('window.AQ_ROADSIDE=' + json.dumps(slot_rs, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + ';\n')

# ---------- 驗收用摘要 ----------
print(meta)
nmin = cnt.groupby(level=['season', 'daytype']).min()
print('各季節×日型，單格最少樣本數（AQI）：'); print(nmin['AQI'].unstack().to_string())
print('樣本 < 10 的格子數：', low, '／', len(POLS) * len(SEASONS) * len(DAYS) * 24 * 12)
for p in ['AQI', 'NO2', 'CO']:
    print(p, '路邊加成（全年平日，0–23 時）：', rs[p]['all']['weekday']['mean'])
    print(p, '路邊加成（全年假日，0–23 時）：', rs[p]['all']['holiday']['mean'])
zb = [out['NO2']['all']['weekday']['v'][h][[x['name'] for x in stations].index('中北站')] for h in range(24)]
print('中北站 NO2 全年平日 0–23 時：', zb)
