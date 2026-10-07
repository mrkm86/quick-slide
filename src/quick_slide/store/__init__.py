"""アクセス層の入口。`Store.open(root)` で DB を開き、マイグレーション・鍵・プリセット・起動時掃除まで済ませる。

上位（サーバ）はこのパッケージの公開メソッドだけを使い、SQL を書かない。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..clock import Clock
from ..db.connection import connect
from ..db.migrate import migrate
from ..paths import DataRoot
from ..security.hmac_key import load_or_create_key
from ..settings import Settings
from .assets import AssetsMixin
from .base import StoreBase
from .brands import BrandsMixin
from .decks import DecksMixin
from .edits import EditsMixin
from .events import EventsMixin
from .intakes import IntakesMixin
from .launches import LaunchesMixin
from .management import DeckManagementMixin
from .presets import PresetsMixin
from .runs import RunsMixin
from .styles import StylesMixin
from .views import DeckViewsMixin


class Store(
    IntakesMixin,
    EditsMixin,
    RunsMixin,
    DeckManagementMixin,
    DeckViewsMixin,
    LaunchesMixin,
    DecksMixin,
    PresetsMixin,
    StylesMixin,
    BrandsMixin,
    AssetsMixin,
    EventsMixin,
    StoreBase,
):
    @classmethod
    def open(
        cls,
        root: DataRoot | str | Path | None = None,
        *,
        clock: Clock | None = None,
        settings: Settings | None = None,
        conn: sqlite3.Connection | None = None,
        startup_sweep: bool = True,
    ) -> "Store":
        """データ置き場を用意し、DB を開き、マイグレーションを適用し、鍵を読み（無ければ作り）、プリセットを保証する。"""
        data_root = root if isinstance(root, DataRoot) else DataRoot.resolve(root)
        data_root.ensure()
        settings = settings or Settings.load(data_root.settings_path)
        key = load_or_create_key(data_root.hmac_key_path)
        connection = conn or connect(data_root.db_path)
        migrate(connection, db_path=data_root.db_path, backup_dir=data_root.db_backup_dir)
        store = cls(data_root, connection, clock=clock or Clock(), settings=settings, hmac_key=key)
        store.ensure_presets()
        if startup_sweep:
            store.startup_sweep()
        return store

    def startup_sweep(self) -> dict[str, list[str]]:
        """起動時の掃除: 期限切れ launch・前のサーバで途中だった仕事（失敗にする。作り直さない）・
        ごみ箱へ移す途中で止まった削除（ごみ箱から戻せる形にする）。"""
        return {"launches_expired": self.expire_launches(), "jobs_interrupted": self.fail_interrupted_jobs(),
                "deletions_recovered": self.recover_interrupted_deletions()}


__all__ = ["Store"]
