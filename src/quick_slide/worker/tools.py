"""ワーカーが呼ぶ実行ファイルの在りか（試験は偽物に差し替える）。

- 台本係の CLI: `QUICK_SLIDE_CLAUDE`（既定は PATH の `claude`）・`QUICK_SLIDE_CODEX_TEXT`（既定は PATH の `codex`）
- エンジン: `skills/` の下の入口を、このサーバと同じ Python で呼ぶ
- エンジンが絵を描くときの `codex` はエンジン自身が探す（`HTML_SLIDE_CODEX`・`IMAGE_SLIDE_CODEX`・設定・PATH）

`extra_env` は子プロセスに足す環境（試験が偽物の振る舞いを渡す口）。足したあとでも API キーは消える
（`childenv.child_env`）。
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..paths import SKILLS_DIR

ENV_CLAUDE = "QUICK_SLIDE_CLAUDE"
ENV_CODEX_TEXT = "QUICK_SLIDE_CODEX_TEXT"


@dataclass
class Tools:
    python: str = field(default_factory=lambda: sys.executable)
    skills_dir: Path = SKILLS_DIR
    claude: str | None = None
    codex: str | None = None
    extra_env: dict[str, str] = field(default_factory=dict)
    # モーションが組むのに要るもの（Playwright の Chromium など）の確かめ。None なら `render/runner.py` の既定の確かめ。
    # 足りなければ 4 で終わり、足りないものの名前の配列（JSON）を標準出力の最後の行に出す（試験が偽物を差す）
    motion_probe: list[str] | None = None

    def _lookup(self, explicit: str | None, env_name: str, command: str) -> str | None:
        value = explicit or self.extra_env.get(env_name) or os.environ.get(env_name)
        if value:
            return value if Path(value).is_file() else shutil.which(value)
        return shutil.which(command)

    def claude_path(self) -> str | None:
        return self._lookup(self.claude, ENV_CLAUDE, "claude")

    def codex_path(self) -> str | None:
        return self._lookup(self.codex, ENV_CODEX_TEXT, "codex")
