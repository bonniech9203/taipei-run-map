"""IDW 推估用網格：500 m 方格（EPSG:3826），只保留中心點落在臺北市行政區內的格子。
輸出 data/web/grid.js：
- GRID: [{lon, lat, d: 行政區索引, dist: [到 12 站的距離 m]}]
- DISTRICTS: 行政區界線 GeoJSON（加上 has_station、stations 屬性）
IDW 本身（power=2，用該情境有值的測站）在瀏覽器計算；行政區值＝區內格子 IDW 值的平均。
"""
import json, math
from pyproj import Transformer

CELL = 500
to97 = Transformer.from_crs(4326, 3826, always_xy=True)
to84 = Transformer.from_crs(3826, 4326, always_xy=True)

g = json.load(open('data/raw/臺北市行政區界線.geojson', encoding='utf-8'))

def rings(geom):
    return geom['coordinates'] if geom['type'] == 'Polygon' else [r for p in geom['coordinates'] for r in p]

def inside(lon, lat, geom):
    polys = [geom['coordinates']] if geom['type'] == 'Polygon' else geom['coordinates']
    for poly in polys:
        def pip(ring):
            c = False
            for i in range(len(ring)):
                x1, y1 = ring[i - 1][:2]; x2, y2 = ring[i][:2]
                if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
                    c = not c
            return c
        if pip(poly[0]) and not any(pip(h) for h in poly[1:]):
            return True
    return False

# 測站（與 build_aq.py 同順序）
import csv
with open('data/raw/臺北市空氣品質監測站基本資料.csv', encoding='big5') as f:
    st = [r for r in csv.DictReader(f) if r['station_type_測站種類'] == '自動測站']
assert len(st) == 12
st_xy = [to97.transform(float(r['longitude_經度']), float(r['latitude_緯度'])) for r in st]

names = [ft['properties']['TOWNNAME'] for ft in g['features']]
for i, ft in enumerate(g['features']):
    inn = [r['station_name_測站名稱'] for r in st if inside(float(r['longitude_經度']), float(r['latitude_緯度']), ft['geometry'])]
    ft['properties'] = {'name': names[i], 'code': ft['properties']['TOWNCODE'], 'idx': i,
                        'has_station': bool(inn), 'stations': '、'.join(inn)}

xs = [to97.transform(c[0], c[1])[0] for ft in g['features'] for r in rings(ft['geometry']) for c in r]
ys = [to97.transform(c[0], c[1])[1] for ft in g['features'] for r in rings(ft['geometry']) for c in r]
grid = []
x = math.floor(min(xs) / CELL) * CELL + CELL / 2
while x < max(xs):
    y = math.floor(min(ys) / CELL) * CELL + CELL / 2
    while y < max(ys):
        lon, lat = to84.transform(x, y)
        for i, ft in enumerate(g['features']):
            if inside(lon, lat, ft['geometry']):
                grid.append({'lon': round(lon, 5), 'lat': round(lat, 5), 'd': i,
                             'dist': [round(math.hypot(x - sx, y - sy)) for sx, sy in st_xy]})
                break
        y += CELL
    x += CELL

per = {n: sum(1 for c in grid if c['d'] == i) for i, n in enumerate(names)}
print('cells', len(grid), per)
print({ft['properties']['name']: ft['properties']['stations'] or '（無測站）' for ft in g['features']})
with open('data/web/grid.js', 'w', encoding='utf-8') as f:
    f.write('// 由 scripts/build_grid.py 產生，勿手改；網格 500 m（EPSG:3826）\n')
    f.write('window.GRID_CELL_M=%d;\n' % CELL)
    f.write('window.GRID=' + json.dumps(grid, separators=(',', ':')) + ';\n')
    f.write('window.DISTRICTS=' + json.dumps(g, ensure_ascii=False, separators=(',', ':')) + ';\n')
