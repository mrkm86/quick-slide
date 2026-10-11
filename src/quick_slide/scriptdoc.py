"""台本（エンジンに渡す JSON）を読む・書くときの決まり。サーバ・台本係・描き係が同じものを使う。

- **ページの同一性は各ページの `id`**（両エンジンが受ける共通のキーで、描くことには使わない）
- **非表示は台本のページに `_hidden: true`** と書く（`_` で始まるキーはエンジンが読み飛ばすが、念のため
  エンジンに渡す台本からは非表示のページごと抜く。`engine_view`）。非表示も台本の版に入るので、戻せる
- **台本の版のファイルは `dumps` の 1 つの書き方で書く**（キーの順は読んだまま・字下げ 1）。指定外のページは
  同じオブジェクトをそのまま書くので、版をまたいでも 1 文字も変わらない
- ページの中身の sha256（`content_sha`）は非表示の印を除いて取る（版スタックで「同じ姿」をまとめる鍵）
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

from . import motion

PAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
HIDDEN_KEY = "_hidden"
INFOGRAPHIC_KEY = "infographic"

# 台本のページから「見出し」と「本文」を取り出すときに読み飛ばすキー（話す台本・動き・描き方の注文・画像の枠）
_NOT_CONTENT = {"type", "talk", "transition", "steps", "fx", "id", "role", "image", "_visual", "_characters", "_layout",
                "layout", "split", "side", "text_side", "tone", "fit", "widths", "focus", "accent", "highlight",
                "current", "arrow", "value_labels", "min", "max", "x", "y", "trend", "initials", "path", "prompt", "alt",
                HIDDEN_KEY}

# 構成（会話で合意したページ割り）の見出し: `## 3. 会議の4割は何も決めずに終わっている`
OUTLINE_HEADING_RE = re.compile(r"^##\s*(\d+)\s*[.．、。)]\s*(\S.*)$", re.M)


def dumps(data: dict[str, Any]) -> bytes:
    """台本の版のファイルの中身（1 つの書き方に決める。指定外のページの文字を変えないため）。"""
    return (json.dumps(data, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def is_hidden(page: dict[str, Any]) -> bool:
    return page.get(HIDDEN_KEY) is True


def content_sha(page: Any) -> str:
    """ページ（か、ページの並び）の中身の sha256。非表示の印は入れない（隠しても同じ姿）。"""
    def strip(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: strip(x) for k, x in v.items() if k != HIDDEN_KEY}
        if isinstance(v, list):
            return [strip(x) for x in v]
        return v

    raw = json.dumps(strip(page), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def slides_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in data.get("slides") or [] if isinstance(s, dict)]


def engine_view(data: dict[str, Any]) -> dict[str, Any]:
    """エンジンに渡す台本: 非表示のページを抜く（印も消す）。ほかは同じ。"""
    visible = []
    for s in slides_of(data):
        if is_hidden(s):
            continue
        visible.append({k: v for k, v in s.items() if k != HIDDEN_KEY})
    return {**data, "slides": visible}


def headline_of(spec: dict[str, Any]) -> str:
    """そのページの見出し（エンジンの `label_of`・`headline_of` と同じ選び方）。"""
    for k in ("headline", "title", "quote"):
        v = spec.get(k)
        if isinstance(v, str) and v.strip():
            return v.replace("**", "").replace("\n", " ").strip()
    if spec.get("value"):
        return f"{spec['value']}{spec.get('unit') or ''}"
    return str(spec.get("type") or "")


def body_of(spec: dict[str, Any]) -> str:
    """画面の「このページの記録」に出す、そのページの文字（話す台本・画像の指示は入れない）。"""
    lines: list[str] = []

    def walk(value: Any, key: str | None = None) -> None:
        if key is not None and key in _NOT_CONTENT:
            return
        if isinstance(value, str):
            text = value.replace("**", "").strip()
            if text:
                lines.append(text)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            lines.append(str(value))
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)

    for k, v in spec.items():
        walk(v, k)
    return "\n".join(lines)[:4000]


def one_page(args: dict[str, Any]) -> bool:
    """台本の全ページを 1 枚に描く（ピッチの infographic）。ページの操作は効かない。"""
    return args.get("mode") == "pitch" and args.get("style") == "infographic"


def screen_pages(args: dict[str, Any], data: dict[str, Any]) -> list[dict[str, Any]]:
    """画面に出すページ（台本の版に結ぶ。非表示も入る）。infographic は全ページで 1 枚。"""
    slides = slides_of(data)
    if one_page(args):
        title = str(data.get("title") or (headline_of(slides[0]) if slides else ""))
        visible = [s for s in slides if not is_hidden(s)]
        return [{"no": 1, "key": INFOGRAPHIC_KEY, "type": "infographic", "title": title.replace("**", ""),
                 "body": "\n\n".join(headline_of(s) for s in visible), "hidden": False,
                 "content_sha": content_sha(visible)}]
    scene = motion.is_motion(args)   # モーションは場面（型は持たない。文字は場面の HTML の見える文字）
    return [{"no": i, "key": str(s["id"]), "type": "scene" if scene else str(s.get("type") or ""),
             "title": headline_of(s), "body": motion.body_of(s) if scene else body_of(s),
             "hidden": is_hidden(s), "content_sha": content_sha(s)} for i, s in enumerate(slides, 1)]


def output_pages(args: dict[str, Any], data: dict[str, Any]) -> list[dict[str, Any]]:
    """描いた回のページ（エンジンが出す `slides/slide-NN.png` の数だけ）。非表示のページは描かない。"""
    pages = screen_pages(args, data)
    if one_page(args):
        return pages
    out = []
    for p in pages:
        if p["hidden"]:
            continue
        out.append({**p, "no": len(out) + 1})
    return out


def page_id_problems(slides: list[dict[str, Any]]) -> list[str]:
    """台本の各ページの `id`（ページの同一性）。無い・形が違う・重なる → `NG:` の行（台本係に直させる）。"""
    problems: list[str] = []
    seen: dict[str, int] = {}
    for no, spec in enumerate(slides, 1):
        pid = spec.get("id")
        if not isinstance(pid, str) or not PAGE_ID_RE.match(pid):
            problems.append(f"NG: {no}枚目: id が無いか形が違う（英小文字・数字・ハイフンで 40 字以内。例 \"cover\"・\"p-why\"）")
        elif pid in seen:
            problems.append(f"NG: {no}枚目: id \"{pid}\" が {seen[pid]}枚目と重なっている（資料の中で重ならない id にする）")
        else:
            seen[pid] = no
    return problems


def outline_headings(manuscript: str) -> list[tuple[int, str]]:
    """合意した構成の見出し（`## N. 見出し`）。ページの数と順番の正本。"""
    return [(int(m.group(1)), m.group(2).strip()) for m in OUTLINE_HEADING_RE.finditer(manuscript or "")]


def fresh_id(base: str, taken: set[str]) -> str:
    """`base` から、台本の中で重ならない id を作る（複製・分割のとき）。"""
    stem = re.sub(r"[^a-z0-9-]", "-", base.lower()).strip("-")[:30] or "p"
    if not PAGE_ID_RE.match(stem):
        stem = "p-" + stem
    for n in range(1, 1000):
        cand = f"{stem}-copy" if n == 1 else f"{stem}-copy{n}"
        cand = cand[:40]
        if cand not in taken and PAGE_ID_RE.match(cand):
            return cand
    raise ValueError("id を作れない")


def deep(page: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(page)


def with_hidden(data: dict[str, Any], key: str, hidden: bool) -> dict[str, Any]:
    """ページ `key` の非表示を替えた台本（元は替えない）。描かない直し（画面の口）とワーカーの両方が使う。"""
    slides = []
    for s in data.get("slides") or []:
        if isinstance(s, dict) and s.get("id") == key:
            s = dict(s)
            if hidden:
                s[HIDDEN_KEY] = True
            else:
                s.pop(HIDDEN_KEY, None)
        slides.append(s)
    return {**data, "slides": slides}


def with_order(data: dict[str, Any], wanted: list[str]) -> dict[str, Any]:
    """ページを `wanted`（台本の id）の順に並べた台本。並びに無いページ（頼んだあとに足されたページ）は、
    前にいたページのすぐ後ろへ置く。"""
    slides = slides_of(data)
    by_key = {str(s.get("id")): s for s in slides}
    order = [k for k in dict.fromkeys(wanted) if k in by_key]
    for i, s in enumerate(slides):
        k = str(s.get("id"))
        if k in order:
            continue
        prev = next((str(slides[j].get("id")) for j in range(i - 1, -1, -1) if str(slides[j].get("id")) in order), None)
        order.insert(order.index(prev) + 1 if prev else 0, k)
    return {**data, "slides": [by_key[k] for k in order]}
