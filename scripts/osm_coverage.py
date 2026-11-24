"""檢查 OSM 在臺北的人行道（sidewalk）與商店（shop）標記完整度。
輸入：build_network.py 產生的 data/interim/edges_attrs.pkl、osm_misc.pkl
輸出：docs/osm_coverage.md、data/web/osm_coverage.js（網頁顯示用）

人行道覆蓋率：車行道路（primary/secondary/tertiary/residential/unclassified/living_street 與 _link）中，
  有 sidewalk / sidewalk:both|left|right 任一標記（含 no、none）的長度比例＝「有標記」；
  標記為 yes/both/left/right/separate 的長度比例＝「標記有人行道」。
  沒有標記 ≠ 沒有人行道，只代表 OSM 沒人填。
商店：沒有官方的商店點位可以比對，無法算真正的覆蓋率；改報告總數、各區密度（每 km 道路的商店數）與差異倍數。
"""
import json, math
import pandas as pd, geopandas as gpd

E = pd.read_pickle('data/interim/edges_attrs.pkl')
M = pd.read_pickle('data/interim/osm_misc.pkl')
first = lambda v: (v[0] if v else None) if isinstance(v, list) else v
E['hw'] = E['highway'].map(first)
CAR = ['primary', 'primary_link', 'secondary', 'secondary_link', 'tertiary', 'tertiary_link', 'residential', 'unclassified', 'living_street']
car = E[E.hw.isin(CAR)].copy()
def tagval(row):
    vals = []
    for c in ['sidewalk', 'sidewalk:both', 'sidewalk:left', 'sidewalk:right']:
        v = row.get(c)
        if isinstance(v, list): vals += [str(x) for x in v]
        elif isinstance(v, str): vals.append(v)
    return vals
car['tags'] = car.apply(tagval, axis=1)
car['tagged'] = car.tags.map(bool)
car['yes'] = car.tags.map(lambda t: any(x in {'yes', 'both', 'left', 'right', 'separate'} for x in t))
L = car.length_m.sum()
by_hw = car.groupby('hw').apply(lambda d: pd.Series({'km': d.length_m.sum() / 1000, '有標記%': d[d.tagged].length_m.sum() / d.length_m.sum() * 100,
                                                    '標記有人行道%': d[d.yes].length_m.sum() / d.length_m.sum() * 100}), include_groups=False).round(1).sort_values('km', ascending=False)
tot_len = E.length_m.sum()
foot_pct = E[E.hw.isin(['footway', 'pedestrian'])].length_m.sum() / tot_len * 100
sw_any_pct = (E.sw * E.length_m).sum() / tot_len * 100
cov = {'car_km': round(L / 1000, 1), 'tagged_pct': round(car[car.tagged].length_m.sum() / L * 100, 1), 'sidewalk_pct': round(car[car.yes].length_m.sum() / L * 100, 1),
       'footway_pct_of_network': round(foot_pct, 1), 'network_sw_pct': round(sw_any_pct, 1)}

# 商店：各區密度
dist = gpd.GeoDataFrame.from_features(json.load(open('data/raw/臺北市行政區界線.geojson', encoding='utf-8'))['features'], crs=4326)
pts = gpd.GeoDataFrame(geometry=M['shop_pts'].values, crs=4326)
j = gpd.sjoin(pts, dist[['TOWNNAME', 'geometry']], predicate='within')
cnt = j.TOWNNAME.value_counts()
# 各區道路長度：用路段中點不方便，這裡以面積密度與每千人（無人口資料對應年份，不算）→ 用每 km² 密度
area = dist.set_index('TOWNNAME').to_crs(3826).area / 1e6
dens = (cnt / area).round(1).sort_values(ascending=False)
ratio = round(float(dens.max() / dens.min()), 1)
cov.update({'shops': int(M['shops']), 'shops_in_taipei': int(cnt.sum()), 'shop_named_pct': round(M['shop_named'] / M['shops'] * 100, 1),
            'shop_density_max': f'{dens.index[0]} {dens.iloc[0]}/km²', 'shop_density_min': f'{dens.index[-1]} {dens.iloc[-1]}/km²', 'shop_density_ratio': ratio,
            'comm_areas': int(M['n_comm']), 'comm_km2': round(M['comm_area_km2'], 2)})
cov['shop_note'] = f"最高 {cov['shop_density_max']}、最低 {cov['shop_density_min']}，差 {ratio} 倍"

with open('data/web/osm_coverage.js', 'w', encoding='utf-8') as f:
    f.write('// 由 scripts/osm_coverage.py 產生\nwindow.OSM_COV=' + json.dumps(cov, ensure_ascii=False) + ';\n')
md = f"""# OSM 人行道與商店標記完整度（臺北市，{pd.Timestamp.today():%Y-%m-%d} 下載）

由 `scripts/osm_coverage.py` 產生。資料：osmnx 下載的步行路網（已排除快速道路與高架橋）、OSM `shop=*`、`landuse=commercial/retail`。

## 人行道（sidewalk）

車行道路（primary、secondary、tertiary、residential、unclassified、living_street）共 **{cov['car_km']} km**。

| 指標 | 數值 |
|---|---|
| 有任何 sidewalk 標記（含 `no`）的長度比例 | **{cov['tagged_pct']}%** |
| 標記為有人行道（yes/both/left/right/separate）的長度比例 | **{cov['sidewalk_pct']}%** |
| 獨立畫出的 footway／pedestrian 占整個步行路網長度 | {cov['footway_pct_of_network']}% |
| 步行路網中被判定為「人行道」的長度比例（footway＋pedestrian＋有標記的道路） | {cov['network_sw_pct']}% |

依道路等級：

{by_hw.to_markdown()}

**結論**：大多數道路**沒有 sidewalk 標記**。沒有標記不代表沒有人行道，只是 OSM 沒人填寫，所以「偏好人行道」只會偏向「OSM 有標記的地方」，會低估實際的人行道。

## 商店（shop）

| 指標 | 數值 |
|---|---|
| 查詢範圍內 shop 點／面 | {cov['shops']:,} |
| 落在臺北市行政區內 | {cov['shops_in_taipei']:,} |
| 有名稱的比例 | {cov['shop_named_pct']}% |
| 商業區面（landuse=commercial/retail） | {cov['comm_areas']} 個，共 {cov['comm_km2']} km² |

各區商店密度（每 km²）：

{dens.to_frame('每 km² 商店數').to_markdown()}

**結論**：沒有官方的商店點位資料可以比對，**無法算出真正的覆蓋率**。各區密度差 {ratio} 倍，一部分是真實差異（商業區集中），一部分可能是 OSM 志工在某些區標得比較多。所以「避開商圈」在標記少的區會失準。
"""
open('docs/osm_coverage.md', 'w', encoding='utf-8').write(md)
print(json.dumps(cov, ensure_ascii=False, indent=1)); print(by_hw); print(dens)
