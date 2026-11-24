"""階段一用的瀏覽器圖資：
- data/web/trees.js ：行道樹＋公園樹點位（WGS84 × 1e5 整數，扁平陣列），去掉樹高 ≤ 0 或 > 40 m
- data/web/parks.js ：OSM leisure=park 面（簡化到約 2 m）＋使用者公園點換成等面積圓（中心、半徑 m）
- data/web/roads.js ：OSM 幹道線（class 1＝motorway/trunk/primary 含 _link；class 2＝secondary 含 _link）
"""
import json, math, time
import numpy as np, pandas as pd, geopandas as gpd, osmnx as ox
from pyproj import Transformer
from shapely.ops import unary_union

ox.settings.use_cache = False
ox.settings.requests_timeout = 300
t0 = time.time()
def log(*a): print(f'[{time.time()-t0:5.0f}s]', *a, flush=True)

dist = gpd.GeoDataFrame.from_features(json.load(open('data/raw/臺北市行政區界線.geojson', encoding='utf-8'))['features'], crs=4326)
poly = unary_union(dist.geometry)
poly_q = poly.buffer(0.002).simplify(0.003)

# ---------- 樹 ----------
cols = ['TreeHeight', 'TWD97X', 'TWD97Y']
trees = pd.concat([pd.read_csv('data/raw/TaipeiTree.csv', usecols=cols).assign(src=0),
                   pd.read_csv('data/raw/TaipeiParkTree.csv', usecols=cols).assign(src=1)])
n_all = len(trees)
trees = trees[(trees.TreeHeight > 0) & (trees.TreeHeight <= 40)]
lon, lat = Transformer.from_crs(3826, 4326, always_xy=True).transform(trees.TWD97X.values, trees.TWD97Y.values)
flat = np.column_stack([np.round(lon * 1e5), np.round(lat * 1e5)]).astype(int).ravel().tolist()
with open('data/web/trees.js', 'w') as f:
    f.write('// 由 scripts/build_layers.py 產生。來源：臺北市行道樹、公園樹（TWD97→WGS84），去掉樹高 ≤0 或 >40 m\n')
    f.write('window.TREES_META=' + json.dumps({'total': n_all, 'used': len(trees)}) + ';\n')
    f.write('window.TREES=[' + ','.join(map(str, flat)) + '];\n')
log('trees', n_all, '→', len(trees))

# ---------- 公園 ----------
log('下載 OSM 公園 …')
po = ox.features_from_polygon(poly_q, tags={'leisure': 'park'})
po = po[po.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])]
po = po[po.geometry.intersects(poly)]
po_m = po.to_crs(3826)
po['area_m2'] = po_m.area.round()
po['geometry'] = po.geometry.simplify(0.00002)
names = po['name'].fillna('') if 'name' in po else pd.Series('', index=po.index)
polys = []
for g, nm, a in zip(po.geometry, names, po.area_m2):
    parts = [g] if g.geom_type == 'Polygon' else list(g.geoms)
    for p in parts:
        polys.append({'n': nm, 'a': int(a), 'r': [[round(x * 1e5), round(y * 1e5)] for x, y in p.exterior.coords]})
pk = pd.json_normalize(json.load(open('data/raw/臺北市公園基本資料.json', encoding='utf-8')))
pk['lon'] = pd.to_numeric(pk.pm_Longitude, errors='coerce'); pk['lat'] = pd.to_numeric(pk.pm_Latitude, errors='coerce')
pk['area'] = pd.to_numeric(pk.pm_LandPublicArea, errors='coerce')
pk = pk.dropna(subset=['lon', 'lat', 'area'])
pk = pk[pk.lon.between(121.4, 121.7) & pk.lat.between(24.9, 25.25)]
circles = [[round(a), round(b, 5), round(math.sqrt(ar / math.pi)), n] for a, b, ar, n in zip(pk.lon * 0 + pk.lon, pk.lat, pk.area, pk.pm_name)]
circles = [[round(lo, 5), la, r, n] for (lo0, la, r, n), lo in zip(circles, pk.lon)]
with open('data/web/parks.js', 'w', encoding='utf-8') as f:
    f.write('// 由 scripts/build_layers.py 產生。PARK_POLYS：OSM leisure=park（© OpenStreetMap contributors）；PARK_CIRCLES：臺北市公園基本資料點＋面積換算的等面積圓 [經度, 緯度, 半徑m, 名稱]\n')
    f.write('window.PARK_POLYS=' + json.dumps(polys, ensure_ascii=False, separators=(',', ':')) + ';\n')
    f.write('window.PARK_CIRCLES=' + json.dumps(circles, ensure_ascii=False, separators=(',', ':')) + ';\n')
log('parks: OSM 面', len(polys), '使用者圓', len(circles), '；大安森林公園：', [p['a'] for p in polys if '大安森林' in p['n']])

# ---------- 幹道 ----------
log('下載幹道 …')
hw1 = ['motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary', 'primary_link']
hw2 = ['secondary', 'secondary_link']
rd = ox.features_from_polygon(poly_q, tags={'highway': hw1 + hw2})
rd = rd[rd.geometry.geom_type == 'LineString']
rd = rd[rd.geometry.intersects(poly.buffer(0.01))]
rd['geometry'] = rd.geometry.simplify(0.00002)
lines = [[1 if h in hw1 else 2, [[round(x * 1e5), round(y * 1e5)] for x, y in g.coords]] for g, h in zip(rd.geometry, rd.highway.astype(str))]
with open('data/web/roads.js', 'w', encoding='utf-8') as f:
    f.write('// 由 scripts/build_layers.py 產生。OSM 幹道（© OpenStreetMap contributors）：[等級 1＝快速/主要道路, 2＝次要道路, 座標×1e5]\n')
    f.write('window.ROADS=' + json.dumps(lines, separators=(',', ':')) + ';\n')
log('roads', len(lines), rd.highway.astype(str).value_counts().to_dict())
