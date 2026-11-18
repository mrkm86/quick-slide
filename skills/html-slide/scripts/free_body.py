"""自由なページ（型 `free`）の中身（body）を読む（ADR 0030）。

書く側が書いた body は、そのまま HTML に差し込まない。ここで解析し、**許した要素・属性・CSS だけ**を通し、
部品（アイコン・割合のマス・棒）を展開し、文字の色を「いちばん近い面」に合わせて決めてから、HTML の断片を返す。

    html, info = render(body, pal, anim)

- 誤りは FreeError（呼ぶ側が台本の誤りとして止める）。文には、何をどう直すかを書く
- pal は型が使う色の役（deck.palette）。色の組の比（4.5:1）を、ブラウザを使わずにここで確かめる
- anim(step, fx, delay, name) は、動きの属性を作る関数（Slide.anim。配る資料・steps: false の扱いは呼ぶ側と同じになる）
- info は {"warnings": [...], "parts": 部品のおおよその数}
"""
from __future__ import annotations

import html as _html
import math
import re
from html.parser import HTMLParser

import deck_icons as ICON
from deck_palette import ratio

SCALE = (200, 160, 120, 96, 80, 64, 57, 48, 45, 36, 32, 28, 26, 24, 22)   # 文字の段（deck_types.SCALE のうち、書いてよいもの）
LINE_HEIGHTS = ("1", "1.0", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.75")
MIN_PX = 22
CONTRAST = 4.5
SHAPE_CONTRAST = 3.0     # 図形（アイコン・マス・棒・線）が、地の上で見える比
BODY_PX = 28             # 本文の下限。これより小さい段は、注記（f-note）と札（f-chip）だけ
SMALL_OK = ("f-note", "f-chip")
BAR_APART = 64           # 強める棒とふつうの棒の色の隔たり（RGB の距離）の下限。色相が違えば明るさが近くても見分けられるので、明るさの比でなく距離で見る


def _apart(a: str, b: str) -> float:
    """2 つの色（#rrggbb）の RGB の距離。"""
    return sum((int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) ** 2 for i in (1, 3, 5)) ** 0.5
FX = ("rise", "fade", "pop", "wipe-right", "wipe-left", "wipe-up", "wipe-down", "wheel")

BLOCKS = ("div",)
INLINES = ("span", "b", "em")
VOIDS = ("br",)
ATTRS = {"class", "style", "data-icon", "data-size", "data-total", "data-on", "data-cols", "data-max", "data-value",
         "data-step", "data-fx", "data-delay", "data-name"}

# 色に書ける変数（地・文字の両方）
COLOR_VARS = ("bg", "ink", "muted", "primary-soft", "accent-soft", "primary-fill", "on-primary-fill", "accent-fill", "on-accent-fill",
              "deep", "on-deep", "deep-pop", "primary-text", "accent-text", "success-2", "error-2")

# 面（地の色）ごとの、上に載る文字の色の役。text＝ふつうの文字・muted＝補足・strong＝強める文字と数字・mark＝図形（アイコン・棒・マス）
SURFACES = {
    "page": {"bg": "bg", "text": "ink", "muted": "muted", "strong": "primary-text", "mark": "primary-fill", "on-mark": "on-primary-fill",
             "pop": "accent-fill", "on-pop": "on-accent-fill", "off": "mark-off"},
    "soft": {"bg": "primary-soft", "text": "ink", "muted": "muted", "strong": "primary-text", "mark": "primary-fill", "on-mark": "on-primary-fill",
             "pop": "accent-fill", "on-pop": "on-accent-fill", "off": "mark-off"},
    "fill": {"bg": "primary-fill", "text": "on-primary-fill", "muted": "on-primary-fill", "strong": "on-primary-fill", "mark": "on-primary-fill",
             "on-mark": "primary-fill", "pop": "on-primary-fill", "on-pop": "primary-fill", "off": "fill-off"},
    "accent": {"bg": "accent-fill", "text": "on-accent-fill", "muted": "on-accent-fill", "strong": "on-accent-fill", "mark": "on-accent-fill",
               "on-mark": "accent-fill", "pop": "on-accent-fill", "on-pop": "accent-fill", "off": "pop-off"},
    "deep": {"bg": "deep", "text": "on-deep", "muted": "on-deep", "strong": "deep-pop", "mark": "deep-pop", "on-mark": "deep",
             "pop": "deep-pop", "on-pop": "deep", "off": "deep-off"},
    "good": {"bg": "tone-good-bg", "text": "ink", "muted": "muted", "strong": "tone-good-text", "mark": "tone-good-text", "on-mark": "tone-good-bg",
             "pop": "tone-good-text", "on-pop": "tone-good-bg", "off": "bg"},
    "bad": {"bg": "tone-bad-bg", "text": "ink", "muted": "muted", "strong": "tone-bad-text", "mark": "tone-bad-text", "on-mark": "tone-bad-bg",
            "pop": "tone-bad-text", "on-pop": "tone-bad-bg", "off": "bg"},
    "warn": {"bg": "tone-warn-bg", "text": "ink", "muted": "muted", "strong": "tone-warn-text", "mark": "tone-warn-text", "on-mark": "tone-warn-bg",
             "pop": "tone-warn-text", "on-pop": "tone-warn-bg", "off": "bg"},
}
BOX_KINDS = ("fill", "deep", "accent", "good", "bad", "warn")
OFFS = ("mark-off", "deep-off", "fill-off", "pop-off")      # マスの「塗っていない側」の候補（地に近い薄い面）
# style で地の色を直に書いたとき、その地を面として読む（上の文字の既定の色を決める）
BG_SURFACE = {"bg": "page", "primary-soft": "soft", "accent-soft": "soft", "primary-fill": "fill", "accent-fill": "accent", "deep": "deep"}

LAYOUT = {"f-row", "f-col", "f-grow"}
ROLES = {"f-num", "f-label", "f-note", "f-muted", "f-strong"}
PARTS = {"f-box", "f-icon", "f-cells", "f-bars", "f-bar", "f-chip", "f-no", "f-line"}
MODIFIERS = set(BOX_KINDS) | {"disc", "on", "top"}
CLASSES = LAYOUT | ROLES | PARTS | MODIFIERS

KEYWORDS = {
    "display": ("flex",),
    "flex-direction": ("row", "column"),
    "flex-wrap": ("wrap", "nowrap"),
    "align-items": ("flex-start", "center", "flex-end", "stretch", "baseline"),
    "align-self": ("flex-start", "center", "flex-end", "stretch"),
    "justify-content": ("flex-start", "center", "flex-end", "space-between", "space-around", "space-evenly"),
    "text-align": ("left", "center", "right"),
    "white-space": ("nowrap", "normal"),
    "font-weight": ("400", "700"),
}
LENGTHS = ("gap", "width", "height", "min-width", "min-height", "max-width", "max-height", "padding", "margin")
LENGTH_SIDES = tuple(f"{p}-{s}" for p in ("padding", "margin") for s in ("top", "right", "bottom", "left"))
RADII = ("0", "8px", "12px", "16px", "24px", "32px", "9999px")


class FreeError(Exception):
    """body の誤り（台本を直す）。"""


class Node:
    __slots__ = ("tag", "attrs", "kids", "text", "line")

    def __init__(self, tag, attrs=None, text=None, line=0):
        self.tag, self.attrs, self.kids, self.text, self.line = tag, dict(attrs or {}), [], text, line

    @property
    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    def has(self, cls: str) -> bool:
        return cls in self.classes


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]

    def _where(self) -> str:
        line, col = self.getpos()
        return f"{line} 行目 {col + 1} 字目"

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag not in BLOCKS + INLINES + VOIDS:
            raise FreeError(f"body の {self._where()}: <{tag}> は書けない（使えるのは {'・'.join(BLOCKS + INLINES + VOIDS)}）")
        node = Node(tag, line=self.getpos()[0])
        for k, v in attrs:
            k = k.lower()
            if k not in ATTRS:
                raise FreeError(f"body の {self._where()}: <{tag}> の属性 {k} は書けない（使えるのは {'・'.join(sorted(ATTRS))}）")
            if k in node.attrs:
                raise FreeError(f"body の {self._where()}: <{tag}> に {k} が 2 回ある")
            node.attrs[k] = v or ""
        self.stack[-1].kids.append(node)
        if tag not in VOIDS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        if tag.lower() not in BLOCKS + INLINES + VOIDS:      # 独自のタグは、閉じ方より先に「書けない」と言う
            raise FreeError(f"body の {self._where()}: <{tag.lower()}> は書けない（使えるのは {'・'.join(BLOCKS + INLINES + VOIDS)}。"
                            "アイコンは <span class=\"f-icon\" data-icon=\"名前\"></span>）")
        if tag.lower() not in VOIDS:
            raise FreeError(f"body の {self._where()}: <{tag.lower()} /> のように閉じる書き方は使えない（<{tag.lower()} …></{tag.lower()}> と書く）")
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in VOIDS:
            return
        if len(self.stack) == 1 or self.stack[-1].tag != tag:
            open_ = self.stack[-1].tag if len(self.stack) > 1 else "（開いている要素なし）"
            raise FreeError(f"body の {self._where()}: </{tag}> が合わない（開いているのは <{open_}>）")
        self.stack.pop()

    def handle_data(self, data):
        if data.strip():
            self.stack[-1].kids.append(Node("#text", text=data, line=self.getpos()[0]))

    def handle_comment(self, data):
        raise FreeError(f"body の {self._where()}: コメントは書けない")

    def handle_decl(self, decl):
        raise FreeError(f"body の {self._where()}: <!{decl[:20]}…> は書けない")

    handle_pi = unknown_decl = handle_decl


def parse(body: str) -> Node:
    p = _Parser()
    p.feed(body)
    # 末尾の閉じていないタグ（属性の値の中に > があるものも）は、close() が黙って捨てる。読み残しを先に見て止める
    m = re.search(r"<[A-Za-z/!?].*", p.rawdata, re.S)
    if m:
        raise FreeError(f"body の末尾: タグ「{m.group(0)[:20]}」が > で閉じていない")
    p.close()
    if len(p.stack) > 1:
        raise FreeError(f"body: <{p.stack[-1].tag}>（{p.stack[-1].line} 行目）が閉じていない")
    if not p.root.kids:
        raise FreeError("body が空")
    return p.root


_VAR = re.compile(r"^var\(--([a-z0-9-]+)\)$")
_PX = re.compile(r"^(\d+)px$")
_PCT = re.compile(r"^(\d+(?:\.\d+)?)%$")


def _length(prop: str, value: str, warns: list, where: str) -> str:
    out = []
    for tok in value.split():
        if tok in ("0", "auto") and (tok == "0" or prop.startswith("margin")):
            out.append(tok)
            continue
        m = _PX.match(tok)
        if m:
            if int(m.group(1)) % 8:
                warns.append(f"{where}: {prop}: {tok} が 8 の倍数でない")
            out.append(tok)
            continue
        if _PCT.match(tok):
            out.append(tok)
            continue
        raise FreeError(f"{where}: {prop}: {value} は書けない（px の整数か %）")
    if not out or len(out) > (4 if prop in ("padding", "margin") else 1):
        raise FreeError(f"{where}: {prop}: {value} は書けない")
    return " ".join(out)


def style_of(node: Node, warns: list) -> dict[str, str]:
    """要素の style を読み、許した書き方だけを {プロパティ: 値} で返す。色は変数の名前（`bg` など）にして返す。"""
    where = f"body の {node.line} 行目の <{node.tag}> の style"
    out: dict[str, str] = {}
    for decl in (node.attrs.get("style") or "").split(";"):
        if not decl.strip():
            continue
        if ":" not in decl:
            raise FreeError(f"{where}: 「{decl.strip()}」は プロパティ: 値 の形でない")
        prop, value = (x.strip().lower() for x in decl.split(":", 1))
        if prop in out:
            raise FreeError(f"{where}: {prop} が 2 回ある")
        if prop in ("color", "background-color"):
            m = _VAR.match(value)
            if not m or m.group(1) not in COLOR_VARS:
                raise FreeError(f"{where}: {prop}: {value} は書けない（色は var(--名前) だけ。名前は {'・'.join(COLOR_VARS)}）")
            out[prop] = m.group(1)
        elif prop == "font-size":
            m = _PX.match(value)
            if not m or int(m.group(1)) not in SCALE:
                raise FreeError(f"{where}: font-size: {value} は書けない（{'・'.join(map(str, SCALE))} の px だけ。本文は 28 以上・注記は {MIN_PX} 以上）")
            out[prop] = value
        elif prop == "line-height":
            if value not in LINE_HEIGHTS:
                raise FreeError(f"{where}: line-height: {value} は書けない（{'・'.join(LINE_HEIGHTS[1:])} のどれか）")
            out[prop] = value
        elif prop in KEYWORDS:
            if value not in KEYWORDS[prop]:
                raise FreeError(f"{where}: {prop}: {value} は書けない（{' / '.join(KEYWORDS[prop])}）")
            out[prop] = value
        elif prop == "flex":
            if not re.fullmatch(r"none|\d+(\.\d+)?", value):
                raise FreeError(f"{where}: flex: {value} は書けない（数か none）")
            out[prop] = value if value == "none" else f"{value} 1 0px"
        elif prop in LENGTHS or prop in LENGTH_SIDES:
            out[prop] = _length(prop, value, warns, where)
        elif prop == "border-radius":
            if value not in RADII:
                raise FreeError(f"{where}: border-radius: {value} は書けない（{' / '.join(RADII)}）")
            out[prop] = value
        else:
            hint = "（position・top・left・transform は使えない。箱の中は flex で割る）" if prop in ("position", "top", "left", "right", "bottom", "transform") else ""
            raise FreeError(f"{where}: {prop} は書けない{hint}")
    return out


def _int(node: Node, key: str, lo: int, hi: int) -> int:
    v = node.attrs.get(key)
    if v is None or not re.fullmatch(r"\d+", v) or not lo <= int(v) <= hi:
        raise FreeError(f"body の {node.line} 行目: {key} は {lo}〜{hi} の整数（{v!r}）")
    return int(v)


def _num(node: Node, key: str) -> float:
    v = node.attrs.get(key)
    if v is None or not re.fullmatch(r"\d+(\.\d+)?", v):
        raise FreeError(f"body の {node.line} 行目: {key} は 0 以上の数（{v!r}）")
    return float(v)


def _css(d: dict[str, str]) -> str:
    return " ".join(f"{k}: {v};" for k, v in d.items())


def _var(name: str) -> str:
    return f"var(--{name})"


class _Render:
    def __init__(self, pal: dict, anim):
        self.pal, self.anim = pal, anim
        self.warns: list[str] = []
        self.parts = 0
        self.in_step = self.in_name = 0     # 段・まとまりの中にいる深さ（入れ子を止める）

    # --- 検査 ---
    def classes(self, node: Node) -> list[str]:
        cs = node.classes
        bad = [c for c in cs if c not in CLASSES]
        if bad:
            raise FreeError(f"body の {node.line} 行目: class {'・'.join(bad)} は無い（使えるのは {'・'.join(sorted(CLASSES))}）")
        return cs

    def contrast(self, node: Node, fg: str, bg: str) -> None:
        r = ratio(self.pal[fg], self.pal[bg])
        if r < CONTRAST - 1e-9:
            raise FreeError(f"body の {node.line} 行目: 文字の色 --{fg} が、地の色 --{bg} の上で読めない（コントラスト {r:.2f} が {CONTRAST} 未満）。"
                            "文字の色を書かずに面の箱（f-box・f-box fill・f-box deep）に任せるか、地に合う色にする")

    def only(self, node: Node, what: str, attrs: tuple, st: dict | None = None, styles: tuple = ()) -> None:
        """部品が読む属性・style だけを通す（書けたのに効かない、を無くす）。"""
        bad = sorted(set(node.attrs) - set(attrs) - {"class"} - ({"style"} if st is not None else set()))
        if bad:
            raise FreeError(f"body の {node.line} 行目: {what} に {'・'.join(bad)} は書けない（使えるのは {'・'.join(a for a in attrs)}）")
        extra = sorted(set(st or {}) - set(styles))
        if extra:
            can = f"使えるのは {'・'.join(styles)}" if styles else "style は書けない"
            raise FreeError(f"body の {node.line} 行目: {what} の style に {'・'.join(extra)} は書けない（{can}）")

    def pick(self, node: Node, what: str, bg: str, names) -> str:
        """図形の色: names の順に、地（bg）の上で 3:1 に届く最初の色。面の候補に無ければ文字の色・地の色も試し、それでも無ければ誤り
        （style で地の色を書いた箱の中では、面の既定の色が合わないことがある。見えないまま出さない）。"""
        for n in dict.fromkeys((*names, "ink", "bg")):
            if ratio(self.pal[n], self.pal[bg]) >= SHAPE_CONTRAST - 1e-9:
                return n
        raise FreeError(f"body の {node.line} 行目: {what} が、地の色 --{bg} の上で見える色が無い（面の箱か、別の地に置く）")

    def motion(self, node: Node, default_name: str | None = None) -> str:
        """動き（data-step・data-fx・data-delay）とまとまりの名前（data-name）の属性。段の中の段・名前の中の名前は止める。"""
        a = node.attrs
        name = a.get("data-name")
        if name is not None:
            if not name.strip():
                raise FreeError(f"body の {node.line} 行目: data-name が空")
            if self.in_name:
                raise FreeError(f"body の {node.line} 行目: data-name の付いた要素の中に、もう 1 つ data-name は書けない（まとまりは外側の 1 つ）")
        elif not self.in_name:
            name = default_name
        if "data-step" not in a:
            if "data-fx" in a or "data-delay" in a:
                raise FreeError(f"body の {node.line} 行目: data-fx・data-delay は data-step と一緒に書く")
            return f' data-name="{_html.escape(name, quote=True)}"' if name else ""
        if node.tag != "div":
            raise FreeError(f"body の {node.line} 行目: data-step は <div> に付ける（文字の中の要素・<span> のアイコンには付けられない。<div> で包む）")
        if self.in_step:
            raise FreeError(f"body の {node.line} 行目: data-step の付いた要素の中に、もう 1 つ data-step は書けない（段は外側の 1 つ。並べて書く）")
        step = _int(node, "data-step", 0, 20)
        fx = a.get("data-fx", "rise")
        if fx not in FX:
            raise FreeError(f"body の {node.line} 行目: data-fx は {' / '.join(FX)} のどれか（{fx!r}）")
        delay = _int(node, "data-delay", 0, 10000) if "data-delay" in a else 0
        return " " + self.anim(step, fx, delay, name)

    # --- 組み立て ---
    def surface_of(self, node: Node, st: dict, cs: list[str], cur: str) -> str:
        if "f-box" in cs:
            kinds = [k for k in BOX_KINDS if k in cs]
            if len(kinds) > 1:
                raise FreeError(f"body の {node.line} 行目: f-box の種類（{'・'.join(kinds)}）は 1 つだけ")
            return kinds[0] if kinds else "soft"
        if "background-color" in st:
            return BG_SURFACE.get(st["background-color"], cur)
        return cur

    def text_color(self, node: Node, st: dict, cs: list[str], surf: str, bg: str) -> str:
        s = SURFACES[surf]
        if "color" in st:
            fg = st["color"]
        elif "f-num" in cs or "f-strong" in cs:
            fg = s["strong"]
        elif "f-muted" in cs or "f-note" in cs:
            fg = s["muted"]
        else:
            fg = s["text"]
        self.contrast(node, fg, bg)
        return fg

    def small(self, node: Node, st: dict, ok: bool) -> None:
        if "font-size" in st and int(st["font-size"][:-2]) < BODY_PX and not ok:
            raise FreeError(f"body の {node.line} 行目: font-size: {st['font-size']} は本文に使えない（本文は {BODY_PX} 以上。"
                            f"{MIN_PX}〜26 は注記の <div class=\"f-note\"> と札の f-chip だけ）")

    def inline(self, node: Node, surf: str, bg: str, small: bool = False) -> str:
        """文字の中身（文字・br・b・em・span）。small は、28 より小さい段を書いてよい所（注記・札の中）。"""
        out = []
        for k in node.kids:
            if k.tag == "#text":
                out.append(_html.escape(k.text, quote=False))
            elif k.tag == "br":
                out.append("<br>")
            elif k.tag in INLINES:
                cs = self.classes(k)
                if any(c in PARTS | LAYOUT for c in cs):
                    raise FreeError(f"body の {k.line} 行目: <{k.tag}> に class {'・'.join(cs)} は付けられない（文字の中では f-strong・f-muted だけ）")
                data = sorted(a for a in k.attrs if a.startswith("data-"))
                if data:
                    raise FreeError(f"body の {k.line} 行目: 文字の中の <{k.tag}> に {'・'.join(data)} は書けない（動きやまとまりは <div> に付ける）")
                st = style_of(k, self.warns)
                extra = sorted(set(st) - {"color", "font-weight", "font-size"})
                if extra:
                    raise FreeError(f"body の {k.line} 行目: 文字の中の <{k.tag}> の style に {'・'.join(extra)} は書けない")
                self.small(k, st, small)
                css = {}
                if "color" in st or cs or k.tag == "em":
                    fg = st.get("color") or SURFACES[surf]["strong" if (k.tag == "em" or "f-strong" in cs) else "muted" if "f-muted" in cs else "text"]
                    self.contrast(k, fg, bg)
                    css["color"] = _var(fg)
                if k.tag in ("b", "em") or "f-strong" in cs:
                    css["font-weight"] = "700"
                for p in ("font-size", "font-weight"):
                    if p in st:
                        css[p] = st[p]
                tag = "span" if k.tag == "em" else k.tag          # em は雛形で差し色が付く。色はここで決めるので span にする
                out.append(f'<{tag} style="{_css(css)}">{self.inline(k, surf, bg, small)}</{tag}>' if css else f"<{tag}>{self.inline(k, surf, bg, small)}</{tag}>")
            else:
                raise FreeError(f"body の {k.line} 行目: 文字と <{k.tag}> を同じ要素に並べられない（文字は <div> で包む）")
        return "".join(out)

    def icon(self, node: Node, cs: list[str], st: dict, surf: str, bg: str) -> str:
        self.only(node, "f-icon", ("data-icon", "data-size", "data-name", "data-step", "data-fx", "data-delay"), st)
        name = node.attrs.get("data-icon")
        if name not in ICON.ICONS:
            raise FreeError(f"body の {node.line} 行目: data-icon {name!r} は無い（名前は deck-format.md の「アイコン」の一覧）")
        if node.kids:
            raise FreeError(f"body の {node.line} 行目: f-icon の中には何も書かない")
        size = _int(node, "data-size", 24, 160) if "data-size" in node.attrs else 56
        s = SURFACES[surf]
        mo = self.motion(node)
        self.parts += 2
        if "disc" in cs:
            d = int(math.ceil(size * 1.7 / 8) * 8)
            # 丸の地は地の上で、中の絵は丸の上で、どちらも 3:1。主役の色が地に近いテーマでは、強める文字の色・文字の色の丸に落とす
            pairs = [(s["mark"], s["on-mark"]), (s["strong"], bg), (s["text"], bg), ("ink", "bg"), ("bg", "ink")]
            pair = next(((a, b) for a, b in pairs if min(ratio(self.pal[a], self.pal[bg]), ratio(self.pal[b], self.pal[a])) >= SHAPE_CONTRAST - 1e-9), None)
            if pair is None:
                raise FreeError(f"body の {node.line} 行目: 丸つきのアイコンが、地の色 --{bg} の上で見える色の組が無い（面の箱か、別の地に置く）")
            disc, glyph = pair
            return (f'<div class="f-icon-disc" style="width: {d}px; height: {d}px; background-color: {_var(disc)};"{mo}>'
                    f'{ICON.svg(name, size, _var(glyph))}</div>')
        c = self.pick(node, "アイコン", bg, (s["mark"], s["strong"], s["text"]))
        return f'<div class="f-icon-plain" style="width: {size}px; height: {size}px;"{mo}>{ICON.svg(name, size, _var(c))}</div>'

    def cells(self, node: Node, st: dict, surf: str, bg: str) -> str:
        keep = ("width", "flex", "align-self", "margin", *LENGTH_SIDES[4:])
        self.only(node, "f-cells", ("data-total", "data-on", "data-cols", "data-name", "data-step", "data-fx", "data-delay"), st, keep)
        total, cols = _int(node, "data-total", 1, 100), _int(node, "data-cols", 1, 20)
        on = _int(node, "data-on", 0, total)
        if node.kids:
            raise FreeError(f"body の {node.line} 行目: f-cells の中には何も書かない")
        s = SURFACES[surf]
        gap = 8 if cols <= 10 else 4
        css = {k: v for k, v in st.items() if k in keep}
        css.update({"grid-template-columns": f"repeat({cols}, 1fr)", "gap": f"{gap}px"})
        # 塗った側は、地の上で 3:1・塗っていない側とも 3:1 離れる色。塗っていない側は、地に近い薄い面（数は文字と data-name でも伝える）。
        # style で地の色を書いた箱の中では、面の既定の組が合わないことがあるので、塗っていない側も候補から選び直す
        near = lambda a, b: ratio(self.pal[a], self.pal[b]) < SHAPE_CONTRAST - 1e-9
        pair = next(((h, o) for o in dict.fromkeys((s["off"], *OFFS)) for h in dict.fromkeys((s["pop"], s["mark"], s["strong"], s["text"]))
                     if not near(h, bg) and not near(h, o) and near(o, bg)), None)
        if pair is None:
            raise FreeError(f"body の {node.line} 行目: f-cells の塗った側と塗っていない側を、地の色 --{bg} の上で見分けられる色の組が無い（マスを面の箱か、別の地に置く）")
        hot, off = pair
        cell = lambda c: f'<div class="f-cell" style="background-color: {_var(c)};"></div>'
        self.parts += total
        mo = self.motion(node, f"マス（{total} のうち {on}）")
        return f'<div class="f-cells" style="{_css(css)}"{mo}>' + cell(hot) * on + cell(off) * (total - on) + "</div>"

    def bars(self, node: Node, st: dict, surf: str, bg: str) -> str:
        keep = ("width", "height", "flex", "gap", "align-self", "margin", *LENGTH_SIDES[4:])
        self.only(node, "f-bars", ("data-max", "data-name", "data-step", "data-fx", "data-delay"), st, keep)
        vmax = _num(node, "data-max")
        if vmax <= 0:
            raise FreeError(f"body の {node.line} 行目: data-max は 0 より大きい数")
        bars = [k for k in node.kids if k.tag == "div" and "f-bar" in k.classes]
        if len(bars) != len(node.kids) or not 1 <= len(bars) <= 8:
            raise FreeError(f"body の {node.line} 行目: f-bars の中は f-bar の <div> を 1〜8 個だけ")
        s = SURFACES[surf]
        labels = [next((k for k in b.kids if k.tag == "b"), None) for b in bars]
        if any(labels) and not all(labels):
            raise FreeError(f"body の {node.line} 行目: 棒の上の数（<b>…</b>）は、全部の f-bar に書くか、どれにも書かない（棒の高さの尺度をそろえるため）")
        # 棒の色: ふつうの棒と強める棒（on）。どちらも地の上で 3:1
        plain = self.pick(node, "棒", bg, (s["mark"], s["strong"], s["text"]))
        hot = self.pick(node, "棒", bg, (s["pop"], s["strong"], s["text"]))
        ons = sum(b.has("on") for b in bars)
        if 0 < ons < len(bars) and _apart(self.pal[hot], self.pal[plain]) < BAR_APART:
            # 強める棒とふつうの棒が同じ色・近い色になる（差し色の無いテーマ・色の面の上）: ふつうの棒を、色みの無い色に退かせる。無ければ誤り
            plain = next((n for n in dict.fromkeys(("rule", s["muted"], "muted", "ink", "bg"))
                          if _apart(self.pal[n], self.pal[hot]) >= BAR_APART and ratio(self.pal[n], self.pal[bg]) >= SHAPE_CONTRAST - 1e-9), None)
            if plain is None:
                raise FreeError(f"body の {node.line} 行目: 強める棒（on）とふつうの棒を、地の色 --{bg} の上で見分けられる色が無い（淡い面の箱か、地の上に置く）")
        mo = self.motion(node, "棒")
        gap = f' style="gap: {st["gap"]};"' if "gap" in st else ""
        cols, names = [], []
        for b, label in zip(bars, labels):
            self.classes(b)
            if label is not None:                            # 棒の上の数は 1 行・同じ大きさ（高さが列ごとに違うと、棒を描く高さがずれて尺度が狂う）
                if label.attrs:
                    raise FreeError(f"body の {b.line} 行目: 棒の上の数の <b> に {'・'.join(sorted(label.attrs))} は書けない（<b>数</b> とだけ書く）")
                if not label.kids or any(k.tag != "#text" for k in label.kids):
                    raise FreeError(f"body の {b.line} 行目: 棒の上の数の <b> の中は文字だけ（空・<br>・<span> は書けない。数を置かないなら、全部の棒で <b> を省く）")
            self.only(b, "f-bar", ("data-value",))
            v = _num(b, "data-value")
            if v > vmax:
                raise FreeError(f"body の {b.line} 行目: data-value {v:g} が data-max {vmax:g} を越えている")
            rest = Node("div", line=b.line)
            rest.kids = [k for k in b.kids if k is not label]
            if label is not None:
                self.contrast(b, s["strong"], bg)
            if rest.kids:
                self.contrast(b, s["muted"], bg)
            head = f'<div class="f-bar-value" style="color: {_var(s["strong"])};">{self.inline(label, surf, bg)}</div>' if label else ""
            # 棒の長さは、数に比例した flex の伸び（丸めない）。上の空きが残りを取る。名前は下の別の行に置くので、
            # 名前が何行でも、棒を描く高さは全部の列で同じ＝同じ f-bars の中は同じ尺度
            cols.append(f'<div class="f-bar-col"><div style="flex: {vmax - v:g} 1 0px;"></div>{head}'
                        f'<div class="f-bar-fill" style="flex: {v:g} 1 0px; background-color: {_var(hot if b.has("on") else plain)};"></div></div>')
            names.append(f'<div class="f-bar-name" style="color: {_var(s["muted"])};">{self.inline(rest, surf, bg)}</div>' if rest.kids
                         else '<div class="f-bar-name"></div>')
            self.parts += 3
        css = {k: v for k, v in st.items() if k in keep and k != "gap"}
        foot = f'<div class="f-bar-names"{gap}>' + "".join(names) + "</div>" if any("style=" in n for n in names) else ""
        return f'<div class="f-bars" style="{_css(css)}"{mo}><div class="f-bar-plots"{gap}>' + "".join(cols) + "</div>" + foot + "</div>"

    def block(self, node: Node, surf: str, bg: str, parent: Node | None = None) -> str:
        cs = self.classes(node)
        st = style_of(node, self.warns)
        if node.tag == "span":
            if "f-icon" not in cs:
                raise FreeError(f"body の {node.line} 行目: <span> は文字の中か、アイコン（class=\"f-icon\"）にだけ使う")
            return self.icon(node, cs, st, surf, bg)
        if "f-icon" in cs:
            return self.icon(node, cs, st, surf, bg)
        if "f-cells" in cs:
            return self.cells(node, st, surf, bg)
        if "f-bars" in cs:
            return self.bars(node, st, surf, bg)
        if "f-bar" in cs:
            raise FreeError(f"body の {node.line} 行目: f-bar は f-bars の中に書く")
        if "f-line" in cs:                                   # 箱と箱をつなぐ線（横に並べた箱の間に置く。残りの幅に伸びる）
            self.only(node, "f-line", ("data-name", "data-step", "data-fx", "data-delay"), st)
            if node.kids:
                raise FreeError(f"body の {node.line} 行目: f-line の中には何も書かない")
            self.parts += 1
            c = self.pick(node, "線", bg, ("rule", SURFACES[surf]["muted"], SURFACES[surf]["text"]))
            return f'<div class="f-line" style="background-color: {_var(c)};"{self.motion(node)}></div>'
        self.only(node, "<div>", ("data-name", "data-step", "data-fx", "data-delay"), st, tuple(st))
        new = self.surface_of(node, st, cs, surf)
        chip = "f-chip" in cs or "f-no" in cs
        if chip:                                             # 札・番号の丸は、自分の面（差し色・主役の色）を持つ
            new = "accent" if "f-chip" in cs else "fill"
        new_bg = st.get("background-color") or (SURFACES[new]["bg"] if (new != surf or chip or "f-box" in cs) else bg)
        css = dict(st)
        if "f-box" in cs or chip or "background-color" in st:
            css["background-color"] = _var(new_bg)
        css.pop("color", None)
        small = any(c in SMALL_OK for c in cs)
        self.small(node, st, small)
        has_text = any(k.tag == "#text" or k.tag in INLINES and "f-icon" not in k.classes or k.tag == "br" for k in node.kids)
        has_block = any(k.tag == "div" or (k.tag == "span" and "f-icon" in k.classes) for k in node.kids)
        if has_text and has_block:
            raise FreeError(f"body の {node.line} 行目: 文字と、箱・アイコンを同じ <div> に並べられない（文字を <div> で包む）")
        if "top" in cs:                                      # 箱の上の縁にかける札: 箱の中身の行を押し下げない（並ぶ箱の行がそろう）
            if "f-chip" not in cs or parent is None or "f-box" not in parent.classes or parent.kids[0] is not node:
                raise FreeError(f"body の {node.line} 行目: top は、f-box の最初の子の f-chip にだけ付ける（class=\"f-chip top\"）")
        mo = self.motion(node)                               # 子を組む前に読む（段・名前の入れ子を、子の側で止めるため）
        if has_text:
            css["color"] = _var(self.text_color(node, st, cs, new, new_bg))
            inner = self.inline(node, new, new_bg, small)
            self.parts += 1
        else:
            stepped, named = "data-step" in node.attrs, "data-name" in node.attrs
            self.in_step += stepped
            self.in_name += named
            try:
                inner = "".join(self.block(k, new, new_bg, node) for k in node.kids)
            finally:
                self.in_step -= stepped
                self.in_name -= named
            if "f-box" in cs or "background-color" in st:
                self.parts += 1
        cls = " ".join(c for c in cs if c not in MODIFIERS or c in BOX_KINDS) + (" f-tag" if "top" in cs else "")
        attr = (f' class="{cls}"' if cls else "") + (f' style="{_css(css)}"' if css else "") + mo
        return f"<div{attr}>{inner}</div>"


def render(body: str, pal: dict, anim) -> tuple[str, dict]:
    """body → (HTML の断片, {"warnings", "parts"})。誤りは FreeError。"""
    if not isinstance(body, str):
        raise FreeError("body は文字（中身の箱に置く HTML）で書く")
    root = parse(body)
    r = _Render(pal, anim)
    if any(k.tag != "div" and not (k.tag == "span" and "f-icon" in k.classes) for k in root.kids):
        raise FreeError("body の一番外は <div> を並べる（文字や <b> を直に置かない）")
    html = "".join(r.block(k, "page", "bg") for k in root.kids)
    return html, {"warnings": r.warns, "parts": r.parts}


def visible_text(body: str) -> list[str]:
    """body の見える文字と、部品の数（マス・棒）を、上から順の文の列で返す（絵で描くエンジンに中身として渡す・直しの番号振りの元）。"""
    out: list[str] = []

    def walk(n: Node):
        if n.tag == "#text":
            out.append(" ".join(n.text.split()))
            return
        if n.has("f-cells"):
            out.append(f"{n.attrs.get('data-total')} のうち {n.attrs.get('data-on')}")
            return
        if n.has("f-bars"):                                  # 棒は「名前: 値（最大 尺度）」。見える数（<b>）を省いた棒でも、長さを決める数が残る
            for b in n.kids:
                texts: list[str] = []
                grab(b, texts)
                out.append(f"{' '.join(texts)}: {b.attrs.get('data-value')}（最大 {n.attrs.get('data-max')}）")
            return
        for k in n.kids:
            walk(k)

    def grab(n: Node, into: list[str]):
        if n.tag == "#text":
            into.append(" ".join(n.text.split()))
        for k in n.kids:
            grab(k, into)

    walk(parse(body))
    return [t for t in out if t]
