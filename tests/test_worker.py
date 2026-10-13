"""P2 のワーカー: 台本係（偽物の claude / codex）→ 検査 → 描き係（偽物のエンジン）の流れ・状態の移り方・
エンジンの終了コードの写し方（1 / 2 / 4）・書き直しの回数・LLM の選び方・API キーを渡さないこと。

エンジンはここでは偽物（`tests/fakes/fake_engine.py`）。本物のエンジンを通す試験は `test_engines_e2e.py`。
"""

from __future__ import annotations

import json
import os
import shutil
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import FAKES, fake_tools, intake_payload, start_server, temp_dir, wait_until_idle, writer_calls

from quick_slide.childenv import API_KEY_VARS, child_env
from quick_slide.engines import ENGINES
from quick_slide.paths import SKILLS_DIR, DataRoot
from quick_slide.settings import Settings, WorkerSettings
from quick_slide.store import Store
from quick_slide.writer.backends import WriterFailed, choose_backend, extract_json
from quick_slide.writer.prompt import build_prompt

KEYS = {name: "sk-should-never-reach-the-child-0123456789" for name in API_KEY_VARS}


def fake_skills_dir(testcase) -> Path:
    """偽物のエンジンを本物と同じ置き方（`<スキル>/scripts/<入口>.py`・決まりの文書）で並べる。"""
    root = temp_dir(testcase, "quick-slide-fake-skills-") / "skills"
    fake = FAKES / "fake_engine.py"
    for skill, entry, kind in (("html-slide", "html_slide.py", "html"), ("image-slide", "image_slide.py", "image"),
                               ("motion-slide", "motion_slide.py", "motion")):
        (root / skill / "scripts").mkdir(parents=True)
        (root / skill / "scripts" / entry).write_text(
            "import runpy, sys\n"
            f"sys.argv = [{str(fake)!r}, {kind!r}, *sys.argv[1:]]\n"
            f"runpy.run_path({str(fake)!r}, run_name='__main__')\n", encoding="utf-8")
        (root / skill / "references").mkdir()
    (root / "illust" / "references").mkdir(parents=True)   # 挿絵の書き方（プレゼンの台本の決まりに入る。ADR 0011）
    for rel in ("html-slide/references/deck-format.md", "image-slide/references/intake.md",
                "motion-slide/references/deck-format.md", "motion-slide/references/authoring.md",
                "illust/references/authoring.md"):
        shutil.copyfile(SKILLS_DIR / rel, root / rel)
    return root


def settings(**worker) -> Settings:
    return Settings(worker=WorkerSettings(**{"parallel": 1, **worker}))


class FlowTests(unittest.TestCase):
    def start(self, *, mode: str = "ok", engine_env: dict[str, str] | None = None, cfg: Settings | None = None,
              tools_kw: dict | None = None):
        tools, log = fake_tools(self, mode=mode, skills_dir=fake_skills_dir(self))
        tools.extra_env.update({"FAKE_ENGINE_SLEEP": "0.01", "FAKE_ENGINE_LOG": str(log / "engine"), **(engine_env or {})})
        for k, v in (tools_kw or {}).items():
            setattr(tools, k, v)
        fx = start_server(self, tools=tools, settings=cfg or settings())
        return fx, log

    def make(self, fx, **attrs):
        payload = intake_payload(runtime=attrs.pop("runtime", "claude_code"), **attrs)
        r = fx.post("/api/intakes", payload)
        self.assertEqual(r.status, 201, r.body)
        body = r.json()
        run = wait_until_idle(fx, body["deck_id"])
        return body, run

    def run_states(self, fx, deck_id: str) -> list[str]:
        with fx.server.store_lock:
            evs = fx.store.events(deck_id=deck_id)
        out: list[str] = []
        for e in evs:
            state = e["payload"].get("state") if e["kind"] in ("run_updated", "run_finished") else None
            if state and (not out or out[-1] != state):
                out.append(state)
        return out

    def test_presentation_flow_reaches_the_screen(self) -> None:
        fx, log = self.start()
        body, run = self.make(fx)
        self.assertEqual((run["state"], run["rc"], run["message"]), ("done", 0, None))
        self.assertEqual(self.run_states(fx, body["deck_id"]), ["writing", "checking", "rendering", "importing", "done"])
        self.assertEqual(run["args"]["writer"], "claude", "会話の出所が Claude Code なら claude で書く")
        calls = writer_calls(log)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["has_contract"] and calls[0]["has_manuscript"] and not calls[0]["has_pitch_contract"])
        self.assertEqual(calls[0]["keys"], [])
        self.assertEqual(Path(calls[0]["cwd"]).parts[-4:], ("runs", run["id"], "writer", "round-1"))
        with fx.server.store_lock:
            script = fx.store.script(run["script_id"])
            self.assertEqual((script["version"], script["check_rc"], script["written_by"], script["writer_backend"]),
                             (1, 0, "writer", "claude"))
            self.assertTrue(fx.store.script_path(script).is_file())
            self.assertEqual(script["path"], f"decks/{body['deck_id']}/scripts/1.json")
            job = fx.store.job(body["job_id"])
        self.assertEqual(job["state"], "done")
        view = fx.get(f"/api/decks/{body['deck_id']}").json()
        self.assertEqual(len(view["pages"]), 4)
        self.assertEqual([p["position"] for p in view["pages"]], [1, 2, 3, 4])
        self.assertEqual(view["pages"][1]["title"], "会議の4割は何も決めずに終わっている")
        self.assertIn("回答240人", view["pages"][1]["body"])
        for p in view["pages"]:
            self.assertEqual(p["display"]["key"], "ready")
            self.assertEqual((p["version_id"], p["version"]), (run["id"], 1))
            self.assertEqual(p["versions"][0]["image_url"], p["image_url"])
            img = fx.get(p["image_url"])
            self.assertEqual((img.status, img.headers["content-type"]), (200, "image/png"))
            self.assertTrue(img.body.startswith(b"\x89PNG"))
        self.assertEqual(view["counts"]["ready"], 4)
        self.assertEqual(view["revision"]["id"], run["id"])
        self.assertEqual((view["live"]["working"], view["planning"]["message"]), (False, None))
        self.assertFalse(view["limit"]["stopped"])
        self.assertTrue(view["capabilities"]["reorder_pages"]["available"], "ページができたら並べ替えられる（P3）")
        prog = fx.get(f"/api/decks/{body['deck_id']}/progress").json()
        self.assertEqual(prog["pages"], view["pages"])
        self.assertEqual(prog["revision_id"], run["id"])
        card = fx.get("/api/decks/home").json()["recent"][0]
        self.assertEqual((card["page_count"], card["page_count_label"]), (4, "4枚"))
        self.assertEqual(card["thumbnail_url"], view["pages"][0]["image_url"])
        self.assertEqual(card["progress_label"], "完成 4/4")

    def test_pitch_flow_reports_each_page_as_it_is_drawn(self) -> None:
        fx, log = self.start()
        body, run = self.make(fx, runtime="codex", mode="pitch", style="standard")
        self.assertEqual(run["state"], "done")
        self.assertEqual(run["args"]["writer"], "codex", "会話の出所が Codex なら codex で書く")
        self.assertTrue(writer_calls(log)[0]["has_pitch_contract"])
        with fx.server.store_lock:
            evs = fx.store.events(deck_id=body["deck_id"], kinds=("run_updated",))
        drawn = sorted(e["payload"]["page_no"] for e in evs if e["payload"].get("page_state") == "drawn")
        self.assertEqual(drawn, [1, 2, 3, 4], "ページごとの「絵が返った」を台帳に書く")
        steps = [(e["payload"].get("progress") or {}).get("step") for e in evs]
        self.assertIn("pages", steps)
        dones = [(e["payload"]["progress"]["done"]) for e in evs if (e["payload"].get("progress") or {}).get("step") == "pages"]
        self.assertEqual(dones, sorted(dones))
        deck_call = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((log / "engine").glob("*-deck.json"))][0]
        argv = deck_call["argv"]
        self.assertEqual(deck_call["kind"], "image")
        self.assertEqual(argv[argv.index("--style") + 1], "standard")
        self.assertEqual(argv[argv.index("--format") + 1], "pptx,pdf,png,thumbs")
        # エンジンはデッキの作業フォルダに描き（P3。キャッシュの区画を回をまたいで使うため）、回のフォルダへ控える
        self.assertEqual(Path(argv[argv.index("--out") + 1]), fx.root.work_dir(body["deck_id"]))
        self.assertEqual(Path(argv[argv.index("--script") + 1]), fx.root.engine_script_path(body["deck_id"]))
        self.assertTrue((fx.root.run_dir(body["deck_id"], run["id"]) / "slides" / "slide-01.png").is_file())

    def test_infographic_is_one_page(self) -> None:
        fx, _ = self.start(engine_env={"FAKE_ENGINE_PAGES": "1"})
        body, run = self.make(fx, mode="pitch", style="infographic")
        self.assertEqual((run["state"], run["message"]), ("done", None))
        pages = fx.get(f"/api/decks/{body['deck_id']}").json()["pages"]
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["title"], "試験の資料（架空）")

    def test_check_failure_is_fed_back_and_rewritten(self) -> None:
        fx, log = self.start(mode="invalid_first")
        body, run = self.make(fx)
        self.assertEqual(run["state"], "done")
        calls = writer_calls(log)
        self.assertEqual(len(calls), 2)
        self.assertFalse(calls[0]["retry"])
        self.assertTrue(calls[1]["retry"])
        self.assertTrue(any("知らない型" in ln for ln in calls[1]["ng"]), calls[1]["ng"])
        with fx.server.store_lock:
            versions = fx.store._all("SELECT version,check_rc,round FROM scripts WHERE deck_id=? ORDER BY version",
                                     (body["deck_id"],))
        self.assertEqual([(v["version"], v["check_rc"], v["round"]) for v in versions], [(1, 2, 1), (2, 0, 2)])
        self.assertEqual(self.run_states(fx, body["deck_id"]),
                         ["writing", "checking", "writing", "checking", "rendering", "importing", "done"])

    def test_gives_up_after_the_round_limit(self) -> None:
        fx, log = self.start(mode="always_invalid", cfg=settings(max_check_rounds=2))
        body, run = self.make(fx)
        self.assertEqual((run["state"], run["failure_kind"]), ("failed", "script"))
        self.assertIn("2 回書き直しました", run["message"])
        self.assertEqual(len(writer_calls(log)), 2)
        view = fx.get(f"/api/decks/{body['deck_id']}").json()
        self.assertEqual(view["pages"], [], "検査に通らなかった台本のページは画面に出さない")
        self.assertEqual(view["planning"], {"state": "failed", "active": False, "message": run["message"]})
        self.assertFalse(view["live"]["working"])

    def test_pages_need_ids_and_keep_their_identity(self) -> None:
        fx, log = self.start(mode="no_ids_first")
        body, run = self.make(fx)
        self.assertEqual(run["state"], "done")
        calls = writer_calls(log)
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("id が無い" in ln for ln in calls[1]["ng"]), calls[1]["ng"])
        with fx.server.store_lock:
            keys = [fx.store.page_key_of(p["page_id"]) for p in fx.store.run_pages(run["id"])]
            first = fx.store.script(fx.store._scalar("SELECT id FROM scripts WHERE deck_id=? AND version=1",
                                                     (body["deck_id"],)))
        self.assertEqual(keys, ["cover", "p-why", "p-rules", "closing"])
        self.assertEqual(first["check_rc"], 2, "id の無い台本はエンジンに渡す前に止める")
        # 同じデッキで同じ id のページは同じページ（台本を書き直しても）
        with fx.server.store_lock:
            store = fx.store
            pages = [{"no": 1, "key": "p-why", "type": "statement", "title": "t", "body": ""}]
            with store.transaction():
                again = store.create_run(body["deck_id"], args=run["args"])
            script = store.save_script(body["deck_id"], {"slides": [{"id": "p-why", "type": "statement", "headline": "x"}]},
                                       written_by="writer", writer_backend="claude", intake_id=None, run_id=again["id"])
            store.start_rendering(again["id"], script["id"], pages, {})
            self.assertEqual(store.run_pages(again["id"])[0]["page_id"],
                             [p for p in store.run_pages(run["id"]) if p["page_no"] == 2][0]["page_id"])

    def test_run_keeps_a_reproducibility_record(self) -> None:
        fx, _ = self.start()
        body, run = self.make(fx, mode="presentation", style="visual", image_look="photo")
        rec = run["record"]
        self.assertEqual(rec["settings"]["writer"], "claude")
        self.assertEqual((rec["settings"]["style"], rec["settings"]["image_look"]), ("visual", "photo"))
        # ブランドから作ったテーマの YAML は回の控え（settings/theme.yaml）に残り、エンジンにはそのファイルを渡す（P4）
        self.assertEqual(Path(rec["settings"]["theme"]).parts[-2:], ("settings", "theme.yaml"), rec["settings"]["theme"])
        self.assertEqual(rec["look"]["theme_file"], rec["settings"]["theme"])
        self.assertEqual(rec["look"]["theme"], {"background": "#FFFFFF", "ink": "#333333", "primary": "#0DB4F2"})
        with fx.server.store_lock:
            script = fx.store.script(run["script_id"])
        self.assertEqual((rec["script"]["version"], rec["script"]["sha256"]), (script["version"], script["sha256"]))
        self.assertEqual(rec["engine"]["skill"], "html-slide")
        self.assertEqual(len(rec["engine"]["sha256"]), 64)
        argv = rec["argv"][0]   # 回で呼んだエンジンのコマンドの並び（P3: deck のあとに revise が続くことがある）
        self.assertIn("--style", argv)
        self.assertEqual(argv[argv.index("--style") + 1], "visual")
        self.assertEqual(argv[argv.index("--theme") + 1], rec["settings"]["theme"])
        self.assertEqual(run["engine_result"]["rc"], 0, "エンジンの終わり方も控える")

    def test_pitch_checks_the_codex_login_before_writing(self) -> None:
        fx, log = self.start(engine_env={"FAKE_ENGINE_AUTH_RC": "4"})
        body, run = self.make(fx, mode="pitch")
        self.assertEqual((run["state"], run["failure_kind"], run["rc"]), ("failed", "auth", 4))
        self.assertEqual(writer_calls(log), [], "描けないと分かっているのに台本係の枠を使わない")
        view = fx.get(f"/api/decks/{body['deck_id']}").json()
        self.assertTrue(view["limit"]["stopped"])
        self.assertIn("codex login", view["limit"]["message"])
        # プレゼンは確かめを挟まない（要るかどうかはエンジンが deck の rc=4 で言う）
        fx, _ = self.start(engine_env={"FAKE_ENGINE_AUTH_RC": "4"})
        _, run = self.make(fx, mode="presentation")
        self.assertEqual(run["state"], "done")

    def test_resent_intake_returns_the_same_deck(self) -> None:
        fx, _ = self.start()
        payload = intake_payload()
        payload["origin"]["conversation_key"] = "conversation-key-0123456789"
        first = fx.post("/api/intakes", payload).json()
        again = fx.post("/api/intakes", payload).json()
        self.assertEqual((again["deck_id"], again["job_id"], again["run_id"], again["resolution"]),
                         (first["deck_id"], first["job_id"], first["run_id"], "resent"))
        self.assertNotEqual(again["launch_id"], first["launch_id"], "開く URL は新しく出す（トークンは 1 回しか使えない）")
        other = dict(payload, manuscript=payload["manuscript"] + "\n- もう 1 行\n")
        self.assertNotEqual(fx.post("/api/intakes", other).json()["deck_id"], first["deck_id"])
        self.assertNotEqual(fx.post("/api/intakes", {**payload, "force_new_deck": True}).json()["deck_id"], first["deck_id"])
        nokey = intake_payload()
        self.assertNotEqual(fx.post("/api/intakes", nokey).json()["deck_id"], fx.post("/api/intakes", nokey).json()["deck_id"])
        # 窓（10 分）を過ぎたら新しいデッキ
        fx.clock.advance(11 * 60)
        self.assertNotEqual(fx.post("/api/intakes", payload).json()["deck_id"], first["deck_id"])

    def test_unreadable_answer_counts_as_a_round(self) -> None:
        fx, log = self.start(mode="garbage_first")
        _, run = self.make(fx)
        self.assertEqual(run["state"], "done")
        calls = writer_calls(log)
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("JSON" in ln for ln in calls[1]["ng"]))

    def test_writer_login_problems_stop_with_the_login_hint(self) -> None:
        for runtime, backend in (("claude_code", "claude"), ("codex", "codex")):
            fx, _ = self.start(mode="auth")
            body, run = self.make(fx, runtime=runtime)
            self.assertEqual((run["state"], run["failure_kind"]), ("failed", "writer_auth"), backend)
            view = fx.get(f"/api/decks/{body['deck_id']}").json()
            self.assertTrue(view["limit"]["stopped"])
            self.assertEqual(view["limit"]["cause"], "auth")
            self.assertIn("ログイン", view["limit"]["message"])
            self.assertEqual(view["planning"]["message"], None, "同じ理由を帯に 2 回出さない")
        fx, _ = self.start(mode="rate")
        _, run = self.make(fx)
        self.assertEqual(run["failure_kind"], "writer_rate")
        self.assertIn("利用枠", run["message"])

    def test_engine_rc_mapping(self) -> None:
        cases = [
            ("presentation", {"FAKE_ENGINE_RC": "1"}, "done", "partial", [1, 2, 3, 4]),
            ("pitch", {"FAKE_ENGINE_RC": "1", "FAKE_ENGINE_PAGES": "1,3"}, "done", "partial", [1, 3]),
            ("pitch", {"FAKE_ENGINE_RC": "1", "FAKE_ENGINE_PAGES": "none"}, "failed", "partial", []),
            ("presentation", {"FAKE_ENGINE_RC": "2"}, "failed", "script", []),
            ("pitch", {"FAKE_ENGINE_RC": "4"}, "failed", "auth", []),
            ("presentation", {"FAKE_ENGINE_RC": "3"}, "failed", "engine", []),
        ]
        for mode, env, state, kind, ready in cases:
            with self.subTest(mode=mode, env=env):
                fx, _ = self.start(engine_env=env)
                body, run = self.make(fx, mode=mode)
                self.assertEqual((run["state"], run["failure_kind"], run["rc"]), (state, kind, int(env["FAKE_ENGINE_RC"])))
                view = fx.get(f"/api/decks/{body['deck_id']}").json()
                got = [p["position"] for p in view["pages"] if p["display"]["key"] == "ready"]
                self.assertEqual(got, ready)
                for p in view["pages"]:
                    self.assertIn(p["display"]["key"], ("ready", "failed"), "終わった回に「生成中」を残さない")
                if kind == "auth":
                    self.assertTrue(view["limit"]["stopped"])
                    self.assertIn("codex login", view["limit"]["message"])
                if state == "done" and kind == "partial":
                    self.assertTrue(view["planning"]["message"], "一部描けなかったことを黙らない")
                if mode == "presentation" and env["FAKE_ENGINE_RC"] == "1":
                    self.assertTrue(view["pages"][0]["warnings"], "描けなかった枠の警告がそのページに付く")

    def test_api_keys_never_reach_the_writer_or_the_engine(self) -> None:
        fx, log = self.start(engine_env=dict(KEYS))
        with mock.patch.dict(os.environ, KEYS):
            _, run = self.make(fx)
        self.assertEqual(run["state"], "done", run["message"])
        self.assertEqual([c["keys"] for c in writer_calls(log)], [[]])
        env = child_env(home=Path("/tmp/h"), extra=KEYS, base={**KEYS, "PATH": "/usr/bin", "CLAUDECODE": "1"})
        self.assertFalse(set(API_KEY_VARS) & set(env))
        self.assertNotIn("CLAUDECODE", env)
        self.assertEqual((env["QUICK_SLIDE_HOME"], env["PATH"]), (str(Path("/tmp/h")), "/usr/bin"))

    def test_backend_choice(self) -> None:
        both = {"claude": "/x/claude", "codex": "/x/codex"}
        self.assertEqual(choose_backend("auto", "claude_code", both), ("claude", "origin"))
        self.assertEqual(choose_backend("auto", "codex", both), ("codex", "origin"))
        self.assertEqual(choose_backend("auto", "other", both), ("claude", "available"))
        with self.assertRaises(WriterFailed):
            choose_backend("auto", "claude_code", {"claude": None, "codex": "/x"})
        self.assertEqual(choose_backend("auto", None, {"claude": None, "codex": "/x"}), ("codex", "available"))
        self.assertEqual(choose_backend("codex", "claude_code", both), ("codex", "settings"))
        with self.assertRaises(WriterFailed) as ctx:
            choose_backend("claude", None, {"claude": None, "codex": "/x"})
        self.assertEqual(ctx.exception.kind, "missing")
        with self.assertRaises(WriterFailed):
            choose_backend("auto", None, {"claude": None, "codex": None})
        # 設定で固定すると、会話の出所より設定が勝つ
        fx, _ = self.start(cfg=Settings(writer="codex", worker=WorkerSettings(parallel=1)))
        _, run = self.make(fx, runtime="claude_code")
        self.assertEqual(run["args"]["writer"], "codex")
        # 会話の出所の CLI が無ければ、黙って別の CLI に替えず、何を入れるかを言って止まる
        fx, log = self.start(tools_kw={"claude": str(FAKES / "no-such-claude")})
        body, run = self.make(fx, runtime="claude_code")
        self.assertEqual((run["state"], run["failure_kind"]), ("failed", "writer_missing"))
        self.assertIn("Claude Code", run["message"])
        self.assertEqual(writer_calls(log), [])
        self.assertTrue(fx.get(f"/api/decks/{body['deck_id']}").json()["limit"]["stopped"])
        # 出所が分からない（画面・CLI）なら手元にあるもの（claude → codex）
        fx, _ = self.start(tools_kw={"claude": str(FAKES / "no-such-claude")})
        _, run = self.make(fx, runtime="cli")
        self.assertEqual((run["state"], run["args"]["writer"]), ("done", "codex"))

    def test_purge_waits_for_the_work_and_trash_keeps_the_pictures(self) -> None:
        fx, _ = self.start(mode="slow", tools_kw={})
        r = fx.post("/api/intakes", intake_payload()).json()
        deck_id = r["deck_id"]
        for _ in range(100):   # 仕事が走り出してから消す（待っているだけの仕事は、消したデッキなら取らずに終える）
            with fx.server.store_lock:
                if fx.store.job(r["job_id"])["state"] == "running":
                    break
            time.sleep(0.05)
        self.assertEqual(fx.post(f"/api/decks/{deck_id}/delete").status, 200)
        row = [c for c in fx.get("/api/trash").json()["items"] if c["id"] == deck_id][0]
        self.assertFalse(row["capabilities"]["purge"]["available"])
        self.assertEqual(fx.post(f"/api/decks/{deck_id}/purge").error_code, "state_conflict")
        wait_until_idle(fx, deck_id)
        self.assertEqual(fx.post(f"/api/decks/{deck_id}/restore").status, 200)
        # ごみ箱へ → 絵は退避され → 戻すと元の場所へ戻る
        fx2, _ = self.start()
        body, run = self.make(fx2)
        pages = fx2.get(f"/api/decks/{body['deck_id']}").json()["pages"]
        first = fx2.get(pages[0]["image_url"]).body
        self.assertEqual(fx2.post(f"/api/decks/{body['deck_id']}/delete").json()["moved"], 4)
        self.assertEqual(fx2.get(pages[0]["image_url"]).status, 404)
        self.assertEqual(fx2.post(f"/api/decks/{body['deck_id']}/restore").status, 200)
        self.assertEqual(fx2.get(pages[0]["image_url"]).body, first)
        self.assertEqual(fx2.post(f"/api/decks/{body['deck_id']}/delete").status, 200)
        purged = fx2.post(f"/api/decks/{body['deck_id']}/purge").json()
        self.assertEqual(purged["assets_removed"], 4)
        self.assertFalse(fx2.root.deck_dir(body["deck_id"]).exists())


class StoreLevelTests(unittest.TestCase):
    def test_jobs_left_running_by_a_dead_server_are_recovered_by_stage(self) -> None:
        root = DataRoot(temp_dir(self) / "home")
        store = Store.open(root, settings=Settings())
        writing = store.create_intake(intake_payload())
        rendering = store.create_intake(intake_payload())
        importing = store.create_intake(intake_payload())
        waiting = store.create_intake(intake_payload())
        script = {"slides": [{"id": "cover", "type": "cover", "title": "t"}]}
        for r, stage in ((writing, None), (rendering, "render"), (importing, "import")):
            self.assertEqual(store.claim_job("w")["id"], r["job_id"])
            store.set_run_state(r["run_id"], "writing", progress={"phase": "writing", "round": 1})
            if stage is None:
                continue
            sc = store.save_script(r["deck_id"], script, written_by="writer", writer_backend="claude",
                                   intake_id=r["intake_id"], run_id=r["run_id"])
            store.start_rendering(r["run_id"], sc["id"], [{"no": 1, "key": "cover", "type": "cover", "title": "t", "body": ""}], {})
            store.mark_run_page(r["run_id"], 1, "drawn")
            if stage == "import":
                store.enter_importing(r["run_id"], {"state": "done", "rc": 0, "message": None, "failure_kind": None,
                                                    "pages": {"1": {"file": None, "warnings": []}}})
        store.close()
        store = Store.open(root, settings=Settings())
        self.addCleanup(store.close)
        self.assertEqual(store.job(writing["job_id"])["state"], "failed")
        run = store.run(writing["run_id"])
        self.assertEqual((run["state"], run["failure_kind"]), ("failed", "interrupted"))
        self.assertIn("もう一度会話から頼んでください", run["message"])
        for r, stage in ((rendering, "render"), (importing, "import")):
            job = store.job(r["job_id"])
            self.assertEqual((job["state"], job["resume_from"], job["attempts"]), ("queued", stage, 1))
            self.assertIn(store.run(r["run_id"])["state"], ("rendering", "importing"))
        self.assertEqual(store.run_pages(rendering["run_id"])[0]["state"], "pending", "描き直すページは印を戻す")
        self.assertEqual(store.job(waiting["job_id"])["state"], "queued", "始まっていない仕事は残す")
        self.assertFalse(store.deck_working(writing["deck_id"]))
        # 止まり続ける仕事は、決まった回数で諦める（「働いている」のまま残さない）
        for _ in range(3):
            while store.claim_job("w") is not None:
                pass
            store.close()
            store = Store.open(root, settings=Settings())
            self.addCleanup(store.close)
        job = store.job(rendering["job_id"])
        self.assertEqual(job["state"], "failed")
        self.assertIn("何度も", store.run(rendering["run_id"])["message"])

    def test_claim_skips_deleted_decks(self) -> None:
        store = Store.open(DataRoot(temp_dir(self) / "home"), settings=Settings())
        self.addCleanup(store.close)
        r = store.create_intake(intake_payload())
        store.delete_deck_to_trash(r["deck_id"])
        self.assertIsNone(store.claim_job("w"))
        self.assertEqual(store.run(r["run_id"])["failure_kind"], "cancelled")


class WriterPartsTests(unittest.TestCase):
    def test_prompt_reads_the_engine_contract_at_runtime(self) -> None:
        deck_format = (SKILLS_DIR / "html-slide/references/deck-format.md").read_text(encoding="utf-8")
        intake_md = (SKILLS_DIR / "image-slide/references/intake.md").read_text(encoding="utf-8")
        args = {"mode": "presentation", "style": "visual", "images": True, "image_look": "photo", "page_count": 7,
                "purpose": "試行の提案", "audience": "部長会", "style_note": "落ち着いた調子"}
        text = build_prompt(ENGINES["presentation"], args, manuscript="原稿の本文", title_hint="仮の題", skills_dir=SKILLS_DIR)
        self.assertIn(deck_format.strip()[:2000], text)
        self.assertNotIn(intake_md.strip()[:500], text)
        for needle in ("JSON オブジェクト 1 つだけ", "7 枚", "試行の提案", "部長会", "写真風", "落ち着いた調子", "仮の題",
                       "image: {\"prompt\"", "原稿の本文"):
            self.assertIn(needle, text)
        pitch = build_prompt(ENGINES["pitch"], {**args, "mode": "pitch", "images": False}, manuscript="m", title_hint=None,
                             skills_dir=SKILLS_DIR, previous={"slides": []}, problems=["NG: 1枚目: 知らない型"])
        self.assertIn(intake_md.strip()[:500], pitch)
        self.assertIn("生成の画像の枠（image の prompt）を使わない", pitch)
        self.assertIn("- NG: 1枚目: 知らない型", pitch)
        self.assertIn("# 前の台本は検査で止まりました", pitch)

    def test_extract_json(self) -> None:
        self.assertEqual(extract_json('説明\n```json\n{"slides": []}\n```'), {"slides": []})
        for bad in ("", "台本です", '{"a": 1} {"b": 2}', '{"a": 1, "a": 2}', "[1, 2]", '{"a": '):
            with self.assertRaises(WriterFailed, msg=bad):
                extract_json(bad)


if __name__ == "__main__":
    unittest.main()
