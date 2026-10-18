"""選んだ参照のファイルだけを世代のフォルダへコピーし、内容のハッシュで名前を付けて控える（§3.3 の「素材解決」）。

取得の直前に、一覧に出したときの大きさ・画素と同じかを確かめる（別の画像に差し替わっていたら止める）。
読んでいる間の変更は `Reader.copy` が検知して止める。全件の再帰コピーはしない。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .catalog import Catalog, CatalogProvider
from .errors import PreparationStopped
from .files import sha256_file
from .reading import Reader

EXT_FOR = {"image/png": ".png", "image/jpeg": ".jpg"}
ASSETS_DIR = "assets"


def asset_total(assets: dict[str, dict[str, Any]]) -> int:
    seen: dict[str, int] = {}
    for a in assets.values():
        for f in a["files"]:
            seen[f["sha256"]] = f["bytes"]
    return sum(seen.values())


def verify_assets(gen_dir: Path, assets: dict[str, dict[str, Any]]) -> bool:
    """コピー済みのファイルが、控えたハッシュのとおり残っているか。"""
    return all(sha256_file(gen_dir / f["path"]) == f["sha256"] for a in assets.values() for f in a["files"])


def resolve_assets(ref_ids: list[str], *, catalog: Catalog, providers: dict[str, CatalogProvider], reader: Reader,
                   gen_dir: Path, existing: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """`ref_ids`（カタログの候補の ID）の素材を揃える。すでにコピー済み（ハッシュが合う）ものは取り直さない。
    戻り値は `existing` に新しい分を足した {ref_id: {"files": [{file_id, path, sha256, bytes, media_type, width, height}]}}。"""
    out = dict(existing)
    for rid in dict.fromkeys(ref_ids):
        ref = catalog.by_id.get(rid)
        if ref is None:
            raise PreparationStopped(f"カタログにない参照 {rid} は取得しません。", "unknown_reference")
        have = out.get(rid)
        if have is not None and verify_assets(gen_dir, {rid: have}):
            continue
        provider = providers.get(ref.resource_id)
        if provider is None:
            raise PreparationStopped("素材の置き場（リソース）が、登録から外れています。", "resource_missing")
        files = []
        for f in provider.fetch(ref.item_id):
            info, sha, total = reader.copy_image(f.root, f.rel, gen_dir / ASSETS_DIR, expected=(f.size, f.width, f.height))
            ext = EXT_FOR[info.media_type]
            files.append({"file_id": f.file_id, "path": f"{ASSETS_DIR}/{sha}{ext}", "sha256": sha,
                          "bytes": total, "media_type": info.media_type, "width": info.width, "height": info.height})
        out[rid] = {"files": files}
        if asset_total(out) > reader.policy.selected_total_bytes:
            raise PreparationStopped(f"選んだ画像の合計が上限（{reader.policy.selected_total_bytes} バイト）を超えます。",
                                     "limit_exceeded", {"limit": reader.policy.selected_total_bytes, "what": "selected_total"})
    return out


def link_or_copy(src: Path, dst: Path) -> None:
    """派生した世代へ、コピー済みの素材を引き継ぐ（ハードリンク。できなければコピー）。"""
    import shutil

    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
