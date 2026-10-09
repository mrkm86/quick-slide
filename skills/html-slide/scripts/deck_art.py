"""図形の飾り（layout: graphic）: 画像の無い表紙・章扉を、テーマの色の幾何学の図形で飾る。

- 図形はインラインの SVG の基本図形だけで描く（PPTX では図形のまま。あとから色や形を直せる）
- 色はテーマの変数（primary-decor・accent-decor・primary-strong・surface。飾りの面だけ・情報を持たせない）だけ。ブランドを替えれば図形の色も替わる
- 画面の端で切れて見える図形は、切れた形の多角形にして渡す（SVG の中の図形も section の外に出ると check が止める）。
  切れてよいのは画面の端だけで、文字の側の縁で切れる図形は置かない（切り損ねに見える）
- 図形は層に分け、層ごとに少しずつ遅れて出る（画面に入ったら自動。クリックの段は増やさない）
- 文字は載せない。飾りは文字の無い側にだけ置く（コントラストの検査は文字の側だけで済む）
"""

from __future__ import annotations

import hashlib
import math

MOTIFS = ("circles", "grid", "bands", "blocks")
MOTIF_WORDS = "circles（重なる円）・grid（点の格子と四角）・bands（斜めの帯）・blocks（色の面の組み合わせ）"


def default_motif(seed: str) -> str:
    """飾りの既定。資料の題から決める（同じ資料の表紙と章扉はそろい、資料ごとには替わる）。"""
    return MOTIFS[int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16) % len(MOTIFS)]


def _clip(pts, w, h):
    """凸の多角形を 0..w × 0..h に切る（Sutherland–Hodgman）。"""
    def cut(poly, inside, cross):
        out = []
        for i, p in enumerate(poly):
            q = poly[i - 1]
            if inside(p):
                if not inside(q):
                    out.append(cross(q, p))
                out.append(p)
            elif inside(q):
                out.append(cross(q, p))
        return out

    def at_x(x):
        return lambda a, b: (x, a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0]))

    def at_y(y):
        return lambda a, b: (a[0] + (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]), y)

    for inside, cross in ((lambda p: p[0] >= 0, at_x(0)), (lambda p: p[0] <= w, at_x(w)),
                          (lambda p: p[1] >= 0, at_y(0)), (lambda p: p[1] <= h, at_y(h))):
        pts = cut(pts, inside, cross)
        if not pts:
            return []
    return pts


def _paint(fill, op=1.0, stroke=None, sw=0):
    st = f"fill:{fill}" if fill else "fill:none"
    if fill and op < 1:
        st += f";fill-opacity:{op:g}"
    if stroke:
        st += f";stroke:{stroke};stroke-width:{sw:g}"
    return f'style="{st}"'


def _poly(pts, w, h, **paint):
    pts = _clip(pts, w, h)
    if len(pts) < 3:
        return ""
    return f'<polygon points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" {_paint(**paint)}/>'


def _circle(cx, cy, r, w, h, **paint):
    """円。画面の中に収まれば circle（PPTX の楕円）、端で切れるなら切った多角形。"""
    if cx - r >= 0 and cy - r >= 0 and cx + r <= w and cy + r <= h:
        return f'<circle cx="{cx:g}" cy="{cy:g}" r="{r:g}" {_paint(**paint)}/>'
    n = 120
    return _poly([(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)], w, h, **paint)


def _ring(cx, cy, r, w, h, color, sw):
    """輪（線だけの円）。端で切れるなら、画面の中の弧を折れ線にする。"""
    if cx - r - sw >= 0 and cy - r - sw >= 0 and cx + r + sw <= w and cy + r + sw <= h:
        return f'<circle cx="{cx:g}" cy="{cy:g}" r="{r:g}" {_paint(None, stroke=color, sw=sw)}/>'
    n, m = 180, sw / 2
    runs, cur = [], []
    for i in range(n + 1):
        x, y = cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)
        if m <= x <= w - m and m <= y <= h - m:
            cur.append((x, y))
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return "".join(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in run)}" {_paint(None, stroke=color, sw=sw)}/>'
                   for run in runs if len(run) > 1)


def _rect(x, y, rw, rh, **paint):
    """四角。線は辺の両側に描かれるので、線のある四角は線の半分だけ内側に寄せる（層の外へ線をはみ出させない）。"""
    m = paint.get("sw", 0) / 2 if paint.get("stroke") else 0
    return f'<rect x="{x + m:g}" y="{y + m:g}" width="{rw - 2 * m:g}" height="{rh - 2 * m:g}" {_paint(**paint)}/>'


P, A, PS, SF = "var(--primary-decor)", "var(--accent-decor)", "var(--primary-strong)", "var(--surface)"


def _circles(w, h):
    # 切れてよいのは画面の端（右・上・下）だけ。文字の側（左）の縁にかかる図形は置かない
    return [
        [_circle(w * 0.70, h * 0.66, h * 0.42, w, h, fill=P)],
        [_circle(w * 0.30, h * 0.27, h * 0.15, w, h, fill=P, op=0.25)],
        [_ring(w * 0.70, h * 0.66, h * 0.52, w, h, P, 4), _circle(w * 0.88, h * 0.15, h * 0.06, w, h, fill=A)],
        [_circle(w * 0.18, h * 0.62, 12, w, h, fill=A), _circle(w * 0.42, h * 0.50, 8, w, h, fill=P, op=0.6)],
    ]


def _grid(w, h):
    gap, r = 72, 7
    nx, ny = int((w - 2 * gap) // gap) + 1, int((h - 2 * gap) // gap) + 1
    ox, oy = (w - (nx - 1) * gap) / 2, (h - (ny - 1) * gap) / 2
    dots = [_circle(ox + i * gap, oy + j * gap, r, w, h, fill=P, op=0.28) for i in range(nx) for j in range(ny)]
    s = gap * 4
    bx, by = ox + gap * round((nx - 1) * 0.45), oy + gap * round((ny - 1) * 0.30)
    return [
        dots,
        [_rect(bx, by, s, s, fill=P)],
        [_rect(bx - gap * 2, by + gap * 3, gap * 3, gap * 3, fill=A, op=0.9)],
        [_rect(bx + gap * 2, by + gap * 5, gap * 2, gap * 2, fill=None, stroke=P, sw=6)],
    ]


def _bands(w, h):
    t = h * 0.55                          # 帯の傾き（横に 1 進むと上に t/w）
    def band(x0, bw, **paint):
        return _poly([(x0, h), (x0 + bw, h), (x0 + bw + t, 0), (x0 + t, 0)], w, h, **paint)
    return [
        [band(w * 0.30, w * 0.34, fill=P)],
        [band(w * 0.70, w * 0.16, fill=A, op=0.9)],
        [band(w * 0.02, w * 0.18, fill=P, op=0.18), band(w * 0.92, w * 0.40, fill=P, op=0.35)],
        [band(w * 0.66, 8, fill=PS)],
    ]


def _blocks(w, h):
    g = 24
    c1, c2 = w * 0.42, w * 0.70
    r1, r2 = h * 0.38, h * 0.70
    return [
        [_rect(c1 + g, 0, w - c1 - g, r2, fill=P)],
        [_rect(0, r1 + g, c1, h - r1 - g, fill=A, op=0.9)],
        [_rect(0, 0, c1, r1, fill=SF), _rect(c2 + g, r2 + g, w - c2 - g, h - r2 - g, fill=P, op=0.25)],
        [_rect(c1 + g, r2 + g, c2 - c1 - g, h - r2 - g, fill=None, stroke=PS, sw=6)],
    ]


DRAW = {"circles": _circles, "grid": _grid, "bands": _bands, "blocks": _blocks}


def art_layers(motif: str, w: int, h: int) -> list[str]:
    """飾りの層（SVG の中身の文字列）を、出る順に返す。座標は 0..w × 0..h。"""
    return ["".join(x for x in layer if x) for layer in DRAW[motif](w, h)]
