"""会話の入口（`skills/quick-slide/scripts/quick_slide_start.py`）: 使い捨ての置き場で、サーバを自分で起こし、
原稿を投入し、JSON 1 行を返す。そのサーバが台本係（偽物）とエンジン（本物の image-slide・絵は偽物の codex）で
1 冊を描き終える。ブラウザの経路は「開かず URL だけ」（manual）で確かめる（本物のブラウザを開かない）。
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import time
import unittest

from helpers import FAKES, MANUSCRIPT, REPO, SKILLS_DIR, fake_codex_home, temp_dir

SCRIPT = REPO / "skills" / "quick-slide" / "scripts" / "quick_slide_start.py"
# 会話の印（この試験を走らせている会話の値を拾わない）
CONVERSATION_VARS = ("CLAUDE_CODE_SESSION_ID", "CLAUDECODE", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_HOME",
                     "CODEX_SANDBOX", "QUICK_SLIDE_REPO", "PYTHONPATH")


class SkillStartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temp_dir(self, "quick-slide-skill-")
        self.home = self.tmp / "home"
        (self.home / "config").mkdir(parents=True)
        (self.home / "config" / "settings.json").write_text(json.dumps({"server": {"port": 0}}))
        self.manuscript = self.tmp / "manuscript.md"
        self.manuscript.write_text(MANUSCRIPT, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in CONVERSATION_VARS}
        env.update({
            "QUICK_SLIDE_HOME": str(self.home), "PYTHONDONTWRITEBYTECODE": "1",
            "QUICK_SLIDE_CLAUDE": str(FAKES / "fake_claude.py"), "QUICK_SLIDE_CODEX_TEXT": str(FAKES / "refuse.py"),
            "FAKE_WRITER_LOG": str(self.tmp / "writer-calls"),
            "IMAGE_SLIDE_CODEX": str(SKILLS_DIR / "image-slide" / "tests" / "fake_codex.py"),
            "FAKE_CODEX_LOG": str(self.tmp / "codex-calls"), "IMAGE_SLIDE_CACHE": str(self.tmp / "image-slide-cache"),
            "IMAGE_SLIDE_CONFIG": str(self.tmp / "no-config.yaml"),
        })
        env["CODEX_HOME"] = str(fake_codex_home(self.tmp))
        self.env = env
        self.addCleanup(self.stop_server)

    def stop_server(self) -> None:
        subprocess.run([sys.executable, "-m", "quick_slide.server", "--home", str(self.home), "stop"],
                       env={**self.env, "PYTHONPATH": str(REPO / "src")}, capture_output=True, timeout=60)

    def start(self, *args: str, env: dict | None = None) -> tuple[int, dict, str, str]:
        r = subprocess.run([sys.executable, str(SCRIPT), *args], env=env or self.env, capture_output=True, text=True,
                           timeout=120, cwd=str(self.tmp))
        lines = r.stdout.strip().splitlines()
        return r.returncode, json.loads(lines[-1]) if lines else {}, r.stdout, r.stderr

    def test_starts_the_server_submits_and_the_deck_gets_drawn(self) -> None:
        rc, out, stdout, stderr = self.start("--manuscript-file", str(self.manuscript), "--title", "会議を半分にする",
                                             "--mode", "pitch", "--style", "standard", "--image-look", "illustration",
                                             "--runtime", "claude_code", "--no-open")
        self.assertEqual(rc, 0, (out, stderr))
        self.assertEqual(len(stdout.strip().splitlines()), 1, "stdout は JSON 1 行だけ")
        self.assertEqual((out["status"], out["mode"], out["style"], out["deck_title"]), ("ready", "pitch", "standard", "会議を半分にする"))
        self.assertIn(f"/slides/{out['deck_id']}?launch={out['launch_id']}&token=", out["url"])
        self.assertFalse(out["opened"])
        self.assertEqual(out["payload"]["deck_attributes"], {"mode": "pitch", "style": "standard", "image_look": "illustration"})
        self.assertNotIn("11.5", stdout + stderr, "原稿の本文を出さない")
        self.assertFalse(out["server"]["already_running"])
        record = json.loads((self.home / "runtime" / "server.json").read_text())
        deadline = time.monotonic() + 120
        view = None
        while time.monotonic() < deadline:
            conn = http.client.HTTPConnection("127.0.0.1", record["port"], timeout=10)
            conn.request("GET", f"/api/decks/{out['deck_id']}", headers={"Host": f"127.0.0.1:{record['port']}"})
            view = json.loads(conn.getresponse().read())
            conn.close()
            if not view["live"]["working"]:
                break
            time.sleep(0.3)
        self.assertEqual(len(view["pages"]), 4, view["planning"])
        self.assertTrue(all(p["image_url"] for p in view["pages"]))

        # 2 回目はサーバを起こし直さない。URL を見せたあとの確かめ（manual は開かず URL だけ）
        rc, ensured, _, _ = self.start("--ensure-launch", out["launch_id"], "--url", out["url"], "--browser-mode", "manual")
        self.assertEqual(rc, 0, ensured)
        self.assertEqual((ensured["status"], ensured["route"], ensured["attached"]), ("ensured", "manual", False))
        self.assertTrue(ensured["message"])

    def test_not_yet_options_and_input_errors(self) -> None:
        rc, out, _, _ = self.start("--revise")
        self.assertEqual((rc, out["code"]), (2, "not_ready_in_v2"))
        self.assertIn("まだできません", out["message"])
        rc, out, _, _ = self.start("--manuscript-file", str(self.manuscript), "--mode", "presentation", "--style", "infographic")
        self.assertEqual((rc, out["code"]), (2, "style_invalid"))
        rc, out, _, _ = self.start("--title", "原稿なし")
        self.assertEqual((rc, out["code"]), (2, "manuscript_required"))
        self.assertFalse((self.home / "runtime" / "server.json").exists(), "入力の誤りではサーバを起こさない")

    def test_where_and_repo_override(self) -> None:
        rc, out, _, _ = self.start("--where")
        self.assertEqual((rc, out["repo"], out["source"]), (0, str(REPO), "plugin"))
        rc, out, _, _ = self.start("--manuscript-file", str(self.manuscript), env={**self.env, "QUICK_SLIDE_REPO": str(self.tmp)})
        self.assertEqual((rc, out["code"]), (3, "repo_invalid"))


if __name__ == "__main__":
    unittest.main()
