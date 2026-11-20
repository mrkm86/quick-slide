"""直す板の「AI に頼む」の札を**本物のブラウザ**で使う（docs/adr/0008-edit-kinds.md・docs/adr/0037-ask-ai-cards.md）。

ピッチの 1 冊（偽物の台本係・本物の image-slide・絵は偽物の codex）の 2 枚目の「直す」を開くと、「どう直しますか？」に
札が 3 つ出る（文字を整える・絵や写真を足す・見せ方を変える。絵と名前と一言）。札を選ぶと、ほかの札は薄くなり、名前だけの
小さな札に畳まれて、次に選ぶものが絵の札で出る。「絵や写真を足す」は線のイラスト・写真や絵を生成・手持ちの画像を使うから選ぶ。
「見せ方を変える」は型を選び、書かなくても送れる。「その他」は書かないと送れない。送った種類と選び方がサーバに届く。
送っても欄は閉じず、ページの絵に「編集中」の札が出る。‹ › で前後のページへ送れる。JavaScript の例外が 1 つも出ないこと。

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

P = ".panel.is-open "
CARDS = f"Array.from(document.querySelectorAll('{P}.editkind__kind')).map(b => b.querySelector('.editkind__name').textContent)"
STEPS = f"Array.from(document.querySelectorAll('{P}.editkind__step')).map(p => p.textContent)"
OPTIONS = f"Array.from(document.querySelectorAll('{P}.editkind__option')).map(b => b.querySelector('.editkind__option-name').textContent)"
PRESSED = (f"Array.from(document.querySelectorAll('{P}.editkind__option[aria-pressed=true]'))"
           ".map(b => b.querySelector('.editkind__option-name').textContent)")
SEND = f"document.querySelector('{P}.editbox__send')"


def click_card(card: str) -> str:
    return f"document.querySelector('{P}.editkind__kind[data-card={card}]').click()"


def click_option(label: str) -> str:
    return (f"Array.from(document.querySelectorAll('{P}.editkind__option'))"
            f".find(b => b.querySelector('.editkind__option-name').textContent === {label!r}).click()")


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

    def test_pick_a_card_and_send_without_writing(self) -> None:
        r = self.fx.post("/api/intakes", intake_payload(runtime="codex", mode="pitch"))
        self.assertEqual(r.status, 201, r.body)
        deck = r.json()["deck_id"]
        first = wait_until_idle(self.fx, deck, seconds=120)
        self.assertEqual(first["state"], "done", first["message"])
        page = self.br.open(f"{self.fx.base_url}/slides/{deck}")
        page.until("document.querySelectorAll('.pages article.page .frame img').length >= 2", seconds=30, what="ページの絵")
        page.eval("document.querySelectorAll('.pages article.page .page__edit')[1].click()")
        page.until(f"!!document.querySelector('{P}.editkind')", what="どう直しますか？")
        self.assertEqual(page.eval(f"document.querySelector('{P}.editkind__q').textContent"), "どう直しますか？")
        self.assertEqual(page.eval(CARDS), ["文字を整える", "絵や写真を足す", "見せ方を変える"], "変わる大きさの小さい順")
        self.assertEqual(page.eval(f"document.querySelectorAll('{P}.editkind__kind .kart').length"), 3, "どの札にも絵")
        self.assertEqual(page.eval(f"document.querySelectorAll('{P}.editkind .help-tip').length"), 0, "「？」の説明は置かない")
        self.assertTrue(page.eval(SEND + ".disabled"), "何も選ばず書かなければ送れない")
        # 絵のあるページは「今の絵をもとに直す」の切り替え（既定はオン）。オフにすると作り直す
        self.assertTrue(page.eval(f"!document.querySelector('{P}.editbox__keep').hidden"))
        self.assertTrue(page.eval(f"document.querySelector('{P}.editbox__keep input').checked"))
        page.eval(f"document.querySelector('{P}.editbox__keep input').click()")
        self.assertTrue(page.eval(f"document.querySelector('{P}.editbox__remake-box').checked"), "オフは作り直す")
        page.eval(f"document.querySelector('{P}.editbox__keep input').click()")
        self.assertFalse(page.eval(f"document.querySelector('{P}.editbox__remake-box').checked"))

        # 見せ方を変える: ほかの札は薄くなり、札は畳まれ、型の札が絵つきで出る。先頭（おまかせ）が選ばれていて、書かなくても送れる
        page.eval(click_card("layout"))
        self.assertTrue(page.eval(f"document.querySelector('{P}.editkind').classList.contains('editkind--picked')"))
        self.assertEqual(page.eval(f"Array.from(document.querySelectorAll('{P}.editkind__kind.is-dim')).map(b => b.dataset.card)"),
                         ["text", "visual"])
        self.assertEqual(page.eval(STEPS), ["どの型にしますか"])
        self.assertEqual(page.eval(OPTIONS), ["おまかせ", "流れ", "関係", "比較", "前と後", "数字", "その他（書いて伝える）"])
        self.assertEqual(page.eval(f"document.querySelectorAll('{P}.editkind__option svg.oart').length"), 7, "どの選択肢にも絵")
        self.assertEqual(page.eval(PRESSED), ["おまかせ"])
        self.assertFalse(page.eval(SEND + ".disabled"), "型は選ぶだけで送れる")
        # その他: 書かないと送れない。欄の案内が変わる
        page.eval(click_option("その他（書いて伝える）"))
        self.assertTrue(page.eval(SEND + ".disabled"))
        self.assertIn("見せ方", page.eval(f"document.querySelector('{P}textarea').placeholder"))
        page.eval(click_option("流れ"))
        self.assertEqual(page.eval(f"Array.from(document.querySelectorAll('{P}.editkind__option.is-dim')).length"), 6, "選んでいない型は薄い")

        # 絵や写真を足す: 線のイラスト・写真や絵を生成・手持ちの画像を使う。既定は線のイラスト（生成の要らないもの）
        page.eval(click_card("visual"))
        self.assertEqual(page.eval(STEPS), ["どんな絵にしますか"])
        self.assertEqual(page.eval(OPTIONS), ["線のイラスト", "写真・絵を生成", "手持ちの画像を使う"])
        self.assertEqual(page.eval(PRESSED), ["線のイラスト"])
        self.assertFalse(page.eval(SEND + ".disabled"))
        if not self.can_generate():
            self.assertEqual(page.eval(f"document.querySelector('{P}.editkind__option[aria-disabled=true] .editkind__option-note').textContent"),
                             "現在利用できません")
        # 手持ちの画像を使う: 画像を渡すまでは送れない（入口は「＋」の 1 つ。案内を小さく出す）
        page.eval(click_option("手持ちの画像を使う"))
        self.assertIn("「＋」", page.eval(f"document.querySelector('{P}.editkind__hint').textContent"))
        self.assertTrue(page.eval(SEND + ".disabled"), "画像を渡すまでは送れない")
        # 画像の URL を欄に貼り付けると取り込みを試みる。自機の URL は取り込まず、文字のまま残して理由を小さく出す
        page.eval("(function(){ var ta = document.querySelector('.panel.is-open textarea'); ta.focus();"
                  " var dt = new DataTransfer(); dt.setData('text/plain', 'http://127.0.0.1/a.png');"
                  " ta.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); })()")
        note = page.until("(function(){ var n = document.querySelector('.panel.is-open .editbox__url-note');"
                          " return n && n.textContent.indexOf('取り込めませんでした') >= 0 && n.textContent; })()",
                          what="URL を取り込めなかった知らせ")
        self.assertIn("公開", note)
        page.eval("(function(){ var ta = document.querySelector('.panel.is-open textarea'); ta.value = '';"
                  " ta.dispatchEvent(new Event('input', {bubbles: true})); })()")

        # 見せ方を変える → 流れ を選んで送る
        page.eval(click_card("layout"))
        page.eval(click_option("流れ"))
        page.eval(SEND + ".click()")
        # 1 ページの直しは、送っても欄を閉じない。文は出さず、ページの絵の上に「編集中」（か「編集待ち」）の札が出る
        page.until("!!document.querySelector('.pages article.page.page--editing .frame .edit-badge')", seconds=20, what="ページの札")
        self.assertIn(page.eval("document.querySelector('.pages article.page.page--editing .frame .edit-badge').textContent"),
                      ("編集中", "編集待ち"))
        self.assertEqual(page.eval("document.querySelector('.panel.is-open .editbox__said').textContent"), "", "送れたら文は出さない")
        self.assertEqual(page.eval("document.querySelectorAll('.panel.is-open .editkind__kind[aria-pressed=true]').length"), 0)
        self.assertFalse(page.eval(f"document.querySelector('{P}.editkind').classList.contains('editkind--picked')"), "送ったら札は元の大きさに戻る")
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
