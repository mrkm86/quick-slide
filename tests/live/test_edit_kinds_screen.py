"""直すときの「何を直しますか？」を**本物のブラウザ**で使う（docs/adr/0008-edit-kinds.md）。

ピッチの 1 冊（偽物の台本係・本物の image-slide・絵は偽物の codex）の 2 枚目の「直す」を開くと、欄の上に
文字・図解・挿絵・画像の札（アイコンつき）と「？」の説明が出る。絵のあるページには「今の絵をもとに直す」の切り替えが出る。
図解を選ぶと選び方が出て、書かなくても送れる。送った直しの種類と選び方がサーバに届く。送っても欄は閉じず、ページの絵に「編集中」の札が出る。
‹ › で前後のページへ送れる（元のスライドの強調もついてくる）。
画像の「渡した画像を入れる」は、画像を渡すまで送れない。画像の URL を欄に貼り付けると取り込みを試みる。JavaScript の例外が 1 つも出ないこと。

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

from helpers import FAKES, fake_tools, intake_payload, runnable, start_server, wait_until_idle  # noqa: E402

from quick_slide.settings import Settings, WorkerSettings  # noqa: E402

from . import browser  # noqa: E402

KINDS = "Array.from(document.querySelectorAll('.panel.is-open .editkind__kind')).map(b => b.textContent)"
OPTIONS = "Array.from(document.querySelectorAll('.panel.is-open .editkind__option')).map(b => b.textContent)"
SEND = "document.querySelector('.panel.is-open .editbox__send')"


def click_text(selector: str, label: str) -> str:
    return f"Array.from(document.querySelectorAll({selector!r})).find(b => b.textContent === {label!r}).click()"


class EditKindsScreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not browser.available():
            raise unittest.SkipTest("Chrome が無い環境")

    def setUp(self) -> None:
        tools, self.log = fake_tools(self, extra={"FAKE_CODEX_SLEEP": "0.1",
                                                  "IMAGE_SLIDE_CODEX": runnable(FAKES / "fake_codex_varied.py")})
        self.fx = start_server(self, tools=tools, settings=Settings(worker=WorkerSettings(parallel=1)))
        self.br = browser.Browser()
        self.addCleanup(self.br.close)

    def can_generate(self) -> bool:
        from quick_slide.server.edit_routes import can_generate_images
        return can_generate_images(self.fx.server)

    def test_pick_a_kind_and_send_without_writing(self) -> None:
        r = self.fx.post("/api/intakes", intake_payload(runtime="codex", mode="pitch"))
        self.assertEqual(r.status, 201, r.body)
        deck = r.json()["deck_id"]
        first = wait_until_idle(self.fx, deck, seconds=120)
        self.assertEqual(first["state"], "done", first["message"])
        page = self.br.open(f"{self.fx.base_url}/slides/{deck}")
        page.until("document.querySelectorAll('.pages article.page .frame img').length >= 2", seconds=30, what="ページの絵")
        page.eval("document.querySelectorAll('.pages article.page .page__edit')[1].click()")
        page.until("!!document.querySelector('.panel.is-open .editkind')", what="何を直しますか？")
        self.assertEqual(page.eval(KINDS), ["文字", "図解", "挿絵", "画像"])
        self.assertTrue(page.eval(SEND + ".disabled"), "何も選ばず書かなければ送れない")
        # 「？」を押すと、このスライドで出す種類の説明が出る
        page.eval("document.querySelector('.panel.is-open .editkind .help-tip summary').click()")
        self.assertIn("挿絵:", page.eval("document.querySelector('.panel.is-open .editkind .help-tip__panel').textContent"))
        page.eval("document.querySelector('.panel.is-open .editkind .help-tip summary').click()")
        # 絵のあるページは「今の絵をもとに直す」の切り替え（既定はオン）。オフにすると作り直す
        self.assertTrue(page.eval("!document.querySelector('.panel.is-open .editbox__keep').hidden"))
        self.assertTrue(page.eval("document.querySelector('.panel.is-open .editbox__keep input').checked"))
        page.eval("document.querySelector('.panel.is-open .editbox__keep input').click()")
        self.assertTrue(page.eval("document.querySelector('.panel.is-open .editbox__remake-box').checked"), "オフは作り直す")
        page.eval("document.querySelector('.panel.is-open .editbox__keep input').click()")
        self.assertFalse(page.eval("document.querySelector('.panel.is-open .editbox__remake-box').checked"))

        # 図解: 選び方が出て、先頭（おまかせ）が選ばれている。書かなくても送れる
        page.eval(click_text(".panel.is-open .editkind__kind", "図解"))
        self.assertEqual(page.eval(OPTIONS), ["おまかせ", "流れ", "関係", "比較", "数字"])
        self.assertEqual(page.eval("document.querySelector('.panel.is-open .editkind__option[aria-pressed=true]').textContent"),
                         "おまかせ")
        self.assertFalse(page.eval(SEND + ".disabled"), "図解は選ぶだけで送れる")

        # 画像 → 渡した画像を入れる: 画像を渡すまでは送れない（入口は「＋」の 1 つ。案内を小さく出す）
        page.eval(click_text(".panel.is-open .editkind__kind", "画像"))
        self.assertEqual(page.eval(OPTIONS), ["生成する", "渡した画像を入れる"] if self.can_generate() else ["渡した画像を入れる"])
        page.eval(click_text(".panel.is-open .editkind__option", "渡した画像を入れる"))
        self.assertIn("「＋」", page.eval("document.querySelector('.panel.is-open .editkind__hint').textContent"))
        self.assertTrue(page.eval(SEND + ".disabled"), "画像を渡すまでは送れない")
        # 画像の URL を欄に貼り付けると取り込みを試みる。自機の URL は取り込まず、文字のまま残して理由を小さく出す
        page.eval("(function(){ var ta = document.querySelector('.panel.is-open textarea'); ta.focus();"
                  " var dt = new DataTransfer(); dt.setData('text/plain', 'http://127.0.0.1/a.png');"
                  " ta.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); })()")
        note = page.until("(function(){ var n = document.querySelector('.panel.is-open .editbox__url-note');"
                          " return n && n.textContent.indexOf('取り込めませんでした') >= 0 && n.textContent; })()",
                          what="URL を取り込めなかった知らせ")
        self.assertIn("公開", note)

        # 図解の流れを選んで送る
        page.eval(click_text(".panel.is-open .editkind__kind", "図解"))
        page.eval(click_text(".panel.is-open .editkind__option", "流れ"))
        page.eval(SEND + ".click()")
        # 1 ページの直しは、送っても欄を閉じない。文は出さず、ページの絵の上に「編集中」（か「編集待ち」）の札が出る
        page.until("!!document.querySelector('.pages article.page.page--editing .frame .edit-badge')", seconds=20, what="ページの札")
        self.assertIn(page.eval("document.querySelector('.pages article.page.page--editing .frame .edit-badge').textContent"),
                      ("編集中", "編集待ち"))
        self.assertEqual(page.eval("document.querySelector('.panel.is-open .editbox__said').textContent"), "", "送れたら文は出さない")
        self.assertEqual(page.eval("document.querySelectorAll('.panel.is-open .editkind__kind[aria-pressed=true]').length"), 0)
        # ‹ › で前後のページへ送る。元のスライドの強調もついてくる
        self.assertIn("2 / ", page.eval("document.querySelector('.panel__target').textContent"))
        page.eval("document.querySelector('.panel .panel__step[aria-label=\"次のページを直す\"]').click()")
        self.assertIn("3 / ", page.eval("document.querySelector('.panel__target').textContent"))
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('.pages article.page')).indexOf(document.querySelector('.page--editing'))"), 2)
        page.eval("document.querySelector('.panel .panel__step[aria-label=\"前のページを直す\"]').click()")
        self.assertIn("2 / ", page.eval("document.querySelector('.panel__target').textContent"))
        page.eval("for (var i = 0; i < 2; i++) { document.querySelector('.panel .panel__step[aria-label=\"前のページを直す\"]').click(); }")
        self.assertTrue(page.eval("document.querySelector('.panel .panel__step[aria-label=\"前のページを直す\"]').disabled"), "先頭では押せない")
        last = wait_until_idle(self.fx, deck, seconds=120)
        self.assertNotEqual(last["id"], first["id"])
        self.assertEqual(last["state"], "done", last["message"])
        with self.fx.server.store_lock:
            job = self.fx.store._one("SELECT request_json FROM jobs WHERE run_id=?", (last["id"],))
        req = json.loads(job["request_json"])
        self.assertEqual((req["kind"], req["options"], req["text"]), ("diagram", ["flow"], ""))
        page.raise_if_broken()
        self.assertEqual(page.errors, [])


if __name__ == "__main__":
    unittest.main()
