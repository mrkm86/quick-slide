"""同梱の見本（docs/adr/0006-bundled-samples.md）。

- 同梱の中身: モードごとに 1 冊・作り直し忘れが無い（台本の正本と描いた台本が同じ）・作った機械のパスや字体・PPTX を配らない
- 起動のとき: 一度も入れていない見本だけを入れる（2 回目の起動で増えない・消した見本は戻さない・完全に削除しても戻さない）
- 入れたデッキは普通のデッキと同じに動く（画面のページ・発表・書き出し・ピッチの絵の控え）
- 作り直して版が変わったとき: 触っていないデッキは進め、触ったデッキはそのまま。「入れ直す」は別のデッキとして足す
- 目印の置き換え（Windows のパスでも JSON が壊れない）と、作るときの漏れの検査
"""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from helpers import ServerFixture, start_server, temp_dir

from quick_slide import contenttypes
from quick_slide import samples as S
from quick_slide.enums import DECK_MODES
from quick_slide.paths import SAMPLES_DIR
from quick_slide.render import workspace as W
from quick_slide.scriptdoc import dumps, screen_pages

# 同梱の見本の大きさの上限（リポジトリの履歴に残るので、増えすぎたら気づく）
MAX_BUNDLE_BYTES = 20 * 1024 * 1024
NOT_SHIPPED = {".pptx", ".pdf", ".ttf", ".otf", ".ttc", ".woff", ".woff2", ".md"}


def bundle() -> list[S.Sample]:
    samples, problems = S.load_samples(SAMPLES_DIR)
    assert not problems, problems
    return samples


def copy_bundle(testcase) -> Path:
    dst = temp_dir(testcase, "quick-slide-samples-") / "samples"
    shutil.copytree(SAMPLES_DIR, dst, ignore=shutil.ignore_patterns("build.py", "__pycache__"))
    return dst


def bump_version(directory: Path, sample_id: str, digit: str = "e") -> str:
    """作り直した体にする（目録の版だけを替える。中身はそのまま）。"""
    path = directory / sample_id / "built" / "manifest.json"
    m = json.loads(path.read_text(encoding="utf-8"))
    m["version"] = digit * 64
    path.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    return m["version"]


class BundleTests(unittest.TestCase):
    def test_one_built_sample_per_mode(self) -> None:
        self.assertEqual(sorted(s.mode for s in bundle()), sorted(DECK_MODES))

    def test_built_script_matches_the_source(self) -> None:
        for s in bundle():
            with self.subTest(s.id):
                script = S.verify(s)
                source = json.loads((s.folder / "script.json").read_text(encoding="utf-8"))
                self.assertEqual(dumps(source), dumps(script),
                                 f"samples/{s.id}/script.json を直したら `python3 samples/build.py {s.id}` で作り直してください")

    def test_nothing_from_the_builders_machine_is_shipped(self) -> None:
        allowed = {"{{" + t + "}}" for t in (*S.PATH_TOKENS, *S.ID_TOKENS)}
        total = 0
        for s in bundle():
            for p in sorted(s.built.rglob("*")):
                if not p.is_file():
                    continue
                with self.subTest(str(p.relative_to(SAMPLES_DIR))):
                    data = p.read_bytes()
                    total += len(data)
                    self.assertNotIn(p.suffix.lower(), NOT_SHIPPED)
                    self.assertEqual(S.leaks(data, list(S.LEAK_MARKERS)), [])
                    self.assertNotIn(b"/.quick-slide", data)
                    if p.suffix.lower() in S.TEXT_SUFFIXES:
                        import re

                        used = set(re.findall(r"\{\{[A-Z_]+\}\}", data.decode("utf-8")))
                        self.assertLessEqual(used, allowed)
        self.assertLess(total, MAX_BUNDLE_BYTES)

    def test_presentation_sample_states_the_engine_type_count(self) -> None:
        # 見本の「型の数」は画面の「使うパターン」と同じ数（エンジンが型を足したら見本も直す）
        s = next(s for s in bundle() if s.mode == "presentation")
        data = json.loads((s.folder / "script.json").read_text(encoding="utf-8"))
        big = [p for p in data["slides"] if p.get("type") == "big-number"]
        self.assertTrue(big)
        self.assertEqual(big[0]["value"], str(len(contenttypes.engine_types())),
                         "html-slide の型の数が変わりました。samples/presentation/script.json を直して作り直してください")


class TokenTests(unittest.TestCase):
    def test_fill_keeps_json_valid_for_windows_paths(self) -> None:
        values = {"HOME": "C:\\Users\\someone\\.quick-slide", "CACHE": None, "RUN_ID": "r1"}
        got = S.fill({"a": "{{HOME}}/decks", "b": ["{{CACHE}}/x"], "c": "{{RUN_ID}}", "d": 3}, values)
        self.assertEqual(got, {"a": "C:\\Users\\someone\\.quick-slide/decks", "b": [None], "c": "r1", "d": 3})
        yaml_line = 'regular: "{{HOME}}/fonts/a.ttf"'
        filled = S.fill_text(yaml_line, values, escape=True)
        self.assertEqual(json.loads(filled.split(": ", 1)[1]), "C:\\Users\\someone\\.quick-slide/fonts/a.ttf")

    def test_tokenizer_prefers_the_longest_path(self) -> None:
        convert = S.tokenizer({"RUN_DIR": ["/srv/h/decks/d1/runs/r1"], "DECK_DIR": ["/srv/h/decks/d1"], "HOME": ["/srv/h"]},
                              {"RUN_ID": "r1", "DECK_ID": "d1"})
        self.assertEqual(convert("/srv/h/decks/d1/runs/r1/slides/a.png"), "{{RUN_DIR}}/slides/a.png")
        self.assertEqual(convert("/srv/h/decks/d1/work/x"), "{{DECK_DIR}}/work/x")
        self.assertEqual(convert("/srv/h/fonts/f.ttf"), "{{HOME}}/fonts/f.ttf")

    def test_leaks_finds_machine_paths(self) -> None:
        self.assertEqual(S.leaks(b'{"x": "/Users/someone/a"}', list(S.LEAK_MARKERS)), ["/Users/"])
        self.assertEqual(S.leaks(b'{"x": "{{HOME}}/a"}', list(S.LEAK_MARKERS)), [])


class StartupTests(unittest.TestCase):
    def live_decks(self, fx: ServerFixture) -> list[dict]:
        return fx.get("/api/decks?limit=50&archived=all").json()["items"]

    def test_without_a_samples_dir_nothing_is_imported(self) -> None:
        fx = start_server(self)
        self.assertEqual(self.live_decks(fx), [])
        self.assertFalse(fx.get("/api/samples").json()["available"])
        self.assertEqual(fx.post("/api/samples/restore").status, 409)

    def test_first_start_imports_one_deck_per_sample(self) -> None:
        fx = start_server(self, samples_dir=SAMPLES_DIR)
        cards = {c["mode"]: c for c in self.live_decks(fx)}
        self.assertEqual(sorted(cards), sorted(DECK_MODES))
        for s in bundle():
            with self.subTest(s.id):
                card = cards[s.mode]
                self.assertEqual(card["title"], s.title)
                self.assertEqual((card["page_count"], card["ready_count"]), (len(s.manifest["pages"]),) * 2)
                img = fx.get(card["thumbnail_url"])
                self.assertEqual(img.status, 200)
                first = s.built / "run" / "slides" / "slide-01.png"
                links = s.manifest.get("links") or {}
                if not first.is_file():
                    first = s.built / "run" / links["slides/slide-01.png"]
                self.assertEqual(img.body, first.read_bytes())
                with fx.server.store_lock:
                    self.assertEqual([t["name"] for t in fx.store.deck_tags(card["id"])], [S.SAMPLE_TAG])
                    head = fx.store.head_script(card["id"])
                    self.assertEqual(head["written_by"], "screen")
                view = fx.get(f"/api/decks/{card['id']}").json()
                self.assertEqual([p["display"]["key"] for p in view["pages"]], ["ready"] * len(s.manifest["pages"]))
        # 置き場の目印はすべて入れた機械の値に戻っている
        for p in (fx.root.root / "decks").rglob("*"):
            if p.is_file() and p.suffix in (".json", ".html", ".yaml"):
                self.assertNotIn("{{", p.read_text(encoding="utf-8"), str(p))
        items = fx.get("/api/samples").json()["items"]
        self.assertEqual([i["state"] for i in items], ["present"] * len(items))
        again = fx.post("/api/samples/restore").json()
        self.assertEqual((again["imported"], again["advanced"], again["message"]), ([], [], "見本はすべて入っています。"))

    def test_restart_keeps_one_copy_and_deleted_samples_stay_deleted(self) -> None:
        tmp = temp_dir(self, "quick-slide-srv-")
        fx = ServerFixture(tmp, samples_dir=SAMPLES_DIR)
        try:
            pitch = next(c for c in self.live_decks(fx) if c["mode"] == "pitch")
            self.assertEqual(fx.post(f"/api/decks/{pitch['id']}/delete").status, 200)
        finally:
            fx.close()
        fx = ServerFixture(tmp, samples_dir=SAMPLES_DIR)
        self.addCleanup(fx.close)
        self.assertEqual(sorted(c["mode"] for c in self.live_decks(fx)), sorted(set(DECK_MODES) - {"pitch"}))
        states = {i["id"]: i["state"] for i in fx.get("/api/samples").json()["items"]}
        self.assertEqual(states["pitch"], "missing")
        r = fx.post("/api/samples/restore")
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual((len(r.json()["imported"]), r.json()["message"]), (1, "見本を 1 冊入れました。"))
        self.assertEqual(sorted(c["mode"] for c in self.live_decks(fx)), sorted(DECK_MODES))
        # 入れ直した 1 冊とは別に、ごみ箱の 1 冊は残っている（戻せる）
        self.assertEqual([t["id"] for t in fx.get("/api/trash").json()["items"]], [pitch["id"]])

    def test_purged_sample_is_not_imported_again(self) -> None:
        tmp = temp_dir(self, "quick-slide-srv-")
        fx = ServerFixture(tmp, samples_dir=SAMPLES_DIR)
        try:
            motion = next(c for c in self.live_decks(fx) if c["mode"] == "motion")
            fx.post(f"/api/decks/{motion['id']}/delete")
            self.assertEqual(fx.post(f"/api/decks/{motion['id']}/purge").status, 200)
            with fx.server.store_lock:
                self.assertEqual([r for r in fx.store.sample_decks("motion")], [])
                self.assertTrue(fx.store.sample_seen("motion"))
        finally:
            fx.close()
        fx = ServerFixture(tmp, samples_dir=SAMPLES_DIR)
        self.addCleanup(fx.close)
        self.assertNotIn("motion", [c["mode"] for c in self.live_decks(fx)])

    def test_new_version_advances_untouched_decks_only(self) -> None:
        directory = copy_bundle(self)
        tmp = temp_dir(self, "quick-slide-srv-")
        fx = ServerFixture(tmp, samples_dir=directory)
        try:
            decks = {c["mode"]: c for c in self.live_decks(fx)}
            # 名前を変えただけ（触ったに数えない）
            self.assertEqual(fx.post(f"/api/decks/{decks['presentation']['id']}/title", {"title": "名前を変えた"}).status, 200)
            # 台本を進めた（触った）
            with fx.server.store_lock:
                store = fx.store
                head = store.head_script(decks["pitch"]["id"])
                data = store.load_script(head)
                data["slides"][0]["title"] = "直した表紙"
                saved = store.save_script(decks["pitch"]["id"], data, written_by="screen", writer_backend=None,
                                          intake_id=None, run_id=None)
                store.accept_script(saved["id"], screen_pages({"mode": "pitch"}, data))
        finally:
            fx.close()
        new = {sid: bump_version(directory, sid) for sid in ("presentation", "pitch", "motion")}
        fx = ServerFixture(tmp, samples_dir=directory)
        self.addCleanup(fx.close)
        with fx.server.store_lock:
            rows = {r["sample_id"]: r for r in fx.store.sample_decks()}
            self.assertEqual(rows["presentation"]["version"], new["presentation"])
            self.assertEqual(rows["motion"]["version"], new["motion"])
            self.assertNotEqual(rows["pitch"]["version"], new["pitch"])
            pres = fx.store.runs_for_deck(decks["presentation"]["id"])
            self.assertEqual([r["number"] for r in pres], [2, 1])
            self.assertEqual(pres[0]["record"]["sample"]["version"], new["presentation"])
            self.assertEqual(fx.store.deck(decks["presentation"]["id"])["title"], "名前を変えた")
            self.assertEqual(len(fx.store.runs_for_deck(decks["pitch"]["id"])), 1)
        # 進めたページの絵は前の回と同じ中身なので、資産を使い回す（画面が読み直さない）
        view = fx.get(f"/api/decks/{decks['presentation']['id']}").json()
        self.assertTrue(all(p["display"]["key"] == "ready" for p in view["pages"]))
        r = fx.post("/api/samples/restore").json()
        self.assertEqual((len(r["imported"]), r["advanced"], sorted(r["kept"])), (1, [], ["motion", "presentation"]))
        self.assertEqual(len([c for c in self.live_decks(fx) if c["mode"] == "pitch"]), 2)

    def test_imported_decks_work_like_drawn_ones(self) -> None:
        fx = start_server(self, samples_dir=SAMPLES_DIR)
        decks = {c["mode"]: c for c in self.live_decks(fx)}
        # ピッチ: 絵の控えがあり（描き直すとき Codex を呼ばずに使い回せる）、記録の区画はこの機械のキャッシュの下
        pitch = decks["pitch"]["id"]
        with fx.server.store_lock:
            run = fx.store.shown_run(pitch)
            keys = [p["key"] for p in fx.store.run_pages(run["id"])]
        run_dir = fx.root.run_dir(pitch, run["id"])
        for k in keys:
            self.assertIsNotNone(W.pitch_picture(run_dir, k), k)
        cache = W.engine_record("pitch", run_dir).get("cache")
        self.assertIsInstance(cache, str)
        self.assertNotIn("{{", cache)
        # ピッチ: ページの画像（ZIP）はそのまま書き出せる
        r = fx.post(f"/api/decks/{pitch}/exports", {"format": "images"})
        self.assertIn(r.status, (200, 201), r.body)
        # モーション: HTML 1枚はそのまま書き出せる（描き直さない）
        motion = decks["motion"]["id"]
        r = fx.post(f"/api/decks/{motion}/exports", {"format": "html"})
        self.assertIn(r.status, (200, 201), r.body)
        got = fx.get(r.json()["export"]["download_url"])
        with fx.server.store_lock:
            mrun = fx.store.shown_run(motion)
        self.assertEqual(got.body, (fx.root.run_dir(motion, mrun["id"]) / "index.html").read_bytes())
        # プレゼン・モーション: 発表の画面の HTML がある
        for mode in ("presentation", "motion"):
            data = fx.get(f"/api/decks/{decks[mode]['id']}/present").json()
            self.assertTrue(data.get("html"), mode)

    def test_broken_sample_is_skipped_and_the_server_still_starts(self) -> None:
        directory = copy_bundle(self)
        s = next(x for x in S.load_samples(directory)[0] if x.mode == "motion")
        (s.built / "run" / "slides" / "slide-01.png").write_bytes(b"not a png")
        fx = start_server(self, samples_dir=directory)
        self.assertEqual(sorted(c["mode"] for c in self.live_decks(fx)), sorted(set(DECK_MODES) - {"motion"}))
        self.assertTrue(any("中身が目録と違います" in p for p in fx.server.samples_summary["problems"]))
        r = fx.post("/api/samples/restore")
        self.assertEqual(r.status, 500)
        self.assertIn("中身が目録と違います", r.json()["error"]["message"])


def cache_roots(fx: ServerFixture, deck_id: str) -> list[Path]:
    """作るときに目印にするキャッシュの根っこ（この機械のエンジンの置き場の決まりで）。"""
    from quick_slide.render.caches import engine_cache_base

    return [b for b in (engine_cache_base(fx.root, skill, deck_id) for skill in ("html-slide", "image-slide", "motion-slide"))
            if b is not None]


class PackTests(unittest.TestCase):
    def test_pack_round_trips_an_imported_sample(self) -> None:
        fx = start_server(self, samples_dir=SAMPLES_DIR)
        out = temp_dir(self, "quick-slide-pack-")
        for s in bundle():
            with self.subTest(s.id):
                deck = next(c for c in fx.get("/api/decks?limit=50").json()["items"] if c["mode"] == s.mode)
                folder = out / s.id
                folder.mkdir()
                shutil.copyfile(s.folder / "sample.json", folder / "sample.json")
                with fx.server.store_lock:
                    m = S.pack(fx.store, fx.root, deck["id"], folder, cache_roots=cache_roots(fx, deck["id"]))
                self.assertEqual(sorted({**m["files"], **m["links"]}), sorted({**s.manifest["files"], **(s.manifest.get("links") or {})}))
                self.assertEqual(m["pages"], s.manifest["pages"])
                self.assertEqual((folder / "built" / "script.json").read_bytes(), (s.built / "script.json").read_bytes())
                S.verify(S.read_sample(folder))

    def test_pack_refuses_files_with_machine_paths(self) -> None:
        fx = start_server(self, samples_dir=SAMPLES_DIR)
        s = next(x for x in bundle() if x.mode == "presentation")
        deck = next(c for c in fx.get("/api/decks?limit=50").json()["items"] if c["mode"] == "presentation")
        with fx.server.store_lock:
            run = fx.store.shown_run(deck["id"])
        rec = fx.root.run_dir(deck["id"], run["id"]) / "deck-info.json"
        data = json.loads(rec.read_text(encoding="utf-8"))
        data["font_file"] = "/Users/someone/Library/Fonts/x.ttf"
        rec.write_text(json.dumps(data), encoding="utf-8")
        folder = temp_dir(self, "quick-slide-pack-") / s.id
        folder.mkdir()
        shutil.copyfile(s.folder / "sample.json", folder / "sample.json")
        with fx.server.store_lock, self.assertRaises(S.SampleError) as cm:
            S.pack(fx.store, fx.root, deck["id"], folder, cache_roots=cache_roots(fx, deck["id"]))
        self.assertIn("deck-info.json", str(cm.exception))
        self.assertFalse((folder / "built").exists())


if __name__ == "__main__":
    unittest.main()
