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

    文字の箱は、中の並び（段落の字の大きさ・行の高さ・余白・字下げ・そろえ方・行ごとの文字の幅の見積もり、空の段落の高さ）も
    控える。PowerPoint の文字の箱は、中の文字よりずっと大きく取ってあることが多い（3 行だけの本文の枠がページの下まである）。
    空いている所を見るときは、箱の大きさでなく、文字が実際に占める範囲を使う（`_tight`）。読むのは、エンジンの取り込み
    （`skills/html-slide/scripts/pptx_text.py`）が出す形。その形でないもの（縦書き・回した箱・表・段落の外の文字）は詰めない。"""

    _NUM = re.compile(r"(left|top|width|height)\s*:\s*(-?[\d.]+)px")
    _VOID = {"img", "br", "hr", "input", "meta", "link"}
    _FONT = re.compile(r"font-size\s*:\s*([\d.]+)px")
    _LH = re.compile(r"line-height\s*:\s*([\d.]+)(px)?")
    _PAD = re.compile(r"(?<![a-z-])padding\s*:\s*([^;]+)")
    _MARGIN = re.compile(r"(?<![a-z-])margin\s*:\s*([^;]+)")
    _ML = re.compile(r"margin-left\s*:\s*(-?[\d.]+)px")
    _INDENT = re.compile(r"text-indent\s*:\s*(-?[\d.]+)px")
    _HEIGHT = re.compile(r"(?<![a-z-])height\s*:\s*([\d.]+)px")
    _JUSTIFY = re.compile(r"justify-content\s*:\s*([a-z-]+)")
    _ALIGN = re.compile(r"text-align\s*:\s*([a-z]+)")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # (タグ, 原点 x, 原点 y, 箱, 段落, 字の大きさ)
        self.stack: list[tuple[str, float, float, dict[str, Any] | None, dict[str, Any] | None, float | None]] = []
        self.boxes: list[dict[str, Any]] = []
        self._lines: list[int] = [0]
        self._src = ""

    def feed(self, data: str) -> None:
        self._src = data
        self._lines = [0]
        at = data.find("\n")
        while at >= 0:
            self._lines.append(at + 1)
            at = data.find("\n", at + 1)
        super().feed(data)

    def _origin(self) -> tuple[float, float]:
        return (self.stack[-1][1], self.stack[-1][2]) if self.stack else (0.0, 0.0)

    def _owner(self) -> dict[str, Any] | None:
        return next((b for _, _, _, b, _, _ in reversed(self.stack) if b is not None), None)

    @staticmethod
    def _four(text: str) -> tuple[float, float, float, float]:
        """`padding`・`margin` の省略形（px）を 上・右・下・左 に。読めなければ 0。"""
        nums = [_num(n) for n in re.findall(r"(-?[\d.]+)(?:px)?", text)][:4]
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
        flat = style.replace(" ", "")
        ox, oy = self._origin()
        box = None
        para = self.stack[-1][4] if self.stack else None
        size = self.stack[-1][5] if self.stack else None
        font = self._FONT.search(style)
        if "position:absolute" in flat:
            got = {k: _num(v) for k, v in self._NUM.findall(style)}
            x, y = ox + got.get("left", 0.0), oy + got.get("top", 0.0)
            w, h = got.get("width", 0.0), got.get("height", 0.0)
            if w > 1 and h > 1:
                pad = self._PAD.search(style)
                just = self._JUSTIFY.search(style)
                # 縦書き（row-reverse）・回した箱・表は、横書きの段落の並びとして見積もれない
                loose = (tag == "table" or "flex-direction:row-reverse" in flat or "transform:" in flat or "writing-mode" in flat
                         or "letter-spacing" in flat)
                box = {"x": round(x), "y": round(y), "w": round(w), "h": round(h), "kind": "画像" if tag == "img" else "図形",
                       "text": "", "_pad": tuple(max(0.0, v) for v in (self._four(pad.group(1)) if pad else (0.0, 0.0, 0.0, 0.0))),
                       "_justify": just.group(1) if just else "flex-start", "_items": [], "_loose": loose, "_spans": []}
                self.boxes.append(box)
            ox, oy = x, y
            para, size = None, None
        else:
            owner = self._owner()
            lh = self._LH.search(style)
            if owner is not None and ("transform:" in flat or "writing-mode" in flat or "letter-spacing" in flat
                                      or tag in ("table", "ul", "ol")):
                owner["_loose"] = True   # 回した文字・縦書き・字間を広げた文字・表は、幅を見積もれない
            if para is None and owner is not None and tag in ("div", "p"):
                if font and lh:
                    # 段落（字の大きさと行の高さを持つ、絶対配置でない入れ物）。箇条書きは、印と本文を横に並べた入れ物（display:flex）
                    margin = self._MARGIN.search(style)
                    mt, _, mb, _ = self._four(margin.group(1)) if margin else (0.0, 0.0, 0.0, 0.0)
                    ml, ti, align = self._ML.search(style), self._INDENT.search(style), self._ALIGN.search(style)
                    fs = _num(font.group(1))
                    para = {"kind": "para", "font": fs, "lh": _num(lh.group(1)) if lh.group(2) else _num(lh.group(1)) * fs,
                            "mt": max(0.0, mt), "mb": max(0.0, mb), "ml": max(0.0, _num(ml.group(1))) if ml else 0.0,
                            "ti": _num(ti.group(1)) if ti else 0.0, "align": align.group(1) if align else "left",
                            "nowrap": "white-space:pre;" in flat + ";", "bullet": "display:flex" in flat, "lines": [0.0]}
                    owner["_items"].append(para)
                    size = fs
                else:
                    # 空の段落（高さだけの入れ物）。その高さぶんは文字の並びの中の空き
                    hgt = self._HEIGHT.search(style)
                    if hgt and "flex:none" in flat:
                        owner["_items"].append({"kind": "space", "h": _num(hgt.group(1))})
            elif para is not None:
                if font:
                    size = _num(font.group(1))
                align = self._ALIGN.search(style)
                if align and para["bullet"]:
                    para["align"] = align.group(1)   # 箇条書きのそろえ方は、本文の入れ物に付く
            if tag == "br" and para is not None:
                para["lines"].append(0.0)
        if tag not in self._VOID:
            self.stack.append((tag, ox, oy, box, para, size))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data: str) -> None:
        para, size = (self.stack[-1][4], self.stack[-1][5]) if self.stack else (None, None)
        if para is not None:
            # 段落は white-space: pre-wrap。文字の中の改行は、そこで行が変わる
            for i, part in enumerate(data.split("\n")):
                if i:
                    para["lines"].append(0.0)
                para["lines"][-1] += _text_width(part, size or para["font"])
        text = data.strip()
        if not text:
            return
        owner = self._owner()
        if owner is None:
            return
        owner["text"] = (owner["text"] + text)[:80]
        owner["kind"] = "文字"
        if para is None:
            owner["_loose"] = True   # 段落の外の文字（見積もれない作り）。箱の大きさのまま使う
        # この文字の、HTML の中の位置（置き場の印を消すときに、その文字だけを指す）
        line, col = self.getpos()
        if 1 <= line <= len(self._lines):
            start = self._lines[line - 1] + col
            end = self._src.find("<", start)
            owner["_spans"].append((start, len(self._src) if end < 0 else end))


NUM_LIMIT = 100000.0   # ページの座標として意味のある大きさ（これを超える・数でない値は 0 にする）


def _num(text: Any) -> float:
    """CSS の数（有限で、ページの座標として意味のある大きさ）。読めない・大きすぎる値は 0。"""
    try:
        v = float(text)
    except (TypeError, ValueError):
        return 0.0
    return v if v == v and abs(v) <= NUM_LIMIT else 0.0


def _text_width(text: str, size: float) -> float:
    """文字の幅の見積もり（px）。全角は 1 字 = 字の大きさ、半角の英数字はその 0.56 倍、空白は 0.3 倍。"""
    import unicodedata

    total = 0.0
    for ch in text:
        if ch in " \t\r":
            total += 0.3
        elif unicodedata.east_asian_width(ch) in ("W", "F", "A"):
            total += 1.0
        else:
            total += 0.56
    return total * size


TEXT_WIDTH_SLACK = 1.1    # 幅の見積もりの余裕（字体の違いで行が増えることがある）
TEXT_EDGE_SLACK = 12      # 詰めた箱の外側に足す余白（px）
BULLET_ALLOWANCE = 1.5    # 箇条書きの印とその余白の幅（字の大きさの何倍か）


def _tight(box: dict[str, Any]) -> dict[str, Any]:
    """文字の箱を、文字が実際に占める範囲に詰める（見積もり。箱より大きくはしない）。段落として読めない箱はそのまま。"""
    out = {k: v for k, v in box.items() if not k.startswith("_")}
    items = box.get("_items") or []
    paras = [p for p in items if p["kind"] == "para"]
    if box["kind"] != "文字" or box.get("_loose") or not paras:
        return out
    top, right, bottom, left = box["_pad"]
    inner = max(1.0, box["w"] - left - right)
    used_h, wrapped = top + bottom, False
    leads, widest = [], 0.0   # 段落ごとの左の字下げ（印の幅を含む）・いちばん長い行の幅（字下げを除く）
    for p in items:
        if p["kind"] == "space":
            used_h += p["h"]
            continue
        # 段落の左の字下げ（margin-left）・1 行目の字下げ（text-indent）・箇条書きの印の幅を、使える幅から引く
        lead = p["ml"] + (BULLET_ALLOWANCE * p["font"] if p["bullet"] else 0.0)
        room = max(p["font"], inner - lead)
        leads.append(lead)
        lines = 0
        for i, width in enumerate(p["lines"]):
            width = width * TEXT_WIDTH_SLACK + (max(0.0, p["ti"]) if i == 0 else 0.0)
            n = 1 if p["nowrap"] else max(1, -(-int(width) // max(1, int(room))))
            wrapped = wrapped or n > 1
            lines += n
            widest = max(widest, width if p["nowrap"] else min(room, width))
        used_h += lines * max(p["lh"], p["font"]) + p["mt"] + p["mb"]
    used_h = max(1.0, min(float(box["h"]), used_h + TEXT_EDGE_SLACK))
    y = box["y"] + {"center": (box["h"] - used_h) / 2, "flex-end": box["h"] - used_h}.get(box["_justify"], 0.0)
    out.update(y=round(y), h=round(used_h))
    aligns = {p["align"] for p in paras}
    lo, hi = min(leads), max(leads)
    # 横: 文字は、字下げのあとの幅の中でそろう。字下げが段落で違うぶん（hi - lo）は、広いほうに合わせて見込む
    span = widest + (hi - lo) + TEXT_EDGE_SLACK
    if wrapped or not (aligns <= {"left", "start"} or aligns == {"center"} or aligns == {"right"}):
        return out   # 折り返す箱・そろえ方が混ざる箱・両端ぞろえの箱は、横は詰めない
    if aligns == {"center"}:
        x = box["x"] + left + lo + (inner - lo) / 2 - span / 2
    elif aligns == {"right"}:
        x = box["x"] + box["w"] - right - span
    else:   # 左そろえ: 左の端は動かさない（字下げのぶんも箱に含める）
        x, span = float(box["x"]), left + hi + widest + right + TEXT_EDGE_SLACK
    x = max(float(box["x"]), x)
    span = max(1.0, min(box["x"] + box["w"] - x, span))
    out.update(x=round(x), w=round(span))
    return out


# 写真・画像を入れるための枠の印（「（製品の写真）」「画像」「Photo」など。短い 1 語だけの文字）
_SLOT_LABEL = re.compile(r"^[\s（(\[【「]*(?:[^\s（()）\[\]【】「」]{0,6}の)?(?:写真|画像|イメージ|イラスト|photo|image|picture)[\s）)\]】」]*$", re.I)
SLOT_KIND = "置き場"
SLOT_MIN_SIDE = 160
LABEL_COVERED = 0.5   # 絵が印の文字のこの割合より多くを覆ったら、印を消す


def _inside(inner: dict[str, Any], outer: dict[str, Any]) -> float:
    """inner のうち outer の中にある面積の割合。"""
    w = min(inner["x"] + inner["w"], outer["x"] + outer["w"]) - max(inner["x"], outer["x"])
    h = min(inner["y"] + inner["h"], outer["y"] + outer["h"]) - max(inner["y"], outer["y"])
    return max(0.0, w) * max(0.0, h) / max(1.0, inner["w"] * inner["h"])


def page_boxes(html: str) -> list[dict[str, Any]]:
    """取り込んだページの箱（{x, y, w, h, kind: 文字・画像・図形・置き場, text}）。ページ全体を覆う背景は除く。

    - 文字の箱は、文字が実際に占める範囲に詰めてある（`_tight`）
    - 写真・画像を入れるための枠は kind を「置き場」にする。条件は 3 つ全部: 「（製品の写真）」のような短い印の文字の箱がある・
      その印を中に含む、文字を持たない図形（枠）がある・その枠に、印のほかの文字が載っていない。印の文字の箱は一覧から外す
      （そこに絵を重ねてよい）。枠の無い「写真」という見出しや、文の載ったカードは置き場にしない"""
    p = _Boxes()
    p.feed(html or "")
    raw = [b for b in p.boxes if not (b["w"] >= PAGE_W * 0.95 and b["h"] >= PAGE_H * 0.95 and b["kind"] != "文字")]
    tights = {id(b): _tight(b) for b in raw}
    labels = [b for b in raw if b["kind"] == "文字" and _SLOT_LABEL.match(b["text"] or "")]
    slots: dict[int, dict[str, Any]] = {}   # 置き場の枠 → 印の箱
    for lab in labels:
        frames = [b for b in raw if b["kind"] == "図形" and _inside(tights[id(lab)], b) >= 0.9
                  and min(b["w"], b["h"]) >= SLOT_MIN_SIDE and b["w"] * b["h"] < PAGE_W * PAGE_H * 0.7]
        if not frames:
            continue
        frame = min(frames, key=lambda b: b["w"] * b["h"])
        others = [b for b in raw if b["kind"] == "文字" and b is not lab and _inside(tights[id(b)], frame) > 0.0]
        if not others and id(frame) not in slots:
            slots[id(frame)] = lab
    drop = {id(lab) for lab in slots.values()}
    out = []
    for b in raw:
        if id(b) in drop:
            continue
        if id(b) in slots:
            lab = slots[id(b)]
            t = tights[id(lab)]
            out.append({"x": b["x"], "y": b["y"], "w": b["w"], "h": b["h"], "kind": SLOT_KIND, "text": "",
                        "label": lab["text"], "label_spans": list(lab["_spans"]),
                        "label_box": {k: t[k] for k in ("x", "y", "w", "h")}})
        else:
            out.append(tights[id(b)])
    return out


def slot_under(boxes: list[dict[str, Any]], box: dict[str, Any]) -> dict[str, Any] | None:
    """重ねる箱が載る置き場（箱の真ん中が置き場の中にあるもの）。無ければ None。"""
    cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
    return next((b for b in boxes if b.get("kind") == SLOT_KIND and b["x"] <= cx <= b["x"] + b["w"]
                 and b["y"] <= cy <= b["y"] + b["h"]), None)


def clear_slot_label(html: str, box: dict[str, Any]) -> str:
    """重ねる箱が置き場に載るなら、その置き場の印の文字（「（製品の写真）」など）をページの HTML から消す（絵の後ろに印が
    残らないように）。消すのは、その置き場の印の文字そのもの（HTML の中の位置で指す）。同じ言葉がほかにあっても消さない。"""
    slot = slot_under(page_boxes(html or ""), box)
    # 消すのは、絵が印を覆うときだけ（枠の端に小さく置いただけで、見えている印を消さない）
    if slot is None or _inside(slot["label_box"], box) < LABEL_COVERED:
        return html
    out = html
    for start, end in sorted(slot.get("label_spans") or [], reverse=True):
        raw = out[start:end]
        if "<" in raw or ">" in raw:   # 位置がずれている（読み方の違い）。触らない
            continue
        lead = raw[:len(raw) - len(raw.lstrip())]
        tail = raw[len(raw.rstrip()):] if raw.strip() else ""
        out = out[:start] + lead + tail + out[end:]
    return out


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
