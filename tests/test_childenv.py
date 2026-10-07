"""子プロセスの環境変数（childenv.py）。従量課金のキーは渡さず、OAuth のトークンは渡す。"""

from __future__ import annotations

import unittest
from pathlib import Path

from quick_slide.childenv import child_env


class ChildEnvTest(unittest.TestCase):
    def test_api_keys_are_dropped_and_oauth_tokens_are_kept(self) -> None:
        base = {"OPENAI_API_KEY": "x", "ANTHROPIC_API_KEY": "x", "AZURE_OPENAI_API_KEY": "x", "CODEX_API_KEY": "x",
                "ANTHROPIC_AUTH_TOKEN": "t", "CLAUDE_CODE_OAUTH_TOKEN": "t", "CLAUDECODE": "1", "PATH": "/usr/bin"}
        env = child_env(home=Path("/tmp/qs-home"), base=base)
        for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY", "CODEX_API_KEY", "CLAUDECODE"):
            self.assertNotIn(name, env)
        # 会話しているアプリが Claude Code をこのトークンでログインさせていることがある。消すと台本係が止まる
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "t")
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "t")
        self.assertEqual(env["QUICK_SLIDE_HOME"], "/tmp/qs-home")

    def test_extra_cannot_put_an_api_key_back(self) -> None:
        env = child_env(home=Path("/tmp/qs-home"), base={}, extra={"OPENAI_API_KEY": "x"})
        self.assertNotIn("OPENAI_API_KEY", env)


if __name__ == "__main__":
    unittest.main()
