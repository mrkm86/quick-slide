"""PPTX の文字の箱（txBody）→ HTML。`pptx_import.py` が使う。

- 箱は図形の箱（から余白を引いた中）。上下の寄せは flex の列で、はみ出しは見せる（PowerPoint と同じ）
- 段落は 1 つの要素。箇条書きは「記号の箱＋文字の箱」の横並びにして、ぶら下げを再現する
- 字の大きさは 1pt = 2px（スライドを縮めたぶんも掛ける）。行間 100% は字の大きさの 1.2 倍
- 自動調整（normAutofit）の縮小率と行間の減らしを掛ける
- 縦書き（eaVert など）は、段落ごとに 1 字ずつ縦に並べる列にする（右から左）。横に寝かせる縦書き（vert・vert270）は回す
"""
from __future__ import annotations

import re

import ooxml as X
from ooxml import A
from pptx_import import CJK, FONT_ALIASES, JA_FALLBACK, JA_SERIF_FALLBACK, LINE_HEIGHT, SERIF_HINT, WINGDINGS, esc, f2

ANCHOR = {"t": "flex-start", "ctr": "center", "b": "flex-end", "just": "flex-start", "dist": "space-between"}
ALIGN = {"l": "left", "ctr": "center", "r": "right", "just": "justify", "dist": "justify", "thaiDist": "justify", "justLow": "justify"}
VERTICAL_STACK = ("eaVert", "wordArtVert", "wordArtVertRtl", "mongolianVert")
WEIGHT_SUFFIX = re.compile(r"\s+(Light|Medium|Bold|Regular|DemiBold|SemiBold|Semibold|Heavy|Black|Thin|ExtraBold|UI|W\d)$", re.I)
SYMBOL_FONTS = ("Wingdings", "Wingdings 2", "Wingdings 3", "Symbol", "Webdings", "Marlett")


def font_stack(latin: str | None, ea: str | None, text: str) -> str:
    first = [ea, latin] if CJK.search(text or "") else [latin, ea]
    names: list[str] = []
    for n in first:
        if not n:
            continue
        n = n.replace('"', "").replace("'", "").replace(";", "").replace("<", "").replace(">", "").strip()
        if n and n not in names:
            names.append(n)
        # 太さの付いた名前（游ゴシック Bold など）は、太さを外した名前でも探す（mac の字体は太さを名前に持たない）
        base = WEIGHT_SUFFIX.sub("", n).strip()
        for cand in (n, base):
            for al in [cand] + FONT_ALIASES.get(cand, []):
                if al and al not in names:
                    names.append(al)
    serif = bool(names) and bool(SERIF_HINT.search(names[0]))
    stack = [f"'{n}'" for n in names] + (JA_SERIF_FALLBACK if serif else JA_FALLBACK)
    return ", ".join(stack)


def _roman(n: int) -> str:
    vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"),
            (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out


def _alpha(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def autonum(kind: str, n: int) -> str:
    circled = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
    if kind.startswith("circleNum"):
        if "WdBlack" in kind:
            return "❶❷❸❹❺❻❼❽❾❿"[n - 1] if 1 <= n <= 10 else str(n)
        return circled[n - 1] if 1 <= n <= 20 else str(n)
    if kind.startswith("arabic"):
        core = str(n)
    elif kind.startswith("alphaLc"):
        core = _alpha(n).lower()
    elif kind.startswith("alphaUc"):
        core = _alpha(n)
    elif kind.startswith("romanLc"):
        core = _roman(n).lower()
    elif kind.startswith("romanUc"):
        core = _roman(n)
    elif kind.startswith("ea1Jpn") or kind.startswith("ea1Chs") or "DbPeriod" in kind:
        core = "".join(chr(0xFF10 + int(c)) for c in str(n))
        return core + "．"
    else:
        core = str(n)
    if kind.endswith("ParenBoth"):
        return f"({core})"
    if kind.endswith("ParenR"):
        return f"{core})"
    if kind.endswith("Period"):
        return f"{core}."
    if kind.endswith("Minus"):
        return f"- {core} -"
    return core


def _space(sp, size_px: float, ctx) -> float:
    if sp is None:
        return 0.0
    kind, v = sp
    if kind == "pct":
        return size_px * LINE_HEIGHT * v
    return ctx.geo.pt(v)


def _runs(ctx, p, chain: X.TextChain, ppr, lvl: int, scale: float, stack_mode: bool):
    """段落の中の run → [(文字, 書式)]。強制改行は ("\\n", None)。"""
    out = []
    for c in p:
        if not isinstance(c.tag, str):
            continue
        n = X.local(c)
        if n in ("r", "fld"):
            t = X.child(c, "t")
            text = (t.text or "") if t is not None else ""
            if n == "fld" and (c.get("type") or "") == "slidenum":
                text = str(ctx.no)
            rp = chain.run(X.child(c, "rPr"), ppr, lvl)
            if rp["cap"] == "all":
                text = text.upper()
            text = text.replace("\t", "    ").replace("\u000b", "\n")
            if text:
                out.append((text, rp))
        elif n == "br":
            out.append(("\n", None))
    return out


def _span(ctx, text: str, rp: dict, scale: float, para_size: float | None = None) -> str:
    size = ctx.geo.pt(rp["sz"] * scale)
    st = [f"font-size:{f2(size)}px", f"font-family:{font_stack(rp['latin'], rp['ea'], text)}",
          f"color:{X.css_color(rp['color'])}"]
    st.append("font-weight:700" if rp["b"] else "font-weight:400")
    if rp["i"]:
        st.append("font-style:italic")
    deco = [d for d, on in (("underline", rp["u"]), ("line-through", rp["strike"])) if on]
    if deco:
        st.append("text-decoration:" + " ".join(deco))
    if rp["spc"]:
        st.append(f"letter-spacing:{f2(ctx.geo.pt(rp['spc']))}px")
    if rp["baseline"]:
        st.append(f"vertical-align:{'super' if rp['baseline'] > 0 else 'sub'}")
        st[0] = f"font-size:{f2(size * 0.66)}px"
    return f'<span style="{";".join(st)}">{esc(text)}</span>'


def _bullet(ctx, pp: dict, runs, counters: dict, lvl: int, scale: float, size_px: float, first_rp: dict | None) -> str | None:
    bu = pp["bullet"]
    if bu is None:
        counters.pop(lvl, None)
        return None
    if bu[0] == "char":
        ch = bu[1]
        font = pp["bu_font"]
        if font and any(font.startswith(s) for s in SYMBOL_FONTS):
            ch = WINGDINGS.get(ch, "•") if font.startswith("Wingdings") or font == "Webdings" else (
                {"·": "•", "§": "▪", "Ø": "➢", "ü": "✔"}.get(ch, ch if ch.isprintable() else "•"))
            font = None
        counters.pop(lvl, None)
        text = ch
    else:
        _, kind, start = bu
        n = counters.get(lvl, start - 1) + 1
        counters[lvl] = n
        for k in list(counters):
            if k > lvl:
                counters.pop(k)
        text = autonum(kind, n)
        font = None
    color = pp["bu_color"] or (first_rp["color"] if first_rp else (0, 0, 0, 1.0))
    bsz = size_px * (pp["bu_size"] or 1.0)
    fam = font_stack(font or (first_rp["latin"] if first_rp else None), first_rp["ea"] if first_rp else None, text)
    return (f'<span style="font-size:{f2(bsz)}px;font-family:{fam};color:{X.css_color(color)};'
            f'font-weight:{700 if first_rp and first_rp["b"] else 400}">{esc(text)}</span>')


def render(ctx, tx, chain: X.TextChain, bp: dict, bx: dict, prst: str, para_hook=None, adj=None) -> str:
    g = ctx.geo
    paras = X.children(tx, "p")
    if not any((t.text or "").strip() for t in tx.iter(A + "t")) and not any(
            (f.get("type") or "") == "slidenum" for f in tx.iter(A + "fld")):
        return ""
    scale = bp["scale"]
    lnred = bp["lnred"]
    x, y, w, h = bx["x"], bx["y"], bx["w"], bx["h"]
    if prst in ("ellipse", "flowChartConnector", "wedgeEllipseCallout"):
        x, y, w, h = x + w * 0.1464, y + h * 0.1464, w * 0.7071, h * 0.7071
    elif prst in ("diamond", "flowChartDecision"):
        x, y, w, h = x + w / 4, y + h / 4, w / 2, h / 2
    elif prst in ("triangle",):
        x, y, w, h = x + w / 4, y + h / 2, w / 2, h / 2
    elif prst in ("chevron", "homePlate"):
        # 文字の箱は凹みと先を除いた所（PowerPoint の形の文字の領域）
        a = min(w, h) * (adj or {}).get("adj", 50000) / 100000
        if prst == "chevron":
            x, w = x + a, max(1.0, w - 2 * a)
        else:
            w = max(1.0, w - a / 2)
    elif prst in ("rightArrow", "leftArrow", "leftRightArrow"):
        sh = h * (adj or {}).get("adj1", 50000) / 100000
        y, h = y + (h - sh) / 2, sh
    rot = (bx["rot"] + bp["rot"] / 60000) % 360
    if bx["fv"]:
        rot = (rot + 180) % 360
    vert = bp["vert"]
    if vert in ("vert", "vert270"):
        rot = (rot + (90 if vert == "vert" else 270)) % 360
        x, y, w, h = x + w / 2 - h / 2, y + h / 2 - w / 2, h, w
    if vert in VERTICAL_STACK and not CJK.search("".join(t.text or "" for t in tx.iter(A + "t"))):
        # 英字だけの縦書きは、PowerPoint でも字が横に寝る（右へ 90° 回した横書きと同じ）
        vert = "vert"
        rot = (rot + 90) % 360
        x, y, w, h = x + w / 2 - h / 2, y + h / 2 - w / 2, h, w
    stack = vert in VERTICAL_STACK
    if stack:
        ctx.rep("縦書き", "1 字ずつ縦に並べた（PowerPoint では横書きの箱になる）")
    pl, pr, pt_, pb = (g.len(bp[k]) for k in ("lIns", "rIns", "tIns", "bIns"))
    nowrap = bp["wrap"] == "none"
    cont = [f"position:absolute;left:{f2(x)}px;top:{f2(y)}px;width:{f2(max(w, 1))}px;height:{f2(max(h, 1))}px",
            f"padding:{f2(pt_)}px {f2(pr)}px {f2(pb)}px {f2(pl)}px", "display:flex",
            f"flex-direction:{'row-reverse' if stack else 'column'}",
            f"justify-content:{ANCHOR.get(bp['anchor'], 'flex-start')}"]
    if rot:
        cont.append(f"transform:rotate({f2(rot)}deg)")
    counters: dict[int, int] = {}
    out = []
    first = True
    for i, p in enumerate(paras):
        ppr = X.child(p, "pPr")
        lvl = X.ival(ppr, "lvl", 0) or 0
        pp = chain.para(ppr, lvl)
        runs = _runs(ctx, p, chain, ppr, lvl, scale, stack)
        end_rp = chain.run(X.child(p, "endParaRPr"), ppr, lvl)
        sizes = [g.pt(rp["sz"] * scale) for t, rp in runs if rp is not None] or [g.pt(end_rp["sz"] * scale)]
        size_px = max(sizes)
        ls = pp["lnSpc"]
        if ls is None or ls[0] == "pct":
            pct = (ls[1] if ls else 1.0) - lnred
            lh = f"{round(LINE_HEIGHT * max(0.5, pct), 3)}"
        else:
            lh = f"{f2(g.pt(ls[1]) * (1 - lnred))}px"
        bef = 0.0 if first else _space(pp["spcBef"], size_px, ctx)
        aft = _space(pp["spcAft"], size_px, ctx)
        first = False
        an = (para_hook or {}).get(i)
        aattr = ""
        if an:
            aattr = f' data-step="{an["step"]}" data-fx="{an["fx"]}"' + (f' data-delay="{an["delay"]}"' if an.get("delay") else "")
        visible = [(t, rp) for t, rp in runs if rp is None or t.strip()]
        if not any(rp is not None and t.strip() for t, rp in runs):
            # 空の段落: 行の高さぶんの空き（記号は出さない）
            hpx = size_px * LINE_HEIGHT * (ls[1] if ls and ls[0] == "pct" else 1.0) if not (ls and ls[0] == "pts") else g.pt(ls[1])
            out.append(f'<div style="height:{f2(hpx + bef + aft)}px;flex:none"></div>')
            counters.pop(lvl, None)
            continue
        first_rp = next((rp for t, rp in runs if rp is not None), None)
        if stack:
            col = []
            for t, rp in visible:
                if rp is None:
                    continue
                chars = [c for c in t if c.strip()]
                col.append("<br>".join(_span(ctx, c, rp, scale) for c in chars))
            out.append(f'<div style="flex:none;text-align:center;line-height:{lh};width:{f2(size_px * 1.25)}px;'
                       f'font-size:{f2(size_px)}px"{aattr}>' + "<br>".join(col) + "</div>")
            continue
        body = "".join("<br>" if rp is None else _span(ctx, t, rp, scale) for t, rp in runs)
        align = ALIGN.get(pp["algn"], "left")
        ws = "pre" if nowrap else "pre-wrap"
        mar = f"margin:{f2(bef)}px 0 {f2(aft)}px 0"
        marl, ind = g.len(pp["marL"]), g.len(pp["indent"])
        bullet = _bullet(ctx, pp, runs, counters, lvl, scale, size_px, first_rp)
        common = f"font-size:{f2(size_px)}px;line-height:{lh};white-space:{ws};flex:none"
        if bullet:
            left = marl + ind
            bw = -ind if ind < 0 else None
            bst = f"flex:none;width:{f2(bw)}px" if bw else f"flex:none;padding-right:{f2(size_px * 0.35)}px"
            out.append(f'<div style="display:flex;{mar};margin-left:{f2(max(0, left))}px;{common}"{aattr}>'
                       f'<div style="{bst};white-space:pre">{bullet}</div>'
                       f'<div style="flex:1 1 auto;min-width:0;text-align:{align};white-space:{ws}">{body}</div></div>')
        else:
            ti = f";text-indent:{f2(ind)}px" if ind else ""
            out.append(f'<div style="{mar};margin-left:{f2(marl)}px;text-align:{align};{common}{ti}"{aattr}>{body}</div>')
    if not out:
        return ""
    return f'<div style="{";".join(cont)}">' + "".join(out) + "</div>"
