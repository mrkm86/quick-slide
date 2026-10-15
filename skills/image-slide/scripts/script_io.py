"""台本を読む。台本は html-slide と同じ JSON で、html-slide の側は何も変えない。

同じ台本を html-slide にも image-slide にも渡せることが、このエンジンの受け口の約束（ADR 0001）。
だから一番外のキー・型の名前・型ごとのキーは html-slide の `scripts/deck.py`・`deck_types.py` と同じにする
（ずれていないかは tests/test_logic.py が html-slide の定義と見比べる）。

違うのは検査の厳しさだけ。image-slide は文字を絵として描くので、文字の長さ・talk とクリックの数の照合・
画像の有無では止めない（クリックが無い・画像は生成が描く）。止めるのは「何を描くかが分からない」ときだけ。

- 一番外・型ごとに知らないキーは rc=2（html-slide でも止まるので、片方だけで通る台本を作らない）
- `_` で始まるキーは html-slide が読み飛ばす。image-slide はこのうち `_visual`（このページの描き方の追加の指示）と
  `_characters`（このページに登場させるキャラの絵・characters_of）を読む
"""
from __future__ import annotations

import json
import pathlib
import re

import oscompat as OS
import places as PL

SKILL = pathlib.Path(__file__).resolve().parent.parent
THEMES = SKILL / "themes"

TOP_KEYS = {"title", "theme", "mode", "footer", "page_numbers", "outline", "slides"}
COMMON_KEYS = {"type", "talk", "transition", "steps", "fx", "id", "role"}
HEAD_KEYS = {"kicker", "headline", "sub", "source"}
THEME_KEYS = {"background", "ink", "primary", "accent", "muted", "surface", "font", "font_files"}
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")

# 型 → (使えるキー, 下の帯（フッタ・ページ番号）を出すか, 必須のキー)。html-slide の deck_types.TYPES と同じ並び
TYPES = {
    "cover": ({"label", "title", "subtitle", "meta", "image", "layout", "text_side", "tone", "motif"}, False, ("title",)),
    "agenda": (HEAD_KEYS | {"items", "highlight"}, True, ("items",)),
    "section": ({"number", "label", "title", "subtitle", "image", "layout", "text_side", "tone", "motif"}, False, ("title",)),
    "statement": ({"kicker", "headline", "body", "source"}, True, ("headline",)),
    "big-number": (HEAD_KEYS | {"value", "unit", "label", "note", "side"}, True, ("headline", "value")),
    "cards": (HEAD_KEYS | {"items"}, True, ("headline", "items")),
    "flow": (HEAD_KEYS | {"items"}, True, ("headline", "items")),
    "compare": (HEAD_KEYS | {"left", "right", "accent", "arrow"}, True, ("headline", "left", "right")),
    "bar-chart": (HEAD_KEYS | {"bars", "unit", "note", "max", "callout"}, True, ("headline", "bars")),
    "table": (HEAD_KEYS | {"columns", "rows", "highlight", "widths"}, True, ("headline", "columns", "rows")),
    "photo": ({"image", "layout", "split", "kicker", "headline", "body", "caption", "hide_text"}, False, ("headline",)),
    "quote": ({"quote", "by", "position", "kicker", "source", "layout", "image"}, True, ("quote",)),
    "bullets": (HEAD_KEYS | {"items", "image", "side", "split"}, True, ("headline", "items")),
    "timeline": (HEAD_KEYS | {"items", "current"}, True, ("headline", "items")),
    "closing": (HEAD_KEYS | {"items", "contact", "image"}, True, ("headline",)),
    "summary": (HEAD_KEYS | {"points", "label"}, True, ("headline", "points")),
    "kpi": (HEAD_KEYS | {"items", "image"}, True, ("headline", "items")),
    "line-chart": (HEAD_KEYS | {"labels", "series", "unit", "min", "max", "note", "callout", "value_labels"}, True,
                   ("headline", "labels", "series")),
    "team": (HEAD_KEYS | {"members"}, True, ("headline", "members")),
    "matrix": (HEAD_KEYS | {"x", "y", "quadrants", "focus", "items", "callout"}, True, ("headline", "x", "y", "items")),
    "gallery": (HEAD_KEYS | {"items", "layout"}, True, ("headline", "items")),
    "figure": (HEAD_KEYS | {"image", "caption"}, True, ("headline", "image")),
    "annotate": (HEAD_KEYS | {"image", "items", "fit"}, True, ("headline", "image", "items")),
    "equation": (HEAD_KEYS | {"items", "result"}, True, ("headline", "items", "result")),
    "hub": (HEAD_KEYS | {"center", "items"}, True, ("headline", "center", "items")),
    "pairs": (HEAD_KEYS | {"items", "bad_label", "good_label"}, True, ("headline", "items")),
    "notice": (HEAD_KEYS | {"items"}, True, ("headline", "items")),
    "deflist": (HEAD_KEYS | {"items"}, True, ("headline", "items")),
    "steps": (HEAD_KEYS | {"items", "current", "layout"}, True, ("headline", "items")),
}
# 締めの帯（takeaway）を置ける型（html-slide の deck_types.TAKEAWAY_TYPES と同じ）
TAKEAWAY_TYPES = ("cards", "flow", "compare", "bullets", "timeline", "summary", "kpi", "bar-chart", "line-chart",
                  "table", "matrix", "equation", "hub", "pairs", "deflist", "steps")
for _t in TAKEAWAY_TYPES:
    TYPES[_t] = (TYPES[_t][0] | {"takeaway", "takeaway_icon"}, TYPES[_t][1], TYPES[_t][2])

# 型ごとの「配置の指定」のキー（一番外だけ）。文字としては描かせず、描き方の指示として別に渡す（prompt.py）。
# 同じ名前でも型によって中身のことがある（big-number の side は脇の数字・bullets の side は写真の側）ので、型ごとに持つ
LAYOUT_KEYS = {
    "cover": {"layout", "text_side", "tone", "motif"},
    "section": {"layout", "text_side", "tone", "motif"},
    "photo": {"layout", "split"},
    "quote": {"layout"},
    "bullets": {"side", "split"},
    "gallery": {"layout"},
    "annotate": {"fit"},
    "table": {"widths"},
    "bar-chart": {"max"},
    "line-chart": {"min", "max", "value_labels"},
    "compare": {"arrow"},
    "steps": {"layout"},
}


class ScriptError(Exception):
    def __init__(self, msg: str, rc: int = 2):
        super().__init__(msg)
        self.rc = rc


def load_config() -> dict:
    return PL.load_config("image-slide")


# --- テーマ（A ブランドの色） ---------------------------------------------------------

def _hex(v, what) -> str:
    s = str(v).strip().strip("'\"").lower()
    if not re.fullmatch(r"#[0-9a-f]{6}", s):
        raise ScriptError(f"テーマの {what} は #rrggbb で書く: {v!r}")
    return s


def load_theme(spec, script_dir: pathlib.Path, cfg: dict) -> dict:
    """--theme > 台本の theme > 設定の default_theme > default。名前は themes/<名前>.yaml、パスも書ける。"""
    import yaml
    name = str(spec or cfg.get("default_theme") or "default")
    if "/" in name or name.endswith((".yaml", ".yml")):
        p = pathlib.Path(name).expanduser()
        path = p if p.is_absolute() else script_dir / p
    else:
        path = THEMES / f"{name}.yaml"
    if not path.is_file():
        have = ", ".join(sorted(x.stem for x in THEMES.glob("*.yaml")))
        raise ScriptError(f"テーマが無い: {name}（あるのは {have}）")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ScriptError(f"{path}: テーマが辞書ではない")
    extra = sorted(set(raw) - THEME_KEYS)
    if extra:
        raise ScriptError(f"{path}: テーマに知らないキー: {', '.join(extra)}（使えるのは {', '.join(sorted(THEME_KEYS))}）")
    t = {"name": path.stem, "font": str(raw.get("font") or "Yu Gothic"), "font_files": raw.get("font_files")}
    if "accent" in raw and "primary" not in raw:
        raise ScriptError(f"{path}: テーマの主役の色は primary に名前が変わった（accent は差し色・任意）。accent: を primary: に書き換える")
    for k in ("background", "ink", "primary"):
        if k not in raw:
            raise ScriptError(f"{path}: テーマに {k} が無い")
        t[k] = _hex(raw[k], k)
    for k in ("accent", "muted", "surface"):
        if raw.get(k):
            t[k] = _hex(raw[k], k)
    return t


# --- 台本 ------------------------------------------------------------------------------

def load(path) -> dict:
    """台本を読み、1枚ずつ {no, type, spec, chrome} にする。誤りは ScriptError（rc=2）。"""
    path = pathlib.Path(path).expanduser().resolve()
    try:
        sc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ScriptError(f"台本が無い: {path}")
    except json.JSONDecodeError as e:
        raise ScriptError(f"台本の JSON が読めない: {path}（{e}）")
    if not isinstance(sc, dict):
        raise ScriptError("台本の一番外はオブジェクト（title・slides …）で書く")
    extra = sorted(set(sc) - TOP_KEYS)
    if extra:
        raise ScriptError(f"台本の一番外に知らないキー: {', '.join(extra)}（使えるのは {', '.join(sorted(TOP_KEYS))}）。"
                          "image-slide だけの値（ロゴ・参照画像・作風）は引数で渡す")
    slides = sc.get("slides")
    if not isinstance(slides, list) or not slides:
        raise ScriptError("slides が無い（1枚以上の配列）")
    out = []
    for no, spec in enumerate(slides, 1):
        if not isinstance(spec, dict):
            raise ScriptError(f"{no}枚目: オブジェクトで書く")
        typ = spec.get("type")
        if typ not in TYPES:
            raise ScriptError(f"{no}枚目: 知らない型 {typ!r}（使えるのは {', '.join(TYPES)}）")
        keys, chrome, required = TYPES[typ]
        bad = sorted(k for k in set(spec) - keys - COMMON_KEYS if not k.startswith("_"))
        if bad:
            raise ScriptError(f"{no}枚目（{typ}）: 知らないキー: {', '.join(bad)}（この型で使えるのは {', '.join(sorted(keys))}）")
        for k in required:
            if spec.get(k) in (None, "", [], {}):
                raise ScriptError(f"{no}枚目（{typ}）: {k} が無い")
        if typ == "closing" and not (spec.get("items") or spec.get("contact")):
            raise ScriptError(f"{no}枚目（closing）: items か contact のどちらかを書く")
        if "_visual" in spec and not isinstance(spec["_visual"], str):
            raise ScriptError(f"{no}枚目（{typ}）: _visual は文字で書く")
        characters_of(spec, path.parent, no)   # 書き方の誤りは読むときに止める（ファイルの有無も）
        if typ == "photo" and "hide_text" in spec:   # html-slide と同じ所で止める（ADR 0018）
            if not isinstance(spec["hide_text"], bool):
                raise ScriptError(f"{no}枚目（photo）: hide_text は true か false")
            if spec["hide_text"] and spec.get("layout", "full") != "full":
                raise ScriptError(f"{no}枚目（photo）: hide_text は layout: full のときだけ")
        if typ == "quote" and spec.get("layout") == "full":
            chrome = False
        out.append({"no": no, "type": typ, "spec": spec, "chrome": chrome})
    return {"path": path, "dir": path.parent, "title": str(sc.get("title") or path.stem),
            "theme": sc.get("theme"), "footer": str(sc.get("footer") or ""),
            "page_numbers": sc.get("page_numbers", False) is True,   # 既定はオフ（html-slide と同じ・2026-10-06）
            "slides": out}


def talk_text(spec) -> str:
    """ノートに入れる話す台本。絵にはクリックが無いので、全部を改行でつなぐ。"""
    talk = spec.get("talk")
    if talk is None:
        return ""
    if isinstance(talk, str):
        return talk
    if isinstance(talk, list) and all(isinstance(x, str) for x in talk):
        return "\n".join(x for x in talk if x)
    raise ScriptError(f"{spec.get('type')}: talk は文字の配列で書く")


def headline_of(spec) -> str:
    """そのページの見出し（文脈の一覧と、PPTX の代替テキストに使う）。"""
    for k in ("headline", "title", "quote"):
        v = spec.get(k)
        if isinstance(v, str) and v.strip():
            return plain(v)
    if spec.get("value"):
        return plain(str(spec["value"]) + str(spec.get("unit") or ""))
    return spec.get("type", "")


def image_only(spec) -> bool:
    """photo の全面の hide_text: 画像だけを描くページ（扉絵）。見出しはページの名前（文脈の一覧・代替テキスト）として
    持つだけで、絵には描かせない（html-slide の ADR 0026 と同じ意味・ADR 0018）。"""
    return spec.get("type") == "photo" and spec.get("hide_text") is True


def plain(s: str) -> str:
    """強調の印 ** と改行を外した文字。"""
    return s.replace("**", "").replace("\n", "")


# --- 画像の枠 ----------------------------------------------------------------------------

def item_slot(typ: str, key: str, i: int) -> str:
    """並びの i 番目（1から）の画像の枠の名前。html-slide の deck_types と同じ（gallery は image1・cards は item1・team は member1）。"""
    if key == "members":
        return f"member{i}"
    return f"image{i}" if typ == "gallery" else f"item{i}"


def _image_specs(spec):
    """(枠の名前, 画像の指定) を順に返す。"""
    typ = spec.get("type", "")
    if spec.get("image") is not None:
        yield "image", spec["image"]
    for key in ("items", "members"):
        for i, it in enumerate(spec.get(key) or [], 1):
            if isinstance(it, dict) and it.get("image") is not None:
                yield item_slot(typ, key, i), it["image"]
    for side in ("left", "right"):
        it = spec.get(side)
        if isinstance(it, dict) and it.get("image") is not None:
            yield side, it["image"]


def images_of(spec, script_dir: pathlib.Path, no: int) -> list[dict]:
    """このページの画像の枠。在るファイルは参照画像として Codex に見せ、無いものは prompt を言葉で描かせる。

    置き場の規則は html-slide と同じ: path が無ければ <台本のフォルダ>/images/s{NN}-{枠の名前}.png（.jpg・.webp も）。
    """
    out = []
    for slot, im in _image_specs(spec):
        if isinstance(im, str):
            im = {"path": im}
        if not isinstance(im, dict):
            raise ScriptError(f"{no}枚目: 画像の枠 {slot} はパスか {{path, prompt, alt}} で書く")
        found = None
        if im.get("path"):
            p = str(im["path"])
            if OS.looks_like_url(p):
                raise ScriptError(f"{no}枚目: {slot} に URL は使えない（ローカルのファイルを置いてパスで書く）: {p}")
            q = pathlib.Path(p).expanduser()
            q = q if q.is_absolute() else script_dir / q
            if q.suffix.lower() not in IMAGE_EXTS:
                raise ScriptError(f"{no}枚目: {slot} の画像は {' / '.join(IMAGE_EXTS)} のどれか: {q.name}")
            found = q if q.is_file() else None
            if found is None and not im.get("prompt"):
                raise ScriptError(f"{no}枚目: {slot} の画像が無い（prompt も無いので描けない）: {q}")
        else:
            stem = script_dir / "images" / f"s{no:02d}-{slot}"
            found = next((stem.with_suffix(e) for e in IMAGE_EXTS if stem.with_suffix(e).is_file()), None)
            if found is None and not im.get("prompt"):
                raise ScriptError(f"{no}枚目: {slot} に path も prompt も無い")
        out.append({"slot": slot, "path": found.resolve() if found else None,
                    "prompt": str(im.get("prompt") or ""), "alt": str(im.get("alt") or "")})
    return out


def content_of(spec, images: list[dict], ref_offset: int = 0) -> dict:
    """プロンプトの「描く文字」に入れる中身。一番外の共通キー（talk など）と配置の指定を外し、画像の枠は言葉に置き換える。

    外すのは**一番外だけ**。並びの中の role（体制の肩書き）や side（大きな数字の脇の数字）は中身なので残す。
    """
    typ = spec.get("type", "")
    by_slot = {x["slot"]: x for x in images}
    ref_no = {x["slot"]: i for i, x in enumerate([x for x in images if x["path"]], ref_offset + 1)}

    def image_words(slot):
        x = by_slot.get(slot)
        if x is None:
            return "（画像）"
        if x["path"] is not None:
            return f"（添付の参照画像{ref_no[slot]}の内容をこの位置に描く）"
        return f"（この位置に描く絵: {x['prompt']}）"

    def walk(v, slot=None):
        """slot は、この辞書の中の image がどの枠か（items の2つ目なら item2）。"""
        if isinstance(v, dict):
            return {k: (image_words(slot) if k == "image" else walk(x))
                    for k, x in v.items() if not k.startswith("_")}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    drop = COMMON_KEYS | LAYOUT_KEYS.get(typ, set()) | {"hide_text"}
    if image_only(spec):   # 画像だけのページ: 出典（caption）のほかは文字を描かせない
        drop |= {"kicker", "headline", "body"}
    out = {}
    for k, v in spec.items():
        if k in drop or k.startswith("_"):
            continue
        if k == "image":
            out[k] = image_words("image")
        elif k in ("items", "members") and isinstance(v, list):
            out[k] = [walk(it, item_slot(typ, k, i)) for i, it in enumerate(v, 1)]
        elif k in ("left", "right") and isinstance(v, dict):
            out[k] = walk(v, k)
        else:
            out[k] = walk(v)
    return out


def layout_of(spec) -> dict:
    """このページの配置の指定（LAYOUT_KEYS のうち書かれているもの）。"""
    return {k: spec[k] for k in sorted(LAYOUT_KEYS.get(spec.get("type", ""), ())) if k in spec}


# --- キャラ（_characters） ------------------------------------------------------------------

def characters_of(spec, script_dir: pathlib.Path, no: int) -> list[dict]:
    """このページに登場させるキャラ。[{name, pose, path}]。

    台本には `"_characters": [{"name": "Hana", "image": "<パス>", "pose": "考える"}]` と書く（並べた順が参照画像の順）。
    絵を選ぶのは呼ぶ側（用途とポーズに合う絵のパスを書く）。image-slide は
    キャラの画像集を知らない。造形は文章で書かず、絵に任せる。
    """
    raw = spec.get("_characters")
    if raw is None:
        return []
    if not isinstance(raw, list) or not raw:
        raise ScriptError(f"{no}枚目: _characters は [{{name, image, pose}}] の配列で書く")
    out = []
    for i, c in enumerate(raw, 1):
        where = f"{no}枚目: _characters の {i} 人目"
        if not isinstance(c, dict):
            raise ScriptError(f"{where}は {{name, image, pose}} で書く")
        extra = sorted(set(c) - {"name", "image", "pose"})
        if extra:
            raise ScriptError(f"{where}に知らないキー: {', '.join(extra)}（使えるのは name・image・pose）")
        name, pose, image = c.get("name"), c.get("pose", ""), c.get("image")
        if not isinstance(name, str) or not name.strip():
            raise ScriptError(f"{where}: name（誰か）が無い。参照画像の何枚目が誰かを結ぶのに使う")
        if not isinstance(pose, str):
            raise ScriptError(f"{where}: pose は文字で書く")
        if not isinstance(image, str) or not image.strip():
            raise ScriptError(f"{where}: image（絵のパス）が無い")
        if OS.looks_like_url(image):
            raise ScriptError(f"{where}: image に URL は使えない（ローカルのファイルのパスで書く）: {image}")
        q = pathlib.Path(image).expanduser()
        q = q if q.is_absolute() else script_dir / q
        if q.suffix.lower() not in IMAGE_EXTS:
            raise ScriptError(f"{where}: image は {' / '.join(IMAGE_EXTS)} のどれか: {q.name}")
        if not q.is_file():
            raise ScriptError(f"{where}: 絵が無い: {q}")
        out.append({"name": name.strip(), "pose": pose.strip(), "path": q.resolve()})
    return out


def character_words(chars: list[dict], first: int) -> str:
    """参照画像の first 枚目から並ぶキャラの言い方（描く指示に入れる）。"""
    lines = []
    for i, c in enumerate(chars, first):
        lines.append(f"添付の参照画像{i}は {c['name']}" + (f"（ポーズ・表情: {c['pose']}）" if c["pose"] else ""))
    return ("\n".join(lines) + "\n上のキャラクターをこのページに登場させる。造形・顔・髪・服・色・塗りは参照画像のまま描き"
            "（作風に合わせて描き直さない）、ポーズと表情は参照画像と添えた言葉に合わせる。中身と関わる位置に置く"
            "（指差しなら指す先に要素を置く・見出しや図の横に立つ）。名前は対応を示すためだけ。名前の文字を新しく書き足さない（参照画像に描かれている名札などはそのまま）")
