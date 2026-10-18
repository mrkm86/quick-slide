"""データ置き場（サーバの置き場はここ 1 か所で決める）。

既定は `~/.quick-slide/`。環境変数 `QUICK_SLIDE_HOME` で差し替える（テストは一時ディレクトリ）。
エンジン（`skills/*/scripts/places.py`）と同じ根っこを同じ規則で読む。エンジンが使う
`<home>/engines/<スキル>/` はエンジンの持ち物なので、ここでは作らない・触らない。
コードの cwd に依存せず、常に絶対パスで扱う。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_HOME = "QUICK_SLIDE_HOME"
DEFAULT_HOME = Path.home() / ".quick-slide"

# リポ同梱の静的ファイル（静的配信ルート）。データ置き場とは別で、config/ と db/ はこの下に無い。
REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_APP_DIR = REPO_ROOT / "web" / "app"
# エンジン（html-slide・image-slide）。サーバは呼ぶだけで、中身は持たない（台本の決まりも実行時にここから読む）
SKILLS_DIR = REPO_ROOT / "skills"
# 同梱の見本（モードごとに 1 冊。最初の起動で入れる。docs/adr/0007-bundled-samples.md）
SAMPLES_DIR = REPO_ROOT / "samples"


@dataclass(frozen=True)
class DataRoot:
    root: Path

    @classmethod
    def resolve(cls, explicit: str | os.PathLike[str] | None = None) -> "DataRoot":
        if explicit is not None:
            return cls(Path(explicit).expanduser().resolve())
        env = os.environ.get(ENV_HOME)
        if env:
            return cls(Path(env).expanduser().resolve())
        return cls(DEFAULT_HOME.expanduser().resolve())

    # --- 固定のサブディレクトリ / ファイル ---
    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def settings_path(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def request_builder_path(self) -> Path:
        """会話へコピーした依頼の選択条件（本文は保存しない）。"""
        return self.config_dir / "request-builder.json"

    @property
    def hmac_key_path(self) -> Path:
        return self.config_dir / "hmac.key"

    @property
    def db_dir(self) -> Path:
        return self.root / "db"

    @property
    def db_path(self) -> Path:
        return self.db_dir / "quick-slide.db"

    @property
    def db_backup_dir(self) -> Path:
        return self.db_dir / "backups"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    @property
    def decks_dir(self) -> Path:
        """デッキごとの置き場（台本の版・描いた回）。"""
        return self.root / "decks"

    def deck_dir(self, deck_id: str) -> Path:
        return self.decks_dir / deck_id

    def present_fonts_dir(self, deck_id: str) -> Path:
        """発表の字体の控え。同じデッキの回が、中身ごとに共有する。"""
        return self.deck_dir(deck_id) / "present-fonts"

    def scripts_dir(self, deck_id: str) -> Path:
        """台本の版（`<版>.json`）。"""
        return self.deck_dir(deck_id) / "scripts"

    def run_dir(self, deck_id: str, run_id: str) -> Path:
        """描いた回の控え（P3 から）。エンジンは `work_dir` に描き、終わった回の出力をここへハードリンクで写す
        （回は不変の控え。画面と書き出しはここを読む）。台本係の控え（`writer/`）とエンジンのログもここに置く。"""
        return self.deck_dir(deck_id) / "runs" / run_id

    def motion_draft_dir(self, deck_id: str, intake_id: str) -> Path:
        """モーションの場面別の途中結果。同じ取り込みを再開した回で共有する。"""
        return self.deck_dir(deck_id) / "motion-drafts" / intake_id

    def work_dir(self, deck_id: str) -> Path:
        """エンジンの `--out`（デッキごとに 1 つで動かない）。image-slide は描いた絵のキャッシュの区画を `--out` で
        引くので、回ごとに新しいフォルダへ描くと毎回全ページを描き直す（P3 で直した）。"""
        return self.deck_dir(deck_id) / "work"

    def engine_script_path(self, deck_id: str) -> Path:
        """エンジンに渡す台本（いまの台本から非表示のページを抜いたもの）。台本の版と同じフォルダに置く:
        html-slide は描いた絵のキャッシュの区画を台本のフォルダで引くので、版が変わっても同じ区画を使う。"""
        return self.scripts_dir(deck_id) / "engine.json"

    def attachments_dir(self, deck_id: str) -> Path:
        """直しに添えた画像の写し（台本の `path` は台本のフォルダからの相対 `attachments/<名前>` で書く）。"""
        return self.scripts_dir(deck_id) / "attachments"

    def imported_dir(self, deck_id: str) -> Path:
        """PPTX から取り込んだページの中身（エンジンの `import` の出力: 台本の元・`media/`・テーマ・報告）。台本の `html` の画像は
        台本のフォルダからの相対 `imported/media/<名前>` で書く（docs/adr/0011-import-pptx.md）。"""
        return self.scripts_dir(deck_id) / "imported"

    def import_source_path(self, deck_id: str) -> Path:
        """取り込んだ元の PPTX（取り込み直す・調べる用。画面にも書き出しにも出さない）。"""
        return self.deck_dir(deck_id) / "source" / "source.pptx"

    def page_asset_rel(self, deck_id: str, run_id: str, page_no: int) -> str:
        """描いた回のページの画像を資産として配る置き場（`assets/` の下・データ置き場からの相対）。

        配信は `assets/` の下に閉じている（`server/static.py`）ので、エンジンの出力をここへ**ハードリンク**で張る
        （複製しない・出力フォルダを配信の根にしない）。
        """
        return f"assets/decks/{deck_id}/{run_id}/slide-{page_no:02d}.png"

    @property
    def runtime_dir(self) -> Path:
        return self.root / "runtime"

    @property
    def uploads_dir(self) -> Path:
        return self.runtime_dir / "uploads"

    # --- 常駐サーバ（`runtime/server.json`） ---
    @property
    def server_json_path(self) -> Path:
        return self.runtime_dir / "server.json"

    @property
    def server_lock_path(self) -> Path:
        return self.runtime_dir / "server.lock"

    @property
    def server_log_path(self) -> Path:
        return self.runtime_dir / "server.log"

    @property
    def server_stdio_path(self) -> Path:
        """`start`（デタッチ起動）した子プロセスの stdout/stderr の行き先。"""
        return self.runtime_dir / "server.out"

    @property
    def last_good_listen_path(self) -> Path:
        """いま動いている（＝起動できた）待ち受けの控え。再起動して新しい設定で起動できなければ、これに戻す。"""
        return self.runtime_dir / "last-good-listen.json"

    @property
    def restart_result_path(self) -> Path:
        """最後の再起動の結果（新しい設定で起動できたか・前の設定に戻したか・その理由）。画面が health 経由で読む。"""
        return self.runtime_dir / "restart.json"

    def run_settings_dir(self, deck_id: str, run_id: str) -> Path:
        """描いた回に渡した設定の控え（ブランドから作ったテーマの YAML など）。エンジンにはこのファイルを渡す。"""
        return self.run_dir(deck_id, run_id) / "settings"

    def run_compilation_dir(self, deck_id: str, run_id: str) -> Path:
        """デザインテンプレートの回のコンパイル結果（writer.md・preparation.md・style.yaml・theme.yaml・engine-input.json・
        compilation.json など）。正本は DB の控え（template_snapshots）で、ここは控えから作り直せる写し。"""
        return self.run_dir(deck_id, run_id) / "template"

    def deck_preparations_dir(self, deck_id: str) -> Path:
        """デザインテンプレートの素材準備の世代（デッキの中で単調に増える番号。失敗した番号も再利用しない）。"""
        return self.deck_dir(deck_id) / "preparations"

    def preparation_dir(self, deck_id: str, generation: int) -> Path:
        """素材準備の 1 世代（入口の確認・読解・カタログ・選んだ素材のコピー・準備の控え）。"""
        return self.deck_preparations_dir(deck_id) / f"gen-{generation:04d}"

    @property
    def trash_dir(self) -> Path:
        return self.root / ".trash"

    def ensure(self) -> "DataRoot":
        """必要なディレクトリを作る。`config/` は 0700（秘匿物が置かれる）。`engines/` は作らない。"""
        for d in (
            self.root,
            self.db_dir,
            self.assets_dir,
            self.decks_dir,
            self.runtime_dir,
            self.uploads_dir,
            self.trash_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.config_dir, 0o700)
        except OSError:
            pass
        return self
