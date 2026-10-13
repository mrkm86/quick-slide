"""P4 の確かめ: **本物のブラウザ**で設定の画面を使う。

- 「この機械」: 設定のファイルを手で直す → 「再起動が必要」の帯と「再起動」ボタン → 押すと新しいサーバへつなぎ直す
  （ポートを変えたなら新しい URL へ移る・この機械に無いアドレスなら前の設定に戻った理由が出る）。サーバは本物のプロセス
- ブランドの編集: ロゴの角・フッタ・ページ番号・透かし（書式）を変えて保存する。見本に下の部品が出る。ロゴ以外の役割の
  画像は消えない
- スタイル: 「おまかせ」と型のチェック・エンジンの見せ方・画風を選んで保存する
- どの画面でも JavaScript の例外が出ない

Chrome が無い環境では skip する。
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from helpers import FAKES, REPO, png_bytes, start_server, temp_dir  # noqa: E402
from test_p4_restart import free_port  # noqa: E402

from quick_slide.ids import PRESET_STYLE_STANDARD_ID  # noqa: E402

from . import browser  # noqa: E402

SETTLE_SECONDS = 0.8


def settle_eval(page: browser.Page, expression: str, seconds: float = 60.0, what: str = ""):
    """画面が移っている間（別の URL へ行く・読み直す）も待つ。移っている途中の評価の失敗は数えない。"""
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            last = page.eval(expression)
        except (browser.JsError, RuntimeError):
            last = None
        if last:
            return last
        time.sleep(0.25)
    raise AssertionError(f"{seconds:.0f} 秒待っても真にならなかった: {what or expression}（最後の値 {last!r}）")


class RestartButtonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not browser.available():
            raise unittest.SkipTest("Chrome が無い環境")

    def setUp(self) -> None:
        self.tmp = temp_dir(self, "quick-slide-p4-live-")
        self.home = self.tmp / "home"
        (self.home / "config").mkdir(parents=True)
        self.port = free_port()
        self.write({"server": {"port": self.port}})
        self.env = {**os.environ, "PYTHONPATH": str(REPO / "src"), "PYTHONDONTWRITEBYTECODE": "1",
                    "QUICK_SLIDE_BROWSER": "manual", "QUICK_SLIDE_CLAUDE": str(FAKES / "refuse.py"),
                    "QUICK_SLIDE_CODEX_TEXT": str(FAKES / "refuse.py")}
        self.assertTrue(self.cli("start")["ok"])
        self.addCleanup(self.cli, "stop")
        self.br = browser.Browser()
        self.addCleanup(self.br.close)

    def write(self, data: dict) -> None:
        (self.home / "config" / "settings.json").write_text(json.dumps(data), encoding="utf-8")

    def cli(self, *args: str) -> dict:
        r = subprocess.run([sys.executable, "-m", "quick_slide.server", "--home", str(self.home), *args],
                           env=self.env, capture_output=True, text=True, timeout=60, cwd=str(REPO))
        return json.loads(r.stdout.strip().splitlines()[-1])

    def record(self) -> dict:
        return json.loads((self.home / "runtime" / "server.json").read_text())

    def open_server_screen(self) -> browser.Page:
        page = self.br.open(f"http://127.0.0.1:{self.port}/settings/server")
        page.until("!!document.querySelector('.restart-banner button')", what="再起動の帯とボタン")
        return page

    def test_the_button_restarts_and_moves_to_the_new_url(self) -> None:
        old = self.record()["instance_id"]
        new_port = free_port()
        self.write({"server": {"port": new_port}})   # 手で直した
        page = self.open_server_screen()
        self.assertIn(f"127.0.0.1:{new_port}", page.eval("document.querySelector('.restart-banner').textContent"))
        page.eval("document.querySelector('.restart-banner .restart-now').click()")
        settle_eval(page, f"location.port === '{new_port}' && location.pathname === '/settings/server'"
                          " && document.querySelectorAll('.setting-choice').length === 2", what="新しい URL で開き直すこと")
        settle_eval(page, "Array.from(document.querySelectorAll('.toast')).some(t => t.textContent.indexOf('再起動しました') >= 0)",
                    what="再起動しましたの知らせ")
        self.assertNotIn("restarted", page.eval("location.search"), "印は URL から消す")
        self.assertFalse(page.eval("!!document.querySelector('.restart-banner')"), "もう再起動は要らない")
        time.sleep(SETTLE_SECONDS)
        page.raise_if_broken()
        self.assertEqual(page.errors, [])
        rec = self.record()
        self.assertNotEqual(rec["instance_id"], old)
        self.assertEqual(rec["port"], new_port)

    def test_an_address_that_does_not_work_comes_back_with_the_reason(self) -> None:
        old = self.record()["instance_id"]
        self.write({"server": {"port": self.port, "host": "203.0.113.7"}})
        page = self.open_server_screen()
        # 他の画面からも気づける（上の帯の横のリンク）
        home = self.br.open(f"http://127.0.0.1:{self.port}/")
        home.until("!!document.querySelector('.restart-link')", what="ほかの画面の「再起動が必要」のリンク")
        page.eval("document.querySelector('.restart-banner .restart-now').click()")
        settle_eval(page, "Array.from(document.querySelectorAll('.toast--error')).some(t => t.textContent.indexOf("
                          "'新しい設定で起動できなかったので、前の設定に戻しました') >= 0)", what="前の設定に戻した知らせ")
        self.assertEqual(page.eval("location.port"), str(self.port), "前の URL のまま")
        time.sleep(SETTLE_SECONDS)
        page.raise_if_broken()
        self.assertEqual(page.errors, [])
        self.assertEqual(home.errors, [])
        self.assertNotEqual(self.record()["instance_id"], old)


class SettingsScreensTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not browser.available():
            raise unittest.SkipTest("Chrome が無い環境")

    def setUp(self) -> None:
        self.fx = start_server(self)
        self.br = browser.Browser()
        self.addCleanup(self.br.close)

    def upload(self, size=(400, 100)) -> str:
        r = self.fx.request("POST", "/api/settings/images", raw_body=png_bytes(*size), session=True)
        self.assertEqual(r.status, 201, r.body)
        return r.json()["asset_id"]

    def open(self, path: str, ready: str, what: str) -> browser.Page:
        page = self.br.open(self.fx.base_url + path)
        page.until(ready, seconds=20, what=what)
        time.sleep(SETTLE_SECONDS)
        page.raise_if_broken()
        return page

    def test_brand_format_and_logo_corner(self) -> None:
        logo, extra = self.upload(), self.upload((300, 300))
        config = {"schema_version": 1, "colors": {"primary": "#C8102E", "accent": "#1D4ED8"},
                  "logo": {"variant": "transparent", "placement": {"corner": "bottom-right"}},
                  "footer": {"text": "", "style": {"position": "footer_left"}}}
        r = self.fx.post("/api/settings/brands", {"name": "書式のブランド", "config": config, "assets": [
            {"asset_id": logo, "role": "logo_transparent"}, {"asset_id": extra, "role": "optional", "label": "差し絵"}]})
        self.assertEqual(r.status, 201, r.body)
        bid = r.json()["item"]["id"]
        page = self.open(f"/settings/brands/{bid}", "!!document.querySelector('.carousel .sample .sample__logo')",
                         "ブランドの編集と見本のロゴ")
        page.eval("(function(){ var f = document.querySelector('.brand-format input[type=text]'); f.value = '試験の資料';"
                  " f.dispatchEvent(new Event('input', {bubbles: true}));"
                  " var c = document.querySelector('.brand-format input[type=checkbox]'); c.click();"
                  " var w = document.querySelectorAll('.brand-format input[type=text]')[1]; w.value = '配布禁止';"
                  " w.dispatchEvent(new Event('input', {bubbles: true}));"
                  " Array.from(document.querySelectorAll('.corner-choice')).find(b => b.textContent.indexOf('左下') >= 0).click(); })()")
        # 表紙・章扉にはフッタとページ番号が入らない（エンジンの決まり）。中身のページ（箇条書き）の見本で確かめる
        page.until("!!document.querySelector('.sample--cover .sample__watermark') && !document.querySelector('.sample--cover .sample__footer')",
                   what="表紙の見本は透かしだけ")
        page.eval("document.querySelectorAll('.carousel__dot')[2].click()")
        page.until("!!document.querySelector('.sample--bullets .sample__pageno') && !!document.querySelector('.sample .sample__watermark')"
                   " && !!document.querySelector('.sample .sample__footer')", what="見本に下の部品が出ること")
        page.until("Array.from(document.querySelectorAll('.settings-actions button')).some(b => b.textContent === '保存する' && !b.disabled)",
                   what="保存できること")
        # 見本のロゴは左下・フッタはその右
        left = page.eval("parseFloat(document.querySelector('.sample .sample__logo').style.left)")
        foot = page.eval("parseFloat(document.querySelector('.sample .sample__footer').style.left)")
        self.assertLess(left, 10)
        self.assertGreater(foot, left)
        page.eval("Array.from(document.querySelectorAll('.settings-actions button')).find(b => b.textContent === '保存する').click()")
        page.until("document.querySelector('.title-save-state').textContent === '保存済み'", what="保存済み")
        page.raise_if_broken()
        saved = next(b for b in self.fx.get("/api/settings").json()["brands"] if b["id"] == bid)
        self.assertEqual(saved["config"]["format"], {"page_numbers": True, "watermark": "配布禁止"})
        self.assertEqual(saved["config"]["footer"]["text"], "試験の資料")
        self.assertEqual(saved["config"]["logo"], {"variant": "transparent", "placement": {"corner": "bottom-left"}})
        self.assertEqual(sorted(a["role"] for a in saved["assets"]), ["logo_transparent", "optional"], "差し絵は消えない")
        # 一覧: 書式はブランドの編集画面で設定するので、「書式を登録」は置かない
        page = self.open("/settings/brands", "document.querySelectorAll('.setting-row').length >= 2", "ブランドの一覧")
        self.assertFalse(page.eval("Array.from(document.querySelectorAll('button')).some(b => b.textContent === '書式を登録')"))
        self.assertEqual(page.errors, [])

    def test_style_types_engine_style_and_image_look(self) -> None:
        page = self.open("/settings/styles", "document.querySelectorAll('.pattern-choice').length === 30", "型の一覧が出ること")
        page.eval("Array.from(document.querySelectorAll('.settings-list button')).find(b => b.textContent.indexOf('新しい') >= 0).click()")
        page.until("document.querySelector('.settings-editor h2').textContent.indexOf('複写') >= 0", what="複写の欄")
        self.assertTrue(page.eval("document.querySelector('.pattern-choice input[value=auto]').checked"), "新しく作るときはおまかせ")
        # 型を選ぶと「おまかせ」が外れる
        page.eval("(function(){ ['cover','statement','bullets','closing'].forEach(function(v){"
                  " document.querySelector('.pattern-choice input[value=\"' + v + '\"]').click(); }); })()")
        self.assertFalse(page.eval("document.querySelector('.pattern-choice input[value=auto]').checked"))
        self.assertIn("4種類", page.eval("document.querySelector('.pattern-picker__status').textContent"))
        page.eval("(function(){ var i = document.querySelector('.settings-editor input[type=text]'); i.value = '4 つの型';"
                  " i.dispatchEvent(new Event('input'));"
                  " var sel = Array.from(document.querySelectorAll('.settings-editor select'));"
                  " var pitch = sel.find(s => Array.from(s.options).some(o => o.value === 'infographic')); pitch.value = 'infographic';"
                  " var look = sel.find(s => Array.from(s.options).some(o => o.value === 'handdrawn')); look.value = 'handdrawn';"
                  " Array.from(document.querySelectorAll('.settings-editor button')).find(b => b.textContent === '保存する').click(); })()")
        page.until("Array.from(document.querySelectorAll('.settings-list button')).some(b => b.textContent === '4 つの型')",
                   what="作ったスタイルが一覧に出ること")
        page.raise_if_broken()
        saved = next(s for s in self.fx.get("/api/settings").json()["styles"] if s["name"] == "4 つの型")
        self.assertEqual(saved["config"]["allowed_layouts"], ["cover", "statement", "bullets", "closing"])
        self.assertEqual(saved["config"]["engine_style"], {"presentation": "standard", "pitch": "infographic"})
        self.assertEqual(saved["config"]["image_look"], "handdrawn")
        self.assertEqual(saved["origin"]["id"], PRESET_STYLE_STANDARD_ID)
        # 「おまかせに戻す」
        page.eval("Array.from(document.querySelectorAll('.settings-list button')).find(b => b.textContent === '4 つの型').click()")
        page.until("document.querySelector('.settings-editor h2').textContent.indexOf('編集') >= 0", what="編集の欄")
        page.eval("Array.from(document.querySelectorAll('.pattern-picker button')).find(b => b.textContent === 'おまかせに戻す').click()")
        self.assertTrue(page.eval("document.querySelector('.pattern-choice input[value=auto]').checked"))
        self.assertEqual(page.eval("document.querySelectorAll('.pattern-choice input:checked').length"), 1)
        time.sleep(SETTLE_SECONDS)
        page.raise_if_broken()
        self.assertEqual(page.errors, [])


if __name__ == "__main__":
    unittest.main()
