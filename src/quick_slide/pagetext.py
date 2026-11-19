"""ページの文字を、人が直接書き換える（docs/adr/0036-direct-text-edits.md）。台本係は通らない。

ページの文字を「欄」の並びにして画面に渡し（`fields`）、書き換えた欄だけを台本に当てる（`apply`）。
位置・色・書式・組み・動きは変えない。欄を足すこと・消すこと・空にすることはできない。

欄の指し方（`ref`）は 3 つ:

- `n:<番号>`    取り込んだページ（PPTX から）の HTML の文字（`importing.text_segments` の番号）
- `b:<番号>`    自由なページ（型 free）の `body` の HTML の文字（同じ数え方）
- `k:<道筋>`    台本のキーの文字（`headline`、`items/0/title` のように `/` でつなぐ）

当てるときは、画面が見ていた元の文字（`from`）がいまの台本と同じことを確かめる（ほかの直しが先に当たって欄がずれたときに、
違う文字を書き換えない）。
"""

from __future__ import annotations

from typing import Any

from . import freepage, importing
from .scriptdoc import IMPORTED_TYPE, deep

MAX_TEXT_CHARS = 2000
# 欄にするのは、**見える文字のキーだけ**（名前で許可する）。描き方の指定（図柄 motif・札の描き方 label_style・進み具合 state・
# 並べ方 layout など）は文字列でも欄にしない。知らないキーは欄にしない（その文字は「AI に頼む」で直す）
_TEXT_KEYS = {"headline", "title", "subtitle", "kicker", "lead", "caption", "takeaway", "label", "body", "source", "footnote",
              "quote", "name", "value", "unit", "text", "note", "heading", "description", "meta", "by", "position", "role",
              "desc", "detail", "summary", "contact", "callout", "result", "center", "number", "bad_label", "good_label",
              "before", "after", "bad", "good", "term", "definition", "date", "when", "period", "tag", "question", "answer",
              # 文字の並び（文字列がそのまま並ぶ配列）
              "items", "points", "rows", "columns", "labels", "legend"}
# この下は読まない（話す台本・動き・画像の枠・取り込んだページの HTML など。中に文字の名前のキーがあっても欄にしない）
_SKIP_KEYS = {"talk", "transition", "steps", "fx", "image", "html", "notes", "svg", "illustrations"}
# グラフの棒・線の中は、描いてある名前だけを欄にする（値は数で、文字の名前のキーでも欄にしない）
_CHART_KEYS = {"bars": {"label", "display"}, "series": {"name"}}
# 欄の見出し（キーの名前 → 画面の言葉）。無いものは「文字」
_LABELS = {"headline": "見出し", "title": "見出し", "subtitle": "副題", "kicker": "小見出し", "lead": "リード",
           "caption": "注記", "takeaway": "まとめ", "label": "ラベル", "body": "本文", "source": "出典",
           "footnote": "脚注", "quote": "引用", "name": "名前", "value": "数字", "unit": "単位", "text": "本文",
           "note": "補足", "heading": "見出し", "description": "説明", "items": "項目", "points": "項目",
           "rows": "表", "columns": "表の見出し", "labels": "ラベル", "by": "発言者", "contact": "連絡先",
           "display": "値の表示"}

NOT_EDITABLE = {
    "pitch": "ピッチは文字も絵として描くので、ここでは書き換えられません。「AI に頼む」から直してください。",
    "motion": "モーションは、ここでは書き換えられません。「AI に頼む」から直してください。",
    "infographic": "全ページを 1 枚に描くスタイルは、ここでは書き換えられません。「AI に頼む」から直してください。",
}
NO_FIELDS = "このページには、書き換えられる文字がありません。"


class TextEditRefused(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _skip(key: str) -> bool:
    return key.startswith("_") or key in _SKIP_KEYS


def _html_fields(html: Any, prefix: str, label: str) -> list[dict[str, Any]]:
    out = []
    for seg in importing.text_segments(html if isinstance(html, str) else ""):
        if seg["bullet"] or not seg["text"]:
            continue
        out.append({"ref": f"{prefix}:{seg['n']}", "label": label, "text": seg["text"]})
    return out


def _key_fields(value: Any, path: list[str], out: list[dict[str, Any]]) -> None:
    if isinstance(value, str):
        name = _named(path)
        allowed = _CHART_KEYS.get(path[0], _TEXT_KEYS)
        if value.strip() and name in allowed:
            label = "項目" if len(path) > 1 and name in ("title", "heading", "name", "label") else _LABELS.get(name, "文字")
            out.append({"ref": "k:" + "/".join(path), "label": label, "text": value})
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _key_fields(v, [*path, str(i)], out)
    elif isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and not _skip(k):
                _key_fields(v, [*path, k], out)


def _named(path: list[str]) -> str:
    """道筋の最後の、番号でない名前（`items/0` → `items`）。"""
    for p in reversed(path):
        if not p.isdigit():
            return p
    return ""


def fields(page: dict[str, Any]) -> list[dict[str, Any]]:
    """そのページの書き換えられる文字（{ref, label, text}。上から順）。同じ見出しの欄には番号を振る。"""
    out: list[dict[str, Any]] = []
    if page.get("type") == IMPORTED_TYPE:
        out = _html_fields(page.get("html"), "n", "文字")
    else:
        for k, v in page.items():
            # ページ直下の role は、見えない印（前の台本との互換のためにエンジンが読み飛ばす）。項目の中の role（役職）は見える
            if not isinstance(k, str) or _skip(k) or k == "role":
                continue
            if page.get("type") == freepage.TYPE and k == "body" and isinstance(v, str):
                out += _html_fields(v, "b", "本文")
            else:
                _key_fields(v, [k], out)
    counts: dict[str, int] = {}
    for f in out:
        counts[f["label"]] = counts.get(f["label"], 0) + 1
    seen: dict[str, int] = {}
    for f in out:
        if counts[f["label"]] > 1:
            seen[f["label"]] = seen.get(f["label"], 0) + 1
            f["label"] = f"{f['label']} {seen[f['label']]}"
    return out


def check_changes(raw: Any) -> list[dict[str, str]]:
    """口が受けた書き換え（[{ref, from, text}]）の形を確かめる。"""
    if not isinstance(raw, list) or not raw:
        raise TextEditRefused("書き換える文字がありません。")
    out, seen = [], set()
    for c in raw:
        ref, old, new = (c.get("ref"), c.get("from"), c.get("text")) if isinstance(c, dict) else (None, None, None)
        if not isinstance(ref, str) or not isinstance(old, str) or not isinstance(new, str) or ref[:2] not in ("n:", "b:", "k:"):
            raise TextEditRefused("書き換えの形が違います。画面を読み込み直してから、もう一度直してください。")
        if ref in seen:
            raise TextEditRefused("同じ欄を 2 回書き換えることはできません。")
        seen.add(ref)
        new = new.strip()
        if not new:
            raise TextEditRefused("文字を空にすることはできません（消すときは「AI に頼む」から頼んでください）。")
        if len(new) > MAX_TEXT_CHARS:
            raise TextEditRefused(f"1 つの欄に書けるのは {MAX_TEXT_CHARS} 字までです。")
        if ref[0] in "nb" and ("\n" in new or "\r" in new):
            new = " ".join(new.split())
        if new != old:
            out.append({"ref": ref, "from": old, "text": new})
    if not out:
        raise TextEditRefused("文字が変わっていません。")
    return out


def apply(page: dict[str, Any], changes: list[dict[str, str]]) -> dict[str, Any]:
    """書き換えを当てたページ（元のページは変えない）。欄が無い・元の文字が違うときは断る。"""
    now = {f["ref"]: f["text"] for f in fields(page)}
    for c in changes:
        if c["ref"] not in now or now[c["ref"]] != c["from"]:
            raise TextEditRefused("ページの文字が、開いたときから変わっています。画面を読み込み直してから、もう一度直してください。")
    out = deep(page)
    html_changes: dict[str, dict[int, str]] = {"n": {}, "b": {}}
    for c in changes:
        kind, rest = c["ref"][0], c["ref"][2:]
        if kind in html_changes:
            html_changes[kind][int(rest)] = c["text"]
            continue
        node: Any = out
        parts = rest.split("/")
        for p in parts[:-1]:
            node = node[int(p)] if isinstance(node, list) else node[p]
        last = parts[-1]
        if isinstance(node, list):
            node[int(last)] = c["text"]
        else:
            node[last] = c["text"]
    if html_changes["n"]:
        # 一覧に出す見出し（取り込みの title）は、title と同じ文字の節が**ちょうど 1 つ**で、それを書き換えたときだけ直す。
        # 同じ文字の節がいくつもある（マスターの文字・本文の同じ文）・1 つも無い（つないだ・切り詰めた title）ときは、
        # どれが見出しか決められないので title は変えない（違う文字を変えるより、一覧が古いままのほうを選ぶ）
        old_title = (out.get("title") or "").strip()
        heads = [s["n"] for s in importing.text_segments(out.get("html") or "") if old_title and s["text"] == old_title]
        if len(heads) == 1 and heads[0] in html_changes["n"]:
            out["title"] = html_changes["n"][heads[0]]
        out["html"] = importing.replace_texts(out.get("html") or "", html_changes["n"])
    if html_changes["b"]:
        out["body"] = importing.replace_texts(out.get("body") or "", html_changes["b"])
    return out
