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
    """取り込んだページの HTML から、絶対配置の箱（位置・大きさ・中の文字）を拾う。座標は外側の箱からの足し算。

    文字の箱は、段落（字の大きさ・行の高さ・余白・そろえ方・文字の幅の見積もり）も控える。PowerPoint の文字の箱は、中の文字より
    ずっと大きく取ってあることが多い（3 行だけの本文の枠がページの下まである）。空いている所を見るときは、箱の大きさでなく、
    文字が実際に占める範囲を使う（`_tight`）。"""

    _NUM = re.compile(r"(left|top|width|height)\s*:\s*(-?[\d.]+)px")
    _VOID = {"img", "br", "hr", "input", "meta", "link"}
    _FONT = re.compile(r"font-size\s*:\s*([\d.]+)px")
    _LH = re.compile(r"line-height\s*:\s*([\d.]+)(px)?")
    _PAD = re.compile(r"padding\s*:\s*([^;]+)")
    _MARGIN = re.compile(r"margin\s*:\s*([^;]+)")
    _JUSTIFY = re.compile(r"justify-content\s*:\s*([a-z-]+)")
    _ALIGN = re.compile(r"text-align\s*:\s*([a-z]+)")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # (タグ, 原点 x, 原点 y, 箱, 段落, 字の大きさ)
        self.stack: list[tuple[str, float, float, dict[str, Any] | None, dict[str, Any] | None, float | None]] = []
        self.boxes: list[dict[str, Any]] = []

    def _origin(self) -> tuple[float, float]:
        return (self.stack[-1][1], self.stack[-1][2]) if self.stack else (0.0, 0.0)

    @staticmethod
    def _four(text: str) -> tuple[float, float, float, float]:
        """`padding`・`margin` の省略形（px）を 上・右・下・左 に。読めなければ 0。"""
        nums = [float(n) for n in re.findall(r"(-?[\d.]+)(?:px)?", text)][:4]
        if not nums:
            return 0.0, 0.0, 0.0, 0.0
        if len(nums) == 1:
            return nums[0], nums[0], nums[0], nums[0]
        if len(nums) == 2:
            return nums[0], nums[1], nums[0], nums[1]
        if len(nums) == 3:
            return nums[0], nums[1], nums[2], nums[1]
        return nums[0], nums[1], nums[2], nums[3]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        style = dict(attrs).get("style") or ""
        ox, oy = self._origin()
        box = None
        para = self.stack[-1][4] if self.stack else None
        size = self.stack[-1][5] if self.stack else None
        font = self._FONT.search(style)
        if "position:absolute" in style.replace(" ", ""):
            got = {k: float(v) for k, v in self._NUM.findall(style)}
            x, y = ox + got.get("left", 0.0), oy + got.get("top", 0.0)
            w, h = got.get("width", 0.0), got.get("height", 0.0)
            if w > 1 and h > 1:
                pad = self._PAD.search(style)
                just = self._JUSTIFY.search(style)
                box = {"x": round(x), "y": round(y), "w": round(w), "h": round(h), "kind": "画像" if tag == "img" else "図形",
                       "text": "", "_pad": self._four(pad.group(1)) if pad else (0.0, 0.0, 0.0, 0.0),
                       "_justify": just.group(1) if just else "flex-start", "_paras": [], "_loose": tag == "table"}
                self.boxes.append(box)
            ox, oy = x, y
            para, size = None, None
        else:
            owner = next((b for _, _, _, b, _, _ in reversed(self.stack) if b is not None), None)
            if tag in ("table", "ul", "ol") and owner is not None:
                owner["_loose"] = True   # 表・箇条書きの入れ物は、段落の並びとして見積もれない（箱の大きさのまま使う）
            lh = self._LH.search(style)
            if para is None and owner is not None and font and lh and tag in ("div", "p"):
                # 段落（字の大きさと行の高さを持つ、絶対配置でない入れ物）
                margin = self._MARGIN.search(style)
                mt, _, mb, _ = self._four(margin.group(1)) if margin else (0.0, 0.0, 0.0, 0.0)
                align = self._ALIGN.search(style)
                fs = float(font.group(1))
                para = {"font": fs, "lh": float(lh.group(1)) if lh.group(2) else float(lh.group(1)) * fs, "mt": mt, "mb": mb,
                        "align": align.group(1) if align else "left", "width": 0.0}
                owner["_paras"].append(para)
                size = fs
            elif font:
                size = float(font.group(1))
            if tag == "br" and para is not None:
                para["breaks"] = para.get("breaks", 0) + 1
        if tag not in self._VOID:
            self.stack.append((tag, ox, oy, box, para, size))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        for _, _, _, box, _, _ in reversed(self.stack):
            if box is not None:
                box["text"] = (box["text"] + text)[:80]
                box["kind"] = "文字"
                break
        para, size = (self.stack[-1][4], self.stack[-1][5]) if self.stack else (None, None)
        if para is not None:
            para["width"] += _text_width(data, size or para["font"])
        else:   # 段落の外の文字（見積もれない作り）。箱の大きさのまま使う
            owner = next((b for _, _, _, b, _, _ in reversed(self.stack) if b is not None), None)
            if owner is not None:
                owner["_loose"] = True


def _text_width(text: str, size: float) -> float:
    """文字の幅の見積もり（px）。全角は 1 字 = 字の大きさ、半角の英数字はその 0.56 倍、空白は 0.3 倍。"""
    import unicodedata

    total = 0.0
    for ch in text:
        if ch in " \t\n\r":
            total += 0.3
        elif unicodedata.east_asian_width(ch) in ("W", "F", "A"):
            total += 1.0
        else:
            total += 0.56
    return total * size


TEXT_WIDTH_SLACK = 1.1    # 幅の見積もりの余裕（字体の違いで行が増えることがある）
TEXT_EDGE_SLACK = 12      # 詰めた箱の外側に足す余白（px）


def _tight(box: dict[str, Any]) -> dict[str, Any]:
    """文字の箱を、文字が実際に占める範囲に詰める（見積もり。箱より大きくはしない）。段落として読めない箱はそのまま。"""
    out = {k: v for k, v in box.items() if not k.startswith("_")}
    paras = box.get("_paras") or []
    if box["kind"] != "文字" or box.get("_loose") or not paras:
        return out
    top, right, bottom, left = box["_pad"]
    inner = max(1.0, box["w"] - left - right)
    used_h, widest, wrapped = top + bottom, 0.0, False
    for p in paras:
        width = p["width"] * TEXT_WIDTH_SLACK
        lines = max(1, -(-int(width) // max(1, int(inner)))) + int(p.get("breaks", 0))
        wrapped = wrapped or lines > 1
        used_h += lines * p["lh"] + p["mt"] + p["mb"]
        widest = max(widest, min(inner, width))
    used_h = min(float(box["h"]), used_h + TEXT_EDGE_SLACK)
    used_w = float(box["w"]) if wrapped else min(float(box["w"]), widest + left + right + TEXT_EDGE_SLACK)
    y = box["y"] + {"center": (box["h"] - used_h) / 2, "flex-end": box["h"] - used_h}.get(box["_justify"], 0.0)
    aligns = {p["align"] for p in paras}
    if aligns == {"center"}:
        x = box["x"] + (box["w"] - used_w) / 2
    elif aligns == {"right"}:
        x = box["x"] + box["w"] - used_w
    elif aligns <= {"left", "start", "justify"}:
        x = float(box["x"])
    else:   # そろえ方が混ざる箱は、横は詰めない
        x, used_w = float(box["x"]), float(box["w"])
    out.update(x=round(x), y=round(y), w=round(used_w), h=round(used_h))
    return out


# 写真・画像を入れるための枠の印（「（製品の写真）」「画像」「Photo」など。短い 1 語だけの文字）
_SLOT_LABEL = re.compile(r"^[\s（(\[【「]*(?:[^\s（()）\[\]【】「」]{0,6}の)?(?:写真|画像|イメージ|イラスト|photo|image|picture)[\s）)\]】」]*$", re.I)
SLOT_KIND = "置き場"
SLOT_MIN_SIDE = 160


def _inside(inner: dict[str, Any], outer: dict[str, Any]) -> float:
    """inner のうち outer の中にある面積の割合。"""
    w = min(inner["x"] + inner["w"], outer["x"] + outer["w"]) - max(inner["x"], outer["x"])
    h = min(inner["y"] + inner["h"], outer["y"] + outer["h"]) - max(inner["y"], outer["y"])
    return max(0.0, w) * max(0.0, h) / max(1.0, inner["w"] * inner["h"])


def page_boxes(html: str) -> list[dict[str, Any]]:
    """取り込んだページの箱（{x, y, w, h, kind: 文字・画像・図形・置き場, text}）。ページ全体を覆う背景は除く。

    - 文字の箱は、文字が実際に占める範囲に詰めてある（`_tight`）
    - 写真・画像を入れるための枠（「（製品の写真）」のような印の付いた図形か、その印の箱そのもの）は kind を「置き場」にする。
      印の文字の箱は一覧から外す（そこに画像を重ねてよい）"""
    p = _Boxes()
    p.feed(html or "")
    raw = [b for b in p.boxes if not (b["w"] >= PAGE_W * 0.95 and b["h"] >= PAGE_H * 0.95 and b["kind"] != "文字")]
    labels = [b for b in raw if b["kind"] == "文字" and _SLOT_LABEL.match(b["text"] or "")]
    slots: list[int] = []
    drop: list[int] = []
    labels_of: dict[int, str] = {}   # 置き場の箱 → 印の文字（そこに絵を置いたら、印は消す。`clear_slot_label`）
    for lab in labels:
        tight = _tight(lab)
        # 印を囲む図形（文字を持たない・印の大半を中に含む・ページ全体でない）のうち、いちばん小さいもの
        frames = [b for b in raw if b["kind"] == "図形" and _inside(tight, b) >= 0.5
                  and min(b["w"], b["h"]) >= SLOT_MIN_SIDE and b["w"] * b["h"] < PAGE_W * PAGE_H * 0.7]
        if frames:
            frame = min(frames, key=lambda b: b["w"] * b["h"])
            slots.append(id(frame))
            labels_of[id(frame)] = lab["text"]
            drop.append(id(lab))
        elif min(lab["w"], lab["h"]) >= SLOT_MIN_SIDE:
            slots.append(id(lab))   # 印の箱そのものが枠
            labels_of[id(lab)] = lab["text"]
    out = []
    for b in raw:
        if id(b) in drop:
            continue
        if id(b) in slots:
            out.append({"x": b["x"], "y": b["y"], "w": b["w"], "h": b["h"], "kind": SLOT_KIND, "text": b["text"],
                        "label": labels_of.get(id(b), "")})
        else:
            out.append(_tight(b))
    return out


def slot_under(boxes: list[dict[str, Any]], box: dict[str, Any]) -> dict[str, Any] | None:
    """重ねる箱が載る置き場（箱の真ん中が置き場の中にあるもの）。無ければ None。"""
    cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
    return next((b for b in boxes if b.get("kind") == SLOT_KIND and b["x"] <= cx <= b["x"] + b["w"]
                 and b["y"] <= cy <= b["y"] + b["h"]), None)


def clear_slot_label(html: str, label: str) -> str:
    """置き場の印の文字（「（製品の写真）」など）を、ページの HTML から消す（そこに絵を置いたので、印が絵の後ろに残らないように）。
    同じ文字のいちばん最初の 1 つだけ。見つからなければそのまま。"""
    label = (label or "").strip()
    if not label:
        return html
    return re.sub(r">(\s*)" + re.escape(htmllib.escape(label, quote=False)) + r"(\s*)<", r">\1\2<", html or "", count=1)


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
