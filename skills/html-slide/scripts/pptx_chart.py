"""PPTX のグラフ（c:chartSpace）→ SVG。中に控えてある数値（numCache・strCache）から描く。`pptx_import.py` が使う。

写すのは 縦棒・横棒（集合・積み上げ・100% 積み上げ）・折れ線・面・円・ドーナツ。題・凡例・軸の目盛り・目盛線・値のラベルも描く。
グラフの部品としての編集はできなくなる（図形と文字になる）。ほかの種類は空の枠にして報告する。
"""
from __future__ import annotations

import math

import ooxml as X
from pptx_import import esc, f2
from pptx_text import font_stack

KINDS = ("barChart", "bar3DChart", "lineChart", "line3DChart", "pieChart", "pie3DChart", "doughnutChart", "areaChart",
         "area3DChart", "scatterChart", "radarChart", "bubbleChart", "stockChart", "surfaceChart", "ofPieChart")


def _cache(el):
    """strRef・numRef・strLit・numLit の点の列。"""
    if el is None:
        return []
    pts = {}
    for pt in el.iter():
        if X.local(pt) == "pt":
            v = X.child(pt, "v")
            if v is not None:
                pts[X.ival(pt, "idx", len(pts))] = v.text or ""
    return [pts[k] for k in sorted(pts)]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _nice(vmax: float) -> tuple[float, float]:
    if vmax <= 0:
        return 1.0, 0.2
    e = 10 ** math.floor(math.log10(vmax))
    for m in (1, 2, 2.5, 5, 10):
        if vmax <= m * e:
            top = m * e
            break
    step = top / 5
    for k in (4, 5, 6):
        if abs(top / k - round(top / k / e * 10) * e / 10) < 1e-9:
            step = top / k
            break
    return top, step


def _fmt(v: float, fmt: str | None) -> str:
    if fmt and "%" in fmt:
        return f"{round(v * 100, 1):g}%"
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v)):,}"
    return f"{v:,.1f}"


class Chart:
    def __init__(self, reader, root, bx):
        self.r, self.ctx = reader, reader.ctx
        self.root, self.bx = root, bx
        self.colors = self.ctx.colors
        self.font = self.ctx.theme.fonts.get("mn-ea") or self.ctx.theme.fonts.get("mn-lt")
        latin = self.ctx.theme.fonts.get("mn-lt")
        self.fam = font_stack(latin, self.font, "あ")
        sz = None
        for d in root.iter():
            if X.local(d) == "defRPr" and d.get("sz"):
                sz = X.ival(d, "sz")
                break
        self.size = self.ctx.geo.pt((sz or 1200) / 100)
        self.ink = self.colors.scheme_rgb("tx1") + (1.0,)
        self.muted = tuple(round(c * 0.55 + 255 * 0.45) for c in self.ink[:3]) + (1.0,)

    def series_color(self, ser, i):
        sp = X.child(ser, "spPr")
        f = X.first_fill(sp, self.colors)
        if f is not None and f.kind not in ("none", "blip"):
            c = f.main_color()
            if c is not None:
                return c
        ln = X.q(sp, "a:ln")
        f = X.first_fill(ln, self.colors)
        if f is not None and f.kind not in ("none", "blip") and f.main_color() is not None:
            return f.main_color()
        k = i % 6
        base = self.colors.scheme_rgb(f"accent{k + 1}")
        if i >= 6:
            base = tuple(round(c * 0.6) for c in base)
        return base + (1.0,)

    def point_color(self, ser, idx, i):
        for dp in X.children(ser, "dPt"):
            if X.ival(X.child(dp, "idx"), "val", -1) == idx:
                f = X.first_fill(X.child(dp, "spPr"), self.colors)
                if f is not None and f.kind not in ("none", "blip") and f.main_color() is not None:
                    return f.main_color()
        return None

    def text(self, x, y, s, size=None, anchor="middle", color=None, bold=False):
        size = size or self.size
        c = X.hex_of(color or self.ink)
        return (f'<text x="{f2(x)}" y="{f2(y)}" text-anchor="{anchor}" dominant-baseline="central" fill="{c}" '
                f'style="font-size:{f2(size)}px;font-family:{self.fam};font-weight:{700 if bold else 400}">{esc(s)}</text>')

    def title(self, chart, series=None, pie=False):
        t = X.child(chart, "title")
        if t is None:
            # 系列が 1 つの円グラフは、題を消していなければ系列の名前が題になる（PowerPoint の既定）
            if pie and series and len(series) == 1 and X.ival(X.child(chart, "autoTitleDeleted"), "val", 0) != 1:
                return " ".join(_cache(X.q(series[0], "c:tx"))) or None
            return None
        s = "".join(x.text or "" for x in t.iter() if X.local(x) == "t")
        if not s:
            s = " ".join(_cache(X.q(t, "c:tx/c:strRef")))
        return s.strip() or None

    def render(self) -> list[str]:
        chart = X.child(self.root, "chart")
        pa = X.child(chart, "plotArea")
        if pa is None:
            return []
        kinds = [c for c in pa if isinstance(c.tag, str) and X.local(c) in KINDS]
        if not kinds:
            return []
        kind = kinds[0]
        name = X.local(kind)
        if len(kinds) > 1:
            self.ctx.rep("グラフ（組み合わせ）", f"最初の種類（{name}）だけを描いた")
        x, y, w, h = self.bx["x"], self.bx["y"], self.bx["w"], self.bx["h"]
        out = []
        top = y + h * 0.03
        ttl = self.title(chart, X.children(kind, "ser"), X.local(kind) in ("pieChart", "pie3DChart", "doughnutChart", "ofPieChart"))
        if ttl:
            out.append(self.text(x + w / 2, top + self.size * 0.9, ttl, self.size * 1.3, bold=False))
            top += self.size * 2.2
        series = X.children(kind, "ser")
        legend = X.child(chart, "legend")
        pos = X.child(legend, "legendPos").get("val") if legend is not None and X.child(legend, "legendPos") is not None else "r"
        names = [" ".join(_cache(X.q(s, "c:tx"))) or f"系列{i + 1}" for i, s in enumerate(series)]
        pie = name in ("pieChart", "pie3DChart", "doughnutChart", "ofPieChart")
        plot = [x + w * 0.04, top, x + w * 0.96, y + h * 0.97]
        leg_items = []
        if legend is not None:
            if pie and series:
                cats = _cache(X.q(series[0], "c:cat"))
                leg_items = [(c, self.point_color(series[0], i, i) or self.pie_color(i)) for i, c in enumerate(cats)]
            else:
                leg_items = [(n, self.series_color(s, i)) for i, (n, s) in enumerate(zip(names, series))]
            if pos in ("b", "t"):
                lh = self.size * 2.0
                if pos == "b":
                    plot[3] -= lh
                    ly = plot[3] + lh * 0.6
                else:
                    ly = plot[1] + lh * 0.4
                    plot[1] += lh
                total = sum(self.size * 1.6 + len(n) * self.size * 0.9 for n, _ in leg_items)
                lx = x + w / 2 - total / 2
                for n, c in leg_items:
                    out.append(f'<rect x="{f2(lx)}" y="{f2(ly - self.size * 0.4)}" width="{f2(self.size * 0.8)}" '
                               f'height="{f2(self.size * 0.8)}" fill="{X.hex_of(c)}"/>')
                    out.append(self.text(lx + self.size * 1.1, ly, n, anchor="start"))
                    lx += self.size * 1.6 + len(n) * self.size * 0.9
            else:
                lw = max((len(n) for n, _ in leg_items), default=4) * self.size * 0.95 + self.size * 2
                plot[2] -= lw
                ly = (plot[1] + plot[3]) / 2 - len(leg_items) * self.size * 0.8
                for n, c in leg_items:
                    lx = plot[2] + self.size
                    out.append(f'<rect x="{f2(lx)}" y="{f2(ly - self.size * 0.4)}" width="{f2(self.size * 0.8)}" '
                               f'height="{f2(self.size * 0.8)}" fill="{X.hex_of(c)}"/>')
                    out.append(self.text(lx + self.size * 1.1, ly, n, anchor="start"))
                    ly += self.size * 1.6
        if pie:
            out += self.pie(kind, series, plot, name == "doughnutChart")
        elif name in ("barChart", "bar3DChart"):
            out += self.bars(kind, series, plot)
        elif name in ("lineChart", "line3DChart", "areaChart", "area3DChart"):
            out += self.lines(kind, series, plot, area=name.startswith("area"))
        else:
            self.ctx.rep(f"グラフ（{name}）", "写せない種類なので空の枠にした")
            return [self.r.missing_img(self.bx)]
        if "3D" in name:
            self.ctx.rep("立体のグラフ", "平らなグラフにした")
        return ['<svg style="position:absolute;left:0;top:0;width:1920px;height:1080px;overflow:visible" '
                'viewBox="0 0 1920 1080">' + "".join(out) + "</svg>"]

    def pie_color(self, i):
        base = self.colors.scheme_rgb(f"accent{i % 6 + 1}")
        if i >= 6:
            base = tuple(round(c * 0.6) for c in base)
        return base + (1.0,)

    def _labels_on(self, kind, ser=None):
        for src in (ser, kind):
            if src is None:
                continue
            d = X.child(src, "dLbls")
            if d is not None:
                return X.ival(X.child(d, "showVal"), "val", 0) == 1, X.ival(X.child(d, "showPercent"), "val", 0) == 1
        return False, False

    def pie(self, kind, series, plot, donut):
        out = []
        if not series:
            return out
        ser = series[0]
        vals = [_num(v) for v in _cache(X.q(ser, "c:val"))]
        total = sum(v for v in vals if v > 0) or 1.0
        cx, cy = (plot[0] + plot[2]) / 2, (plot[1] + plot[3]) / 2
        r = min(plot[2] - plot[0], plot[3] - plot[1]) / 2 * 0.92
        hole = r * (X.ival(X.child(kind, "holeSize"), "val", 50) / 100) if donut else 0
        ang = math.radians(X.ival(X.child(kind, "firstSliceAng"), "val", 0)) - math.pi / 2
        show_val, show_pct = self._labels_on(kind, ser)
        fmt = None
        for i, v in enumerate(vals):
            if v <= 0:
                continue
            a2 = ang + 2 * math.pi * v / total
            c = self.point_color(ser, i, i) or self.pie_color(i)
            large = 1 if a2 - ang > math.pi else 0
            x1, y1 = cx + r * math.cos(ang), cy + r * math.sin(ang)
            x2, y2 = cx + r * math.cos(a2), cy + r * math.sin(a2)
            if hole:
                hx1, hy1 = cx + hole * math.cos(a2), cy + hole * math.sin(a2)
                hx2, hy2 = cx + hole * math.cos(ang), cy + hole * math.sin(ang)
                d = (f"M{f2(x1)},{f2(y1)} A{f2(r)},{f2(r)} 0 {large} 1 {f2(x2)},{f2(y2)} L{f2(hx1)},{f2(hy1)} "
                     f"A{f2(hole)},{f2(hole)} 0 {large} 0 {f2(hx2)},{f2(hy2)} Z")
            else:
                d = f"M{f2(cx)},{f2(cy)} L{f2(x1)},{f2(y1)} A{f2(r)},{f2(r)} 0 {large} 1 {f2(x2)},{f2(y2)} Z"
            if v >= total - 1e-9:
                d = (f"M{f2(cx - r)},{f2(cy)} A{f2(r)},{f2(r)} 0 1 1 {f2(cx + r)},{f2(cy)} A{f2(r)},{f2(r)} 0 1 1 {f2(cx - r)},{f2(cy)} Z")
            out.append(f'<path d="{d}" fill="{X.hex_of(c)}" stroke="#ffffff" stroke-width="{f2(max(1, r / 150))}"/>')
            if show_val or show_pct:
                mid = (ang + a2) / 2
                rr = (r + hole) / 2 if hole else r * 0.62
                s = f"{round(v / total * 100):d}%" if show_pct else _fmt(v, fmt)
                out.append(self.text(cx + rr * math.cos(mid), cy + rr * math.sin(mid), s, color=(255, 255, 255, 1.0)))
            ang = a2
        return out

    def _axis_fmt(self, kind):
        pa = kind.getparent()
        va = X.child(pa, "valAx")
        nf = X.child(va, "numFmt") if va is not None else None
        return nf.get("formatCode") if nf is not None else None, va

    def bars(self, kind, series, plot):
        out = []
        horiz = (X.child(kind, "barDir").get("val") if X.child(kind, "barDir") is not None else "col") == "bar"
        grouping = X.child(kind, "grouping").get("val") if X.child(kind, "grouping") is not None else "clustered"
        cats = _cache(X.q(series[0], "c:cat")) if series else []
        data = [[_num(v) for v in _cache(X.q(s, "c:val"))] for s in series]
        n = max([len(cats)] + [len(d) for d in data] + [1])
        stacked = grouping in ("stacked", "percentStacked")
        pct = grouping == "percentStacked"
        if stacked:
            tots = [sum(max(0, d[i]) for d in data if i < len(d)) for i in range(n)]
            vmax = 1.0 if pct else max(tots + [0])
        else:
            vmax = max([v for d in data for v in d] + [0])
        vmin = min([v for d in data for v in d] + [0])
        fmt, va = self._axis_fmt(kind)
        top, step = _nice(vmax if not pct else 1.0)
        lo = 0.0
        if vmin < 0:
            lo = -_nice(-vmin)[0]
        lab_w = self.size * (len(_fmt(top, fmt)) * 0.65 + 1.0)
        cat_h = self.size * 2.0
        x0, y0, x1, y1 = plot
        if horiz:
            cw = max((len(c) for c in cats), default=2) * self.size * 0.95 + self.size
            x0 += min(cw, (x1 - x0) * 0.35)
            y1 -= cat_h
        else:
            x0 += lab_w
            y1 -= cat_h
        gap = X.ival(X.child(kind, "gapWidth"), "val", 150) / 100
        grid = va is not None and X.child(va, "majorGridlines") is not None
        deleted = va is not None and X.ival(X.child(va, "delete"), "val", 0) == 1
        span = top - lo or 1
        k = lo
        while k <= top + 1e-9:
            if horiz:
                gx = x0 + (k - lo) / span * (x1 - x0)
                if grid:
                    out.append(f'<line x1="{f2(gx)}" y1="{f2(y0)}" x2="{f2(gx)}" y2="{f2(y1)}" stroke="#d9d9d9" stroke-width="1"/>')
                if not deleted:
                    out.append(self.text(gx, y1 + cat_h * 0.5, _fmt(k, fmt), color=self.muted))
            else:
                gy = y1 - (k - lo) / span * (y1 - y0)
                if grid:
                    out.append(f'<line x1="{f2(x0)}" y1="{f2(gy)}" x2="{f2(x1)}" y2="{f2(gy)}" stroke="#d9d9d9" stroke-width="1"/>')
                if not deleted:
                    out.append(self.text(x0 - self.size * 0.5, gy, _fmt(k, fmt), anchor="end", color=self.muted))
            k += step
        band = ((y1 - y0) if horiz else (x1 - x0)) / n
        m = 1 if stacked else max(1, len(series))
        bw = band / (m + gap)
        show_val, _ = self._labels_on(kind)
        for i in range(n):
            base_pos = (y0 if horiz else x0) + band * i + bw * gap / 2
            acc = 0.0
            for si, (s, d) in enumerate(zip(series, data)):
                v = d[i] if i < len(d) else 0.0
                if pct:
                    t = tots[i] or 1
                    v = v / t
                c = self.point_color(s, i, i) or self.series_color(s, si)
                start = acc if stacked else 0.0
                end = start + v
                acc = end if stacked else acc
                p = base_pos + (0 if stacked else bw * si)
                if horiz:
                    a_ = x0 + (min(start, end) - lo) / span * (x1 - x0)
                    b_ = x0 + (max(start, end) - lo) / span * (x1 - x0)
                    out.append(f'<rect x="{f2(a_)}" y="{f2(p)}" width="{f2(max(0, b_ - a_))}" height="{f2(bw)}" fill="{X.hex_of(c)}"/>')
                    if show_val or self._labels_on(kind, s)[0]:
                        out.append(self.text(b_ + self.size * 0.3, p + bw / 2, _fmt(d[i] if i < len(d) else 0, fmt), anchor="start"))
                else:
                    a_ = y1 - (max(start, end) - lo) / span * (y1 - y0)
                    b_ = y1 - (min(start, end) - lo) / span * (y1 - y0)
                    out.append(f'<rect x="{f2(p)}" y="{f2(a_)}" width="{f2(bw)}" height="{f2(max(0, b_ - a_))}" fill="{X.hex_of(c)}"/>')
                    if show_val or self._labels_on(kind, s)[0]:
                        out.append(self.text(p + bw / 2, a_ - self.size * 0.7, _fmt(d[i] if i < len(d) else 0, fmt)))
            if i < len(cats):
                if horiz:
                    out.append(self.text(x0 - self.size * 0.5, (y0 if horiz else 0) + band * (i + 0.5), cats[i], anchor="end"))
                else:
                    out.append(self.text(x0 + band * (i + 0.5), y1 + cat_h * 0.5, cats[i]))
        axis = (f'<line x1="{f2(x0)}" y1="{f2(y0)}" x2="{f2(x0)}" y2="{f2(y1)}" stroke="#a6a6a6" stroke-width="1.5"/>' if horiz else
                f'<line x1="{f2(x0)}" y1="{f2(y1 - (0 - lo) / span * (y1 - y0))}" x2="{f2(x1)}" y2="{f2(y1 - (0 - lo) / span * (y1 - y0))}" stroke="#a6a6a6" stroke-width="1.5"/>')
        out.append(axis)
        return out

    def lines(self, kind, series, plot, area=False):
        out = []
        cats = _cache(X.q(series[0], "c:cat")) if series else []
        data = [[_num(v) for v in _cache(X.q(s, "c:val"))] for s in series]
        n = max([len(cats)] + [len(d) for d in data] + [1])
        vmax = max([v for d in data for v in d] + [0])
        vmin = min([v for d in data for v in d] + [0])
        fmt, va = self._axis_fmt(kind)
        top, step = _nice(vmax)
        lo = -_nice(-vmin)[0] if vmin < 0 else 0.0
        x0, y0, x1, y1 = plot
        x0 += self.size * (len(_fmt(top, fmt)) * 0.65 + 1.0)
        y1 -= self.size * 2.0
        span = top - lo or 1
        grid = va is not None and X.child(va, "majorGridlines") is not None
        k = lo
        while k <= top + 1e-9:
            gy = y1 - (k - lo) / span * (y1 - y0)
            if grid:
                out.append(f'<line x1="{f2(x0)}" y1="{f2(gy)}" x2="{f2(x1)}" y2="{f2(gy)}" stroke="#d9d9d9" stroke-width="1"/>')
            out.append(self.text(x0 - self.size * 0.5, gy, _fmt(k, fmt), anchor="end", color=self.muted))
            k += step
        band = (x1 - x0) / n
        for i, c in enumerate(cats):
            out.append(self.text(x0 + band * (i + 0.5), y1 + self.size, c))
        lw = max(2.0, self.size * 0.18)
        for si, (s, d) in enumerate(zip(series, data)):
            c = self.series_color(s, si)
            pts = [(x0 + band * (i + 0.5), y1 - (v - lo) / span * (y1 - y0)) for i, v in enumerate(d)]
            if not pts:
                continue
            if area:
                base = y1 - (0 - lo) / span * (y1 - y0)
                poly = [(pts[0][0], base)] + pts + [(pts[-1][0], base)]
                out.append(f'<polygon points="{" ".join(f"{f2(a)},{f2(b)}" for a, b in poly)}" fill="{X.hex_of(c)}" fill-opacity="0.85"/>')
            else:
                out.append(f'<polyline points="{" ".join(f"{f2(a)},{f2(b)}" for a, b in pts)}" fill="none" stroke="{X.hex_of(c)}" '
                           f'stroke-width="{f2(lw)}" stroke-linejoin="round" stroke-linecap="round"/>')
                mk = X.q(s, "c:marker/c:symbol")
                if mk is None or mk.get("val") != "none":
                    for a, b in pts:
                        out.append(f'<circle cx="{f2(a)}" cy="{f2(b)}" r="{f2(lw * 1.6)}" fill="{X.hex_of(c)}"/>')
        out.append(f'<line x1="{f2(x0)}" y1="{f2(y1)}" x2="{f2(x1)}" y2="{f2(y1)}" stroke="#a6a6a6" stroke-width="1.5"/>')
        return out


def render(reader, part, bx) -> list[str]:
    try:
        root = X.parse(part.blob)
    except Exception:
        reader.ctx.rep("グラフ", "読めないので空の枠にした")
        return [reader.missing_img(bx)]
    reader.ctx.rep("グラフ", "図形と文字で描いた（グラフとしては編集できない）")
    return Chart(reader, root, bx).render()
