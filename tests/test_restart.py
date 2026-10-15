"""サーバが描いている途中で落ちても、仕事は「働いている」のまま残らない: 起こし直すと描くところから続け、
取り込み終えた回だけが画面に出る。

本物のサーバのプロセス（`python -m quick_slide.server start`）を描いている最中に SIGKILL で落とし、起こし直す。
エンジンは本物の image-slide（絵を描く codex だけ偽物）、台本係は偽物の claude。
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import unittest

from helpers import FAKES, REPO, SKILLS_DIR, fake_codex_home, intake_payload, runnable, temp_dir

from quick_slide.oscompat import pid_alive


class KillAndRestartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temp_dir(self, "quick-slide-restart-")
        self.home = self.tmp / "home"
        (self.home / "config").mkdir(parents=True)
        (self.home / "config" / "settings.json").write_text(json.dumps({"server": {"port": 0}, "worker": {"parallel": 1}}), encoding="utf-8")
        self.env = {
            **os.environ, "PYTHONPATH": str(REPO / "src"), "PYTHONDONTWRITEBYTECODE": "1", "QUICK_SLIDE_BROWSER": "manual",
            "QUICK_SLIDE_CLAUDE": runnable(FAKES / "fake_claude.py"), "QUICK_SLIDE_CODEX_TEXT": runnable(FAKES / "refuse.py"),
            "FAKE_WRITER_LOG": str(self.tmp / "writer-calls"),
            "IMAGE_SLIDE_CODEX": runnable(SKILLS_DIR / "image-slide" / "tests" / "fake_codex.py"),
            "FAKE_CODEX_LOG": str(self.tmp / "codex-calls"), "FAKE_CODEX_SLEEP": "3",
            "CODEX_HOME": str(fake_codex_home(self.tmp)), "IMAGE_SLIDE_CACHE": str(self.tmp / "image-slide-cache"),
            "IMAGE_SLIDE_CONFIG": str(self.tmp / "no-config.yaml"),
        }
        self.addCleanup(self.cli, "stop")

    def cli(self, *args: str) -> dict:
        r = subprocess.run([sys.executable, "-m", "quick_slide.server", "--home", str(self.home), *args],
                           env=self.env, capture_output=True, text=True, timeout=60, cwd=str(REPO), encoding="utf-8")
        return json.loads(r.stdout.strip().splitlines()[-1])

    def assert_started(self) -> None:
        result = self.cli("start")
        if not result.get("ok"):
            logs = []
            for name in ("server.out", "server.log"):
                path = self.home / "runtime" / name
                if path.exists():
                    logs.append(f"{name}:\n{path.read_text(encoding='utf-8', errors='replace')[-6000:]}")
            self.fail(f"サーバを起動できませんでした: {result}\n" + "\n".join(logs))

    def record(self) -> dict:
        return json.loads((self.home / "runtime" / "server.json").read_text(encoding="utf-8"))

    def http(self, method: str, path: str, body=None):
        rec = self.record()
        conn = http.client.HTTPConnection("127.0.0.1", rec["port"], timeout=10)
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Host": f"127.0.0.1:{rec['port']}", "X-Quick-Slide-Session": rec["session_token"]}
        if data is not None:
            headers.update({"Content-Type": "application/json", "Content-Length": str(len(data))})
        try:
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()

    def wait(self, what: str, fn, seconds: float = 120.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            got = fn()
            if got:
                return got
            time.sleep(0.1)
        self.fail(f"{seconds:.0f} 秒待っても {what}")

    def test_killed_while_drawing_resumes_from_the_drawing_stage(self) -> None:
        self.assert_started()
        status, body = self.http("POST", "/api/intakes", intake_payload(mode="pitch"))
        self.assertEqual(status, 201, body)
        deck_id, run_id = body["deck_id"], body["run_id"]
        progress = f"/api/decks/{deck_id}/progress"
        self.wait("描く段にならない", lambda: self.http("GET", progress)[1]["planning"]["state"] == "rendering")
        self.assertEqual(self.http("GET", progress)[1]["pages"], [], "取り込む前の回は画面に出さない")
        pid = self.record()["pid"]
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))   # Windows の SIGTERM は TerminateProcess（強制終了）
        self.wait("サーバが落ちない", lambda: not pid_alive(pid))
        # 置き去りのエンジン（別のセッションで動いている）は、出力の口が切れたところで止まる。少し待ってから起こし直す
        time.sleep(4)

        self.assert_started()
        self.wait("続きが終わらない", lambda: not self.http("GET", progress)[1]["live"]["working"], seconds=180)
        status, view = self.http("GET", f"/api/decks/{deck_id}")
        self.assertEqual(len(view["pages"]), 4)
        self.assertTrue(all(p["display"]["key"] == "ready" and p["image_url"] for p in view["pages"]), view["pages"])
        self.assertIsNone(view["planning"]["message"])
        db = sqlite3.connect(f"file:{self.home / 'db' / 'quick-slide.db'}?mode=ro", uri=True)
        self.addCleanup(db.close)
        job = db.execute("SELECT state,resume_from,attempts FROM jobs WHERE run_id=?", (run_id,)).fetchone()
        self.assertEqual(job, ("done", "render", 2))
        kinds = [r[0] for r in db.execute("SELECT kind FROM events WHERE deck_id=? ORDER BY seq", (deck_id,))]
        self.assertIn("job_requeued", kinds)
        self.assertEqual(len(list((self.tmp / "writer-calls").glob("*.json"))), 1, "台本係はもう一度呼ばない")


if __name__ == "__main__":
    unittest.main()
