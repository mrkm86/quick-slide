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

    def test_generate_is_shown_but_disabled_without_codex(self) -> None:
        """作れない機械でも「作ってもらう」は消さない（作れることがあると分かるように）。押せない選択肢と理由で出す。"""
        def sources(can: bool) -> list[tuple[str, bool]]:
            image = next(k for k in E.offered("presentation", can_generate=can) if k["kind"] == "image")
            return [(o["value"], bool(o.get("disabled"))) for o in image["options"]]
        self.assertEqual(sources(True), [("generate", False), ("attached", False)])
        self.assertEqual(sources(False), [("generate", True), ("attached", False)])
        image = next(k for k in E.offered("presentation", can_generate=False) if k["kind"] == "image")
        self.assertEqual(image["options"][0]["reason"], E.GENERATE_UNAVAILABLE)
        self.assertEqual(E.parse("presentation", "image", None, can_generate=False), ("image", ["attached"]), "既定は押せる先頭")
        with self.assertRaises(ValueError) as cm:
            E.parse("presentation", "image", ["generate"], can_generate=False)
        self.assertEqual(str(cm.exception), E.GENERATE_UNAVAILABLE)

    def test_other_diagram_shapes_are_written_in_words(self) -> None:
        """図の形は数が多いので、札に無い形は「その他」を選んで書いて伝える（文が要る）。"""
        diagram = next(k for k in E.offered("pitch", can_generate=True) if k["kind"] == "diagram")
        other = diagram["options"][-1]
        self.assertEqual((other["value"], other["text_reason"]), ("other", E.DIAGRAM_OTHER_REQUIRED))
        self.assertTrue(other["placeholder"])
        miss = lambda text: E.missing("diagram", ["other"], text=text, attachments=0)  # noqa: E731
        self.assertEqual(miss(""), E.DIAGRAM_OTHER_REQUIRED)
        self.assertEqual(miss("  "), E.DIAGRAM_OTHER_REQUIRED)
        self.assertIsNone(miss("3 つの輪が重なる図"))
        self.assertIsNone(E.missing("diagram", ["flow"], text="", attachments=0), "ほかの形は選ぶだけで送れる")
        self.assertIn("指示の文に書かれたとおり", "\n".join(E.direction("presentation", "diagram", ["other"])))
        self.assertEqual(E.summary("diagram", ["other"]), "図解（その他）")
        no_codex = next(k for k in E.offered("presentation", can_generate=False) if k["kind"] == "image")
        self.assertEqual(no_codex["options"][0]["note"], "現在利用できません")

    def test_three_cards_by_how_much_the_page_changes(self) -> None:
        """札は 3 つ（文字を整える・絵や写真を足す・見せ方を変える）。種類は 4 つのままで、挿絵と画像が同じ札に入る。
        版の控えの名前（図解 など）は変えない。"""
        for mode, imported in (("presentation", False), ("pitch", False), ("presentation", True)):
            kinds = E.offered_for_imported(mode) if imported else E.offered(mode, can_generate=True)
            self.assertEqual([(k["kind"], k["card"], k["card_label"]) for k in kinds],
                             [("text", "text", "文字を整える"), ("diagram", "layout", "見せ方を変える"),
                              ("illustration", "visual", "絵や写真を足す"), ("image", "visual", "絵や写真を足す")])
            self.assertTrue(all(k["card_brief"] for k in kinds))
            self.assertEqual({k["card_step"] for k in kinds if k["card"] == "visual"}, {"どんな絵にしますか"})
        self.assertEqual(E.summary("image", ["generate"]), "画像（生成する）")
        shapes = [o["label"] for k in E.offered("pitch", can_generate=True) if k["kind"] == "diagram" for o in k["options"]]
        self.assertEqual(shapes, ["おまかせ", "流れ", "関係", "比較", "前と後", "数字", "その他（書いて伝える）"])
        self.assertIn("前と後", E.shape_words("presentation", "before_after"))
        self.assertEqual(E.summary("diagram", ["before_after"]), "図解（前と後）")

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

    def test_free_pages_are_asked_for_in_presentation_and_refused_in_pitch(self) -> None:
        """プレゼンは「まず自由なページ」、ピッチは「自由なページを使わない」（docs/adr/0033）。"""
        from pathlib import Path
        from quick_slide import engines
        from quick_slide.writer import prompt as P
        skills = Path(__file__).resolve().parent.parent / "skills"
        for mode, made, edited in (("presentation", "まず自由なページ（type: free）", P.FREE_EDIT_RULE), ("pitch", P.PITCH_NO_FREE, P.PITCH_NO_FREE)):
            engine = engines.engine_for(mode)
            text = P.build_prompt(engine, {}, manuscript="原稿", title_hint=None, skills_dir=skills)
            self.assertIn(made, text.split("# 台本の決まり")[0], mode)
            self.assertEqual(mode == "pitch", P.PITCH_NO_FREE in text, mode)
            rules = P._edit_rules(engine, {"mode": mode})
            self.assertIn(edited, rules, mode)
            self.assertEqual(mode == "presentation", P.FREE_EDIT_RULE in rules, mode)
        for shape in ("auto", "flow", "relation", "compare", "numbers"):
            self.assertIn("free", "".join(E.direction("presentation", "diagram", [shape])), shape)
            self.assertNotIn("free", "".join(E.direction("pitch", "diagram", [shape])), shape)

    def test_free_page_edits_keep_the_shape_and_show_only_text(self) -> None:
        """自由なページ: 文字の直しで組みが変われば書き直させる・数の属性は一緒に変えてよい・記録の欄には文字だけ（docs/adr/0033）。"""
        from pathlib import Path
        from quick_slide import engines, freepage, scriptdoc
        from quick_slide.worker.edit import free_shape_problems
        from quick_slide.writer import prompt as P
        body = ('<div class="f-row f-grow"><div class="f-num">62%</div>'
                '<div class="f-cells" data-total="100" data-on="62" data-cols="10" data-step="1"></div></div>'
                '<div class="f-bars" data-max="10"><div class="f-bar" data-value="7.5">導入前</div></div>')
        page = {"id": "p", "type": "free", "headline": "見出し", "body": body}
        numbers = {**page, "body": body.replace("62", "65").replace("7.5", "8")}
        self.assertFalse(freepage.reshaped(page, numbers), "数字と、その数の属性だけの直しは組みを変えていない")
        for name, changed in {"並べ方": body.replace("f-row", "f-col"), "style": body.replace('class="f-num"', 'class="f-num" style="font-size: 160px"'),
                              "段": body.replace('data-step="1"', 'data-step="2"'), "要素を足す": body + "<div>追記</div>",
                              "マスの列の数": body.replace('data-cols="10"', 'data-cols="5"')}.items():
            after = {**page, "body": changed}
            self.assertTrue(freepage.reshaped(page, after), name)
            self.assertEqual(len(free_shape_problems({"kind": "text"}, [page], [after], {"p"})), 1, name)
            self.assertEqual(free_shape_problems({"kind": "diagram"}, [page], [after], {"p"}), [], "図解の直しは組み替えてよい")
            self.assertEqual(free_shape_problems({}, [page], [after], {"p"}), [], "種類を選ばない指示は組み替えてよい")
            self.assertEqual(free_shape_problems({"kind": "text"}, [page], [after], set()), [], "直していないページは見ない")
        self.assertEqual(free_shape_problems({"kind": "text"}, [page], [numbers], {"p"}), [])
        self.assertEqual(len(free_shape_problems({"kind": "text"}, [page], [{"id": "p", "type": "cards", "headline": "h"}], {"p"})), 1, "別の型に替えるのも組み替え")
        self.assertEqual(free_shape_problems({"kind": "text"}, [{"id": "p", "type": "cards"}], [page], {"p"}), [], "元が自由なページでなければ見ない")
        same = {**page, "body": body.replace('class="f-row f-grow"', 'class="f-grow  f-row" style=""')}
        self.assertFalse(freepage.reshaped(page, same), "class の語の順・空の style は、組みの違いにしない")
        styled = {**page, "body": '<div style="gap: 24px; align-items: center"><div>a</div></div>'}
        self.assertFalse(freepage.reshaped(styled, {**page, "body": '<div style="align-items:center;gap:24px;"><div>b</div></div>'}), "style の空白と順")
        self.assertTrue(freepage.reshaped(styled, {**page, "body": '<div style="gap: 32px; align-items: center"><div>a</div></div>'}), "style の値")
        self.assertEqual(scriptdoc.body_of(page).split("\n"), ["見出し", "62%", "100 のうち 62", "導入前: 7.5（最大 10）"])
        self.assertNotIn("<", scriptdoc.body_of(page))
        # 直しの指示（完成した文）に、組みを保つ決まりと、数の属性を一緒に直す決まりが入る。差し込み・組み直しにも届く
        skills = Path(__file__).resolve().parent.parent / "skills"
        script = {"title": "t", "slides": [page]}
        pres, pitch = engines.engine_for("presentation"), engines.engine_for("pitch")
        rev = P.build_revision_prompt(pres, {}, script=script, targets=[{"id": "p", "position": 1, "title": "見出し", "picture": None}], instruction="62% を 65% に", attachments=[], skills_dir=skills)
        self.assertIn(P.FREE_EDIT_RULE, rev)
        self.assertIn("data-on", P.FREE_EDIT_RULE)
        self.assertIn(P.PITCH_NO_FREE, P.build_revision_prompt(pitch, {}, script=script, targets=[{"id": "p", "position": 1, "title": "見出し", "picture": None}], instruction="短く", attachments=[], skills_dir=skills))
        self.assertIn(P.PITCH_NO_FREE, P.build_insert_prompt(pitch, {}, script=script, after={"id": "p", "position": 1, "title": "見出し"}, instruction="1 枚足す", attachments=[], skills_dir=skills))
        self.assertIn("自由なページ free", P.build_rebuild_prompt(pres, {}, page={"id": "p", "type": "html", "position": 1}, texts=["a"], what="diagram",
                                                              picture=None, generate=False, instruction="", skills_dir=skills))

    def test_every_problem_of_the_check_reaches_the_writer_with_page_ids(self) -> None:
        """検査は全部のページの誤りを 1 回で出す。サーバは全部の行を読み、直しではページの id を添える（docs/adr/0033 の追記）。"""
        import sys
        import tempfile
        from pathlib import Path
        from quick_slide.render import runner
        from quick_slide.worker.edit import _engine_problems
        data = {"slides": [{"id": "cover", "type": "cover"}, {"id": "p-two", "type": "closing"}, {"id": "p-three", "type": "free"}]}
        got = _engine_problems(["NG: 2枚目（closing）: headline が無い",
                                "NG: slide3 div.f-body > div: [free-outside] 中身の箱からはみ出す（1枚目ではない）",
                                "NG: 9枚目（table）: 範囲の外", "NG: 決まり違反が 2 件"], data)
        self.assertEqual(got, ["NG: 2枚目（id=p-two）（closing）: headline が無い",
                               "NG: 3枚目（id=p-three） div.f-body > div: [free-outside] 中身の箱からはみ出す（1枚目ではない）",
                               "NG: 9枚目（table）: 範囲の外", "NG: 決まり違反が 2 件"])
# 検査の入口（check_script）そのものを通す: 300 行の NG が、先頭から全部 problems に入る
        code = "import sys\nfor i in range(1, 301): print(f'NG: slide{i}', file=sys.stderr)\nsys.exit(2)"

        class Engine:
            def check_command(self, python, skills_dir, script, args, quick=False):
                return [python, "-c", code]

        class Tools:
            python, skills_dir, extra_env = sys.executable, Path("."), {}

        with tempfile.TemporaryDirectory() as tmp:
            r = runner.check_script(Engine(), tools=Tools(), home=Path(tmp), script=Path(tmp) / "script.json", args={})
        self.assertEqual((r.rc, len(r.problems), r.problems[0], r.problems[-1]), (2, 300, "NG: slide1", "NG: slide300"), "先頭のページの誤りを落とさない")

    def test_picture_diagrams_follow_the_mode_and_the_machine(self) -> None:
        """絵で描く図解（図の中の文字も描く）: プレゼンで、絵を生成できる機械にだけ出す・台本係に頼む。図の中の文字も日本語の検査に掛ける。"""
        from pathlib import Path
        from quick_slide import engines
        from quick_slide.writer import jlint
        from quick_slide.writer import prompt as P
        shapes = lambda mode, gen: [o["value"] for e in E.offered(mode, can_generate=gen) if e["kind"] == "diagram" for o in e["options"]]
        self.assertEqual(shapes("presentation", True)[-2:], ["picture", "other"], "その他（書いて伝える）はいつも最後")
        for mode, gen in (("presentation", False), ("pitch", True), ("motion", True)):
            self.assertNotIn("picture", shapes(mode, gen), (mode, gen))
        self.assertEqual(E.parse("presentation", "diagram", ["picture"], can_generate=True), ("diagram", ["picture"]))
        with self.assertRaises(ValueError):
            E.parse("presentation", "diagram", ["picture"], can_generate=False)
        said = "".join(E.direction("presentation", "diagram", ["picture"]))
        self.assertIn("figure", said)
        self.assertIn("labels", said)
        skills = Path(__file__).resolve().parent.parent / "skills"
        rule = P.DIAGRAM_RULE["presentation-picture"]
        for mode, images, want in (("presentation", True, True), ("presentation", False, False), ("pitch", True, False)):
            text = P.build_prompt(engines.engine_for(mode), {"images": images}, manuscript="原稿", title_hint=None, skills_dir=skills)
            self.assertEqual(rule in text, want, (mode, images))
        self.assertIn("### 絵で描く図解", P.contract_text(engines.engine_for("presentation"), skills))
        # 絵を生成しないとき: 台本の決まりの節は同じ文書で渡るので、作るとき・直すとき・差し込むときの「守ること」で使わせない
        made = P.build_prompt(engines.engine_for("presentation"), {"images": False}, manuscript="原稿", title_hint=None, skills_dir=skills)
        self.assertIn(P.NO_PICTURE_DIAGRAM, made.split("# 台本の決まり")[0])
        self.assertTrue(any(P.NO_PICTURE_DIAGRAM in r for r in P._edit_rules(engines.engine_for("presentation"), {"images": False})))
        self.assertFalse(any(P.NO_PICTURE_DIAGRAM in r for r in P._edit_rules(engines.engine_for("presentation"), {"images": True})))
        self.assertNotIn(P.NO_PICTURE_DIAGRAM, P.build_prompt(engines.engine_for("presentation"), {"images": True}, manuscript="原稿",
                                                               title_hint=None, skills_dir=skills))
        # 台本の決まりを渡す指示は 4 つ（作る・直す・差し込む・取り込んだページの組み直し）。組み直しにも届く
        pres = engines.engine_for("presentation")
        # worker から実際に渡る組み合わせ: 図解の組み直しは generate がいつも偽。画像の組み直しは、生成できて画像を渡さないときだけ真
        for what, images, generate in (("diagram", False, False), ("diagram", True, False), ("image", False, False), ("image", True, True),
                                       ("image", True, False)):
            text = P.build_rebuild_prompt(pres, {"images": images}, page={"id": "p", "type": "html", "position": 1}, texts=["a"], what=what,
                                          picture=None, generate=generate, instruction="", skills_dir=skills)
            self.assertEqual(P.NO_PICTURE_DIAGRAM in text.split("# 台本の決まり")[0], not images, (what, images, generate))
        ins = P.build_insert_prompt(pres, {"images": False}, script={"title": "t", "slides": []}, after=None, instruction="1 枚足す",
                                    attachments=[], skills_dir=skills)
        self.assertIn(P.NO_PICTURE_DIAGRAM, ins.split("# 台本の決まり")[0])
        segs = jlint.segments([{"id": "p", "type": "figure", "headline": "見出し",
                                "image": {"prompt": "調べない指示", "alt": "調べない説明", "labels": ["店頭", "宅配"]}}])
        self.assertEqual([s.text for s in segs], ["見出し", "店頭", "宅配"])

    def test_picture_answers_are_checked_by_the_server(self) -> None:
        """「絵で描く」を選んだ直しは、絵で描く図解になっていること（頼むだけにしないで、サーバが確かめる。docs/adr/0034）。"""
        from quick_slide.worker.edit import picture_problems
        fig = {"id": "p", "type": "figure", "headline": "h", "image": {"prompt": "全体図", "labels": ["店頭", "宅配"]}}
        photo = {"id": "q", "type": "photo", "headline": "h", "image": {"prompt": "風景"}}
        # 「絵で描く」: route は script・直したページが絵で描く図解になっていること
        req = {"kind": "diagram", "options": ["picture"], "scope": "page"}
        plain = {"id": "p", "type": "statement", "headline": "h"}
        self.assertEqual(picture_problems(req, {"p": "script"}, {"p": [fig]}), [])
        self.assertEqual(len(picture_problems(req, {"q": "image"}, {"q": []})), 1, "今ある絵の描き直しは、図解にならない")
        self.assertEqual(len(picture_problems(req, {"q": "script"}, {"q": [photo]})), 1, "labels の無い絵は、図解ではない")
        self.assertEqual(len(picture_problems(req, {"p": "both"}, {"p": [fig]})), 1)
        self.assertEqual(len(picture_problems(req, {"p": "keep"}, {"p": []})), 1, "指定したページを変えないのは、頼みに応えていない")
        self.assertEqual(picture_problems(req, {"p": "script"}, {"p": [plain, {**fig, "id": "new"}]}), [], "分けたときは、どれか 1 枚が図解")
        self.assertEqual(len(picture_problems(req, {"p": "script"}, {"p": [plain, {**plain, "id": "new"}]})), 1)
        whole = {**req, "scope": "deck"}
        self.assertEqual(picture_problems(whole, {"p": "script", "q": "keep"}, {"p": [fig], "q": []}), [], "全体への直しは、変えないページがあってよい")
        self.assertEqual(len(picture_problems(whole, {"q": "script", "p": "keep"}, {"q": [photo], "p": []})), 1)
        self.assertEqual(len(picture_problems(whole, {"p": "script", "q": "image"}, {"p": [fig], "q": []})), 1)
        self.assertEqual(picture_problems({"kind": "diagram", "options": ["flow"]}, {"q": "image"}, {"q": []}), [], "ほかの直しでは見ない")
        self.assertEqual(E.summary("diagram", ["picture"]), "図解（絵で描く）")

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
                 ({"kind": "diagram", "options": ["other"]}, "text_required"),   # 見せ方の「その他」は、文が要る
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
