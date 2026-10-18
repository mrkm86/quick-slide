"""素材のカタログ（§3.4）。提供者（provider）が資源ごとに一覧と取得を持ち、サーバが世代内で安定した参照 ID（`ref-N`）を発行する。

提供者は通常のフォルダ・ファイルの `FolderProvider` だけ（スキルの提供者は作らない。docs/adr/0031-static-template-resources.md）。
どの提供者でも、準備係・台本係へ渡すのは「ID・資源名・役割・説明」だけ。実パス・コマンドは渡さない（返させない）。
取得前の ID は候補、取得とコピーの後に実体のハッシュが付いて初めて「準備済み参照」になる。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .errors import PreparationStopped

# 役割（キャラクター・持込画像・構図の見本・全ページ共通）。ページ限定の参照を共通参照へ昇格しない
ROLES = ("character", "image", "layout", "common")
ROLE_WORDS = {"character": "キャラクター（ページに登場させる絵）", "image": "持込画像（そのページの画像として使う絵）",
              "layout": "構図の見本", "common": "全ページ共通の参照"}
# 6a のエンジン入力に変換できる役割（モード別）。HTML への変換と、構図の見本・共通参照は後の PR
SUPPORTED_ROLES = {"pitch": ("character", "image"), "presentation": ()}
CATALOG_SCHEMA = 1


@dataclass(frozen=True)
class CatalogItem:
    resource_id: str
    item_id: str
    description: str
    media_type: str
    size: int | None = None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class FetchedFile:
    """取得した 1 ファイルの在りか（コピーする前）。`rel` は `root` の下の相対パス。"""
    file_id: str
    root: Path
    rel: str
    media_type: str
    size: int | None = None
    width: int | None = None
    height: int | None = None


class CatalogProvider(Protocol):
    resource_id: str

    def items(self) -> list[CatalogItem]: ...

    def fetch(self, item_id: str) -> list[FetchedFile]: ...


class FolderProvider:
    """通常のフォルダ・ファイル。入口の確認で作った目録（`intake.json` の items）から、そのままの一覧と取得を返す。"""

    def __init__(self, resource_id: str, root: Path, items: list[dict[str, Any]]) -> None:
        self.resource_id = resource_id
        self.root = root
        self._items = {i["item_id"]: i for i in items}

    def items(self) -> list[CatalogItem]:
        return [CatalogItem(self.resource_id, i["item_id"], i["description"], i["media_type"], i.get("size"),
                            i.get("width"), i.get("height")) for i in self._items.values()]

    def fetch(self, item_id: str) -> list[FetchedFile]:
        item = self._items.get(item_id)
        if item is None:
            raise PreparationStopped("一覧にない素材は取得しません。", "unknown_item")
        return [FetchedFile("main", self.root, item["rel"], item["media_type"], item.get("size"), item.get("width"),
                            item.get("height"))]


def issue_ids(provider_items: list[CatalogItem]) -> list[tuple[str, CatalogItem]]:
    """世代の中で安定な参照 ID を発行する（入力の並び＝資源の登録順 → 一覧内の順。同じ入力なら同じ ID）。"""
    return [(f"ref-{n}", item) for n, item in enumerate(provider_items, 1)]


@dataclass(frozen=True)
class Reference:
    id: str
    resource_id: str
    item_id: str
    roles: tuple[str, ...]
    description: str
    media_type: str


class Catalog:
    """世代のカタログ。`issued` は発行した全 ID、`references` は準備係が候補にした ID（台本係が選べる）。"""

    def __init__(self, *, preparation_id: str, generation: int, mode: str, issued: list[dict[str, Any]],
                 references: list[Reference], notes: list[str], unsupported: list[dict[str, Any]],
                 resource_names: dict[str, str]) -> None:
        self.preparation_id = preparation_id
        self.generation = generation
        self.mode = mode
        self.issued = issued
        self.references = references
        self.notes = notes
        self.unsupported = unsupported
        self.resource_names = resource_names
        self.by_id = {r.id: r for r in references}
        self.issued_ids = {i["id"] for i in issued}

    def to_json(self) -> dict[str, Any]:
        return {"schema_version": CATALOG_SCHEMA, "preparation_id": self.preparation_id, "generation": self.generation,
                "mode": self.mode, "issued": self.issued, "resource_names": self.resource_names,
                "references": [{"id": r.id, "resource_id": r.resource_id, "item_id": r.item_id, "roles": list(r.roles),
                                "description": r.description, "media_type": r.media_type} for r in self.references],
                "notes": self.notes, "unsupported": self.unsupported}

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Catalog":
        try:
            if raw["schema_version"] != CATALOG_SCHEMA:
                raise ValueError("schema")
            refs = [Reference(r["id"], r["resource_id"], r["item_id"], tuple(r["roles"]), r["description"], r["media_type"])
                    for r in raw["references"]]
            return cls(preparation_id=raw["preparation_id"], generation=raw["generation"], mode=raw["mode"],
                       issued=raw["issued"], references=refs, notes=raw["notes"], unsupported=raw["unsupported"],
                       resource_names=raw["resource_names"])
        except (KeyError, TypeError, ValueError):
            raise PreparationStopped("素材のカタログの控えを読めませんでした。", "catalog_corrupt") from None

    def derive(self, *, preparation_id: str, generation: int) -> "Catalog":
        """同じ中身（同じ ID）で、別の世代・準備 ID のカタログにする（直し・再試行で引き継ぐ。ID は変わらない）。"""
        return Catalog(preparation_id=preparation_id, generation=generation, mode=self.mode, issued=self.issued,
                       references=self.references, notes=self.notes, unsupported=self.unsupported,
                       resource_names=self.resource_names)

    def content_sha256(self) -> str:
        """世代・準備 ID を除いた中身のハッシュ（台本が指す「カタログ」の同一性。派生した世代でも変わらない）。"""
        body = self.to_json()
        body.pop("preparation_id")
        body.pop("generation")
        return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def writer_view(self) -> dict[str, Any]:
        """台本係に見せる形（ID・資源名・役割・説明だけ。パス・item_id は出さない）。"""
        return {"generation": self.generation,
                "references": [{"id": r.id, "resource": self.resource_names.get(r.resource_id, r.resource_id),
                                "roles": list(r.roles), "description": r.description} for r in self.references],
                "notes": list(self.notes)}
