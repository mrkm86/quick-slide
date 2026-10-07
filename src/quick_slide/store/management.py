"""デッキ管理の状態変更: ピン留め / アーカイブ / 複製 / ごみ箱へ移動 / 復元 / 完全削除。

`DecksMixin` の書き込みメソッド（`set_pinned` / `set_archived` / `soft_delete_deck`）はそのまま使い、
ここで **events と対にする**（状態更新とイベントを同じトランザクションで）。

削除の方式:
1. `decks.deleted_at` と `deletion_tasks` 行を同時 commit（即時非表示）
2. 復元 manifest を `.trash/<deletion-id>/manifest.json` に**確定してから**
3. **専有ファイルだけ**を `.trash/<deletion-id>/...` へ退避する（`os.replace`。同じファイルシステム内の移動で、
   1 バイトも複製・削除しない）。他デッキ・ブランドから参照されている asset は触らない
4. 退避した asset 行を `trashed` にし、タスクを `trashed` にして `deck_trashed` イベントを書く

復元は manifest どおりに戻し、sha256 を検証してから `deleted_at=NULL` にする。
完全削除（`purge_deck_from_trash`）はごみ箱の中身を**本当に消す**：そのデッキの行を 1 つ残らず消し、
退避したファイルとデッキの置き場（`decks/<id>/`）まで消す。共有している asset は行もファイルも触らない。

P1 のデッキはページを持たないので、専有する asset は無い。P2 で台本・描いた回が入ると、
`views._ASSET_REFS_CTE` と `_PURGE_DELETES` にその表を足す（足し忘れは外部キーの検査が止める）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from ..errors import NotFoundError, StateError, ValidationError
from .base import StoreBase

MANIFEST_SCHEMA_VERSION = 1


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json_atomic(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    data = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _dir_bytes(path: Path) -> int:
    """そのディレクトリの中の**実ファイル**の合計。無ければ 0。"""
    if not path.is_dir():
        return 0
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue  # 数えられないものは 0 として扱う（見積りのために失敗しない）
    return total


def _rmdir_if_empty(path: Path) -> None:
    try:
        os.rmdir(path)
    except OSError:
        pass


class DeckManagementMixin(StoreBase):
    # --- ピン / アーカイブ（イベント付き） ---
    def pin_deck(self, deck_id: str, pinned: bool, *, expected_lock_version: int | None = None) -> dict[str, Any]:
        with self.transaction():
            self.set_pinned(deck_id, bool(pinned), expected_lock_version=expected_lock_version)
            self.append_event("deck_updated", {"deck_id": deck_id, "fields": ["pinned"], "pinned": bool(pinned)}, deck_id=deck_id)
            return self.deck(deck_id)

    def archive_deck(self, deck_id: str, archived: bool, *, expected_lock_version: int | None = None) -> dict[str, Any]:
        with self.transaction():
            self.set_archived(deck_id, bool(archived), expected_lock_version=expected_lock_version)
            self.append_event("deck_updated", {"deck_id": deck_id, "fields": ["archived_at"], "archived": bool(archived)},
                              deck_id=deck_id)
            return self.deck(deck_id)

    # --- 複製 ---
    def duplicate_deck(self, deck_id: str, *, title: str | None = None) -> dict[str, Any]:
        """新デッキ（draft / 非ピン / 非アーカイブ、`origin_deck_id`）を作る。モード・ブランド・スタイル・
        メタデータ・タグを写す。ページ（台本の版・描いた回）を写すのは P2。"""
        if title is not None and (not isinstance(title, str) or not title.strip()):
            raise ValidationError("decks.title は空にできない")
        with self.transaction():
            src = self._get("decks", deck_id)
            if src["deleted_at"] is not None:
                raise NotFoundError("削除済みデッキは複製できません")
            new_title = title if title is not None else f"{src['title']}のコピー"
            new = self.create_deck(
                new_title,
                mode=src["mode"],
                brand_revision_id=src["brand_revision_id"],
                style_revision_id=src["style_revision_id"],
                subtitle=src["subtitle"],
                purpose=src["purpose"],
                audience=src["audience"],
                origin_deck_id=deck_id,
            )
            new_id = new["id"]
            for t in self.deck_tags(deck_id):
                self._exec("INSERT OR IGNORE INTO deck_tags(deck_id,tag_id) VALUES(?,?)", (new_id, t["id"]))
            self.append_event("deck_duplicated", {"deck_id": new_id, "origin_deck_id": deck_id, "pages": 0}, deck_id=new_id)
            return self.deck(new_id)

    # --- ごみ箱へ移動 ---
    def delete_deck_to_trash(self, deck_id: str) -> dict[str, Any]:
        """非表示 → manifest 確定 → 専有ファイルだけ退避 → 記録。戻り値に何を動かし何を残したかを実数で返す。"""
        deck = self._get("decks", deck_id)
        if deck["deleted_at"] is not None:
            raise StateError("既に削除済みです")
        usage = self.deck_asset_usage(deck_id)
        tags = [t["name"] for t in self.deck_tags(deck_id)]
        tid = self.new_id()
        manifest_rel = f".trash/{tid}/manifest.json"
        trash_dir = self.root.trash_dir / tid
        exclusive = [a for a in usage if not a["shared"]]
        shared = [a for a in usage if a["shared"]]

        # 1. 即時非表示（deleted_at + deletion_tasks を同時 commit。既存メソッド）
        self.soft_delete_deck(deck_id, manifest_path=manifest_rel, deletion_id=tid)

        # 2. 復元 manifest を先に確定する
        manifest: dict[str, Any] = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "deletion_id": tid,
            "deck_id": deck_id,
            "created_at": self.now(),
            "deck": {k: deck[k] for k in ("id", "title", "subtitle", "purpose", "audience", "mode", "status", "pinned",
                                          "archived_at", "brand_revision_id", "style_revision_id",
                                          "created_at", "updated_at")},
            "tags": tags,
            "assets": {
                "exclusive": [{"asset_id": a["asset_id"], "relative_path": a["relative_path"], "sha256": a["sha256"],
                               "bytes": a["bytes"], "media_type": a["media_type"], "state_before": a["state"]} for a in exclusive],
                "shared": [{"asset_id": a["asset_id"], "relative_path": a["relative_path"], "other_refs": a["other_refs"]}
                           for a in shared],
            },
        }
        _write_json_atomic(self.root.root / manifest_rel, manifest)

        # 3. 専有ファイルだけ退避（同一ファイルシステム内の rename。共有 asset は触らない）
        moved: list[str] = []
        missing: list[str] = []
        error: str | None = None
        try:
            for a in exclusive:
                src = self.root.root / a["relative_path"]
                dst = trash_dir / a["relative_path"]
                if not src.is_file():
                    missing.append(a["asset_id"])
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src, dst)
                moved.append(a["asset_id"])
                _rmdir_if_empty(src.parent)
        except OSError as exc:
            error = exc.__class__.__name__

        # 4. 記録（退避できたぶんだけ trashed。失敗しても「何が動いたか」は manifest と events に残る）
        with self.transaction():
            for aid in moved:
                self.set_asset_state(aid, "trashed")
            state = "failed" if error else "trashed"
            self._exec("UPDATE deletion_tasks SET state=? WHERE id=?", (state, tid))
            self.append_event("deck_trashed", {
                "deck_id": deck_id, "deletion_task_id": tid, "state": state, "moved": len(moved),
                "shared_kept": len(shared), "missing": len(missing), "error_class": error,
            }, deck_id=deck_id)
        if error:
            raise StateError("ファイルの退避に失敗しました。スライドは非表示のままです。ごみ箱から復元できます")
        return {"deletion_task_id": tid, "manifest_path": manifest_rel, "state": state, "moved": len(moved),
                "shared_kept": len(shared), "missing": missing}

    # --- 復元 ---
    def restore_deck_from_trash(self, deck_id: str) -> dict[str, Any]:
        deck = self._get("decks", deck_id)
        if deck["deleted_at"] is None:
            raise StateError("このスライドは削除されていません")
        # 同着（created_at が同値）は挿入順 = rowid で決める。乱数 id で決めると「最新」が実行ごとに変わる
        task = self._one("SELECT * FROM deletion_tasks WHERE deck_id=? AND state IN ('trashed','failed')"
                         " ORDER BY created_at DESC,rowid DESC LIMIT 1", (deck_id,))
        if task is None:
            raise StateError("ごみ箱に復元できる記録がありません")
        manifest_path = self.root.root / task["manifest_path"]
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise StateError("復元 manifest を読めません") from None
        trash_dir = manifest_path.parent
        # 検証してから動かす（sha256 が manifest と違えば何も戻さない）
        plan: list[tuple[Path, Path, str]] = []
        for a in manifest.get("assets", {}).get("exclusive", []):
            src = trash_dir / a["relative_path"]
            dst = self.root.root / a["relative_path"]
            if dst.is_file():
                continue  # 退避されなかった（missing / 途中失敗）ので既に元の場所にある
            if not src.is_file():
                continue  # どちらにも無い: 戻せないが、行は trashed のまま残す（配信は 404 で「取れない」と言う）
            if _sha256_file(src) != a["sha256"]:
                raise StateError("退避したファイルの内容が記録と一致しないため復元を中止しました")
            plan.append((src, dst, a["asset_id"]))
        restored: list[str] = []
        for src, dst, aid in plan:
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dst)
            restored.append(aid)
            _rmdir_if_empty(src.parent)
        with self.transaction():
            for aid in restored:
                self.set_asset_state(aid, "ready")
            now = self.now()
            n = self._exec("UPDATE decks SET deleted_at=NULL,updated_at=?,lock_version=lock_version+1 WHERE id=? AND deleted_at IS NOT NULL",
                           (now, deck_id))
            if n != 1:
                raise StateError("復元対象のデッキが見つかりません")
            self._exec("UPDATE deletion_tasks SET state='restored',restored_at=? WHERE id=?", (now, task["id"]))
            self.append_event("deck_restored", {"deck_id": deck_id, "deletion_task_id": task["id"], "restored": len(restored)},
                              deck_id=deck_id)
            return self.deck(deck_id)

    # --- 完全削除 ---
    def _purge_targets(self, deck_id: str) -> tuple[list[dict[str, Any]], list[Path]]:
        """完全削除が触るもの: (このデッキが参照する asset の一覧, 消すディレクトリ)。

        **消す処理と、空く容量の見積りが同じ答えを見るためにここに集めている。** 2 か所で別に
        数え上げると、画面に出した「空けられる容量」と実際に空く容量が静かに食い違う。
        ディレクトリはすべて**こちらが発番した id で指せるもの**に限る。
        """
        dirs = [self.root.trash_dir / r["id"]
                for r in self._all("SELECT id FROM deletion_tasks WHERE deck_id=?", (deck_id,))]
        # デッキの置き場（台本の版・描いた回の出力。P2 以降で中身が入る）
        dirs.append(self.root.deck_dir(deck_id))
        return self.deck_asset_usage(deck_id), dirs

    def purge_estimate(self, deck_id: str) -> dict[str, int]:
        """完全削除で空く容量（バイト）と、消える / 残る asset の数。

        **disk の実ファイルを数える。** `assets.bytes` を足すのではない: 退避済みのファイルは
        `.trash/<deletion-id>/` の中に居るので、両方足すと**同じファイルを 2 回数える**。
        """
        usage, dirs = self._purge_targets(deck_id)
        total = sum(_dir_bytes(d) for d in dirs)
        exclusive = [a for a in usage if not a["shared"]]
        for a in exclusive:
            try:
                path = self.root.root / a["relative_path"]
                if path.is_file():
                    total += path.stat().st_size  # 退避されていない分だけ（退避済みは上で数えた）
            except OSError:
                continue
        return {"bytes": total, "assets": len(exclusive), "shared_kept": len(usage) - len(exclusive)}

    def purge_deck_from_trash(self, deck_id: str) -> dict[str, Any]:
        """ごみ箱のスライドを**本当に消す**。戻せない。

        **墓標（deleted_at だけ立てた行）を残さない。** 「完全に削除」というボタンを押した人が
        期待するのは「無くなる」であって「見えなくなる」ではない。行を残せば原稿や台本の文章も残る。

        消す範囲（`_PURGE_DELETES` が唯一の定義）と、消さないもの:

        - 消す: そのデッキに属する全ての行、`.trash/<deletion-id>/`、`decks/<deck-id>/`、
          **もう誰も参照していない** asset の行とファイル
        - 消さない: 他のスライド・ブランドがまだ参照している asset（行もファイルも触らない）。
          判断は `unreferenced_asset_ids` が**行を消したあとの DB に訊いて**決める

        外部キーは接続ごとに有効なので、表を 1 つ書き忘れれば commit が落ちて**何も起きない**。
        `defer_foreign_keys` で順序の縛りを外し、代わりに commit 時にまとめて検査させる。
        """
        deck = self._get("decks", deck_id)
        if deck["deleted_at"] is None:
            raise StateError("このスライドはごみ箱にありません。先にごみ箱へ移してください")
        # 描いている途中のスライドは消せない（P2 で描いた回が入ったら、ここで断る）

        # 1. 消す前に「どのファイルを消すか」と「どれだけ空くか」を控える（行を消したあとでは辿れない）
        freed = self.purge_estimate(deck_id)["bytes"]
        usage, directories = self._purge_targets(deck_id)
        candidates = {a["asset_id"]: a["relative_path"] for a in usage}

        # 2. 行を消す（1 トランザクション。落ちたら 1 行も消えない）
        with self.transaction():
            self._exec("PRAGMA defer_foreign_keys = ON")
            # 生き残る行から、消える行への由来のポインタを外す（複製の「どれの複製か」）。
            # ここで消えるのは**消したスライドを指し続ける参照**だけである
            self._exec("UPDATE decks SET origin_deck_id=NULL WHERE origin_deck_id=:d", {"d": deck_id})
            for sql in _PURGE_DELETES:
                self._exec(sql, {"d": deck_id})
            orphans = self.unreferenced_asset_ids(list(candidates))
            for asset_id in orphans:
                self._exec("DELETE FROM assets WHERE id=?", (asset_id,))
            self.append_event("deck_purged", {
                "deck_id": deck_id, "assets_removed": len(orphans), "assets_kept": len(candidates) - len(orphans),
            })

        # 3. ファイルを消す（記録が消えたあと。ここで失敗しても残るのは行に紐づかないファイルだけで、
        #    逆順にすると「行はあるのに中身が無い」= 復元できないごみ箱ができる）
        failed: list[str] = []
        for directory in directories:
            try:
                shutil.rmtree(directory)
            except FileNotFoundError:
                pass
            except OSError as exc:
                failed.append(exc.__class__.__name__)
        for asset_id in orphans:
            path = self.root.root / candidates[asset_id]
            try:
                path.unlink()
                _rmdir_if_empty(path.parent)
            except FileNotFoundError:
                pass  # 退避済み（`.trash/<deletion-id>/` ごと上で消えている）
            except OSError as exc:
                failed.append(exc.__class__.__name__)
        return {"deck_id": deck_id, "assets_removed": len(orphans), "bytes_freed": freed,
                "assets_kept": len(candidates) - len(orphans), "file_errors": sorted(set(failed))}

    def empty_trash(self) -> dict[str, Any]:
        """ごみ箱の全部を完全に削除する。

        **1 件ずつ独立に消す。** 途中の 1 件が断られても（作っている途中など）残りは消える。
        1 つのトランザクションにまとめないのは、15 件のうち 1 件のせいで 14 件が消えないほうが
        利用者にとって驚きだからである。断られたぶんは数を返し、画面が「残した」と言う。
        """
        purged: list[dict[str, Any]] = []
        skipped = 0
        for row in self.trashed_decks():
            try:
                purged.append(self.purge_deck_from_trash(row["id"]))
            except StateError:
                skipped += 1  # 理由は行ごとの `purge_capability` が既に画面に出している
        return {"purged": len(purged), "skipped": skipped,
                "assets_removed": sum(p["assets_removed"] for p in purged),
                "bytes_freed": sum(p["bytes_freed"] for p in purged),
                "file_errors": sorted({e for p in purged for e in p["file_errors"]})}


# デッキ 1 つに属する行の**全て**。完全削除がここだけを見て消す（他に書き足さない）。
# 書き忘れた表があれば commit 時の外部キー検査が落ち、何も消えないまま断る。
_PURGE_DELETES: tuple[str, ...] = (
    "DELETE FROM launches WHERE deck_id=:d",
    "DELETE FROM deck_tags WHERE deck_id=:d",
    "DELETE FROM deletion_tasks WHERE deck_id=:d",
    "DELETE FROM events WHERE deck_id=:d",
    "DELETE FROM decks WHERE id=:d",
)
