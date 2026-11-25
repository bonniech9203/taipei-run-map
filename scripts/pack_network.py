"""把 build_network.py 產生的 data/web/network.js 重新打包成較小、解析較快的 data/web/network.json。

為什麼：network.js 是 9.4 MB 的 JavaScript，手機要整份解析，慢且吃記憶體。改成 JSON（解析快）＋差值編碼：
- 節點依 Z-order（空間）排序，座標存「和前一個節點的差」（經緯度 × 1e5 整數）
- 路段依起點排序；u 存「和前一段的差」，v 存「v − u」
- 路段形狀的中間點存「和前一點的差」，第一點相對於起點節點
- 旗標合併：sw | comm<<1 | river<<2
同時輸出 data/web/net_manifest.js（未壓縮位元組數，供網頁顯示下載進度）。
"""
import json, re

t = open('data/web/network.js', encoding='utf-8').read()
get = lambda name: json.loads(re.search(r'window\.' + name + r'=(.*?);\n', t, re.S).group(1))
META, PARKS, N, E, GEOM = get('NET_META'), get('NET_PARKS'), get('NET_NODES'), get('NET_EDGES'), get('NET_GEOM')

def morton(x, y):
    def part(n):
        n &= 0xFFFF; n = (n | (n << 8)) & 0x00FF00FF; n = (n | (n << 4)) & 0x0F0F0F0F; n = (n | (n << 2)) & 0x33333333; n = (n | (n << 1)) & 0x55555555; return n
    return part(x) | (part(y) << 1)
x0 = min(p[0] for p in N); y0 = min(p[1] for p in N)
order = sorted(range(len(N)), key=lambda i: morton((N[i][0] - x0) // 8, (N[i][1] - y0) // 8))
new_id = {old: new for new, old in enumerate(order)}
nx, ny, px, py = [], [], 0, 0
for old in order:
    x, y = N[old]; nx.append(x - px); ny.append(y - py); px, py = x, y

# 路段：u<v，依 (u, v) 排序；形狀方向跟著調整成 u→v
edges = []
for k, e in enumerate(E):
    u, v = new_id[e[0]], new_id[e[1]]; g = GEOM[k]
    pts = [(g[j], g[j + 1]) for j in range(0, len(g), 2)]
    if u > v: u, v = v, u; pts = pts[::-1]
    edges.append((u, v, e, pts))
edges.sort(key=lambda r: (r[0], r[1]))
cols = {c: [] for c in ['du', 'dv', 'len', 'tr', 'pid', 'd1', 'd2', 'fl', 'sh', 'hw', 'gc', 'gx', 'gy']}
pu = 0
NX = [N[o][0] for o in order]; NY = [N[o][1] for o in order]
for u, v, e, pts in edges:
    cols['du'].append(u - pu); pu = u; cols['dv'].append(v - u)
    cols['len'].append(round(e[2])); cols['tr'].append(e[3]); cols['pid'].append(e[4]); cols['d1'].append(e[5]); cols['d2'].append(e[6])
    cols['fl'].append(e[7] | (e[8] << 1) | (e[10] << 2)); cols['sh'].append(e[9]); cols['hw'].append(e[11])
    cols['gc'].append(len(pts)); qx, qy = NX[u], NY[u]
    for x, y in pts: cols['gx'].append(x - qx); cols['gy'].append(y - qy); qx, qy = x, y
out = {'meta': META, 'parks': PARKS, 'nx': nx, 'ny': ny, **cols}
s = json.dumps(out, ensure_ascii=False, separators=(',', ':'))
open('data/web/network.json', 'w', encoding='utf-8').write(s)
nbytes = len(s.encode('utf-8'))
open('data/web/net_manifest.js', 'w').write('// 由 scripts/pack_network.py 產生：network.json 未壓縮大小（顯示下載進度用）\nwindow.NET_BYTES=%d;\n' % nbytes)
import gzip
print('network.json', round(nbytes / 1e6, 2), 'MB，gzip', round(len(gzip.compress(s.encode())) / 1e6, 2), 'MB；節點', len(N), '路段', len(E))
