"""常駐の仕組み — server.json、二重起動の防止、生存の証拠つき status、起動時掃除の継続、
実プロセスでの start → health → 静的配信 → attach → stop、稼働時間の時計。"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import unittest

from helpers import REPO, SRC, capture_logs, make_deck, start_server, temp_dir

from quick_slide.errors import StartupError
from quick_slide.paths import DataRoot
from quick_slide.server import QuickSlideServer, read_record, status
from quick_slide.server.runtime import ServerRecord, write_record
from quick_slide.settings import Settings
from quick_slide.store import Store


class ServerRecordAndStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temp_dir(self)

    def test_server_json_is_written_0600_with_pid_start_and_instance_and_removed_on_close(self) -> None:
        fx = start_server(self)
        rec = read_record(fx.root)
        self.assertIsNotNone(rec)
        self.assertEqual((rec.pid, rec.instance_id, rec.port, rec.host), (os.getpid(), fx.server.instance_id, fx.port, "127.0.0.1"))
        self.assertEqual(rec.started_at, fx.server.started_at)
        self.assertEqual(rec.url, f"http://127.0.0.1:{fx.port}")
        self.assertEqual(fx.root.server_json_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(rec.session_token, fx.session())
        st = status(fx.root)
        self.assertTrue(st["running"], st)
        self.assertEqual((st["pid"], st["instance_id"], st["health"]["instance_id"]),
                         (os.getpid(), fx.server.instance_id, fx.server.instance_id))
        self.assertNotIn("session_token", json.dumps(st))
        fx.close()
        self.assertFalse(fx.root.server_json_path.exists())
        self.assertEqual(status(fx.root)["reason"], "no_server_json")

    def test_status_never_says_running_without_proof(self) -> None:
        root = DataRoot(self.tmp / "home")
        root.ensure()
        # 死んだ PID
        write_record(root, ServerRecord(instance_id="i-dead", pid=2_000_000_000, started_at="x", host="127.0.0.1",
                                        port=1, url="http://127.0.0.1:1", session_token="s", version="v"))
        self.assertEqual(status(root)["reason"], "stale_pid")
        # 生きている PID（自分）だが何も聞いていないポート
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
        s.close()
        write_record(root, ServerRecord(instance_id="i-mine", pid=os.getpid(), started_at="x", host="127.0.0.1",
                                        port=free_port, url="u", session_token="s", version="v"))
        self.assertEqual(status(root, timeout=1.0)["reason"], "unreachable")
        # 別インスタンスが応答している（記録が古い）
        fx = start_server(self)
        write_record(fx.root, ServerRecord(instance_id="i-other", pid=os.getpid(), started_at="x", host="127.0.0.1",
                                           port=fx.port, url="u", session_token="s", version="v"))
        st = status(fx.root)
        self.assertEqual((st["running"], st["reason"], st["responding_instance_id"]),
                         (False, "instance_mismatch", fx.server.instance_id))
        # 壊れた server.json
        fx.root.server_json_path.write_text("{not json", encoding="utf-8")
        self.assertEqual(status(fx.root)["reason"], "server_json_corrupt")
        fx.root.server_json_path.write_text(json.dumps({"pid": "x"}), encoding="utf-8")
        self.assertEqual(status(fx.root)["reason"], "server_json_corrupt")
        # close は他インスタンスの記録を消さない
        write_record(fx.root, ServerRecord(instance_id="i-other", pid=os.getpid(), started_at="x", host="127.0.0.1",
                                           port=fx.port, url="u", session_token="s", version="v"))
        fx.close()
        self.assertTrue(fx.root.server_json_path.exists(), "自分の記録でなければ消さない")

    def test_double_start_on_the_same_home_is_refused(self) -> None:
        fx = start_server(self)
        with self.assertRaises(StartupError) as ctx:
            QuickSlideServer(fx.root, settings=Settings(), port=0).start()
        self.assertIn("既に起動", str(ctx.exception))
        self.assertTrue(status(fx.root)["running"], "先客は動き続けている")
        self.assertEqual(read_record(fx.root).instance_id, fx.server.instance_id, "先客の記録が上書きされていない")
        # 先客が止まれば起動できる
        fx.close()
        second = QuickSlideServer(fx.root, settings=Settings(), port=0)
        second.start_in_thread()
        self.addCleanup(second.close)
        self.addCleanup(second.shutdown)
        self.assertTrue(status(fx.root)["running"])

    def test_port_in_use_by_another_process_is_explained_not_switched(self) -> None:
        blocker = socket.socket()
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        self.addCleanup(blocker.close)
        port = blocker.getsockname()[1]
        with self.assertRaises(StartupError) as ctx:
            QuickSlideServer(DataRoot(self.tmp / "home3"), settings=Settings(), port=port).start()
        self.assertIn(str(port), str(ctx.exception))
        self.assertIn("勝手に切り替えることはしません", str(ctx.exception))
        self.assertFalse((self.tmp / "home3" / "runtime" / "server.json").exists())

    def test_default_port_is_7801(self) -> None:
        self.assertEqual(Settings().server.port, 7801)

    def test_health_reports_db_failure_as_failure(self) -> None:
        fx = start_server(self)
        self.assertEqual(fx.get("/api/health").json()["ok"], True)
        fx.store.conn.close()  # DB を壊す
        r = fx.get("/api/health")
        self.assertEqual(r.status, 503)
        h = r.json()
        self.assertEqual((h["ok"], h["db"]["ok"]), (False, False))
        self.assertIsNotNone(h["db"]["error_class"])
        fx.server.store = None  # close 時に二重に閉じない


class StartupSweepTests(unittest.TestCase):
    def test_expired_launches_are_swept_at_startup(self) -> None:
        from quick_slide.clock import FixedClock

        tmp = temp_dir(self)
        root = DataRoot(tmp / "home")
        store = Store.open(root, settings=Settings(), clock=FixedClock("2026-09-07T00:00:00.000000Z"))
        launch = store.create_launch(requested_via="start", deck_id=make_deck(store)["id"])
        store.close()
        # 期限（15 分）を過ぎてから起動する
        server = QuickSlideServer(root, settings=Settings(), port=0, clock=FixedClock("2026-09-07T01:00:00.000000Z"))
        server.start_in_thread()
        self.addCleanup(server.close)
        self.addCleanup(server.shutdown)
        self.assertEqual(server.sweep_summary["launches_expired"], 1)
        self.assertTrue(server.sweep_summary["ok"])
        self.assertEqual(server.store.launch(launch["id"])["state"], "expired")

    def test_a_failing_sweep_does_not_prevent_startup_and_is_reported_as_failed(self) -> None:
        from quick_slide.server.app import run_startup_sweep

        store = Store.open(DataRoot(temp_dir(self) / "home2"), settings=Settings())
        self.addCleanup(store.close)

        def boom():
            raise OSError("disk")

        store.expire_launches = boom  # type: ignore[assignment]
        logs = capture_logs(self)
        summary = run_startup_sweep(store)
        self.assertIn("起動時掃除 launches_expired が失敗", logs.text())
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["failed_sweeps"], [{"sweep": "launches_expired", "error_class": "OSError"}])
        self.assertIsNone(summary["launches_expired"], "失敗した掃除の件数は 0 ではなく null（測っていない）")


class RealProcessLifecycleTests(unittest.TestCase):
    """実プロセス: start（デタッチ）→ status → 二重 start → health → 静的配信 → attach → stop。stdout/stderr に秘匿値が無い。"""

    def setUp(self) -> None:
        self.home = temp_dir(self, "quick-slide-proc-") / "home"
        self.env = dict(os.environ, PYTHONPATH=str(SRC), QUICK_SLIDE_HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1")
        self.env.pop("QUICK_SLIDE_BROWSER", None)
        self.addCleanup(self._kill_leftover)

    def _kill_leftover(self) -> None:
        try:
            rec = read_record(DataRoot(self.home))
        except ValueError:
            rec = None
        if rec is not None:
            try:
                os.kill(rec.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def _cli(self, *args: str, timeout: float = 30) -> tuple[int, dict]:
        proc = subprocess.run([sys.executable, "-m", "quick_slide.server", *args], env=self.env, cwd=str(REPO),
                              capture_output=True, text=True, timeout=timeout)
        last = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "{}"
        return proc.returncode, json.loads(last)

    def _http(self, port: int, method: str, path: str, body=None, headers=None):
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        h = {"Host": f"127.0.0.1:{port}"}
        if data is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        out = (resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read())
        conn.close()
        return out

    def test_full_lifecycle(self) -> None:
        # 起動前に、デッキと起動（会話から来たときに作られるもの）を用意しておく
        store = Store.open(DataRoot(self.home), settings=Settings())
        deck = make_deck(store)
        launch = store.create_launch(requested_via="start", deck_id=deck["id"])
        launch_id, token = launch["id"], launch["token"]
        store.close()

        rc, started = self._cli("start", "--port", "0")
        self.assertEqual((rc, started.get("ok"), started.get("already_running")), (0, True, False), started)
        pid, url = started["pid"], started["url"]
        port = int(url.rsplit(":", 1)[1])
        self.assertNotEqual(pid, os.getpid())
        try:
            rc, st = self._cli("status")
            self.assertEqual((rc, st["running"], st["pid"]), (0, True, pid), st)
            # 二重起動: CLI は already_running、直接起動は StartupError
            rc, again = self._cli("start", "--port", "0")
            self.assertEqual((rc, again["already_running"], again["pid"]), (0, True, pid))
            with self.assertRaises(StartupError):
                QuickSlideServer(DataRoot(self.home), settings=Settings(), port=0).start()
            rec = read_record(DataRoot(self.home))
            session = rec.session_token
            self.assertEqual(rec.pid, pid)
            # health
            s, _, b = self._http(port, "GET", "/api/health")
            self.assertEqual(s, 200)
            self.assertTrue(json.loads(b)["ok"])
            # 静的配信
            s, h, b = self._http(port, "GET", f"/slides/{deck['id']}?launch={launch_id}&token={token}")
            self.assertEqual((s, h["content-type"]), (200, "text/html; charset=utf-8"))
            self.assertIn(b"Quick Slide", b)
            self.assertEqual(self._http(port, "GET", "/static/app.js")[0], 200)
            self.assertEqual(self._http(port, "GET", "/static/../config/hmac.key")[0], 404)
            # attach → 1 回目 200、2 回目 409
            s, _, b = self._http(port, "POST", f"/api/launches/{launch_id}/attach",
                                 {"token": token, "client": {"user_agent": "test"}}, {"X-Quick-Slide-Session": session})
            self.assertEqual(s, 200, b)
            s, _, b = self._http(port, "POST", f"/api/launches/{launch_id}/attach", {"token": token},
                                 {"X-Quick-Slide-Session": session})
            self.assertEqual((s, json.loads(b)["error"]["code"]), (409, "already_consumed"))
            # まだ無い口は「まだ使えない」と答える（成功したふりをしない）
            s, _, b = self._http(port, "POST", "/api/conversations/deck", {"origin": {"kind": "conversation"}},
                                 {"X-Quick-Slide-Session": session})
            self.assertEqual((s, json.loads(b)["error"]["code"]), (503, "not_ready_in_v2"))
            # 取り込みは形を検査する（原稿だけの不完全な形は断る。何も作らない）
            s, _, b = self._http(port, "POST", "/api/intakes", {"manuscript": "x"}, {"X-Quick-Slide-Session": session})
            self.assertEqual((s, json.loads(b)["error"]["code"]), (400, "validation_error"))
        finally:
            rc, stopped = self._cli("stop")
        self.assertEqual((rc, stopped.get("stopped")), (0, True), stopped)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and (self.home / "runtime" / "server.json").exists():
            time.sleep(0.05)
        self.assertFalse((self.home / "runtime" / "server.json").exists())
        rc, st = self._cli("status")
        self.assertEqual((rc, st["running"], st["reason"]), (3, False, "no_server_json"))
        rc, stopped = self._cli("stop")
        self.assertEqual((rc, stopped["stopped"], stopped["reason"]), (0, False, "not_running"))
        # 子プロセスの stdout/stderr とログに、トークン平文・トレースバックが無い
        out = (self.home / "runtime" / "server.out").read_text(encoding="utf-8", errors="replace")
        log = (self.home / "runtime" / "server.log").read_text(encoding="utf-8", errors="replace")
        for text, name in ((out, "server.out"), (log, "server.log")):
            self.assertNotIn(token, text, name)
            self.assertNotIn("Traceback", text, name)
        self.assertIn("起動 instance=", log)
        self.assertIn("停止 instance=", log)
        self.assertIn(f"GET /slides/{deck['id']} 200", log, "アクセスログはパスだけ（クエリ無し）")
        self.assertNotIn("token=", log)
        for p in (self.home / "runtime" / "server.out", self.home / "runtime" / "server.log"):
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        # エンジンの置き場はサーバが作らない
        self.assertFalse((self.home / "engines").exists())


class UptimeCountsTheTimeTheMachineWasAsleepTests(unittest.TestCase):
    """稼働時間は**眠っていた分も数える**。`time.monotonic()` は macOS では眠っている間止まる。
    **壁時計で直さない**（時刻合わせで戻るので、経過時間が負になり得る）。"""

    def test_the_chosen_clock_does_not_stop_while_the_machine_sleeps(self):
        """選んだ時計は「起動してからの壁時計の経過」と揃う（眠っていた分も入っている）。

        `time.monotonic()` と大小を比べる形にはしない: macOS の CLOCK_MONOTONIC は眠っていない機械でも
        `mach_absolute_time()` より数秒遅れることがあり（実測）、大小では眠った分の有無を言えない。
        """
        import re

        from quick_slide.clock import elapsed_clock

        if sys.platform != "darwin":
            self.skipTest("起動時刻の読み方は macOS だけ")
        out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True).stdout
        m = re.search(r"sec = (\d+)", out)
        if m is None:
            self.skipTest("起動時刻を読めない")
        name, read = elapsed_clock()
        since_boot = time.time() - int(m.group(1))
        self.assertLess(abs(read() - since_boot), 120, f"{name} が、起動からの経過（眠っていた分を含む）と食い違う")

    def test_it_is_not_the_wall_clock(self):
        from quick_slide.clock import elapsed_clock

        name, read = elapsed_clock()
        self.assertNotIn("REALTIME", name, "壁時計は時刻合わせで戻るので経過時間には使えない")
        first = read()
        self.assertGreaterEqual(read(), first, "読むたびに戻る時計を選んでいる")

    def test_health_says_which_clock_it_measured_with(self):
        fx = start_server(self)
        health = fx.get("/api/health").json()
        self.assertGreater(health["uptime_seconds"], 0)
        self.assertTrue(health["uptime_clock"], "どの時計で測ったかを言っていない")

    def test_the_server_does_not_read_the_stopping_clock_for_uptime(self):
        source = (SRC / "quick_slide" / "server" / "app.py").read_text(encoding="utf-8")
        line = next(ln for ln in source.splitlines() if '"uptime_seconds"' in ln)
        self.assertNotIn("time.monotonic()", line, "稼働時間を、眠っている間止まる時計で測っている")


if __name__ == "__main__":
    unittest.main()
