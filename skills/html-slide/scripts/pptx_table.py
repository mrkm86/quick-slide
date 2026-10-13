"""PPTX の表（a:tbl）→ HTML。セルごとに地の箱と文字の箱、罫線は 1 本ずつの線（SVG）。`pptx_import.py` が使う。

- 列の幅（gridCol）と行の高さ（tr/@h）は保存された値のまま
- 結合（gridSpan・rowSpan・hMerge・vMerge）は、結合の左上のセルだけを描く
- 表のスタイル（tableStyles.xml の tblStyle）を tblPr の印（firstRow・bandRow など）に合わせて重ねる。
  スタイルの定義が無ければ、テーマの accent1 の既定のスタイル（中間スタイル 2）に近い見た目にする
"""
from __future__ import annotations

from lxml import etree

import ooxml as X
from ooxml import A, NS
from pptx_import import f2

PARTS_ORDER = ("wholeTbl", "band1H", "band2H", "band1V", "band2V", "lastCol", "firstCol", "lastRow", "firstRow",
               "seCell", "swCell", "neCell", "nwCell")
SIDES = {"left": "lnL", "right": "lnR", "top": "lnT", "bottom": "lnB"}


def _style_el(reader, tbl):
    sid = X.q(tbl, "a:tblPr/a:tableStyleId")
    sid = (sid.text or "").strip() if sid is not None else ""
    ts = reader.ctx.deck.table_styles
    if ts is None:
        return None, sid
    for st in X.qa(ts, "a:tblStyle"):
        if st.get("styleId") == sid:
            return st, sid
    return None, sid


def _builtin(ctx):
    """スタイルの定義が無いとき: 中間スタイル 2 - アクセント 1 に近い見た目。"""
    acc = ctx.colors.scheme_rgb("accent1")
    tint = lambda f: tuple(round(c + (255 - c) * f) for c in acc) + (1.0,)  # noqa: E731
    white = (255, 255, 255, 1.0)
    return {
        "wholeTbl": {"fill": X.Fill("solid", tint(0.8)), "color": ctx.colors.scheme_rgb("tx1") + (1.0,), "b": None,
                     "borders": {k: (white, 12700) for k in ("left", "right", "top", "bottom", "insideH", "insideV")}},
        "band1H": {"fill": X.Fill("solid", tint(0.6))},
        "firstRow": {"fill": X.Fill("solid", acc + (1.0,)), "color": white, "b": True,
                     "borders": {"bottom": (white, 38100)}},
        "lastRow": {"fill": X.Fill("solid", acc + (1.0,)), "color": white, "b": True},
        "firstCol": {"fill": X.Fill("solid", acc + (1.0,)), "color": white, "b": True},
        "lastCol": {"fill": X.Fill("solid", acc + (1.0,)), "color": white, "b": True},
    }


def _read_style(st, ctx) -> dict:
    out = {}
    for part in PARTS_ORDER:
        el = X.q(st, f"a:{part}")
        if el is None:
            continue
        d = {}
        tc_tx = X.q(el, "a:tcTxStyle")
        if tc_tx is not None:
            c = ctx.colors.find(tc_tx)
            if c is not None:
                d["color"] = c
            elif X.q(tc_tx, "a:fontRef") is not None:
                c = ctx.colors.find(X.q(tc_tx, "a:fontRef"))
                if c is not None:
                    d["color"] = c
            if tc_tx.get("b") is not None:
                d["b"] = tc_tx.get("b") == "on"
        tcs = X.q(el, "a:tcStyle")
        if tcs is not None:
            fill = X.q(tcs, "a:fill")
            if fill is not None:
                f = X.first_fill(fill, ctx.colors)
                if f is not None:
                    d["fill"] = f
            fr = X.q(tcs, "a:fillRef")
            if fr is not None and "fill" not in d:
                f = X.ref_fill(ctx.theme, fr.get("idx"), ctx.colors.find(fr), ctx.colors)
                if f is not None:
                    d["fill"] = f
            bdr = X.q(tcs, "a:tcBdr")
            if bdr is not None:
                bd = {}
                for side in ("left", "right", "top", "bottom", "insideH", "insideV"):
                    s = X.q(bdr, f"a:{side}")
                    if s is None:
                        continue
                    ln = X.q(s, "a:ln")
                    if ln is None:
                        lr = X.q(s, "a:lnRef")
                        if lr is not None:
                            c = ctx.colors.find(lr)
                            bd[side] = (c, 12700) if c is not None else (None, 0)
                        continue
                    f = X.first_fill(ln, ctx.colors)
                    c = f.main_color() if f is not None and f.kind != "none" else None
                    bd[side] = (c, X.ival(ln, "w", 12700))
                d["borders"] = bd
        out[part] = d
    return out


def _parts_for(r, c, nr, nc, flags) -> list[str]:
    ps = ["wholeTbl"]
    if flags["bandRow"]:
        rr = r - (1 if flags["firstRow"] else 0)
        if rr >= 0:
            ps.append("band1H" if rr % 2 == 0 else "band2H")
    if flags["bandCol"]:
        cc = c - (1 if flags["firstCol"] else 0)
        if cc >= 0:
            ps.append("band1V" if cc % 2 == 0 else "band2V")
    if flags["lastCol"] and c == nc - 1:
        ps.append("lastCol")
    if flags["firstCol"] and c == 0:
        ps.append("firstCol")
    if flags["lastRow"] and r == nr - 1:
        ps.append("lastRow")
    if flags["firstRow"] and r == 0:
        ps.append("firstRow")
    return ps


def _lst_for(color, bold):
    """表のスタイルの文字の色と太さを、段落の書式の継承の 1 段として渡す（9 段すべて）。"""
    lst = etree.Element(A + "lstStyle", nsmap={"a": NS["a"]})
    for i in range(1, 10):
        lv = etree.SubElement(lst, A + f"lvl{i}pPr")
        d = etree.SubElement(lv, A + "defRPr")
        if bold is not None:
            d.set("b", "1" if bold else "0")
        if color is not None:
            sf = etree.SubElement(d, A + "solidFill")
            s = etree.SubElement(sf, A + "srgbClr")
            s.set("val", "%02X%02X%02X" % tuple(color[:3]))
            if color[3] < 0.999:
                a = etree.SubElement(s, A + "alpha")
                a.set("val", str(round(color[3] * 100000)))
    return lst


def render(reader, tbl, bx) -> list[str]:
    import pptx_text
    ctx = reader.ctx
    g = ctx.geo
    cols = [X.ival(gc, "w", 0) for gc in X.qa(tbl, "a:tblGrid/a:gridCol")]
    rows = X.qa(tbl, "a:tr")
    if not cols or not rows:
        return []
    tp = X.q(tbl, "a:tblPr")
    flags = {k: X.bval(tp, k) for k in ("firstRow", "lastRow", "firstCol", "lastCol", "bandRow", "bandCol")}
    st, sid = _style_el(reader, tbl)
    style = _read_style(st, ctx) if st is not None else (_builtin(ctx) if sid else {})
    if st is None and sid:
        ctx.rep("表のスタイル", "定義が無いので、既定の表のスタイルに近い色にした")
    nr, nc = len(rows), len(cols)
    xs = [bx["x"]]
    for w in cols:
        xs.append(xs[-1] + g.len(w))
    hs = [g.len(X.ival(tr, "h", 370840)) for tr in rows]
    ys = [bx["y"]]
    for h in hs:
        ys.append(ys[-1] + h)
    pieces: list[str] = []
    lines: list[str] = []
    texts: list[str] = []
    for r, tr in enumerate(rows):
        cells = X.qa(tr, "a:tc")
        for c, tc in enumerate(cells[:nc]):
            if X.bval(tc, "hMerge") or X.bval(tc, "vMerge"):
                continue
            span_c = X.ival(tc, "gridSpan", 1) or 1
            span_r = X.ival(tc, "rowSpan", 1) or 1
            x0, x1 = xs[c], xs[min(nc, c + span_c)]
            y0, y1 = ys[r], ys[min(nr, r + span_r)]
            parts = _parts_for(r, c, nr, nc, flags)
            fill, color, bold = None, None, None
            borders: dict = {}
            for p in parts:
                d = style.get(p) or {}
                if "fill" in d:
                    fill = d["fill"]
                if "color" in d:
                    color = d["color"]
                if d.get("b") is not None:
                    bold = d["b"]
                bd = d.get("borders") or {}
                for side, v in bd.items():
                    if side == "insideH":
                        if r > 0:
                            borders["top"] = v
                        if r + span_r < nr:
                            borders["bottom"] = v
                    elif side == "insideV":
                        if c > 0:
                            borders["left"] = v
                        if c + span_c < nc:
                            borders["right"] = v
                    else:
                        if side == "top" and r != 0 and p == "wholeTbl":
                            continue
                        if side == "bottom" and r + span_r != nr and p == "wholeTbl":
                            continue
                        if side == "left" and c != 0 and p == "wholeTbl":
                            continue
                        if side == "right" and c + span_c != nc and p == "wholeTbl":
                            continue
                        borders[side] = v
            tcpr = X.q(tc, "a:tcPr")
            f = X.first_fill(tcpr, ctx.colors)
            if f is not None:
                fill = f
            for side, tag in SIDES.items():
                ln = X.q(tcpr, f"a:{tag}")
                if ln is not None:
                    lf = X.first_fill(ln, ctx.colors)
                    lc = lf.main_color() if lf is not None and lf.kind != "none" else None
                    borders[side] = (lc, X.ival(ln, "w", 12700))
            bg = None
            if fill is not None and fill.kind not in ("none", "group", "blip"):
                c_ = fill.main_color()
                if c_ is not None:
                    bg = X.css_color(c_)
            if bg:
                pieces.append(f'<div style="position:absolute;left:{f2(x0)}px;top:{f2(y0)}px;width:{f2(x1 - x0)}px;'
                              f'height:{f2(y1 - y0)}px;background-color:{bg}"></div>')
            for side, (lc, lw) in borders.items():
                if lc is None or not lw or lc[3] < 0.002:
                    continue
                w_ = max(0.5, g.len(lw))
                seg = {"top": (x0, y0, x1, y0), "bottom": (x0, y1, x1, y1), "left": (x0, y0, x0, y1), "right": (x1, y0, x1, y1)}[side]
                key = (round(seg[0], 1), round(seg[1], 1), round(seg[2], 1), round(seg[3], 1))
                lines.append((key, w_, lc))
            txb = X.q(tc, "a:txBody")
            if txb is not None:
                lists = [X.child(txb, "lstStyle"), _lst_for(color, bold), ctx.deck.default_text]
                chain = X.TextChain(lists, ctx.theme, ctx.colors)
                bp = {"lIns": X.ival(tcpr, "marL", 91440), "rIns": X.ival(tcpr, "marR", 91440),
                      "tIns": X.ival(tcpr, "marT", 45720), "bIns": X.ival(tcpr, "marB", 45720),
                      "anchor": (tcpr.get("anchor") if tcpr is not None else None) or "t", "wrap": "square",
                      "vert": (tcpr.get("vert") if tcpr is not None else None) or "horz", "rot": 0, "autofit": None,
                      "scale": 1.0, "lnred": 0.0, "upright": False}
                cb = {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0, "rot": 0, "fh": False, "fv": False}
                t = pptx_text.render(ctx, txb, chain, bp, cb, "rect")
                if t:
                    texts.append(t)
    if lines:
        seen = {}
        for key, w_, lc in lines:
            seen[key] = (w_, lc)          # 後のセルの指定が勝つ
        segs = []
        for (ax, ay, bx_, by), (w_, lc) in seen.items():
            op = f' stroke-opacity="{round(lc[3], 3)}"' if lc[3] < 0.999 else ""
            segs.append(f'<line x1="{f2(ax)}" y1="{f2(ay)}" x2="{f2(bx_)}" y2="{f2(by)}" stroke="{X.hex_of(lc)}" '
                        f'stroke-width="{f2(w_)}"{op}/>')
        pieces.append('<svg style="position:absolute;left:0;top:0;width:1920px;height:1080px;overflow:visible" '
                      'viewBox="0 0 1920 1080">' + "".join(segs) + "</svg>")
    return pieces + texts
