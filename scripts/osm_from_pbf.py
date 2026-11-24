"""從 Geofabrik 臺灣 OSM 離線檔（.osm.pbf）切出臺北市步行路網與相關圖資，取代 Overpass（2026-11-24 Overpass 主站與鏡像站皆故障）。
輸出 data/interim/taipei_osm.pkl，給 scripts/build_network.py 使用（格式對齊 osmnx 的 graph_to_gdfs）。

步行路網篩選：照 osmnx 2.x 的 network_type='walk' 規則：
  有 highway，area≠yes，highway 不含 abandoned|bus_guideway|construction|corridor|elevator|escalator|motor|no|planned|platform|proposed|raceway|razed，
  foot≠no，service≠private，access≠private。
路口切段：節點被 ≥2 條路使用、或是路的端點，就切開（相當於 osmnx simplify）。
範圍：路段兩端點都在臺北市行政區內才保留（osmnx graph_from_polygon 也是用節點是否在範圍內截斷）。
"""
import json, math, re, sys, time
from collections import defaultdict
import numpy as np, pandas as pd, geopandas as gpd, shapely, osmium
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

PBF = sys.argv[1] if len(sys.argv) > 1 else 'data/raw/osm/taiwan-261123.osm.pbf'
t0 = time.time()
def log(*a): print(f'[{time.time()-t0:6.0f}s]', *a, flush=True)

dist = gpd.GeoDataFrame.from_features(json.load(open('data/raw/臺北市行政區界線.geojson', encoding='utf-8'))['features'], crs=4326)
poly = unary_union(dist.geometry); shapely.prepare(poly)
BB = (121.44, 24.95, 121.68, 25.22)   # 臺北市外框加一點緩衝
inb = lambda x, y: BB[0] <= x <= BB[2] and BB[1] <= y <= BB[3]

WALK_BAD = re.compile(r'abandoned|bus_guideway|construction|corridor|elevator|escalator|motor|no|planned|platform|proposed|raceway|razed')
def is_walk(t):
    h = t.get('highway')
    if not h or t.get('area') == 'yes' or WALK_BAD.search(h): return False
    if t.get('foot') == 'no' or t.get('service') == 'private' or t.get('access') == 'private': return False
    return True
MAJOR = {'motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary', 'primary_link', 'secondary', 'secondary_link'}
KEEP_TAGS = ['highway', 'name', 'sidewalk', 'sidewalk:both', 'sidewalk:left', 'sidewalk:right', 'footway', 'bridge', 'tunnel', 'layer', 'foot', 'service', 'access', 'surface']

walk_ways, road_lines, shops, areas = [], [], [], {'park': [], 'comm': [], 'water': [], 'shop': []}
wkb = osmium.geom.WKBFactory()
fp = osmium.FileProcessor(PBF).with_locations().with_areas(osmium.filter.KeyFilter('leisure', 'landuse', 'natural', 'waterway', 'shop'))
n_obj = 0
for o in fp:
    n_obj += 1
    if o.is_node():
        if 'shop' in o.tags and o.location.valid() and inb(o.location.lon, o.location.lat):
            shops.append((o.location.lon, o.location.lat, o.tags.get('name')))
    elif o.is_way():
        if 'highway' not in o.tags: continue
        try: coords = [(n.lon, n.lat) for n in o.nodes]
        except osmium.InvalidLocationError: continue
        if not any(inb(x, y) for x, y in coords): continue
        t = {k: o.tags.get(k) for k in KEEP_TAGS if k in o.tags}
        if t['highway'] in MAJOR: road_lines.append((t['highway'], LineString(coords)))
        if is_walk({**t, 'area': o.tags.get('area')}):
            walk_ways.append((o.id, [n.ref for n in o.nodes], coords, t))
    elif o.is_area():
        tg = o.tags
        kind = ('park' if tg.get('leisure') == 'park' else
                'comm' if tg.get('landuse') in ('commercial', 'retail') else
                'water' if (tg.get('natural') == 'water' and tg.get('water') in ('river', 'canal')) or tg.get('waterway') == 'riverbank' else
                'shop' if 'shop' in tg else None)
        if not kind: continue
        try: g = shapely.from_wkb(wkb.create_multipolygon(o))
        except Exception: continue
        x0, y0, x1, y1 = g.bounds
        if x1 < BB[0] or x0 > BB[2] or y1 < BB[1] or y0 > BB[3]: continue
        if kind == 'shop': shops.append((g.representative_point().x, g.representative_point().y, tg.get('name')))
        else: areas[kind].append((tg.get('name'), g))
    if n_obj % 5_000_000 == 0: log('objects', n_obj)
log('讀完 pbf：walk ways', len(walk_ways), '幹道', len(road_lines), '商店', len(shops), {k: len(v) for k, v in areas.items()})

# ---------- 路口切段 ----------
use = defaultdict(int)
for _, ids, _, _ in walk_ways:
    for r in ids: use[r] += 1
    use[ids[0]] += 1; use[ids[-1]] += 1          # 端點一定是節點
node_xy, rows = {}, []
def hav(a, b):
    R = 6371008.8; p1, p2 = math.radians(a[1]), math.radians(b[1]); dp = p2 - p1; dl = math.radians(b[0] - a[0])
    return 2 * R * math.asin(math.sqrt(math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2))
for wid, ids, coords, t in walk_ways:
    start = 0
    for i in range(1, len(ids)):
        if use[ids[i]] >= 2 or i == len(ids) - 1:
            seg = coords[start:i + 1]
            if len(seg) >= 2 and ids[start] != ids[i]:
                L = sum(hav(seg[k], seg[k + 1]) for k in range(len(seg) - 1))
                rows.append({'u': ids[start], 'v': ids[i], 'osmid': wid, 'length': L, 'geometry': LineString(seg), **t})
                node_xy[ids[start]] = seg[0]; node_xy[ids[i]] = seg[-1]
            start = i
E = gpd.GeoDataFrame(rows, geometry='geometry', crs=4326)
log('切段後路段', len(E))
# 臺北市範圍：兩端點都在行政區內
ids = np.array(list(node_xy)); xy = np.array([node_xy[i] for i in ids])
inside = dict(zip(ids, shapely.contains_xy(poly, xy[:, 0], xy[:, 1])))
E = E[E.u.map(inside) & E.v.map(inside)].reset_index(drop=True)
log('臺北市範圍內路段', len(E), '總長', round(E.length.sum() if False else E['length'].sum() / 1000, 1), 'km')

nodes = gpd.GeoDataFrame({'osmid': list(node_xy)}, geometry=[Point(node_xy[i]) for i in node_xy], crs=4326).set_index('osmid')
roads = gpd.GeoDataFrame({'highway': [h for h, _ in road_lines]}, geometry=[g for _, g in road_lines], crs=4326)
mk = lambda L: gpd.GeoDataFrame({'name': [n for n, _ in L]}, geometry=[g for _, g in L], crs=4326)
shops_g = gpd.GeoDataFrame({'name': [n for _, _, n in shops]}, geometry=gpd.points_from_xy([x for x, _, _ in shops], [y for _, y, _ in shops]), crs=4326)
shops_g = shops_g[shapely.contains_xy(poly, shops_g.geometry.x.values, shops_g.geometry.y.values) | True]
import os; os.makedirs('data/interim', exist_ok=True)
pd.to_pickle({'edges': E, 'nodes': nodes, 'roads': roads, 'parks': mk(areas['park']), 'comm': mk(areas['comm']), 'water': mk(areas['water']),
              'shops': shops_g, 'source': PBF.split('/')[-1]}, 'data/interim/taipei_osm.pkl')
log('完成 → data/interim/taipei_osm.pkl')
