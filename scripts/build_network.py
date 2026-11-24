"""階段二路網：osmnx 下載臺北市步行路網與相關 OSM 圖資，替每段路打分數，輸出 data/web/network.js。

排除不適合跑步的道路：highway=motorway/motorway_link/trunk/trunk_link（快速道路、市區高架多標為 trunk），
以及 bridge=viaduct（高架橋）。排除後只保留最大連通網路。

每段路（無向；同一對節點只留最短的一條）輸出欄位，見 FIELDS。
OSM 原始屬性另存 data/interim/edges_attrs.pkl，給 scripts/osm_coverage.py 算覆蓋率。
空品不在這裡算：瀏覽器依情境用 9 個一般站 IDW 推估到路段中點，再加路邊加成。
"""
import json, math, time
import numpy as np, pandas as pd, geopandas as gpd, osmnx as ox
from shapely import STRtree
from shapely.ops import unary_union

ox.settings.use_cache = True          # 快取在 ./cache（已列入 .gitignore），重跑時不必重抓
ox.settings.cache_folder = 'data/interim/osmnx_cache'
ox.settings.log_console = False
ox.settings.requests_timeout = 300
ox.settings.useful_tags_way = list(dict.fromkeys(ox.settings.useful_tags_way + [
    'sidewalk', 'sidewalk:both', 'sidewalk:left', 'sidewalk:right', 'footway', 'bridge', 'tunnel', 'layer', 'foot', 'surface']))
CRS = 3826
t0 = time.time()
def log(*a): print(f'[{time.time()-t0:6.0f}s]', *a, flush=True)

dist = gpd.GeoDataFrame.from_features(json.load(open('data/raw/臺北市行政區界線.geojson', encoding='utf-8'))['features'], crs=4326)
poly = unary_union(dist.geometry)
poly_q = poly.buffer(0.002).simplify(0.003)   # Overpass 查詢用的簡化外框（原邊界太細會回 413）

def first(v):  # osmnx 合併路段時屬性可能是 list
    if isinstance(v, list): return v[0] if v else None
    return None if (isinstance(v, float) and math.isnan(v)) else v
def anyin(v, vals):
    vs = v if isinstance(v, list) else [v]
    return any(x in vals for x in vs)

# ---------- 步行路網與其他圖資：優先用離線檔（scripts/osm_from_pbf.py 的輸出），沒有才走 Overpass ----------
import os
EXCL_HW = {'motorway', 'motorway_link', 'trunk', 'trunk_link'}
PKL = 'data/interim/taipei_osm.pkl'
if os.path.exists(PKL):
    O = pd.read_pickle(PKL); SOURCE = 'Geofabrik ' + O['source']
    log('使用離線檔', SOURCE)
    edges = O['edges'].copy(); nodes = O['nodes']
    n0, e0 = len(nodes), len(edges)
    bad = edges.highway.isin(EXCL_HW) | (edges.get('bridge') == 'viaduct')
    drop_n, excl_len = int(bad.sum()), float(edges.loc[bad, 'length'].sum())
    edges = edges[~bad].copy()
    # 最大連通網路（union-find）
    parent = {}
    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x]); x = parent[x]
        return x
    for u, v in zip(edges.u, edges.v):
        ru, rv = find(u), find(v)
        if ru != rv: parent[ru] = rv
    root = pd.Series([find(u) for u in edges.u], index=edges.index)
    big = root.value_counts().idxmax()
    edges = edges[root == big].copy()
    nodes = nodes.loc[pd.unique(pd.concat([edges.u, edges.v]))]
    log(f'nodes {n0}→{len(nodes)}, edges {e0}→{len(edges)}；排除快速道路/高架 {drop_n} 段、約 {excl_len/1000:.1f} km')
    roads, parks, comm, shops, water = O['roads'].copy(), O['parks'].copy(), O['comm'].copy(), O['shops'].copy(), O['water'].copy()
    shops['name'] = shops['name']
else:
    SOURCE = 'Overpass（osmnx）'
    log('下載步行路網 walk …')
    G = ox.graph_from_polygon(poly, network_type='walk', simplify=True, retain_all=True)
    n0, e0 = len(G.nodes), len(G.edges)
    drop = [(u, v, k) for u, v, k, d in G.edges(keys=True, data=True)
            if anyin(d.get('highway'), EXCL_HW) or anyin(d.get('bridge'), {'viaduct'})]
    drop_n, excl_len = len(drop) // 2, sum(G.edges[e]['length'] for e in drop) / 2
    G.remove_edges_from(drop)
    G = ox.truncate.largest_component(G, strongly=False)
    log(f'nodes {n0}→{len(G.nodes)}, edges {e0}→{len(G.edges)}；排除快速道路/高架 {drop_n} 段、約 {excl_len/1000:.1f} km')
    nodes, edges = ox.graph_to_gdfs(G)
    del G
    edges = edges.reset_index()
    MIRRORS = ['https://overpass-api.de/api', 'https://overpass.kumi.systems/api']
    def feats(tags, label):
        for attempt in range(6):   # Overpass 偶爾拒絕連線：等一下再試，連續失敗就換鏡像站
            ox.settings.overpass_url = MIRRORS[min(attempt // 3, 1)]
            try:
                f = ox.features_from_polygon(poly_q, tags=tags); log(label, len(f), '（', ox.settings.overpass_url, '）'); return f
            except Exception as ex:
                log(label, '失敗，重試', attempt + 1, type(ex).__name__); time.sleep(30)
        raise RuntimeError(label + ' 下載失敗')
    roads = feats({'highway': ['motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary', 'primary_link', 'secondary', 'secondary_link']}, '幹道 ways')
    roads = roads[roads.geometry.geom_type.isin(['LineString', 'MultiLineString'])].copy()
    parks = feats({'leisure': 'park'}, 'OSM 公園')
    parks = parks[parks.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])].copy()
    comm = feats({'landuse': ['commercial', 'retail']}, '商業區 landuse')
    comm = comm[comm.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])].copy()
    shops = feats({'shop': True}, '商店 shop')
    water = feats({'natural': 'water', 'waterway': 'riverbank'}, '水域')
    water = water[water.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])].copy()
    if 'water' in water: water = water[(water['water'].isin(['river', 'canal'])) | (water.get('waterway') == 'riverbank')]
roads['hw'] = roads['highway'].astype(str)
log('幹道', len(roads), '公園', len(parks), '商業區', len(comm), '商店', len(shops), '河流水域面', len(water))
edges['a'] = edges[['u', 'v']].min(axis=1); edges['b'] = edges[['u', 'v']].max(axis=1)
edges = edges[edges.a != edges.b].sort_values('length').drop_duplicates(['a', 'b']).reset_index(drop=True)
for c in ['highway', 'sidewalk', 'sidewalk:both', 'sidewalk:left', 'sidewalk:right', 'footway', 'bridge', 'name']:
    if c not in edges: edges[c] = None
edges['hw'] = edges['highway'].map(first)
log('無向路段', len(edges), '總長', round(float(edges['length'].sum()) / 1000, 1), 'km')

# ---------- 投影與幾何量測 ----------
e = edges.to_crs(CRS)
mid = e.geometry.interpolate(0.5, normalized=True).values

def nearest(geoms):
    out = np.full(len(mid), 999.0)
    if len(geoms):
        idx, d = STRtree(geoms).query_nearest(mid, return_distance=True, all_matches=False)
        out[idx[0]] = np.minimum(d, 999)
    return out.round().astype(int)
r = roads.to_crs(CRS)
e['d1'] = nearest(r[r.hw.isin(['motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary', 'primary_link'])].geometry.values)
e['d2'] = nearest(r[r.hw.isin(['secondary', 'secondary_link'])].geometry.values)

cols = ['TreeHeight', 'TWD97X', 'TWD97Y']
trees = pd.concat([pd.read_csv('data/raw/TaipeiTree.csv', usecols=cols), pd.read_csv('data/raw/TaipeiParkTree.csv', usecols=cols)])
trees = trees[(trees.TreeHeight > 0) & (trees.TreeHeight <= 40)]
pairs = STRtree(gpd.points_from_xy(trees.TWD97X, trees.TWD97Y)).query(e.geometry.values, predicate='dwithin', distance=20)
e['trees'] = np.bincount(pairs[0], minlength=len(e))

# 公園：OSM 公園面 ＋ 使用者公園點等面積圓；記錄公園編號（算「經過幾個公園」）
pk = pd.json_normalize(json.load(open('data/raw/臺北市公園基本資料.json', encoding='utf-8')))
pk['lon'] = pd.to_numeric(pk.pm_Longitude, errors='coerce'); pk['lat'] = pd.to_numeric(pk.pm_Latitude, errors='coerce')
pk['area'] = pd.to_numeric(pk.pm_LandPublicArea, errors='coerce')
pk = pk.dropna(subset=['lon', 'lat', 'area']); pk = pk[pk.lon.between(121.4, 121.7) & pk.lat.between(24.9, 25.25)]
pk_g = gpd.GeoDataFrame(pk, geometry=gpd.points_from_xy(pk.lon, pk.lat), crs=4326).to_crs(CRS)
park_geoms = list(parks.to_crs(CRS).geometry.values) + list(pk_g.geometry.buffer(np.sqrt(pk_g.area / math.pi)).values)
pname = parks['name'] if 'name' in parks else pd.Series(index=parks.index, dtype=object)
park_names = [str(n) if isinstance(n, str) else 'OSM 公園（無名）' for n in pname] + list(pk_g.pm_name)
hit = STRtree(park_geoms).query(mid, predicate='within')
pid = np.full(len(e), -1)
for i, j in zip(*hit):   # 多個公園重疊時取第一個
    if pid[i] < 0: pid[i] = j
# 同名公園合併成同一編號
uniq = {}; pid2 = np.array([uniq.setdefault(park_names[j], len(uniq)) if j >= 0 else -1 for j in pid])
park_list = list(uniq.keys())
e['pid'] = pid2

# 河濱：中點在名稱含「河濱」的公園內，或距河流水域面 ≤ 100 m
riv_park = np.array([j >= 0 and '河濱' in park_names[j] for j in pid])
e['river'] = ((nearest(water.to_crs(CRS).geometry.values) <= 100) | riv_park).astype(int)

# 商圈：中點在 landuse=commercial/retail 內，以及 50 m 內 shop 點數
cm = comm.to_crs(CRS).geometry.values
e['comm'] = 0
if len(cm):
    h = STRtree(cm).query(mid, predicate='within'); e.loc[np.unique(h[0]), 'comm'] = 1
sp = shops.to_crs(CRS).geometry.representative_point().values
sh = STRtree(sp).query(mid, predicate='dwithin', distance=50)
e['shops'] = np.bincount(sh[0], minlength=len(e))

# 人行道：footway/pedestrian，或 sidewalk(:both/:left/:right) ∈ {yes, both, left, right, separate}，或 footway=sidewalk
SW = {'yes', 'both', 'left', 'right', 'separate'}
def has_sw(row):
    if anyin(row['highway'], {'footway', 'pedestrian'}): return 1
    for c in ['sidewalk', 'sidewalk:both', 'sidewalk:left', 'sidewalk:right']:
        if anyin(row[c], SW): return 1
    if anyin(row['footway'], {'sidewalk'}): return 1
    return 0
e['sw'] = edges.apply(has_sw, axis=1)
HW_CODE = {'footway': 1, 'pedestrian': 1, 'path': 2, 'cycleway': 2, 'track': 2, 'steps': 3, 'living_street': 4, 'residential': 4, 'service': 4, 'unclassified': 4,
           'tertiary': 5, 'tertiary_link': 5, 'secondary': 6, 'secondary_link': 6, 'primary': 7, 'primary_link': 7}
e['hwc'] = edges['hw'].map(HW_CODE).fillna(0).astype(int)

import os
os.makedirs('data/interim', exist_ok=True)
pd.to_pickle(pd.DataFrame(edges.drop(columns='geometry')).assign(length_m=edges['length'], sw=e.sw, comm=e.comm, shops=e.shops, river=e.river, pid=e.pid, d1=e.d1, d2=e.d2, trees=e.trees),
             'data/interim/edges_attrs.pkl')
pd.to_pickle({'shops': len(shops), 'shop_named': int(shops['name'].notna().sum()) if 'name' in shops else 0,
              'shop_pts': shops.to_crs(4326).geometry.representative_point(), 'comm_area_km2': float(comm.to_crs(CRS).area.sum() / 1e6),
              'n_comm': len(comm), 'n_parks_osm': len(parks), 'n_water': len(water)}, 'data/interim/osm_misc.pkl')

# ---------- 輸出 ----------
used = pd.unique(pd.concat([edges.u, edges.v]))
nid = {n: i for i, n in enumerate(used)}
nd = nodes.loc[used]
node_arr = [[round(x * 1e5), round(y * 1e5)] for x, y in zip(nd.geometry.x, nd.geometry.y)]
FIELDS = ['u', 'v', '長度m', '20m內樹數', '公園編號(-1無)', '離快速/主要道路m', '離次要道路m', '人行道0/1', '商業區0/1', '50m內商店數', '河濱0/1', '道路類別碼']
edge_arr = [[nid[u], nid[v], round(float(L), 1), int(t), int(p), int(a), int(b), int(s), int(c), int(sh_), int(rv), int(hc)]
            for u, v, L, t, p, a, b, s, c, sh_, rv, hc in zip(edges.u, edges.v, edges['length'], e.trees, e.pid, e.d1, e.d2, e.sw, e.comm, e.shops, e.river, e.hwc)]
# 路段形狀（u→v），簡化到約 2 m，只存中間點，經緯度×1e5 整數
geo = []
for g in edges.geometry.simplify(0.00002):
    cs = list(g.coords)[1:-1]
    geo.append([v for x, y in cs for v in (round(x * 1e5), round(y * 1e5))])
meta = {'downloaded': time.strftime('%Y-%m-%d'), 'nodes': len(node_arr), 'edges': len(edge_arr),
        'total_km': round(float(edges['length'].sum()) / 1000, 1), 'excluded_segments': drop_n, 'source': SOURCE, 'excluded_km': round(excl_len / 1000, 1),
        'parks': len(park_list), 'shops': len(shops), 'fields': FIELDS,
        'hw_codes': {'1': '人行道/徒步區', '2': '步道/自行車道', '3': '階梯', '4': '巷道/服務道路', '5': '三級道路', '6': '次要道路', '7': '主要道路', '0': '其他'}}
with open('data/web/network.js', 'w', encoding='utf-8') as f:
    f.write('// 由 scripts/build_network.py 產生；路網與圖資 © OpenStreetMap contributors (ODbL)\n')
    f.write('window.NET_META=' + json.dumps(meta, ensure_ascii=False) + ';\n')
    f.write('window.NET_PARKS=' + json.dumps(park_list, ensure_ascii=False) + ';\n')
    f.write('window.NET_NODES=' + json.dumps(node_arr, separators=(',', ':')) + ';\n')
    f.write('window.NET_EDGES=' + json.dumps(edge_arr, separators=(',', ':')) + ';\n')
    f.write('window.NET_GEOM=' + json.dumps(geo, separators=(',', ':')) + ';\n')
log('完成', {k: meta[k] for k in ['nodes', 'edges', 'total_km', 'excluded_segments', 'excluded_km', 'parks', 'shops']})
log('人行道比例', round(float((e.sw * edges['length']).sum() / edges['length'].sum()), 3),
    '商業區', round(float(e.comm.mean()), 3), '河濱', round(float((e.river * edges['length']).sum() / edges['length'].sum()), 3))
