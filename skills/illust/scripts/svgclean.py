"""挿絵の SVG を確かめて、描いてよい形だけに絞る（`illust.py` の中身。ほかからも import できる）。

- `<icon name="users" x="…" y="…" size="…" fill="…"/>` を、Material Symbols の形（`skills/dads/icons`）の `<path>` に展開する
- 残すのは形と文字の要素だけ（下の `ELEMENTS`）。スクリプト・外への参照・埋め込みの文書・画像・アニメーションは落とす
- 属性も許したものだけ。`on…` は落とす。`url(…)` は文書の中の `#id` を指すものだけ
- 直さないと描けないもの（読めない・viewBox が無い・知らないアイコン・大きすぎる）は `NG:` の行で返す（書いた側に直させる）
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
MAX_CHARS = 300_000
# 共有のアイコン（DADS の置き場。html-slide も同じものを読む）。このファイルの位置から辿る（symlink 越しでも動くように）
ICONS_PATH = Path(__file__).resolve().parents[2] / "dads" / "icons" / "material-symbols.json"

ELEMENTS = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan",
            "defs", "linearGradient", "radialGradient", "stop", "clipPath", "title", "desc"}
DROPPED = {"script", "foreignObject", "image", "use", "a", "style", "iframe", "animate", "animateMotion",
           "animateTransform", "set", "filter", "mask", "pattern", "symbol", "marker", "switch", "view"}
ATTRS = {
    "id", "viewBox", "preserveAspectRatio", "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
    "fx", "fy", "d", "points", "transform", "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-linecap",
    "stroke-linejoin", "stroke-dasharray", "stroke-dashoffset", "stroke-opacity", "stroke-miterlimit", "opacity", "clip-rule",
    "clip-path", "font-family", "font-size", "font-weight", "font-style", "text-anchor", "dominant-baseline",
    "letter-spacing", "offset", "stop-color", "stop-opacity", "gradientUnits", "gradientTransform", "spreadMethod",
    "clipPathUnits", "style", "dx", "dy", "rotate", "textLength", "lengthAdjust", "vector-effect",
}
_URL = re.compile(r"url\(\s*([^)]*)\)", re.I)
_NUM = r"-?(?:\d+\.?\d*|\.\d+)(?:e-?\d+)?"


@dataclass
class Cleaned:
    svg: str                     # 絞ったあとの SVG（`width`・`height` は viewBox の大きさ）
    width: float                 # viewBox の幅・高さ
    height: float
    problems: list[str] = field(default_factory=list)   # NG:（直さないと描けない）
    warnings: list[str] = field(default_factory=list)   # 落としたもの（描けるが、書いた側に知らせる）


_icons: dict[str, dict] | None = None


def icon_names() -> list[str]:
    return sorted(_load_icons())


def _load_icons() -> dict[str, dict]:
    global _icons
    if _icons is None:
        _icons = json.loads(ICONS_PATH.read_text(encoding="utf-8"))
    return _icons


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _number(value: str | None, what: str, problems: list[str]) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        problems.append(f"NG: <icon> の {what} は数（{value!r}）")
        return 0.0


def _expand_icon(el: ET.Element, problems: list[str]) -> ET.Element:
    """`<icon name x y size fill/>` → Material Symbols の形の `<path>`（viewBox 0 -960 960 960 を size の四角へ）。"""
    name = el.get("name") or ""
    icon = _load_icons().get(name)
    if icon is None:
        problems.append(f"NG: <icon name=\"{name}\"> は無いアイコン（使える名前は手引きの一覧）")
        return ET.Element(f"{{{SVG_NS}}}g")
    x, y = _number(el.get("x", "0"), "x", problems), _number(el.get("y", "0"), "y", problems)
    size = _number(el.get("size", "96"), "size", problems)
    k = size / 960
    path = ET.Element(f"{{{SVG_NS}}}path", {"d": icon["path"], "transform": f"translate({x:g},{y + size:g}) scale({k:g})",
                                             "fill": el.get("fill") or "currentColor"})
    for a in ("opacity", "fill-opacity"):
        if el.get(a):
            path.set(a, el.get(a))  # type: ignore[arg-type]
    return path


def _safe_value(name: str, value: str) -> bool:
    low = value.lower()
    if "javascript:" in low or "expression(" in low or "@import" in low or "\\" in value:
        return False
    return all(m.group(1).strip().strip("'\"").startswith("#") for m in _URL.finditer(value))


def _clean(el: ET.Element, problems: list[str], warnings: list[str]) -> None:
    for child in list(el):
        name = _local(child.tag)
        if name == "icon":
            idx = list(el).index(child)
            el.remove(child)
            el.insert(idx, _expand_icon(child, problems))
            continue
        if name not in ELEMENTS:
            el.remove(child)
            if name not in ("", None):
                warnings.append(f"<{name}> は描けないので落とした" if name not in DROPPED else f"<{name}> は使えないので落とした")
            continue
        _clean(child, problems, warnings)
    for attr in list(el.attrib):
        local = _local(attr)
        value = el.attrib[attr]
        if attr.startswith("{") and not attr.startswith(f"{{{SVG_NS}}}"):
            del el.attrib[attr]           # xlink:href など、外の名前空間の属性
            continue
        if local not in ATTRS or local.lower().startswith("on") or not _safe_value(local, value):
            del el.attrib[attr]
            if local.lower().startswith("on") or local in ("href",):
                warnings.append(f"{local} 属性は使えないので落とした")


def clean(text: str) -> Cleaned:
    """SVG の文字列を確かめて絞る。"""
    problems: list[str] = []
    warnings: list[str] = []
    if not isinstance(text, str) or not text.strip():
        return Cleaned("", 0, 0, ["NG: 挿絵の SVG が空"])
    if len(text) > MAX_CHARS:
        return Cleaned("", 0, 0, [f"NG: 挿絵の SVG が長すぎる（{len(text)} 字。{MAX_CHARS} 字まで。形を減らす）"])
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I):
        return Cleaned("", 0, 0, ["NG: 挿絵の SVG に DOCTYPE・ENTITY を書かない"])
    if "xmlns=" not in text.split(">", 1)[0]:
        text = text.replace("<svg", f'<svg xmlns="{SVG_NS}"', 1)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return Cleaned("", 0, 0, [f"NG: 挿絵の SVG を読めない（{exc}）。閉じていないタグ・属性の引用符を確かめる"])
    if _local(root.tag) != "svg":
        return Cleaned("", 0, 0, ["NG: 挿絵は <svg> で始める"])
    m = re.fullmatch(rf"\s*({_NUM})[\s,]+({_NUM})[\s,]+({_NUM})[\s,]+({_NUM})\s*", root.get("viewBox") or "")
    if not m:
        return Cleaned("", 0, 0, ["NG: 挿絵の <svg> に viewBox=\"0 0 幅 高さ\" を書く"])
    w, h = float(m.group(3)), float(m.group(4))
    if w <= 0 or h <= 0 or max(w / h, h / w) > 4:
        return Cleaned("", 0, 0, ["NG: 挿絵の viewBox の幅・高さは正の数で、縦横の比は 4 倍まで"])
    _clean(root, problems, warnings)
    if not len(root):
        problems.append("NG: 挿絵に描くものが無い")
    for a in ("width", "height"):
        root.attrib.pop(a, None)
    root.set("width", f"{w:g}")
    root.set("height", f"{h:g}")
    ET.register_namespace("", SVG_NS)
    out = ET.tostring(root, encoding="unicode")
    return Cleaned(out, w, h, problems, sorted(set(warnings)))


def sized(svg: str, width: int, height: int) -> str:
    """絞った SVG の `width`・`height` を描く大きさに替える（中身は viewBox のまま伸び縮みする）。"""
    root = ET.fromstring(svg)
    root.set("width", str(width))
    root.set("height", str(height))
    ET.register_namespace("", SVG_NS)
    return ET.tostring(root, encoding="unicode")
