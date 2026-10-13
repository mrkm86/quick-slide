"""P1 の確かめ: **本物のブラウザ**で v2 のサーバの画面を開き、JavaScript の例外が 1 つも出ないこと。

開く画面: ホーム・一覧・ごみ箱・スライドの画面（両モード）・設定（ブランド一覧・ブランドの編集・スタイル・この機械）。
それぞれ「画面が描き終わった」印を待ち、そのあと少し待って遅れて投げられる例外も拾う。
画面がエラーの器（`SS.errorBlock`）を出していないことも見る（API が画面の読む形を返していない兆し）。

Chrome が無い環境では skip する。
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from helpers import start_server  # noqa: E402

from quick_slide.ids import PRESET_BRAND_ID  # noqa: E402

from . import browser  # noqa: E402

SETTLE_SECONDS = 1.0


class ScreensOpenWithoutErrorsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not browser.available():
            raise unittest.SkipTest("Chrome が無い環境")

    def setUp(self) -> None:
        self.fx = start_server(self)
        self.talk = self.fx.make_deck("会話から作ったプレゼン", mode="presentation")
        self.pitch = self.fx.make_deck("ピッチの資料", mode="pitch")
        with self.fx.server.store_lock:
            self.fx.store.pin_deck(self.pitch["id"], True)
            gone = self.fx.make_deck("消したスライド", mode="presentation")
            self.fx.store.delete_deck_to_trash(gone["id"])
        self.br = browser.Browser()
        self.addCleanup(self.br.close)

    def open(self, path: str, ready: str, what: str) -> browser.Page:
        page = self.br.open(self.fx.base_url + path)
        page.until(ready, seconds=20, what=what)
        time.sleep(SETTLE_SECONDS)
        page.raise_if_broken()
        shown_error = page.eval("Array.from(document.querySelectorAll('#view .notice--error')).map(e => e.textContent).join(' / ')")
        self.assertEqual(shown_error, "", f"{path}: 画面がエラーを出している")
        self.assertEqual(page.errors, [], f"{path}: JavaScript の例外")
        return page

    def test_home_list_and_trash(self) -> None:
        page = self.open("/", "document.querySelectorAll('#view .card').length >= 2", "ホームにカードが出ること")
        self.assertEqual(page.eval("document.querySelectorAll('.pins .card').length"), 1)
        page.until("document.getElementById('server-state').textContent === 'サーバ稼働中'", what="サーバの状態")

        page = self.open("/slides", "document.querySelectorAll('.list .card').length === 2", "一覧にカードが 2 枚出ること")
        self.assertIn("2 件", page.eval("document.querySelector('#view p[aria-live]').textContent"))

        page = self.open("/slides?trash=1", "document.querySelectorAll('.list .card--row').length === 1",
                         "ごみ箱に 1 行出ること")
        self.assertTrue(page.eval("!!document.querySelector('.trash-head')"))

    def test_deck_screen_for_both_modes(self) -> None:
        for deck in (self.talk, self.pitch):
            page = self.open(f"/slides/{deck['id']}",
                             "!!document.querySelector('.deck-screen .pages .empty')", "スライドの画面が出ること")
            self.assertEqual(page.eval("document.querySelector('.deck-title h1').textContent"), deck["title"])
            # ページが無いうちは、直す・書き出しは押せない（理由が 1 か所に出る）
            self.assertTrue(page.eval("document.querySelector('.deck-export').disabled"))
            self.assertIn("まだページがありません", page.eval("document.querySelector('.screen-reasons').textContent"))

    def test_settings_screens(self) -> None:
        # 設定のトップは各設定へのリンクの一覧（タブは無い）
        page = self.open("/settings", "document.querySelectorAll('.settings-index__link').length === 3", "設定のトップ")
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('.settings-index__link')).map(a => a.getAttribute('href')).join(' ')"),
                         "/settings/brands /settings/styles /settings/server")
        page = self.open("/settings/brands", "document.querySelectorAll('.setting-row').length >= 1", "ブランドの一覧")
        self.assertEqual(page.eval("document.querySelector('.breadcrumb [aria-current]').textContent"), "ブランド")
        page = self.open(f"/settings/brands/{PRESET_BRAND_ID}",
                         "document.querySelector('.brand-editor__preview .help-tip') !== null"
                         " && !!document.querySelector('.carousel .sample')", "ブランドの編集と見本")
        self.assertEqual(page.eval("document.querySelectorAll('.corner-choice').length"), 3)
        page = self.open("/settings/styles", "document.querySelectorAll('.pattern-choice').length >= 1",
                         "スライドスタイルの編集")
        page = self.open("/settings/server", "document.querySelectorAll('.setting-choice').length === 2", "この機械")

    def test_deck_management_works_from_the_screen(self) -> None:
        """名前を変える（鉛筆）・ピン留め・複製・ごみ箱へ・元に戻す・完全に削除。"""
        page = self.open(f"/slides/{self.talk['id']}", "!!document.querySelector('.deck-screen')", "スライドの画面")
        page.eval("(function(){ document.querySelector('.deck-title-edit').click(); var i = document.querySelector('.deck-title-input');"
                  " i.value = '名前を変えたプレゼン'; i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter'})); })()")
        page.until("document.querySelector('.deck-title h1').textContent === '名前を変えたプレゼン'", what="名前が変わること")
        # メニューから複製
        page.eval("document.querySelector('.deck-actions .btn--icon').click()")
        page.until("!!document.querySelector('.menu')", what="メニュー")
        page.eval("Array.from(document.querySelectorAll('.menu button')).find(b => b.textContent === '複製').click()")
        page.until("Array.from(document.querySelectorAll('.toast')).some(t => t.textContent.indexOf('複製しました') >= 0)", what="複製の知らせ")
        # メニューからごみ箱へ（確認は通す）
        page.eval("window.confirm = function () { return true; }")
        page.eval("document.querySelector('.deck-actions .btn--icon').click()")
        page.until("!!document.querySelector('.menu')", what="メニュー")
        page.eval("Array.from(document.querySelectorAll('.menu button')).find(b => b.textContent.indexOf('削除') === 0).click()")
        page.until("location.pathname === '/' && document.querySelectorAll('#view .card').length >= 1", what="ホームへ戻ること")
        page.raise_if_broken()
        titles = [c["title"] for c in self.fx.get("/api/decks?limit=50").json()["items"]]
        self.assertNotIn("名前を変えたプレゼン", titles)
        self.assertIn("名前を変えたプレゼンのコピー", titles)

        # ホームのカードのメニューからピン留め
        page.eval("Array.from(document.querySelectorAll('.cards .card')).find(c => c.textContent.indexOf('のコピー') >= 0)"
                  ".querySelector('.card__menu button').click()")
        page.until("!!document.querySelector('.menu')", what="カードのメニュー")
        page.eval("Array.from(document.querySelectorAll('.menu button')).find(b => b.textContent === 'ピン留め').click()")
        page.until("document.querySelectorAll('.pins .card').length === 2", what="ピン留めが 2 枚になること")

        # ごみ箱: 元に戻す → もう一度ごみ箱へ → 完全に削除（2 段のボタン）
        page = self.open("/slides?trash=1", "document.querySelectorAll('.list .card--row').length === 2", "ごみ箱に 2 行")
        page.eval("Array.from(document.querySelectorAll('.list .card--row')).find(r => r.textContent.indexOf('名前を変えた') >= 0)"
                  ".querySelector('button').click()")
        page.until("document.querySelectorAll('.list .card--row').length === 1", what="元に戻って 1 行になること")
        page.eval("Array.from(document.querySelectorAll('.list .card--row button')).find(b => b.textContent === '完全に削除').click()")
        page.until("Array.from(document.querySelectorAll('.list .card--row button')).some(b => b.textContent === '本当に削除する')",
                   what="2 段目のボタン")
        page.eval("Array.from(document.querySelectorAll('.list .card--row button')).find(b => b.textContent === '本当に削除する').click()")
        page.until("!!document.querySelector('.list .empty')", what="ごみ箱が空になること")
        page.raise_if_broken()
        self.assertEqual(page.errors, [])
        with self.fx.server.store_lock:
            self.assertEqual(self.fx.store.trashed_decks(), [])
        self.assertIn("名前を変えたプレゼン", [c["title"] for c in self.fx.get("/api/decks?limit=50").json()["items"]])

    def test_list_selection_acts_on_several_decks(self) -> None:
        """一覧の行のチェックで選ぶと帯が出て、ピン留め・ごみ箱へ・元に戻すがまとめて効く。"""
        page = self.open("/slides", "document.querySelectorAll('.list .card__select-box').length === 2", "一覧に選ぶチェックが 2 つ")
        self.assertTrue(page.eval("document.querySelector('.selection-bar--list').hidden"), "選ぶまで帯は出ない")
        bar_button = "Array.from(document.querySelectorAll('.selection-bar--list button')).find(b => b.textContent === {!r})"
        # チェックを押してもスライドの画面へは移らない（リンクの覆いより手前にある）
        page.eval("document.querySelector('.list .card__select-box').click()")
        page.until("!document.querySelector('.selection-bar--list').hidden", what="帯が出ること")
        self.assertEqual(page.eval("location.pathname"), "/slides")
        self.assertEqual(page.eval("document.querySelector('.selection-bar__text').textContent"), "1 件を選んでいます")
        self.assertEqual(page.eval("document.querySelectorAll('.list .card--selected').length"), 1)
        # すべて選ぶ → ピン留め（ピン留めしていないものだけに送る）
        page.eval(bar_button.format("すべて選ぶ") + ".click()")
        page.until("document.querySelector('.selection-bar__text').textContent === '2 件を選んでいます'", what="2 件を選ぶ")
        self.assertFalse(page.eval(bar_button.format("ピン留め") + ".disabled"))
        self.assertFalse(page.eval(bar_button.format("ピン留めを解除") + ".disabled"))
        page.eval(bar_button.format("ピン留め") + ".click()")
        page.until("Array.from(document.querySelectorAll('.toast')).some(t => t.textContent.indexOf('1 件をピン留めしました') >= 0)",
                   what="ピン留めの知らせ")
        page.until("document.querySelector('.selection-bar--list').hidden && document.querySelectorAll('.list .card__select-box').length === 2",
                   what="読み込み直して選びが外れること")
        items = self.fx.get("/api/decks?limit=50").json()["items"]
        self.assertTrue(all(c["pinned"] for c in items), items)
        # すべて選ぶ → ごみ箱へ（確認は通す）→ 知らせの「元に戻す」で 2 件とも戻る
        page.eval("window.confirm = function () { return true; }")
        page.eval(bar_button.format("すべて選ぶ") + ".click()")
        page.eval(bar_button.format("ごみ箱へ移動") + ".click()")
        page.until("!!document.querySelector('.list .empty')", what="一覧が空になること")
        with self.fx.server.store_lock:
            self.assertEqual(len(self.fx.store.trashed_decks()), 3)
        page.eval("Array.from(document.querySelectorAll('.toast')).find(t => t.textContent.indexOf('2 件をごみ箱へ移動しました') >= 0)"
                  ".querySelector('button').click()")
        page.until("document.querySelectorAll('.list .card__select-box').length === 2", what="2 件とも元に戻ること")
        page.raise_if_broken()
        self.assertEqual(page.errors, [])
        with self.fx.server.store_lock:
            self.assertEqual(len(self.fx.store.trashed_decks()), 1)

    def test_settings_can_be_saved_from_the_screen(self) -> None:
        """ブランドを作って色と角を変えて保存する・スタイルを複写して作る。画面から最後まで通ること。"""
        page = self.open("/settings/brands", "document.querySelectorAll('.setting-row').length >= 1", "ブランドの一覧")
        page.eval("Array.from(document.querySelectorAll('.settings-toolbar button')).find(b => b.textContent === '新しいブランド').click()")
        page.until("!!document.querySelector('.modal input')", what="名前のモーダル")
        page.eval("(function(){ var i = document.querySelector('.modal input'); i.value = '画面で作ったブランド';"
                  " Array.from(document.querySelectorAll('.modal button')).find(b => b.textContent === '作る').click(); })()")
        page.until("location.pathname.indexOf('/settings/brands/') === 0 && !!document.querySelector('.carousel .sample')",
                   what="ブランドの編集画面")
        page.eval("(function(){ var c = document.querySelector('.color-field input[type=text]'); c.value = '#2255AA';"
                  " c.dispatchEvent(new Event('input', {bubbles: true})); c.dispatchEvent(new Event('change', {bubbles: true}));"
                  " document.querySelectorAll('.corner-choice')[1].click(); })()")
        page.until("Array.from(document.querySelectorAll('.settings-actions button')).some(b => b.textContent === '保存する' && !b.disabled)",
                   what="見本が更新されて保存できること")
        page.eval("Array.from(document.querySelectorAll('.settings-actions button')).find(b => b.textContent === '保存する').click()")
        page.until("document.querySelector('.title-save-state').textContent === '保存済み'", what="保存済みの表示")
        page.raise_if_broken()
        brands = {b["name"]: b for b in self.fx.get("/api/settings").json()["brands"]}
        saved = brands["画面で作ったブランド"]
        self.assertEqual(saved["config"]["colors"]["primary"], "#2255AA")
        self.assertEqual(saved["config"]["logo"]["placement"], {"corner": "bottom-left"})

        page = self.open("/settings/styles", "document.querySelectorAll('.pattern-choice').length >= 1", "スタイルの画面")
        page.eval("Array.from(document.querySelectorAll('.settings-list button')).find(b => b.textContent.indexOf('新しい') >= 0).click()")
        page.until("document.querySelector('.settings-editor h2').textContent.indexOf('複写') >= 0", what="複写の欄")
        page.eval("(function(){ var i = document.querySelector('.settings-editor input[type=text]'); i.value = '画面で作った型';"
                  " i.dispatchEvent(new Event('input')); Array.from(document.querySelectorAll('.settings-editor button'))"
                  ".find(b => b.textContent === '保存する').click(); })()")
        page.until("Array.from(document.querySelectorAll('.settings-list button')).some(b => b.textContent === '画面で作った型')",
                   what="作った型が一覧に出ること")
        page.raise_if_broken()
        self.assertEqual(page.errors, [])

    def test_opening_the_launch_url_attaches_once_and_drops_the_token(self) -> None:
        launch_id, token = self.fx.make_launch(self.talk["id"])
        page = self.open(f"/slides/{self.talk['id']}?launch={launch_id}&token={token}",
                         "!!document.querySelector('.deck-screen')", "起動の URL で画面が出ること")
        self.assertNotIn("token", page.eval("location.href"))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with self.fx.server.store_lock:
                state = self.fx.store.launch(launch_id)["state"]
            if state == "attached":
                break
            time.sleep(0.2)
        self.assertEqual(state, "attached")


if __name__ == "__main__":
    unittest.main()
