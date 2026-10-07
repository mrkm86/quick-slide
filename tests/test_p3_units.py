"""P3 の部品: 台本係の答えの確かめと当て方・区画の付け替え・エンジンの区画の名前との一致・構成を守る書き直し・
止まった直しの扱い。本物の LLM・codex は呼ばない。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

from helpers import MANUSCRIPT, fake_tools, intake_payload, open_store, start_server, temp_dir, wait_until_idle, writer_calls

from quick_slide.engines import ENGINES
from quick_slide.paths import SKILLS_DIR
from quick_slide.render import workspace as W
from quick_slide.scriptdoc import content_sha, dumps, engine_view, outline_headings, output_pages, screen_pages
from quick_slide.writer.patch import apply_revision, insert_after, parse_insert, parse_revision
from quick_slide.writer.prompt import build_prompt, build_revision_prompt

from test_worker import fake_skills_dir, settings

sys.dont_write_bytecode = True


def ten() -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "fakes"))
    import os

    os.environ.setdefault("FAKE_WRITER_LOG", "/nonexistent-writer-log")
    import writer_common

    return writer_common.ten_page_script()


class PatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = ten()

    def answer(self, *pages, suggestions=None):
        return {"pages": list(pages), "suggestions": suggestions or [], "summary": "直した"}

    def test_only_target_pages_change_and_the_rest_keep_their_bytes(self) -> None:
        s = self.script
        p7 = dict(s["slides"][6], headline="短く")
        patch = parse_revision(self.answer({"id": "p-seven", "route": "script", "slides": [p7]}),
                               script=s, targets=["p-seven"], pictures={}, attachments=[], mode="pitch")
        self.assertEqual(patch.problems, [])
        new = apply_revision(s, patch)
        for i, (a, b) in enumerate(zip(s["slides"], new["slides"])):
            if i == 6:
                self.assertEqual(b["headline"], "短く")
            else:
                self.assertIs(a, b, "指定外のページは同じオブジェクト")
                self.assertIn(json.dumps(a, ensure_ascii=False, indent=1).replace("\n", "\n  "), dumps(new).decode())

    def test_problems_are_reported_for_the_writer(self) -> None:
        s = self.script
        cases = [
            (self.answer({"id": "p-six", "route": "script", "slides": [s["slides"][5]]}), "直すページではない"),
            (self.answer({"id": "p-seven", "route": "paint"}), "route は"),
            (self.answer({"id": "p-seven", "route": "script", "slides": [dict(s["slides"][6], id="other")]}), "元のまま"),
            (self.answer({"id": "p-seven", "route": "script",
                          "slides": [s["slides"][6], {"id": "p-why", "type": "statement", "headline": "x"}]}), "新しい id"),
            (self.answer({"id": "p-seven", "route": "image", "image": {"note": "夜"}}), "直せる絵が無い"),
            (self.answer({"id": "p-seven", "route": "script",
                          "slides": [dict(s["slides"][6], image={"path": "/etc/x.png"})]}), "path"),
            (self.answer({"id": "p-seven", "route": "script", "slides": [dict(s["slides"][6], _hidden=True)]}), "_hidden"),
            ({"nope": 1}, "pages"),
            (self.answer(), "答えが無い"),
        ]
        for answer, word in cases:
            patch = parse_revision(answer, script=s, targets=["p-seven"], pictures={}, attachments=[], mode="pitch")
            self.assertTrue(any(word in p for p in patch.problems), (word, patch.problems))

    def test_image_route_slots_split_and_suggestions(self) -> None:
        s = self.script
        pics = {"p-seven": ["image", "member1"], "p-eight": ["image"]}
        patch = parse_revision(self.answer({"id": "p-seven", "route": "image", "image": {"note": "夜"}}, ),
                               script=s, targets=["p-seven"], pictures=pics, attachments=[], mode="presentation")
        self.assertTrue(any("image.slot" in p for p in patch.problems))
        patch = parse_revision(self.answer({"id": "p-eight", "route": "image", "image": {"note": "夜"}},
                                           suggestions=[{"id": "p-rule", "text": "短く"}, {"id": "p-eight", "text": "x"},
                                                        {"id": "nope", "text": "x"}]),
                               script=s, targets=["p-eight"], pictures=pics, attachments=[], mode="presentation")
        self.assertEqual(patch.problems, [])
        self.assertEqual(patch.pages["p-eight"].image, {"slot": "image", "note": "夜"})
        self.assertEqual(patch.suggestions, [{"id": "p-rule", "text": "短く"}], "対象と知らない id の提案は捨てる")
        split = [dict(s["slides"][6], headline="前半"), {"id": "p-seven-b", "type": "statement", "headline": "後半",
                                                        "image": {"path": "attachments/a.png"}}]
        patch = parse_revision(self.answer({"id": "p-seven", "route": "script", "slides": split}),
                               script=s, targets=["p-seven"], pictures={}, attachments=["attachments/a.png"], mode="pitch")
        self.assertEqual(patch.problems, [])
        new = apply_revision(s, patch)
        self.assertEqual([x["id"] for x in new["slides"]][6:9], ["p-seven", "p-seven-b", "p-eight"])

    def test_hidden_flag_survives_a_rewrite_and_engine_view_drops_hidden(self) -> None:
        s = dict(self.script, slides=[dict(x) for x in self.script["slides"]])
        s["slides"][6]["_hidden"] = True
        patch = parse_revision(self.answer({"id": "p-seven", "route": "script", "slides": [dict(self.script["slides"][6])]}),
                               script=s, targets=["p-seven"], pictures={}, attachments=[], mode="pitch")
        new = apply_revision(s, patch)
        self.assertTrue(new["slides"][6]["_hidden"])
        view = engine_view(new)
        self.assertEqual(len(view["slides"]), 9)
        self.assertNotIn("_hidden", json.dumps(view))
        args = {"mode": "pitch", "style": "visual"}
        self.assertEqual(len(screen_pages(args, new)), 10)
        self.assertEqual([p["no"] for p in output_pages(args, new)], list(range(1, 10)))
        self.assertEqual(content_sha(new["slides"][6]), content_sha(self.script["slides"][6]), "隠しても同じ姿")
        one = output_pages({"mode": "pitch", "style": "infographic"}, new)
        self.assertEqual([p["key"] for p in one], ["infographic"])

    def test_insert(self) -> None:
        s = self.script
        pages, _, problems = parse_insert({"slides": [{"id": "p-why", "type": "statement", "headline": "x"}]}, script=s,
                                          attachments=[])
        self.assertTrue(any("新しい id" in p for p in problems))
        pages, summary, problems = parse_insert({"slides": [{"id": "p-new", "type": "statement", "headline": "x"}],
                                                 "summary": "足した"}, script=s, attachments=[])
        self.assertEqual((problems, summary), ([], "足した"))
        self.assertEqual([x["id"] for x in insert_after(s, "p-why", pages)["slides"]][:3], ["cover", "p-why", "p-new"])
        self.assertEqual(insert_after(s, None, pages)["slides"][0]["id"], "p-new")


class PromptTests(unittest.TestCase):
    def test_outline_fixed_rules_and_revision_prompt(self) -> None:
        engine = ENGINES["pitch"]
        text = build_prompt(engine, {"style": "visual", "outline_fixed": True, "page_count": 3}, manuscript=MANUSCRIPT,
                            title_hint=None, skills_dir=SKILLS_DIR)
        self.assertIn("合意した「構成」", text)
        self.assertIn("ページの数・順番・各ページの主張", text)
        self.assertNotIn("ストーリー（何をどの順で言うか・何枚にするか）と、各ページの型はあなたが決める", text)
        self.assertIn("source", text)
        rev = build_revision_prompt(ENGINES["presentation"], {"style": "standard"}, script=ten(),
                                    targets=[{"id": "p-seven", "position": 7, "title": "t", "picture": ["image"]}],
                                    instruction="見出しを短く", attachments=["attachments/a.png"], skills_dir=SKILLS_DIR)
        for word in ("対象の id: p-seven", "直せる絵: 枠 image", "suggestions", "迷ったら script", "attachments/a.png",
                     "<<<指示ここから\n見出しを短く\n指示ここまで>>>", "エンジンが数える 7枚目"):
            self.assertIn(word, rev)

    def test_outline_headings(self) -> None:
        self.assertEqual(outline_headings("# 題\n## 1. 一つ目\n本文\n## 2．二つ目\n### 3. 小見出しではない"),
                         [(1, "一つ目"), (2, "二つ目")])


class WorkspaceTests(unittest.TestCase):
    def test_area_name_matches_the_engine(self) -> None:
        """区画の名前の付け方はエンジン（image-slide の cache.area_path）と同じでなければならない。"""
        scripts = SKILLS_DIR / "image-slide" / "scripts"
        sys.path.insert(0, str(scripts))
        try:
            spec = importlib.util.spec_from_file_location("engine_cache_for_test", scripts / "cache.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            sys.path.remove(str(scripts))
        tmp = temp_dir(self)
        for owner in (tmp / "decks" / "x" / "work", tmp / "a b" / "work"):
            self.assertEqual(W.pitch_area_for(owner, tmp / "cache" / "other-123456789abc"),
                             mod.area_path(tmp / "cache", owner))

    def test_arrange_moves_seeds_aliases_and_falls_back(self) -> None:
        tmp = temp_dir(self)
        work, area = tmp / "work", tmp / "area"
        work.mkdir()
        area.mkdir()
        for n, k in enumerate(["a", "b", "c"], 1):
            (area / f"slide-{n:02d}.png").write_text(k)
            (area / f"slide-{n:02d}.json").write_text(json.dumps({"key": k}))
        W.write_layout(work, ["a", "b", "c"], str(area))
        seed = tmp / "seed.png"
        seed.write_text("old-b")
        snap = tmp / "snap-d.png"
        snap.write_text("d")
        placed = W.arrange_pitch_cache(work, ["c", "a", "a2", "b", "d", "e"], area=area,
                                       seeds={"b": (seed, None)}, aliases={"a2": "a"},
                                       fallback=lambda k: (snap, None) if k == "d" else None)
        self.assertEqual([(area / f"slide-{n:02d}.png").read_text() if (area / f"slide-{n:02d}.png").exists() else None
                          for n in range(1, 7)], ["c", "a", "a", "old-b", "d", None])
        self.assertEqual(placed, {"c": "moved", "a": "moved", "a2": "alias", "b": "seed", "d": "snapshot"})
        self.assertEqual(json.loads((area / "slide-03.json").read_text())["key"], "a")
        self.assertFalse((area / W.STAGE).exists())
        self.assertEqual(W.read_layout(work)["ids"], ["c", "a", "a2", "b", "d", "e"])


class OutlineAndRecoveryTests(unittest.TestCase):
    def start(self, *, mode: str = "ok"):
        tools, log = fake_tools(self, mode=mode, skills_dir=fake_skills_dir(self))
        tools.extra_env.update({"FAKE_ENGINE_SLEEP": "0.01"})
        return start_server(self, tools=tools, settings=settings()), log

    def test_outline_fixed_rewrites_when_the_page_count_differs(self) -> None:
        fx, log = self.start(mode="outline_wrong_first")
        payload = intake_payload(mode="pitch", outline_fixed=True)
        payload["manuscript"] = "# 題\n\n## 1. 一つ目の主張\n- a\n\n## 2. 二つ目の主張\n- b\n\n## 3. 三つ目の主張\n- c\n"
        body = fx.post("/api/intakes", payload).json()
        run = wait_until_idle(fx, body["deck_id"])
        self.assertEqual(run["state"], "done", run["message"])
        calls = writer_calls(log)
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("構成は 3 ページ" in ln for ln in calls[1]["ng"]), calls[1]["ng"])
        titles = [p["title"] for p in fx.get(f"/api/decks/{body['deck_id']}").json()["pages"]]
        self.assertEqual(titles, ["一つ目の主張", "二つ目の主張", "三つ目の主張"])
        r = fx.post("/api/intakes", {**intake_payload(mode="pitch", outline_fixed=True), "manuscript": "見出しの無い原稿"})
        self.assertEqual((r.status, r.error_code), (400, "validation_error"))

    def test_infographic_revises_the_whole_script_and_refuses_page_operations(self) -> None:
        fx, log = self.start()
        fx.server.tools.extra_env["FAKE_ENGINE_PAGES"] = "1"
        body = fx.post("/api/intakes", intake_payload(mode="pitch", style="infographic")).json()
        self.assertEqual(wait_until_idle(fx, body["deck_id"])["state"], "done")
        view = fx.get(f"/api/decks/{body['deck_id']}").json()
        self.assertEqual(len(view["pages"]), 1)
        self.assertFalse(view["capabilities"]["insert_page"]["available"])
        sid = view["pages"][0]["slide_id"]
        r = fx.post(f"/api/decks/{body['deck_id']}/instructions", {"scope": "page", "slide_id": sid, "text": "短く"})
        self.assertEqual(r.status, 200, r.body)
        run = wait_until_idle(fx, body["deck_id"])
        self.assertEqual(run["state"], "done", run["message"])
        self.assertEqual(writer_calls(log)[-1]["targets"], "cover, p-why, p-rules, closing")
        r = fx.post(f"/api/decks/{body['deck_id']}/pages/{sid}/duplicate", {})
        self.assertEqual((r.status, r.error_code), (409, "not_for_infographic"))

    def test_an_interrupted_image_fix_is_not_redrawn_silently(self) -> None:
        store = open_store(self)
        from helpers import make_deck

        deck = make_deck(store, mode="pitch")
        with store.transaction():
            run = store.create_run(deck["id"], args={"mode": "pitch", "style": "visual"})
            job = store.create_job(deck["id"], run_id=run["id"], intake_id=None, kind="edit",
                                   request={"op": "revise", "source": "screen"})
        store.claim_job("w")
        store.set_run_state(run["id"], "rendering")
        store.set_run_record(run["id"], {"plan": {"deck": False, "revise": [{"key": "p-eight"}]}})
        out = store.recover_interrupted_jobs()
        self.assertEqual(out["failed"], [job["id"]])
        self.assertIn("直している途中", store.run(run["id"])["message"])
        # 絵を直さない直し（台本の差し替えだけ）は、描くところから続ける
        with store.transaction():
            run2 = store.create_run(deck["id"], args={"mode": "pitch", "style": "visual"})
            job2 = store.create_job(deck["id"], run_id=run2["id"], intake_id=None, kind="edit",
                                    request={"op": "revise", "source": "screen"})
        store.claim_job("w")
        store.set_run_state(run2["id"], "rendering")
        store.set_run_record(run2["id"], {"plan": {"deck": True, "revise": []}})
        self.assertEqual(store.recover_interrupted_jobs()["requeued"], [job2["id"]])

    def test_an_unchanged_page_keeps_its_own_asset_even_if_another_page_has_the_same_bytes(self) -> None:
        """同じバイトの絵が 2 ページにあっても（複製など）、ページの絵の資産はそのページのものを使い続ける。"""
        store = open_store(self)
        from helpers import make_deck, png_bytes

        deck = make_deck(store, mode="pitch")
        tmp = temp_dir(self)
        same, other = tmp / "same.png", tmp / "other.png"
        same.write_bytes(png_bytes(color=(1, 2, 3, 255)))
        other.write_bytes(png_bytes(color=(9, 9, 9, 255)))
        data = {"slides": [{"id": k, "type": "statement", "headline": k} for k in ("a", "b", "c")]}
        args = {"mode": "pitch", "style": "visual"}
        assets = []
        for files in ((same, same, other), (same, same, same)):
            with store.transaction():
                run = store.create_run(deck["id"], args=args)
            script = store.save_script(deck["id"], data, written_by="screen", writer_backend=None, intake_id=None,
                                       run_id=run["id"])
            store.accept_script(script["id"], screen_pages(args, data))
            store.start_rendering(run["id"], script["id"], output_pages(args, data), {})
            store.finish_run(run["id"], state="done", rc=0, message=None, failure_kind=None,
                             pages={n: {"file": str(f), "warnings": []} for n, f in enumerate(files, 1)})
            assets.append({r["key"]: r["asset_id"] for r in store.run_pages(run["id"])})
        first, second = assets
        self.assertNotEqual(first["a"], first["b"], "1 回目: 同じバイトでもページごとに資産")
        self.assertEqual((second["a"], second["b"]), (first["a"], first["b"]), "2 回目: どのページも自分の資産を使い続ける")
        self.assertNotIn(second["c"], (first["a"], first["b"], first["c"]), "絵が替わったページは新しい資産")

    def test_one_job_per_deck_at_a_time(self) -> None:
        store = open_store(self)
        from helpers import make_deck

        a, b = make_deck(store), make_deck(store)
        with store.transaction():
            jobs = []
            for deck in (a, a, b):
                run = store.create_run(deck["id"], args={"mode": "presentation", "style": "standard"})
                jobs.append(store.create_job(deck["id"], run_id=run["id"], intake_id=None, kind="edit",
                                             request={"op": "render", "source": "screen"}))
        first = store.claim_job("w1")
        second = store.claim_job("w2")
        self.assertEqual((first["id"], second["id"]), (jobs[0]["id"], jobs[2]["id"]), "同じデッキの 2 つ目は待つ")
        self.assertIsNone(store.claim_job("w3"))
        store.finish_job(first["id"], "done")
        self.assertEqual(store.claim_job("w3")["id"], jobs[1]["id"])


if __name__ == "__main__":
    unittest.main()
