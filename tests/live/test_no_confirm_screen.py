"""直しを送るときに、確かめる窓（ブラウザの confirm）を出さないことを**本物のブラウザ**で見る。

- 取り込んだページ（PPTX から）で、画像を「主役にして組み直す」を選ぶと、選び方の下に一言が出る。送っても窓は出ない
- デッキ全体への指示は、窓を出さずに送り、画面が見せている枚数をサーバへ渡す。ほかの場所で枚数が変わっていたら、
  サーバが断り、書いた文は消えない

Chrome が無い環境では skip する。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from helpers import fake_tools, intake_payload, start_server, temp_dir, wait_until_idle  # noqa: E402

from quick_slide.settings import Settings, WorkerSettings  # noqa: E402

from test_engines_e2e import html_slide_ready  # noqa: E402
from test_import_pptx import pptx_bytes  # noqa: E402

from . import browser  # noqa: E402

P = ".panel.is-open "
SEND = f"document.querySelector('{P}.editbox__send')"
TRAP = "window.__asked = []; window.confirm = function (m) { window.__asked.push(m); return false; };"


def click_text(selector: str, label: str) -> str:
    return f"Array.from(document.querySelectorAll({selector!r})).find(b => b.textContent === {label!r}).click()"


class NoConfirmScreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not browser.available():
            raise unittest.SkipTest("Chrome が無い環境")
        why = html_slide_ready()
        if why:
            raise unittest.SkipTest(why)

    def setUp(self) -> None:
        tools, self.log = fake_tools(self, extra={"FAKE_CODEX_SLEEP": "0.05"})
        self.fx = start_server(self, tools=tools, settings=Settings(worker=WorkerSettings(parallel=1)))
        self.br = browser.Browser()
        self.addCleanup(self.br.close)

    def request_of(self, run_id: str) -> dict:
        with self.fx.server.store_lock:
            job = self.fx.store._one("SELECT request_json FROM jobs WHERE run_id=?", (run_id,))
        return json.loads(job["request_json"])

    def test_rebuild_shows_a_note_and_sends_without_asking(self) -> None:
        r = self.fx.request("POST", "/api/imports?runtime=cli&name=deck.pptx", raw_body=pptx_bytes(temp_dir(self)), session=True)
        self.assertEqual(r.status, 201, r.body)
        deck = r.json()["deck_id"]
        first = wait_until_idle(self.fx, deck, seconds=300)
        self.assertEqual(first["state"], "done", first["message"])
        page = self.br.open(f"{self.fx.base_url}/slides/{deck}")
        page.until("document.querySelectorAll('.pages article.page .frame img').length >= 1", seconds=30, what="ページの絵")
        page.eval(TRAP)
        page.eval("document.querySelectorAll('.pages article.page .page__edit')[0].click()")
        page.until(f"!!document.querySelector('{P}.editkind')", what="何を直しますか？")
        page.eval(click_text(f"{P}.editkind__kind", "画像"))
        self.assertEqual(page.eval(f"document.querySelectorAll('{P}.editkind__note').length"), 0, "足す・差し替えるには一言は無い")
        page.eval(click_text(f"{P}.editkind__option", "主役にして組み直す"))
        note = page.eval(f"document.querySelector('{P}.editkind__note').textContent")
        self.assertIn("組み直します", note)
        self.assertIn("前の版にはいつでも戻せます", note)
        self.assertFalse(page.eval(SEND + ".disabled"))
        page.eval(SEND + ".click()")
        page.until("!!document.querySelector('.pages article.page.page--editing .frame .edit-badge')", seconds=20, what="ページの札")
        self.assertEqual(page.eval("window.__asked"), [], "確かめる窓は出さない")
        last = wait_until_idle(self.fx, deck, seconds=300)
        self.assertNotEqual(last["id"], first["id"])
        req = self.request_of(last["id"])
        self.assertEqual((req["kind"], req["options"]), ("image", ["main"]))
        self.assertEqual(page.errors, [])

    def test_a_deck_wide_edit_sends_without_asking_and_echoes_the_page_count(self) -> None:
        r = self.fx.post("/api/intakes", intake_payload(runtime="claude_code", mode="presentation"))
        self.assertEqual(r.status, 201, r.body)
        deck = r.json()["deck_id"]
        first = wait_until_idle(self.fx, deck, seconds=300)
        self.assertEqual(first["state"], "done", first["message"])
        pages = len(self.fx.get(f"/api/decks/{deck}").json()["pages"])
        self.assertGreaterEqual(pages, 2)
        page = self.br.open(f"{self.fx.base_url}/slides/{deck}")
        page.until("document.querySelectorAll('.pages article.page .frame img').length >= 2", seconds=30, what="ページの絵")
        page.eval(TRAP)
        # 送る要求の本文を控える（見せた枚数を渡しているかを見る）
        page.eval("window.__sent = []; (function(){ var f = window.fetch; window.fetch = function (u, o) {"
                  " if (String(u).indexOf('/instructions') >= 0 && o && o.body) { window.__sent.push(JSON.parse(o.body)); }"
                  " return f.apply(this, arguments); }; })()")
        page.eval("document.querySelector('.deck-actions .btn--icon').click()")
        page.until("!!document.querySelector('.menu')", what="メニュー")
        page.eval(click_text(".menu button", "デッキ全体を直す"))
        page.until(f"!!document.querySelector('{P}textarea')", what="デッキ全体の欄")
        self.assertIn(f"{pages} ページ", page.eval("document.querySelector('.panel__target').textContent"), "枚数は板の見出しに出ている")
        type_in = ("(function(v){ var ta = document.querySelector('" + P + "textarea'); ta.value = v;"
                   " ta.dispatchEvent(new Event('input', {bubbles: true})); })")
        # ほかの場所で枚数が変わっていたら、サーバが断る。書いた文は消えない
        sid = self.fx.get(f"/api/decks/{deck}").json()["pages"][0]["slide_id"]
        r = self.fx.post(f"/api/decks/{deck}/pages/{sid}/duplicate", {})
        self.assertEqual(r.status, 200, r.body)
        wait_until_idle(self.fx, deck, seconds=300)
        page.eval(type_in + "('全体を、やさしい言葉にして')")
        page.eval(SEND + ".click()")
        page.until(f"!!document.querySelector('{P}.editbox__refused')", seconds=20, what="断られた理由")
        self.assertEqual(page.eval("window.__asked"), [], "確かめる窓は出さない")
        self.assertEqual(page.eval("window.__sent[0].confirm_pages"), pages, "画面が見せていた枚数を渡す")
        self.assertEqual(page.eval(f"document.querySelector('{P}textarea').value"), "全体を、やさしい言葉にして", "書いた文は消えない")
        self.assertEqual(page.errors, [])


if __name__ == "__main__":
    unittest.main()
