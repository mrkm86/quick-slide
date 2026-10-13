"""PPTX（OOXML）を読む道具: 名前空間・安全な XML の読み・テーマ・色・塗り・線・文字の書式の継承。

`pptx_import.py`（PPTX → 取り込みの台本）が使う。python-pptx はパッケージを開いて部品（part）をたどるのに使い、
中身は lxml で直に読む（python-pptx はテーマ・継承・アニメーションを読まないため）。

座標と大きさの換算はここでは持たない（`pptx_import.Geo`）。色は (r, g, b, a) の 0〜255・a は 0〜1。
"""
from __future__ import annotations

import colorsys
import re

from lxml import etree

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "dsp": "http://schemas.microsoft.com/office/drawing/2008/diagram",
    "a14": "http://schemas.microsoft.com/office/drawing/2010/main",
    "p14": "http://schemas.microsoft.com/office/powerpoint/2010/main",
    "asvg": "http://schemas.microsoft.com/office/drawing/2016/SVG/main",
}
A = "{%s}" % NS["a"]
P = "{%s}" % NS["p"]
R = "{%s}" % NS["r"]
MC = "{%s}" % NS["mc"]

RT_DIAGRAM_DRAWING = "http://schemas.microsoft.com/office/2007/relationships/diagramDrawing"
RT_CHART = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart"
RT_THEME = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"
RT_TABLE_STYLES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/tableStyles"

# 外から来たファイルとして読む: 実体参照を解かない・ネットに行かない・巨大な木を許さない
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, remove_comments=True)


def parse(blob: bytes):
    return etree.fromstring(blob, parser=_PARSER)


def local(el) -> str:
    t = el.tag
    return t.rsplit("}", 1)[-1] if isinstance(t, str) else ""


def q(el, path: str):
    """`a:spPr/a:xfrm` のような道で最初の子孫。無ければ None。"""
    if el is None:
        return None
    r = el.find(path, NS)
    return r


def qa(el, path: str) -> list:
    return [] if el is None else el.findall(path, NS)


def child(el, name: str):
    """名前空間を問わず、ローカル名で最初の子（dsp:sp と p:sp を同じに読むため）。"""
    if el is None:
        return None
    for c in el:
        if local(c) == name:
            return c
    return None


def children(el, name: str | None = None) -> list:
    if el is None:
        return []
    return [c for c in el if isinstance(c.tag, str) and (name is None or local(c) == name)]


def ival(el, attr: str, default=None):
    if el is None:
        return default
    v = el.get(attr)
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        try:
            return int(float(v))
        except ValueError:
            return default


def bval(el, attr: str, default=False) -> bool:
    if el is None:
        return default
    v = el.get(attr)
    if v is None:
        return default
    return v in ("1", "true", "on")


def unwrap_mc(el) -> list:
    """mc:AlternateContent を開く: Fallback があればその子、無ければ Choice の子（古い読み手向けの代わりを読む）。"""
    out = []
    for c in el:
        if not isinstance(c.tag, str):
            continue
        if c.tag == MC + "AlternateContent":
            fb = c.find("mc:Fallback", NS)
            pick = fb if fb is not None and len(fb) else c.find("mc:Choice", NS)
            if pick is not None:
                out.extend(x for x in pick if isinstance(x.tag, str))
        else:
            out.append(c)
    return out


# --- 色 ------------------------------------------------------------------------------

PRESET_COLORS = {
    "black": "000000", "white": "FFFFFF", "red": "FF0000", "green": "008000", "blue": "0000FF", "yellow": "FFFF00",
    "gray": "808080", "grey": "808080", "darkGray": "A9A9A9", "lightGray": "D3D3D3", "orange": "FFA500",
    "purple": "800080", "navy": "000080", "teal": "008080", "silver": "C0C0C0", "maroon": "800000", "olive": "808000",
    "lime": "00FF00", "aqua": "00FFFF", "cyan": "00FFFF", "magenta": "FF00FF", "fuchsia": "FF00FF",
    "darkBlue": "00008B", "darkRed": "8B0000", "darkGreen": "006400", "skyBlue": "87CEEB", "pink": "FFC0CB",
    "brown": "A52A2A", "gold": "FFD700", "beige": "F5F5DC", "ltGray": "D3D3D3", "dkGray": "A9A9A9",
}
SYS_COLORS = {"windowText": "000000", "window": "FFFFFF", "btnFace": "F0F0F0", "highlight": "0078D7",
              "menuText": "000000", "captionText": "000000", "grayText": "6D6D6D", "btnText": "000000"}
COLOR_TAGS = ("srgbClr", "schemeClr", "sysClr", "prstClr", "scrgbClr", "hslClr")
DEFAULT_CLRMAP = {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2", "accent1": "accent1", "accent2": "accent2",
                  "accent3": "accent3", "accent4": "accent4", "accent5": "accent5", "accent6": "accent6",
                  "hlink": "hlink", "folHlink": "folHlink"}


def _hex(h: str) -> tuple[int, int, int]:
    h = (h or "000000").strip().lstrip("#")
    if len(h) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        return (0, 0, 0)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _srgb(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


class Colors:
    """テーマの色（clrScheme）と色の対応（clrMap）。`resolve` が色の要素を (r, g, b, a) にする。"""

    def __init__(self, scheme: dict[str, tuple[int, int, int]], clrmap: dict[str, str] | None = None):
        self.scheme = scheme
        self.clrmap = dict(DEFAULT_CLRMAP)
        if clrmap:
            self.clrmap.update(clrmap)

    def with_map(self, clrmap: dict[str, str] | None) -> "Colors":
        if not clrmap:
            return self
        c = Colors(self.scheme, self.clrmap)
        c.clrmap.update(clrmap)
        return c

    def scheme_rgb(self, name: str) -> tuple[int, int, int]:
        name = self.clrmap.get(name, name)
        return self.scheme.get(name, (0, 0, 0))

    def find(self, parent, ph=None):
        """parent の子の色の要素（srgbClr など）を探して解く。無ければ None。"""
        if parent is None:
            return None
        for c in parent:
            if isinstance(c.tag, str) and local(c) in COLOR_TAGS:
                return self.resolve(c, ph)
        return None

    def resolve(self, el, ph=None):
        """色の要素 → (r, g, b, a)。ph は phClr（スタイルの参照で渡る色）。"""
        tag = local(el)
        a = 1.0
        if tag == "srgbClr":
            rgb = _hex(el.get("val"))
        elif tag == "schemeClr":
            v = el.get("val")
            if v == "phClr":
                if ph is None:
                    rgb = (0, 0, 0)
                else:
                    rgb, a = ph[:3], ph[3]
            else:
                rgb = self.scheme_rgb(v)
        elif tag == "sysClr":
            rgb = _hex(el.get("lastClr") or SYS_COLORS.get(el.get("val"), "000000"))
        elif tag == "prstClr":
            rgb = _hex(PRESET_COLORS.get(el.get("val"), "000000"))
        elif tag == "scrgbClr":
            rgb = tuple(round(_srgb(ival(el, k, 0) / 100000) * 255) for k in ("r", "g", "b"))
        elif tag == "hslClr":
            h = ival(el, "hue", 0) / 60000 / 360
            s = ival(el, "sat", 0) / 100000
            l_ = ival(el, "lum", 0) / 100000
            rgb = tuple(round(v * 255) for v in colorsys.hls_to_rgb(h, l_, s))
        else:
            return None
        return apply_mods(rgb, a, el)


def apply_mods(rgb, a, el):
    """色の変化（tint・shade・lumMod・lumOff・satMod・alpha など）を順に掛ける。"""
    r, g, b = (v / 255 for v in rgb)
    for m in el:
        if not isinstance(m.tag, str):
            continue
        n = local(m)
        v = ival(m, "val", 0)
        if n == "alpha":
            a = v / 100000
        elif n == "alphaMod":
            a = a * v / 100000
        elif n == "alphaOff":
            a = a + v / 100000
        elif n in ("tint", "shade"):
            f = v / 100000
            lr, lg, lb = _lin(r), _lin(g), _lin(b)
            if n == "tint":
                lr, lg, lb = (x * f + (1 - f) for x in (lr, lg, lb))
            else:
                lr, lg, lb = (x * f for x in (lr, lg, lb))
            r, g, b = _srgb(lr), _srgb(lg), _srgb(lb)
        elif n in ("lumMod", "lumOff", "satMod", "satOff", "hueMod", "hueOff", "lum", "sat", "hue"):
            h, l_, s = colorsys.rgb_to_hls(r, g, b)
            if n == "lumMod":
                l_ *= v / 100000
            elif n == "lumOff":
                l_ += v / 100000
            elif n == "lum":
                l_ = v / 100000
            elif n == "satMod":
                s *= v / 100000
            elif n == "satOff":
                s += v / 100000
            elif n == "sat":
                s = v / 100000
            elif n == "hueMod":
                h = (h * v / 100000) % 1
            elif n == "hueOff":
                h = (h + v / 60000 / 360) % 1
            elif n == "hue":
                h = v / 60000 / 360
            r, g, b = colorsys.hls_to_rgb(h, max(0, min(1, l_)), max(0, min(1, s)))
        elif n == "gray":
            y = 0.2126 * r + 0.7152 * g + 0.0722 * b
            r = g = b = y
        elif n == "inv":
            r, g, b = 1 - r, 1 - g, 1 - b
        elif n == "comp":
            h, l_, s = colorsys.rgb_to_hls(r, g, b)
            r, g, b = colorsys.hls_to_rgb((h + 0.5) % 1, l_, s)
        elif n in ("red", "green", "blue", "redMod", "greenMod", "blueMod", "redOff", "greenOff", "blueOff"):
            f = v / 100000
            if n == "red":
                r = f
            elif n == "green":
                g = f
            elif n == "blue":
                b = f
            elif n == "redMod":
                r *= f
            elif n == "greenMod":
                g *= f
            elif n == "blueMod":
                b *= f
            elif n == "redOff":
                r += f
            elif n == "greenOff":
                g += f
            elif n == "blueOff":
                b += f
    clamp = lambda x: max(0, min(255, round(x * 255)))  # noqa: E731
    return (clamp(r), clamp(g), clamp(b), max(0.0, min(1.0, a)))


def css_color(c) -> str:
    if c is None:
        return "transparent"
    r, g, b, a = c
    if a >= 0.999:
        return f"#{r:02x}{g:02x}{b:02x}"
    return f"rgba({r}, {g}, {b}, {round(a, 3)})"


def hex_of(c) -> str:
    r, g, b = c[:3]
    return f"#{r:02x}{g:02x}{b:02x}"


# --- テーマ ----------------------------------------------------------------------------

class Theme:
    """テーマの色・字体・書式（fmtScheme）。"""

    def __init__(self, root):
        self.root = root
        scheme: dict[str, tuple[int, int, int]] = {}
        cs = q(root, "a:themeElements/a:clrScheme")
        if cs is not None:
            for c in cs:
                if not isinstance(c.tag, str):
                    continue
                col = Colors({}).find(c)
                if col is not None:
                    scheme[local(c)] = col[:3]
        for k, v in (("dk1", (0, 0, 0)), ("lt1", (255, 255, 255)), ("dk2", (68, 84, 106)), ("lt2", (231, 230, 230)),
                     ("accent1", (68, 114, 196)), ("accent2", (237, 125, 49)), ("accent3", (165, 165, 165)),
                     ("accent4", (255, 192, 0)), ("accent5", (91, 155, 213)), ("accent6", (112, 173, 71)),
                     ("hlink", (5, 99, 193)), ("folHlink", (149, 79, 114))):
            scheme.setdefault(k, v)
        self.scheme = scheme
        fs = q(root, "a:themeElements/a:fontScheme")
        self.fonts = {}
        for kind, tag in (("mj", "majorFont"), ("mn", "minorFont")):
            f = q(fs, f"a:{tag}")
            latin = (q(f, "a:latin").get("typeface") if q(f, "a:latin") is not None else "") or ""
            ea = (q(f, "a:ea").get("typeface") if q(f, "a:ea") is not None else "") or ""
            jpan = ""
            for x in qa(f, "a:font"):
                if x.get("script") == "Jpan":
                    jpan = x.get("typeface") or ""
            self.fonts[kind + "-lt"] = latin
            self.fonts[kind + "-ea"] = ea or jpan
            self.fonts[kind + "-cs"] = latin
        fmt = q(root, "a:themeElements/a:fmtScheme")
        self.fill_styles = children(q(fmt, "a:fillStyleLst"))
        self.line_styles = children(q(fmt, "a:lnStyleLst"))
        self.bg_styles = children(q(fmt, "a:bgFillStyleLst"))

    def font(self, typeface: str | None) -> str | None:
        """`+mj-lt` などのテーマの字体を名前にする。"""
        if not typeface:
            return None
        if typeface.startswith("+"):
            return self.fonts.get(typeface[1:]) or None
        return typeface


# --- 塗り・線 --------------------------------------------------------------------------

FILL_TAGS = ("noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill")


class Fill:
    """塗り。kind: none・solid・grad・blip・patt・group。"""

    def __init__(self, kind: str, color=None, stops=None, angle: float = 90.0, radial: bool = False, blip=None):
        self.kind, self.color, self.stops, self.angle, self.radial, self.blip = kind, color, stops or [], angle, radial, blip

    def main_color(self):
        if self.kind == "solid":
            return self.color
        if self.kind in ("grad", "patt") and self.stops:
            return self.stops[0][1]
        return None

    def __repr__(self):
        return f"Fill({self.kind}, {self.color}, {len(self.stops)} stops)"


NO_FILL = Fill("none")


def fill_of(el, colors: Colors, ph=None):
    """塗りの要素（solidFill など）→ Fill。塗りの要素でなければ None。"""
    if el is None:
        return None
    n = local(el)
    if n == "noFill":
        return NO_FILL
    if n == "solidFill":
        c = colors.find(el, ph)
        return Fill("solid", c if c is not None else (0, 0, 0, 1.0))
    if n == "gradFill":
        stops = []
        for gs in qa(el, "a:gsLst/a:gs"):
            c = colors.find(gs, ph)
            if c is not None:
                stops.append((ival(gs, "pos", 0) / 100000, c))
        stops.sort(key=lambda s: s[0])
        lin = q(el, "a:lin")
        path = q(el, "a:path")
        ang = ival(lin, "ang", 0) / 60000 if lin is not None else 90.0
        if not stops:
            return NO_FILL
        return Fill("grad", stops=stops, angle=ang if path is None else 90.0, radial=path is not None)
    if n == "blipFill":
        return Fill("blip", blip=el)
    if n == "pattFill":
        fg = colors.find(q(el, "a:fgClr"), ph) or (0, 0, 0, 1.0)
        bg = colors.find(q(el, "a:bgClr"), ph) or (255, 255, 255, 1.0)
        # 模様は写せない。前景と背景を混ぜた色で塗る（模様の種類は報告に出す）
        mix = tuple(round(fg[i] * 0.35 + bg[i] * 0.65) for i in range(3)) + (max(fg[3], bg[3]),)
        f = Fill("patt", color=mix, stops=[(0, mix)])
        f.pattern = el.get("prst") or "?"
        return f
    if n == "grpFill":
        return Fill("group")
    return None


def first_fill(parent, colors: Colors, ph=None):
    if parent is None:
        return None
    for c in parent:
        if isinstance(c.tag, str) and local(c) in FILL_TAGS:
            return fill_of(c, colors, ph)
    return None


def style_ref(style_el, name: str, colors: Colors):
    """p:style の lnRef・fillRef・effectRef・fontRef → (idx, 色)。"""
    ref = child(style_el, name)
    if ref is None:
        return None, None
    return ref.get("idx"), colors.find(ref)


def ref_fill(theme: Theme, idx, ph, colors: Colors):
    """fillRef の番号 → テーマの塗り（1〜 は fillStyleLst、1001〜 は bgFillStyleLst。0 は塗らない）。"""
    try:
        i = int(idx)
    except (TypeError, ValueError):
        return None
    if i == 0:
        return NO_FILL
    lst, k = (theme.bg_styles, i - 1001) if i >= 1001 else (theme.fill_styles, i - 1)
    if 0 <= k < len(lst):
        return fill_of(lst[k], colors, ph)
    return None


class Line:
    def __init__(self, color=None, width: float = 0.0, dash: str | None = None, head=None, tail=None, fill=None):
        self.color, self.width, self.dash, self.head, self.tail = color, width, dash, head, tail
        self.fill = fill

    @property
    def visible(self) -> bool:
        return self.color is not None and self.color[3] > 0.002 and self.width > 0


def line_of(sppr, style_el, theme: Theme, colors: Colors) -> Line:
    """線（spPr の a:ln と p:style の lnRef）。幅は EMU のまま（呼ぶ側が px に直す）。"""
    ln = q(sppr, "a:ln")
    base = None
    idx, refc = style_ref(style_el, "lnRef", colors)
    if idx is not None:
        try:
            k = int(idx) - 1
        except ValueError:
            k = -1
        if 0 <= k < len(theme.line_styles):
            base = theme.line_styles[k]
    color = None
    width = None
    dash = None
    head = tail = None
    for src, ph in ((base, refc), (ln, refc)):
        if src is None:
            continue
        f = first_fill(src, colors, ph)
        if f is not None:
            color = f.main_color() if f.kind != "none" else None
        w = ival(src, "w")
        if w is not None:
            width = w
        pd = q(src, "a:prstDash")
        if pd is not None:
            dash = pd.get("val")
        he, te = q(src, "a:headEnd"), q(src, "a:tailEnd")
        if he is not None:
            head = he.get("type")
        if te is not None:
            tail = te.get("type")
    if ln is not None and first_fill(ln, colors, refc) is None and base is None:
        # a:ln はあるが塗りが無く、スタイルの参照も無い: 線は描かない（PowerPoint の既定）
        color = None
    if width is None:
        width = 9525 if color is not None else 0
    return Line(color, width, dash if dash not in (None, "solid") else None,
                head if head not in (None, "none") else None, tail if tail not in (None, "none") else None)


# --- 文字の書式の継承 ---------------------------------------------------------------------

LVL_TAGS = [f"lvl{i}pPr" for i in range(1, 10)]


def lvl_of(lst, level: int):
    """a:lstStyle（や p:titleStyle）の何段目かの段落の書式。"""
    if lst is None:
        return None
    return child(lst, LVL_TAGS[max(0, min(8, level))])


class TextChain:
    """文字の書式の継承の列。手前ほど強い: 段落・図形の lstStyle・レイアウトのプレースホルダー・マスターのプレースホルダー・
    マスターの txStyles（title・body・other）・プレゼンテーションの既定（defaultTextStyle）。"""

    def __init__(self, lists: list, theme: Theme, colors: Colors, font_ref=None, own: int = 1):
        # fontRef（p:style の字体と色の参照）は、図形自身の lstStyle（先頭の own 個）より弱く、プレゼンテーションの既定より強い
        self.own = sum(1 for x in lists[:own] if x is not None)
        self.lists = [x for x in lists if x is not None]
        self.theme = theme
        self.colors = colors
        self.font_ref = font_ref       # p:style の fontRef（色と字体の既定）

    def ppr_chain(self, ppr, level: int) -> list:
        out = [ppr] if ppr is not None else []
        out += [x for x in (lvl_of(lst, level) for lst in self.lists) if x is not None]
        return out

    def para(self, ppr, level: int) -> dict:
        chain = self.ppr_chain(ppr, level)

        def attr(name, conv=str):
            for e in chain:
                v = e.get(name)
                if v is not None:
                    try:
                        return conv(v)
                    except ValueError:
                        continue
            return None

        def sub(name):
            for e in chain:
                s = child(e, name)
                if s is not None:
                    return s
            return None

        def spacing(name):
            s = sub(name)
            if s is None:
                return None
            pct, pts = child(s, "spcPct"), child(s, "spcPts")
            if pct is not None:
                return ("pct", ival(pct, "val", 100000) / 100000)
            if pts is not None:
                return ("pts", ival(pts, "val", 0) / 100)
            return None

        bullet = None
        for e in chain:                                  # 記号の種類は一番手前の指定（buNone・buChar・buAutoNum・buBlip）
            hit = None
            for c in e:
                if isinstance(c.tag, str) and local(c) in ("buNone", "buChar", "buAutoNum", "buBlip"):
                    hit = c
                    break
            if hit is not None:
                n = local(hit)
                if n == "buChar":
                    bullet = ("char", hit.get("char") or "•")
                elif n == "buAutoNum":
                    bullet = ("num", hit.get("type") or "arabicPeriod", ival(hit, "startAt", 1))
                elif n == "buBlip":
                    bullet = ("char", "•")
                break
        bu_font = None
        bf = sub("buFont")
        if bf is not None:
            bu_font = self.theme.font(bf.get("typeface"))
        bu_color = None
        bc = sub("buClr")
        if bc is not None:
            bu_color = self.colors.find(bc)
        bu_size = None
        bs = sub("buSzPct")
        if bs is not None:
            bu_size = ival(bs, "val", 100000) / 100000
        return {
            "algn": attr("algn") or "l", "marL": attr("marL", int) or 0, "indent": attr("indent", int) or 0,
            "lnSpc": spacing("lnSpc"), "spcBef": spacing("spcBef"), "spcAft": spacing("spcAft"),
            "bullet": bullet, "bu_font": bu_font, "bu_color": bu_color, "bu_size": bu_size,
            "rtl": attr("rtl") == "1",
        }

    def run(self, rpr, ppr, level: int) -> dict:
        """文字の書式（大きさ pt・太字・斜体・下線・取り消し・色・字体・字間・上付き・大文字）。"""
        chain = ([rpr] if rpr is not None else [])
        if ppr is not None and child(ppr, "defRPr") is not None:
            chain.append(child(ppr, "defRPr"))
        cut = None                     # fontRef を挟む位置（chain の何番目の前か）
        for k, lst in enumerate(self.lists):
            if k == self.own:
                cut = len(chain)
            lv = lvl_of(lst, level)
            d = child(lv, "defRPr") if lv is not None else None
            if d is not None:
                chain.append(d)
        if cut is None:
            cut = len(chain)

        def attr(name, conv=str):
            for e in chain:
                v = e.get(name)
                if v is not None:
                    try:
                        return conv(v)
                    except ValueError:
                        continue
            return None

        color = None
        ref_color = self.colors.find(self.font_ref) if self.font_ref is not None else None
        ref_k = {"major": "mj", "minor": "mn"}.get(self.font_ref.get("idx")) if self.font_ref is not None else None
        for i, e in enumerate(chain):
            if i == cut and ref_color is not None:
                color = ref_color
                break
            f = first_fill(e, self.colors)
            if f is not None:
                color = f.main_color() if f.kind != "none" else (0, 0, 0, 0.0)
                break
        if color is None:
            color = ref_color
        latin = ea = None
        for i, e in enumerate(chain):
            if i == cut and ref_k:
                latin = latin or self.theme.fonts.get(ref_k + "-lt")
                ea = ea or self.theme.fonts.get(ref_k + "-ea")
            if latin is None and child(e, "latin") is not None:
                latin = self.theme.font(child(e, "latin").get("typeface"))
            if ea is None and child(e, "ea") is not None:
                ea = self.theme.font(child(e, "ea").get("typeface"))
        if ref_k:
            latin = latin or self.theme.fonts.get(ref_k + "-lt")
            ea = ea or self.theme.fonts.get(ref_k + "-ea")
        latin = latin or self.theme.fonts.get("mn-lt")
        ea = ea or self.theme.fonts.get("mn-ea")
        hl = None
        if rpr is not None and child(rpr, "hlinkClick") is not None:
            hl = self.colors.scheme_rgb("hlink") + (1.0,)
        u = attr("u")
        return {
            "sz": (attr("sz", int) or 1800) / 100, "b": attr("b") in ("1", "true"), "i": attr("i") in ("1", "true"),
            "u": bool(u and u != "none") or hl is not None, "strike": (attr("strike") or "noStrike") != "noStrike",
            "color": hl or (color if color is not None else (0, 0, 0, 1.0)), "latin": latin, "ea": ea,
            "spc": (attr("spc", int) or 0) / 100, "baseline": (attr("baseline", int) or 0) / 1000,
            "cap": attr("cap") or "none",
        }


def body_props(chain: list) -> dict:
    """a:bodyPr の継承（手前ほど強い）。余白は EMU。"""
    def attr(name, default=None, conv=str):
        for e in chain:
            if e is None:
                continue
            v = e.get(name)
            if v is not None:
                try:
                    return conv(v)
                except ValueError:
                    continue
        return default

    autofit = None
    scale, lnred = 1.0, 0.0
    for e in chain:
        if e is None:
            continue
        hit = None
        for c in e:
            if isinstance(c.tag, str) and local(c) in ("normAutofit", "spAutoFit", "noAutofit"):
                hit = c
                break
        if hit is not None:
            autofit = local(hit)
            if autofit == "normAutofit":
                scale = ival(hit, "fontScale", 100000) / 100000
                lnred = ival(hit, "lnSpcReduction", 0) / 100000
            break
    return {
        "lIns": attr("lIns", 91440, int), "rIns": attr("rIns", 91440, int), "tIns": attr("tIns", 45720, int),
        "bIns": attr("bIns", 45720, int), "anchor": attr("anchor", "t"), "wrap": attr("wrap", "square"),
        "vert": attr("vert", "horz"), "rot": attr("rot", 0, int), "autofit": autofit, "scale": scale, "lnred": lnred,
        "upright": attr("upright") in ("1", "true"),
    }
