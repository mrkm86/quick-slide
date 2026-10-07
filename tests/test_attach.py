"""POST /api/launches/<id>/attach — I4（トークンは 1 回だけ消費。拒否は区別できる code。平文は応答にもログにも出ない）
と clipboard_copy の記録、GET /api/launches/<id>。"""

from __future__ import annotations

import threading
import unittest
import uuid

from helpers import capture_logs, start_server

from quick_slide.settings import LaunchSettings, Settings


class AttachTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logs = capture_logs(self)
        self.fx = start_server(self)

    def _intake(self, **over):
        """P2 では会話からの投入が作る起動を、P1 ではデッキと一緒に直接作る。"""
        deck = self.fx.make_deck()
        launch_id, token = self.fx.make_launch(deck["id"])
        return {"deck_id": deck["id"], "launch_id": launch_id}, launch_id, token

    def test_concurrent_attaches_with_the_same_token_succeed_exactly_once(self) -> None:
        body, launch_id, token = self._intake()
        n = 16
        barrier = threading.Barrier(n)
        results: list[tuple[int, str | None]] = []
        lock = threading.Lock()

        def worker(i: int) -> None:
            barrier.wait()
            r = self.fx.post(f"/api/launches/{launch_id}/attach", {"token": token, "client": {"user_agent": f"t{i}"}})
            with lock:
                results.append((r.status, r.error_code))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(len(results), n)
        successes = [s for s, _ in results if s == 200]
        self.assertEqual(len(successes), 1, results)
        for status, code in results:
            if status != 200:
                self.assertEqual((status, code), (409, "already_consumed"), results)
        launch = self.fx.store.launch(launch_id)
        self.assertEqual(launch["state"], "attached")
        kinds = [e["kind"] for e in self.fx.store.events(deck_id=body["deck_id"])]
        self.assertEqual(kinds.count("launch_attached"), 1)
        self.assertEqual(kinds.count("launch_attach_rejected"), n - 1, "拒否は全部 events に残る")

    def test_rejections_are_distinguishable_and_do_not_consume(self) -> None:
        body, launch_id, token = self._intake()
        other, other_launch, other_token = self._intake()
        # 未知の launch
        r = self.fx.post(f"/api/launches/{uuid.uuid4()}/attach", {"token": token})
        self.assertEqual((r.status, r.error_code), (404, "launch_not_found"))
        # 存在する launch に別のトークン → 何も消費しない
        r = self.fx.post(f"/api/launches/{launch_id}/attach", {"token": other_token})
        self.assertEqual((r.status, r.error_code), (403, "token_mismatch"))
        self.assertEqual(self.fx.store.launch(launch_id)["state"], "pending")
        self.assertEqual(self.fx.store.launch(other_launch)["state"], "pending", "他の launch のトークンを消費していない")
        r = self.fx.post(f"/api/launches/{launch_id}/attach", {"token": "not-a-real-token"})
        self.assertEqual((r.status, r.error_code), (403, "token_mismatch"))
        # client にトークン平文を紛れ込ませる要求は拒否（client_json は永続化される）
        r = self.fx.post(f"/api/launches/{launch_id}/attach", {"token": token, "client": {"user_agent": f"x {token} y"}})
        self.assertEqual((r.status, r.error_code), (400, "token_in_client"))
        self.assertEqual(self.fx.store.launch(launch_id)["state"], "pending")
        # トークン無し
        r = self.fx.post(f"/api/launches/{launch_id}/attach", {"client": {}})
        self.assertEqual((r.status, r.error_code), (400, "token_required"))
        # client の未知キーは拒否（store の検査）
        r = self.fx.post(f"/api/launches/{launch_id}/attach", {"token": token, "client": {"cookie": "x"}})
        self.assertEqual((r.status, r.error_code), (400, "validation_error"))
        self.assertEqual(self.fx.store.launch(launch_id)["state"], "pending", "検証で落ちた要求は消費しない")
        # 正しい attach → 1 回目成功、2 回目 already_consumed
        r = self.fx.post(f"/api/launches/{launch_id}/attach", {"token": token, "client": {"user_agent": "ua", "clipboard": {"write": False}}})
        self.assertEqual(r.status, 200, r)
        out = r.json()
        self.assertEqual((out["launch_id"], out["deck_id"], out["state"], out["attached_via"]), (launch_id, body["deck_id"], "attached", "unknown"))
        self.assertIsNotNone(out["attached_at"])
        self.assertEqual(out["client"], {"user_agent": "ua", "clipboard": {"write": False}})
        r = self.fx.post(f"/api/launches/{launch_id}/attach", {"token": token})
        self.assertEqual((r.status, r.error_code), (409, "already_consumed"))
        # 期限切れ（15 分 + 1 秒後）
        self.fx.clock.advance(15 * 60 + 1)
        r = self.fx.post(f"/api/launches/{other_launch}/attach", {"token": other_token})
        self.assertEqual((r.status, r.error_code), (410, "expired"))
        self.assertEqual(self.fx.store.launch(other_launch)["state"], "expired")
        reasons = [e["payload"]["reason"] for e in self.fx.store.events(kinds=("launch_attach_rejected",))]
        self.assertEqual(sorted(set(reasons)), ["already_consumed", "expired", "token_mismatch", "unknown_launch"])

    def test_token_plaintext_never_appears_in_responses_logs_or_files(self) -> None:
        body, launch_id, token = self._intake()
        tok = token.encode("utf-8")
        self.fx.get(f"/decks/{body['deck_id']}?launch={launch_id}&token={token}")
        self.fx.get(f"/slides/{body['deck_id']}?launch={launch_id}&token={token}")
        self.fx.get(f"/api/launches/{launch_id}")
        self.fx.post(f"/api/launches/{launch_id}/attach", {"token": token})
        self.fx.post(f"/api/launches/{launch_id}/attach", {"token": token})
        self.fx.get("/api/health")
        for b in self.fx.responses:
            if b.startswith(b"HTTP/") or b == b"":
                continue
            self.assertNotIn(tok, b, "トークン平文が応答に出ている")
        self.assertNotIn(token, self.logs.text(), "トークン平文がログに出ている（アクセスログはクエリを落とす）")
        persisted = b"\n".join(self.fx.all_files().values())
        self.assertNotIn(tok, persisted, "トークン平文が DB / runtime / server.json のどこかに書かれている")
        r = self.fx.get(f"/api/launches/{launch_id}")
        self.assertNotIn("token_hash", r.json())

    def test_launch_get_reflects_store_state(self) -> None:
        body, launch_id, token = self._intake()
        r = self.fx.get(f"/api/launches/{launch_id}")
        self.assertEqual(r.status, 200)
        self.assertEqual({k: r.json()[k] for k in ("id", "deck_id", "state", "requested_via", "attached_at")},
                         {"id": launch_id, "deck_id": body["deck_id"], "state": "pending", "requested_via": "start", "attached_at": None})
        self.assertEqual(self.fx.get(f"/api/launches/{uuid.uuid4()}").error_code, "launch_not_found")
        self.assertEqual(self.fx.get(f"/api/launches/{launch_id.upper()}").status, 404)

    def test_short_ttl_from_settings_is_respected(self) -> None:
        fx = start_server(self, settings=Settings(launch=LaunchSettings(token_ttl_seconds=30)))
        launch_id, token = fx.make_launch(fx.make_deck()["id"])
        fx.clock.advance(31)
        r = fx.post(f"/api/launches/{launch_id}/attach", {"token": token})
        self.assertEqual((r.status, r.error_code), (410, "expired"))


class ClipboardCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = start_server(self)

    def test_records_event_linked_to_launch_and_derives_deck_from_the_row(self) -> None:
        deck = self.fx.make_deck()
        launch_id, _token = self.fx.make_launch(deck["id"])
        body = {"deck_id": deck["id"], "launch_id": launch_id}
        r = self.fx.post("/api/events/clipboard-copy", {"launch_id": body["launch_id"], "method": "api", "ok": False, "error_name": "NotAllowedError"})
        self.assertEqual(r.status, 201, r)
        self.assertEqual(r.json(), {"recorded": True, "launch_id": body["launch_id"], "deck_id": body["deck_id"]})
        events = self.fx.store.events(kinds=("clipboard_copy",))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["deck_id"], body["deck_id"])
        self.assertEqual(events[0]["payload"], {"launch_id": body["launch_id"], "method": "api", "ok": False, "error_name": "NotAllowedError"})
        # launch 無しでも記録できる（ホームでのコピー等）
        r = self.fx.post("/api/events/clipboard-copy", {"launch_id": None, "method": "save", "ok": True})
        self.assertEqual(r.status, 201)
        self.assertEqual(len(self.fx.store.events(kinds=("clipboard_copy",))), 2)

    def test_validation(self) -> None:
        for bad, code in (
            ({"method": "telepathy", "ok": True}, "validation_error"),
            ({"method": "api", "ok": "yes"}, "validation_error"),
            ({"method": "api", "ok": True, "error_name": "x" * 201}, "validation_error"),
            ({"method": "api", "ok": True, "launch_id": str(uuid.uuid4())}, "launch_not_found"),
        ):
            r = self.fx.post("/api/events/clipboard-copy", bad)
            self.assertEqual(r.error_code, code, (bad, r))
        self.assertEqual(len(self.fx.store.events(kinds=("clipboard_copy",))), 0, "拒否した要求は何も記録しない")


if __name__ == "__main__":
    unittest.main()
