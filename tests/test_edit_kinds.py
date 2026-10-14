"""直すときの「何を直しますか？」（docs/adr/0008-edit-kinds.md）と、画像の URL からの取り込み。

- 出す種類と選択肢はモードと機械で決まる（できないものは出さない）。送られた種類はサーバも確かめる
- 文字以外は書かなくても送れ、選んだ方針が台本係への「直すもの」の節になる
- URL の取り込みは公開のアドレスだけ。自機・社内のアドレスは、転送された先でも断る
"""

from __future__ import annotations

import http.server
import io
import threading
import unittest
from unittest import mock

from PIL import Image

from helpers import wait_until_idle, writer_calls

from quick_slide import edit_kinds as E
from quick_slide.server import image_fetch as F
from quick_slide.server.errors import ApiError

from p3_edit_helpers import EditBase


class OfferedTests(unittest.TestCase):
    def kinds(self, mode: str, can_generate: bool = True) -> list[str]:
        return [k["kind"] for k in E.offered(mode, can_generate=can_generate)]

    def test_each_mode_offers_only_what_it_can(self) -> None:
        self.assertEqual(self.kinds("presentation"), ["text", "diagram", "illustration", "image"], "プレゼンの挿絵は SVG（ADR 0011）")
        self.assertEqual(self.kinds("pitch"), ["text", "diagram", "illustration", "image"])
        self.assertEqual(self.kinds("motion"), ["text", "diagram", "illustration"], "モーションは画像を持たない")

    def test_image_is_offered_whatever_the_deck_said_at_creation(self) -> None:
        """「画像を使わない」は作るときだけの指定。直しでは機械（Codex の有無）だけで決まる（docs/adr/0010）。"""
        self.assertIn("image", [k["kind"] for k in E.offered("presentation", can_generate=False)])

    def test_generate_is_hidden_without_codex(self) -> None:
        def sources(can: bool) -> list[str]:
            image = next(k for k in E.offered("presentation", can_generate=can) if k["kind"] == "image")
            return [o["value"] for o in image["options"]]
        self.assertEqual(sources(True), ["generate", "attached"])
        self.assertEqual(sources(False), ["attached"])

    def test_parse_checks_kind_and_options(self) -> None:
        self.assertEqual(E.parse("pitch", None, None, can_generate=True), (None, []))
        self.assertEqual(E.parse("pitch", "diagram", None, can_generate=True), ("diagram", ["auto"]), "1 つ選ぶものは先頭が既定")
        self.assertEqual(E.parse("pitch", "text", ["short", "firm"], can_generate=True), ("text", ["short", "firm"]))
        for kind, options, can in (("image", [], True), ("nazo", [], True)):
            with self.assertRaises(ValueError):
                E.parse("motion", kind, options, can_generate=can)
        bad = (("diagram", ["flow", "compare"]), ("diagram", ["nazo"]), ("text", ["short", "short"]), ("text", "short"))
        for kind, options in bad:
            with self.assertRaises(ValueError, msg=(kind, options)):
                E.parse("pitch", kind, options, can_generate=True)
        with self.assertRaises(ValueError, msg="Codex が無いのに生成を送った"):
            E.parse("presentation", "image", ["generate"], can_generate=False)

    def test_missing_and_direction(self) -> None:
        self.assertEqual(E.missing("text", [], text="", attachments=0), E.TEXT_REQUIRED)
        self.assertIsNone(E.missing("text", ["short"], text="", attachments=0))
        self.assertIsNone(E.missing("diagram", ["auto"], text="", attachments=0), "文字以外は選ぶだけで送れる")
        self.assertEqual(E.missing("image", ["attached"], text="", attachments=0), E.ATTACHMENT_REQUIRED["attached"])
        self.assertIsNone(E.missing("image", ["attached"], text="", attachments=1))
        lines = E.direction("presentation", "diagram", ["flow"], scope="deck")
        self.assertIn("スライド全体", lines[0])
        self.assertTrue(any("flow" in ln for ln in lines), lines)
        self.assertEqual(E.direction("pitch", None, []), [])
        self.assertEqual(E.summary("diagram", ["auto"]), "図解")
        self.assertEqual(E.summary("text", ["short", "numbers"]), "文字（短くする・数字を先に）")


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 3), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/pic.png":
            body = _png()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/to-private":
            self.send_response(302)
            self.send_header("Location", "http://10.0.0.1/pic.png")
            self.end_headers()
        elif self.path == "/to-pic":
            self.send_response(302)
            self.send_header("Location", "/pic.png")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args) -> None:
        pass


class ImagesFollowTheMachineTests(unittest.TestCase):
    """生成の画像を使えるかは、頼んだときの機械で決める。図解をできるだけ入れる（docs/adr/0010）。"""

    def test_edit_args_follow_the_machine(self) -> None:
        from quick_slide.worker.edit import _edit_args
        made = {"mode": "presentation", "images": False}
        self.assertTrue(_edit_args(made, {"can_generate": True})["images"], "作るときの指定は直しに効かせない")
        self.assertFalse(_edit_args({"mode": "presentation", "images": True}, {"can_generate": False})["images"])
        self.assertIs(_edit_args(made, {}), made, "前の形の頼みはそのまま")
        self.assertFalse(_edit_args({"mode": "motion", "images": False}, {"can_generate": True})["images"], "モーションは画像を持たない")

    def test_prompts_ask_for_diagrams_and_allow_given_pictures(self) -> None:
        from pathlib import Path
        from quick_slide.engines import ENGINES
        from quick_slide.writer import prompt as P
        for mode in ("presentation", "pitch", "motion"):
            self.assertIn(P.DIAGRAM_RULE[mode], P._rules(ENGINES[mode], {"mode": mode, "images": True}), mode)
        rules = P._edit_rules(ENGINES["presentation"], {"mode": "presentation", "images": False})
        self.assertTrue(any("path" in r and "生成の画像の枠" in r for r in rules), rules)


class FetchTests(unittest.TestCase):
    def test_refuses_non_http_and_non_public_addresses(self) -> None:
        for url in ("file:///etc/hosts", "ftp://example.com/a.png", "", 3, "http://", "http://example.com:nan/"):
            with self.assertRaises(ApiError, msg=url) as cm:
                F.download(url, max_bytes=100)
            self.assertEqual(cm.exception.code, "image_url_invalid")
        for addresses in (["127.0.0.1"], ["10.1.2.3"], ["192.168.0.5"], ["169.254.169.254"], ["::1"], ["fd00::1"],
                          ["93.184.216.34", "10.0.0.1"]):
            with self.assertRaises(ApiError, msg=addresses) as cm:
                F.public_address("example.test", 80, resolve=lambda h, p, a=addresses: a)
            self.assertEqual(cm.exception.code, "image_url_not_public")
        self.assertEqual(F.public_address("example.test", 80, resolve=lambda h, p: ["93.184.216.34"]), "93.184.216.34")

    def test_loopback_url_is_refused_for_real(self) -> None:
        with self.assertRaises(ApiError) as cm:
            F.download("http://127.0.0.1:9/pic.png", max_bytes=100)
        self.assertEqual(cm.exception.code, "image_url_not_public")

    def test_reads_image_follows_redirects_and_rechecks_them(self) -> None:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        real = F.public_address

        def allow_loopback(host, port, resolve=F._resolve):   # この試験の間だけ、自機を公開のアドレスとみなす
            return "127.0.0.1" if host == "127.0.0.1" else real(host, port, resolve)

        with mock.patch.object(F, "public_address", allow_loopback):
            self.assertEqual(F.download(base + "/pic.png", max_bytes=10_000), _png())
            self.assertEqual(F.download(base + "/to-pic", max_bytes=10_000), _png(), "転送を追う")
            with self.assertRaises(ApiError) as cm:
                F.download(base + "/to-private", max_bytes=10_000)
            self.assertEqual(cm.exception.code, "image_url_not_public", "転送先も確かめる")
            with self.assertRaises(ApiError) as cm:
                F.download(base + "/pic.png", max_bytes=10)
            self.assertEqual(cm.exception.code, "image_size_limit")
            with self.assertRaises(ApiError) as cm:
                F.download(base + "/nothing", max_bytes=10_000)
            self.assertEqual(cm.exception.code, "image_url_status")


class EditKindApiTests(EditBase):
    """ピッチの 1 冊で、種類を選んだ直しを送る（台本係は偽物）。"""

    def test_presentation_is_made_without_generated_pictures_when_codex_is_missing(self) -> None:
        """Codex が無い機械では、プレゼンを生成の画像なしで作る（置くと描く段で止まる。docs/adr/0010）。"""
        from helpers import intake_payload
        with mock.patch.object(self.fx.server, "kind_refusals", lambda: {"pitch": "Codex が無い（試験）"}):
            r = self.fx.post("/api/intakes", intake_payload(runtime=self.runtime, mode="presentation"))
        self.assertEqual(r.status, 201, r.body)
        with self.fx.server.store_lock:
            self.assertIs(self.fx.store.deck_run_args(r.json()["deck_id"])["images"], False)
        r = self.fx.post("/api/intakes", intake_payload(runtime=self.runtime, mode="presentation"))
        with self.fx.server.store_lock:
            self.assertIs(self.fx.store.deck_run_args(r.json()["deck_id"])["images"], True, "Codex があれば、これまでどおり")
        wait_until_idle(self.fx, r.json()["deck_id"], seconds=300)

    def test_kinds_are_offered_and_checked(self) -> None:
        deck = self.make_deck()
        view = self.view(deck)
        kinds = [k["kind"] for k in view["edit_kinds"]]
        self.assertEqual(kinds, ["text", "diagram", "illustration", "image"])
        sid = view["pages"][1]["slide_id"]

        def post(**body):
            return self.fx.post(f"/api/decks/{deck}/instructions", {"scope": "page", "slide_id": sid, "text": "", **body})

        cases = (({"kind": "text"}, "text_required"), ({"kind": "image", "options": ["attached"]}, "attachment_required"),
                 ({"kind": "nazo"}, "edit_kind_invalid"), ({"kind": "diagram", "options": ["nazo"]}, "edit_kind_invalid"),
                 ({"options": ["flow"]}, "edit_kind_invalid"))
        for body, code in cases:
            r = post(**body)
            self.assertEqual(r.status, 400, (body, r.body))
            self.assertEqual(r.json()["error"]["code"], code, body)

        before = len(writer_calls(self.log))
        r = post(kind="diagram", options=["flow"])
        self.assertEqual(r.status, 200, r.body)
        run = self.wait(deck)
        self.assertEqual(run["state"], "done", run["message"])
        call = writer_calls(self.log)[before]
        self.assertIn(f"- {E.direction('pitch', 'diagram', ['flow'])[0]}", call["prompt_lines"], "選んだ方針が「直すもの」に入る")
        self.assertIn("直すもの", call["instruction"] or "", "文を書かなければ「直すもの」がそのまま指示")
        with self.fx.server.store_lock:
            row = self.fx.store.head_script(deck)
        self.assertEqual(row["instruction"], "図解（流れ）", "台本の版には選んだ方針の名前を控える")


if __name__ == "__main__":
    unittest.main()
