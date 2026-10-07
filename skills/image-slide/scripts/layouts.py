"""ユーザーが持ち込むレイアウト（ADR 0006）。

製品はレイアウトを持たない（ADR 0005: 構図は Codex に任せる）。ただ、週報・月報のように見せ方が決まっている資料のために、
**ユーザーが自分のレイアウトをファイルで持ち込める**。ファイルはエンジンの外（呼ぶ側のスキルや自分のフォルダ）に置き、
`--layout <yaml>` で渡す（何度でも）。台本のページに `_layout` で名前を書いたページだけ、そのレイアウトで描く。

レイアウトのファイル（YAML）:

    name: weekly-report                  # 必須。ページの _layout で呼ぶ名前
    composition: |                       # 必須。構図（区画の位置・大きさ・何をどこに置くか）を文章で
      上にヘッダ、左1/3にキーワード、右2/3に主役の図解、下に学びの帯…
    reference: weekly-sample.png         # 任意。構図の見本の絵（YAML からの相対でよい）。参照画像として添える
    parts:                               # 任意。台本の型のキーに無い部品の置き場。ページ側が文字を渡す
      week_badge: ヘッダ右上の角丸のバッジ
      takeaway: 下部の主役色の帯

台本のページ:

    "_layout": "weekly-report"
    "_layout": {"name": "weekly-report", "parts": {"week_badge": "2026-W40", "takeaway": "…"}}

html-slide は `_` で始まるキーを読み飛ばすので、台本は2つのエンジンで共通のまま。
"""
from __future__ import annotations

import pathlib

import script_io as S

FIELDS = {"name", "composition", "reference", "parts"}


def load(paths) -> dict:
    """--layout で渡されたファイルを読む。{名前: {name, composition, reference(Path|None), parts, path}}。誤りは rc=2。"""
    import yaml
    out = {}
    for raw in paths or []:
        path = pathlib.Path(raw).expanduser().resolve()
        if not path.is_file():
            raise S.ScriptError(f"レイアウトのファイルが無い（--layout）: {path}")
        try:
            d = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise S.ScriptError(f"{path}: レイアウトの YAML が読めない（{e}）")
        if not isinstance(d, dict):
            raise S.ScriptError(f"{path}: レイアウトは name・composition … のキーで書く")
        if "style" in d:
            raise S.ScriptError(f"{path}: レイアウトのファイルに style は書かない。スタイルは別のファイルにして --style で渡す（ADR 0007）")
        extra = [k for k in d if k not in FIELDS]
        if extra:
            raise S.ScriptError(f"{path}: レイアウトに知らないキー: {', '.join(map(repr, extra))}（使えるのは {', '.join(sorted(FIELDS))}）")
        name, comp = d.get("name"), d.get("composition")
        if not isinstance(name, str) or not name.strip():
            raise S.ScriptError(f"{path}: name が無い")
        if not isinstance(comp, str) or not comp.strip():
            raise S.ScriptError(f"{path}: composition（構図の文章）が無い")
        if name in out:
            raise S.ScriptError(f"レイアウトの名前 {name} が2つある: {out[name]['path']} と {path}")
        parts = {} if d.get("parts") is None else d["parts"]          # 「parts:」だけ（null）は空。false・[] は誤り
        if not isinstance(parts, dict) or not all(isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in parts.items()):
            raise S.ScriptError(f"{path}: parts は「部品の名前: 置き場の説明」の組で書く")
        ref = None
        if d.get("reference") is not None and (not isinstance(d["reference"], str) or not d["reference"].strip()):
            raise S.ScriptError(f"{path}: reference は構図の見本の絵のパス（文字）で書く")
        if d.get("reference") is not None:
            ref = pathlib.Path(str(d["reference"])).expanduser()
            ref = (ref if ref.is_absolute() else path.parent / ref).resolve()
            if not ref.is_file():
                raise S.ScriptError(f"{path}: 構図の見本（reference）が無い: {ref}")
            if ref.suffix.lower() not in S.IMAGE_EXTS:
                raise S.ScriptError(f"{path}: 構図の見本は {' / '.join(S.IMAGE_EXTS)} のどれか: {ref.name}")
        out[name] = {"name": name, "composition": comp.strip(),
                     "reference": ref, "parts": parts, "path": path}
    return out


def resolve(page: dict, layouts: dict) -> dict | None:
    """このページのレイアウト（{name, spec, parts}）か None（構図は Codex に任せる）。"""
    want = page["spec"].get("_layout")
    if want is None:
        return None
    where = f"{page['no']}枚目（{page['type']}）"
    if isinstance(want, str):
        name, values = want, {}
    elif isinstance(want, dict) and all(k in ("name", "parts") for k in want) and isinstance(want.get("name"), str):
        name, values = want["name"], ({} if want.get("parts") is None else want["parts"])
    else:
        raise S.ScriptError(f"{where}: _layout はレイアウトの名前か {{name, parts}} で書く")
    if name not in layouts:
        have = "、".join(layouts) or "無し"
        raise S.ScriptError(f"{where}: レイアウト {name!r} が渡されていない（--layout <ファイル> で渡す。今あるのは {have}）。"
                            "製品はレイアウトを持たない（ADR 0005）")
    spec = layouts[name]
    if not isinstance(values, dict):
        raise S.ScriptError(f"{where}: _layout の parts は「部品の名前: 文字」で書く")
    unknown = [k for k in values if k not in spec["parts"]]
    if unknown:
        raise S.ScriptError(f"{where}: レイアウト {name} に無い部品: {', '.join(map(repr, unknown))}（あるのは {', '.join(spec['parts']) or '無し'}）")
    for k, v in values.items():
        bad = _bad_text(v)
        if bad:
            raise S.ScriptError(f"{where}: 部品 {k} の文字が{bad}（文字か、文字を並べた配列・辞書で書く）")
    return {"name": name, "spec": spec, "parts": values}


def _bad_text(v) -> str | None:
    """部品の文字として描けない値なら、その理由。文字・数・それを並べた配列と辞書だけを許す（null・真偽値・空は描けない）。"""
    if isinstance(v, bool) or v is None:
        return f" {v!r}"
    if isinstance(v, str):
        return None if v.strip() else "空"
    if isinstance(v, (int, float)):
        return None
    if isinstance(v, list):
        return " 空の配列" if not v else next((b for b in map(_bad_text, v) if b), None)
    if isinstance(v, dict):
        if not v:
            return " 空の辞書"
        if not all(isinstance(k, str) for k in v):
            return " 文字でないキーを持つ"
        return next((b for b in map(_bad_text, v.values()) if b), None)
    return f" {type(v).__name__}"


def prompt_part(lay: dict, ref_no: int | None) -> str:
    """描く指示の「レイアウト」の節。ref_no は構図の見本が何番目の参照画像か。"""
    import json
    spec = lay["spec"]
    lines = [f"レイアウト（ユーザーが指定した「{spec['name']}」。この構図を守る）:\n{spec['composition']}"]
    if lay["parts"]:
        filled = {k: {"置き場": spec["parts"][k], "描く文字": v} for k, v in lay["parts"].items()}
        lines.append("レイアウトの部品（それぞれ置き場の説明の位置に、描く文字を一字一句そのまま描く。置き場の説明は描かない）:\n"
                     + json.dumps(filled, ensure_ascii=False, indent=1))
    if ref_no is not None:
        lines.append(f"添付の参照画像{ref_no}は、このレイアウトの構図の見本。区画の位置・大きさ・余白の取り方・部品の形をこの見本に寄せる。"
                     "見本に描かれている文字・数字・絵の中身は写さない（中身はこのページのものを描く）")
    return "\n\n".join(lines)
