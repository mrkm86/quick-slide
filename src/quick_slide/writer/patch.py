"""台本係の答え（直し・差し込み）を確かめ、台本に当てる。

**当てるのは指定したページの id だけ**（と、そのページを分けたときに増えたページ）。ほかのページは同じ
オブジェクトをそのまま残すので、台本の版のファイル（`scriptdoc.dumps`）でも 1 文字も変わらない。
台本係が隣のページも直したいと言ってきたら（suggestions）、当てずに文として利用者に見せる。

答えの形（直し）:

    {"pages": [{"id": "p-7", "route": "script", "slides": [{...}, ...]},
               {"id": "p-8", "route": "image", "image": {"slot": "image", "note": "夜の景色に"}},
               {"id": "p-9", "route": "both", "slides": [...], "image": {"note": "..."}},
               {"id": "p-10", "route": "keep"}],
     "suggestions": [{"id": "p-6", "text": "..."}], "summary": "..."}

答えの形（差し込み）: `{"slides": [{...}], "summary": "..."}`

確かめて合わないところは `NG:` の行にして返す（台本係に直させる。回数はワーカーが数える）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..scriptdoc import HIDDEN_KEY, PAGE_ID_RE, slides_of

ROUTES = ("script", "image", "both", "keep")
MAX_SPLIT = 3
MAX_INSERT = 3
MAX_SUGGESTIONS = 10
MAX_NOTE = 1000


@dataclass
class PagePatch:
    key: str
    route: str
    slides: list[dict[str, Any]] | None = None
    image: dict[str, Any] | None = None


@dataclass
class Patch:
    pages: dict[str, PagePatch] = field(default_factory=dict)
    suggestions: list[dict[str, str]] = field(default_factory=list)
    summary: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def script_changes(self) -> list[PagePatch]:
        return [p for p in self.pages.values() if p.route in ("script", "both")]

    @property
    def image_changes(self) -> list[PagePatch]:
        return [p for p in self.pages.values() if p.route in ("image", "both")]


def image_paths(value: Any) -> set[str]:
    """ページの中の画像のパス（`image: "<パス>"`・`{"path": "<パス>"}`）。"""
    out: set[str] = set()

    def walk(v: Any, key: str | None = None) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                if k == "path" and isinstance(x, str):
                    out.add(x)
                elif k == "image" and isinstance(x, str):
                    out.add(x)
                else:
                    walk(x, k)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(value)
    return out


def _check_page(page: Any, where: str, allowed_paths: set[str], forbidden_keys=("_characters", "_layout", HIDDEN_KEY),
                *, motion: bool = False) -> list[str]:
    if not isinstance(page, dict):
        return [f"NG: {where}: ページはオブジェクトで書く"]
    problems = []
    if motion:
        # モーションの場面は型を持たない（題・拍の数・HTML は、エンジンの形の検査が確かめる）
        if not isinstance(page.get("title"), str):
            problems.append(f"NG: {where}: title が無い")
    elif not isinstance(page.get("type"), str):
        problems.append(f"NG: {where}: type が無い")
    for k in forbidden_keys:
        if k in page:
            problems.append(f"NG: {where}: {k} は書かない")
    bad = sorted(p for p in image_paths(page) if p not in allowed_paths)
    if bad:
        problems.append(f"NG: {where}: 画像の path は「添えた画像」のパスか、いまある path だけ（{', '.join(bad)}）")
    return problems


def parse_revision(answer: Any, *, script: dict[str, Any], targets: list[str], pictures: dict[str, Any],
                   attachments: list[str], mode: str) -> Patch:
    """台本係の直しの答えを確かめる。`pictures` は id → 直せる絵（None・"あり"・枠の名前の並び）。"""
    patch = Patch()
    problems = patch.problems
    if not isinstance(answer, dict) or not isinstance(answer.get("pages"), list):
        problems.append("NG: 答えに pages（直したページの配列）が無い")
        return patch
    slides = slides_of(script)
    by_key = {str(s.get("id")): s for s in slides}
    taken = set(by_key)
    new_ids: set[str] = set()
    for i, item in enumerate(answer["pages"], 1):
        if not isinstance(item, dict):
            problems.append(f"NG: pages の {i} 番目がオブジェクトではない")
            continue
        key = item.get("id")
        route = item.get("route")
        if key not in targets:
            problems.append(f"NG: pages の {i} 番目の id \"{key}\" は直すページではない（直すページ: {', '.join(targets)}）")
            continue
        if key in patch.pages:
            problems.append(f"NG: id \"{key}\" が pages に 2 回ある")
            continue
        if route not in ROUTES:
            problems.append(f"NG: id \"{key}\" の route は script・image・both・keep のどれか")
            continue
        pp = PagePatch(key=key, route=route)
        allowed = image_paths(by_key.get(key, {})) | set(attachments)
        if route in ("script", "both"):
            new = item.get("slides")
            if not isinstance(new, list) or not new or len(new) > MAX_SPLIT:
                problems.append(f"NG: id \"{key}\": slides（直したページの中身）を 1〜{MAX_SPLIT} ページで書く")
                continue
            ok = True
            for j, page in enumerate(new):
                where = f"id \"{key}\" の slides の {j + 1} 番目"
                errs = _check_page(page, where, allowed, motion=mode == "motion")
                if errs:
                    problems.extend(errs)
                    ok = False
                    continue
                pid = page.get("id")
                if j == 0:
                    if pid != key:
                        problems.append(f"NG: {where}: id は元のまま \"{key}\" にする")
                        ok = False
                elif not isinstance(pid, str) or not PAGE_ID_RE.match(pid) or pid in taken or pid in new_ids:
                    problems.append(f"NG: {where}: 分けて増やすページには、台本の中で重ならない新しい id を付ける")
                    ok = False
                else:
                    new_ids.add(pid)
            if not ok:
                continue
            pp.slides = new
        if route in ("image", "both"):
            pic = pictures.get(key)
            if not pic:
                problems.append(f"NG: id \"{key}\" には直せる絵が無い。route は script か keep にする")
                continue
            image = item.get("image") if isinstance(item.get("image"), dict) else {}
            note = image.get("note")
            note = note.strip()[:MAX_NOTE] if isinstance(note, str) else ""
            slot = image.get("slot")
            if mode == "presentation":
                slots = list(pic) if isinstance(pic, (list, tuple)) else []
                if isinstance(slot, str) and slot in slots:
                    pass
                elif len(slots) == 1:
                    slot = slots[0]
                else:
                    problems.append(f"NG: id \"{key}\": image.slot に直す枠の名前を書く（{', '.join(slots)} のどれか）")
                    continue
            else:
                slot = None
            pp.image = {"slot": slot, "note": note}
        patch.pages[key] = pp
    missing = [k for k in targets if k not in patch.pages]
    if missing and not problems:
        problems.append(f"NG: 直すページの答えが無い: {', '.join(missing)}（pages に全部入れる）")
    raw = answer.get("suggestions")
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, dict):
            continue
        sid, text = s.get("id"), s.get("text")
        if isinstance(sid, str) and sid in by_key and sid not in targets and isinstance(text, str) and text.strip():
            patch.suggestions.append({"id": sid, "text": text.strip()[:300]})
        if len(patch.suggestions) >= MAX_SUGGESTIONS:
            break
    summary = answer.get("summary")
    patch.summary = summary.strip()[:300] if isinstance(summary, str) else ""
    return patch


def apply_revision(script: dict[str, Any], patch: Patch) -> dict[str, Any]:
    """指定したページだけを差し替える（分けたページは、そのすぐ後ろに入れる）。ほかのページは同じオブジェクトのまま。"""
    out: list[Any] = []
    for page in script.get("slides") or []:
        key = page.get("id") if isinstance(page, dict) else None
        pp = patch.pages.get(key) if isinstance(key, str) else None
        if pp is None or pp.slides is None:
            out.append(page)
            continue
        first, *rest = pp.slides
        first = dict(first)
        if page.get(HIDDEN_KEY) is True:
            first[HIDDEN_KEY] = True   # 非表示の印は台本係ではなく画面が持つ
        out.append(first)
        out.extend(rest)
    return {**script, "slides": out}


def parse_insert(answer: Any, *, script: dict[str, Any], attachments: list[str],
                 mode: str = "presentation") -> tuple[list[dict[str, Any]], str, list[str]]:
    """差し込みの答え → (足すページ, 一言, NG の行)。"""
    problems: list[str] = []
    if not isinstance(answer, dict) or not isinstance(answer.get("slides"), list) or not answer["slides"]:
        return [], "", ["NG: 答えに slides（足すページの配列）が無い"]
    new = answer["slides"]
    if len(new) > MAX_INSERT:
        problems.append(f"NG: 足すページは {MAX_INSERT} ページまで")
    taken = {str(s.get("id")) for s in slides_of(script)}
    seen: set[str] = set()
    for j, page in enumerate(new, 1):
        where = f"slides の {j} 番目"
        problems.extend(_check_page(page, where, set(attachments), motion=mode == "motion"))
        pid = page.get("id") if isinstance(page, dict) else None
        if not isinstance(pid, str) or not PAGE_ID_RE.match(pid) or pid in taken or pid in seen:
            problems.append(f"NG: {where}: 台本の中で重ならない新しい id を付ける（英小文字・数字・ハイフンで 40 字以内）")
        else:
            seen.add(pid)
    summary = answer.get("summary")
    return new, (summary.strip()[:300] if isinstance(summary, str) else ""), problems


def insert_after(script: dict[str, Any], anchor: str | None, pages: list[dict[str, Any]]) -> dict[str, Any]:
    slides = list(script.get("slides") or [])
    if anchor is None:
        at = 0
    else:
        at = next(i for i, s in enumerate(slides) if isinstance(s, dict) and s.get("id") == anchor) + 1
    return {**script, "slides": slides[:at] + list(pages) + slides[at:]}
