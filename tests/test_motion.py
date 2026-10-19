"""モーション（motion-slide）をエンジンとしてつなぐ: 作る → 検査で書き直し → 組む → ページが場面で並ぶ → 直す → 発表の API と配信 → 書き出し。

エンジンは偽物（`tests/fakes/fake_engine.py` の motion。`motion_slide.py` の `check`・`deck` の形をまねる）、台本係も偽物
（`tests/fakes/writer_common.py`。指示文に「モード: モーション」があれば場面ごとの台本を返す）。本物の motion-slide を通すのは
`skills/motion-slide/tests/run.sh`（Playwright の Chromium が要る）。
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

from helpers import FAKES, REPO, intake_payload, wait_until_idle, writer_calls
from test_p4_settings import Base, arg, engine_calls

from quick_slide import motion
from quick_slide.db.migrate import LATEST_VERSION, load_migrations, user_version
from quick_slide.db.migrate import migrate as run_migrate
from quick_slide.engines import ENGINES, MOTION_FORMATS
from quick_slide.settings import Settings, WorkerSettings
from quick_slide.paths import SKILLS_DIR
from quick_slide.render import workspace as W
from quick_slide.server.present_routes import MOTION_PLAYER_PATH, motion_csp, motion_document
from quick_slide.server.static import PresentFiles
from quick_slide.writer import jlint as JLINT
from quick_slide.writer.prompt import build_insert_prompt, build_prompt, build_revision_prompt

THEME = {"theme": "/tmp/theme.yaml", "logo": "/tmp/logo.png", "logo_white": "/tmp/white.png", "logo_position": "bottom-left",
         "watermark": "/tmp/wm.png"}


class EngineArgsTests(unittest.TestCase):
    def test_motion_takes_only_theme_and_logo(self) -> None:
        e = ENGINES["motion"]
        args = {"mode": "motion", "style": "standard", "image_look": "photo", "style_note": "手描き風", "look": THEME}
        cmd = e.deck_command("py", SKILLS_DIR, Path("/t/s.json"), Path("/t/out"), args)
        self.assertEqual(cmd[:3], ["py", str(SKILLS_DIR / "motion-slide" / "scripts" / "motion_slide.py"), "deck"])
        self.assertEqual(arg(cmd, "--theme"), "/tmp/theme.yaml")
        self.assertEqual(arg(cmd, "--logo"), "/tmp/logo.png")
        self.assertEqual(arg(cmd, "--format"), MOTION_FORMATS)
        for flag in ("--style", "--image-look", "--style-note", "--logo-position", "--logo-white", "--watermark", "--no-odp"):
            self.assertNotIn(flag, cmd, flag)
        quick = e.check_command("py", SKILLS_DIR, Path("/t/s.json"), args, quick=True)
        self.assertIn("--quick", quick)
        self.assertNotIn("--quick", e.check_command("py", SKILLS_DIR, Path("/t/s.json"), args))
        self.assertNotIn("--logo", quick)   # 検査は色（テーマ）だけ。ロゴは組み立てのときだけ渡す
        self.assertIsNone(e.auth_command("py", SKILLS_DIR))

    def test_other_engines_keep_their_arguments(self) -> None:
        args = {"mode": "presentation", "style": "visual", "image_look": "photo", "look": THEME}
        cmd = ENGINES["presentation"].deck_command("py", SKILLS_DIR, Path("/t/s.json"), Path("/t/out"), args)
        self.assertEqual((arg(cmd, "--style"), arg(cmd, "--logo-position"), arg(cmd, "--watermark")),
                         ("visual", "bottom-left", "/tmp/wm.png"))
        self.assertEqual(arg(cmd, "--format"), "pptx,pdf,png,thumbs")
        self.assertNotIn("--quick", ENGINES["presentation"].check_command("py", SKILLS_DIR, Path("/t/s.json"), args, quick=True))

    def test_the_contract_docs_exist_and_the_engine_writes_what_we_export(self) -> None:
        e = ENGINES["motion"]
        for rel in e.contract_docs:
            self.assertTrue((SKILLS_DIR / rel).is_file(), rel)
        self.assertEqual((e.exports, e.has_images, e.can_revise), (("html",), False, False))


class ScriptShapeTests(unittest.TestCase):
    def test_scenes_become_pages_and_back(self) -> None:
        data = {"title": "t", "config": {"crumb": "c"}, "slides": [{"id": "a", "title": "A", "beats": 1, "html": "<p>x</p>"},
                                                                   {"id": "b", "title": "B", "beats": 1, "html": "", "_hidden": True}]}
        from quick_slide.scriptdoc import engine_view, screen_pages
        view = motion.engine_view(engine_view(data))
        self.assertNotIn("slides", view)
        self.assertEqual([s["id"] for s in view["scenes"]], ["a"], "非表示の場面は組み立てる台本から外す")
        self.assertEqual(view["config"], {"crumb": "c"})
        pages = screen_pages({"mode": "motion"}, data)
        self.assertEqual([(p["key"], p["type"], p["title"], p["hidden"]) for p in pages],
                         [("a", "scene", "A", False), ("b", "scene", "B", True)])
        self.assertEqual(pages[0]["body"], "x")
        self.assertEqual(motion.normalize({"scenes": [1], "title": "t"}), {"title": "t", "slides": [1]})
        self.assertEqual(motion.normalize({"scenes": [1], "slides": [2]}), {"slides": [2]})

    def test_html_texts_skip_tags_and_scripts(self) -> None:
        html = ['<div class="✅ x"><p>見出し<b>太字</b></p><style>.a{}</style>', '<script>alert(1)</script><span>2つ目</span></div>']
        self.assertEqual(motion.html_texts(html), ["見出し", "太字", "2つ目"])

    def test_japanese_lint_reads_html_text_title_term_and_talk_but_not_tags(self) -> None:
        scenes = [{"id": "ok", "title": "題", "term": "言葉", "html": '<p class="x" data-label="✅">本文</p>', "talk": ["話す"]},
                  {"id": "html-emoji", "title": "題", "html": "<p>✅ 済み</p>", "talk": ["話す"]},
                  {"id": "title-emoji", "title": "🚀 題", "html": "<p>本文</p>"},
                  {"id": "term-emoji", "title": "題", "term": "✨言葉", "html": "<p>本文</p>"},
                  {"id": "talk-emoji", "title": "題", "html": "<p>本文</p>", "talk": ["✅ 話す"]},
                  {"id": "filler", "title": "題", "html": "<p>本文</p>", "talk": ["結論から言うと、続けます"]}]
        ng, _ = JLINT.problems_and_notes(motion.jlint_pages(scenes), enforce=True)
        joined = "\n".join(ng)
        self.assertNotIn("id ok", joined, "タグの属性の絵文字・普通の文字は見ない")
        for sid in ("html-emoji", "title-emoji", "term-emoji", "talk-emoji"):
            self.assertIn(f"id {sid}", joined, sid)
        self.assertIn("話す台本", joined)

    def test_check_timeout_grows_with_beats(self) -> None:
        few = {"slides": [{"id": "a", "beats": 3}]}
        many = {"slides": [{"id": f"s{i}", "beats": 4} for i in range(60)]}   # 240 拍
        self.assertEqual(motion.check_timeout(few), 300.0)
        self.assertGreater(motion.check_timeout(many), 900.0)
        hidden = {"slides": [{"id": "a", "beats": 500, "_hidden": True}]}
        self.assertEqual(motion.beats_of(hidden), 0)


class DuplicateSceneTests(unittest.TestCase):
    SCENE = {"id": "s3", "title": "t", "beats": 1, "html": ['<div id="tb" class="x" data-in="0"></div>',
                                                          '<label for="tb">x</label><svg id="w3"><use href="#tb"/></svg>'],
             "style": "#s3 #tb{left:1px} #s3 .a{top:2px}", "script": "const T = document.getElementById('tb'); const tbl = 1;",
             "hook": 'x.table("tb", {}); x.wire("w3", x.rel(document.querySelector("#tb")), 1);'}

    def test_ids_and_references_move_together(self) -> None:
        other = {"id": "s4", "html": '<p id="tb-copy"></p>'}
        taken = motion.element_ids(self.SCENE["html"], other["html"])
        out = motion.duplicate_scene(self.SCENE, "s3-copy", taken)
        self.assertEqual(out["id"], "s3-copy")
        self.assertEqual(motion.element_ids(out["html"]), {"tb-copy2", "w3-copy"}, "他の場面の id と重ならない")
        text = json.dumps(out, ensure_ascii=False)
        for ref in ('for=\\"tb-copy2\\"', 'href=\\"#tb-copy2\\"', "#s3-copy #tb-copy2{", "getElementById('tb-copy2')",
                    'x.table(\\"tb-copy2\\"', 'x.wire(\\"w3-copy\\"', 'querySelector(\\"#tb-copy2\\")'):
            self.assertIn(ref, text, ref)
        self.assertIn("const tbl = 1", out["script"], "語の一部は置き換えない")
        self.assertIsInstance(out["html"], list)
        self.assertEqual(self.SCENE["id"], "s3", "元は変えない")

    def test_only_id_arguments_and_selectors_are_rewritten(self) -> None:
        scene = {"id": "a", "html": '<p id="hello">x</p>', "style": "#a #hello{x:y}",
                 "script": 'const g = "hello"; node.textContent = "hello world"; const w = ["hello", "a.hello"];',
                 "hook": ('document.getElementById("hello"); $("hello"); x.cell("hello", "k"); x.table("hello", {}); '
                          'document.querySelector("#hello .c"); document.querySelectorAll(\'[id="hello"], .z\'); '
                          'x.say("hello"); const t = "say hello"; const u = "#hello-x";')}
        out = motion.duplicate_scene(scene, "a-copy", {"a"})
        self.assertEqual(out["script"], scene["script"], "代入・配列・文章の中の語は触らない")
        for keep in ('x.say("hello")', 'const t = "say hello"', 'const u = "#hello-x"'):
            self.assertIn(keep, out["hook"], keep)
        for moved in ('getElementById("hello-a-copy")' if False else 'getElementById("hello-copy")', '$("hello-copy")',
                      'x.cell("hello-copy", "k")', 'x.table("hello-copy", {})', '"#hello-copy .c"', '[id="hello-copy"], .z'):
            self.assertIn(moved, out["hook"], moved)

    def test_display_strings_are_left_alone_and_comments_before_the_argument_are_skipped(self) -> None:
        scene = {"id": "a", "html": '<p id="face"></p><p id="item"></p>',
                 "hook": ('node.textContent = "色は #face"; x.say("#face"); const m = "[id=\'face\']"; '
                          'document.querySelector( /* target */ "#face"); document.getElementById(/* t */ "item"); '
                          'el.closest(\n  // c\n  "[id=item]"); el.matches("#face.on"); $("#item");')}
        out = motion.duplicate_scene(scene, "a-copy", {"a"})["hook"]
        for keep in ('textContent = "色は #face"', 'x.say("#face")', "const m = \"[id='face']\""):
            self.assertIn(keep, out, keep)
        for moved in ('querySelector( /* target */ "#face-copy")', 'getElementById(/* t */ "item-copy")', '"[id=item-copy]"',
                      'matches("#face-copy.on")', '$("#item-copy")'):
            self.assertIn(moved, out, moved)

    def test_scene_and_element_ids_share_one_used_set(self) -> None:
        from types import SimpleNamespace

        from quick_slide.worker.edit import _duplicate

        data = {"slides": [{"id": "s1", "title": "A", "beats": 1, "html": '<p id="s1-copy"></p><p id="k"></p>'},
                           {"id": "s2", "title": "B", "beats": 1, "html": '<p id="k-copy"></p>'}],
                "stage": '<div id="frame"></div>'}
        ctx = SimpleNamespace(call=lambda fn, *a, **kw: fn(*a, **kw), store=SimpleNamespace(page_key_of=lambda pid: pid))
        r = _duplicate(ctx, "d", {"page": "s1"}, data, "motion")
        new = r.data["slides"][1]
        self.assertNotIn(new["id"], {"s1", "s2", "s1-copy", "k", "k-copy", "frame"}, "場面の id は要素・stage の id とも重ならない")
        ids = [new["id"]] + [x for s in r.data["slides"] for x in motion.element_ids(s["html"])] + ["frame"]
        self.assertEqual(len(ids), len(set(ids)), ids)
        # 要素の新しい id が別の場面の id と重ならない: s2 が「k-copy」でなく場面の id を持つ場合
        data2 = {"slides": [{"id": "s1", "title": "A", "beats": 1, "html": '<p id="k"></p>'},
                            {"id": "k-copy", "title": "B", "beats": 1, "html": "<p></p>"}]}
        r = _duplicate(ctx, "d", {"page": "s1"}, data2, "motion")
        ids = [s["id"] for s in r.data["slides"]] + [x for s in r.data["slides"] for x in motion.element_ids(s["html"])]
        self.assertEqual(len(ids), len(set(ids)), ids)

    def test_a_duplicated_scene_passes_the_real_engine_check(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "deck.json"
            r = subprocess.run([sys.executable, "-I", str(SKILLS_DIR / "motion-slide" / "scripts" / "split.py"),
                                str(SKILLS_DIR / "motion-slide" / "templates" / "deck.html"), "--out", str(src)],
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(src.read_text(encoding="utf-8"))
            scene = next(s for s in data["scenes"] if motion.element_ids(s.get("html")))
            taken = motion.element_ids(data.get("stage"), *(s.get("html") for s in data["scenes"]))
            check = lambda d: subprocess.run([sys.executable, "-I", str(SKILLS_DIR / "motion-slide" / "scripts" / "motion_slide.py"),
                                              "check", "--script", str(src), "--quick"], capture_output=True, text=True, encoding="utf-8")

            def run(scenes):
                src.write_text(json.dumps({**data, "scenes": scenes}, ensure_ascii=False), encoding="utf-8")
                return check(None)

            naive = {**scene, "id": scene["id"] + "-copy"}
            self.assertEqual(run([*data["scenes"], naive]).returncode, 2, "id だけ替えた複製は、要素の id が重なって NG")
            good = motion.duplicate_scene(scene, scene["id"] + "-copy", taken)
            r = run([*data["scenes"], good])
            self.assertEqual(r.returncode, 0, r.stderr)


class PromptTests(unittest.TestCase):
    ARGS = {"mode": "motion", "style": "standard", "images": False, "look": {"emphasis": "結論を先に", "tone": "firm",
                                                                         "allowed_types": ["cards"]}}

    def test_the_writer_gets_the_motion_contract_and_no_image_or_type_rules(self) -> None:
        text = build_prompt(ENGINES["motion"], self.ARGS, manuscript="原稿の本文", title_hint="仮の題", skills_dir=SKILLS_DIR)
        self.assertIn("motion-slide/references/deck-format.md", text)
        self.assertIn("motion-slide/references/authoring.md", text)
        self.assertIn("書く前に（台本係が読む）", text)
        self.assertIn("場面の配列は `slides` のキーに書く", text)
        self.assertIn("モード: モーション", text)
        self.assertNotIn("スタイル:", text)
        self.assertNotIn("使ってよい型", text)
        self.assertNotIn("image: {", text)
        self.assertNotIn("html-slide/references", text)
        self.assertIn("書き方の方針（スタイル）: 結論を先に", text)
        self.assertIn("画像: 使わない", text)
        self.assertIn("拍の合計は", text)

    def test_outline_rules_replace_the_page_rules(self) -> None:
        text = build_prompt(ENGINES["motion"], {**self.ARGS, "outline_fixed": True}, manuscript="## 1. 一\n## 2. 二",
                            title_hint=None, skills_dir=SKILLS_DIR)
        self.assertIn("1 場面", text)
        self.assertNotIn("型は「書く前に」", text)

    def test_revision_and_insert_prompts_have_no_image_route(self) -> None:
        script = {"title": "t", "slides": [{"id": "a", "title": "A", "beats": 1, "html": "<p>x</p>"}]}
        rev = build_revision_prompt(ENGINES["motion"], self.ARGS, script=script,
                                    targets=[{"id": "a", "position": 1, "title": "A", "picture": None}],
                                    instruction="短く", attachments=[], skills_dir=SKILLS_DIR)
        self.assertIn("script: 文字・数字・構成・図・動き（拍）", rev)
        self.assertIn("route に image・both は使わない", rev)
        self.assertNotIn("slot", rev)
        self.assertIn('"route": "keep"', rev)
        ins = build_insert_prompt(ENGINES["motion"], self.ARGS, script=script, after=None, instruction="足す", attachments=[],
                                  skills_dir=SKILLS_DIR)
        self.assertIn('"beats": 2', ins)
        self.assertNotIn("（型）", ins)


class MotionBase(Base):
    def setUp(self) -> None:
        self.start()
        self.fx.server.tools.extra_env["FAKE_ENGINE_LOG"] = str(self.log / "engine")

    def make_motion(self, **attrs):
        body, run = self.make(mode="motion", **attrs)
        return body, run

    def head(self, deck: str) -> dict:
        with self.fx.server.store_lock:
            return json.loads(self.fx.store.script_path(self.fx.store.head_script(deck)).read_text(encoding="utf-8"))

    def view(self, deck: str) -> dict:
        return self.fx.get(f"/api/decks/{deck}").json()

    def engine_script(self, deck: str) -> dict:
        return json.loads(self.fx.root.engine_script_path(deck).read_text(encoding="utf-8"))

    def instruct(self, deck: str, positions: list[int], text: str, **extra):
        pages = self.view(deck)["pages"]
        ids = [pages[n - 1]["slide_id"] for n in positions]
        return self.fx.post(f"/api/decks/{deck}/instructions", {"scope": "pages", "slide_ids": ids, "text": text, **extra})


class MakeTests(MotionBase):
    def test_a_motion_deck_is_made_checked_and_laid_out_by_scene(self) -> None:
        body, run = self.make_motion()
        self.assertEqual((body["mode"], run["state"], run["mode"], run["engine"]), ("motion", "done", "motion", "motion-slide"), run["message"])
        deck = body["deck_id"]
        self.assertEqual(run["args"]["images"], False, "モーションは画像の枠を持たない")
        view = self.view(deck)
        self.assertEqual(view["deck"]["mode"], "motion")
        self.assertEqual(view["deck"]["mode_label"], "モーション")
        pages = view["pages"]
        self.assertEqual([p["key"] for p in pages], ["cover", "why", "rules", "closing"], "場面 = ページ")
        self.assertEqual([p["type"] for p in pages], ["scene"] * 4)
        self.assertEqual(pages[1]["title"], "会議の4割は何も決めない")
        self.assertIn("会議の4割は何も決めないの1つ目（架空）", pages[1]["body"])
        self.assertNotIn("<p", pages[1]["body"], "画面に出すのは見える文字だけ")
        for p in pages:
            self.assertEqual(p["display"]["key"], "ready", p)
            self.assertTrue(p["image_url"])
            self.assertEqual(self.fx.get(p["image_url"]).status, 200)
            # 絵の直し（revise）は無い: 選択肢は出さず、理由を言う
            self.assertEqual((p["can_minor"]["available"], p["can_minor"]["hidden"]), (False, True))
            self.assertIn("絵だけを直す操作がありません", p["can_minor"]["reason"])

    def test_the_engine_gets_theme_and_logo_only_and_the_quick_check_comes_first(self) -> None:
        brand = self.brand(corner="bottom-left")
        body, run = self.make_motion(brand_id=brand["id"])
        self.assertEqual(run["state"], "done", run["message"])
        calls = engine_calls(self.log)
        cmds = [c["argv"][0] for c in calls]
        self.assertEqual(cmds, ["check", "check", "deck"], "形だけの検査（--quick）→ 全拍の検査 → 組み立て")
        quick, full, deck = (c["argv"] for c in calls)
        self.assertIn("--quick", quick)
        self.assertNotIn("--quick", full)
        for argv in (quick, full, deck):
            for flag in ("--style", "--image-look", "--logo-position", "--watermark", "--logo-white"):
                self.assertNotIn(flag, argv, flag)
            self.assertTrue(arg(argv, "--theme").endswith("theme.yaml"))
        self.assertTrue(arg(deck, "--logo"))
        self.assertEqual(arg(deck, "--format"), "png,thumbs")
        # 台本は場面（scenes）で渡る。フッタ・ページ番号は入れない（motion-slide は知らないキーを NG にする）
        script = self.engine_script(body["deck_id"])
        self.assertEqual(sorted(script), ["config", "scenes", "title"])
        self.assertEqual(len(script["scenes"]), 4)

    def test_failed_quick_check_is_rewritten_before_any_full_check(self) -> None:
        self.start(mode="invalid_first")
        self.fx.server.tools.extra_env["FAKE_ENGINE_LOG"] = str(self.log / "engine")
        body, run = self.make_motion()
        self.assertEqual(run["state"], "done", run["message"])
        self.assertEqual([c["kind"] for c in writer_calls(self.log)], ["claude"] * 6)
        cmds = [("quick" if "--quick" in c["argv"] else c["argv"][0]) for c in engine_calls(self.log)]
        # 1 回目の台本は形の NG（全拍の検査には進まない）。2 回目は quick → 全拍 → 組み立て
        self.assertEqual(cmds, ["quick", "quick", "check", "deck"])
        second = writer_calls(self.log)[-1]
        self.assertTrue(second["retry"])
        self.assertTrue(any("NG: 1枚目（id cover）" in ln for ln in second["ng"]), second["ng"])

    def test_a_full_check_ng_goes_back_to_the_writer(self) -> None:
        self.fx.server.tools.extra_env["FAKE_ENGINE_FULL_NG"] = "1"
        body, run = self.make_motion()
        self.assertEqual((run["state"], run["failure_kind"]), ("failed", "script"), run["message"])
        cmds = [("quick" if "--quick" in c["argv"] else c["argv"][0]) for c in engine_calls(self.log)]
        self.assertEqual(cmds, ["quick", "check"] * 3, "書き直しのたびに形 → 全拍。3 回で諦める")

    def test_writer_may_say_scenes_and_the_emoji_check_applies_to_scene_text(self) -> None:
        self.start(mode="scenes_key")
        body, run = self.make_motion()
        self.assertEqual(run["state"], "done", run["message"])
        self.assertEqual([p["key"] for p in self.view(body["deck_id"])["pages"]], ["cover", "why", "rules", "closing"])
        self.assertIn("slides", self.head(body["deck_id"]), "台本は slides で持つ")
        self.assertNotIn("scenes", self.head(body["deck_id"]))
        self.start(mode="emoji_first")
        body, run = self.make_motion(title="絵文字")
        self.assertEqual(run["state"], "done", run["message"])
        calls = writer_calls(self.log)
        self.assertEqual(len(calls), 6)
        self.assertTrue(any("絵文字" in ln and "3枚目（id rules）" in ln for ln in calls[-1]["ng"]), calls[-1]["ng"])

    def test_bad_json_during_scene_repair_keeps_the_original_check_result(self) -> None:
        self.start(mode='invalid_repair_garbage')
        _, run = self.make_motion()
        self.assertEqual(run['state'], 'done', run['message'])
        calls = writer_calls(self.log)
        self.assertEqual(len(calls), 7)
        self.assertTrue(any('NG: 1枚目（id cover）' in line for line in calls[-1]['ng']))
        self.assertEqual(Path(calls[-1]['cwd']).name, 'cover')

    def test_codex_also_writes_plan_and_scenes(self) -> None:
        self.start(cfg=Settings(writer='codex', worker=WorkerSettings(parallel=4)))
        _, run = self.make_motion()
        self.assertEqual((run['state'], run['args']['writer']), ('done', 'codex'), run['message'])
        calls = writer_calls(self.log)
        self.assertEqual([c['kind'] for c in calls], ['codex'] * 5)
        self.assertEqual(sum(c['what'] == 'motion_plan' for c in calls), 1)
        self.assertEqual(sum(c['what'] == 'motion_scene' for c in calls), 4)

    def test_resume_keeps_completed_scenes_and_skips_research_and_plan(self) -> None:
        from test_create_from_screen import request_payload
        self.start(cfg=Settings(worker=WorkerSettings(parallel=4, writer_timeout_seconds=2)))
        self.fx.server.tools.extra_env['FAKE_MOTION_SLEEP_SCENE'] = 'why'
        response = self.fx.post('/api/intakes', request_payload('4 場面で説明する（架空）', mode='motion'))
        self.assertEqual(response.status, 201, response.body)
        deck = response.json()['deck_id']
        run = wait_until_idle(self.fx, deck)
        self.assertEqual((run['state'], run['failure_kind']), ('failed', 'writer_timeout'))
        self.assertIn('3/4', run['message'])
        before = len(writer_calls(self.log))
        self.fx.server.tools.extra_env.pop('FAKE_MOTION_SLEEP_SCENE')
        resumed = self.fx.post(f'/api/decks/{deck}/resume')
        self.assertEqual(resumed.status, 200, resumed.body)
        run = wait_until_idle(self.fx, deck)
        self.assertEqual(run['state'], 'done', run['message'])
        calls = writer_calls(self.log)[before:]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['what'], 'motion_scene')
        self.assertEqual(Path(calls[0]['cwd']).name, 'why')
        self.assertEqual(len(self.view(deck)['pages']), 4)

    def test_the_writer_prompt_is_the_motion_one(self) -> None:
        self.make_motion(style="standard")
        call = next(c for c in writer_calls(self.log) if c["what"] == "motion_scene")
        self.assertTrue(call["motion"] and call["has_motion_contract"])
        self.assertFalse(call["has_pitch_contract"])
        text = "\n".join(call["prompt_lines"])
        self.assertIn("場面の配列は `slides` のキーに書く", text)
        self.assertNotIn("画像の枠は", text)

    def test_intake_validation(self) -> None:
        r = self.fx.post("/api/intakes", intake_payload(mode="motion", style="visual"))
        self.assertEqual(r.status, 400, r.body)
        self.assertIn("standard", r.json()["error"]["message"])
        r = self.fx.post("/api/intakes", intake_payload(mode="movie"))
        self.assertEqual(r.status, 400)
        self.assertIn("motion", r.json()["error"]["message"])
        r = self.fx.post("/api/intakes", intake_payload(mode="motion", images=True))
        self.assertEqual(r.status, 201, "images を言われても、モーションは画像を持たない")

    def test_a_missing_chromium_stops_before_the_writer_is_called(self) -> None:
        self.fx.server.tools.motion_probe = [sys.executable, "-c", "import json,sys; print(json.dumps(['chromium'])); sys.exit(4)"]
        body, run = self.make_motion()
        self.assertEqual((run["state"], run["failure_kind"]), ("failed", "dependency"))
        self.assertIn("Playwright の Chromium", run["message"])
        self.assertIn("--doctor", run["message"])
        self.assertEqual(writer_calls(self.log), [], "描けないのに台本係の枠を使わない")

    def test_an_engine_dependency_failure_says_motion_not_node(self) -> None:
        self.fx.server.tools.extra_env["FAKE_ENGINE_RC"] = "4"
        body, run = self.make_motion()
        self.assertEqual((run["state"], run["failure_kind"]), ("failed", "dependency"))
        self.assertIn("Playwright", run["message"])
        self.assertNotIn("puppeteer", run["message"])

    def test_the_run_folder_keeps_index_html_check_and_present_copy(self) -> None:
        body, run = self.make_motion()
        run_dir = self.fx.root.run_dir(body["deck_id"], run["id"])
        index = (run_dir / "index.html").read_bytes()
        self.assertIn(b'id="stage"', index)
        self.assertEqual((run_dir / W.PRESENT / "index.html").read_bytes(), index)
        self.assertTrue((run_dir / "check" / "beats" / "s01-b1.png").is_file())
        self.assertTrue((run_dir / "check" / "inspect.md").is_file())
        self.assertTrue((run_dir / "deck-info.json").is_file())
        self.assertFalse((run_dir / "deck.pptx").exists())
        work = self.fx.root.work_dir(body["deck_id"])
        self.assertTrue((work / "index.html").is_file())
        # 次の回の前に作業フォルダの出力（index.html・check/）を消す。控えの回の中身は変わらない
        W.clear_outputs(work)
        self.assertFalse((work / "index.html").exists() or (work / "check").exists())
        self.assertEqual((run_dir / "index.html").read_bytes(), index)


class EditTests(MotionBase):
    def setUp(self) -> None:
        super().setUp()
        self.body, self.run = self.make_motion()
        self.assertEqual(self.run["state"], "done", self.run["message"])
        self.deck = self.body["deck_id"]

    def wait(self) -> dict:
        run = wait_until_idle(self.fx, self.deck)
        return run

    def test_revise_one_scene_rewrites_only_that_scene_and_rebuilds(self) -> None:
        before = self.head(self.deck)
        n_calls = len(writer_calls(self.log))
        r = self.instruct(self.deck, [2], "もっと短く")
        self.assertEqual(r.status, 200, r.body)
        run = self.wait()
        self.assertEqual(run["state"], "done", run["message"])
        after = self.head(self.deck)
        self.assertEqual(after["slides"][1]["title"], before["slides"][1]["title"] + "（直し）")
        for i in (0, 2, 3):
            self.assertEqual(after["slides"][i], before["slides"][i], "指定外の場面は変わらない")
        revise = [c for c in writer_calls(self.log)[n_calls:] if c["what"] == "revise"]
        self.assertEqual(len(revise), 1)
        self.assertEqual(revise[0]["targets"], "why")
        self.assertTrue(revise[0]["has_motion_contract"])
        cmds = [c["argv"][0] for c in engine_calls(self.log)]
        self.assertEqual(cmds[-3:], ["check", "check", "deck"], "直したあとも全体を組み直す（revise は呼ばない）")
        self.assertNotIn("revise", cmds)

    def test_an_image_route_is_refused_for_motion_and_the_writer_is_sent_back(self) -> None:
        self.start(mode="ok")   # 偽物の台本係は「絵だけ」で image の振り分けを返す
        body, _ = self.make_motion(title="絵の直し")
        deck = body["deck_id"]
        pages = self.view(deck)["pages"]
        r = self.fx.post(f"/api/decks/{deck}/instructions", {"scope": "pages", "slide_ids": [pages[0]["slide_id"]], "text": "絵だけ直して"})
        self.assertEqual(r.status, 200, r.body)
        run = wait_until_idle(self.fx, deck)
        self.assertEqual((run["state"], run["failure_kind"]), ("failed", "script"))
        calls = [c for c in writer_calls(self.log) if c["what"] == "revise"]
        self.assertTrue(any("直せる絵が無い" in ln for c in calls for ln in c["ng"]), [c["ng"] for c in calls])
        self.assertTrue(self.view(deck)["pages"][0]["display"]["key"] == "ready", "前の姿のまま")

    def test_attachments_are_refused(self) -> None:
        r = self.fx.request("POST", "/api/settings/images", raw_body=__import__("helpers").png_bytes(), session=True)
        asset = r.json()["asset_id"]
        pages = self.view(self.deck)["pages"]
        r = self.instruct(self.deck, [1], "直す", attachments=[asset])
        self.assertEqual((r.status, r.error_code), (400, "attachments_not_supported"), r.body)
        r = self.fx.post(f"/api/decks/{self.deck}/pages/insert", {"after_slide_id": pages[0]["slide_id"], "text": "足す", "attachments": [asset]})
        self.assertEqual((r.status, r.error_code), (400, "attachments_not_supported"))

    def test_insert_duplicate_hide_and_reorder_work_per_scene(self) -> None:
        pages = self.view(self.deck)["pages"]
        first = pages[0]["slide_id"]
        r = self.fx.post(f"/api/decks/{self.deck}/pages/insert", {"after_slide_id": first, "text": "費用の場面を足して"})
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(self.wait()["state"], "done")
        self.assertEqual([p["key"] for p in self.view(self.deck)["pages"]], ["cover", "p-added-1", "why", "rules", "closing"])
        self.assertEqual(len(self.engine_script(self.deck)["scenes"]), 5)
        # 複製
        pages = self.view(self.deck)["pages"]
        r = self.fx.post(f"/api/decks/{self.deck}/pages/{pages[2]['slide_id']}/duplicate", {})
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(self.wait()["state"], "done")
        keys = [p["key"] for p in self.view(self.deck)["pages"]]
        self.assertEqual(keys[:4], ["cover", "p-added-1", "why", "why-copy"])
        # 非表示: 組み立てる台本から場面ごと外れる（場面の数が減る）
        pages = self.view(self.deck)["pages"]
        r = self.fx.post(f"/api/decks/{self.deck}/pages/{pages[3]['slide_id']}/visibility", {"hidden": True})
        self.assertEqual(r.status, 200, r.body)
        run = self.wait()
        self.assertEqual(run["state"], "done")
        shown = self.engine_script(self.deck)["scenes"]
        self.assertNotIn("why-copy", [s["id"] for s in shown])
        self.assertTrue(all("_hidden" not in s for s in shown))
        view = self.view(self.deck)
        self.assertEqual([p["hidden"] for p in view["pages"]][:5], [False, False, False, True, False])
        run_dir = self.fx.root.run_dir(self.deck, run["id"])
        self.assertEqual(len(list((run_dir / "slides").glob("slide-*.png"))), len(shown), "隠した場面の絵は作らない")
        # 並べ替え
        ids = [p["slide_id"] for p in view["pages"]]
        r = self.fx.post(f"/api/decks/{self.deck}/pages/reorder", {"slide_ids": [ids[1], ids[0], *ids[2:]]})
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(self.wait()["state"], "done")
        self.assertEqual([p["key"] for p in self.view(self.deck)["pages"]][:2], ["p-added-1", "cover"])

    def test_version_stack_restore_puts_the_old_scene_back(self) -> None:
        before = self.head(self.deck)
        r = self.instruct(self.deck, [2], "短く")
        self.assertEqual(r.status, 200)
        self.assertEqual(self.wait()["state"], "done")
        pages = self.view(self.deck)["pages"]
        versions = pages[1]["versions"]
        self.assertEqual(len(versions), 2)
        old = next(v for v in versions if not v["active"])
        r = self.fx.post(f"/api/decks/{self.deck}/adopt", {"choices": {pages[1]["slide_id"]: old["id"]}})
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(self.wait()["state"], "done")
        after = self.head(self.deck)["slides"][1]
        self.assertEqual(after.pop("_pin"), old["id"], "戻した場面は、その版を描いた回を指す（描き直さない）")
        self.assertEqual(after, before["slides"][1])
        page = self.view(self.deck)["pages"][1]
        self.assertEqual((len(page["versions"]), page["version"], page["pinned"]), (2, old["version"], True),
                         "切り替えても版は増えない")
        # 組み直しの後は、発表は見せる回の HTML 1枚（中身から組み直してある）で、切り替えた場面も拍で進む
        data = self.fx.get(f"/api/decks/{self.deck}/present").json()
        shown = wait_until_idle(self.fx, self.deck)
        self.assertEqual(data["html"]["url"], f"/present/{self.deck}/{shown['id']}/index.html",
                         "版を切り替えた場面があっても、ページの絵だけにしない")
        self.assertEqual([p["section"] for p in data["pages"]], [0, 1, 2, 3])
        self.assertEqual([p["steps"] for p in data["pages"]], [1, 2, 1, 1])


class PresentTests(MotionBase):
    def setUp(self) -> None:
        super().setUp()
        self.body, self.run = self.make_motion()
        self.deck = self.body["deck_id"]

    def test_present_data_has_scenes_as_pages_and_beats_as_steps(self) -> None:
        data = self.fx.get(f"/api/decks/{self.deck}/present").json()
        self.assertEqual((data["engine"], data["run_id"]), ("motion-slide", self.run["id"]))
        self.assertEqual([p["steps"] for p in data["pages"]], [1, 2, 1, 1], "段 = 拍の数 - 1")
        self.assertEqual([p["section"] for p in data["pages"]], [0, 1, 2, 3])
        self.assertEqual(data["html"]["url"], f"/present/{self.deck}/{self.run['id']}/index.html")
        for p in data["pages"]:
            self.assertEqual(self.fx.get(p["image_url"]).status, 200)

    def test_index_html_is_served_with_the_motion_csp_and_the_bridge(self) -> None:
        r = self.fx.get(f"/present/{self.deck}/{self.run['id']}/index.html")
        self.assertEqual(r.status, 200)
        self.assertIn("text/html", r.headers["content-type"])
        csp = r.headers["content-security-policy"]
        host = f"127.0.0.1:{self.fx.port}"
        self.assertEqual(csp, motion_csp(host))
        directives = {d.split(" ", 1)[0]: d.split(" ", 1)[1] if " " in d else "" for d in csp.split("; ")}
        self.assertEqual(directives["default-src"], "'none'")
        self.assertEqual(directives["script-src"], f"'unsafe-inline' 'unsafe-eval' http://{host}{MOTION_PLAYER_PATH}")
        self.assertEqual(directives["style-src"], "'unsafe-inline'")
        self.assertEqual(directives["img-src"], "data: 'self'")
        self.assertEqual(directives["media-src"], "data: 'self' blob:")
        self.assertEqual(directives["font-src"], "data: 'self'")
        self.assertEqual((directives["base-uri"], directives["form-action"], directives["frame-ancestors"], directives["sandbox"]),
                         ("'none'", "'none'", "'self'", "allow-scripts"))
        self.assertNotIn("connect-src", csp)
        self.assertNotIn("allow-same-origin", csp)
        body = r.body.decode("utf-8")
        self.assertEqual(body.count(f'<script src="{MOTION_PLAYER_PATH}"></script>'), 1)
        self.assertTrue(body.index(MOTION_PLAYER_PATH) > body.index('id="stage"'), "エンジンのあとに置く")
        # 控えそのものは書き換えない（橋渡しは配信のときだけ足す）
        saved = (self.fx.root.run_dir(self.deck, self.run["id"]) / W.PRESENT / "index.html").read_text(encoding="utf-8")
        self.assertNotIn(MOTION_PLAYER_PATH, saved)
        # 橋渡しのスクリプトは配信される。html-slide の再生スクリプトも別のまま
        js = self.fx.get(MOTION_PLAYER_PATH)
        self.assertEqual(js.status, 200)
        self.assertIn(b"present-motion", js.body)
        self.assertEqual(self.fx.get("/static/present-frame.js").status, 200)

    def test_only_the_html_of_a_motion_run_is_served_and_nothing_around_it(self) -> None:
        base = f"/present/{self.deck}/{self.run['id']}"
        self.assertEqual(self.fx.get(f"{base}/deck.html").status, 404, "motion に deck.html は無い")
        for rel in ("../index.html", "check/inspect.md", "inspect.md", "index.html/x", "index.htm", "build.json"):
            self.assertEqual(self.fx.get(f"{base}/{rel}").status, 404, rel)
        self.assertIsNotNone(PresentFiles(self.fx.root.root).resolve(self.deck, self.run["id"], "index.html"))
        self.assertIsNone(PresentFiles(self.fx.root.root).resolve(self.deck, str(uuid.uuid4()), "index.html"))

    def test_hidden_scenes_are_left_out_of_the_present_pages(self) -> None:
        pages = self.view(self.deck)["pages"]
        r = self.fx.post(f"/api/decks/{self.deck}/pages/{pages[1]['slide_id']}/visibility", {"hidden": True})
        self.assertEqual(r.status, 200)
        wait_until_idle(self.fx, self.deck)
        data = self.fx.get(f"/api/decks/{self.deck}/present").json()
        self.assertEqual([p["title"] for p in data["pages"]], ["会議を半分にする", "3つのルールで短くする", "まず2チームで試す"])
        self.assertEqual([p["section"] for p in data["pages"]], [0, 1, 2], "エンジンの場面の番号は詰まる")

    def test_motion_document_adds_one_script_and_nothing_else(self) -> None:
        self.assertEqual(motion_document("<body>x</body>"), f'<body>x<script src="{MOTION_PLAYER_PATH}"></script></body>')
        self.assertEqual(motion_document("<p>x"), f'<p>x<script src="{MOTION_PLAYER_PATH}"></script>')

    def test_the_bridge_script_parses(self) -> None:
        node = __import__("shutil").which("node")
        if node is None:
            self.skipTest("node が無い")
        for name in ("present-motion-frame.js", "present.js"):
            r = subprocess.run([node, "--check", str(REPO / "web" / "app" / name)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)


class ExportTests(MotionBase):
    def setUp(self) -> None:
        super().setUp()
        self.body, self.run = self.make_motion(title="動く: 会議/半分")
        self.deck = self.body["deck_id"]

    def test_the_menu_offers_only_html(self) -> None:
        """モーションの書き出しのメニューは HTML だけ。書き出せない PDF・PPTX は並べない（口は理由つきで断る）。"""
        view = self.view(self.deck)
        formats = {f["format"]: f for f in view["export_formats"]}
        self.assertEqual(list(formats), ["html"])
        self.assertEqual(formats["html"]["label"], "HTML")
        self.assertTrue(formats["html"]["available"])
        for fmt in ("pdf", "pptx"):
            r = self.fx.post(f"/api/decks/{self.deck}/exports", {"format": fmt})
            self.assertEqual(r.status, 409, r.body)
            self.assertIn("「HTML」", r.json()["error"]["message"])
        self.assertTrue(view["capabilities"]["export"]["available"])
        # プレゼン・ピッチには HTML を出さない（PDF・PPTX は書き出せる）
        body, _ = self.make(mode="presentation", title="普通のスライド")
        other = {f["format"]: f for f in self.view(body["deck_id"])["export_formats"]}
        self.assertEqual(list(other), ["pdf", "pptx"])
        self.assertTrue(all(f["available"] for f in other.values()))

    def test_html_export_is_the_index_html_as_is(self) -> None:
        r = self.fx.post(f"/api/decks/{self.deck}/exports", {"format": "html"})
        self.assertEqual(r.status, 201, r.body)
        ex = r.json()["export"]
        self.assertEqual((ex["format"], ex["run_id"], ex["filename"]), ("html", self.run["id"], "動く_ 会議_半分.html"))
        got = self.fx.get(ex["download_url"])
        self.assertEqual(got.status, 200)
        self.assertEqual(got.body, (self.fx.root.run_dir(self.deck, self.run["id"]) / "index.html").read_bytes())
        self.assertTrue(got.headers["content-disposition"].startswith("attachment;"))
        self.assertIn("text/html", got.headers["content-type"])
        self.assertEqual(got.headers["content-security-policy"], "sandbox allow-scripts")
        self.assertEqual(self.fx.post(f"/api/decks/{self.deck}/exports", {"format": "html"}).json()["export"]["id"], ex["id"])

    def test_pdf_and_pptx_are_refused_for_motion_and_html_for_the_others(self) -> None:
        for fmt in ("pdf", "pptx"):
            r = self.fx.post(f"/api/decks/{self.deck}/exports", {"format": fmt})
            self.assertEqual((r.status, r.error_code), (409, "export_unsupported"), r.body)
            self.assertIn("「HTML」", r.json()["error"]["message"])
        body, _ = self.make(mode="presentation", title="普通")
        r = self.fx.post(f"/api/decks/{body['deck_id']}/exports", {"format": "html"})
        self.assertEqual((r.status, r.error_code), (409, "export_unsupported"), r.body)
        # ページの画像の ZIP はどのモードでも書き出せる
        self.assertEqual(self.fx.post(f"/api/decks/{self.deck}/exports", {"format": "images"}).status, 201)

    def test_html_export_survives_a_render_with_the_same_bytes(self) -> None:
        first = self.fx.post(f"/api/decks/{self.deck}/exports", {"format": "html"}).json()["export"]
        with self.fx.server.store_lock:
            self.fx.store.create_edit(self.deck, {"op": "render", "source": "screen"})
        wait_until_idle(self.fx, self.deck)
        again = self.fx.post(f"/api/decks/{self.deck}/exports", {"format": "html"}).json()["export"]
        self.assertNotEqual(again["run_id"], first["run_id"], "新しい回のファイルを配る")
        self.assertEqual(self.fx.get(again["download_url"]).status, 200)


class MigrationTests(unittest.TestCase):
    def seed_v4(self, tmp: Path) -> sqlite3.Connection:
        """版 4 の DB（0001〜0004 だけ適用）に、プレゼンとピッチの 2 冊・描いた回・書き出しを直に入れる。"""
        import shutil

        old = tmp / "m4"
        old.mkdir()
        for m in load_migrations():
            if m.version <= 4:
                shutil.copyfile(m.path, old / m.path.name)
        conn = sqlite3.connect(tmp / "q.db", isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        run_migrate(conn, directory=old)
        self.assertEqual(user_version(conn), 4)
        t = "2026-10-11T00:00:00Z"
        conn.execute("BEGIN")
        conn.execute("INSERT INTO brands(id,name,current_revision_id,kind,created_at,updated_at) VALUES('b','b','br','user',?,?)", (t, t))
        conn.execute("INSERT INTO brand_revisions(id,brand_id,revision,config_json,created_at) VALUES('br','b',1,'{}',?)", (t,))
        conn.execute("INSERT INTO styles(id,name,current_revision_id,kind,created_at,updated_at) VALUES('s','s','sr','user',?,?)", (t, t))
        conn.execute("INSERT INTO style_revisions(id,style_id,revision,config_json,created_at) VALUES('sr','s',1,'{}',?)", (t,))
        for did, mode, engine in (("d1", "presentation", "html-slide"), ("d2", "pitch", "image-slide")):
            conn.execute("INSERT INTO decks(id,title,mode,brand_revision_id,style_revision_id,pinned,created_at,updated_at)"
                         " VALUES(?,?,?,'br','sr',1,?,?)", (did, did, mode, t, t))
            conn.execute("INSERT INTO runs(id,deck_id,number,mode,engine,args_json,state,created_at,updated_at,ended_at)"
                         " VALUES(?,?,1,?,?,'{}','done',?,?,?)", (f"r-{did}", did, mode, engine, t, t, t))
            conn.execute("INSERT INTO exports(id,deck_id,run_id,format,path,sha256,bytes,filename,created_at)"
                         " VALUES(?,?,?,?,?,?,5,'a.pdf',?)", (f"e-{did}", did, f"r-{did}", "pdf", "decks/x", "0" * 64, t))
        conn.execute("COMMIT")
        return conn

    def test_upgrading_a_v4_db_keeps_every_row_and_accepts_motion(self) -> None:
        from helpers import temp_dir

        conn = self.seed_v4(temp_dir(self))
        before = {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")] for t in ("decks", "runs", "exports")}
        run_migrate(conn)
        self.assertEqual(user_version(conn), LATEST_VERSION)
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1, "外部キーは戻す")
        after = {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")] for t in ("decks", "runs", "exports")}
        # 0013 が足した列（デザインテンプレートの控え。旧い行は NULL）は、これまでの列の中身と分けて見る
        for row in after["decks"]:
            self.assertIsNone(row.pop("template_snapshot_id"))
        self.assertEqual(after, before, "中身はそのまま")
        # モーションの 1 冊・描いた回・HTML の書き出しを受ける。参照（デッキ ← 描いた回 ← 書き出し）も効く
        t = "2026-10-11T00:00:00Z"
        conn.execute("INSERT INTO decks(id,title,mode,brand_revision_id,style_revision_id,created_at,updated_at)"
                     " VALUES('d3','動く','motion','br','sr',?,?)", (t, t))
        conn.execute("INSERT INTO runs(id,deck_id,number,mode,engine,args_json,state,created_at,updated_at)"
                     " VALUES('r-d3','d3',1,'motion','motion-slide','{}','queued',?,?)", (t, t))
        conn.execute("INSERT INTO exports(id,deck_id,run_id,format,path,sha256,bytes,filename,created_at)"
                     " VALUES('e-d3','d3','r-d3','html','decks/y',?,5,'a.html',?)", ("1" * 64, t))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE decks SET mode='movie' WHERE id='d3'")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE runs SET engine='other' WHERE id='r-d3'")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO runs(id,deck_id,number,mode,engine,args_json,state,created_at,updated_at)"
                         " VALUES('r2','d3',1,'motion','motion-slide','{}','queued',?,?)", (t, t))   # (deck_id, number) は一意のまま
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO exports(id,deck_id,run_id,format,path,sha256,bytes,filename,created_at)"
                         " VALUES('e2','d3','no-such-run','html','p',?,5,'a.html',?)", ("1" * 64, t))
        conn.execute("PRAGMA foreign_keys = ON")
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertTrue({"deck_recent", "run_deck", "export_deck", "job_deck"} <= indexes, indexes)

    def test_a_fresh_db_has_the_same_schema_as_an_upgraded_one(self) -> None:
        from helpers import open_store, temp_dir

        conn = self.seed_v4(temp_dir(self))
        run_migrate(conn)
        fresh = open_store(self).conn

        def shapes(c: sqlite3.Connection) -> dict:
            out = {}
            for table in ("decks", "runs", "exports", "intakes"):
                out[table] = [(r[1], r[2], r[3]) for r in c.execute(f"PRAGMA table_info({table})")]
            return out

        self.assertEqual(shapes(conn), shapes(fresh))
        self.assertGreaterEqual(LATEST_VERSION, 5)   # 6 で取り込みに依頼の列（docs/adr/0002-create-from-screen.md）


class SkillStartTests(unittest.TestCase):
    def test_start_accepts_motion_and_says_its_styles(self) -> None:
        sys.path.insert(0, str(REPO / "skills" / "quick-slide" / "scripts"))
        import importlib

        start = importlib.import_module("quick_slide_start")
        self.assertIn("motion", start.MODES)
        self.assertEqual(start.STYLES["motion"], ("standard",))
        self.assertIn("モーション", start.conversation_message({"deck_title": "動く", "mode": "motion"}))
        self.assertIn("プレゼン", start.conversation_message({"deck_title": "普通", "mode": "presentation"}))
        self.assertIn("ピッチ", start.conversation_message({"deck_title": "絵", "mode": "pitch"}))
        p = start.build_parser()
        self.assertEqual(p.parse_args(["--mode", "motion"]).mode, "motion")


if __name__ == "__main__":
    unittest.main()


class MotionLogoTests(unittest.TestCase):
    def test_no_brand_logo_is_passed_as_none(self) -> None:
        # motion-slide はロゴを渡さないと機体の設定のロゴを既定に使うので、ブランドにロゴが無ければ「なし」を渡す
        from quick_slide.engines import ENGINES
        self.assertEqual(ENGINES["motion"].brand_args({"look": {}}), ["--logo", "none"])
        self.assertEqual(ENGINES["motion"].brand_args({"look": {"logo": "/x.png"}})[:2], ["--logo", "/x.png"])
        self.assertNotIn("--logo", ENGINES["presentation"].brand_args({"look": {}}))
