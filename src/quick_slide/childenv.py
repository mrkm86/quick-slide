"""子プロセス（台本係の `claude` / `codex`・エンジン）に渡す環境変数。**サブスク枠だけで動かす**ための 1 か所。

`claude` も `codex` も、API キーの環境変数が残っていると従量課金の API に切り替わる。だから子プロセスには
**キーを 1 つも渡さない**（ログイン済みのサブスクを使わせる）。消すのは呼ぶ側の手元の値で、何をしても戻さない。

入れ子の印（会話から起こしたサーバが受け継いだ「いま Claude Code / Codex の中にいる」という印）も消す。
残っていると、子の CLI が自分を入れ子の会話だと思い込む。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

# 従量課金に切り替わるキー。値は読まない・出さない・渡さない
API_KEY_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "AZURE_OPENAI_API_KEY", "CODEX_API_KEY")
NESTED_SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT",
                       "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED")


def child_env(*, home: Path, extra: Mapping[str, str] | None = None,
              base: Mapping[str, str] | None = None) -> dict[str, str]:
    """`base`（既定は今の環境）＋`extra` から、API キーと入れ子の印を**最後に**消した環境。

    `QUICK_SLIDE_HOME` はこのサーバの置き場に揃える（エンジンの設定・出力の既定がそこを見る）。
    """
    env = dict(os.environ if base is None else base)
    env.update(extra or {})
    for name in API_KEY_VARS + NESTED_SESSION_VARS:
        env.pop(name, None)
    env["QUICK_SLIDE_HOME"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env
