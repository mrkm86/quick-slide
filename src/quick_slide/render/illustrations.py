"""台本の挿絵（画像の枠の `image: {"svg": "<svg …>"}`）を、エンジンに渡す前に PNG にする（docs/adr/0011-svg-illustrations.md）。

- 挿絵のスキル（`skills/illust`）の `illust.py png` に通す。スキルが SVG を確かめて描いてよい形に絞り、Chrome で PNG にする
- 出来た PNG はデッキの台本のフォルダの `illustrations/<鍵>.png` に置き、枠を `{"path": "illustrations/<鍵>.png"}` に替える
  （エンジンの台本 `scripts/engine.json` からの相対。エンジンは変えない・素材はファイルで受ける）。台本の版は SVG のまま
- 鍵は SVG の文字から作る。同じ挿絵は描き直さない（台本の版が進んでも、その枠の SVG が同じなら使い回す）
- 直さないと描けない挿絵（スキルの `NG:` の行）は、どのページの挿絵かを付けて返す（台本係に書き直させる）
- Chrome の在りかは html-slide の `browser.py` が知っている（エンジンの探し方が正本）。聞けなければスキルが自分で探す
"""

from __future__ import annotations

import copy
import hashlib
import html as htmllib
import re
import tempfile
import threading
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from ..childenv import child_env
from ..worker.proc import run_process
from ..worker.tools import Tools

ILLUST_TIMEOUT_SECONDS = 90
FOLDER = "illustrations"
RC_NG, RC_DEPENDENCY = 2, 4


class IllustrationFailed(Exception):
    """挿絵を PNG にできなかった（Chrome が無い・動かない）。台本の直しでは直らない。"""


# 取り込んだページ（型 html）に重ねる挿絵（ページの `illustrations`。docs/adr/0013-overlay-illustrations.md）。
# 1 つは {"image": {"svg": …}, "x", "y", "w", "h"}（1920×1080 の座標）。エンジンに渡す前に、PNG にしてページの HTML の最後に
# `<img>` として足し、キーは消す（エンジンは知らないキー）
OVERLAY_KEY = "illustrations"
PAGE_W, PAGE_H = 1920, 1080


def has_illustrations(data: dict[str, Any]) -> bool:
    return bool(_slots(data)) or any(isinstance(s, dict) and s.get(OVERLAY_KEY) for s in data.get("slides") or [])


def apply_overlays(data: dict[str, Any]) -> dict[str, Any]:
    """取り込んだページの重ねる挿絵（PNG の path になったもの）を、ページの HTML の最後に足す。キーは消す。"""
    out = copy.deepcopy(data)
    for s in out.get("slides") or []:
        items = s.pop(OVERLAY_KEY, None) if isinstance(s, dict) else None
        if not items or not isinstance(s.get("html"), str):
            continue
        tags = []
        for it in items:
            path = ((it or {}).get("image") or {}).get("path")
            if not isinstance(path, str):
                continue
            x, y, w, h = (float(it.get(k) or 0) for k in ("x", "y", "w", "h"))
            fit = "cover" if it.get("fit") == "cover" else "contain"   # 写真は枠いっぱいに切り抜き、挿絵・図は全体を収める
            tags.append(f'<img src="{htmllib.escape(path)}" alt="" style="position:absolute;left:{x:g}px;top:{y:g}px;'
                        f'width:{w:g}px;height:{h:g}px;object-fit:{fit}" />')
        if tags:
            s["html"] += ('\n<div style="position:absolute;left:0;top:0;width:0;height:0" data-name="挿絵">'
                          + "".join(tags) + "</div>")
    return out


class _Boxes(HTMLParser):
    """取り込んだページの HTML から、絶対配置の箱（位置・大きさ・中の文字）を拾う。座標は外側の箱からの足し算。"""

    _NUM = re.compile(r"(left|top|width|height)\s*:\s*(-?[\d.]+)px")
    _VOID = {"img", "br", "hr", "input", "meta", "link"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, float, float, dict[str, Any] | None]] = []
        self.boxes: list[dict[str, Any]] = []

    def _origin(self) -> tuple[float, float]:
        return (self.stack[-1][1], self.stack[-1][2]) if self.stack else (0.0, 0.0)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        style = dict(attrs).get("style") or ""
        ox, oy = self._origin()
        box = None
        if "position:absolute" in style.replace(" ", ""):
            got = {k: float(v) for k, v in self._NUM.findall(style)}
            x, y = ox + got.get("left", 0.0), oy + got.get("top", 0.0)
            w, h = got.get("width", 0.0), got.get("height", 0.0)
            if w > 1 and h > 1:
                box = {"x": round(x), "y": round(y), "w": round(w), "h": round(h), "kind": "画像" if tag == "img" else "図形",
                       "text": ""}
                self.boxes.append(box)
            ox, oy = x, y
        if tag not in self._VOID:
            self.stack.append((tag, ox, oy, box))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        for _, _, _, box in reversed(self.stack):
            if box is not None:
                box["text"] = (box["text"] + text)[:80]
                box["kind"] = "文字"
                break


def page_boxes(html: str) -> list[dict[str, Any]]:
    """取り込んだページの箱（{x, y, w, h, kind: 文字・画像・図形, text}）。ページ全体を覆う背景は除く。"""
    p = _Boxes()
    p.feed(html or "")
    return [b for b in p.boxes if not (b["w"] >= PAGE_W * 0.95 and b["h"] >= PAGE_H * 0.95 and b["kind"] != "文字")]


def _slots(data: dict[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
    """(ページの id, 枠を持つ dict, キー) の並び。キーの値が `{"svg": …}` の枠だけ。"""
    out: list[tuple[str, dict[str, Any], str]] = []

    def walk(value: Any, page: str) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if k == "image" and isinstance(v, dict) and "svg" in v:
                    out.append((page, value, k))
                else:
                    walk(v, page)
        elif isinstance(value, list):
            for v in value:
                walk(v, page)

    for s in data.get("slides") or []:
        if isinstance(s, dict):
            walk(s, str(s.get("id") or "?"))
    return out


def key_of(svg: str) -> str:
    return hashlib.sha256(svg.encode("utf-8")).hexdigest()[:24]


def materialize(data: dict[str, Any], *, scripts_dir: Path, tools: Tools, home: Path, chrome: str | None = None,
                stop: threading.Event | None = None) -> tuple[dict[str, Any], list[str]]:
    """挿絵の枠を PNG の `path` に替えた台本と、台本係に返す `NG:` の行。挿絵が無ければ台本をそのまま返す。

    Chrome が無い・動かないときは IllustrationFailed（台本を直しても直らない）。"""
    if not _slots(data):
        return data, []
    out = copy.deepcopy(data)
    folder = scripts_dir / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    script = tools.skills_dir / "illust" / "scripts" / "illust.py"
    for page, holder, key in _slots(out):
        slot = holder[key]
        svg = slot.get("svg")
        if not isinstance(svg, str) or not svg.strip():
            problems.append(f"NG: id \"{page}\" の挿絵（image.svg）が空。<svg viewBox=…> で書く")
            continue
        name = f"{key_of(svg)}.png"
        target = folder / name
        if not target.is_file():
            with tempfile.TemporaryDirectory(prefix="qs-illust-") as tmp:
                src = Path(tmp) / "illust.svg"
                src.write_text(svg, encoding="utf-8")
                cmd = [tools.python, str(script), "png", "--svg", str(src), "--out", str(target)]
                if chrome:
                    cmd += ["--chrome", chrome]
                r = run_process(cmd, cwd=Path(tmp), env=child_env(home=home, extra=tools.extra_env),
                                timeout=ILLUST_TIMEOUT_SECONDS, stop=stop)
            if r.rc == RC_NG:
                problems += [f"NG: id \"{page}\" の挿絵: {line[3:].strip()}" for line in r.stdout.splitlines()
                             if line.startswith("NG:")] or [f"NG: id \"{page}\" の挿絵を描けない（書き直す）"]
                continue
            if r.rc == RC_DEPENDENCY:
                raise IllustrationFailed("挿絵を描く Chrome が見つかりません。`quick_slide_setup.py --doctor` で確かめてください。")
            if not r.ok or not target.is_file():
                raise IllustrationFailed("挿絵を描けませんでした（Chrome が動きませんでした）。もう一度頼んでください。")
        rest = {k: v for k, v in slot.items() if k != "svg"}
        holder[key] = {**rest, "path": f"{FOLDER}/{name}"}
    return out, problems
