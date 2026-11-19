"""自由なページ（型 `free`）の `body` を、サーバの側で読む（html-slide の ADR 0030・docs/adr/0033）。

`body` は html-slide が解析して組む HTML。決まりに合うかはエンジンの検査が見る。ここでは組みを検査せず、
画面に出す文字（`texts`）と、直しの前後で組みが変わったかを見比べる骨組み（`skeleton`）だけを取り出す。
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

TYPE = "free"
# 中身の数を表す属性（割合のマス・棒）。数字の直しでは文字と一緒に変わるので、骨組みの見比べからは外す。
# マスの列の数（data-cols）は並べ方なので、骨組みに入れる
VALUE_ATTRS = ("data-total", "data-on", "data-max", "data-value")


def _style(text: str) -> tuple[tuple[str, str], ...]:
    """style を（プロパティ, 値）の並びにそろえる（空白・大文字小文字・宣言の順の違いは、組みの違いにしない）。"""
    out = []
    for decl in text.split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            out.append((k.strip().lower(), " ".join(v.split()).lower()))
        elif decl.strip():
            out.append((decl.strip().lower(), ""))
    return tuple(sorted(out))


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.texts: list[str] = []
        self.skeleton: list[tuple[Any, ...]] = []
        self.vmax = ""
        self.bar: tuple[str, list[str]] | None = None
        self.depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        self.skeleton.append((tag, tuple(sorted(set(a.get("class", "").split()))), _style(a.get("style", "")),
                              tuple(sorted((k, v) for k, v in a.items() if k.startswith("data-") and k not in VALUE_ATTRS))))
        cls = a.get("class", "").split()
        if "f-cells" in cls:
            self.texts.append(f"{a.get('data-total', '')} のうち {a.get('data-on', '')}")
        if "f-bars" in cls:
            self.vmax = a.get("data-max", "")
        if "f-bar" in cls:
            self.bar, self.depth = (a.get("data-value", ""), []), 0
        elif self.bar is not None and tag != "br":
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        self.skeleton.append(("/" + tag,))
        if self.bar is None:
            return
        if self.depth:
            self.depth -= 1
            return
        value, words = self.bar
        if words or value:
            self.texts.append(f"{' '.join(words)}: {value}（最大 {self.vmax}）" if value else " ".join(words))
        self.bar = None

    def handle_data(self, data: str) -> None:
        t = " ".join(data.split())
        if t:
            (self.bar[1] if self.bar is not None else self.texts).append(t)


def _read(body: Any) -> _Reader:
    r = _Reader()
    if isinstance(body, str):
        r.feed(body)
        r.close()
    return r


def texts(body: Any) -> list[str]:
    """見える文字と、マス・棒の数を、上から順に（タグ・class・style は出さない）。"""
    return _read(body).texts


def skeleton(body: Any) -> list[tuple[Any, ...]]:
    """組みの骨組み（要素・class・style・動きの属性の並び）。文字と、数を表す属性は入れない。"""
    return _read(body).skeleton


def reshaped(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """自由なページの組みが変わったか（文字だけの直しでは、変わってはいけない）。別の型に替えたのも、組みを変えたことになる。"""
    return before.get("type") == TYPE and (after.get("type") != TYPE or skeleton(before.get("body")) != skeleton(after.get("body")))
