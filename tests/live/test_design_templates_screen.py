"""デザインテンプレートの設定画面（PR 3）: **本物のブラウザ**で、ブランドの詳細の下の一覧と編集画面を使う。

- 一覧: 名前・版の札・形式の札・「編集」と ⋮（複製・エクスポート・削除）。追加・インポート（作る／衝突の選択）・書き出し
- 編集: 保存は今の版の上書き、▾ の「新しい版」で v2、版の一覧で前の版をコピーして作る（v3・由来 v1）
- 未保存のまま画面を移ると確かめる・狭い画面（390px）で横にはみ出さない

編集画面の URL は、画面の中の移動（SS.navigate）で開く。Chrome が無い環境では skip する。
"""

from __future__ import annotations

import json
import sys
import time
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from helpers import start_server  # noqa: E402

from quick_slide import design_templates as dt  # noqa: E402

from . import browser  # noqa: E402

BASE = "/api/settings/design-templates"
SETTLE_SECONDS = 0.5


def doc(name, **kw):
    d = dt.new_document("placeholder", "placeholder", name)
    d.update(structure="## 1. 結論\n先に結論を書く", instructions="図は少なめにする")
    d.update(kw)
    return d


class Tabs:
    """クラスで 1 つ起こした Chrome の中に、試験ごとの新しいタブを開く。閉じるのは試験の終わり（タブも後片付けする）。"""

    def __init__(self, shared: "browser.Browser", case: unittest.TestCase) -> None:
        self.shared, self.case = shared, case

    def open(self, url: str) -> "browser.Page":
        page = self.shared.open(url)
        self.case.addCleanup(self.shut, page)
        return page

    def shut(self, page: "browser.Page") -> None:
        try:
            page.close()
        except OSError:
            pass
        if page in self.shared.pages:
            self.shared.pages.remove(page)
        try:   # タブそのものも閉じる（溜めると Chrome が重くなる）
            urllib.request.urlopen(f"http://127.0.0.1:{self.shared.port}/json/close/{page.ws_url.rsplit('/', 1)[1]}", timeout=10).close()
        except OSError:
            pass


class ScreenBase(unittest.TestCase):
    width = 1280
    height = 1400
    shared: "browser.Browser | None" = None

    @classmethod
    def setUpClass(cls) -> None:
        if not browser.available():
            raise unittest.SkipTest("Chrome が無い環境")
        # Chrome はクラスで 1 つ（試験ごとに起こすと、並列で走らせたときに重い）。サーバは試験ごとに新しくして、独立を保つ
        cls.shared = browser.Browser(width=cls.width, height=cls.height)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.shared is not None:
            cls.shared.close()
            cls.shared = None

    def setUp(self) -> None:
        self.fx = start_server(self)
        with self.fx.server.store_lock:
            store = self.fx.store
            config = json.loads(json.dumps(store.brand_revision(store.preset_brand_revision_id())["config"]))
            self.bid = store.create_brand("試験のブランド", config)["id"]
        self.br = Tabs(self.shared, self)

    # --- API ---
    def create(self, name, **kw):
        r = self.fx.post(BASE, {"brand_id": self.bid, "document": doc(name, **kw)})
        self.assertEqual(201, r.status, r.body)
        return r.json()

    def templates(self):
        r = self.fx.get(f"{BASE}?brand_id={self.bid}")
        self.assertEqual(200, r.status, r.body)
        return r.json()["templates"]

    def view(self, tid):
        r = self.fx.get(f"{BASE}/{tid}")
        self.assertEqual(200, r.status, r.body)
        return r.json()

    # --- 画面 ---
    def open_brand(self) -> browser.Page:
        page = self.br.open(f"{self.fx.base_url}/settings/brands/{self.bid}")
        page.until("!!document.querySelector('#design-templates') && !document.querySelector('#design-templates .setting-rows').textContent.includes('読み込')"
                   " && !!document.querySelector('#design-templates .setting-rows > *')", what="テンプレートの一覧")
        page.raise_if_broken()
        return page

    def go(self, page: browser.Page, path: str, ready: str, what: str) -> None:
        page.eval(f"SS.navigate({json.dumps(path)})")
        page.until(ready, what=what)
        time.sleep(SETTLE_SECONDS)
        page.raise_if_broken()

    def open_editor_url(self, tid: str) -> browser.Page:
        """編集画面の URL を直接開く（再読み込み・ブックマークと同じ）。"""
        page = self.br.open(f"{self.fx.base_url}/settings/brands/{self.bid}/templates/{tid}")
        page.until("!!document.querySelector('.template-editor')", what="編集画面（URL を直接）")
        time.sleep(SETTLE_SECONDS)
        page.raise_if_broken()
        return page

    def open_editor(self, page: browser.Page, tid: str) -> None:
        self.go(page, f"/settings/brands/{self.bid}/templates/{tid}", "!!document.querySelector('.template-editor')", "編集画面")

    def click(self, page, selector: str, text: str | None = None) -> None:
        if text is None:
            page.eval(f"document.querySelector({json.dumps(selector)}).click()")
        else:
            page.eval(f"Array.from(document.querySelectorAll({json.dumps(selector)})).find(b => b.textContent.trim() === {json.dumps(text)}).click()")

    def fill(self, page, selector: str, value: str) -> None:
        page.eval(f"(function(){{ var e = document.querySelector({json.dumps(selector)}); e.value = {json.dumps(value)};"
                  " e.dispatchEvent(new Event('input', {bubbles: true})); })()")

    def tab(self, page, name: str) -> None:
        self.click(page, f"#template-tab-{name}")
        page.until(f"!document.querySelector('#template-{name}').hidden", what=f"{name}のタブ")

    def save_button(self, page) -> str:
        return "document.querySelector('.template-split button')"

    def row_menu(self, page, name: str, item: str) -> None:
        page.eval("Array.from(document.querySelectorAll('#design-templates .setting-row')).find(r => r.textContent.indexOf("
                  f"{json.dumps(name)}) >= 0).querySelector('button[aria-haspopup=menu]').click()")
        page.until("!!document.querySelector('.menu')", what="メニュー")
        self.click(page, ".menu button", item)

    def no_overflow(self, page) -> None:
        self.assertEqual(page.eval("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), True,
                         f"横にはみ出している: {page.eval('document.documentElement.scrollWidth')} > {page.eval('document.documentElement.clientWidth')}")


class ListTests(ScreenBase):
    def test_brand_detail_shows_the_section_and_rows(self) -> None:
        a = self.create("週報", modes=["presentation"])
        b = self.create("営業ピッチ", modes=["pitch", "presentation"])
        page = self.open_brand()
        self.assertEqual(page.eval("document.querySelector('#design-templates h2').textContent"), "デザインテンプレート")
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('#design-templates .template-section-head button')).map(b => b.textContent)"),
                         ["インポート", "追加する"])
        self.assertEqual(page.eval("document.querySelectorAll('#design-templates .setting-row').length"), 2)
        # 色・ロゴ・プレビューなど、ブランドの既存の部分はそのまま
        self.assertTrue(page.eval("!!document.querySelector('.carousel') && !!document.querySelector('.danger-zone')"))
        self.assertTrue(page.eval("document.querySelector('#design-templates').compareDocumentPosition(document.querySelector('.danger-zone')) & Node.DOCUMENT_POSITION_FOLLOWING"))
        row = "Array.from(document.querySelectorAll('#design-templates .setting-row')).find(r => r.textContent.indexOf('営業ピッチ') >= 0)"
        self.assertEqual(page.eval(f"{row}.querySelector('.template-list__title .chip.page__ver').textContent"), "v1")
        self.assertEqual(page.eval(f"Array.from({row}.querySelectorAll('.kind-tag')).map(t => t.dataset.mode)"), ["pitch", "presentation"])
        self.assertEqual(page.eval(f"{row}.querySelector('a.btn').textContent"), "編集")
        self.assertEqual(page.eval(f"{row}.querySelector('a.btn').getAttribute('href')"), f"/settings/brands/{self.bid}/templates/{b['template_id']}")
        page.eval(f"{row}.querySelector('button[aria-haspopup=menu]').click()")
        page.until("!!document.querySelector('.menu')", what="メニュー")
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('.menu li')).map(l => l.className === 'menu__sep' ? '-' : l.textContent)"),
                         ["複製", "エクスポート", "-", "削除"])
        self.assertEqual(a["revision"], 1)
        self.assertEqual(page.errors, [])

    def test_add_duplicate_and_delete(self) -> None:
        self.create("元のテンプレート")
        page = self.open_brand()
        # 追加: 名前だけ聞いて、空の v1 を作って編集画面へ
        self.click(page, "#design-templates button", "追加する")
        page.until("!!document.querySelector('.modal input')", what="名前の入力")
        self.fill(page, ".modal input", "新しいテンプレート")
        self.click(page, ".modal button.btn--primary")
        page.until("!!document.querySelector('.template-editor')", what="編集画面")
        names = [t["name"] for t in self.templates()]
        self.assertIn("新しいテンプレート", names)
        self.assertEqual(page.eval("document.querySelector('#template-basic input').value"), "新しいテンプレート")
        # 複製: 別のテンプレート（v1・「のコピー」）
        self.go(page, f"/settings/brands/{self.bid}", "!!document.querySelector('#design-templates .setting-row')", "ブランドの詳細")
        page.until("document.querySelectorAll('#design-templates .setting-row').length === 2", what="2 件")
        self.row_menu(page, "元のテンプレート", "複製")
        page.until("!!document.querySelector('.template-editor')", what="複製のあとの編集画面")
        copies = [t for t in self.templates() if t["name"] == "元のテンプレートのコピー"]
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0]["revision"], 1)
        # 削除: 確認のあと、一覧から外れる（版の控えは残る）
        self.go(page, f"/settings/brands/{self.bid}", "document.querySelectorAll('#design-templates .setting-row').length === 3", "3 件")
        page.eval("window.confirm = function (m) { window.__asked = m; return true; }")
        self.row_menu(page, "元のテンプレートのコピー", "削除")
        page.until("document.querySelectorAll('#design-templates .setting-row').length === 2", what="削除後の一覧")
        self.assertIn("削除します", page.eval("window.__asked"))
        self.assertNotIn("元のテンプレートのコピー", [t["name"] for t in self.templates()])
        self.assertEqual(self.view(copies[0]["template_id"])["document"]["name"], "元のテンプレートのコピー")
        self.assertEqual(page.errors, [])


class TransferTests(ScreenBase):
    def import_text(self, page, text: str) -> None:
        page.eval("(function(){ var input = document.querySelector('#design-templates input[type=file]');"
                  f" var dt = new DataTransfer(); dt.items.add(new File([{json.dumps(text)}], 'template.md', {{type: 'text/markdown'}}));"
                  " input.files = dt.files; input.dispatchEvent(new Event('change', {bubbles: true})); })()")
        page.until("!!document.querySelector('.template-import-modal')", what="取り込みの確認")

    def test_export_downloads_the_markdown_without_asking(self) -> None:
        t = self.create("書き出すテンプレート")
        page = self.open_brand()
        page.eval("window.__saved = []; window.confirm = function () { window.__asked = true; return true; };"
                  " HTMLAnchorElement.prototype.click = function () { if (this.download) { window.__saved.push(this.download); } };")
        self.row_menu(page, "書き出すテンプレート", "エクスポート")
        page.until("window.__saved.length === 1", what="書き出し")
        self.assertEqual(page.eval("window.__saved[0]"), "qs-テンプレート-書き出すテンプレート.md")
        self.assertIsNone(page.eval("window.__asked"))
        r = self.fx.get(f"{BASE}/{t['template_id']}/markdown")
        self.assertEqual(200, r.status)
        self.assertIn("quick-slide-template", r.body.decode("utf-8"))
        self.assertEqual(page.errors, [])

    def test_import_creates_and_resolves_a_conflict(self) -> None:
        t = self.create("渡すテンプレート")
        text = self.fx.get(f"{BASE}/{t['template_id']}/markdown").body.decode("utf-8")
        other = text.replace("渡すテンプレート", "別の名前", 1)
        page = self.open_brand()
        # 同じ ID がある → 新しい版として取り込む
        self.import_text(page, text.replace("先に結論を書く", "先に結論を書く（改訂）"))
        self.assertIn("すでにあります", page.eval("document.querySelector('.template-import-modal').textContent"))
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('.template-import-modal button')).map(b => b.textContent)"),
                         ["やめる", "別のテンプレートとして追加", "v2 として取り込む"])
        self.click(page, ".template-import-modal button", "v2 として取り込む")
        page.until("!document.querySelector('.template-import-modal') && !!document.querySelector('#design-templates .chip.page__ver') &&"
                   " document.querySelector('#design-templates .chip.page__ver').textContent === 'v2'", what="v2 になった一覧")
        self.assertEqual(self.view(t["template_id"])["revision"], 2)
        self.assertIn("改訂", self.view(t["template_id"])["document"]["structure"])
        # 別のテンプレートとして追加
        self.import_text(page, text)
        self.click(page, ".template-import-modal button", "別のテンプレートとして追加")
        page.until("document.querySelectorAll('#design-templates .setting-row').length === 2", what="追加された一覧")
        self.assertEqual(sorted(x["name"] for x in self.templates()), ["渡すテンプレート", "渡すテンプレート（インポート）"])
        # 消したテンプレートと同じ ID は、復活させず「別のテンプレートとして追加」だけ
        self.fx.post(f"{BASE}/{t['template_id']}/delete", {"expected_revision_id": self.view(t["template_id"])["current_revision_id"]})
        page.eval("SS.navigate(location.pathname, true)")
        page.until("document.querySelectorAll('#design-templates .setting-row').length === 1", what="削除後の一覧")
        self.import_text(page, text)
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('.template-import-modal button')).map(b => b.textContent)"),
                         ["やめる", "別のテンプレートとして追加"])
        self.click(page, ".template-import-modal button", "やめる")
        # ID の無いファイルは、そのまま作る（サーバが ID を決める）
        self.import_text(page, text.replace(f"id: {t['template_id']}\n", "").replace("渡すテンプレート", "IDの無いテンプレート", 1))
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('.template-import-modal button')).map(b => b.textContent)"), ["やめる", "取り込む"])
        self.click(page, ".template-import-modal button", "取り込む")
        page.until("document.querySelectorAll('#design-templates .setting-row').length === 2", what="取り込んだ一覧")
        self.assertIn("IDの無いテンプレート", [x["name"] for x in self.templates()])
        self.assertEqual(page.errors, [])

    def test_a_broken_file_is_refused_with_the_server_message(self) -> None:
        self.create("使うテンプレート")
        page = self.open_brand()
        page.eval("(function(){ var input = document.querySelector('#design-templates input[type=file]');"
                  " var dt = new DataTransfer(); dt.items.add(new File(['これはテンプレートではない'], 'x.md'));"
                  " input.files = dt.files; input.dispatchEvent(new Event('change', {bubbles: true})); })()")
        page.until("!!document.querySelector('.toast--error')", what="断りの通知")
        self.assertFalse(page.eval("!!document.querySelector('.template-import-modal')"))
        self.assertEqual(page.errors, [])


class EditorTests(ScreenBase):
    def test_editor_chrome(self) -> None:
        t = self.create("見た目の確認")
        page = self.open_brand()
        self.go(page, f"/settings/brands/{self.bid}/templates/{t['template_id']}", "!!document.querySelector('.template-editor')", "編集画面")
        self.assertEqual(page.eval("document.querySelector('.template-editor h1').textContent"), "デザインテンプレートの編集")
        self.assertEqual(page.eval("document.querySelector('.notice.notice--info.template-caution').textContent.trim()"),
                         "AI はここに書いた指示を、いつも守れるとは限りません。できあがった資料で、指示どおりになっているか確かめてください。")
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('.template-tab')).map(b => b.textContent)"), ["基本", "構成", "追加指示", "リソース"])
        self.assertEqual(page.eval("document.querySelector('.template-ver').textContent"), "v1")
        self.assertTrue(page.eval("document.querySelector('.template-revert').hidden"))
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('.template-split button')).map(b => b.textContent || b.getAttribute('aria-label'))"),
                         ["保存する", "保存のしかたを選ぶ"])
        # 通常の画面には、保存した指示の確認（検証用）を出さない
        self.assertFalse(page.eval("!!document.querySelector('.template-debug')"))
        # 構成は描いた状態（見出しが <h4>）で、鉛筆で書く状態になる
        self.tab(page, "structure")
        self.assertTrue(page.eval("!!document.querySelector('#template-structure .template-md h4')"))
        self.click(page, "#template-structure .template-section__tools .template-mode")
        self.assertFalse(page.eval("document.querySelector('#template-structure textarea').hidden"))
        # 例: 「？」で開き、コピーすると畳む（コピーできない環境では開いたまま）
        self.click(page, "#template-structure .template-guide__toggle")
        self.assertFalse(page.eval("document.querySelector('#template-guide-structure').hidden"))
        self.tab(page, "resources")
        self.click(page, "#template-resources .template-guide__toggle")
        self.click(page, "#template-guide-resources button", "この例を追加")
        page.until("document.querySelectorAll('#template-resources .template-row').length === 1", what="例の行")
        self.assertTrue(page.eval("document.querySelector('#template-guide-resources').hidden"))
        self.assertEqual(page.eval("document.querySelector('#template-resources .template-row select').value"), "directory")
        self.assertEqual(page.eval("document.querySelector('#template-resources .template-pick button').textContent"), "選ぶ…")
        self.assertEqual(page.errors, [])

    def test_create_edit_save_overwrite_new_version_and_copy_from_old(self) -> None:
        t = self.create("編集するテンプレート", structure="", instructions="")
        tid = t["template_id"]
        page = self.open_brand()
        self.open_editor(page, tid)
        # 基本・構成・追加指示・リソースを書いて保存（上書き: 版は増えない）
        self.fill(page, "#template-basic input", "編集したテンプレート")
        self.tab(page, "structure")
        self.fill(page, "#template-structure textarea", "## 表紙\n題材を書く\n\n| 列 | 値 |\n|---|---|\n| a | b |")
        self.tab(page, "instructions")
        self.fill(page, "#template-instructions textarea", "- 白地を広く取る\n- **数字**は`表`で示す")
        self.tab(page, "resources")
        self.click(page, "#template-resources .gap__add")
        page.until("document.querySelectorAll('#template-resources .template-row input').length >= 2", what="リソースの行")
        self.fill(page, "#template-resources .template-row input", "画像集")
        page.eval("(function(){ var i = document.querySelectorAll('#template-resources .template-row input[type=text]')[1]; i.value = '~/Pictures/x';"
                  " i.dispatchEvent(new Event('input', {bubbles: true})); })()")
        self.assertEqual(page.eval("document.querySelector('.template-toolbar__status .small').textContent"), "未保存の変更があります")
        self.assertFalse(page.eval("document.querySelector('.template-revert').hidden"))
        self.click(page, ".template-split button:first-child")
        page.until("document.querySelector('.template-toolbar__status .small').textContent === ''", what="保存の終わり")
        v = self.view(tid)
        self.assertEqual((v["revision"], v["document"]["name"]), (1, "編集したテンプレート"))
        self.assertEqual([r["name"] for r in v["document"]["resources"]], ["画像集"])
        self.assertIn("表紙", v["document"]["structure"])
        self.assertTrue(page.eval("document.querySelector('.template-revert').hidden"))
        # 戻ってもう一度開くと、保存した内容が復元される（描いた状態で）
        self.go(page, f"/settings/brands/{self.bid}", "!!document.querySelector('#design-templates .setting-row')", "ブランドの詳細")
        self.open_editor(page, tid)
        self.assertEqual(page.eval("document.querySelector('#template-basic input').value"), "編集したテンプレート")
        self.tab(page, "structure")
        self.assertTrue(page.eval("!!document.querySelector('#template-structure .template-md-table table')"), "表は枠の中の表として描く")
        self.tab(page, "instructions")
        self.assertTrue(page.eval("!!document.querySelector('#template-instructions .template-md ul li strong') && !!document.querySelector('#template-instructions .template-md code')"))
        self.tab(page, "resources")
        self.assertIn("画像集", page.eval("document.querySelector('#template-resources .template-row__title').textContent"))
        # ▾ から新しい版（v2・由来 v1）
        self.tab(page, "basic")
        self.fill(page, "#template-basic input", "編集したテンプレート 2")
        self.click(page, ".template-save-more")
        page.until("!!document.querySelector('.menu')", what="保存のメニュー")
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('.menu button')).map(b => b.textContent)"),
                         ["v1 を上書き保存", "v2 として保存（新しい版）"])
        self.click(page, ".menu button", "v2 として保存（新しい版）")
        page.until("document.querySelector('.template-ver').textContent === 'v2'", what="v2")
        v = self.view(tid)
        self.assertEqual((v["revision"], v["document"]["based_on"]), (2, 1))
        # 上書きでは版は増えない
        self.fill(page, "#template-basic input", "編集したテンプレート 3")
        self.click(page, ".template-split button:first-child")
        page.until("document.querySelector('.template-toolbar__status .small').textContent === ''", what="上書きの終わり")
        self.assertEqual(self.view(tid)["revision"], 2)
        self.assertEqual(self.view(tid)["document"]["name"], "編集したテンプレート 3")
        # 版の一覧: v2（v1 から）と v1
        self.click(page, ".template-ver")
        page.until("document.querySelectorAll('.template-version').length === 2", what="版の一覧")
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('.template-version__no')).map(b => b.textContent)"), ["v2（v1 から）・最新", "v1"])
        self.assertIn("画像集", page.eval("document.querySelector('.template-version-detail').textContent"))
        self.assertIn("~/Pictures/x", page.eval("document.querySelector('.template-version-detail').textContent"))
        # 前の版（v1）をコピーして v3 を作る（由来 v1。最新の v2 も v1 も変わらない）
        page.eval("Array.from(document.querySelectorAll('.template-version')).find(b => b.textContent.indexOf('v1') === 0 && b.textContent.indexOf('v2') < 0).click()")
        page.until("!!document.querySelector('.template-version-call button')", what="コピーの操作")
        self.click(page, ".template-version-call button", "コピーして v3 を作成")
        page.until("document.querySelector('.template-ver').textContent === 'v3 作成中'", what="作成中の札")
        self.assertTrue(page.eval("document.querySelector('.template-save-more').hidden"))
        self.assertEqual(page.eval("document.querySelector('.template-split button').textContent"), "v3 として保存")
        self.assertEqual(page.eval("document.querySelector('#template-basic input').value"), "編集したテンプレート")
        self.click(page, ".template-split button:first-child")
        page.until("document.querySelector('.template-ver').textContent === 'v3'", what="v3")
        v = self.view(tid)
        self.assertEqual((v["revision"], v["document"]["based_on"], v["document"]["name"]), (3, 1, "編集したテンプレート"))
        revisions = self.fx.get(f"{BASE}/{tid}/revisions").json()["revisions"]
        self.assertEqual([(r["revision"], r["based_on"]) for r in revisions], [(3, 1), (2, 1), (1, None)])
        self.assertEqual(page.errors, [])

    def test_unsaved_changes_are_asked_before_leaving(self) -> None:
        t = self.create("離れる確認")
        page = self.open_brand()
        self.open_editor(page, t["template_id"])
        page.eval("window.__asked = []; window.confirm = function (m) { window.__asked.push(m); return window.__answer; }; window.__answer = false;")
        self.fill(page, "#template-basic input", "書き換えた")
        page.eval(f"SS.navigate('/settings/brands/{self.bid}')")
        self.assertEqual(page.eval("window.__asked.length"), 1)
        self.assertIn("保存していない", page.eval("window.__asked[0]"))
        self.assertTrue(page.eval("!!document.querySelector('.template-editor')"), "断ったら編集画面のまま")
        self.assertEqual(page.eval("document.querySelector('#template-basic input').value"), "書き換えた")
        # 戻す: 確認のうえ、保存済みの内容に戻る
        page.eval("window.__answer = true;")
        self.click(page, ".template-revert")
        self.assertEqual(page.eval("document.querySelector('#template-basic input').value"), "離れる確認")
        self.assertTrue(page.eval("document.querySelector('.template-revert').hidden"))
        self.assertEqual(page.eval("SS.settingsLeave"), None)
        # 保存済みなら黙って移れる。許したときは移る
        self.fill(page, "#template-basic input", "また書き換えた")
        page.eval(f"SS.navigate('/settings/brands/{self.bid}')")
        page.until("!!document.querySelector('#design-templates')", what="ブランドの詳細へ")
        self.assertEqual(self.view(t["template_id"])["document"]["name"], "離れる確認", "保存していない編集は送られない")
        self.assertEqual(page.errors, [])

    def test_a_conflict_is_shown_and_not_overwritten(self) -> None:
        t = self.create("競合の確認")
        tid = t["template_id"]
        page = self.open_brand()
        self.open_editor(page, tid)
        # 別の画面（ここでは API）が先に更新する
        d = dict(t["document"], name="先に更新された")
        r = self.fx.post(f"{BASE}/{tid}", {"document": d, "mode": "overwrite", "expected_revision_id": t["current_revision_id"]})
        self.assertEqual(r.status, 200, r.body)
        self.fill(page, "#template-basic input", "あとから保存")
        self.click(page, ".template-split button:first-child")
        page.until("!!document.querySelector('.notice--error')", what="競合の知らせ")
        self.assertEqual(self.view(tid)["document"]["name"], "先に更新された")
        self.assertEqual(page.errors, [])

    def test_a_server_error_goes_to_the_right_tab(self) -> None:
        t = self.create("入力の誤り")
        page = self.open_brand()
        self.open_editor(page, t["template_id"])
        self.fill(page, "#template-basic input", "")
        self.tab(page, "resources")
        self.click(page, "#template-resources .gap__add")
        self.click(page, ".template-split button:first-child")
        page.until("!!document.querySelector('.notice--error')", what="入力の誤り")
        self.assertEqual(page.eval("document.querySelector('#template-tab-basic').getAttribute('aria-selected')"), "true")
        self.assertEqual(page.errors, [])


    def test_the_editor_opens_by_direct_url_and_after_reload(self) -> None:
        t = self.create("直接開く")
        page = self.open_editor_url(t["template_id"])
        self.assertEqual(page.eval("document.querySelector('#template-basic input').value"), "直接開く")
        self.fill(page, "#template-basic input", "保存してから再読み込み")
        self.click(page, ".template-split button:first-child")
        page.until("document.querySelector('.template-toolbar__status .small').textContent === ''", what="保存の終わり")
        self.assertEqual(self.view(t["template_id"])["document"]["name"], "保存してから再読み込み")
        page.eval("location.reload()")
        page.until("document.readyState === 'complete' && !!document.querySelector('.template-editor') && "
                   "document.querySelector('#template-basic input').value === '保存してから再読み込み'", what="再読み込み後の編集画面")
        self.assertEqual(page.errors, [])

    def test_browser_back_asks_before_dropping_unsaved_edits(self) -> None:
        t = self.create("戻るの確認")
        page = self.open_brand()
        self.open_editor(page, t["template_id"])
        url = page.eval("location.pathname")
        page.eval("window.__asked = []; window.confirm = function (m) { window.__asked.push(m); return window.__answer; }; window.__answer = false;")
        self.fill(page, "#template-basic input", "書き換えた")
        # 断る: URL は編集画面のまま・入力も残る
        page.eval("history.back()")
        page.until("window.__asked.length === 1", what="確認")
        time.sleep(SETTLE_SECONDS)
        self.assertEqual(page.eval("location.pathname"), url)
        self.assertEqual(page.eval("document.querySelector('#template-basic input').value"), "書き換えた")
        self.assertTrue(page.eval("!!document.querySelector('.template-editor')"))
        # 許す: 前の画面（ブランドの詳細）へ移る
        page.eval("window.__answer = true; history.back()")
        page.until("!!document.querySelector('#design-templates') && !document.querySelector('.template-editor')", what="ブランドの詳細へ")
        self.assertEqual(page.eval("location.pathname"), f"/settings/brands/{self.bid}")
        self.assertEqual(self.view(t["template_id"])["document"]["name"], "戻るの確認")
        self.assertEqual(page.errors, [])

    def test_leaving_warning_belongs_to_the_open_editor_only(self) -> None:
        a = self.create("A")
        b = self.create("B")
        page = self.open_brand()
        page.eval("window.confirm = function () { return true; };"
                  " window.__unload = function () { var e = new Event('beforeunload', {cancelable: true}); window.dispatchEvent(e); return e.defaultPrevented; };")
        self.open_editor(page, a["template_id"])
        self.fill(page, "#template-basic input", "A を書き換えた")
        self.assertTrue(page.eval("window.__unload()"), "未保存の編集があるあいだは警告する")
        # A の編集を捨てて B を開き、B を保存したら、もう警告しない
        self.go(page, f"/settings/brands/{self.bid}/templates/{b['template_id']}", "!!document.querySelector('#template-basic input') && document.querySelector('#template-basic input').value === 'B'", "B の編集画面")
        self.fill(page, "#template-basic input", "B を書き換えた")
        self.assertTrue(page.eval("window.__unload()"))
        self.click(page, ".template-split button:first-child")
        page.until("document.querySelector('.template-toolbar__status .small').textContent === ''", what="B の保存")
        self.assertFalse(page.eval("window.__unload()"), "保存したあとは警告しない")
        self.assertEqual(page.errors, [])

    def test_markdown_is_drawn_as_text_not_html(self) -> None:
        evil = "<img src=x onerror=\"window.__x=1\">\n\n<script>window.__y=1</script>\n\n- <b>太字</b>"
        t = self.create("安全な描画", instructions=evil, structure=evil)
        page = self.open_editor_url(t["template_id"])
        for name in ("structure", "instructions"):
            self.tab(page, name)
            self.assertFalse(page.eval(f"!!document.querySelector('#template-{name} .template-md img, #template-{name} .template-md script, #template-{name} .template-md b')"))
            self.assertIn("<script>", page.eval(f"document.querySelector('#template-{name} .template-md').textContent"))
        self.click(page, ".template-ver")
        page.until("!!document.querySelector('.template-version-detail .template-md')", what="版の中身")
        self.assertFalse(page.eval("!!document.querySelector('.template-version-detail .template-md img, .template-version-detail .template-md script')"))
        time.sleep(0.3)
        self.assertIsNone(page.eval("window.__x"))
        self.assertIsNone(page.eval("window.__y"))
        self.assertEqual(page.errors, [])

    def test_missing_resources_are_marked_on_the_card(self) -> None:
        res = [{"id": "r1", "name": "無いフォルダ", "kind": "directory", "path": "/no/such/dir-for-test", "usage": ""}]
        t = self.create("見つからない", resources=res)
        page = self.open_editor_url(t["template_id"])
        self.tab(page, "resources")
        page.until("document.querySelector('#template-resources .template-row__summary').textContent.includes('見つかりません')", what="見つかりませんの印")
        self.assertEqual(page.eval("document.querySelector('#template-resources .template-row__summary .template-row__missing').textContent"), "見つかりません")
        # 通常の画面には、保存した指示の確認（検証用）を出さない
        self.assertFalse(page.eval("!!document.querySelector('.template-debug')"))
        self.assertEqual(page.errors, [])


class ConflictTests(ScreenBase):
    def test_conflict_keeps_inputs_and_sends_exactly_one_request(self) -> None:
        t = self.create("競合の確認")
        tid = t["template_id"]
        page = self.open_brand()
        self.open_editor(page, tid)
        page.eval("window.__posts = 0; var f = window.fetch; window.fetch = function (u, o) {"
                  " if (o && o.method === 'POST' && String(u).indexOf('/design-templates/') >= 0) { window.__posts++; } return f.apply(this, arguments); };")
        d = dict(t["document"], name="先に更新された")
        r = self.fx.post(f"{BASE}/{tid}", {"document": d, "mode": "overwrite", "expected_revision_id": t["current_revision_id"]})
        self.assertEqual(r.status, 200, r.body)
        self.fill(page, "#template-basic input", "あとから保存")
        self.click(page, ".template-split button:first-child")
        page.until("!!document.querySelector('.template-editor .notice--error')", what="競合の知らせ")
        time.sleep(1.0)
        self.assertIn("先に変更", page.eval("document.querySelector('.template-editor .notice--error').textContent"))
        self.assertEqual(page.eval("document.querySelector('#template-basic input').value"), "あとから保存", "入力は残る")
        self.assertEqual(page.eval("window.__posts"), 1, "黙って送り直さない")
        self.assertEqual(self.view(tid)["document"]["name"], "先に更新された")
        self.assertFalse(page.eval("document.querySelector('.template-split button').disabled"), "もう一度押せる")
        self.assertEqual(page.errors, [])

    def test_import_choice_is_refused_when_another_session_saved_first(self) -> None:
        t = self.create("取り込み先")
        tid = t["template_id"]
        text = self.fx.get(f"{BASE}/{tid}/markdown").body.decode("utf-8")
        page = self.open_brand()
        page.eval("(function(){ var input = document.querySelector('#design-templates input[type=file]');"
                  f" var dt = new DataTransfer(); dt.items.add(new File([{json.dumps(text.replace('先に結論を書く', '取り込みの改訂'))}], 't.md'));"
                  " input.files = dt.files; input.dispatchEvent(new Event('change', {bubbles: true})); })()")
        page.until("!!document.querySelector('.template-import-modal')", what="取り込みの確認")
        # 確認を開いている間に、別の画面が保存する
        d = dict(t["document"], name="別の画面で更新")
        r = self.fx.post(f"{BASE}/{tid}", {"document": d, "mode": "overwrite", "expected_revision_id": t["current_revision_id"]})
        self.assertEqual(r.status, 200, r.body)
        before = self.view(tid)
        self.click(page, ".template-import-modal button", "v2 として取り込む")
        page.until("document.querySelector('.template-import-modal .reason').textContent.length > 0", what="断りの文")
        self.assertIn("更新されています", page.eval("document.querySelector('.template-import-modal .reason').textContent"))
        after = self.view(tid)
        self.assertEqual((after["revision"], after["current_revision_id"]), (before["revision"], before["current_revision_id"]))
        self.assertEqual(len(self.templates()), 1)
        self.assertEqual(page.errors, [])

    def test_missing_resource_after_import_is_marked(self) -> None:
        t = self.create("元", resources=[{"id": "r1", "name": "別のパソコンの素材", "kind": "file", "path": "/no/such/file.md", "usage": ""}])
        text = self.fx.get(f"{BASE}/{t['template_id']}/markdown").body.decode("utf-8")
        self.fx.post(f"{BASE}/{t['template_id']}/delete", {"expected_revision_id": t["current_revision_id"]})
        page = self.open_brand()
        page.eval("(function(){ var input = document.querySelector('#design-templates input[type=file]');"
                  f" var dt = new DataTransfer(); dt.items.add(new File([{json.dumps(text.replace('id: ' + t['template_id'] + chr(10), ''))}], 't.md'));"
                  " input.files = dt.files; input.dispatchEvent(new Event('change', {bubbles: true})); })()")
        page.until("!!document.querySelector('.template-import-modal')", what="取り込みの確認")
        self.assertIn("見つかりません", page.eval("document.querySelector('.template-import-modal').textContent"))
        self.click(page, ".template-import-modal button", "取り込む")
        page.until("!document.querySelector('.template-import-modal') && document.querySelectorAll('#design-templates .setting-row').length === 1", what="取り込み")
        new = self.templates()[0]
        page = self.open_editor_url(new["template_id"])
        self.tab(page, "resources")
        page.until("document.querySelector('#template-resources .template-row__summary').textContent.includes('見つかりません')", what="見つかりませんの印")
        self.assertEqual(page.errors, [])


class HistoryTests(ScreenBase):
    """戻る・進むで未保存の確認を断っても、履歴の項目は増えも減りもしない。"""

    def start(self):
        t = self.create("履歴の確認")
        self.tid = t["template_id"]
        self.brand_path = f"/settings/brands/{self.bid}"
        self.editor_path = f"{self.brand_path}/templates/{self.tid}"
        page = self.br.open(f"{self.fx.base_url}/settings")
        page.until("!!document.querySelector('.settings-index')", what="設定のトップ")
        page.eval("window.__asked = 0; window.__answer = false; window.confirm = function () { window.__asked++; return window.__answer; };")
        return page

    def nav(self, page, path, ready):
        page.eval(f"SS.navigate({json.dumps(path)})")
        page.until(ready, what=path)
        time.sleep(0.3)

    def settle(self, page, asked):
        page.until(f"window.__asked === {asked}", what="確認")
        time.sleep(1.0)
        page.raise_if_broken()

    def at(self, page, path):
        page.until(f"location.pathname === {json.dumps(path)}", what=f"{path} にいる")
        time.sleep(0.5)

    def test_declined_multi_step_back_leaves_history_alone(self) -> None:
        page = self.start()
        self.nav(page, "/settings/brands", "!!document.querySelector('.setting-row')")
        self.nav(page, self.brand_path, "!!document.querySelector('#design-templates')")
        self.nav(page, self.editor_path, "!!document.querySelector('.template-editor')")
        length = page.eval("history.length")
        self.fill(page, "#template-basic input", "書き換えた")
        page.eval("history.go(-2)")
        self.settle(page, 1)
        self.assertEqual(page.eval("location.pathname"), self.editor_path)
        self.assertEqual(page.eval("document.querySelector('#template-basic input').value"), "書き換えた")
        self.assertEqual(page.eval("history.length"), length)
        # 許すと、元の隣の項目へ順に動ける（途中の項目は消えていない）
        page.eval("window.__answer = true; history.back()")
        self.at(page, self.brand_path)
        page.eval("history.back()")
        self.at(page, "/settings/brands")
        page.eval("history.forward()")
        self.at(page, self.brand_path)
        page.eval("history.forward()")
        self.at(page, self.editor_path)
        self.assertEqual(page.errors, [])

    def test_declined_forward_adds_no_entry(self) -> None:
        page = self.start()
        self.nav(page, self.brand_path, "!!document.querySelector('#design-templates')")
        self.nav(page, self.editor_path, "!!document.querySelector('.template-editor')")
        page.eval("history.back()")
        page.until("!!document.querySelector('#design-templates') && !document.querySelector('.template-editor')", what="ブランドへ戻る")
        self.at(page, self.brand_path)
        length = page.eval("history.length")
        self.fill(page, ".title-input", "ブランド名を書き換えた")
        page.eval("history.forward()")
        self.settle(page, 1)
        self.assertEqual(page.eval("location.pathname"), self.brand_path)
        self.assertEqual(page.eval("document.querySelector('.title-input').value"), "ブランド名を書き換えた")
        self.assertEqual(page.eval("history.length"), length)
        # 次の「戻る」は、元の前の画面（設定のトップ）へ行く
        page.eval("window.__answer = true; history.back()")
        self.at(page, "/settings")
        page.eval("history.forward()")
        self.at(page, self.brand_path)
        page.eval("history.forward()")
        self.at(page, self.editor_path)
        self.assertEqual(page.errors, [])

    def test_declined_back_keeps_the_forward_entries(self) -> None:
        page = self.start()
        self.nav(page, self.brand_path, "!!document.querySelector('#design-templates')")
        self.nav(page, self.editor_path, "!!document.querySelector('.template-editor')")
        page.eval("history.back()")
        self.at(page, self.brand_path)
        page.until("!!document.querySelector('#design-templates')", what="ブランドの詳細")
        self.fill(page, ".title-input", "ブランド名を書き換えた")
        page.eval("history.back()")
        self.settle(page, 1)
        self.assertEqual(page.eval("location.pathname"), self.brand_path)
        page.eval("window.__answer = true; history.forward()")
        self.at(page, self.editor_path)
        self.assertEqual(page.errors, [])

    def test_accepted_navigation_is_unchanged(self) -> None:
        page = self.start()
        self.nav(page, self.brand_path, "!!document.querySelector('#design-templates')")
        self.nav(page, self.editor_path, "!!document.querySelector('.template-editor')")
        # 保存済み（確認なし）の戻る・進む
        page.eval("history.back()")
        page.until("!!document.querySelector('#design-templates')", what="戻る")
        page.eval("history.forward()")
        page.until("!!document.querySelector('.template-editor')", what="進む")
        self.assertEqual(page.eval("window.__asked"), 0)
        # 許した場合は移る
        self.fill(page, "#template-basic input", "書き換えた")
        page.eval("window.__answer = true; history.back()")
        page.until("!!document.querySelector('#design-templates') && !document.querySelector('.template-editor')", what="許して戻る")
        self.assertEqual(page.eval("window.__asked"), 1)
        self.assertEqual(page.errors, [])

    def test_navigation_during_a_pending_restore_is_deferred_once(self) -> None:
        page = self.start()
        self.nav(page, self.brand_path, "!!document.querySelector('#design-templates')")
        self.nav(page, self.editor_path, "!!document.querySelector('.template-editor')")
        length = page.eval("history.length")
        self.fill(page, "#template-basic input", "書き換えた")
        # 戻しを遅らせて、着く前に別の移動が来る場面を作る
        page.eval("(function(){ var g = history.go.bind(history); history.go = function (d) { setTimeout(function () { g(d); }, 1500); }; })()")
        page.eval("history.back()")
        page.until("window.__asked === 1", what="確認（断る）")
        page.eval("window.__answer = true; SS.navigate('/settings/brands')")
        # 戻しが着いたあとで、預かった移動が1回だけ行われる
        self.at(page, "/settings/brands")
        time.sleep(1.0)
        self.assertEqual(page.eval("location.pathname"), "/settings/brands")
        self.assertEqual(page.eval("window.__asked"), 2, "預かった移動の確認は1回")
        self.assertEqual(page.eval("history.length"), length + 1)
        page.eval("history.back()")
        self.at(page, self.editor_path)
        page.until("!!document.querySelector('.template-editor')", what="編集画面に戻る")
        self.assertEqual(page.errors, [])

    def test_a_fast_second_back_is_not_swallowed(self) -> None:
        page = self.start()
        self.nav(page, "/settings/brands", "!!document.querySelector('.setting-row')")
        self.nav(page, self.brand_path, "!!document.querySelector('#design-templates')")
        self.nav(page, self.editor_path, "!!document.querySelector('.template-editor')")
        length = page.eval("history.length")
        self.fill(page, "#template-basic input", "書き換えた")
        page.eval("history.back()")
        page.eval("history.back()")
        time.sleep(2.0)
        self.assertGreaterEqual(page.eval("window.__asked"), 1)
        self.assertEqual(page.eval("location.pathname"), self.editor_path)
        self.assertEqual(page.eval("document.querySelector('#template-basic input').value"), "書き換えた")
        self.assertTrue(page.eval("!!document.querySelector('.template-editor')"))
        self.assertEqual(page.eval("history.length"), length)
        # そのあとの普通の「戻る」は、画面と URL が食い違わず、確認を通る
        asked = page.eval("window.__asked")
        page.eval("history.back()")
        page.until(f"window.__asked === {asked + 1}", what="確認")
        time.sleep(1.0)
        self.assertEqual(page.eval("location.pathname"), self.editor_path)
        page.eval("window.__answer = true; history.back()")
        self.at(page, self.brand_path)
        page.until("!!document.querySelector('#design-templates') && !document.querySelector('.template-editor')", what="ブランドの詳細")
        self.assertEqual(page.errors, [])

    def test_declined_multi_step_forward_adds_no_entry(self) -> None:
        page = self.start()
        page.eval("window.__answer = true; SS.navigate('/settings/brands', true)")
        self.nav(page, self.brand_path, "!!document.querySelector('#design-templates')")
        self.nav(page, self.editor_path, "!!document.querySelector('.template-editor')")
        self.nav(page, "/settings", "!!document.querySelector('.settings-index')")
        page.eval("history.go(-2)")
        self.at(page, self.brand_path)
        page.until("!!document.querySelector('#design-templates')", what="ブランドの詳細")
        length = page.eval("history.length")
        page.eval("window.__answer = false; window.__asked = 0;")
        self.fill(page, ".title-input", "ブランド名を書き換えた")
        page.eval("history.go(2)")
        self.settle(page, 1)
        self.assertEqual(page.eval("location.pathname"), self.brand_path)
        self.assertEqual(page.eval("document.querySelector('.title-input').value"), "ブランド名を書き換えた")
        self.assertEqual(page.eval("history.length"), length)
        page.eval("window.__answer = true; history.go(2)")
        self.at(page, "/settings")
        self.assertEqual(page.errors, [])

    def test_replace_state_keeps_the_history_index(self) -> None:
        page = self.start()
        self.nav(page, self.brand_path, "!!document.querySelector('#design-templates')")
        index = page.eval("history.state.qsIndex")
        self.assertIsInstance(index, int)
        page.eval("history.replaceState(null, '')")
        self.assertEqual(page.eval("history.state.qsIndex"), index)
        page.eval("history.replaceState({other: 1}, '')")
        self.assertEqual(page.eval("JSON.stringify([history.state.qsIndex, history.state.other])"), json.dumps([index, 1], separators=(",", ":")))
        page.eval("SS.navigate(location.pathname + '?x=1', true)")
        self.assertEqual(page.eval("history.state.qsIndex"), index)

    def test_a_restore_that_never_arrives_realigns_the_url_and_drops_the_deferred_move(self) -> None:
        page = self.start()
        self.nav(page, self.brand_path, "!!document.querySelector('#design-templates')")
        self.nav(page, self.editor_path, "!!document.querySelector('.template-editor')")
        self.fill(page, "#template-basic input", "書き換えた")
        page.eval("SS.restoreFailsafeMs = 500; history.go = function () {};")   # 戻しが届かない場面（最後の手段の経路）
        page.eval("history.back()")
        page.until("window.__asked === 1", what="確認（断る）")
        page.eval("SS.navigate('/settings/brands')")   # 預けられる
        time.sleep(1.5)
        self.assertEqual(page.eval("location.pathname"), self.editor_path, "URL は出している画面に合う")
        self.assertEqual(page.eval("document.querySelector('#template-basic input').value"), "書き換えた")
        self.assertTrue(page.eval("!!document.querySelector('.template-editor')"))
        self.assertEqual(page.eval("window.__asked"), 1, "預けた移動は行わない")
        self.assertIsInstance(page.eval("history.state.qsIndex"), int)
        self.assertEqual(page.errors, [])


class PhoneTests(ScreenBase):
    width = 390
    height = 900

    def test_rows_do_not_overflow_on_a_phone(self) -> None:
        self.create("とても長い名前のテンプレートで折り返しの確かめをするためのもの", modes=["pitch", "presentation"])
        page = self.open_brand()
        self.no_overflow(page)
        # 行は折り返さない: 編集と ⋮ は名前と同じ行の右に残る
        self.assertTrue(page.eval("(function(){ var r = document.querySelector('#design-templates .setting-row');"
                                  " var a = r.querySelector('a.btn').getBoundingClientRect(), n = r.querySelector('.template-list__title a').getBoundingClientRect();"
                                  " return a.top < n.bottom && a.right <= r.getBoundingClientRect().right + 1; })()"))
        self.assertEqual(page.errors, [])

    def test_no_horizontal_overflow(self) -> None:
        long = "とても長い名前のテンプレートの確かめ" * 2 + "x" * 60
        t = self.create(long, structure="## 見出し\n| 列1 | 列2 | 列3 | 列4 | 列5 | 列6 |\n|---|---|---|---|---|---|\n| あああああああ | いいいいいいい | ううううううう | えええええええ | おおおおおおお | かかかかかかか |",
                        instructions="- 項目\n" * 10 + "`" + "x" * 80 + "`")
        page = self.open_brand()
        self.no_overflow(page)
        self.open_editor(page, t["template_id"])
        self.no_overflow(page)
        for name in ("structure", "instructions", "resources"):
            self.tab(page, name)
            self.no_overflow(page)
        self.click(page, "#template-resources .gap__add")
        self.click(page, "#template-resources .template-guide__toggle")
        self.no_overflow(page)
        # 版の一覧の板も、画面の幅に収まる
        self.click(page, ".template-ver")
        page.until("!!document.querySelector('.template-version')", what="版の一覧")
        self.no_overflow(page)
        self.assertTrue(page.eval("document.querySelector('.template-versions-modal').getBoundingClientRect().right <= window.innerWidth"))
        self.assertEqual(page.errors, [])

    def test_no_overflow_for_an_older_version_and_the_import_dialog(self) -> None:
        res = [{"id": "r1", "name": "長いパスの素材", "kind": "file", "path": "/no/such/" + "very-long-directory-name/" * 5 + "file.md", "usage": "使い方" * 30}]
        t = self.create("版と取り込み", resources=res, structure="## 見出し\n| 列1 | 列2 | 列3 | 列4 | 列5 |\n|---|---|---|---|---|\n| ああああああ | いいいいいい | うううううう | ええええええ | おおおおおお |")
        tid = t["template_id"]
        d = dict(t["document"], name="版と取り込み 2")
        self.fx.post(f"{BASE}/{tid}", {"document": d, "mode": "new_version", "expected_revision_id": t["current_revision_id"]})
        page = self.open_brand()
        self.open_editor(page, tid)
        self.click(page, ".template-ver")
        page.until("document.querySelectorAll('.template-version').length === 2", what="版の一覧")
        page.eval("Array.from(document.querySelectorAll('.template-version')).find(b => b.textContent.indexOf('v2') < 0).click()")
        page.until("!!document.querySelector('.template-version-call button')", what="前の版の中身")
        self.no_overflow(page)
        self.assertTrue(page.eval("document.querySelector('.template-versions-modal').getBoundingClientRect().right <= window.innerWidth"))
        self.click(page, ".modal-actions button", "閉じる")
        text = self.fx.get(f"{BASE}/{tid}/markdown").body.decode("utf-8")
        self.go(page, f"/settings/brands/{self.bid}", "!!document.querySelector('#design-templates .setting-row')", "ブランドの詳細")
        page.eval("(function(){ var input = document.querySelector('#design-templates input[type=file]');"
                  f" var dt = new DataTransfer(); dt.items.add(new File([{json.dumps(text)}], 't.md'));"
                  " input.files = dt.files; input.dispatchEvent(new Event('change', {bubbles: true})); })()")
        page.until("!!document.querySelector('.template-import-modal')", what="取り込みの確認")
        self.no_overflow(page)
        self.assertTrue(page.eval("document.querySelector('.template-import-modal').getBoundingClientRect().right <= window.innerWidth"))
        self.assertEqual(page.errors, [])


if __name__ == "__main__":
    unittest.main()
