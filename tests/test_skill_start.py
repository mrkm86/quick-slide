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

from helpers import FAKES, MANUSCRIPT, REPO, SKILLS_DIR, fake_codex_home, runnable, temp_dir

SCRIPT = REPO / "skills" / "quick-slide" / "scripts" / "quick_slide_start.py"
# 会話の印（この試験を走らせている会話の値を拾わない）
CONVERSATION_VARS = ("CLAUDE_CODE_SESSION_ID", "CLAUDECODE", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_HOME",
                     "CODEX_SANDBOX", "QUICK_SLIDE_REPO", "PYTHONPATH")


class SkillStartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temp_dir(self, "quick-slide-skill-")
        self.home = self.tmp / "home"
        (self.home / "config").mkdir(parents=True)
        (self.home / "config" / "settings.json").write_text(json.dumps({"server": {"port": 0}}), encoding="utf-8")
        self.manuscript = self.tmp / "manuscript.md"
        self.manuscript.write_text(MANUSCRIPT, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in CONVERSATION_VARS}
        env.update({
            "QUICK_SLIDE_HOME": str(self.home), "PYTHONDONTWRITEBYTECODE": "1",
            "QUICK_SLIDE_CLAUDE": runnable(FAKES / "fake_claude.py"), "QUICK_SLIDE_CODEX_TEXT": runnable(FAKES / "refuse.py"),
            "FAKE_WRITER_LOG": str(self.tmp / "writer-calls"),
            "IMAGE_SLIDE_CODEX": runnable(SKILLS_DIR / "image-slide" / "tests" / "fake_codex.py"),
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
                           timeout=120, cwd=str(self.tmp), encoding="utf-8")
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
        record = json.loads((self.home / "runtime" / "server.json").read_text(encoding="utf-8"))
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
        self.assertEqual((rc, out["code"]), (2, "note_required"))
        rc, out, _, _ = self.start("--manuscript-file", str(self.manuscript), "--attach", str(self.manuscript))
        self.assertEqual((rc, out["code"]), (2, "not_ready_in_v2"))
        self.assertIn("まだできません", out["message"])
        rc, out, _, _ = self.start("--revise", "--note", "x", "--pages", "7,a")
        self.assertEqual((rc, out["code"]), (2, "pages_invalid"))
        rc, out, _, _ = self.start("--manuscript-file", str(self.manuscript), "--mode", "presentation", "--style", "infographic")
        self.assertEqual((rc, out["code"]), (2, "style_invalid"))
        rc, out, _, _ = self.start("--title", "原稿なし")
        self.assertEqual((rc, out["code"]), (2, "manuscript_required"))
        self.assertFalse((self.home / "runtime" / "server.json").exists(), "入力の誤りではサーバを起こさない")

    def test_outline_fixed_and_wait_returns_the_page_headlines(self) -> None:
        outline = self.tmp / "outline.md"
        outline.write_text("# 会議を半分にする（架空）\n\n## 1. 会議を半分にする\n- 3か月の試行の提案\n画像: なし\n\n"
                           "## 2. 会議の4割は何も決めずに終わっている\n- 回答240人（架空）\n画像: なし\n\n"
                           "## 3. まず2チームで試します\n- 来月から\n画像: 明るい会議室\n", encoding="utf-8")
        rc, out, stdout, stderr = self.start("--manuscript-file", str(outline), "--title", "会議を半分にする",
                                             "--mode", "pitch", "--runtime", "claude_code", "--outline-fixed",
                                             "--wait", "--wait-timeout", "120", "--no-open")
        self.assertEqual(rc, 0, (out, stderr))
        self.assertEqual(len(stdout.strip().splitlines()), 1)
        self.assertEqual(out["result"], "done")
        self.assertEqual([p["title"] for p in out["pages"]],
                         ["会議を半分にする", "会議の4割は何も決めずに終わっている", "まず2チームで試します"])
        self.assertTrue(out["payload"]["deck_attributes"]["outline_fixed"])
        self.assertIn("3 ページで作りました", out["message"])
        self.assertIn(f"/slides/{out['deck_id']}", out["url"])
        calls = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((self.tmp / "writer-calls").glob("*.json"))]
        self.assertTrue(calls[0]["outline"], "台本係に「構成を守る」を言った")

    def test_request_instead_of_a_manuscript_researches_then_draws(self) -> None:
        """`--request` は画面の入力欄と同じ形（`origin.kind=request`）で送る。違いは runtime だけ。"""
        request = "会議を短くする工夫をまとめて\n3 ページで、管理職向けに（この行は外に出さない）"
        rc, out, stdout, stderr = self.start("--request", request, "--mode", "pitch", "--runtime", "claude_code",
                                             "--wait", "--wait-timeout", "120", "--no-open")
        self.assertEqual(rc, 0, (out, stderr))
        self.assertEqual(len(stdout.strip().splitlines()), 1)
        self.assertEqual(out["result"], "done")
        self.assertEqual(out["payload"]["origin"]["kind"], "request")
        self.assertEqual(out["payload"]["origin"]["runtime"], "claude_code")
        self.assertEqual((out["payload"]["request_chars"], out["payload"]["manuscript_chars"]), (len(request), None))
        self.assertNotIn("この行は外に出さない", stdout + stderr, "依頼の文を出さない（仮の題にした 1 行目だけ）")
        self.assertEqual(out["deck_title"], "会議の進め方の今（架空）", "待ったときは下調べ係が付けた題を返す")
        calls = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((self.tmp / "writer-calls").glob("*.json"))]
        research = [c for c in calls if c.get("what") == "research"]
        writes = [c for c in calls if c.get("what") == "make"]
        self.assertEqual(len(research), 1)
        self.assertEqual(research[0]["argv"][research[0]["argv"].index("--tools") + 1], "WebSearch,WebFetch")
        self.assertEqual(writes[0]["request"], request, "台本係にも利用者の依頼として渡る")
        self.assertIn("- 枚数: 3 枚（表紙と締めを含む）", writes[0]["prompt_lines"], "依頼の枚数が台本係の属性に入る")

    def test_request_input_errors(self) -> None:
        for args, code in ((("--request", "x", "--manuscript-file", str(self.manuscript)), "input_conflict"),
                           (("--request", "x", "--request-file", str(self.manuscript)), "input_conflict"),
                           (("--request", "x", "--outline-fixed"), "input_conflict"),
                           (("--request", "  "), "request_required")):
            with self.subTest(args=args):
                rc, out, _, _ = self.start(*args)
                self.assertEqual((rc, out["code"]), (2, code))
        self.assertFalse((self.home / "runtime" / "server.json").exists(), "入力の誤りではサーバを起こさない")

    def test_revise_pages_7_8_with_wait_from_the_same_conversation(self) -> None:
        env = {**self.env, "FAKE_WRITER_PAGES": "10"}
        key = ["--conversation-key", "conversation-key-for-the-revise-test-0123"]  # gitleaks:allow（試験用のダミーの会話キー）
        rc, out, _, stderr = self.start("--manuscript-file", str(self.manuscript), "--mode", "pitch",
                                        "--runtime", "claude_code", *key, "--wait", "--no-open", env=env)
        self.assertEqual((rc, out.get("result")), (0, "done"), (out, stderr))
        self.assertEqual(len(out["pages"]), 10)
        before = {p["no"]: p["title"] for p in out["pages"]}
        rc, rev, stdout, stderr = self.start("--revise", "--pages", "7,8", "--note", "見出しを短くして", "--wait",
                                             "--runtime", "claude_code", *key, env=env)
        self.assertEqual(rc, 0, (rev, stderr))
        self.assertEqual(len(stdout.strip().splitlines()), 1)
        self.assertEqual((rev["status"], rev["deck_id"], rev["target_pages"]), ("revised", out["deck_id"], [7, 8]))
        self.assertEqual(rev["url"].rsplit("/", 1)[-1], out["deck_id"], "URL はいつも返す")
        after = {p["no"]: p["title"] for p in rev["pages"]}
        self.assertEqual(after[7], before[7] + "（直し）")
        self.assertEqual(after[8], before[8] + "（直し）")
        self.assertEqual({n: t for n, t in after.items() if n not in (7, 8)},
                         {n: t for n, t in before.items() if n not in (7, 8)})
        self.assertEqual(rev["suggestions"], [{"page": 6, "text": "前のページの見出しも短くするとよい（架空）"}])
        self.assertNotIn("見出しを短くして", stderr)
        # スライドを指定して、全体への指示（--pages を省く）
        rc, rev, _, stderr = self.start("--revise", "--deck", out["deck_id"], "--note", "そのままでよい", "--wait",
                                        "--runtime", "claude_code", env=env)
        self.assertEqual((rc, rev["status"], rev["target_pages"]), (0, "revised", "all"), (rev, stderr))
        # 範囲の外
        rc, rev, _, _ = self.start("--revise", "--deck", out["deck_id"], "--pages", "11", "--note", "x", env=env)
        self.assertEqual((rc, rev["code"]), (2, "pages_invalid"))
        self.assertIn("/slides/", rev["url"])

    def test_where_and_repo_override(self) -> None:
        rc, out, _, _ = self.start("--where")
        self.assertEqual((rc, out["repo"], out["source"]), (0, str(REPO), "plugin"))
        rc, out, _, _ = self.start("--manuscript-file", str(self.manuscript), env={**self.env, "QUICK_SLIDE_REPO": str(self.tmp)})
        self.assertEqual((rc, out["code"]), (3, "repo_invalid"))


if __name__ == "__main__":
    unittest.main()
