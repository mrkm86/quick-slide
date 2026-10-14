"""PPTX の図形の形 → SVG の path（`d`）。図形の箱の左上が原点の px で返す。

- プリセットの形（`a:prstGeom`）は、よく使う形だけを手で書く（四角・角丸・楕円・三角・矢印・山形・吹き出し・フローチャートなど）。
  知らない形は None を返し、呼ぶ側が四角で代えて報告する
- 自由な形（`a:custGeom`）は、ガイドの式（`a:gdLst`）を解いて、moveTo・lnTo・cubicBezTo・quadBezTo・arcTo・close を写す
- 塗りの規則は even-odd（html-slide の部品 JSON と同じ）。穴のある形（ドーナツ・額縁）は内側を逆向きの部分パスにする
"""
from __future__ import annotations

import math

import ooxml as X

PATH_ONLY = {"line", "straightConnector1", "bentConnector2", "bentConnector3", "bentConnector4", "bentConnector5",
             "curvedConnector2", "curvedConnector3", "curvedConnector4", "curvedConnector5", "arc", "leftBracket",
             "rightBracket", "leftBrace", "rightBrace", "bracketPair", "bracePair"}


def _f(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def poly(pts, close=True) -> str:
    d = "M" + " L".join(f"{_f(x)},{_f(y)}" for x, y in pts)
    return d + (" Z" if close else "")


def ellipse_d(cx, cy, rx, ry, ccw=False) -> str:
    s = 0 if ccw else 1
    return (f"M{_f(cx - rx)},{_f(cy)} A{_f(rx)},{_f(ry)} 0 1 {s} {_f(cx + rx)},{_f(cy)} "
            f"A{_f(rx)},{_f(ry)} 0 1 {s} {_f(cx - rx)},{_f(cy)} Z")


def round_rect_d(x, y, w, h, rtl, rtr, rbr, rbl) -> str:
    def cl(r):
        return max(0.0, min(r, w / 2, h / 2))
    rtl, rtr, rbr, rbl = cl(rtl), cl(rtr), cl(rbr), cl(rbl)
    d = f"M{_f(x + rtl)},{_f(y)} L{_f(x + w - rtr)},{_f(y)}"
    if rtr:
        d += f" A{_f(rtr)},{_f(rtr)} 0 0 1 {_f(x + w)},{_f(y + rtr)}"
    d += f" L{_f(x + w)},{_f(y + h - rbr)}"
    if rbr:
        d += f" A{_f(rbr)},{_f(rbr)} 0 0 1 {_f(x + w - rbr)},{_f(y + h)}"
    d += f" L{_f(x + rbl)},{_f(y + h)}"
    if rbl:
        d += f" A{_f(rbl)},{_f(rbl)} 0 0 1 {_f(x)},{_f(y + h - rbl)}"
    d += f" L{_f(x)},{_f(y + rtl)}"
    if rtl:
        d += f" A{_f(rtl)},{_f(rtl)} 0 0 1 {_f(x + rtl)},{_f(y)}"
    return d + " Z"


def adjusts(geom) -> dict[str, float]:
    out = {}
    for gd in X.qa(geom, "a:avLst/a:gd"):
        f = (gd.get("fmla") or "").split()
        if len(f) == 2 and f[0] == "val":
            try:
                out[gd.get("name")] = float(f[1])
            except ValueError:
                pass
    return out


def _arc_pts(cx, cy, rx, ry, a0, a1, n=24):
    return [(cx + rx * math.cos(a0 + (a1 - a0) * i / n), cy + ry * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]


def _callout_tip(w, h, adj, d1=-20833, d2=62500):
    return w / 2 + w * adj.get("adj1", d1) / 100000, h / 2 + h * adj.get("adj2", d2) / 100000


def _rect_callout(w, h, tip, r=0.0) -> str:
    """四角（角丸）の吹き出し。先が出る辺に三角を差し込んだ1つの輪郭にする。"""
    tx, ty = tip
    inside = 0 <= tx <= w and 0 <= ty <= h
    if inside:
        return round_rect_d(0, 0, w, h, r, r, r, r)
    # どの辺から出るか（はみ出しの大きい向き）
    dx = (tx - w) if tx > w else (-tx if tx < 0 else 0)
    dy = (ty - h) if ty > h else (-ty if ty < 0 else 0)
    if dy / max(h, 1) >= dx / max(w, 1):
        side = "b" if ty > h else "t"
    else:
        side = "r" if tx > w else "l"
    if side in ("t", "b"):
        c = max(w * 0.2, min(w * 0.8, tx))
        half = min(w * 0.12, w / 6)
        a, b = c - half, c + half
    else:
        c = max(h * 0.2, min(h * 0.8, ty))
        half = min(h * 0.12, h / 6)
        a, b = c - half, c + half
    r = max(0.0, min(r, w / 2, h / 2))
    # 角丸は弧で、辺の途中に先を差し込む（時計回り）
    d = f"M{_f(r)},0"
    if side == "t":
        d += f" L{_f(a)},0 L{_f(tx)},{_f(ty)} L{_f(b)},0"
    d += f" L{_f(w - r)},0"
    if r:
        d += f" A{_f(r)},{_f(r)} 0 0 1 {_f(w)},{_f(r)}"
    if side == "r":
        d += f" L{_f(w)},{_f(a)} L{_f(tx)},{_f(ty)} L{_f(w)},{_f(b)}"
    d += f" L{_f(w)},{_f(h - r)}"
    if r:
        d += f" A{_f(r)},{_f(r)} 0 0 1 {_f(w - r)},{_f(h)}"
    if side == "b":
        d += f" L{_f(b)},{_f(h)} L{_f(tx)},{_f(ty)} L{_f(a)},{_f(h)}"
    d += f" L{_f(r)},{_f(h)}"
    if r:
        d += f" A{_f(r)},{_f(r)} 0 0 1 0,{_f(h - r)}"
    if side == "l":
        d += f" L0,{_f(b)} L{_f(tx)},{_f(ty)} L0,{_f(a)}"
    d += f" L0,{_f(r)}"
    if r:
        d += f" A{_f(r)},{_f(r)} 0 0 1 {_f(r)},0"
    return d + " Z"


def _ellipse_callout(w, h, tip) -> str:
    cx, cy, rx, ry = w / 2, h / 2, w / 2, h / 2
    tx, ty = tip
    if ((tx - cx) / rx) ** 2 + ((ty - cy) / ry) ** 2 <= 1:
        return ellipse_d(cx, cy, rx, ry)
    t = math.atan2((ty - cy) / ry, (tx - cx) / rx)
    span = math.radians(12)
    pts = _arc_pts(cx, cy, rx, ry, t + span, t + 2 * math.pi - span, 48)
    return poly(pts + [(tx, ty)])


def preset(name: str, w: float, h: float, adj: dict[str, float]) -> str | None:
    """プリセットの形 → path の d。知らない形は None。"""
    ss = min(w, h)

    def a(key, default):
        return adj.get(key, default)

    if name in ("rect", "flowChartProcess", "actionButtonBlank"):
        return poly([(0, 0), (w, 0), (w, h), (0, h)])
    if name == "roundRect":
        r = ss * a("adj", 16667) / 100000
        return round_rect_d(0, 0, w, h, r, r, r, r)
    if name == "flowChartAlternateProcess":
        r = ss * 16667 / 100000
        return round_rect_d(0, 0, w, h, r, r, r, r)
    if name == "round1Rect":
        r = ss * a("adj", 16667) / 100000
        return round_rect_d(0, 0, w, h, 0, r, 0, 0)
    if name == "round2SameRect":
        r1, r2 = ss * a("adj1", 16667) / 100000, ss * a("adj2", 0) / 100000
        return round_rect_d(0, 0, w, h, r1, r1, r2, r2)
    if name == "round2DiagRect":
        r1, r2 = ss * a("adj1", 16667) / 100000, ss * a("adj2", 0) / 100000
        return round_rect_d(0, 0, w, h, r1, r2, r1, r2)
    if name == "snip1Rect":
        s = ss * a("adj", 16667) / 100000
        return poly([(0, 0), (w - s, 0), (w, s), (w, h), (0, h)])
    if name == "snip2SameRect":
        s1, s2 = ss * a("adj1", 16667) / 100000, ss * a("adj2", 0) / 100000
        return poly([(s1, 0), (w - s1, 0), (w, s1), (w, h - s2), (w - s2, h), (s2, h), (0, h - s2), (0, s1)])
    if name == "snip2DiagRect":
        s1, s2 = ss * a("adj1", 0) / 100000, ss * a("adj2", 16667) / 100000
        return poly([(s1, 0), (w - s2, 0), (w, s2), (w, h - s1), (w - s1, h), (s2, h), (0, h - s2), (0, s1)])
    if name in ("ellipse", "flowChartConnector"):
        return ellipse_d(w / 2, h / 2, w / 2, h / 2)
    if name == "triangle":
        x = w * a("adj", 50000) / 100000
        return poly([(x, 0), (w, h), (0, h)])
    if name == "flowChartExtract":
        return poly([(w / 2, 0), (w, h), (0, h)])
    if name == "flowChartMerge":
        return poly([(0, 0), (w, 0), (w / 2, h)])
    if name == "rtTriangle":
        return poly([(0, 0), (w, h), (0, h)])
    if name in ("diamond", "flowChartDecision"):
        return poly([(w / 2, 0), (w, h / 2), (w / 2, h), (0, h / 2)])
    if name == "parallelogram":
        x = ss * a("adj", 25000) / 100000
        return poly([(x, 0), (w, 0), (w - x, h), (0, h)])
    if name == "flowChartInputOutput":
        x = w / 5
        return poly([(x, 0), (w, 0), (w - x, h), (0, h)])
    if name == "trapezoid":
        x = ss * a("adj", 25000) / 100000
        return poly([(0, h), (x, 0), (w - x, 0), (w, h)])
    if name == "flowChartManualOperation":
        return poly([(0, 0), (w, 0), (w * 0.8, h), (w * 0.2, h)])
    if name == "flowChartManualInput":
        return poly([(0, h * 0.2), (w, 0), (w, h), (0, h)])
    if name == "pentagon":       # 正五角形（PowerPoint の「五角形」）。矢羽根は homePlate
        return poly([(w / 2, 0), (w, h * 0.3633), (w * 0.8090, h), (w * 0.1910, h), (0, h * 0.3633)])
    if name == "hexagon":
        x = ss * a("adj", 25000) / 100000
        return poly([(x, 0), (w - x, 0), (w, h / 2), (w - x, h), (x, h), (0, h / 2)])
    if name == "flowChartPreparation":
        x = w / 5
        return poly([(x, 0), (w - x, 0), (w, h / 2), (w - x, h), (x, h), (0, h / 2)])
    if name == "heptagon":
        return poly([(w / 2 + w / 2 * math.sin(2 * math.pi * i / 7), h / 2 - h / 2 * math.cos(2 * math.pi * i / 7)) for i in range(7)])
    if name == "octagon":
        x = ss * a("adj", 29289) / 100000
        return poly([(x, 0), (w - x, 0), (w, x), (w, h - x), (w - x, h), (x, h), (0, h - x), (0, x)])
    if name == "decagon":
        return poly([(w / 2 + w / 2 * math.sin(2 * math.pi * i / 10), h / 2 - h / 2 * math.cos(2 * math.pi * i / 10)) for i in range(10)])
    if name == "dodecagon":
        return poly([(w / 2 + w / 2 * math.sin(2 * math.pi * i / 12), h / 2 - h / 2 * math.cos(2 * math.pi * i / 12)) for i in range(12)])
    if name == "homePlate":
        x = ss * a("adj", 50000) / 100000
        return poly([(0, 0), (w - x, 0), (w, h / 2), (w - x, h), (0, h)])
    if name == "chevron":
        x = ss * a("adj", 50000) / 100000
        return poly([(0, 0), (w - x, 0), (w, h / 2), (w - x, h), (0, h), (x, h / 2)])
    if name == "plus":
        x = ss * a("adj", 25000) / 100000
        return poly([(x, 0), (w - x, 0), (w - x, x), (w, x), (w, h - x), (w - x, h - x), (w - x, h), (x, h), (x, h - x),
                     (0, h - x), (0, x), (x, x)])
    if name == "mathPlus":
        t = ss * a("adj1", 23520) / 100000 / 2
        cx, cy = w / 2, h / 2
        lx, ly = w * 0.4, h * 0.4
        return poly([(cx - t, cy - ly), (cx + t, cy - ly), (cx + t, cy - t), (cx + lx, cy - t), (cx + lx, cy + t), (cx + t, cy + t),
                     (cx + t, cy + ly), (cx - t, cy + ly), (cx - t, cy + t), (cx - lx, cy + t), (cx - lx, cy - t), (cx - t, cy - t)])
    if name == "mathMinus":
        t = h * a("adj1", 23520) / 100000 / 2
        return poly([(w * 0.1, h / 2 - t), (w * 0.9, h / 2 - t), (w * 0.9, h / 2 + t), (w * 0.1, h / 2 + t)])
    if name == "mathEqual":
        t = h * a("adj1", 23520) / 100000
        g = h * a("adj2", 11760) / 100000
        y1 = h / 2 - g / 2 - t
        y2 = h / 2 + g / 2
        return (poly([(w * 0.1, y1), (w * 0.9, y1), (w * 0.9, y1 + t), (w * 0.1, y1 + t)]) + " " +
                poly([(w * 0.1, y2), (w * 0.9, y2), (w * 0.9, y2 + t), (w * 0.1, y2 + t)]))
    if name == "mathMultiply":
        t = ss * a("adj1", 23520) / 100000 / 2
        cx, cy, L = w / 2, h / 2, ss * 0.36
        pts = []
        for k in range(4):
            th = math.pi / 4 + k * math.pi / 2
            ux, uy, vx, vy = math.cos(th), math.sin(th), -math.sin(th), math.cos(th)
            pts += [(cx + ux * L - vx * t, cy + uy * L - vy * t), (cx + ux * L + vx * t, cy + uy * L + vy * t)]
            mid = th + math.pi / 4
            pts.append((cx + math.cos(mid) * t * math.sqrt(2), cy + math.sin(mid) * t * math.sqrt(2)))
        return poly(pts)
    if name in ("rightArrow", "notchedRightArrow", "stripedRightArrow"):
        sh = h * a("adj1", 50000) / 100000
        hl = ss * a("adj2", 50000) / 100000
        y1, y2, x = (h - sh) / 2, (h + sh) / 2, w - hl
        n = (hl * 0.5 if name == "notchedRightArrow" else 0)
        return poly([(0, y1), (x, y1), (x, 0), (w, h / 2), (x, h), (x, y2), (0, y2)] + ([(n, h / 2)] if n else []))
    if name == "leftArrow":
        sh = h * a("adj1", 50000) / 100000
        hl = ss * a("adj2", 50000) / 100000
        y1, y2 = (h - sh) / 2, (h + sh) / 2
        return poly([(w, y1), (hl, y1), (hl, 0), (0, h / 2), (hl, h), (hl, y2), (w, y2)])
    if name == "upArrow":
        sw = w * a("adj1", 50000) / 100000
        hl = ss * a("adj2", 50000) / 100000
        x1, x2 = (w - sw) / 2, (w + sw) / 2
        return poly([(x1, h), (x1, hl), (0, hl), (w / 2, 0), (w, hl), (x2, hl), (x2, h)])
    if name == "downArrow":
        sw = w * a("adj1", 50000) / 100000
        hl = ss * a("adj2", 50000) / 100000
        x1, x2, y = (w - sw) / 2, (w + sw) / 2, h - hl
        return poly([(x1, 0), (x2, 0), (x2, y), (w, y), (w / 2, h), (0, y), (x1, y)])
    if name == "leftRightArrow":
        sh = h * a("adj1", 50000) / 100000
        hl = ss * a("adj2", 50000) / 100000
        y1, y2 = (h - sh) / 2, (h + sh) / 2
        return poly([(0, h / 2), (hl, 0), (hl, y1), (w - hl, y1), (w - hl, 0), (w, h / 2), (w - hl, h), (w - hl, y2),
                     (hl, y2), (hl, h)])
    if name == "upDownArrow":
        sw = w * a("adj1", 50000) / 100000
        hl = ss * a("adj2", 50000) / 100000
        x1, x2 = (w - sw) / 2, (w + sw) / 2
        return poly([(w / 2, 0), (w, hl), (x2, hl), (x2, h - hl), (w, h - hl), (w / 2, h), (0, h - hl), (x1, h - hl),
                     (x1, hl), (0, hl)])
    if name in ("star5", "star4", "star6", "star7", "star8", "star10", "star12", "star16", "star24", "star32"):
        n = int(name[4:])
        default_in = {4: 12500, 5: 19098, 6: 28868, 7: 34601, 8: 38250, 10: 42533, 12: 37500, 16: 37500, 24: 37500, 32: 37500}[n]
        ratio = a("adj", default_in) / 50000
        pts = []
        for i in range(2 * n):
            ang = -math.pi / 2 + math.pi * i / n
            k = 1 if i % 2 == 0 else ratio
            pts.append((w / 2 + w / 2 * k * math.cos(ang), h / 2 + h / 2 * k * math.sin(ang)))
        return poly(pts)
    if name in ("wedgeRectCallout", "wedgeRoundRectCallout", "borderCallout1", "callout1", "accentCallout1"):
        tip = _callout_tip(w, h, adj)
        r = ss * 16667 / 100000 if name == "wedgeRoundRectCallout" else 0.0
        if name in ("borderCallout1", "callout1", "accentCallout1"):
            return poly([(0, 0), (w, 0), (w, h), (0, h)])
        return _rect_callout(w, h, tip, r)
    if name in ("wedgeEllipseCallout", "cloudCallout"):
        return _ellipse_callout(w, h, _callout_tip(w, h, adj))
    if name == "cloud":
        return ellipse_d(w / 2, h / 2, w / 2, h / 2)
    if name == "flowChartTerminator":
        r = min(h / 2, w / 2)
        return round_rect_d(0, 0, w, h, r, r, r, r)
    if name == "flowChartPredefinedProcess":
        return (poly([(0, 0), (w, 0), (w, h), (0, h)]) + " " + poly([(w / 8, 0), (w / 8, h)], False) + " " +
                poly([(w * 7 / 8, 0), (w * 7 / 8, h)], False))
    if name == "flowChartDocument":
        y = h * 17322 / 21600
        return (f"M0,0 L{_f(w)},0 L{_f(w)},{_f(y)} C{_f(w * 0.75)},{_f(y)} {_f(w * 0.75)},{_f(h * 1.08)} {_f(w / 2)},{_f(h * 0.93)}"
                f" C{_f(w * 0.25)},{_f(h * 0.78)} {_f(w * 0.25)},{_f(h * 0.97)} 0,{_f(h * 0.97)} Z")
    if name in ("can", "flowChartMagneticDisk"):
        ry = ss * a("adj", 25000) / 100000 / 2 if name == "can" else h / 6
        return (f"M0,{_f(ry)} A{_f(w / 2)},{_f(ry)} 0 0 1 {_f(w)},{_f(ry)} L{_f(w)},{_f(h - ry)} "
                f"A{_f(w / 2)},{_f(ry)} 0 0 1 0,{_f(h - ry)} Z")
    if name == "cube":
        d = ss * a("adj", 25000) / 100000
        return poly([(0, d), (d, 0), (w, 0), (w, h - d), (w - d, h), (0, h)])
    if name == "donut":
        t = ss * a("adj", 25000) / 100000
        return ellipse_d(w / 2, h / 2, w / 2, h / 2) + " " + ellipse_d(w / 2, h / 2, max(0, w / 2 - t), max(0, h / 2 - t), ccw=True)
    if name == "noSmoking":
        t = ss * a("adj", 18750) / 100000
        return ellipse_d(w / 2, h / 2, w / 2, h / 2) + " " + ellipse_d(w / 2, h / 2, max(0, w / 2 - t), max(0, h / 2 - t), ccw=True)
    if name == "frame":
        t = ss * a("adj1", 12500) / 100000
        return poly([(0, 0), (w, 0), (w, h), (0, h)]) + " " + poly([(t, t), (t, h - t), (w - t, h - t), (w - t, t)])
    if name == "halfFrame":
        t1, t2 = ss * a("adj1", 33333) / 100000, ss * a("adj2", 33333) / 100000
        return poly([(0, 0), (w, 0), (w - t1, t1), (t2, t1), (t2, h - t2), (0, h)])
    if name == "corner":
        t1, t2 = ss * a("adj1", 50000) / 100000, ss * a("adj2", 50000) / 100000
        return poly([(0, 0), (t2, 0), (t2, h - t1), (w, h - t1), (w, h), (0, h)])
    if name in ("pie", "chord", "blockArc", "arc"):
        a1 = math.radians(a("adj1", 0 if name == "pie" else (16200000 if name == "arc" else (2700000 if name == "chord" else 10800000))) / 60000)
        a2 = math.radians(a("adj2", 16200000 if name == "pie" else (0 if name == "arc" else (16200000 if name == "chord" else 0))) / 60000)
        if a2 <= a1:
            a2 += 2 * math.pi
        pts = _arc_pts(w / 2, h / 2, w / 2, h / 2, a1, a2, 48)
        if name == "pie":
            return poly(pts + [(w / 2, h / 2)])
        if name == "chord":
            return poly(pts)
        if name == "arc":
            return poly(pts, close=False)
        t = ss * a("adj3", 25000) / 100000
        inner = _arc_pts(w / 2, h / 2, max(0, w / 2 - t), max(0, h / 2 - t), a2, a1, 48)
        return poly(pts + inner)
    if name == "teardrop":
        return (f"M{_f(w / 2)},0 L{_f(w)},0 L{_f(w)},{_f(h / 2)} A{_f(w / 2)},{_f(h / 2)} 0 1 1 {_f(w / 2)},0 Z")
    if name == "heart":
        return (f"M{_f(w / 2)},{_f(h / 4)} C{_f(w * 0.5)},{_f(-h * 0.05)} {_f(-w * 0.1)},{_f(h * 0.05)} {_f(w * 0.05)},{_f(h * 0.45)}"
                f" L{_f(w / 2)},{_f(h)} L{_f(w * 0.95)},{_f(h * 0.45)} C{_f(w * 1.1)},{_f(h * 0.05)} {_f(w * 0.5)},{_f(-h * 0.05)} {_f(w / 2)},{_f(h / 4)} Z")
    if name == "lightningBolt":
        pts = [(0.39, 0), (0.62, 0.32), (0.53, 0.37), (0.81, 0.62), (0.73, 0.66), (1, 1), (0.42, 0.72), (0.53, 0.68),
               (0.24, 0.47), (0.35, 0.41), (0, 0.18)]
        return poly([(x * w, y * h) for x, y in pts])
    if name == "moon":
        t = w * a("adj", 50000) / 100000
        return f"M{_f(w)},0 A{_f(w)},{_f(h / 2)} 0 0 0 {_f(w)},{_f(h)} A{_f(max(1, w - t))},{_f(h / 2)} 0 0 1 {_f(w)},0 Z"
    if name == "line" or name == "straightConnector1":
        return f"M0,0 L{_f(w)},{_f(h)}"
    if name == "bentConnector2":
        return f"M0,0 L{_f(w)},0 L{_f(w)},{_f(h)}"
    if name == "bentConnector3":
        x = w * a("adj1", 50000) / 100000
        return f"M0,0 L{_f(x)},0 L{_f(x)},{_f(h)} L{_f(w)},{_f(h)}"
    if name in ("bentConnector4", "bentConnector5"):
        x = w * a("adj1", 50000) / 100000
        y = h * a("adj2", 50000) / 100000
        return f"M0,0 L{_f(x)},0 L{_f(x)},{_f(y)} L{_f(w)},{_f(y)} L{_f(w)},{_f(h)}"
    if name.startswith("curvedConnector"):
        return f"M0,0 C{_f(w / 2)},0 {_f(w / 2)},{_f(h)} {_f(w)},{_f(h)}"
    if name == "leftBracket":
        r = ss * a("adj", 8333) / 100000
        return f"M{_f(w)},0 A{_f(w)},{_f(r)} 0 0 0 0,{_f(r)} L0,{_f(h - r)} A{_f(w)},{_f(r)} 0 0 0 {_f(w)},{_f(h)}"
    if name == "rightBracket":
        r = ss * a("adj", 8333) / 100000
        return f"M0,0 A{_f(w)},{_f(r)} 0 0 1 {_f(w)},{_f(r)} L{_f(w)},{_f(h - r)} A{_f(w)},{_f(r)} 0 0 1 0,{_f(h)}"
    if name in ("leftBrace", "rightBrace"):
        r = ss * a("adj1", 8333) / 100000
        m = h * a("adj2", 50000) / 100000
        if name == "leftBrace":
            return (f"M{_f(w)},0 Q{_f(w / 2)},0 {_f(w / 2)},{_f(r)} L{_f(w / 2)},{_f(m - r)} Q{_f(w / 2)},{_f(m)} 0,{_f(m)} "
                    f"Q{_f(w / 2)},{_f(m)} {_f(w / 2)},{_f(m + r)} L{_f(w / 2)},{_f(h - r)} Q{_f(w / 2)},{_f(h)} {_f(w)},{_f(h)}")
        return (f"M0,0 Q{_f(w / 2)},0 {_f(w / 2)},{_f(r)} L{_f(w / 2)},{_f(m - r)} Q{_f(w / 2)},{_f(m)} {_f(w)},{_f(m)} "
                f"Q{_f(w / 2)},{_f(m)} {_f(w / 2)},{_f(m + r)} L{_f(w / 2)},{_f(h - r)} Q{_f(w / 2)},{_f(h)} 0,{_f(h)}")
    if name == "bracketPair":
        r = ss * a("adj", 16667) / 100000
        return (f"M{_f(r)},{_f(h)} A{_f(r)},{_f(r)} 0 0 1 0,{_f(h - r)} L0,{_f(r)} A{_f(r)},{_f(r)} 0 0 1 {_f(r)},0 "
                f"M{_f(w - r)},0 A{_f(r)},{_f(r)} 0 0 1 {_f(w)},{_f(r)} L{_f(w)},{_f(h - r)} A{_f(r)},{_f(r)} 0 0 1 {_f(w - r)},{_f(h)}")
    if name == "bracePair":
        r = ss * a("adj", 8333) / 100000
        return (f"M{_f(2 * r)},{_f(h)} Q{_f(r)},{_f(h)} {_f(r)},{_f(h - r)} L{_f(r)},{_f(h / 2 + r)} Q{_f(r)},{_f(h / 2)} 0,{_f(h / 2)} "
                f"Q{_f(r)},{_f(h / 2)} {_f(r)},{_f(h / 2 - r)} L{_f(r)},{_f(r)} Q{_f(r)},0 {_f(2 * r)},0 "
                f"M{_f(w - 2 * r)},0 Q{_f(w - r)},0 {_f(w - r)},{_f(r)} L{_f(w - r)},{_f(h / 2 - r)} Q{_f(w - r)},{_f(h / 2)} {_f(w)},{_f(h / 2)} "
                f"Q{_f(w - r)},{_f(h / 2)} {_f(w - r)},{_f(h / 2 + r)} L{_f(w - r)},{_f(h - r)} Q{_f(w - r)},{_f(h)} {_f(w - 2 * r)},{_f(h)}")
    if name == "plaque":
        r = ss * a("adj", 16667) / 100000
        return (f"M{_f(r)},0 L{_f(w - r)},0 A{_f(r)},{_f(r)} 0 0 0 {_f(w)},{_f(r)} L{_f(w)},{_f(h - r)} "
                f"A{_f(r)},{_f(r)} 0 0 0 {_f(w - r)},{_f(h)} L{_f(r)},{_f(h)} A{_f(r)},{_f(r)} 0 0 0 0,{_f(h - r)} "
                f"L0,{_f(r)} A{_f(r)},{_f(r)} 0 0 0 {_f(r)},0 Z")
    if name == "foldedCorner":
        s = ss * a("adj", 16667) / 100000
        return poly([(0, 0), (w, 0), (w, h - s), (w - s, h), (0, h)])
    if name == "bevel":
        return poly([(0, 0), (w, 0), (w, h), (0, h)])
    return None


# --- 自由な形（custGeom） ------------------------------------------------------------------

def _builtins(w: float, h: float) -> dict[str, float]:
    ss, ls = min(w, h), max(w, h)
    v = {"w": w, "h": h, "l": 0.0, "t": 0.0, "r": w, "b": h, "hc": w / 2, "vc": h / 2, "ss": ss, "ls": ls,
         "cd2": 10800000.0, "cd4": 5400000.0, "cd8": 2700000.0, "3cd4": 16200000.0, "3cd8": 8100000.0,
         "5cd8": 13500000.0, "7cd8": 18900000.0}
    for k in (2, 3, 4, 5, 6, 8, 10, 12, 16, 32):
        v[f"wd{k}"] = w / k
        v[f"hd{k}"] = h / k
        v[f"ssd{k}"] = ss / k
    v["wd3"], v["hd3"] = w / 3, h / 3
    return v


def _eval(fmla: str, env: dict[str, float]) -> float:
    p = fmla.split()
    if not p:
        return 0.0

    def g(i):
        t = p[i]
        try:
            return float(t)
        except ValueError:
            return env.get(t, 0.0)

    op = p[0]
    try:
        if op == "val":
            return g(1)
        if op == "*/":
            return g(1) * g(2) / (g(3) or 1)
        if op == "+-":
            return g(1) + g(2) - g(3)
        if op == "+/":
            return (g(1) + g(2)) / (g(3) or 1)
        if op == "?:":
            return g(2) if g(1) > 0 else g(3)
        if op == "abs":
            return abs(g(1))
        if op == "at2":
            return math.degrees(math.atan2(g(2), g(1))) * 60000
        if op == "cat2":
            return g(1) * math.cos(math.atan2(g(3), g(2)))
        if op == "sat2":
            return g(1) * math.sin(math.atan2(g(3), g(2)))
        if op == "cos":
            return g(1) * math.cos(math.radians(g(2) / 60000))
        if op == "sin":
            return g(1) * math.sin(math.radians(g(2) / 60000))
        if op == "tan":
            return g(1) * math.tan(math.radians(g(2) / 60000))
        if op == "max":
            return max(g(1), g(2))
        if op == "min":
            return min(g(1), g(2))
        if op == "mod":
            return math.sqrt(g(1) ** 2 + g(2) ** 2 + g(3) ** 2)
        if op == "pin":
            return max(g(1), min(g(3), g(2)))
        if op == "sqrt":
            return math.sqrt(max(0.0, g(1)))
    except (IndexError, ZeroDivisionError, ValueError):
        return 0.0
    return 0.0


def custom(geom, w: float, h: float) -> tuple[str, bool] | None:
    """custGeom → (path の d, 塗るか)。読めなければ None。"""
    paths = X.qa(geom, "a:pathLst/a:path")
    if not paths:
        return None
    parts = []
    any_fill = False
    for pth in paths:
        pw = float(pth.get("w") or 0) or None
        ph = float(pth.get("h") or 0) or None
        sx = w / pw if pw else 1.0
        sy = h / ph if ph else 1.0
        env = _builtins(pw or w, ph or h)
        for gd in X.qa(geom, "a:avLst/a:gd") + X.qa(geom, "a:gdLst/a:gd"):
            env[gd.get("name")] = _eval(gd.get("fmla") or "", env)

        def num(s):
            try:
                return float(s)
            except (TypeError, ValueError):
                return env.get(s, 0.0)

        if pth.get("fill") != "none":
            any_fill = True
        d = []
        cx = cy = 0.0
        for cmd in pth:
            if not isinstance(cmd.tag, str):
                continue
            n = X.local(cmd)
            pts = [(num(pt.get("x")), num(pt.get("y"))) for pt in X.qa(cmd, "a:pt")]
            if n == "moveTo" and pts:
                cx, cy = pts[0]
                d.append(f"M{_f(cx * sx)},{_f(cy * sy)}")
            elif n == "lnTo" and pts:
                cx, cy = pts[0]
                d.append(f"L{_f(cx * sx)},{_f(cy * sy)}")
            elif n == "cubicBezTo" and len(pts) == 3:
                d.append("C" + " ".join(f"{_f(x * sx)},{_f(y * sy)}" for x, y in pts))
                cx, cy = pts[2]
            elif n == "quadBezTo" and len(pts) == 2:
                d.append("Q" + " ".join(f"{_f(x * sx)},{_f(y * sy)}" for x, y in pts))
                cx, cy = pts[1]
            elif n == "arcTo":
                wr, hr = num(cmd.get("wR")), num(cmd.get("hR"))
                st, sw = num(cmd.get("stAng")) / 60000, num(cmd.get("swAng")) / 60000
                if wr <= 0 or hr <= 0:
                    continue
                a0 = math.radians(st)
                ocx, ocy = cx - wr * math.cos(a0), cy - hr * math.sin(a0)
                steps = max(2, int(abs(sw) / 10) + 1)
                for i in range(1, steps + 1):
                    ang = math.radians(st + sw * i / steps)
                    px, py = ocx + wr * math.cos(ang), ocy + hr * math.sin(ang)
                    d.append(f"L{_f(px * sx)},{_f(py * sy)}")
                cx, cy = ocx + wr * math.cos(math.radians(st + sw)), ocy + hr * math.sin(math.radians(st + sw))
            elif n == "close":
                d.append("Z")
        if d:
            parts.append(" ".join(d))
    if not parts:
        return None
    return " ".join(parts), any_fill
