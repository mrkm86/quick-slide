"""P4 の頭: 画面の「再起動」。本物のサーバのプロセス（`python -m quick_slide.server start`）を使い捨ての置き場で起こし、
`POST /api/server/restart` で起動し直させる。

- 同じ待ち受けなら、同じ URL で新しいインスタンスになる
- ポートを変えたら、答えの `expected_url` が新しい URL で、そこに新しいサーバがいる
- この機械に無いアドレス（TEST-NET の 203.0.113.7）なら、前の設定に戻して前の URL で起動し、理由が health に出る
- 「終わるのを待って」は走っている仕事を終えてから起動し直す（仕事は失敗にならない）
- 断る: 形の違う頼み・読めない設定・「ぜんぶの口」・セッションの無い頼み・試験のスレッドの中のサーバ
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import time
import unittest

from helpers import FAKES, REPO, SKILLS_DIR, fake_codex_home, intake_payload, runnable, start_server, temp_dir


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RestartProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temp_dir(self, "quick-slide-p4-restart-")
        self.home = self.tmp / "home"
        (self.home / "config").mkdir(parents=True)
        self.port = free_port()
        self.write_settings({"server": {"port": self.port}, "worker": {"parallel": 1}})
        self.env = {
            **os.environ, "PYTHONPATH": str(REPO / "src"), "PYTHONDONTWRITEBYTECODE": "1", "QUICK_SLIDE_BROWSER": "manual",
            "QUICK_SLIDE_CLAUDE": runnable(FAKES / "fake_claude.py"), "QUICK_SLIDE_CODEX_TEXT": runnable(FAKES / "refuse.py"),
            "FAKE_WRITER_LOG": str(self.tmp / "writer-calls"),
            "IMAGE_SLIDE_CODEX": runnable(SKILLS_DIR / "image-slide" / "tests" / "fake_codex.py"),
            "FAKE_CODEX_LOG": str(self.tmp / "codex-calls"),
            "CODEX_HOME": str(fake_codex_home(self.tmp)), "IMAGE_SLIDE_CACHE": str(self.tmp / "image-slide-cache"),
            "IMAGE_SLIDE_CONFIG": str(self.tmp / "no-config.yaml"),
        }
        self.addCleanup(self.cli, "stop")

    # --- 道具 ---
    def write_settings(self, data: dict) -> None:
        (self.home / "config" / "settings.json").write_text(json.dumps(data), encoding="utf-8")

    def settings(self) -> dict:
        return json.loads((self.home / "config" / "settings.json").read_text(encoding="utf-8"))

    def cli(self, *args: str) -> dict:
        r = subprocess.run([sys.executable, "-m", "quick_slide.server", "--home", str(self.home), *args],
                           env=self.env, capture_output=True, text=True, timeout=60, cwd=str(REPO), encoding="utf-8")
        return json.loads(r.stdout.strip().splitlines()[-1])

    def record(self) -> dict:
        return json.loads((self.home / "runtime" / "server.json").read_text(encoding="utf-8"))

    def http(self, method: str, path: str, body=None, *, port: int | None = None, token: str | None = None,
             session: bool = True):
        rec = self.record()
        port = port or rec["port"]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Host": f"127.0.0.1:{port}"}
        if session:
            headers["X-Quick-Slide-Session"] = token or rec["session_token"]
        if data is not None:
            headers.update({"Content-Type": "application/json", "Content-Length": str(len(data))})
        try:
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()

    def health(self, port: int) -> dict | None:
        try:
            status, body = self.http("GET", "/api/health", port=port, session=False)
        except (OSError, http.client.HTTPException, ValueError):
            return None
        return body if status in (200, 503) else None

    def wait(self, what: str, fn, seconds: float = 60.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            got = fn()
            if got:
                return got
            time.sleep(0.1)
        out = (self.home / "runtime" / "server.out")
        self.fail(f"{seconds:.0f} 秒待っても {what}\n" + (out.read_text(encoding="utf-8")[-3000:] if out.exists() else ""))

    def new_instance(self, port: int, old: str, seconds: float = 60.0) -> dict:
        return self.wait("新しいインスタンスが起きない",
                         lambda: (lambda h: h if h and h["instance_id"] != old else None)(self.health(port)), seconds)

    # --- 試験 ---
    def test_restart_comes_back_on_the_same_url_with_a_new_instance(self) -> None:
        started = self.cli("start")
        self.assertTrue(started["ok"], started)
        status, body = self.http("POST", "/api/server/restart", {"mode": "now"})
        self.assertEqual(status, 202, body)
        self.assertEqual((body["same_url"], body["expected_url"], body["running_jobs"]), (True, started["url"], 0))
        h = self.new_instance(self.port, started["instance_id"])
        self.assertEqual(h["restart"]["restart_id"], body["restart_id"])
        self.assertEqual((h["restart"]["ok"], h["restart"]["fallback"], h["restart"]["message"]), (True, False, None))
        self.assertEqual(self.record()["url"], started["url"], "同じ URL")
        self.assertFalse(h["settings"]["restart_required"])

    def test_a_new_port_is_reported_and_the_new_server_is_there(self) -> None:
        started = self.cli("start")
        new_port = free_port()
        self.write_settings({"server": {"port": new_port}, "worker": {"parallel": 1}})
        self.assertTrue(self.health(self.port)["settings"]["restart_required"], "手で直したことに気づく")
        status, body = self.http("POST", "/api/server/restart", {"mode": "now"})
        self.assertEqual(status, 202, body)
        self.assertEqual((body["same_url"], body["expected_url"]), (False, f"http://127.0.0.1:{new_port}"))
        h = self.new_instance(new_port, started["instance_id"])
        self.assertEqual((h["port"], h["restart"]["fallback"]), (new_port, False))
        self.assertIsNone(self.health(self.port), "前のポートではもう待っていない")
        self.assertEqual(self.record()["url"], body["expected_url"])

    def test_an_address_this_machine_does_not_have_falls_back_to_the_last_good_settings(self) -> None:
        started = self.cli("start")
        self.write_settings({"server": {"port": self.port, "host": "203.0.113.7"}, "writer": "codex",
                             "worker": {"parallel": 1}})
        status, body = self.http("POST", "/api/server/restart", {"mode": "now"})
        self.assertEqual(status, 202, body)
        self.assertEqual(body["expected_url"], f"http://203.0.113.7:{self.port}")
        h = self.new_instance(self.port, started["instance_id"])
        self.assertEqual(self.record()["url"], started["url"], "前の URL で起動し直した")
        r = h["restart"]
        self.assertEqual((r["ok"], r["fallback"], r["restart_id"]), (True, True, body["restart_id"]))
        self.assertTrue(r["message"].startswith("新しい設定で起動できなかったので、前の設定に戻しました（"), r["message"])
        self.assertIn("203.0.113.7", r["message"])
        written = self.settings()
        self.assertEqual(written["server"]["host"], "127.0.0.1", "待ち受けの段だけを前の設定に戻す")
        self.assertEqual(written["writer"], "codex", "ほかのキーはそのまま")
        self.assertFalse(h["settings"]["restart_required"])

    def test_wait_mode_lets_the_running_job_finish_first(self) -> None:
        self.env.update({"FAKE_WRITER_MODE": "slow", "FAKE_WRITER_SLEEP": "4"})
        started = self.cli("start")
        status, intake = self.http("POST", "/api/intakes", intake_payload(mode="pitch"))
        self.assertEqual(status, 201, intake)
        self.wait("仕事が走らない", lambda: self.health(self.port)["worker"]["busy"] == 1)
        status, body = self.http("POST", "/api/server/restart", {"mode": "wait"})
        self.assertEqual((status, body["running_jobs"], body["mode"]), (202, 1, "wait"), body)
        # 待っている間は新しい仕事を受けない（古いサーバはまだ答える）
        status, refused = self.http("POST", "/api/intakes", intake_payload(mode="pitch", title="もう 1 冊"))
        self.assertEqual((status, refused["error"]["code"]), (503, "restarting"))
        h = self.health(self.port)
        self.assertEqual(h["instance_id"], started["instance_id"], "仕事が終わるまでは前のサーバのまま")
        self.assertEqual(h["restarting"]["mode"], "wait")
        self.new_instance(self.port, started["instance_id"], seconds=120)
        status, view = self.http("GET", f"/api/decks/{intake['deck_id']}/progress")
        self.assertFalse(view["live"]["working"])
        import sqlite3

        db = sqlite3.connect(f"file:{self.home / 'db' / 'quick-slide.db'}?mode=ro", uri=True)
        self.addCleanup(db.close)
        self.assertEqual(db.execute("SELECT state FROM runs WHERE id=?", (intake["run_id"],)).fetchone(), ("done",))
        self.assertEqual(len(list((self.tmp / "writer-calls").glob("*.json"))), 1)

    def test_now_mode_resumes_a_drawing_job_from_the_drawing_stage(self) -> None:
        """今すぐ: 走っている仕事の終わりを書かずに止まり、次のサーバの起動時の掃除が描く段から続ける（P2 と同じ戻し方）。"""
        self.env.update({"FAKE_CODEX_SLEEP": "3"})
        started = self.cli("start")
        status, intake = self.http("POST", "/api/intakes", intake_payload(mode="pitch"))
        self.assertEqual(status, 201, intake)
        progress = f"/api/decks/{intake['deck_id']}/progress"
        self.wait("描く段にならない", lambda: self.http("GET", progress)[1]["planning"]["state"] == "rendering")
        status, body = self.http("POST", "/api/server/restart", {"mode": "now"})
        self.assertEqual((status, body["running_jobs"]), (202, 1), body)
        self.new_instance(self.port, started["instance_id"])
        self.wait("続きが終わらない", lambda: not self.http("GET", progress)[1]["live"]["working"], seconds=180)
        import sqlite3

        db = sqlite3.connect(f"file:{self.home / 'db' / 'quick-slide.db'}?mode=ro", uri=True)
        self.addCleanup(db.close)
        self.assertEqual(db.execute("SELECT state,resume_from FROM jobs WHERE run_id=?", (intake["run_id"],)).fetchone(),
                         ("done", "render"))
        self.assertEqual(db.execute("SELECT state FROM runs WHERE id=?", (intake["run_id"],)).fetchone(), ("done",))
        self.assertEqual(len(list((self.tmp / "writer-calls").glob("*.json"))), 1, "台本係はもう一度呼ばない")

    def test_refusals_change_nothing(self) -> None:
        started = self.cli("start")
        status, body = self.http("POST", "/api/server/restart", {"mode": "later"})
        self.assertEqual((status, body["error"]["code"]), (400, "bad_restart_mode"))
        status, body = self.http("POST", "/api/server/restart", {"mode": "now"}, session=False)
        self.assertEqual((status, body["error"]["code"]), (403, "session_required"))
        self.write_settings({"server": {"port": self.port, "host": "0.0.0.0"}})
        status, body = self.http("POST", "/api/server/restart", {"mode": "now"})
        self.assertEqual((status, body["error"]["code"]), (400, "bad_bind_address"))
        (self.home / "config" / "settings.json").write_text("{ 壊れた", encoding="utf-8")
        status, body = self.http("POST", "/api/server/restart", {"mode": "now"})
        self.assertEqual((status, body["error"]["code"]), (400, "settings_unreadable"))
        h = self.health(self.port)
        self.assertEqual(h["instance_id"], started["instance_id"])
        self.assertIsNone(h["restarting"])
        self.assertIn("settings.json", h["settings"]["error"])


class RestartInThreadTests(unittest.TestCase):
    def test_a_server_running_inside_a_test_thread_does_not_restart_itself(self) -> None:
        fx = start_server(self)
        r = fx.post("/api/server/restart", {"mode": "now"})
        self.assertEqual((r.status, r.error_code), (409, "restart_unavailable"))
        self.assertFalse(fx.get("/api/settings").json()["server"]["restartable"])


if __name__ == "__main__":
    unittest.main()
