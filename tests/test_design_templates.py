"""テンプレート指示を失わずに、共通の入力へ変換できること。実モデルは呼ばない。"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from quick_slide import design_templates as dt
from quick_slide import design_templates_legacy as legacy
from quick_slide.design_templates import (TOP_KEYS, compile_document, convert, from_markdown, new_document, resource_presence,
                                          to_markdown, unresolved_paths, validate)
from quick_slide.store.presets import PRESET_BRAND_CONFIG

REPO = Path(__file__).resolve().parents[1]
# どの OS でも絶対パスになる、存在しない置き場（Windows ではドライブ名の無いパスは絶対パスにならない）
NO_SUCH = Path(tempfile.gettempdir()).resolve() / "qs-design-template-no-such"


class DesignTemplateTests(unittest.TestCase):
    def setUp(self):
        self.brand = {"id": "brand-fixture", "current_revision_id": "brand-revision", "name": "試験ブランド", "config": copy.deepcopy(PRESET_BRAND_CONFIG), "assets": []}
        self.d = new_document("meeting-format", self.brand["id"], "試験用の定例")
        self.d["modes"] = ["pitch"]
        self.d["revision"] = 1

    def test_saved_document_has_only_the_four_screen_sections(self):
        # 画面から編集できない欄を保存形式に持たない。
        self.assertEqual(set(self.d), TOP_KEYS)
        self.assertEqual(TOP_KEYS - {"schema_version", "id", "brand_id", "revision", "based_on"},
                         {"name", "description", "modes", "structure", "instructions", "resources"})
        self.assertNotIn("attachments", self.d)
        self.assertEqual(self.d["schema_version"], 4)

    def test_free_instructions_reach_writer_preparation_and_drawing_without_truncation(self):
        original = "指定のない人物は描かず、原案の順序を保つ。\n" * 50
        self.d["instructions"] = original
        self.d["structure"] = "## 追加の主張\n主張を独立して見せる\n条件: 他のページに埋もれるときだけ"
        compiled = compile_document(self.d, self.brand, mode="pitch")
        for name in ("writer.md", "preparation.md"):
            self.assertIn(original, compiled["files"][name])
        self.assertIn("他のページに埋もれるときだけ", compiled["files"]["writer.md"])
        self.assertIn("承認済みの台本が渡された場合は", compiled["files"]["writer.md"])
        import yaml
        self.assertIn(original.strip(), yaml.safe_load(compiled["files"]["style.yaml"])["text"])

    def test_structure_accepts_free_text_without_parsing(self):
        original = "pages:\n  - title: 結論\n    when: 必要な場合だけ\n\n自由な補足。\n"
        self.d["structure"] = original
        self.assertEqual(validate(self.d)["structure"], original)
        self.assertIn(original, compile_document(self.d, self.brand, mode="pitch")["files"]["writer.md"])

    def test_resource_name_is_bound_to_its_path_for_every_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("画像集は読み取りだけで使う", encoding="utf-8")
            self.d["instructions"] = "「画像集」から、内容に合う絵を選んで使う。"
            self.d["resources"] = [{"id": "images", "name": "画像集", "kind": "directory", "path": directory, "usage": "必要な絵だけ選ぶ"}]
            compiled = compile_document(self.d, self.brand, mode="pitch")
            self.assertTrue(compiled["can_prepare"])
            self.assertFalse(compiled["render_ready"])
            self.assertEqual(compiled["resources"][0]["guide"], "README.md")
            self.assertEqual(compiled["resources"][0]["selection"], "not_run")
            for name in ("writer.md", "preparation.md", "style.yaml"):
                text = compiled["files"][name]
                self.assertIn("## 画像集", text)
                self.assertIn(directory, text)
                self.assertIn("必要な絵だけ選ぶ", text)
            (root / "README.md").unlink()
            root_missing = compile_document(dict(self.d, resources=[dict(self.d["resources"][0], path=directory + "-missing")]), self.brand, mode="pitch")
            self.assertFalse(root_missing["can_prepare"])
            self.assertFalse(root_missing["resources"][0]["exists"])

    def test_resource_names_must_be_unique_across_paths_and_old_attachments(self):
        # 旧形式の添付は同じ名前のリソースへ移るので、名前が重なれば黙って改名せずに断る
        old = {**copy.deepcopy(self.d), "schema_version": 3, "attachments": [
            {"id": "b", "name": "参考資料", "file": "b.md", "url": "/preview/files/b", "image": False, "usage": "", "sha256": "b"}]}
        old["resources"] = [{"id": "a", "name": "参考資料", "kind": "file", "path": str(NO_SUCH / "a.md"), "usage": ""}]
        with self.assertRaisesRegex(ValueError, "重複"):
            validate(old, relative_paths=True)

    def test_a_current_document_cannot_carry_an_attachments_field(self):
        self.d["attachments"] = []
        with self.assertRaises(ValueError):
            validate(self.d)

    def test_old_detailed_fields_move_into_instructions_and_resources(self):
        old = {"schema_version": 2, "id": "old", "brand_id": self.brand["id"], "revision": 3, "name": "旧", "modes": ["pitch"], "description": "",
               "script": {"purpose": "目的の文", "audience": "", "writing": "", "conversion": "", "mandatory": "共通の文", "source_policy": "guided", "structure": "## 表紙"},
               "drawing": {"overall": "全体の文", "body": "", "diagrams": "", "cover": "", "colors": "", "typography": "", "illustration": "",
                           "forbidden": "", "additional": "共通の文", "image_look": "photo", "bleed": False, "image_only": True},
               "references": [{"id": "kit", "label": "画像集", "path": str(NO_SUCH / "kit"), "kind": "skill", "instructions": "使い方の文", "required": False, "scope": "both"}],
               "materials": [], "inputs": [{"key": "date", "label": "開催日", "type": "date", "required": True, "instructions": "推測しない"}],
               "checks": {"script": "文章の確認", "image": ""}}
        original = copy.deepcopy(old)
        upgraded = validate(old)
        self.assertEqual(old, original)
        self.assertEqual(set(upgraded), TOP_KEYS)
        self.assertEqual(upgraded["structure"], "## 表紙")
        for value in ("目的の文", "全体の文", "写真調", "画像だけで描く", "開催日", "推測しない", "文章の確認"):
            self.assertIn(value, upgraded["instructions"])
        # 台本・描画の両方に書かれていた同じ文は、一度だけ写す。
        self.assertEqual(upgraded["instructions"].count("共通の文"), 1)
        self.assertEqual(upgraded["resources"], [{"id": "kit", "name": "画像集", "kind": "directory", "path": str(NO_SUCH / "kit"),
                                                  "usage": "使い方の文\n見つからない場合は、このリソースを使わずに進めてよい。"}])

    def test_old_preserve_policy_and_recipient_survive_migration(self):
        old = {"schema_version": 2, "id": "old", "brand_id": self.brand["id"], "revision": 1, "name": "旧", "modes": ["pitch"], "description": None,
               "script": {"source_policy": "preserve", "structure": "## 表紙"}, "drawing": {},
               "references": [{"id": "a", "label": "画像集", "path": str(NO_SUCH / "a"), "kind": "directory", "instructions": "", "required": True, "scope": "drawing"}],
               "materials": [], "inputs": [], "checks": {}}
        upgraded = validate(old)
        self.assertIn("構成へ無断で圧縮しない", upgraded["instructions"])
        self.assertEqual(upgraded["resources"][0]["usage"], "主に描画で使う。")
        self.assertEqual(upgraded["description"], "")

    def test_broken_old_document_is_an_input_error(self):
        with self.assertRaisesRegex(ValueError, "旧形式"):
            validate({"schema_version": 2, "id": "old", "script": {}, "drawing": {}, "checks": {}, "inputs": [None]})

    def test_v1_structure_rows_migrate_to_text(self):
        old = new_document("old", self.brand["id"], "旧")
        old = {**{k: v for k, v in old.items() if k not in ("structure", "instructions", "resources")}, "schema_version": 1,
               "script": {"structure": [{"role": "結論", "instructions": "第一行\n  第二行", "condition": "必要なとき\nだけ"}]},
               "drawing": {}, "references": [], "materials": [], "inputs": [], "checks": {}}
        upgraded = validate(old)
        for value in ("結論", "第一行\n  第二行", "必要なとき\nだけ"):
            self.assertIn(value, upgraded["structure"])

    def test_unknown_fields_cannot_be_hidden_in_saved_json(self):
        self.d["mapping"] = [{"hidden": "not editable"}]
        with self.assertRaises(ValueError):
            validate(self.d)

    def test_relative_resource_paths_are_rejected(self):
        self.d["resources"] = [{"id": "kit", "name": "画像集", "kind": "directory", "path": "relative-kit", "usage": ""}]
        with self.assertRaisesRegex(ValueError, "絶対パス"):
            validate(self.d)

    def test_cli_and_shared_compiler_have_identical_effective_instructions(self):
        self.d["instructions"] = "白地と余白を使い、主張を1つだけ見せる。"
        expected = compile_document(self.d, self.brand, mode="pitch")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "template.json").write_text(json.dumps(self.d), encoding="utf-8")
            (root / "brand.json").write_text(json.dumps(self.brand), encoding="utf-8")
            result = subprocess.run([sys.executable, str(REPO / "tools/compile_design_template.py"), "--template", str(root / "template.json"), "--brand", str(root / "brand.json"), "--mode", "pitch", "--out", str(root / "out")], text=True, encoding="utf-8", capture_output=True, check=True)
            self.assertEqual(json.loads(result.stdout)["contract_sha256"], expected["contract_sha256"])
            for name, value in expected["files"].items():
                if value:
                    self.assertEqual((root / "out" / name).read_text(encoding="utf-8"), value)
            # 既存エンジンのローダーが、出力したカスタムスタイルをそのまま読める。
            code = "import sys;sys.path.insert(0,sys.argv[1]);import styles;print(styles.load(sys.argv[2])['name'])"
            engine = subprocess.run([sys.executable, "-B", "-c", code, str(REPO / "skills/image-slide/scripts"), str(root / "out/style.yaml")], text=True, encoding="utf-8", capture_output=True, check=True)
            self.assertEqual(engine.stdout.strip(), self.d["id"])

    def test_markdown_file_round_trips_the_four_sections(self):
        self.d.update(description="会議用", structure="## 表紙\n題材", instructions="## やること\n- 白地\n\n| a | b |\n|---|---|\n| 1 | 2 |")
        self.d["resources"] = [{"id": "r1", "name": "画像集", "kind": "directory", "path": "~/images", "usage": "1行目\n2行目"}]
        back = from_markdown(to_markdown(self.d), brand_id=self.brand["id"])
        for key in ("id", "name", "description", "modes", "structure", "instructions"):
            self.assertEqual(back[key], self.d[key], key)
        self.assertEqual([(r["name"], r["kind"], r["path"], r["usage"]) for r in back["resources"]], [("画像集", "directory", "~/images", "1行目\n2行目")])
        # 版・由来は持ち込まない（取り込む側が決める）
        self.assertEqual((back["revision"], back["based_on"]), (0, None))

    def test_markdown_section_marker_inside_content_is_refused_on_export(self):
        self.d["instructions"] = "# 構成\n紛らわしい"
        with self.assertRaisesRegex(ValueError, "##"):
            to_markdown(self.d)


class LegacyConversionTests(unittest.TestCase):
    """保存形式3（添付の専用欄）→ 4（resources だけ）の読み込み時の変換。元の辞書・保存済みのバイトには触らない。"""

    def old(self, attachments):
        d = new_document("legacy", "b", "旧")
        d.update(schema_version=3, attachments=attachments)
        d["resources"] = [{"id": "kit", "name": "画像集", "kind": "directory", "path": str(NO_SUCH / "kit"), "usage": "選ぶ"}]
        return d

    ATT = {"id": "m1", "name": "旧添付", "file": "memo.md", "url": "/x", "image": False, "usage": "用語の見本", "sha256": "a" * 64}

    def test_empty_attachments_are_only_dropped(self):
        old = self.old([])
        before = copy.deepcopy(old)
        out = convert(old)
        self.assertEqual(old, before, "元の辞書を壊さない")
        self.assertEqual((out["schema_version"], "attachments" in out), (4, False))
        self.assertEqual(out["resources"], before["resources"])
        self.assertEqual(set(validate(old)), TOP_KEYS)

    def test_attachments_with_content_become_file_resources_keeping_id_name_and_usage(self):
        out = convert(self.old([self.ATT]))
        self.assertEqual(out["resources"][-1], {"id": "m1", "name": "旧添付", "kind": "file", "path": "memo.md", "usage": "用語の見本"})
        self.assertEqual(len(out["resources"]), 2, "既存のリソースへ足す。捨てない")
        # 元のファイル名は絶対パスではない: 保存・取り込みでは断り、読み取りは通し、有無の確認は「無い」（起動した場所を引かない）
        with self.assertRaisesRegex(ValueError, "絶対パス"):
            validate(self.old([self.ATT]))
        self.assertEqual(validate(self.old([self.ATT]), relative_paths=True)["resources"][-1]["path"], "memo.md")
        self.assertEqual(validate(self.old([self.ATT]), relative_paths=frozenset({"memo.md"}))["resources"][-1]["path"], "memo.md")
        self.assertEqual(unresolved_paths(out), frozenset({"memo.md"}))
        self.assertEqual(resource_presence(out["resources"]), [False, False])
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "memo.md").write_text("x", encoding="utf-8")
            cwd = Path.cwd()
            try:
                import os
                os.chdir(directory)
                self.assertEqual(resource_presence(out["resources"])[-1], False, "カレントに同名のファイルがあっても見つかったことにしない")
            finally:
                os.chdir(cwd)

    def test_the_original_file_name_is_never_turned_into_an_absolute_or_parent_path(self):
        for file in ("/etc/passwd", "../../secret.md", "C:\\Users\\x\\memo.md", "dir/memo.md"):
            row = {**self.ATT, "file": file}
            path = convert(self.old([row]))["resources"][-1]["path"]
            self.assertNotIn("/", path)
            self.assertNotIn("\\", path)
            self.assertNotIn("..", path)

    def test_a_missing_or_unsafe_file_name_is_explicitly_unresolved_and_never_the_display_name(self):
        bad = ("", "   ", "dir/", "dir\\", "/", "..", "a/..", ".", "C:x.md", "c:", "~", "~root", "\\\\host\\share\\")
        for file in bad:
            old = self.old([{**self.ATT, "name": "/unintended/private.md", "file": file}])
            out = convert(old)
            path = out["resources"][-1]["path"]
            self.assertEqual(path, dt.UNRESOLVED_PATH, repr(file))
            self.assertIn(path, unresolved_paths(out), repr(file))
            self.assertEqual(resource_presence(out["resources"])[-1], False)
            with self.assertRaisesRegex(ValueError, "絶対パス"):
                validate(old)   # 保存・取り込みでは通らない
            self.assertEqual(out["resources"][-1]["name"], "/unintended/private.md", "表示名は名前のまま")

    def test_conversion_is_idempotent(self):
        once = convert(self.old([self.ATT]))
        self.assertEqual(convert(once), once)
        self.assertEqual(convert(convert(self.old([]))), convert(self.old([])))

    def test_old_v1_v2_materials_reach_the_same_final_conversion(self):
        v2 = {"schema_version": 2, "id": "old", "brand_id": "b", "revision": 1, "name": "旧", "modes": ["pitch"], "description": "",
              "script": {"structure": "## 表紙"}, "drawing": {}, "references": [], "inputs": [], "checks": {},
              "materials": [{"id": "m9", "name": "見本", "file": "a.png", "url": "/x", "image": True, "usage": "手本", "sha256": "b" * 64,
                             "scope": "drawing"}]}
        out = convert(v2)
        self.assertEqual((out["schema_version"], "attachments" in out), (4, False))
        self.assertEqual(out["resources"], [{"id": "m9", "name": "見本", "kind": "file", "path": "a.png", "usage": "手本\n主に描画で使う。"}])

    def test_too_many_resources_after_conversion_stop_with_a_reason(self):
        old = self.old([{**self.ATT, "id": f"m{i}", "name": f"添付{i}"} for i in range(100)])
        with self.assertRaisesRegex(ValueError, "100件"):
            validate(old, relative_paths=True)

    def test_a_colliding_id_is_refused_not_renamed(self):
        old = self.old([{**self.ATT, "id": "kit"}])
        with self.assertRaisesRegex(ValueError, "ID"):
            validate(old, relative_paths=True)

    def test_markdown_exports_the_converted_resources_and_has_no_attachment_branch(self):
        text = to_markdown(self.old([self.ATT]))
        self.assertIn("旧添付", text)
        self.assertIn("memo.md", text)
        self.assertNotIn("attachments", text)
        # 取り込みは resources だけ（元のファイル名は絶対パスではないので、そのまま取り込むときは断る）
        with self.assertRaisesRegex(ValueError, "絶対パス"):
            from_markdown(text, brand_id="b")

    def test_compile_of_an_old_document_uses_the_effective_resources_and_the_current_compiler(self):
        brand = {"id": "b", "current_revision_id": "r", "config": copy.deepcopy(PRESET_BRAND_CONFIG), "assets": []}
        old = self.old([self.ATT])
        old["modes"] = ["presentation"]
        compiled = compile_document(old, brand, mode="presentation")
        self.assertEqual(compiled["compiler_version"], 5)
        self.assertNotIn("attachments", json.loads(compiled["files"]["template.json"]))
        self.assertIn("## 旧添付", compiled["files"]["writer.md"])
        self.assertIn("実行されない", compiled["files"]["writer.md"])
        self.assertEqual([r["exists"] for r in compiled["resources"]], [False, False])


class LegacyCompilerTests(unittest.TestCase):
    """コンパイラ4（旧）の再現は、添付を廃止する前のコードが出した値と一致する（新しい処理で再現しない）。"""

    def test_the_isolated_legacy_compiler_reproduces_the_recorded_old_output(self):
        from legacy_templates import BRAND, GOLDEN_CONTRACT, GOLDEN_PREPARATION_SHA, GOLDEN_WRITER_SHA, legacy_doc
        import hashlib
        from pathlib import PurePosixPath

        class PosixPath(PurePosixPath):   # 固定した見本のパスは POSIX の文字列。Windows でも同じ手順で絶対パスと判定させる
            def expanduser(self):
                return self
        for kind in ("empty", "full"):
            raw = legacy_doc(kind)
            raw_before = copy.deepcopy(raw)
            for mode in ("presentation", "pitch"):
                with mock.patch.object(legacy, "Path", PosixPath):
                    out = legacy.compile_document(raw, BRAND, mode=mode)
                self.assertEqual(out["compiler_version"], 4)
                self.assertEqual(out["contract_sha256"], GOLDEN_CONTRACT[(kind, mode)], (kind, mode))
                self.assertEqual(hashlib.sha256(out["files"]["writer.md"].encode()).hexdigest(), GOLDEN_WRITER_SHA[kind])
                self.assertEqual(hashlib.sha256(out["files"]["preparation.md"].encode()).hexdigest(), GOLDEN_PREPARATION_SHA[kind])
            self.assertEqual(raw, raw_before)

    def test_the_new_compiler_never_reproduces_the_old_output(self):
        from legacy_templates import BRAND, GOLDEN_CONTRACT, legacy_doc
        for kind in ("empty", "full"):
            out = compile_document(legacy_doc(kind), BRAND, mode="presentation")
            self.assertNotEqual(out["contract_sha256"], GOLDEN_CONTRACT[(kind, "presentation")])
            self.assertEqual(out["compiler_version"], dt.COMPILER_VERSION)
