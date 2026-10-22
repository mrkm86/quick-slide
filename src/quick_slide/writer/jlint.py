"""台本の日本語の検査（同梱の yomiyasu）。台本係が書いた台本から、スライドに出る文字と話す台本（`talk`）を取り出して調べる。

yomiyasu の決まりのうち、使うのは次の 3 つだけ（許可リスト方式。上流で決まりが増えても勝手に効かない）。

- NG（台本係に書き直させる）: `emoji_prohibited`（スライドの文字にも話す台本にも）・`meta_filler`（前置きの紋切り型）
- 注意（記録するだけ・書き直させない）: `sentence_end_repetition`（話す台本だけ。同じ文末が 3 回続く）

ほかの決まり（`slop_vocabulary` は業務の言葉「台帳」などを誤検知した。太字・箇条書きの多さはスライドの書式では当然）は使わない。

- 調べるのは表示される文字のキーだけ（`VISIBLE_KEYS`）。`id`・`type`・`icon`・`image`・`prompt`・`kind` などの値は調べない
- スライドの文字は 1 つずつ別の段落にして 1 回で、話す台本はページごとに 1 行 1 つで調べる。行番号からページと位置へ戻す
- 読み込みや実行で例外が出ても台本係を止めない（記録して素通りする）
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

log = logging.getLogger("quick_slide.jlint")

NG_RULES = frozenset({"emoji_prohibited", "meta_filler"})
NOTE_RULES = frozenset({"sentence_end_repetition"})   # 話す台本だけ

# 表示される文字のキー（`skills/html-slide/scripts/deck_types.py` の `TYPES` と deck-format.md の型ごとのキー）。
# 文字列の値、またはその配列・入れ子（items の中身・表の行・series の name など）が対象
VISIBLE_KEYS = frozenset({
    "headline", "kicker", "sub", "subtitle", "title", "body", "text", "term", "label", "note", "value", "unit",
    "caption", "quote", "badge", "takeaway", "source", "contact", "display", "meta", "number", "by", "position",
    "when", "before", "after", "delta", "tag", "name", "role", "initials", "low", "high", "bad_label", "good_label",
    "items", "points", "side", "left", "right", "center", "callout", "bars", "columns", "rows", "labels", "series",
    "members", "x", "y", "quadrants", "result", "group", "legend", "summary",
})
# 表示しない（台本のキーのうち、文字ではないもの）。`TYPES` の全キーはどちらかに入っている（試験で確かめる）
HIDDEN_KEYS = frozenset({
    "type", "id", "talk", "transition", "steps", "fx", "image", "layout", "text_side", "tone", "motif", "split", "hide_text",
    "highlight", "current", "accent", "arrow", "max", "min", "widths", "align", "dim_rows", "fit", "value_labels", "focus", "takeaway_icon", "label_style",
})
# 一番外では表示しない（章の ID）が、入れ子の中では表示する（team の members[].role は肩書き）
NESTED_ONLY_KEYS = frozenset({"role"})
MAX_LINES = 10   # 1 回に台本係へ返す NG の行数（指示文を膨らませない）
SNIPPET = 28


@dataclass
class Segment:
    page_no: int
    page_id: str
    kind: str      # "slide"（スライドの文字）か "talk"（話す台本）
    where: str     # ページの中の位置（例: items[1].title・talk の 2 つ目）
    text: str


@dataclass
class Findings:
    ng: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _walk(value: Any, key: str, path: str, depth: int = 0) -> Iterable[tuple[str, str]]:
    """`value` の中の、表示される文字列を (位置, 文字) で返す。文字列の配列は親のキーを引き継ぐ。"""
    if isinstance(value, str):
        if key in VISIBLE_KEYS and value.strip():
            yield path, value
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, key, f"{path}[{i}]", depth)
    elif isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str) or k.startswith("_") or k not in VISIBLE_KEYS:
                continue
            if depth == 0 and k in NESTED_ONLY_KEYS:
                continue
            yield from _walk(v, k, f"{path}.{k}" if path else k, depth + 1)


def talk_lines(spec: dict[str, Any]) -> list[str]:
    talk = spec.get("talk")
    if isinstance(talk, str):
        return [talk]
    if isinstance(talk, list):
        return [t for t in talk if isinstance(t, str)]
    return []


def segments(slides: list[dict[str, Any]], only: set[str] | None = None) -> list[Segment]:
    """台本のページから、調べる文字を取り出す。`only` があれば、その id のページだけ。"""
    out: list[Segment] = []
    for no, spec in enumerate(slides, 1):
        if not isinstance(spec, dict):
            continue
        pid = str(spec.get("id") or "")
        if only is not None and pid not in only:
            continue
        for path, text in _walk(spec, "", ""):
            out.append(Segment(no, pid, "slide", path, text))
        for i, text in enumerate(talk_lines(spec), 1):
            if text.strip():
                out.append(Segment(no, pid, "talk", f"talk の{i}つ目", text))
    return out


_LIST_OR_QUOTE = re.compile(r"^(?:[-+*]\s|[#>|<]|(?:-{3,}|={3,}|_{3,}|`{3,}|~{3,}))")
_NUMBERED = re.compile(r"^(\d+)([.)])(\s)")
# 描画は台本の文字を Markdown としては読まず（`*語*` の強調だけ）、バッククォートも括弧もそのまま出す。
# yomiyasu は Markdown として読み、コード・リンクの先・画像・HTML・URL を「見えない領域」にして検査から外すので、
# その印になる記号を全角に替えて渡す（1 文字を 1 文字に替えるだけ。行は増えない）
_NEUTRAL = str.maketrans({"`": "｀", "<": "＜", ">": "＞", "[": "［", "]": "］"})


def _one_line(text: str) -> str:
    """見える文字をそのまま、Markdown の保護領域・見出し・箇条・引用にならない 1 行にする。"""
    line = " ".join(text.split()).translate(_NEUTRAL)
    line = re.sub(r"(?i)(https?|ftp):/+", lambda m: m.group(1) + "：//", line)
    line = re.sub(r"(?i)www\.", "www．", line)   # 語の境目を問わない（「_www.」のように記号の直後でも上流は URL として外す）
    m = _NUMBERED.match(line)
    if m:
        return f"{m.group(1)}\\{m.group(2)}{line[m.end(1) + 1:]}"
    if _LIST_OR_QUOTE.match(line):
        return "\\" + line
    return line


def _lint(lines: list[str]) -> list[dict[str, Any]]:
    from ..vendor.yomiyasu import yomiyasu_lint
    return list(yomiyasu_lint.lint_text("\n".join(lines))["findings"])


def _emojis(message: str) -> str:
    m = re.search(r"絵文字（(.+?)）", message)
    return m.group(1) if m else ""


def _filler(message: str) -> str:
    m = re.match(r"(.+?)が検出されました", message)
    return m.group(1) if m else "前置きの紋切り型"


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SNIPPET else text[:SNIPPET] + "…"


def _place(seg: Segment) -> str:
    who = f"{seg.page_no}枚目（id {seg.page_id}）" if seg.page_id else f"{seg.page_no}枚目"
    return f"{who}の{'話す台本（' + seg.where + '）' if seg.kind == 'talk' else '文字（' + seg.where + '）'}"


def _ng_line(seg: Segment, f: dict[str, Any]) -> str | None:
    rule, message = f.get("rule"), str(f.get("message") or "")
    if rule == "emoji_prohibited":
        mark = _emojis(message)
        mark = f"「{mark}」" if mark else ""
        if seg.kind == "talk":
            return f"NG: {_place(seg)}に絵文字{mark}がある。話す台本に絵文字は書かず、語で書く"
        return f"NG: {_place(seg)}に絵文字{mark}がある。絵文字は使わず、語で書く（アイコンが要るなら icon キー）"
    if rule == "meta_filler":
        return (f"NG: {_place(seg)}に前置きの紋切り型（{_filler(message)}）がある:「{_short(seg.text)}」。"
                "前置きは削り、本題から書く")
    return None


def _note_line(seg_page: Segment, f: dict[str, Any]) -> str:
    m = re.search(r"同一文末「(.+?)」", str(f.get("message") or ""))
    end = f"「{m.group(1)}」" if m else ""
    return (f"警告: {seg_page.page_no}枚目（id {seg_page.page_id}）の話す台本で、同じ文末{end}が 3 回以上続いている"
            "（読みにくくないか見直す。書き直しは求めない）")


def check(slides: list[dict[str, Any]], only: set[str] | None = None) -> Findings:
    """台本のスライドを調べて、NG の行と注意の行を返す。例外は出さない（出たら記録して空を返す）。"""
    out = Findings()
    try:
        segs = segments(slides, only)
        # スライドの文字: 1 つずつ別の段落（空行で区切る）。行番号 → 文字
        slide = [s for s in segs if s.kind == "slide"]
        if slide:
            lines: list[str] = []
            owner: dict[int, Segment] = {}
            for s in slide:
                if lines:
                    lines.append("")
                lines.append(_one_line(s.text))
                owner[len(lines)] = s
            for f in _lint(lines):
                if f.get("rule") in NG_RULES and f.get("line") in owner:
                    line = _ng_line(owner[f["line"]], f)
                    if line:
                        out.ng.append(line)
        # 話す台本: ページごとに 1 つ 1 行（隣り合う行は同じ段落。文末の繰り返しは段落の中で数える）
        by_page: dict[int, list[Segment]] = {}
        for s in segs:
            if s.kind == "talk":
                by_page.setdefault(s.page_no, []).append(s)
        for page in by_page.values():
            rows = {i: s for i, s in enumerate(page, 1)}
            for f in _lint([_one_line(s.text) for s in page]):
                rule, seg = f.get("rule"), rows.get(f.get("line"))
                if seg is None:
                    continue
                if rule in NG_RULES:
                    line = _ng_line(seg, f)
                    if line:
                        out.ng.append(line)
                elif rule in NOTE_RULES:
                    out.notes.append(_note_line(seg, f))
    except Exception:   # 台本係を止めない。リンターのせいで 1 冊が作れなくなるのを避ける
        log.exception("台本の日本語の検査が動かなかった（素通りする）")
        return Findings()
    out.ng = out.ng[:MAX_LINES]
    out.notes = out.notes[:MAX_LINES]
    return out


def problems_and_notes(slides: list[dict[str, Any]], *, enforce: bool,
                       only: set[str] | None = None) -> tuple[list[str], list[str]]:
    """(書き直させる NG の行, 記録だけの警告の行)。`enforce` が偽（最後の回）のときは NG も警告として記録するだけ。"""
    found = check(slides, only)
    if enforce:
        return found.ng, found.notes
    return [], found.notes + [ln.replace("NG:", "警告:", 1) for ln in found.ng]
