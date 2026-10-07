"""台本のページの型（html-slide の `references/deck-format.md` の「型の一覧」）。スタイルの「使うパターン」の目録。

**型の一覧はエンジンの文書から実行時に読む**（写しを持たない。エンジンが型を足せば、ここにも出る）。
画面に出す短い名前（`LABELS`）だけはここに置く。知らない型は型の名前のまま出す。

スタイルの `allowed_layouts`:
- `["auto"]` … おまかせ（すべての型を使ってよい。台本係が選ぶ）
- 型の名前の並び … 台本係はその型だけを使う（サーバも `NG:` で確かめる）
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from .paths import SKILLS_DIR

AUTO = "auto"
DOC = "html-slide/references/deck-format.md"
LABELS = {
    "cover": "表紙", "agenda": "目次", "section": "章扉", "statement": "主張を大きく", "summary": "結論と根拠",
    "big-number": "大きな数字", "kpi": "指標を並べる", "cards": "カード", "flow": "矢羽根（順序）",
    "compare": "比較", "bar-chart": "棒グラフ", "line-chart": "折れ線グラフ", "table": "表",
    "bullets": "箇条書き", "timeline": "年表", "photo": "写真", "quote": "引用", "gallery": "ギャラリー",
    "figure": "図・画面の写し", "annotate": "注釈つきの画像", "team": "体制・人", "matrix": "2×2",
    "closing": "締め",
}
# 画像が要る型（画像を使わないデッキでは使えない）
NEEDS_IMAGE = ("photo", "gallery", "figure", "annotate")

_ROW = re.compile(r"^\|\s*`([a-z][a-z0-9-]*)`\s*\|\s*([^|]+?)\s*\|")


@lru_cache(maxsize=4)
def _parse(path: str, mtime_ns: int) -> tuple[tuple[str, str], ...]:
    text = Path(path).read_text(encoding="utf-8")
    start = text.find("## 型の一覧")
    if start < 0:
        return ()
    section = text[start:]
    end = section.find("\n## ", 5)
    rows = []
    for line in (section[:end] if end > 0 else section).splitlines():
        m = _ROW.match(line)
        if m:
            rows.append((m.group(1), m.group(2).strip()))
    return tuple(rows)


def engine_types(skills_dir: Path = SKILLS_DIR) -> list[dict[str, str]]:
    """[{value, label, use}]（文書の並び）。文書が読めなければ空（呼ぶ側が「おまかせ」だけにする）。"""
    path = skills_dir / DOC
    try:
        rows = _parse(str(path), path.stat().st_mtime_ns)
    except OSError:
        return []
    return [{"value": key, "label": LABELS.get(key, key), "use": _first_clause(use)} for key, use in rows]


def type_names(skills_dir: Path = SKILLS_DIR) -> list[str]:
    return [t["value"] for t in engine_types(skills_dir)]


def _first_clause(text: str, limit: int = 40) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", text)
    head = text.split("。")[0]
    return head if len(head) <= limit else head[:limit] + "…"


def allowed(layouts: list[str] | None) -> list[str] | None:
    """使ってよい型の並び。おまかせ（または読めない）なら None。"""
    if not layouts or AUTO in layouts:
        return None
    return list(layouts)


def problems(layouts: list[str] | None, skills_dir: Path = SKILLS_DIR) -> list[str]:
    """スタイルの `allowed_layouts` の誤り（保存のときに見る）。"""
    if not isinstance(layouts, list) or not layouts:
        return ["使うパターンを 1 つ以上選んでください"]
    if layouts == [AUTO]:
        return []
    if AUTO in layouts:
        return ["「おまかせ」とほかのパターンは一緒に選べません"]
    known = set(type_names(skills_dir))
    unknown = [x for x in layouts if x not in known]
    if unknown:
        return ["製品目録に無い型があります: " + ", ".join(unknown)]
    return []
