"""新しい版のお知らせを**本物のブラウザ**で見る（docs/adr/0020-release-and-update-notice.md）。GitHub は叩かない。

- ホーム: 新しい版があれば頭に控えめな 1 行が出る（閉じられない。更新するまで出る）。最新なら何も出ない
- 変更点はアプリに写さず、GitHub のリリースの頁へのリンクだけ（帯と 設定 › 更新）。設定 › 更新 は更新のしかたと切り替え
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from helpers import start_server  # noqa: E402

from quick_slide import updates as U  # noqa: E402

from . import browser  # noqa: E402

NOTES = "### 追加\n\n- 新しいことができる"


class Answers:
    """差し替える GitHub の答え（`version` を書き換えると、次に確かめたときの答えが変わる）。"""

    def __init__(self, version: str) -> None:
        self.version = version

    def __call__(self) -> bytes:
        return json.dumps({"tag_name": "v" + self.version, "name": f"Quick Slide {self.version}",
                           "html_url": f"{U.RELEASES_PAGE}/tag/v{self.version}",
                           "published_at": "2026-11-01T00:00:00Z", "body": NOTES}).encode("utf-8")


@mock.patch.dict(os.environ, {U.ENV_NO_CHECK: ""})
class UpdateNoticeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not browser.available():
            raise unittest.SkipTest("Chrome が無い環境")

    def setUp(self) -> None:
        self.answers = Answers("9.1.0")
        self.fx = start_server(self, update_fetch=self.answers)
        self.fx.make_deck("ホームのカード", mode="presentation")
        self.br = browser.Browser()
        self.addCleanup(self.br.close)

    def home(self) -> browser.Page:
        page = self.br.open(self.fx.base_url + "/")
        page.until("document.querySelectorAll('#view .card').length >= 1", seconds=20, what="ホーム")
        return page

    def ask_again(self, version: str) -> None:
        self.answers.version = version
        self.fx.server.updates._next_at = 0.0   # 覚えた答えを捨てて、次の問い合わせで確かめ直させる

    def test_home_notice_stays_until_updated(self) -> None:
        page = self.home()
        page.until("!!document.querySelector('.update-notice')", what="お知らせの 1 行")
        self.assertIn("9.1.0 が公開されています（現在：", page.eval("document.querySelector('.update-notice').textContent"))
        self.assertEqual(page.eval("document.querySelector('#view').firstElementChild.classList.contains('update-notice')"), True)
        self.assertIsNone(page.eval("document.querySelector('.update-notice button')"), "閉じるボタンは置かない")
        # 読み直しても出る。次の版が出れば、その版を出す
        self.ask_again("9.2.0")
        page = self.home()
        page.until("!!document.querySelector('.update-notice')", what="次の版のお知らせ")
        self.assertIn("9.2.0", page.eval("document.querySelector('.update-notice').textContent"))
        page.raise_if_broken()
        self.assertEqual(page.errors, [])

    def test_no_banner_when_up_to_date(self) -> None:
        from quick_slide import __version__
        self.ask_again(__version__)
        page = self.home()
        time.sleep(1.0)
        self.assertFalse(page.eval("!!document.querySelector('.update-notice')"))
        self.assertEqual(page.errors, [])

    def test_updates_screen(self) -> None:
        page = self.home()
        page.until("!!document.querySelector('.update-notice__more')", what="お知らせの帯")
        # 変更点はアプリに写さず、GitHub のリリースの頁へのリンクだけ（同じウィンドウで開く）
        self.assertEqual(page.eval("document.querySelector('.update-notice__notes').getAttribute('href')"),
                         f"{U.RELEASES_PAGE}/tag/v9.1.0")
        self.assertIsNone(page.eval("document.querySelector('.update-notice__notes').getAttribute('target')"))
        page.eval("document.querySelector('.update-notice__more').click()")
        page.until("location.pathname === '/settings/updates' && !!document.querySelector('.update-howto')", what="設定 › 更新")
        self.assertEqual(page.eval("document.querySelector('.update-release__link').getAttribute('href')"),
                         f"{U.RELEASES_PAGE}/tag/v9.1.0")
        self.assertIsNone(page.eval("document.querySelector('.update-release__link').getAttribute('target')"))
        self.assertNotIn("新しいこと", page.eval("document.getElementById('view').textContent"))
        self.assertIn("claude plugin update quick-slide@quick-slide", page.eval("document.querySelector('.update-howto').textContent"))
        # コマンドの「コピー」: 写せたら知らせる。写せなければ文字を選んで ⌘C を案内する（どちらでも黙らない）
        self.assertEqual(page.eval("document.querySelectorAll('.update-howto__copy').length"), 2)
        page.eval("document.querySelector('.update-howto__copy').click()")
        page.until("Array.from(document.querySelectorAll('.toast')).some(t => t.textContent.indexOf('コピー') >= 0)", what="コピーの知らせ")
        self.assertFalse(page.eval("document.querySelector('.update-check').disabled"))

        # 切り替えを外すと、確かめるのを止める（設定のファイルに書く）
        page.eval("document.getElementById('update-check-toggle').click()")
        page.until("!!document.querySelector('.update-check') && document.querySelector('.update-check').disabled"
                   " && !document.getElementById('update-check-toggle').checked", what="止めた画面")
        self.assertFalse(self.fx.server.refresh_settings().updates.check)
        self.assertIsNone(page.eval("document.querySelector('.update-howto')"))
        page.raise_if_broken()
        self.assertEqual(page.errors, [])


if __name__ == "__main__":
    unittest.main()
