"""停止と DB を使う HTTP 要求の競合を、イベントで順序を固定して確かめる。"""
import http.client
import threading
import unittest
from unittest.mock import patch

from helpers import ServerFixture, temp_dir
from quick_slide.server.app import Response


class ShutdownRequestsTests(unittest.TestCase):
    def test_close_waits_for_requests_and_rejects_late_requests(self):
        fx = ServerFixture(temp_dir(self), start_workers=False)
        entered = threading.Barrier(5)
        release = threading.Event()
        closed = threading.Event()
        results = []
        errors = []
        store = fx.store
        httpd = fx.server._httpd

        def route(req):
            entered.wait(timeout=5)
            if not release.wait(5):
                raise RuntimeError("request was not released")
            # close がここより前に接続を閉じると失敗する。
            with fx.server.store_lock:
                value = store.conn.execute("SELECT 1").fetchone()[0]
            return Response.json(200, {"value": value})

        def request():
            try:
                results.append(fx.request("GET", "/api/decks"))
            except BaseException as exc:
                errors.append(exc)

        def close():
            try:
                fx.server.close()
            except BaseException as exc:
                errors.append(exc)
            finally:
                closed.set()

        clients = []
        closer = None
        try:
            with patch.object(fx.server, "_route", side_effect=route):
                for _ in range(4):
                    t = threading.Thread(target=request)
                    clients.append(t)
                    t.start()
                entered.wait(timeout=5)
                closer = threading.Thread(target=close)
                closer.start()
                self.assertTrue(fx.server._stopping.wait(5))
                self.assertFalse(closed.is_set())
                late = fx.request("GET", "/api/decks")
                self.assertEqual(late.status, 503)
                self.assertEqual(late.error_code, "server_stopping")
                self.assertEqual(late.headers.get("connection"), "close")
                release.set()
                for t in clients:
                    t.join(5)
                    self.assertFalse(t.is_alive())
                closer.join(5)
                self.assertFalse(closer.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 4)
            self.assertTrue(all(r.status == 200 for r in results))
            self.assertIsNone(fx.server.store)
        finally:
            release.set()
            for t in clients:
                t.join(5)
            if closer is not None:
                closer.join(5)
            httpd.shutdown()
            fx.close()

    def test_stop_rejects_request_on_keep_alive_connection(self):
        fx = ServerFixture(temp_dir(self), start_workers=False)
        conn = http.client.HTTPConnection("127.0.0.1", fx.port, timeout=5)
        try:
            conn.request("GET", "/api/session")
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            fx.server._stopping.set()
            conn.request("GET", "/api/decks")
            response = conn.getresponse()
            self.assertEqual(response.status, 503)
            response.read()
        finally:
            conn.close()
            fx.close()

    def test_idle_keep_alive_does_not_delay_close(self):
        fx = ServerFixture(temp_dir(self), start_workers=False)
        conn = http.client.HTTPConnection("127.0.0.1", fx.port, timeout=5)
        finished = threading.Event()

        def close():
            try:
                fx.close()
            finally:
                finished.set()

        closer = threading.Thread(target=close)
        try:
            conn.request("GET", "/api/session")
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            closer.start()
            self.assertTrue(finished.wait(5), "idle connection delayed shutdown")
        finally:
            conn.close()
            if closer.ident is not None:
                closer.join(5)
            fx.close()

    def test_failed_dispatch_releases_request(self):
        fx = ServerFixture(temp_dir(self), start_workers=False)
        try:
            with patch.object(fx.server, "_dispatch", side_effect=RuntimeError("failed")):
                with self.assertRaises(RuntimeError):
                    fx.server.dispatch(None)
            self.assertEqual(fx.server._active_requests, 0)
        finally:
            fx.close()
