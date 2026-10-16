"""デザインテンプレートの素材準備と台本（PR 6a: docs/design-template-generation.md §3）。

本物のモデル・エンジン・子プロセスは使わない。準備係 LLM・台本係（`pipeline.ask`）・エンジンの検査（`pipeline.check_script`）は
偽物を差し、**素材のために子プロセスを一度も起こさない**ことを `subprocess.Popen` の差し替えで確かめる。
"""

from __future__ import annotations

import copy
import json
import os
import struct
import sys
import threading
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from helpers import fake_tools, intake_payload, start_server, temp_dir, wait_until_idle
from test_template_intake import Base, doc

from quick_slide.engines import engine_for
from quick_slide.paths import SKILLS_DIR
from quick_slide.preparation import llm as LLM
from quick_slide.preparation.assets import verify_assets
from quick_slide.preparation.catalog import Catalog, FolderProvider
from quick_slide.preparation.engine_input import materialize_engine_input
from quick_slide.preparation.errors import EngineInputUnsupported, PreparationStopped
from quick_slide.preparation.files import read_json, sha256_file
from quick_slide.preparation.policy import Policy
from quick_slide.preparation.reading import Reader
from quick_slide.preparation.references import PREP_KEY, REF_KEY, RefContext, check_references, stamp, validate_script
from quick_slide.preparation.service import load_template_run, prepare_run
from quick_slide.render.runner import CheckResult
from quick_slide.scriptdoc import dumps
from quick_slide.settings import Settings
from quick_slide.worker import pipeline
from quick_slide.writer import backends
from quick_slide.writer.backends import WriterFailed
from quick_slide.writer.patch import apply_revision, insert_after, parse_insert, parse_revision


def png(width: int = 4, height: int = 4, *, header_only: bool = False, pad: int = 0) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"" if header_only else zlib.compress(b"".join(b"\x00" + b"\x10\x20\x30" * width for _ in range(height)))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", raw) + chunk(b"IEND", b"") + b"\0" * pad


def jpeg(width: int, height: int) -> bytes:
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0" + b"\0" * 9 + sof + b"\xff\xd9"


class FakeLlm:
    """渡された指示文を全部記録し、決めた答えを順に返す（関数なら中で作る・WriterFailed なら投げる）。"""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str):
        self.prompts.append(prompt)
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        return a(prompt) if callable(a) else copy.deepcopy(a)


GOOD = {"references": [{"id": "ref-1", "roles": ["character", "image"], "description": "案内役。説明するポーズ"},
                       {"id": "ref-2", "roles": ["character"], "description": "案内役。考えるポーズ"}],
        "notes": ["説明書きどおり、人物は character で使う"]}


class Harness(Base):
    def setUp(self) -> None:
        super().setUp()
        self.ctx = SimpleNamespace(store=self.store, root=self.store.root, stop=threading.Event(),
                                   call=lambda fn, *a, **k: fn(*a, **k))
        self.tmp = temp_dir(self)
        self.images = self.tmp / "images"
        self.images.mkdir()
        for name, size in (("a.png", 4), ("b.png", 5), ("c.png", 6)):
            (self.images / name).write_bytes(png(size, size))
        (self.images / "README.md").write_text("# 使い方\na.png は案内役。説明するポーズ。\n", encoding="utf-8")
        (self.images / "README.txt").write_text("TXT-GUIDE-MUST-NOT-BE-READ", encoding="utf-8")
        self.spawned: list = []
        popen = mock.patch("subprocess.Popen", side_effect=lambda *a, **k: self.spawned.append(a) or (_ for _ in ()).throw(
            AssertionError("素材のために子プロセスを起こした")))
        group = mock.patch("quick_slide.oscompat.popen_group", side_effect=lambda *a, **k: self.spawned.append(a) or (
            _ for _ in ()).throw(AssertionError("素材のために子プロセスを起こした")))
        popen.start(), group.start()
        self.addCleanup(popen.stop)
        self.addCleanup(group.stop)

    def resources(self, *rows) -> list[dict]:
        return [{"id": f"res-{i}", "name": n, "kind": k, "path": str(p), "usage": u} for i, (n, k, p, u) in enumerate(rows, 1)]

    def new_run(self, resources=None, mode: str = "pitch", name: str = "素材つき"):
        self.seq = getattr(self, "seq", 0) + 1
        name = f"{name}-{self.seq}"
        d = doc(name, modes=("pitch", "presentation"))
        d["resources"] = resources if resources is not None else self.resources(("画像集", "directory", self.images, "案内役を使う"))
        t = self.store.create_design_template(self.bid, d)
        made = self.intake(design_template=name, mode=mode)
        run = self.store.run(made["run_id"])
        pipeline.settle_look(self.ctx, run, previous_style=None)
        return run

    def another_run(self, deck_id: str, mode: str = "pitch"):
        """同じデッキの次の回（再試行）。"""
        with self.store.transaction():
            made = self.store.create_run(deck_id, args=self.store.deck_run_args(deck_id))
        run = self.store.run(made["id"])
        pipeline.settle_look(self.ctx, run, previous_style=None)
        return run

    def prepare(self, run, llm=None, **kw):
        return prepare_run(self.ctx, run, llm=llm if llm is not None else FakeLlm(GOOD), **kw)

    def script(self, run, slides, **extra) -> tuple[dict, dict]:
        data = {"title": "試験", "slides": slides, **extra}
        row = self.store.save_script(run["deck_id"], data, written_by="writer", writer_backend="claude", intake_id=None,
                                     run_id=run["id"])
        return row, data

    def tearDown(self) -> None:
        self.assertEqual(self.spawned, [], "素材のために子プロセスを一度も起こさない")


def page(pid: str, refs=None, **kw) -> dict:
    p = {"id": pid, "type": "cover", "headline": f"見出し {pid}", **kw}
    if refs is not None:
        p[REF_KEY] = refs
    return p


CHAR = {"id": "ref-1", "role": "character", "name": "案内役", "pose": "説明する", "reason": "説明書きの用途に合う"}


class ReadingTests(Harness):
    def test_guide_precedence_for_a_normal_folder_is_readme_md_then_txt(self) -> None:
        llm = FakeLlm(GOOD)
        tr = self.prepare(self.new_run(), llm)
        self.assertEqual(len(llm.prompts), 1)
        self.assertIn("a.png は案内役", llm.prompts[0])
        self.assertNotIn("TXT-GUIDE-MUST-NOT-BE-READ", llm.prompts[0])
        self.assertEqual(tr.intake["resources"][0]["guide"]["name"], "README.md")
        (self.images / "README.md").unlink()
        tr2 = self.prepare(self.another_run(tr.run["deck_id"]), FakeLlm(GOOD))
        self.assertEqual(tr2.intake["resources"][0]["guide"]["name"], "README.txt")

    def test_the_preparer_never_sees_paths_or_attachments_and_gets_original_text(self) -> None:
        llm = FakeLlm(GOOD)
        self.prepare(self.new_run(), llm)
        p = llm.prompts[0]
        self.assertNotIn(str(self.tmp), p, "登録した実パスは準備係へ渡さない（置換せずに検査する）")
        self.assertNotIn(self.tmp.name, p)
        self.assertIn("画像集", p, "名前・種類・使い方の対応表は渡す")
        self.assertIn("案内役を使う", p)
        self.assertIn("ref-1 | a.png", p)
        self.assertIn("## 1. 結論", p, "構成の原文")
        self.assertIn("図は少なめにする", p, "追加指示の原文")

    def test_skill_folders_are_refused_with_a_reason_and_nothing_runs(self) -> None:
        for marker in ("SKILL.md", "quick-slide-read.json"):
            skill = self.tmp / ("skill-" + marker)
            skill.mkdir()
            (skill / marker).write_text("{}" if marker.endswith("json") else "# skill", encoding="utf-8")
            llm = FakeLlm(GOOD)
            run = self.new_run(self.resources(("スキル", "directory", skill, "")), name="スキル" + marker)
            with self.assertRaises(PreparationStopped) as cm:
                self.prepare(run, llm)
            self.assertEqual(cm.exception.code, "skill_unsupported")
            self.assertIn("スキルの読み取りはまだ使えない", cm.exception.message)
            self.assertEqual(llm.prompts, [])
        # README.md・README.txt だけのフォルダは、通常のフォルダ
        self.prepare(self.new_run(name="普通"), FakeLlm(GOOD))

    def test_a_missing_registered_path_stops_even_if_others_exist(self) -> None:
        rows = self.resources(("画像集", "directory", self.images, ""), ("消えた", "directory", self.tmp / "gone", "任意"),
                              ("消えた file", "file", self.tmp / "gone.md", ""))
        llm = FakeLlm(GOOD)
        with self.assertRaises(PreparationStopped) as cm:
            self.prepare(self.new_run(rows), llm)
        self.assertEqual(cm.exception.code, "resource_missing")
        self.assertIn("「消えた」", cm.exception.message)
        self.assertIn("「消えた file」", cm.exception.message)
        self.assertEqual(llm.prompts, [])

    def test_legacy_attachments_are_not_read_yet(self) -> None:
        d = doc("添付", modes=("pitch",))
        d["attachments"] = [{"id": "m1", "name": "旧添付", "file": "a.md", "url": "/x", "image": False, "usage": "", "sha256": "a" * 64}]
        self.store.create_design_template(self.bid, d)
        run = self.store.run(self.intake(design_template="添付", mode="pitch")["run_id"])
        pipeline.settle_look(self.ctx, run, previous_style=None)
        with self.assertRaises(PreparationStopped) as cm:
            self.prepare(run)
        self.assertEqual(cm.exception.code, "attachment_unsupported")

    def test_limits_are_exact_ok_and_over_stops(self) -> None:
        def run_with(policy, files: dict[str, bytes] | None = None, name="limit"):
            folder = self.tmp / name
            folder.mkdir(exist_ok=True)
            for n, b in (files or {}).items():
                (folder / n).write_bytes(b)
            return self.new_run(self.resources(("L", "directory", folder, "")), name=name), policy

        small = Policy(guide_bytes=100, text_total_bytes=150, listing_items=3, image_bytes=2000, image_pixels=100)
        for size, ok in ((100, True), (101, False)):
            run, _ = run_with(small, {"README.md": b"x" * size}, name=f"guide{size}")
            try:
                self.prepare(run, FakeLlm({"references": []}), reader=Reader(small))
                self.assertTrue(ok)
            except PreparationStopped as exc:
                self.assertFalse(ok)
                self.assertEqual(exc.code, "limit_exceeded")
        for n, ok in ((3, True), (4, False)):
            run, _ = run_with(small, {f"f{i}.txt": b"x" for i in range(n)}, name=f"list{n}")
            try:
                self.prepare(run, FakeLlm({"references": []}), reader=Reader(small))
                self.assertTrue(ok)
            except PreparationStopped as exc:
                self.assertFalse(ok)
                self.assertEqual(exc.details["what"], "listing")
        # テキストの合計: 説明書き 100 + 読むファイル 50 = 150 ちょうどは通り、1 つ多いと止まる
        for extra, ok in ((50, True), (51, False)):
            folder = self.tmp / f"total{extra}"
            folder.mkdir()
            (folder / "README.md").write_bytes(b"x" * 100)
            f = self.tmp / f"file{extra}.md"
            f.write_bytes(b"y" * extra)
            run = self.new_run(self.resources(("F", "directory", folder, ""), ("T", "file", f, "")), name=f"total{extra}")
            try:
                self.prepare(run, FakeLlm({"references": []}), reader=Reader(small))
                self.assertTrue(ok)
            except PreparationStopped as exc:
                self.assertFalse(ok)
                self.assertEqual(exc.details["what"], "text_total")

    def test_image_size_and_pixel_limits_exclude_folder_images_and_stop_a_registered_file(self) -> None:
        small = Policy(image_bytes=len(png(4, 4)), image_pixels=16)
        folder = self.tmp / "imgs"
        folder.mkdir()
        (folder / "ok.png").write_bytes(png(4, 4))                         # 画素 16 ちょうど・大きさちょうど
        (folder / "wide.png").write_bytes(png(5, 4, header_only=True))     # 画素 20（ヘッダだけで判定。復号しない）
        (folder / "heavy.png").write_bytes(png(4, 4, pad=1))               # 大きさ 1 バイト超過
        (folder / "broken.png").write_bytes(b"not a png")
        (folder / "photo.jpg").write_bytes(jpeg(4, 4).ljust(len(png(4, 4)), b"\0"))
        tr = self.prepare(self.new_run(self.resources(("I", "directory", folder, "")), name="imgs"),
                          FakeLlm({"references": []}), reader=Reader(small))
        res = tr.intake["resources"][0]
        self.assertEqual(sorted(i["rel"] for i in res["items"]), ["ok.png", "photo.jpg"])
        self.assertEqual(sorted(e["rel"] for e in res["excluded"]), ["broken.png", "heavy.png", "wide.png"])
        big = self.tmp / "big.png"
        big.write_bytes(png(5, 4, header_only=True))
        with self.assertRaises(PreparationStopped) as cm:
            self.prepare(self.new_run(self.resources(("B", "file", big, "")), name="big"), FakeLlm(GOOD), reader=Reader(small))
        self.assertEqual(cm.exception.code, "limit_exceeded")

    def test_non_utf8_json_yaml_and_unsupported_formats_stop(self) -> None:
        def stop(name: str, data: bytes, code: str) -> None:
            f = self.tmp / name
            f.write_bytes(data)
            with self.assertRaises(PreparationStopped) as cm:
                self.prepare(self.new_run(self.resources(("F", "file", f, "")), name=name), FakeLlm(GOOD))
            self.assertEqual(cm.exception.code, code, name)

        stop("sjis.md", "日本語".encode("cp932"), "not_utf8")
        stop("dup.json", b'{"a": 1, "a": 2}', "bad_json")
        stop("nan.json", b'{"a": NaN}', "bad_json")
        stop("bad.yaml", b"a: [1, 2", "bad_yaml")
        stop("doc.pdf", b"%PDF-1.4", "unsupported_format")
        ok = self.tmp / "style.yaml"
        ok.write_text("tone: 落ち着き\n", encoding="utf-8")
        llm = FakeLlm({"references": []})
        self.prepare(self.new_run(self.resources(("Y", "file", ok, "")), name="yaml"), llm)
        self.assertIn("tone: 落ち着き", llm.prompts[0])

    def test_a_change_while_reading_is_detected(self) -> None:
        def touch(path: Path) -> None:
            if path.name == "README.md":
                path.write_text("# 使い方（読んでいる間に書き換えた）\n" * 3, encoding="utf-8")

        with self.assertRaises(PreparationStopped) as cm:
            self.prepare(self.new_run(), reader=Reader(on_read=touch))
        self.assertEqual(cm.exception.code, "changed_during_read")


class LlmTests(Harness):
    def test_malformed_output_is_repaired_twice_then_fails_with_a_reason(self) -> None:
        bad = [{"references": [{"id": "ref-99", "roles": ["character"], "description": "x"}]},
               {"references": [{"id": "ref-1", "roles": ["character"], "description": "x", "path": "/etc/passwd"}]},
               WriterFailed("format", "JSON を読めませんでした")]
        llm = FakeLlm(*bad)
        run = self.new_run()
        with self.assertRaises(PreparationStopped) as cm:
            self.prepare(run, llm)
        self.assertEqual(cm.exception.code, "preparer_malformed")
        self.assertEqual(len(llm.prompts), 3, "初回 + 修正 2 回")
        self.assertNotIn("前の答えは受け付けられませんでした", llm.prompts[0])
        self.assertIn("ref-99", llm.prompts[1])
        self.assertIn("パス", llm.prompts[2])
        self.assertNotIn("ref-99", llm.prompts[2], "各回に返すのは検証エラーだけ（前の答えの全文は返さない）")
        gen = self.store.run(run["id"])["record"]["preparation"]["generations"]["1"]
        self.assertEqual((gen["state"], gen["stop_reason"]["code"]), ("failed", "preparer_malformed"))

    def test_a_repair_that_succeeds_is_accepted(self) -> None:
        llm = FakeLlm({"references": [{"id": "ref-9", "roles": ["character"], "description": "x"}]}, GOOD)
        tr = self.prepare(self.new_run(), llm)
        self.assertEqual(len(llm.prompts), 2)
        self.assertEqual([r.id for r in tr.catalog.references], ["ref-1", "ref-2"])

    def test_the_preparer_cannot_widen_roles_and_blocked_stops(self) -> None:
        for answer in ({"references": [{"id": "ref-1", "roles": ["admin"], "description": "x"}]},
                       {"references": [], "command": "ls"},
                       {"references": [{"id": "ref-1", "roles": ["character"], "description": "x"}] * 2}):
            with self.assertRaises(PreparationStopped):
                self.prepare(self.new_run(name="n" + str(len(str(answer)))), FakeLlm(answer))
        with self.assertRaises(PreparationStopped) as cm:
            self.prepare(self.new_run(name="blk"), FakeLlm({"references": [], "blocked": [{"reason": "案内役が見つからない"}]}))
        self.assertEqual(cm.exception.code, "blocked")
        self.assertIn("案内役が見つからない", cm.exception.message)

    def test_a_role_that_cannot_be_converted_stops_instead_of_being_ignored(self) -> None:
        for roles in (["layout"], ["character", "common"]):
            answer = {"references": [{"id": "ref-1", "roles": roles, "description": "必須の素材"}]}
            run = self.new_run()
            with self.assertRaises(PreparationStopped) as cm:
                self.prepare(run, FakeLlm(answer))
            self.assertEqual(cm.exception.code, "unsupported_role", roles)
        with self.assertRaises(PreparationStopped) as cm:
            self.prepare(self.new_run(mode="presentation"), FakeLlm(GOOD))   # HTML には素材を置く変換がまだ無い
        self.assertEqual(cm.exception.code, "unsupported_role")
        self.prepare(self.new_run(mode="presentation"), FakeLlm({"references": []}))   # 素材を求めなければ通る

    def test_ids_are_stable_for_the_same_input_and_issued_per_generation(self) -> None:
        a = self.prepare(self.new_run(), FakeLlm(GOOD))
        b = self.prepare(self.another_run(a.run["deck_id"]), FakeLlm(GOOD))
        self.assertEqual(a.catalog.issued, b.catalog.issued)
        self.assertEqual([i["id"] for i in a.catalog.issued], ["ref-1", "ref-2", "ref-3"])

    def test_no_resources_means_no_llm_call(self) -> None:
        llm = FakeLlm(GOOD)
        tr = self.prepare(self.new_run([], name="empty"), llm)
        self.assertEqual((llm.prompts, tr.catalog.references), ([], []))

    def test_a_fake_catalog_provider_is_enough_for_resolution(self) -> None:
        tr = self.prepare(self.new_run(), FakeLlm(GOOD))
        src = self.tmp / "fake-provider"
        src.mkdir()
        (src / "x.png").write_bytes(png(4, 4))
        item = {"item_id": "f-fake", "rel": "x.png", "description": "x", "media_type": "image/png", "size": (src / "x.png").stat().st_size,
                "width": 4, "height": 4}
        tr.providers = lambda: {"res-1": FolderProvider("res-1", src, [item])}
        cat = Catalog.from_json({**tr.catalog.to_json(), "references": [dict(tr.catalog.to_json()["references"][0], item_id="f-fake")]})
        tr._set_catalog(cat)
        row, data = self.script(tr.run, [page("p-1", [CHAR])], **{})
        tr.resolve(row, tr.stamped(data))
        self.assertEqual(sha256_file(tr.dir / tr.assets["ref-1"]["files"][0]["path"]), tr.assets["ref-1"]["files"][0]["sha256"])


class ReferenceTests(Harness):
    def ctx_for(self, mode: str = "pitch") -> RefContext:
        tr = self.prepare(self.new_run(mode=mode, name="ctx" + mode), FakeLlm(GOOD))
        return RefContext.of(tr.catalog)

    def problems(self, ref_entry, ctx=None, page_extra=None) -> list[str]:
        ctx = ctx or self.ctx_for()
        return check_references({"id": "p", "type": "cover", **(page_extra or {}), REF_KEY: ref_entry}, "p", ctx)

    def test_valid_and_each_violation(self) -> None:
        self.assertEqual(self.problems([CHAR]), [])
        self.assertEqual(self.problems([]), [])
        cases = {
            "未知の id": [{**CHAR, "id": "ref-77"}],
            "候補外の id": [{**CHAR, "id": "ref-3"}],
            "role 逸脱": [{**CHAR, "id": "ref-2", "role": "image"}],
            "未対応の role": [{**CHAR, "role": "layout"}],
            "パス": [{**CHAR, "image": "/etc/passwd"}],
            "name 無し": [{k: v for k, v in CHAR.items() if k != "name"}],
            "name の型": [{**CHAR, "name": 3}],
            "pose を image に": [{**CHAR, "role": "image", "pose": "x"}],
            "reason 無し": [{k: v for k, v in CHAR.items() if k != "reason"}],
            "slot（ピッチ）": [{**CHAR, "slot": "image"}],
            "重複": [CHAR, CHAR],
            "配列でない": {"id": "ref-1"},
        }
        for label, entry in cases.items():
            self.assertTrue(self.problems(entry), label)
        self.assertTrue(self.problems([CHAR], page_extra={"headline": "ref-1 を描く"}), "本文に参照 ID")
        self.assertTrue(self.problems([CHAR] * 6), "1 ページの上限")

    def test_slot_is_checked_in_html_after_the_role(self) -> None:
        from quick_slide.preparation.references import RefInfo

        ctx = RefContext("p", 1, "s", "presentation", {"ref-1": RefInfo("ref-1", ("image",), "r")}, frozenset({"ref-1"}))
        # HTML の役割は 6a ではまだ使えない（枠の検査より前に断る）
        self.assertTrue(self.problems([{"id": "ref-1", "role": "image", "slot": "image", "reason": "r"}], ctx,
                                      {"image": {"prompt": "x"}}))

    def test_references_are_refused_when_there_is_no_template(self) -> None:
        self.assertTrue(validate_script({"slides": [page("a", [CHAR])]}, None))
        self.assertEqual(validate_script({"slides": [page("a")]}, None), [])
        problems = parse_insert({"slides": [page("n1", [CHAR])]}, script={"slides": [page("a")]}, attachments=[], mode="pitch")[2]
        self.assertTrue(any("_references" in p for p in problems))

    def test_llm_written_characters_and_paths_are_still_rejected(self) -> None:
        ctx = self.ctx_for()
        script = {"slides": [page("a")]}
        answer = {"slides": [page("n", [CHAR], _characters=[{"name": "x", "image": "/x.png", "pose": ""}])]}
        _, _, problems = parse_insert(answer, script=script, attachments=[], mode="pitch", refs=ctx)
        self.assertTrue(any("_characters" in p for p in problems))
        answer = {"slides": [page("n", [CHAR], image={"path": "/etc/hosts"})]}
        _, _, problems = parse_insert(answer, script=script, attachments=[], mode="pitch", refs=ctx)
        self.assertTrue(any("path" in p for p in problems))

    def test_script_stamp_and_generation_mismatch(self) -> None:
        ctx = self.ctx_for()
        data = stamp({"slides": [page("a", [CHAR])]}, ctx)
        self.assertEqual(data[PREP_KEY]["catalog_sha256"], ctx.catalog_sha256)
        self.assertEqual(validate_script(data, ctx), [])
        data[PREP_KEY]["catalog_sha256"] = "0" * 64
        self.assertTrue(any("世代違い" in p for p in validate_script(data, ctx)))
        self.assertNotIn(PREP_KEY, stamp({"slides": [page("a")]}, ctx), "参照を使わない台本には印を付けない")

    def revision(self, ctx, slides_answer, **kw):
        script = {"title": "t", "slides": [page("a", [CHAR], takeaway="元"), page("b", [{**CHAR, "id": "ref-2"}]), page("c")]}
        answer = {"pages": [{"id": "a", "route": "script", "slides": slides_answer}], "summary": "s"}
        patch = parse_revision(answer, script=script, targets=["a"], pictures={}, attachments=[], mode="pitch", refs=ctx, **kw)
        return script, patch

    def test_omitted_empty_and_split_semantics(self) -> None:
        ctx = self.ctx_for()
        # 省略: 元の参照を引き継ぐ
        script, patch = self.revision(ctx, [page("a", takeaway="新")])
        self.assertEqual(patch.problems, [])
        new = apply_revision(script, patch)
        self.assertEqual(new["slides"][0][REF_KEY], [CHAR])
        self.assertEqual(new["slides"][0]["takeaway"], "新")
        # 空配列: 明示的な削除
        script, patch = self.revision(ctx, [page("a", [])])
        self.assertEqual(apply_revision(script, patch)["slides"][0][REF_KEY], [])
        # 分割: 全部に明示（省略したページは断る。空なら通る。元の絵を広げない）
        _, patch = self.revision(ctx, [page("a"), page("a2")])
        self.assertTrue(any("分けたページ" in p for p in patch.problems))
        script, patch = self.revision(ctx, [page("a"), page("a2", [])])
        self.assertEqual(patch.problems, [])
        new = apply_revision(script, patch)
        self.assertEqual([s.get(REF_KEY) for s in new["slides"][:3]], [[CHAR], [], [{**CHAR, "id": "ref-2"}]])
        # 直さないページ（b・c）は 1 文字も変わらない
        self.assertEqual(dumps(new["slides"][-2]), dumps(script["slides"][1]))
        # route が keep・image のとき pages の直下に参照を書かせない
        answer = {"pages": [{"id": "a", "route": "keep", REF_KEY: []}]}
        self.assertTrue(parse_revision(answer, script=script, targets=["a"], pictures={}, attachments=[], mode="pitch",
                                       refs=ctx).problems)

    def test_revision_rejects_unknown_ids_and_a_page_outside_the_targets(self) -> None:
        ctx = self.ctx_for()
        _, patch = self.revision(ctx, [page("a", [{**CHAR, "id": "ref-9"}])])
        self.assertTrue(any("ref-9" in p for p in patch.problems))
        script = {"slides": [page("a"), page("b")]}
        patch = parse_revision({"pages": [{"id": "b", "route": "script", "slides": [page("b", [CHAR])]}]}, script=script,
                               targets=["a"], pictures={}, attachments=[], mode="pitch", refs=ctx)
        self.assertTrue(patch.problems)

    def test_insert_keeps_the_other_pages_byte_for_byte(self) -> None:
        ctx = self.ctx_for()
        script = {"title": "t", "slides": [page("a", [CHAR]), page("b")]}
        pages, _, problems = parse_insert({"slides": [page("n1", [{**CHAR, "id": "ref-2"}]), page("n2")]}, script=script,
                                          attachments=[], mode="pitch", refs=ctx)
        self.assertEqual(problems, [])
        new = insert_after(script, "a", pages)
        self.assertEqual([s["id"] for s in new["slides"]], ["a", "n1", "n2", "b"])
        self.assertEqual(dumps(new["slides"][0]), dumps(script["slides"][0]))
        self.assertEqual(dumps(new["slides"][3]), dumps(script["slides"][1]))
        _, _, problems = parse_insert({"slides": [page("n1", [{**CHAR, "id": "ref-8"}])]}, script=script, attachments=[],
                                      mode="pitch", refs=ctx)
        self.assertTrue(problems)


class ResolveTests(Harness):
    def finished(self):
        tr = self.prepare(self.new_run(), FakeLlm(GOOD))
        data = tr.stamped({"title": "t", "slides": [page("p-1", [CHAR]), page("p-2", [{**CHAR, "id": "ref-2", "name": "案内役"}]),
                                                    page("p-3")]})
        row = self.store.save_script(tr.run["deck_id"], data, written_by="writer", writer_backend="claude", intake_id=None,
                                     run_id=tr.run["id"])
        return tr, row, data

    def test_selected_files_are_copied_by_content_hash_and_bound_to_pages(self) -> None:
        tr, row, data = self.finished()
        tr.resolve(row, data)
        a = self.images / "a.png"
        import hashlib

        sha = hashlib.sha256(a.read_bytes()).hexdigest()
        f = tr.assets["ref-1"]["files"][0]
        self.assertEqual((f["sha256"], f["path"]), (sha, f"assets/{sha}.png"))
        self.assertEqual((tr.dir / f["path"]).read_bytes(), a.read_bytes())
        self.assertFalse((tr.dir / "assets" / "c.png").exists())
        self.assertEqual(sorted(p.name for p in (tr.dir / "assets").iterdir()), sorted(
            [f"{sha}.png", hashlib.sha256((self.images / "b.png").read_bytes()).hexdigest() + ".png"]), "選んだ 2 枚だけ")
        bindings = read_json(tr.dir / "bindings.json")
        self.assertEqual([(b["page_id"], b["ref_id"], b["role"], b["name"]) for b in bindings["bindings"]],
                         [("p-1", "ref-1", "character", "案内役"), ("p-2", "ref-2", "character", "案内役")])
        self.assertEqual(bindings["script_sha256"], row["sha256"])
        tr.finish(row)
        manifest = read_json(tr.dir / "manifest.json")
        self.assertFalse(manifest["render_ready"])
        self.assertEqual(manifest["script"]["sha256"], row["sha256"])

    def test_the_selected_total_budget_and_unlisted_changes_stop(self) -> None:
        run = self.new_run()
        tr = self.prepare(run, FakeLlm(GOOD), reader=Reader(Policy(selected_total_bytes=len(png(4, 4)) + 1)))
        row, data = self.script(run, [page("p-1", [CHAR]), page("p-2", [{**CHAR, "id": "ref-2"}])])
        with self.assertRaises(PreparationStopped) as cm:
            tr.resolve(row, tr.stamped(data))
        self.assertEqual(cm.exception.details["what"], "selected_total")
        # 一覧に出したあとで別の画像に差し替わった
        run2 = self.new_run(name="swap")
        tr2 = self.prepare(run2, FakeLlm(GOOD))
        (self.images / "a.png").write_bytes(png(9, 9))
        row, data = self.script(run2, [page("p-1", [CHAR])])
        with self.assertRaises(PreparationStopped) as cm:
            tr2.resolve(row, tr2.stamped(data))
        self.assertEqual(cm.exception.code, "changed_since_listing")

    def test_a_change_during_the_copy_is_detected(self) -> None:
        run = self.new_run()
        tr = self.prepare(run, FakeLlm(GOOD))
        row, data = self.script(run, [page("p-1", [CHAR])])

        def touch(path: Path) -> None:
            path.write_bytes(png(4, 4) + b"\0")

        tr.reader = Reader(on_read=touch)
        with self.assertRaises(PreparationStopped) as cm:
            tr.resolve(row, tr.stamped(data))
        self.assertEqual(cm.exception.code, "changed_during_read")
        self.assertEqual(list((tr.dir / "assets").glob("*.png")) if (tr.dir / "assets").exists() else [], [])

    def test_engine_view_strips_references_and_generates_characters(self) -> None:
        tr, row, data = self.finished()
        tr.resolve(row, data)
        view = tr.convert(data)
        self.assertNotIn(PREP_KEY, view)
        self.assertTrue(all(REF_KEY not in s for s in view["slides"]))
        chars = view["slides"][0]["_characters"]
        self.assertEqual([(c["name"], c["pose"]) for c in chars], [("案内役", "説明する")])
        self.assertTrue(Path(chars[0]["image"]).is_absolute() and Path(chars[0]["image"]).is_file())
        self.assertNotIn("_characters", view["slides"][2])
        self.assertIn(REF_KEY, data["slides"][0], "元の台本は変えない")
        image = materialize_engine_input({"slides": [page("x", [{"id": "ref-1", "role": "image", "reason": "r"}])]}, mode="pitch",
                                         gen_dir=tr.dir, assets=tr.assets)
        self.assertTrue(image["slides"][0]["image"]["path"].endswith(".png"))
        with self.assertRaises(EngineInputUnsupported):
            materialize_engine_input(data, mode="presentation", gen_dir=tr.dir, assets=tr.assets)
        with self.assertRaises(EngineInputUnsupported):
            materialize_engine_input({"slides": [page("x", None, _characters=[{"name": "n"}])]}, mode="pitch", gen_dir=None, assets={})
        plain = materialize_engine_input({"slides": [page("x")]}, mode="presentation", gen_dir=None, assets=None)
        self.assertEqual(plain["slides"], [page("x")])

    def test_check_candidate_gives_the_engine_the_converted_view(self) -> None:
        tr, row, data = self.finished()
        tr.resolve(row, data)
        seen: dict = {}

        def fake_check(engine, **kw):
            seen["script"] = json.loads(Path(kw["script"]).read_text(encoding="utf-8"))
            return CheckResult(0, [], [])

        ctx = SimpleNamespace(root=self.store.root, tools=SimpleNamespace(), stop=threading.Event())
        with mock.patch.object(pipeline, "check_script", fake_check):
            result = pipeline.check_candidate(ctx, engine_for("pitch"), tr.run, data, template=tr)
        self.assertEqual(result.rc, 0)
        self.assertTrue(all(REF_KEY not in s for s in seen["script"]["slides"]))
        self.assertEqual(seen["script"]["slides"][0]["_characters"][0]["name"], "案内役")


class ResumeTests(Harness):
    class Interrupt(Exception):
        pass

    def stop_at(self, name: str):
        def boundary(n: str) -> None:
            if n == name:
                raise ResumeTests.Interrupt(n)
        return boundary

    def test_resume_after_an_interrupt_at_each_preparation_boundary(self) -> None:
        for name in ("intake.begin", "intake.done", "reading.begin", "reading.done"):
            llm = FakeLlm(GOOD)
            run = self.new_run(name="res-" + name)
            with self.assertRaises(self.Interrupt):
                self.prepare(run, llm, boundary=self.stop_at(name))
            tr = self.prepare(run, llm)
            self.assertEqual(len(llm.prompts), 1, f"{name}: 完了した段をやり直さない（LLM は 1 回）")
            self.assertEqual([r.id for r in tr.catalog.references], ["ref-1", "ref-2"], name)
            gen = self.store.run(run["id"])["record"]["preparation"]["generations"][str(tr.generation)]
            self.assertEqual(set(gen["stages"]), {"intake", "reading"}, name)
            self.assertEqual(tr.resumed, name in ("reading.done",), name)
        # 入口の確認の途中（記録に完了が無い）の世代は捨て、新しい番号でやり直す（番号は再利用しない）
        run = self.new_run(name="res-numbers")
        with self.assertRaises(self.Interrupt):
            self.prepare(run, boundary=self.stop_at("intake.begin"))
        self.assertEqual(self.prepare(run).generation, 2 if False else self.store.run(run["id"])["record"]["preparation"]["current"])
        self.assertEqual(sorted(p.name for p in self.store.root.deck_preparations_dir(run["deck_id"]).glob("gen-*")),
                         ["gen-0001", "gen-0002"])

    def test_resume_after_an_interrupt_at_each_script_boundary(self) -> None:
        expected = {"resolve.begin": (None, None), "resolve.done": ("pending", None), "manifest.begin": ("pending", None),
                    "manifest.done": (None, "done")}
        for name, (pending, done) in expected.items():
            run = self.new_run(name="scr-" + name)
            tr = self.prepare(run, FakeLlm(GOOD))
            row, data = self.script(run, [page("p-1", [CHAR])])
            data = tr.stamped(data)
            tr.boundary = self.stop_at(name)
            with self.assertRaises(self.Interrupt):
                tr.resolve(row, data)
                tr.finish(row)
            again = self.prepare(run, FakeLlm(GOOD))
            self.assertTrue(again.resumed, name)
            get = lambda sid: self.store.script(sid)   # noqa: E731
            self.assertEqual(again.pending_candidate(get) is not None, pending == "pending", name)
            self.assertEqual(again.completed(get) is not None, done == "done", name)

    def test_completed_records_are_reused_even_after_the_originals_change_or_vanish(self) -> None:
        run = self.new_run()
        llm = FakeLlm(GOOD)
        tr = self.prepare(run, llm)
        row, data = self.script(run, [page("p-1", [CHAR])])
        data = tr.stamped(data)
        tr.resolve(row, data)
        tr.finish(row)
        for f in self.images.iterdir():
            f.unlink()
        self.images.rmdir()
        again = self.prepare(run, llm)
        self.assertTrue(again.resumed)
        done = again.completed(lambda sid: self.store.script(sid))
        self.assertEqual(done["id"], row["id"])
        self.assertEqual(len(llm.prompts), 1)
        self.assertTrue(verify_assets(again.dir, again.assets))
        # コピーが壊れたら、完了した控えとしては使わない
        victim = again.dir / again.assets["ref-1"]["files"][0]["path"]
        victim.write_bytes(b"tampered")
        self.assertIsNone(again.completed(lambda sid: self.store.script(sid)))

    def test_a_retry_with_the_same_input_reuses_the_reading_but_changed_input_starts_a_new_generation(self) -> None:
        run = self.new_run()
        llm = FakeLlm(GOOD)
        first = self.prepare(run, llm)
        retry = self.prepare(self.another_run(run["deck_id"]), llm)
        self.assertEqual(len(llm.prompts), 1, "同じ入力 digest の完了した読解は LLM を呼ばずに引き継ぐ")
        self.assertEqual((retry.reused_from, retry.generation), (first.generation, first.generation + 1))
        self.assertEqual(retry.catalog.issued, first.catalog.issued)
        (self.images / "README.md").write_text("# 変えた説明書き\n", encoding="utf-8")
        changed = self.prepare(self.another_run(run["deck_id"]), llm)
        self.assertEqual((len(llm.prompts), changed.reused_from), (2, None))
        self.assertEqual(changed.generation, 3)

    def test_the_record_is_written_as_a_whole_so_generations_survive(self) -> None:
        run = self.new_run()
        self.prepare(run, FakeLlm(GOOD))
        self.store.set_run_record(run["id"], {"other": 1})   # 別のトップレベルの項目を足しても残る
        self.store.set_run_record(run["id"], {"preparation": {**self.store.run(run["id"])["record"]["preparation"], "x": 1}})
        rec = self.store.run(run["id"])["record"]["preparation"]
        self.assertEqual(set(rec["generations"]["1"]["stages"]), {"intake", "reading"})
        self.assertEqual(rec["generations"]["1"]["folder_ids"], ["res-1"])
        self.assertTrue(rec["generations"]["1"]["input_digest"])


class WriterFlowBase(Harness):
    """台本係（`pipeline.ask`）とエンジンの検査（`pipeline.check_script`）を偽物にして、`write_script` を通す。"""

    def setUp(self) -> None:
        super().setUp()
        self.ctx.settings = Settings()
        self.ctx.tools = SimpleNamespace(claude_path=lambda: "claude", codex_path=lambda: None, skills_dir=SKILLS_DIR,
                                         python=sys.executable, extra_env={})

    def run_flow(self, answers, *, run=None, tr=None, check=None):
        run = run or self.new_run()
        tr = tr or self.prepare(run, FakeLlm(GOOD))
        calls: list[dict] = []
        scripted = list(answers)

        def fake_ask(backend, exe, prompt, **kw):
            calls.append({"prompt": prompt, **kw})
            return copy.deepcopy(scripted.pop(0))

        seen = []

        def fake_check(engine, **kw):
            seen.append(json.loads(Path(kw["script"]).read_text(encoding="utf-8")))
            return check(len(seen)) if check else CheckResult(0, [], [])

        job = {"intake_id": run["args"].get("intake_id") or self.store.conn.execute(
            "SELECT intake_id FROM jobs WHERE run_id=?", (run["id"],)).fetchone()[0]}
        with mock.patch.object(pipeline, "ask", fake_ask), mock.patch.object(pipeline, "check_script", fake_check):
            out = pipeline.write_script(self.ctx, job, run, template=tr)
        return out, calls, seen, tr

    def draft(self, refs):
        return {"title": "試験", "slides": [page("cover", refs, type="cover"), page("end", type="closing")]}



class WriterFlowTests(WriterFlowBase):
    def test_the_writer_gets_writer_md_and_the_catalog_without_tools_and_the_script_is_checked_and_recorded(self) -> None:
        out, calls, seen, tr = self.run_flow([self.draft([CHAR])])
        script, data = out
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["no_tools"], "道具を持たせない")
        p = calls[0]["prompt"]
        self.assertIn("# デザインテンプレートの指示", p)
        self.assertIn("図は少なめにする", p, "writer.md の追加指示（原文）")
        self.assertIn('"id": "ref-1"', p)
        self.assertNotIn(str(self.tmp), p, "writer.md の対応表にも実パスは入らない")
        self.assertIn("案内役を使う", p)
        catalog = p.split("```json")[1].split("```")[0]
        self.assertNotIn(str(self.tmp), catalog, "カタログにパスは出ない")
        self.assertNotIn('"path"', catalog)
        self.assertIn("_references", p)
        self.assertEqual(data[PREP_KEY]["generation"], tr.generation)
        self.assertEqual(data["slides"][0][REF_KEY], [CHAR])
        self.assertEqual(seen[0]["slides"][0]["_characters"][0]["name"], "案内役", "検査は変換した台本で")
        self.assertNotIn(REF_KEY, seen[0]["slides"][0])
        gen = self.store.run(tr.run["id"])["record"]["preparation"]["generations"][str(tr.generation)]
        self.assertEqual((gen["state"], gen["stage"], gen["stages"]["script"]["script_id"]), ("completed", "manifest", script["id"]))
        self.assertIsNone(self.store.head_script(tr.run["deck_id"]), "描く前なので、いまの台本にはしない")

    def test_an_invalid_reference_is_sent_back_with_the_reason_and_nothing_is_copied_until_it_is_valid(self) -> None:
        bad = self.draft([{**CHAR, "id": "ref-3"}])
        out, calls, seen, tr = self.run_flow([bad, self.draft([CHAR])])
        self.assertEqual(len(calls), 2)
        self.assertIn("準備係が候補にしなかった", calls[1]["prompt"])
        self.assertEqual(len(seen), 1, "不正な台本は検査に回さない")

    def test_an_id_written_into_the_title_or_a_hand_written_characters_is_sent_back(self) -> None:
        bad = {"title": "t", "slides": [page("cover", [CHAR], _characters=[{"name": "n"}])]}
        out, calls, _, _ = self.run_flow([bad, self.draft([CHAR])])
        self.assertEqual(len(calls), 2)
        self.assertIn("_characters", calls[1]["prompt"])

    def test_writer_cannot_write_the_server_stamp(self) -> None:
        d = self.draft([CHAR])
        d[PREP_KEY] = {"generation": 9}
        _, calls, _, _ = self.run_flow([d, self.draft([CHAR])])
        self.assertEqual(len(calls), 2)

    def test_engine_ng_goes_back_to_the_writer_with_the_converted_check(self) -> None:
        def check(n):
            return CheckResult(2, ["NG: 1枚目: 長すぎる"], []) if n == 1 else CheckResult(0, [], [])
        out, calls, seen, tr = self.run_flow([self.draft([CHAR]), self.draft([CHAR])], check=check)
        self.assertEqual((len(calls), len(seen)), (2, 2))
        self.assertIn("長すぎる", calls[1]["prompt"])

    def test_html_with_a_reference_stops_with_a_reason_and_a_template_without_one_passes(self) -> None:
        run = self.new_run(mode="presentation", name="htmlflow")
        tr = self.prepare(run, FakeLlm({"references": []}))
        out, calls, seen, _ = self.run_flow([{"title": "t", "slides": [page("a", type="statement", headline="本文")]}], run=run, tr=tr)
        self.assertIsInstance(out, tuple)
        self.assertEqual(seen[0]["slides"][0]["id"], "a")

    def test_the_preparation_stop_is_a_failed_run_with_the_reason_never_the_old_path(self) -> None:
        run = self.new_run(name="stopflow")
        tr = self.prepare(run, FakeLlm(GOOD))
        shutil_target = self.images / "a.png"
        shutil_target.write_bytes(png(9, 9))   # 一覧のあとに差し替わる
        out, calls, seen, _ = self.run_flow([self.draft([CHAR])], run=run, tr=tr)
        self.assertEqual(out, "failed")
        stored = self.store.run(run["id"])
        self.assertEqual((stored["state"], stored["failure_kind"]), ("failed", "template_preparation"))
        self.assertEqual(seen, [], "エンジンの検査まで進まない")
        self.assertEqual(stored["record"]["template_stop"]["reason"], "changed_since_listing")

    def test_resume_reuses_a_completed_script_without_calling_the_writer(self) -> None:
        out, calls, seen, tr = self.run_flow([self.draft([CHAR])])
        again = self.prepare(tr.run, FakeLlm(GOOD))
        out2, calls2, seen2, _ = self.run_flow([], run=tr.run, tr=again)
        self.assertEqual((out2[0]["id"], calls2, seen2), (out[0]["id"], [], []))

    def test_resume_with_a_pending_candidate_only_rechecks(self) -> None:
        run = self.new_run(name="pendingflow")
        tr = self.prepare(run, FakeLlm(GOOD))
        row, data = self.script(run, [page("p-1", [CHAR])])
        data = tr.stamped(data)
        row = self.store.save_script(run["deck_id"], data, written_by="writer", writer_backend="claude", intake_id=None,
                                     run_id=run["id"])
        tr.resolve(row, data)
        again = self.prepare(run, FakeLlm(GOOD))
        out, calls, seen, _ = self.run_flow([], run=run, tr=again)
        self.assertEqual((out[0]["id"], calls, len(seen)), (row["id"], [], 1))
        self.assertEqual(self.store.run(run["id"])["record"]["preparation"]["generations"][str(again.generation)]["state"], "completed")


class EditFlowTests(WriterFlowBase):
    def test_edits_load_the_completed_generation_and_derive_a_new_one(self) -> None:
        out, _, _, tr = self.run_flow([self.draft([CHAR])])
        script, data = out
        deck = tr.run["deck_id"]
        self.store.accept_script(script["id"], __import__("quick_slide.scriptdoc", fromlist=["x"]).screen_pages(tr.run["args"], data))
        edit_run = self.another_run(deck)
        base = load_template_run(self.ctx, edit_run)
        self.assertEqual((base.generation, base.catalog.content_sha256()), (tr.generation, tr.catalog.content_sha256()))
        self.assertFalse(base.owned)
        # 新しい素材（ref-2）を使うページを差し込む → 引き継いだ新しい世代に控える（元の世代は変えない）
        before = {p.name: p.read_bytes() for p in tr.dir.rglob("*") if p.is_file()}
        new = insert_after(data, "cover", [page("n1", [{**CHAR, "id": "ref-2"}])])
        row = self.store.save_script(deck, new, written_by="writer", writer_backend="claude", intake_id=None, run_id=edit_run["id"])
        base.resolve(row, new)
        base.finish(row)
        self.assertTrue(base.owned and base.generation > tr.generation)
        self.assertEqual({p.name: p.read_bytes() for p in tr.dir.rglob("*") if p.is_file()}, before, "元の世代は書き換えない")
        gens = self.store.run(edit_run["id"])["record"]["preparation"]["generations"]
        (g,) = gens.values()
        self.assertEqual((g["parent"], g["state"]), (tr.generation, "completed"))
        self.assertEqual(set(base.assets), {"ref-1", "ref-2"}, "既存ページの素材は引き継ぐ（取り直さない）")
        # 控えが無い資料は断る
        other = self.new_run(name="nopreparation")
        with self.assertRaises(PreparationStopped):
            load_template_run(self.ctx, other)


class HardeningTests(Harness):
    def test_a_directory_component_that_is_a_link_is_refused(self) -> None:
        from helpers import can_symlink

        if not can_symlink():
            self.skipTest("symlink を作れない機械")
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "secret.png").write_bytes(png())
        (self.images / "sub").symlink_to(outside, target_is_directory=True)
        (self.images / "leaf.png").symlink_to(outside / "secret.png")
        reader = Reader()
        for rel in ("sub/secret.png", "leaf.png"):
            with self.assertRaises(PreparationStopped, msg=rel):
                os.close(reader.open(self.images, rel)[0])
        tr = self.prepare(self.new_run(), FakeLlm({"references": []}))
        rels = [i["rel"] for i in tr.intake["resources"][0]["items"]]
        self.assertNotIn("sub/secret.png", rels)
        self.assertNotIn("leaf.png", rels)

    def test_windows_names_junctions_and_short_names_are_refused(self) -> None:
        from quick_slide.preparation.reading import _check_rel, is_reparse

        for bad in ("a.png:stream", "x.png.", "x.png ", "CON", "nul.png", "com1.txt", "PROGRA~1/a.png", "a/AUX.png"):
            with self.assertRaises(PreparationStopped, msg=bad):
                _check_rel(bad, windows=True)
        self.assertEqual(_check_rel("dir/ok.png", windows=True), ["dir", "ok.png"])
        _check_rel("a:b", windows=False) if os.name != "nt" else None
        self.assertTrue(is_reparse(SimpleNamespace(st_file_attributes=0x400)))
        self.assertFalse(is_reparse(SimpleNamespace(st_file_attributes=0x20)))

    def test_inventory_stops_while_enumerating(self) -> None:
        consumed = []

        class Entry:
            def __init__(self, n):
                self.name, self.path = f"f{n}.txt", f"/x/f{n}.txt"
                consumed.append(n)

            def is_symlink(self): return False
            def is_dir(self, follow_symlinks=True): return False
            def is_file(self, follow_symlinks=True): return True
            def stat(self, follow_symlinks=True): return SimpleNamespace(st_size=1)

        class It:
            def __iter__(self): return (Entry(n) for n in range(10**9))
            def __enter__(self): return self
            def __exit__(self, *a): return False

        with mock.patch("os.scandir", return_value=It()):
            with self.assertRaises(PreparationStopped):
                Reader(Policy(listing_items=3)).inventory(Path("/x"), "n")
        self.assertLessEqual(len(consumed), 5, "全件を取ってから数えない")

    def test_a_swap_to_a_huge_image_after_listing_cannot_slip_past_the_pixel_limit(self) -> None:
        tr = self.prepare(self.new_run(), FakeLlm(GOOD), reader=Reader(Policy(image_pixels=100)))
        (self.images / "a.png").write_bytes(png(50, 50, header_only=True))
        row, data = self.script(tr.run, [page("p-1", [CHAR])])
        with self.assertRaises(PreparationStopped) as cm:
            tr.resolve(row, tr.stamped(data))
        self.assertIn(cm.exception.code, ("limit_exceeded", "changed_since_listing"))
        self.assertEqual(list((tr.dir / "assets").glob("*")) if (tr.dir / "assets").exists() else [], [])

    def test_resume_before_resolution_rechecks_the_original_input(self) -> None:
        run = self.new_run()
        llm = FakeLlm(GOOD)
        with self.assertRaises(ResumeTests.Interrupt):
            self.prepare(run, llm, boundary=ResumeTests.stop_at(self, "reading.begin"))
        (self.images / "README.md").write_text("# 中断のあとで変わった説明書き\n", encoding="utf-8")
        tr = self.prepare(run, llm)
        self.assertEqual(tr.generation, 2, "入力が変わっていたら新しい世代でやり直す")
        self.assertIn("中断のあとで変わった説明書き", llm.prompts[-1])

    def test_two_images_on_one_page_are_refused(self) -> None:
        tr = self.prepare(self.new_run(), FakeLlm({"references": [
            {"id": "ref-1", "roles": ["image"], "description": "a"}, {"id": "ref-2", "roles": ["image"], "description": "b"}]}))
        two = [{"id": "ref-1", "role": "image", "reason": "r"}, {"id": "ref-2", "role": "image", "reason": "r"}]
        self.assertTrue(check_references(page("p", two), "p", RefContext.of(tr.catalog)))
        row, data = self.script(tr.run, [page("p", two)])
        with self.assertRaises(EngineInputUnsupported):
            tr.resolve(row, tr.stamped(data))
            tr.convert(data)

    def test_first_draft_image_paths_are_refused(self) -> None:
        tr = self.prepare(self.new_run(), FakeLlm(GOOD))
        for slide in (page("a", image={"path": "/etc/hosts"}), page("a", image="/etc/hosts"),
                      page("a", cards=[{"image": {"path": "../x.png"}}])):
            self.assertTrue(tr.check_script({"slides": [slide]}, fresh=True), slide)
        self.assertEqual(tr.check_script({"slides": [page("a", image={"path": "attachments/x.png"})]}, fresh=True,
                                         allowed_paths={"attachments/x.png"}), [])
        self.assertEqual(tr.check_script({"slides": [page("a", image={"prompt": "夕焼け"})]}, fresh=True), [])


class ExplicitGenerationTests(WriterFlowBase):
    def test_an_edit_uses_the_generation_of_the_head_script_and_stops_if_it_is_broken(self) -> None:
        from quick_slide.scriptdoc import screen_pages

        out, _, _, tr = self.run_flow([self.draft([CHAR])])
        script, data = out
        deck = tr.run["deck_id"]
        self.store.accept_script(script["id"], screen_pages(tr.run["args"], data))
        # 新しい世代（壊れていない古い世代が残る状況）を作ってから、対象の台本が使った世代を壊す
        spare = self.another_run(deck)
        extra = load_template_run(self.ctx, spare)
        new = insert_after(data, "cover", [page("n1", [{**CHAR, "id": "ref-2"}])])
        row = self.store.save_script(deck, new, written_by="writer", writer_backend="claude", intake_id=None, run_id=spare["id"])
        extra.resolve(row, new)
        extra.finish(row)
        self.assertGreater(extra.generation, tr.generation)
        edit_run = self.another_run(deck)
        self.assertEqual(load_template_run(self.ctx, edit_run).generation, tr.generation, "最新の世代ではなく、台本が使った世代")
        (tr.dir / "manifest.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(PreparationStopped) as cm:
            load_template_run(self.ctx, edit_run)
        self.assertEqual(cm.exception.code, "preparation_changed", "古い世代・新しい世代へ落ちずに止める")


class HardeningTests2(Harness):
    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "権限が効かない環境（root・Windows）では試せない")
    def test_an_ancestor_that_can_be_searched_but_not_listed_still_opens(self) -> None:
        anc = self.tmp / "searchable"
        root = anc / "root"
        root.mkdir(parents=True)
        (root / "a.png").write_bytes(png())
        anc.chmod(0o111)
        self.addCleanup(anc.chmod, 0o755)
        fd, st, _ = Reader().open(root, "a.png")
        os.close(fd)
        self.assertGreater(st.st_size, 0)

    def test_swapping_the_root_or_an_ancestor_right_after_realpath_cannot_escape(self) -> None:
        from helpers import can_symlink

        if not can_symlink():
            self.skipTest("symlink を作れない機械")
        outside = self.tmp / "outside"
        (outside / "inner").mkdir(parents=True)
        (outside / "secret.png").write_bytes(png())
        (outside / "inner" / "secret.png").write_bytes(png())
        real_realpath = os.path.realpath
        for victim, rel in ((self.tmp / "root1", "secret.png"), (self.tmp / "anc" / "root2", "secret.png")):
            victim.mkdir(parents=True)
            (victim / rel).write_bytes(png())
            swapped = victim if victim.parent == self.tmp else victim.parent
            moved = swapped.with_name(swapped.name + ".moved")
            target = outside if swapped is victim else outside.parent / "outside"

            def hook(path, *a, _v=victim, _s=swapped, _m=moved, _t=target, **k):
                out = real_realpath(path, *a, **k)
                if Path(path) == _v and not _m.exists():
                    _s.rename(_m)
                    _s.symlink_to(_t if _s is _v else outside.parent, target_is_directory=True)
                    if _s is not _v:
                        (outside.parent / "root2").mkdir(exist_ok=True)
                        (outside.parent / "root2" / rel).write_bytes(png())
                return out

            with mock.patch("os.path.realpath", hook):
                with self.assertRaises(PreparationStopped, msg=str(victim)):
                    os.close(Reader().open(victim, rel)[0])

    def test_the_copy_opens_the_input_once_and_a_swap_after_the_header_check_is_caught(self) -> None:
        calls = []
        real_open = Reader.open

        def counting(self_, root, rel):
            calls.append(rel)
            return real_open(self_, root, rel)

        tr = self.prepare(self.new_run(), FakeLlm(GOOD), reader=Reader(Policy(image_pixels=100)))
        row, data = self.script(tr.run, [page("p-1", [CHAR])])
        data = tr.stamped(data)
        with mock.patch.object(Reader, "open", counting):
            tr.resolve(row, data)
        self.assertEqual(calls, ["a.png"], "ヘッダ検査・照合・コピーを、1 回開いた入力でする")

    @unittest.skipIf(os.name == "nt", "Windows では、開いている間のファイルの置き換え自体を OS が断る（PermissionError）ので、この競合は POSIX だけで試す")
    def test_a_swap_after_the_header_check_is_caught(self) -> None:
        tr = self.prepare(self.new_run(), FakeLlm(GOOD), reader=Reader(Policy(image_pixels=100)))
        row, data = self.script(tr.run, [page("p-1", [CHAR])])
        tr.resolve(row, tr.stamped(data))
        # 検査の直後・コピーの直前に、巨大な画像へ差し替える
        import quick_slide.preparation.reading as R

        real_sniff = R.sniff_image
        seen = {"n": 0}
        target = self.images / "b.png"

        def sniff_then_swap(fd, size):
            out = real_sniff(fd, size)
            if seen["n"] == 0:
                seen["n"] = 1
                big = self.images / "swap.tmp"
                big.write_bytes(png(50, 50, header_only=True))
                os.replace(big, target)
            return out

        row2, data2 = self.script(tr.run, [page("p-2", [{**CHAR, "id": "ref-2"}])])
        with mock.patch.object(R, "sniff_image", sniff_then_swap):
            with self.assertRaises(PreparationStopped) as cm:
                tr.resolve(row2, tr.stamped(data2))
        self.assertIn(cm.exception.code, ("changed_during_read", "limit_exceeded", "changed_since_listing"))
        self.assertEqual(len(list((tr.dir / "assets").iterdir())), 1, "巨大な画像はコピーされない（a だけ）")

    def test_unc_final_paths_are_normalised(self) -> None:
        from quick_slide.preparation.reading import normalize_final_path as n

        self.assertEqual(n("\\\\?\\UNC\\server\\share\\dir\\a.png"), "\\\\server\\share\\dir\\a.png")
        self.assertEqual(n("\\\\?\\C:\\dir\\a.png"), "C:\\dir\\a.png")
        self.assertEqual(n("C:\\dir"), "C:\\dir")

    def test_concurrent_writes_of_the_same_file_do_not_break_each_other(self) -> None:
        from quick_slide.preparation.files import write_json

        target = self.tmp / "same.json"
        errors = []

        def work(i: int) -> None:
            try:
                for _ in range(30):
                    write_json(target, {"i": i, "pad": "x" * 1000})
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertIn("i", json.loads(target.read_text(encoding="utf-8")))

    def test_same_named_headings_in_the_original_text_keep_every_instruction_and_no_path_remains(self) -> None:
        d = doc("見出し", modes=("pitch",))
        d["structure"] = "## 1. 結論\n# リソース\n構成の後半（必須A）"
        d["instructions"] = "前半の指示\n\n# リソース\n後半の追加指示（必須B）\n\n# リソースを読む・素材を選ぶ工程の指示\n必須C"
        d["resources"] = self.resources(("画像集", "directory", self.images, "案内役を使う"))
        self.store.create_design_template(self.bid, d)
        run = self.store.run(self.intake(design_template="見出し", mode="pitch")["run_id"])
        pipeline.settle_look(self.ctx, run, previous_style=None)
        llm = FakeLlm(GOOD)
        tr = self.prepare(run, llm)
        for text in (tr.compiled["writer.md"], tr.compiled["preparation.md"], llm.prompts[0]):
            for must in ("構成の後半（必須A）" if text is not tr.compiled["preparation.md"] else "前半の指示", "後半の追加指示（必須B）", "必須C"):
                self.assertIn(must, text)
            self.assertNotIn(str(self.tmp), text)
        self.assertIn("画像集", tr.compiled["writer.md"])


class ManifestIntegrityTests(WriterFlowBase):
    def forge_manifest(self, tr, **changes):
        """manifest の ID・世代だけを書き換え、ハッシュの鎖（by-script・記録）は合わせ直す（ID の照合だけで止まるかを見る）。"""
        from quick_slide.preparation.files import sha256_file, write_json

        m = json.loads((tr.dir / "manifest.json").read_text(encoding="utf-8"))
        for k, v in changes.items():
            if k == "script_id":
                m["script"]["id"] = v
            else:
                m[k] = v
        sha = write_json(tr.dir / "manifest.json", m)
        mapping = self.store.root.deck_preparations_dir(tr.run["deck_id"]) / "by-script"
        ref_file = next(mapping.glob("*.json"))
        ref = json.loads(ref_file.read_text(encoding="utf-8"))
        write_json(ref_file, {**ref, "manifest_sha256": sha})
        rid = tr.run["id"]
        rec = self.store.run(rid)["record"]["preparation"]
        rec["generations"][str(tr.generation)]["stages"]["manifest"]["files"]["manifest.json"] = sha
        self.store.set_run_record(rid, {"preparation": rec})

    def test_each_manifest_id_alone_is_enough_to_stop(self) -> None:
        for change in ({"script_id": "other-script"}, {"generation": 99}, {"preparation_id": "other-preparation"}):
            run = self.new_run(name="ids" + next(iter(change)))
            tr = self.prepare(run, FakeLlm(GOOD))
            row, data = self.script(run, [page("p-1", [CHAR])])
            data = tr.stamped(data)
            row = self.store.save_script(run["deck_id"], data, written_by="writer", writer_backend="claude", intake_id=None,
                                         run_id=run["id"])
            tr.resolve(row, data)
            tr.finish(row)
            from quick_slide.scriptdoc import screen_pages

            self.store.accept_script(row["id"], screen_pages(run["args"], data))
            edit_run = self.another_run(run["deck_id"])
            load_template_run(self.ctx, edit_run)
            self.forge_manifest(tr, **change)
            with self.assertRaises(PreparationStopped, msg=str(change)):
                load_template_run(self.ctx, edit_run)

    def test_a_mapping_with_only_a_wrong_script_id_is_repaired_on_resume(self) -> None:
        run = self.new_run(name="mapid")
        tr = self.prepare(run, FakeLlm(GOOD))
        row, data = self.script(run, [page("p-1", [CHAR])])
        data = tr.stamped(data)
        row = self.store.save_script(run["deck_id"], data, written_by="writer", writer_backend="claude", intake_id=None,
                                     run_id=run["id"])
        tr.resolve(row, data)
        tr.finish(row)
        mapping = self.store.root.deck_preparations_dir(run["deck_id"]) / "by-script" / f"{row['id']}.json"
        ref = json.loads(mapping.read_text(encoding="utf-8"))
        mapping.write_text(json.dumps({**ref, "script_id": "wrong"}), encoding="utf-8")
        again = self.prepare(run, FakeLlm(GOOD))
        self.assertIsNotNone(again.completed(lambda sid: self.store.script(sid)))
        self.assertEqual(json.loads(mapping.read_text(encoding="utf-8"))["script_id"], row["id"])

    def accepted(self):
        from quick_slide.scriptdoc import screen_pages

        out, _, _, tr = self.run_flow([self.draft([CHAR])])
        script, data = out
        self.store.accept_script(script["id"], screen_pages(tr.run["args"], data))
        return script, data, tr, self.another_run(tr.run["deck_id"])

    def test_tampered_resolve_files_stop_the_edit_load(self) -> None:
        script, data, tr, edit_run = self.accepted()
        load_template_run(self.ctx, edit_run)   # 無傷なら読める
        assets, bindings = (tr.dir / "assets.json").read_bytes(), (tr.dir / "bindings.json").read_bytes()
        (tr.dir / "assets.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(PreparationStopped):
            load_template_run(self.ctx, edit_run)
        (tr.dir / "assets.json").write_bytes(assets)
        # 別の有効な素材（ref-2）へ対応表を替える
        other = json.loads(bindings)
        other["bindings"][0]["ref_id"] = "ref-2"
        other["refs"] = {"ref-2": other["refs"]["ref-1"]}
        (tr.dir / "bindings.json").write_text(json.dumps(other), encoding="utf-8")
        with self.assertRaises(PreparationStopped):
            load_template_run(self.ctx, edit_run)
        (tr.dir / "bindings.json").write_bytes(bindings)
        load_template_run(self.ctx, edit_run)

    def test_an_interrupt_between_the_manifest_record_and_the_mapping_is_repaired_on_resume(self) -> None:
        class Boom(Exception):
            pass

        run = self.new_run(name="repair")
        tr = self.prepare(run, FakeLlm(GOOD))
        row, data = self.script(run, [page("p-1", [CHAR])])
        data = tr.stamped(data)
        row = self.store.save_script(run["deck_id"], data, written_by="writer", writer_backend="claude", intake_id=None, run_id=run["id"])
        tr.resolve(row, data)

        def boundary(name: str) -> None:
            if name == "manifest.recorded":
                raise Boom()

        tr.boundary = boundary
        with self.assertRaises(Boom):
            tr.finish(row)
        mapping = self.store.root.deck_preparations_dir(run["deck_id"]) / "by-script" / f"{row['id']}.json"
        self.assertFalse(mapping.exists())
        again = self.prepare(run, FakeLlm(GOOD))
        done = again.completed(lambda sid: self.store.script(sid))
        self.assertEqual(done["id"], row["id"])
        self.assertTrue(mapping.is_file())
        self.assertEqual(self.store.run(run["id"])["record"]["preparation"]["generations"][str(again.generation)]["state"], "completed")
        # 対応表だけが消えた（完了済み）場合も、再開で同じ記録から作り直す
        mapping.unlink()
        self.assertIsNotNone(self.prepare(run, FakeLlm(GOOD)).completed(lambda sid: self.store.script(sid)))
        self.assertTrue(mapping.is_file())


class BackendFlagTests(unittest.TestCase):
    def test_codex_no_tools_disables_the_same_set_as_web_research_but_without_web(self) -> None:
        cmd = backends._codex_command("codex", Path("/work"), no_tools=True)
        # 期待値は実装の定数ではなく、試験の側に独立して書く（欠けた無効化を検出するため）
        required = ["shell_tool", "unified_exec", "view_image", "image_generation", "browser_use", "browser_use_external",
                    "browser_use_full_cdp_access", "computer_use", "in_app_browser", "in_app_local_automation", "apps", "plugins",
                    "remote_plugin", "multi_agent", "code_mode_host", "skill_mcp_dependency_install", "hooks"]
        for name in required:
            self.assertIn(f"features.{name}=false", cmd)
        self.assertEqual({c for c in cmd if c.startswith("features.")}, {f"features.{n}=false" for n in required})
        self.assertIn('web_search="disabled"', cmd)
        self.assertNotIn("--search", cmd)
        self.assertNotIn('web_search="live"', cmd)
        self.assertIn("read-only", cmd)
        web = backends._codex_command("codex", Path("/work"), web=True)
        self.assertEqual([c for c in web if c.startswith("features.")], [c for c in cmd if c.startswith("features.")])
        plain = backends._codex_command("codex", Path("/work"))
        self.assertNotIn("features.shell_tool=false", plain, "これまでの呼び方は変えない")
        with self.assertRaises(ValueError):
            backends._codex_command("codex", Path("/work"), web=True, no_tools=True)

    def test_a_cli_that_cannot_apply_the_required_flags_is_refused(self) -> None:
        env = {}
        with mock.patch.object(backends, "_help_text", return_value="Usage: codex --ephemeral"):
            with self.assertRaises(WriterFailed) as cm:
                backends.ask_codex.__wrapped__("codex", "p", job_dir=Path(temp_dir(self)), env=env, timeout=1, stop=None, no_tools=True)
            self.assertEqual(cm.exception.kind, "missing")
        with mock.patch.object(backends, "_help_text", return_value="Usage: claude --verbose --include-partial-messages"):
            with self.assertRaises(WriterFailed) as cm:
                backends.ask_claude.__wrapped__("claude", "p", job_dir=Path(temp_dir(self)), env=env, timeout=1, stop=None,
                                                no_tools=True)
            self.assertEqual(cm.exception.kind, "missing")
        with mock.patch.object(backends, "_help_text", return_value="--tools --strict-mcp-config --safe-mode"):
            flags = backends._claude_flags("claude", {}, Path("/w"), no_tools=True)
        self.assertEqual(flags[flags.index("--tools") + 1], "")

    def test_ask_passes_no_tools_only_when_asked(self) -> None:
        seen = {}

        def runner(exe, prompt, **kw):
            seen.update(kw)
            return '{"ok": 1}'

        with mock.patch.object(backends, "ask_claude", runner):
            backends.ask("claude", "x", "p", job_dir=Path("/w"), env={}, timeout=1)
            self.assertNotIn("no_tools", seen)
            backends.ask("claude", "x", "p", job_dir=Path("/w"), env={}, timeout=1, no_tools=True)
            self.assertTrue(seen["no_tools"])


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        from test_worker import fake_skills_dir, settings

        tools, self.log = fake_tools(self, skills_dir=fake_skills_dir(self))
        tools.extra_env.update({"FAKE_ENGINE_SLEEP": "0.01", "FAKE_ENGINE_LOG": str(self.log / "engine")})
        self.fx = start_server(self, tools=tools, settings=settings())
        self.tmp = temp_dir(self)
        with self.fx.server.store_lock:
            cfg = json.loads(json.dumps(self.fx.store.brand_revision(self.fx.store.preset_brand_revision_id())["config"]))
            self.brand = self.fx.store.create_brand("試験のブランド", cfg)

    def test_a_template_with_a_skill_folder_stops_at_preparation_and_never_calls_the_writer(self) -> None:
        from helpers import writer_calls

        skill = self.tmp / "skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text("# skill\n", encoding="utf-8")
        d = doc("スキルつき", modes=("presentation",))
        d["resources"] = [{"id": "r1", "name": "スキル", "kind": "directory", "path": str(skill), "usage": ""}]
        with self.fx.server.store_lock:
            self.fx.store.create_design_template(self.brand["id"], d)
        body = self.fx.post("/api/intakes", intake_payload(design_template="スキルつき")).json()
        run = wait_until_idle(self.fx, body["deck_id"])
        self.assertEqual((run["state"], run["failure_kind"]), ("failed", "template_preparation"))
        self.assertIn("スキルの読み取りはまだ使えない", run["message"])
        self.assertEqual(writer_calls(self.log), [], "台本係も準備係も呼ばない")
        with self.fx.server.store_lock:
            self.assertEqual(self.fx.store.conn.execute("SELECT COUNT(*) FROM scripts").fetchone()[0], 0)
            self.assertEqual(self.fx.store.run(run["id"])["record"]["template_stop"]["reason"], "skill_unsupported")

    def test_a_normal_resource_template_goes_through_preparation_and_script_and_stops_before_drawing(self) -> None:
        from helpers import writer_calls

        folder = self.tmp / "refs"
        folder.mkdir()
        (folder / "README.md").write_text("使い方\n", encoding="utf-8")
        d = doc("資料つき", modes=("presentation",))
        d["resources"] = [{"id": "r1", "name": "資料", "kind": "directory", "path": str(folder), "usage": ""}]
        with self.fx.server.store_lock:
            self.fx.store.create_design_template(self.brand["id"], d)
        body = self.fx.post("/api/intakes", intake_payload(design_template="資料つき")).json()
        run = wait_until_idle(self.fx, body["deck_id"])
        # 偽の台本係は台本を返すので、準備係の答えは形が合わず、初回＋修正 2 回で止まる（本物のモデルは呼ばない）
        self.assertEqual((run["state"], run["failure_kind"]), ("failed", "template_preparation"))
        self.assertEqual(len(writer_calls(self.log)), 3)
        with self.fx.server.store_lock:
            gen = self.fx.store.run(run["id"])["record"]["preparation"]["generations"]["1"]
            self.assertEqual(gen["stop_reason"]["code"], "preparer_malformed")


if __name__ == "__main__":
    unittest.main()
