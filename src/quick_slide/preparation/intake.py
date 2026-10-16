"""入口の確認と通常素材の読取（§3.1 の「入口の確認」）。登録した全パスの有無・種類・読取可否と、説明書き・目録・テキストを読む。

LLM は呼ばない。結果（`intake.json`）は不変の控えで、世代の入力 digest の元になる。パスそのものは控えに残さない
（準備係・台本係にも渡さない。置き場は回の控えのスナップショットから引く）。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from .errors import SKILL_UNSUPPORTED_MESSAGE, PreparationStopped
from .policy import Policy
from .reading import (
    GUIDE_NAMES,
    IMAGE_MEDIA,
    JSON_EXTS,
    SKILL_MARKERS,
    SUPPORTED_WORDS,
    TEXT_EXTS,
    YAML_EXTS,
    Reader,
    decode_text,
    label,
    parse_json_safe,
    parse_yaml_safe,
)

INTAKE_SCHEMA = 1


class TextBudget:
    def __init__(self, total: int) -> None:
        self.total = total
        self.left = total

    def take(self, n: int, what: str) -> None:
        if n > self.left:
            raise PreparationStopped(f"{what}を読むと、テキストの読取量の上限（{self.total} バイト）を超えます。",
                                     "limit_exceeded", {"limit": self.total, "what": "text_total"})
        self.left -= n


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def item_id_for(rel: str) -> str:
    """フォルダの中の画像の一覧内の ID（相対パスから決まる。同じ入力で変わらない）。"""
    return "f-" + hashlib.sha256(rel.encode("utf-8")).hexdigest()[:12]


def _expand(row: dict[str, Any]) -> Path:
    try:
        return Path(row["path"]).expanduser()
    except (RuntimeError, ValueError):
        return Path("/nonexistent-" + row["id"])


def _exists(row: dict[str, Any]) -> bool:
    path = _expand(row)
    try:
        return path.is_dir() if row["kind"] == "directory" else path.is_file()
    except (OSError, ValueError):
        return False


def check_entrances(doc: dict[str, Any]) -> None:
    """登録した全パスの入口を確かめる。一つでも欠けたら止める（任意扱いで無視しない）。スキルのフォルダは未対応で止める。"""
    attachments = doc.get("attachments") or []
    if attachments:
        names = "・".join(f"「{a['name']}」" for a in attachments)
        raise PreparationStopped(f"このテンプレートには旧形式の添付ファイル（{names}）があります。添付ファイルの読み取りは"
                                 "まだ使えないため、素材の準備を止めました。添付をリソース（ファイル）に置き換えてください。",
                                 "attachment_unsupported", {"names": [a["name"] for a in attachments]})
    resources = doc.get("resources") or []
    missing = [r["name"] for r in resources if not _exists(r)]
    if missing:
        raise PreparationStopped(
            "登録したリソースが見つかりません: " + "・".join(f"「{n}」" for n in missing)
            + "。パスを直すか、リソースから外してから、もう一度頼んでください（見つからないものを無視して進めません）。",
            "resource_missing", {"names": missing})
    skills = [r["name"] for r in resources if r["kind"] == "directory"
              and any(os.path.lexists(_expand(r) / m) for m in SKILL_MARKERS)]
    if skills:
        raise PreparationStopped(SKILL_UNSUPPORTED_MESSAGE.format(name="」「".join(skills)), "skill_unsupported",
                                 {"names": skills})


def _item(rel: str, info, size: int) -> dict[str, Any]:
    return {"item_id": item_id_for(rel), "rel": rel, "description": rel, "media_type": info.media_type, "size": size,
            "width": info.width, "height": info.height}


def _read_folder(row: dict[str, Any], reader: Reader, budget: TextBudget) -> dict[str, Any]:
    name, root, policy = row["name"], _expand(row), reader.policy
    entries = reader.inventory(root, name)
    guide = None
    for g in GUIDE_NAMES:   # 最初に見つかった 1 つだけ（README.md が README.txt に先立つ）
        if os.path.lexists(root / g):
            what = f"リソース「{name}」の説明書き（{g}）"
            data, sha = reader.read_bytes(root, g, policy.guide_bytes, what)
            text = decode_text(data, what)
            budget.take(len(data), what)
            guide = {"name": g, "sha256": sha, "bytes": len(data), "text": text}
            break
    items: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for e in entries:
        ext = os.path.splitext(e.rel)[1].lower()
        if ext not in IMAGE_MEDIA:
            continue
        if e.kind != "file":
            excluded.append({"rel": e.rel, "reason": "通常のファイルではない（シンボリックリンク等は使えない）"})
            continue
        try:
            info, size = reader.sniff(root, e.rel)
        except PreparationStopped as exc:
            if exc.code not in ("limit_exceeded", "bad_image"):
                raise
            excluded.append({"rel": e.rel, "reason": exc.message})
            continue
        if info.media_type != IMAGE_MEDIA[ext]:
            excluded.append({"rel": e.rel, "reason": "拡張子と画像の形式が合わない"})
            continue
        items.append(_item(e.rel, info, size))
    return {"form": "folder", "guide": guide, "readings": [],
            "entries": [{"rel": e.rel, "kind": e.kind, "size": e.size} for e in entries], "items": items,
            "excluded": excluded}


def _read_file(row: dict[str, Any], reader: Reader, budget: TextBudget) -> dict[str, Any]:
    name, path, policy = row["name"], _expand(row), reader.policy
    ext = path.suffix.lower()
    root, rel = path.parent, path.name
    what = f"リソース「{name}」"
    base = {"form": "file", "guide": None, "entries": [], "items": [], "excluded": [], "readings": []}
    if ext in IMAGE_MEDIA:
        try:
            info, size = reader.sniff(root, rel)
        except PreparationStopped as exc:   # 登録したファイルそのものが使えない: 理由を言って止める
            raise PreparationStopped(f"{what}: {exc.message}", exc.code, exc.details) from None
        if info.media_type != IMAGE_MEDIA[ext]:
            raise PreparationStopped(f"{what}は拡張子と画像の形式が合いません。", "bad_image")
        base["items"].append(_item(rel, info, size))
        return base
    if ext in TEXT_EXTS | JSON_EXTS | YAML_EXTS:
        kind = "json" if ext in JSON_EXTS else "yaml" if ext in YAML_EXTS else "text"
        try:
            data, sha = reader.read_bytes(root, rel, min(budget.left, policy.text_total_bytes), what)
        except PreparationStopped as exc:
            if exc.code == "limit_exceeded":   # 残りの読取量を超えた（テキストの合計の上限）
                raise PreparationStopped(f"{what}を読むと、テキストの読取量の上限（{budget.total} バイト）を超えます。",
                                         "limit_exceeded", {"limit": budget.total, "what": "text_total"}) from None
            raise
        text = decode_text(data, what)
        budget.take(len(data), what)
        if kind == "json":
            parse_json_safe(text, what)
        elif kind == "yaml":
            parse_yaml_safe(text, what)
        base["readings"].append({"rel": rel, "type": kind, "sha256": sha, "bytes": len(data), "text": text})
        return base
    raise PreparationStopped(f"{what}は対応していない形式（{ext or '拡張子なし'}）です。対応しているのは {SUPPORTED_WORDS} です。"
                             "PDF などは、対応した形式に変換してから登録してください。", "unsupported_format")


def read_resources(doc: dict[str, Any], *, reader: Reader, preparation_md: str, mode: str) -> dict[str, Any]:
    """入口の確認と読取。戻り値は `intake.json` の中身（決まった形・時刻を含まない）。"""
    check_entrances(doc)
    budget = TextBudget(reader.policy.text_total_bytes)
    out = []
    for row in doc.get("resources") or []:
        read = _read_folder(row, reader, budget) if row["kind"] == "directory" else _read_file(row, reader, budget)
        out.append({"id": row["id"], "name": row["name"], "kind": row["kind"], "usage": row["usage"], **read})
    return {"schema_version": INTAKE_SCHEMA, "mode": mode, "resources": out,
            "preparation_md_sha256": sha_text(preparation_md), "template_sha256": sha_text(_canon(doc)),
            "text_bytes": budget.total - budget.left}


def _canon(doc: dict[str, Any]) -> str:
    import json

    return json.dumps(doc, ensure_ascii=False, sort_keys=True)


__all__ = ["read_resources", "check_entrances", "item_id_for", "label", "TextBudget"]
