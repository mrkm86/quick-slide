"""旧版（コンパイラ4・保存形式3）の再現。新しい処理とは混ぜない（docs/design-template-static-resources.md §3.2）。

添付の専用欄（attachments）を持つ保存形式3の文書と、それを元にしたコンパイラ4の指示・ハッシュを、当時のまま作り直す。
使うのは、コンパイラ4で受け付けた回の控え（ブランド・テンプレートの文書）を再コンパイルするときだけ。
新しい `design_templates.validate`・`writer_text` で旧版を再現してはいけない（結果が変わる）。
保存済みの行・控えは読むだけで、この中から書き換えない。
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import palette
from .design_templates import GUIDE_FILES, resource_report

COMPILER_VERSION = 4
_TOP_KEYS = {"schema_version", "id", "brand_id", "revision", "based_on", "name", "modes", "description",
             "structure", "instructions", "resources", "attachments"}
_RESOURCE_KEYS = {"id", "name", "kind", "path", "usage"}
_ATTACHMENT_KEYS = {"id", "name", "file", "url", "image", "usage", "sha256"}


def validate_v3(document: Any) -> dict[str, Any]:
    """保存形式3の文書の検査（当時のまま）。形式1・2は扱わない（保存した文書は常に3だった）。"""
    if not isinstance(document, dict) or document.get("schema_version") != 3:
        raise ValueError("テンプレートの保存形式を確認してください。")
    d = copy.deepcopy(document)
    d.setdefault("based_on", None)
    if set(d) != _TOP_KEYS:
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
    for kind, keys in (("resources", _RESOURCE_KEYS), ("attachments", _ATTACHMENT_KEYS)):
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


def resource_table(d: dict[str, Any], *, paths: bool = True) -> str:
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
    """保存形式3の文書から、コンパイラ4の結果を当時のまま作る。"""
    d = validate_v3(document)
    if d["brand_id"] != brand["id"] or mode not in d["modes"]:
        raise ValueError("ブランドまたは使える形式が一致しません。")
    table = resource_table(d)
    instructions = d["instructions"].strip()
    writer, preparation = writer_text(d, table), preparation_text(d, table)
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
