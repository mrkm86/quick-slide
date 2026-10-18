"""旧い形式（保存形式3・コンパイラ4。添付の専用欄を持つ）の、保存済みデータの固定の見本。

変換や新しい版のアルゴリズムの試験が、**保存したバイトとハッシュを変えない**ことを確かめるための入力。
ハッシュの期待値は、添付を廃止する前のコード（保存形式3・コンパイラ4）が出した値をそのまま固定したもの（ここで再計算しない）。
旧い行は、保存の API を通さず（新しい形式で書き直されてしまうので）SQL で直接置く。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from quick_slide.store.base import dump_json

# 旧コードで試したブランド（固定の値。プリセットの設定を写したもの）
BRAND_ID = "b-legacy"
BRAND = {"id": BRAND_ID, "current_revision_id": "br-legacy", "assets": [], "config": {
    "colors": {"accent": "#0DB4F2", "gray": ["#1A1A1A", "#333333", "#666666", "#8C8C8C", "#B2B2B2", "#D6D6D6", "#EDEDED", "#F7F7F7"],
               "primary": "#0DB4F2"},
    "fonts": {"body": {"fallback_families": ["Hiragino Sans", "sans-serif"], "requested_family": "Noto Sans JP", "weight": 400},
              "heading": {"fallback_families": ["Hiragino Sans", "sans-serif"], "requested_family": "Noto Sans JP", "weight": 700}},
    "footer": {"style": {"position": "footer_left"}, "text": ""}, "format": {"page_numbers": False, "watermark": ""},
    "logo": {"placement": {"corner": "bottom-right"}, "variant": "none"}, "schema_version": 1}}

ATTACHMENT = {"id": "m1", "name": "旧添付", "file": "memo.md", "url": "/preview/files/m1", "image": False,
              "usage": "用語の見本", "sha256": "a" * 64}

# 旧コード（コンパイラ4）の出力: (空の添付, 中身のある添付) × (presentation, pitch)
GOLDEN_CONTRACT = {
    ("empty", "presentation"): "4e4c70bc2500e03f51cd3bfdd340d6efc0af2c0d89237a86a1e49663bc463e71",
    ("empty", "pitch"): "9ae77949f6779b1764b5e860b28d5deb562dbb4f09bb906cc183fde63d42766c",
    ("full", "presentation"): "5a1d752d194ec6140313fd9210e9fa4d6b1373f0709fa7a0d4347320eae846a1",
    ("full", "pitch"): "d51cb5419416ec8c1382a8cf146db6838dcec7255e971705769e52a23b69a9f4",
}
GOLDEN_WRITER_SHA = {
    "empty": "a5c3ea9b1e186af17d846cce92d5839e8574ab589142668ed6cf6516428aae1b",
    "full": "a1c22cfb0726d7ed062a0813d5676716ef255c3f960481a91c898bad7d369ff0",
}
GOLDEN_PREPARATION_SHA = {
    "empty": "74470da9fdd87bd0fc0d73d7b76a93ecb79c2db3bff219fb2a09b2c341531fa5",
    "full": "243b538b492fa1c47017f55ed6f0d042ed745b33a66de3660a956fb7d0f10c1f",
}
# 旧コードの `content_sha256`（改訂の行に保存していたハッシュ）
GOLDEN_ROW_SHA = {
    "empty": "3b93dc1abce69bf7fbf3c362dc542cf18e08032578ff922efba83a3f925eb2b1",
    "full": "7ea165b4d64b7f88530f1cf3a349a112f5a24644a7a43858456929c47079a90a",
}


def legacy_doc(kind: str = "empty", *, template_id: str = "t-legacy", brand_id: str = BRAND_ID, name: str = "旧テンプレート",
               revision: int = 2, based_on: int | None = 1, kit_path: str = "/nonexistent-legacy-kit") -> dict[str, Any]:
    """保存形式3の文書。`kind` は "empty"（添付なし）か "full"（中身のある添付が1つ）。"""
    return {"schema_version": 3, "id": template_id, "brand_id": brand_id, "revision": revision, "based_on": based_on,
            "name": name, "modes": ["presentation", "pitch"], "description": "旧形式の説明",
            "structure": "## 1. 結論\n先に結論を書く", "instructions": "図は少なめにする",
            "resources": [{"id": "kit", "name": "画像集", "kind": "directory", "path": kit_path,
                           "usage": "必要な絵だけ選ぶ"}],
            "attachments": [dict(ATTACHMENT)] if kind == "full" else []}


def old_content_sha256(document: dict[str, Any]) -> str:
    """旧コードの内容ハッシュ（添付も数えた4項目）。新しい算法とは別物。"""
    files = sorted(({k: r[k] for k in ("name", "kind", "path", "usage")} for r in document["resources"]), key=dump_json)
    attachments = sorted(({k: r[k] for k in ("name", "sha256", "usage")} for r in document["attachments"]), key=dump_json)
    sections = {"基本": {"name": document["name"], "description": document["description"], "modes": sorted(document["modes"])},
                "構成": document["structure"], "追加指示": document["instructions"],
                "リソース": {"files": files, "attachments": attachments}}
    return hashlib.sha256(dump_json(sections).encode("utf-8")).hexdigest()


def insert_legacy_rows(execute, *, brand_id: str, brand_revision_id: str, template_id: str, revision_id: str, kind: str,
                       name: str, kit_path: str, now: str) -> dict[str, str]:
    """旧い形式の行を、保存の API を通さず直接置く（1 版・1 改訂）。`execute(sql, params)` は SQL を実行する関数。"""
    document = legacy_doc(kind, template_id=template_id, brand_id=brand_id, name=name, revision=1, based_on=None,
                          kit_path=kit_path)
    raw, sha = dump_json(document), old_content_sha256(document)
    execute("INSERT INTO design_templates(id,brand_id,current_revision_id,created_at,updated_at) VALUES(?,?,?,?,?)",
            (template_id, brand_id, revision_id, now, now))
    execute("INSERT INTO design_template_revisions(id,template_id,revision,save_sequence,document_json,content_sha256,"
            "based_on_revision_id,brand_revision_id,origin_runtime,conversation_hash,save_kind,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (revision_id, template_id, 1, 1, raw, sha, None, brand_revision_id, None, None, "create", now))
    return {"template_id": template_id, "revision_id": revision_id, "document_json": raw, "content_sha256": sha, "name": name}


def insert_legacy_template(store, brand_id: str, kind: str = "empty", *, name: str = "旧テンプレート",
                           kit_path: str = "/nonexistent-legacy-kit") -> dict[str, str]:
    """旧い形式の行をストアへ直接置く。置いたバイトとハッシュを返す。"""
    with store.transaction():
        return insert_legacy_rows(store._exec, brand_id=brand_id, brand_revision_id=store.brand(brand_id)["current_revision_id"],
                                  template_id=store.new_id(), revision_id=store.new_id(), kind=kind, name=name, kit_path=kit_path,
                                  now=store.now())


def stored_row(store, revision_id: str) -> tuple[str, str]:
    """改訂の行に保存されているままの (document_json, content_sha256)。"""
    row = store.conn.execute("SELECT document_json,content_sha256 FROM design_template_revisions WHERE id=?",
                             (revision_id,)).fetchone()
    return row[0], row[1]


def all_rows(store) -> list[tuple]:
    """全改訂の行（SQL の中身そのまま）。書き換えがないことを前後で比べる。"""
    return [tuple(r) for r in store.conn.execute("SELECT * FROM design_template_revisions ORDER BY rowid")]


def snapshot_bytes(store) -> list[tuple]:
    return [tuple(r) for r in store.conn.execute("SELECT id,snapshot_sha256,snapshot_json FROM template_snapshots ORDER BY id")]


def sha(obj: Any) -> str:
    return hashlib.sha256(dump_json(obj).encode("utf-8")).hexdigest()



def repoint_to_legacy_snapshot(store, run_id: str, template: dict[str, str], *, compiler_version: int = 4) -> dict[str, str]:
    """通常の投入で出来た回の控えを、旧い控え（文書は保存形式3のまま・コンパイラ4・旧いハッシュ）に差し替える。
    控えの行・回の引数・デッキの参照を SQL で直接置く（旧い版が書いた状態を作る）。置いた控えの参照を返す。"""
    run = store.run(run_id)
    snapshot = store.template_snapshot(run["args"]["template_snapshot"])
    document = json.loads(template["document_json"])
    snapshot["compiler_version"] = compiler_version
    snapshot["template"] = {"id": template["template_id"], "revision": 1, "revision_id": template["revision_id"],
                            "document": document, "content_sha256": template["content_sha256"]}
    digest = sha(snapshot)
    snapshot_id = store.new_id()
    store.conn.execute("INSERT INTO template_snapshots(id,snapshot_sha256,snapshot_json,created_at) VALUES(?,?,?,?)",
                       (snapshot_id, digest, dump_json(snapshot), store.now()))
    ref = {"snapshot_id": snapshot_id, "snapshot_sha256": digest}
    store.conn.execute("UPDATE runs SET args_json=? WHERE id=?", (dump_json({**run["args"], "template_snapshot": ref}), run_id))
    store.conn.execute("UPDATE decks SET template_snapshot_id=? WHERE id=?", (snapshot_id, run["deck_id"]))
    store.conn.commit()
    return ref
