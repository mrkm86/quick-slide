"""デザインテンプレートの試作契約。保存文書から、共通の台本・素材準備・描画指示を作る。

保存する項目は画面と同じ4つ（基本・構成・追加指示・リソース）だけ。
本番の投入・ワーカーには未接続。自由文の遵守や素材選択を実行したとは扱わない。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from . import palette

SCHEMA_VERSION = 3
TOP_KEYS = {"schema_version", "id", "brand_id", "revision", "based_on", "name", "modes", "description",
            "structure", "instructions", "resources", "attachments"}
RESOURCE_KEYS = {"id", "name", "kind", "path", "usage"}
ATTACHMENT_KEYS = {"id", "name", "file", "url", "image", "usage", "sha256"}
# フォルダのリソースで、中身の説明として先に読むファイル。
GUIDE_FILES = ("SKILL.md", "README.md", "README.txt")
COMPILER_VERSION = 4

# 旧形式（v1/v2）の細かい欄。追加指示へ見出し付きで寄せるときの名前。
_OLD_SCRIPT_FIELDS = {
    "purpose": "資料の目的", "audience": "想定する読者", "writing": "文章と情報の扱い",
    "conversion": "原稿から台本への写し方", "mandatory": "必ず守る指示",
}
_OLD_DRAWING_FIELDS = {
    "overall": "全体の見た目", "body": "本文の配置と情報量", "diagrams": "図表の使い分け",
    "cover": "表紙の見せ方", "colors": "ブランド色の使い方", "typography": "文字の見せ方",
    "illustration": "挿絵の画風", "forbidden": "描かないもの・避ける表現", "additional": "描画への追加指示",
}
_OLD_LOOKS = {"photo": "写真調", "handdrawn": "手描き風"}
_OLD_SCOPES = {"script": "主に台本づくりで使う。", "drawing": "主に描画で使う。"}


def new_document(template_id: str, brand_id: str, name: str = "") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION, "id": template_id, "brand_id": brand_id, "revision": 0, "based_on": None,
        "name": name, "modes": ["presentation"], "description": "",
        "structure": "", "instructions": "", "resources": [], "attachments": [],
    }


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _upgrade(old: dict[str, Any]) -> dict[str, Any]:
    """v1/v2 を一方向に移す。画面から編集できない欄を残さず、文章は失わない。"""
    script, drawing = old.get("script") or {}, old.get("drawing") or {}
    if not isinstance(script, dict) or not isinstance(drawing, dict):
        raise ValueError("旧形式のテンプレートを確認してください。")
    structure = script.get("structure", "")
    if isinstance(structure, list):
        if any(not isinstance(row, dict) or any(not isinstance(row.get(k), str) for k in ("role", "instructions", "condition"))
               for row in structure):
            raise ValueError("旧形式の構成を確認してください。")
        structure = "\n\n".join(
            f"## {i}. {row['role']}\n\n{row['instructions']}" + (f"\n\n条件: {row['condition']}" if row["condition"] else "")
            for i, row in enumerate(structure, 1))
    parts: list[str] = []
    if script.get("source_policy") == "preserve" and _text(structure).strip():
        parts.append("## 原稿の構成の扱い\n渡された原稿が承認済みなら、そのページ数・順番・主張・明示された素材指定を保持し、構成へ無断で圧縮しない。")
    # 前の画面は、同じ文章を台本・描画の両方へ書いていた。重ねて写さない。
    shared = _text(script.get("mandatory")) and script.get("mandatory") == drawing.get("additional")
    for key, label in _OLD_SCRIPT_FIELDS.items():
        value = _text(script.get(key)).strip()
        if value:
            parts.append(value if key == "mandatory" and shared else f"## {label}\n{value}")
    for key, label in _OLD_DRAWING_FIELDS.items():
        value = _text(drawing.get(key)).strip()
        if value and not (key == "additional" and shared):
            parts.append(f"## {label}\n{value}")
    look = drawing.get("image_look")
    if look in _OLD_LOOKS:
        parts.append(f"## 画風\n{_OLD_LOOKS[look]}で描く。")
    if drawing.get("image_only") or drawing.get("bleed"):
        parts.append("## 描画範囲\n" + "".join((["文字も含めて画像だけで描く。"] if drawing.get("image_only") else [])
                                            + (["絵を紙面の端まで広げる。"] if drawing.get("bleed") else [])))
    checks = old.get("checks") or {}
    if _text(checks.get("script")).strip():
        parts.append("## 文章で確認すること\n" + checks["script"].strip())
    if _text(checks.get("image")).strip():
        parts.append("## 見た目で確認すること\n" + checks["image"].strip())
    for row in old.get("inputs") or []:
        need = "必須。渡されていなければ作成前に確認する。" if row.get("required") else "必要なときだけ渡される。"
        parts.append(f"## 資料を作るときに渡す情報: {_text(row.get('label'))}\n{_text(row.get('instructions'))}\n{need}")
    resources = []
    for row in old.get("references") or []:
        kind = "file" if row.get("kind") == "file" else "directory"
        notes = [_text(row.get("instructions")), _OLD_SCOPES.get(row.get("scope"), ""),
                 "見つからない場合は、このリソースを使わずに進めてよい。" if row.get("required") is False else ""]
        usage = "\n".join(n for n in notes if n)
        resources.append({"id": _text(row.get("id")), "name": _text(row.get("label")), "kind": kind,
                          "path": _text(row.get("path")), "usage": usage})
    attachments = [{"id": _text(m.get("id")), "name": _text(m.get("name")), "file": _text(m.get("file")),
                    "url": _text(m.get("url")), "image": m.get("image") is True,
                    "usage": "\n".join(n for n in (_text(m.get("usage")), _OLD_SCOPES.get(m.get("scope"), "")) if n),
                    "sha256": _text(m.get("sha256"))} for m in old.get("materials") or []]
    return {"schema_version": SCHEMA_VERSION, "id": old.get("id"), "brand_id": old.get("brand_id"),
            "revision": old.get("revision"), "based_on": None, "name": old.get("name"), "modes": old.get("modes"),
            "description": _text(old.get("description")), "structure": _text(structure),
            "instructions": "\n\n".join(parts), "resources": resources, "attachments": attachments}


def validate(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("schema_version") not in (1, 2, SCHEMA_VERSION):
        raise ValueError("テンプレートの保存形式を確認してください。")
    d = copy.deepcopy(document)
    if d["schema_version"] != SCHEMA_VERSION:
        try:
            d = _upgrade(d)
        except (AttributeError, TypeError, KeyError) as exc:
            raise ValueError("旧形式のテンプレートを移せません。保存形式を確認してください。") from exc
    d.setdefault("based_on", None)   # 由来を持つ前に保存した v3
    if set(d) != TOP_KEYS:
        raise ValueError("テンプレートの保存形式を確認してください。")

    def string(value, label, maximum=60000):
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError(f"{label}の文字数・形式を確認してください。")
    for key, label in (("id", "ID"), ("brand_id", "ブランド"), ("name", "テンプレート名"), ("description", "用途の説明"),
                       ("structure", "構成"), ("instructions", "追加指示")):
        string(d[key], label)
    if not d["name"].strip() or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", d["id"]):
        raise ValueError("テンプレート名と ID を確認してください。")
    if type(d["revision"]) is not int or d["revision"] < 0:
        raise ValueError("テンプレートの版を確認してください。")
    if d["based_on"] is not None and (type(d["based_on"]) is not int or d["based_on"] < 1):
        raise ValueError("元にした版を確認してください。")
    if not isinstance(d["modes"], list) or not d["modes"] or any(m not in ("presentation", "pitch") for m in d["modes"]) or len(set(d["modes"])) != len(d["modes"]):
        raise ValueError("使える形式を選んでください。")
    for kind, keys in (("resources", RESOURCE_KEYS), ("attachments", ATTACHMENT_KEYS)):
        rows = d[kind]
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("リソースは100件以内で設定してください。")
        for row in rows:
            if not isinstance(row, dict) or set(row) != keys:
                raise ValueError("リソースの項目を確認してください。")
            for key, value in row.items():
                if key == "image":
                    if type(value) is not bool:
                        raise ValueError("リソースの項目を確認してください。")
                else:
                    string(value, "リソース")
    ids = [r["id"] for r in d["resources"] + d["attachments"]]
    if any(not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", value) for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("リソースの ID は重複しない英数字にしてください。")
    names = [r["name"].strip() for r in d["resources"] + d["attachments"]]
    if any(not name for name in names):
        raise ValueError("リソースの名前を入力してください。")
    if len(names) != len(set(names)):
        raise ValueError("リソースの名前は重複しないようにしてください。")
    for row in d["resources"]:
        if row["kind"] not in ("file", "directory") or not row["path"].strip():
            raise ValueError("リソースの種類とパスを確認してください。")
        if not Path(row["path"]).expanduser().is_absolute():
            raise ValueError("リソースのパスは絶対パスまたは ~/ で指定してください。起動した場所には依存させません。")
    return d


# ---- 受け渡しのファイル（Markdown 1枚）----
# CLI に渡すファイル・画面の書き出し・ブランドの ZIP の中身を、すべてこの形にそろえる。
# 前付け（YAML）に名前・説明・使える形式・リソース、本文に「# 構成」「# 追加指示」。版・由来・ブランドは入れない。
FILE_FORMAT = "quick-slide-template"
FILE_VERSION = 1
_SECTIONS = ("# 構成", "# 追加指示")
_FRONT_KEYS = {"format", "version", "id", "name", "description", "modes", "resources"}


def to_markdown(document: Any) -> str:
    import yaml
    d = validate(document)
    if d["attachments"]:
        raise ValueError("添付ファイルがあるテンプレートは書き出せません。添付をリソース（ファイル）に置き換えてください。")
    for key in ("structure", "instructions"):
        if any(line.rstrip() in _SECTIONS for line in d[key].splitlines()):
            raise ValueError("構成・追加指示の中に「# 構成」「# 追加指示」だけの行があると書き出せません。見出しを「##」にしてください。")
    front = {"format": FILE_FORMAT, "version": FILE_VERSION, "id": d["id"], "name": d["name"], "description": d["description"],
             "modes": d["modes"], "resources": [{"name": r["name"], "kind": r["kind"], "path": r["path"], "usage": r["usage"]}
                                                for r in d["resources"]]}
    class Dumper(yaml.SafeDumper):
        pass
    # 複数行の文（使い方）は「|」でそのまま改行して書く（人が読んで直せるように）
    Dumper.add_representer(str, lambda dumper, value: dumper.represent_scalar(
        "tag:yaml.org,2002:str", value, style="|" if "\n" in value else None))
    head = yaml.dump(front, Dumper=Dumper, allow_unicode=True, sort_keys=False, width=1000)
    return f"---\n{head}---\n\n# 構成\n\n{d['structure'].strip()}\n\n# 追加指示\n\n{d['instructions'].strip()}\n"


def from_markdown(text: str, *, brand_id: str, template_id: str | None = None) -> dict[str, Any]:
    """受け渡しのファイルを、保存する文書（まだ版の無い v0）にする。他人から受け取ったファイルとして疑って読む。"""
    import yaml
    if not isinstance(text, str) or len(text) > 200_000:
        raise ValueError("テンプレートのファイルを確認してください（200,000 文字まで）。")
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---" or "---" not in [l.strip() for l in lines[1:]]:
        raise ValueError("テンプレートのファイルではありません（先頭の「---」で囲んだ前付けがありません）。")
    end = 1 + [l.strip() for l in lines[1:]].index("---")
    try:
        front = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError:
        raise ValueError("前付けの書き方を確認してください。") from None
    if not isinstance(front, dict) or front.get("format") != FILE_FORMAT:
        raise ValueError("Quick Slide のデザインテンプレートのファイルではありません。")
    if front.get("version") != FILE_VERSION:
        raise ValueError("このファイルの形式の版には対応していません。")
    unknown = set(front) - _FRONT_KEYS
    if unknown:
        raise ValueError("前付けに知らない項目があります: " + "・".join(sorted(map(str, unknown))))
    body = lines[end + 1:]
    marks = [i for i, l in enumerate(body) if l.rstrip() in _SECTIONS]
    if [body[i].rstrip() for i in marks] != list(_SECTIONS):
        raise ValueError("本文に「# 構成」「# 追加指示」の見出しを、この順に1つずつ書いてください。")
    structure = "\n".join(body[marks[0] + 1:marks[1]]).strip()
    instructions = "\n".join(body[marks[1] + 1:]).strip()
    resources = front.get("resources") or []
    if not isinstance(resources, list):
        raise ValueError("リソースの書き方を確認してください。")
    rows = []
    for i, r in enumerate(resources, 1):
        if not isinstance(r, dict) or set(r) - {"name", "kind", "path", "usage"}:
            raise ValueError("リソースの項目を確認してください（name・kind・path・usage）。")
        rows.append({"id": f"res-{i}", "name": _text(r.get("name")), "kind": _text(r.get("kind")) or "directory",
                     "path": _text(r.get("path")), "usage": _text(r.get("usage"))})
    tid = template_id or _text(front.get("id"))
    modes = front.get("modes") if front.get("modes") is not None else ["presentation"]
    return validate({"schema_version": SCHEMA_VERSION, "id": tid, "brand_id": brand_id, "revision": 0, "based_on": None,
                     "name": _text(front.get("name")), "description": _text(front.get("description")), "modes": modes,
                     "structure": structure, "instructions": instructions, "resources": rows, "attachments": []})


# 内容のハッシュを取るファイルの大きさの上限。これを超えるものは、存在だけ確かめてハッシュは取らない
MAX_HASH_BYTES = 8 * 1024 * 1024


def _hash_stream(stream: Any) -> tuple[str | None, bool]:
    """読んだ合計が `MAX_HASH_BYTES` を超えたらそこで止め、(None, True)。申告された大きさは信用しない。"""
    digest, total = hashlib.sha256(), 0
    while chunk := stream.read(65536):
        total += len(chunk)
        if total > MAX_HASH_BYTES:
            return None, True
        digest.update(chunk)
    return digest.hexdigest(), False


def _capped_sha256(path: Path) -> tuple[str | None, bool]:
    """(ハッシュ, 上限で省いたか)。通常のファイルだけを、上限まで読む。
    上限を超えるもの・通常でないものも、開いて1バイト読み、読めることを確かめる（読めなければ OSError）。"""
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_HASH_BYTES:
            stream.read(1)
            return None, True
        return _hash_stream(stream)


def resource_presence(resources: list[dict[str, Any]]) -> list[bool]:
    """指定した入口の有無だけ（stat のみ。中身は読まない）。"""
    out = []
    for row in resources:
        try:
            path = Path(row["path"]).expanduser()
            out.append(path.is_dir() if row["kind"] == "directory" else path.is_file())
        except (OSError, ValueError, RuntimeError):
            out.append(False)
    return out


def resource_report(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """指定した入口の有無と、フォルダの説明書きだけを見る。中身の選択はしない。
    ハッシュは `MAX_HASH_BYTES` までのファイルだけ取る（超えたら `hash_skipped`）。"""
    out = []
    for row in resources:
        exists, guide, sha, skipped = False, None, None, False
        try:
            path = Path(row["path"]).expanduser()
            if row["kind"] == "directory" and path.is_dir():
                # 再帰的にフォルダ全体をコピーしない。個々のファイルは準備工程で選ぶ。
                with os.scandir(path) as items:
                    next(items, None)
                exists = True
                guide = next((name for name in GUIDE_FILES if (path / name).is_file()), None)
                if guide:
                    sha, skipped = _capped_sha256(path / guide)
            elif row["kind"] == "file" and path.is_file():
                exists = True
                sha, skipped = _capped_sha256(path)
        except (OSError, ValueError, RuntimeError):
            exists, guide, sha, skipped = False, None, None, False   # 読めない入口は「無い」と同じに扱う
        out.append({"id": row["id"], "name": row["name"], "kind": row["kind"], "exists": exists,
                    "guide": guide, "sha256": sha, "hash_skipped": skipped, "selection": "not_run"})
    return out


def resource_table(d: dict[str, Any], *, paths: bool = True) -> str:
    """指示の中の名前と、実際の置き場の対応。台本係・準備・描画係に同じものを渡す。
    `paths=False` は、登録した実パスを含めない表（ID・名前・種類・使い方。モデルへ渡す用）。"""
    if not d["resources"] and not d["attachments"]:
        return ""
    lines = ["# リソース",
             "追加指示や構成に出てくる名前は、次の登録リソースを指す。名前から別の素材を推測しない。",
             "フォルダの場合は、中の説明書き（" + "・".join(GUIDE_FILES) + "）があれば先に読み、使い方に従って必要なファイルだけを選ぶ。",
             "指示で使うリソースが見つからない・読めない場合は止めて確認し、代わりの素材を作らない。"]
    for row in d["resources"]:
        lines.append(f"\n## {row['name']}\n- 種類: {'フォルダ' if row['kind'] == 'directory' else 'ファイル'}"
                     + (f"\n- パス: {row['path']}" if paths else f"\n- ID: {row['id']}")
                     + (f"\n- 使い方: {row['usage']}" if row["usage"].strip() else ""))
    for row in d["attachments"]:
        lines.append(f"\n## {row['name']}\n- 種類: 添付ファイル（{row['file']}）\n- 内容のハッシュ: {row['sha256']}"
                     + (f"\n- 使い方: {row['usage']}" if row["usage"].strip() else ""))
    return "\n".join(lines)


def writer_text(d: dict[str, Any], table: str) -> str:
    """台本係への指示（`writer.md`）。原文の構成・追加指示はそのまま、リソースの対応表だけを `table` で渡す。"""
    writer = ("# デザインテンプレートの指示\n\n## 台本の組み立て方\n"
              "承認済みの台本が渡された場合は、そのページ数・順番・主張・素材指定を優先して保持する。"
              "台本が未確定の場合は、今回の原稿から構成に沿って台本を組み立てる。")
    if d["structure"].strip():
        writer += "\n\n## 構成\n" + d["structure"]
    if d["instructions"].strip():
        writer += "\n\n## 追加指示（原文）\n" + d["instructions"]
    if table:
        writer += "\n\n" + table
    return writer


def preparation_text(d: dict[str, Any], table: str) -> str:
    """素材準備係への指示（`preparation.md`）。"""
    preparation = ("# リソースを読む・素材を選ぶ工程の指示\n"
                   "追加指示と構成を読み、使うよう指示されたリソースを実際に開く。"
                   "今回の原稿と指示から必要なファイルだけを選び、ページ・リソース名・選んだファイル・内容のハッシュ・選んだ理由を返す。"
                   "ここでは生成・外部への投稿・元データの変更をしない。")
    if d["instructions"].strip():
        preparation += "\n\n## 追加指示（原文）\n" + d["instructions"]
    if table:
        preparation += "\n\n" + table
    return preparation


def compile_document(document: Any, brand: dict[str, Any], *, mode: str) -> dict[str, Any]:
    """保存文書を唯一の元に、Quick Slide と直接実行が共用する入力を生成する。"""
    d = validate(document)
    if d["brand_id"] != brand["id"] or mode not in d["modes"]:
        raise ValueError("ブランドまたは使える形式が一致しません。")
    table = resource_table(d)
    instructions = d["instructions"].strip()
    writer, preparation = writer_text(d, table), preparation_text(d, table)
    # 描画係へは準備済みの素材と一緒に渡す。画像ツール自体にファイル探索を任せない。
    drawing = "\n\n".join(part for part in (instructions, table) if part)
    import yaml
    style = {"name": d["id"], "description": d["description"], "text": drawing or "原稿の内容を読みやすく描く。",
             "bleed": False, "one_page": False}
    theme = palette.engine_theme(palette.snapshot_colors(brand["config"]["colors"]))
    engine = {"mode": mode, "skill": "image-slide" if mode == "pitch" else "html-slide",
              "style_file": "style.yaml" if mode == "pitch" else None, "theme_file": "theme.yaml",
              "brand_revision_id": brand.get("current_revision_id"),
              "logo": copy.deepcopy(brand["config"].get("logo")), "brand_assets": copy.deepcopy(brand.get("assets", [])),
              "footer": copy.deepcopy(brand["config"].get("footer")), "format": copy.deepcopy(brand["config"].get("format")),
              "fonts": copy.deepcopy(brand["config"].get("fonts"))}
    notes = ["リソースの有無の確認は、素材の選択や指示の遵守を保証しません。素材準備・台本作成・描画は未実行です。"]
    if mode == "presentation":
        notes.append("HTML では、追加指示とリソースを台本係へ渡します。画像スライド向けの指示（画風など）は、HTML の表現にできる範囲で反映します。")
    contract = {"compiler_version": COMPILER_VERSION, "template": d, "brand": brand, "mode": mode,
                "writer": writer, "preparation": preparation, "drawing": drawing, "engine": engine}
    digest = hashlib.sha256(json.dumps(contract, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    report = resource_report(d["resources"])
    return {"compiler_version": COMPILER_VERSION, "contract_sha256": digest, "template_revision": d["revision"], "mode": mode,
            "stage": "instructions_compiled", "render_ready": False,
            "can_prepare": all(r["exists"] for r in report),
            "files": {"template.json": json.dumps(d, ensure_ascii=False, indent=2),
                      "brand.json": json.dumps(brand, ensure_ascii=False, indent=2),
                      "writer.md": writer, "preparation.md": preparation,
                      "style.yaml": yaml.safe_dump(style, allow_unicode=True, sort_keys=False) if mode == "pitch" else "",
                      "theme.yaml": palette.theme_yaml(theme),
                      "engine-input.json": json.dumps(engine, ensure_ascii=False, indent=2)},
            "resources": report, "generation_executed": False, "notes": notes}
