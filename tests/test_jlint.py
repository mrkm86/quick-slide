"""同梱の yomiyasu（日本語のリンター）と、台本係の検査への組み込み。

- 同梱の写し: ライセンス文が写しと同じフォルダにあり、README の版が固定の版であること
- 固定の試験: 小さな台本にかけて、NG は絵文字と前置きだけ・注意は文末の繰り返しだけ・ほかの決まり（「台帳」・太字）は出ないこと。
  上流の版を替えたときに、挙動の変化がここで分かる
- 組み込み: 絵文字入りの台本は 1 回目に NG で書き直しになり、最後の回では止めずに記録だけ残すこと
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import writer_calls

from quick_slide.writer import jlint
import test_worker as worker_tests

VENDOR = Path(__file__).resolve().parent.parent / "src" / "quick_slide" / "vendor" / "yomiyasu"


def deck() -> list[dict]:
    return [
        {"id": "cover", "type": "cover", "title": "会議を短くする", "subtitle": "提案", "icon": "check",
         "image": {"prompt": "✅ 画像の枠の指示は調べない", "alt": "🚫"}},
        {"id": "p-flow", "type": "cards", "headline": "3つのルールで短くする",
         "items": [{"title": "✅ 議題を前日に出す", "body": "台帳に書く", "icon": "🚫"},
                   {"title": "**25分**を**基本**に", "body": "**短く**・**強く**・**軽く**・**深く**"}]},
        {"id": "p-talk", "type": "statement", "headline": "まず結論です",
         "talk": ["結論から言うと、会議は短くできます。", "会議は短くします。", "議題は前日に出します。",
                  "決めたら閉じます。", "共有は文書で行います。"]},
    ]


class VendoredFilesTests(unittest.TestCase):
    def test_license_files_sit_next_to_the_code(self) -> None:
        for name in ("LICENSE", "UNICODE-LICENSE.txt", "yomiyasu_lint.py", "markdown_visibility.py", "README.md"):
            self.assertTrue((VENDOR / name).is_file(), name)
        self.assertIn("MIT License", (VENDOR / "LICENSE").read_text(encoding="utf-8"))
        self.assertIn("UNICODE LICENSE V3", (VENDOR / "UNICODE-LICENSE.txt").read_text(encoding="utf-8"))

    def test_readme_names_the_version_and_commit(self) -> None:
        # 版の値は固定しない（update.sh が別の版へ更新したとき、この試験だけで巻き戻らないように）。
        # 上流の決まりの変化は、下の「指摘の出方を固定した試験」が見る
        readme = (VENDOR / "README.md").read_text(encoding="utf-8")
        self.assertRegex(readme, r"- 版: v?[0-9]+(\.[0-9]+){1,3}\n")
        self.assertRegex(readme, r"- コミット: [0-9a-f]{40}")


# 名前の "bash" で起こすと、Windows は PATH より先に System32 を探し、WSL の bash.exe に当たる。見つけたパスで呼ぶ
BASH = shutil.which("bash")


class UpdateScriptTests(unittest.TestCase):
    @unittest.skipIf(BASH is None, "bash が無い")
    def test_a_tag_that_is_not_a_version_is_refused_before_anything_runs(self) -> None:
        for bad in ("v|;e${IFS}printf${IFS}injected;#", "main", "v1", "$(id)", "v1.1.1;ls", "--bogus"):
            r = subprocess.run([BASH, str(VENDOR / "update.sh"), bad], capture_output=True, text=True, errors="replace", timeout=30, encoding="utf-8")
            self.assertEqual(r.returncode, 2, (bad, r.stderr))


class DisplayedKeysTests(unittest.TestCase):
    def test_every_key_of_every_type_is_classified(self) -> None:
        scripts = str(Path(__file__).resolve().parent.parent / "skills" / "html-slide" / "scripts")
        sys.path.insert(0, scripts)
        try:
            import deck_types
        finally:
            sys.path.remove(scripts)
        keys = set().union(*[v[1] for v in deck_types.TYPES.values()]) | set(deck_types.COMMON_KEYS)
        unclassified = keys - jlint.VISIBLE_KEYS - jlint.HIDDEN_KEYS - jlint.NESTED_ONLY_KEYS
        self.assertEqual(unclassified, set(), "表示する・しないを jlint.py に足す")
        self.assertEqual(jlint.VISIBLE_KEYS & jlint.HIDDEN_KEYS, set())

    def test_nested_texts_of_each_shape_are_checked(self) -> None:
        slides = [
            {"id": "p-eq", "type": "equation", "headline": "式", "items": [{"title": "A"}], "result": {"title": "✅ 答え"}},
            {"id": "p-pr", "type": "pairs", "headline": "直す", "items": [{"label": "x", "before": "🚫 悪い", "after": "✅ 良い"}]},
            {"id": "p-tm", "type": "team", "headline": "人", "members": [{"name": "A", "role": "🎉 長"}]},
            {"id": "p-ln", "type": "line-chart", "headline": "推移", "labels": ["🙂 1月"], "series": [{"name": "🙂 A", "values": [1]}]},
            {"id": "p-mx", "type": "matrix", "headline": "位置", "x": {"label": "🙂 横"}, "items": []},
            {"id": "p-kp", "type": "kpi", "headline": "数", "items": [{"label": "a", "value": "1", "group": {"label": "🙂 自動", "tone": "a"}}]},
        ]
        found = jlint.check(slides).ng
        self.assertEqual(len(found), 8, found)
        for where in ("result.title", "items[0].before", "items[0].after", "members[0].role", "labels[0]", "series[0].name", "x.label",
                      "items[0].group.label"):
            self.assertTrue(any(where in ln for ln in found), (where, found))

    def test_table_align_is_not_displayed_text(self) -> None:
        self.assertIn("align", jlint.HIDDEN_KEYS)
        slide = {"id": "p-tb", "type": "table", "headline": "表", "columns": ["品目", "数"], "align": ["left", "right"], "rows": [["a", "1"]]}
        self.assertEqual(jlint.check([slide]).ng, [])

    def test_table_legend_text_is_checked_and_dim_rows_is_not(self) -> None:
        self.assertIn("legend", jlint.VISIBLE_KEYS)
        self.assertIn("dim_rows", jlint.HIDDEN_KEYS)
        slide = {"id": "p-tb", "type": "table", "headline": "表", "columns": ["品目", "数"], "dim_rows": [1], "rows": [["a", {"text": "🙂 1", "tone": "warn"}]],
                 "legend": [{"tone": "warn", "text": "✅ 注意"}, {"tone": "dim", "text": "見送り"}]}
        found = jlint.check([slide]).ng
        self.assertEqual(len(found), 2, found)
        for where in ("rows[0][1].text", "legend[0].text"):
            self.assertTrue(any(where in ln for ln in found), (where, found))

    def test_top_level_role_is_a_chapter_id_and_not_checked(self) -> None:
        self.assertEqual(jlint.check([{"id": "p-a", "type": "statement", "headline": "短い", "role": "🙂"}]).ng, [])


class RuleSelectionTests(unittest.TestCase):
    """上流の決まりのうち、使う 3 つだけが効くこと。"""

    def test_only_emoji_and_filler_are_ng(self) -> None:
        found = jlint.check(deck())
        self.assertEqual(len(found.ng), 2, found.ng)
        self.assertTrue(any("絵文字" in ln and "1枚目" not in ln and "2枚目（id p-flow）" in ln and "「✅」" in ln for ln in found.ng))
        self.assertTrue(any("3枚目（id p-talk）" in ln and "前置きの紋切り型" in ln for ln in found.ng))
        self.assertTrue(all(ln.startswith("NG: ") for ln in found.ng))

    def test_sentence_end_repetition_is_a_note_only(self) -> None:
        found = jlint.check(deck())
        self.assertEqual(len(found.notes), 1, found.notes)
        self.assertIn("3枚目（id p-talk）", found.notes[0])
        self.assertIn("「ます」", found.notes[0])
        self.assertTrue(found.notes[0].startswith("警告: "))

    def test_other_rules_and_unseen_keys_stay_silent(self) -> None:
        # 「台帳」（slop_vocabulary）・太字の多さ・image/icon の中の絵文字は、出さない
        joined = "\n".join(jlint.check(deck()).ng + jlint.check(deck()).notes)
        self.assertNotIn("台帳", joined)
        self.assertNotIn("太字", joined)
        self.assertNotIn("🚫", joined)
        self.assertNotIn("画像の枠", joined)

    def test_talk_emoji_is_ng(self) -> None:
        found = jlint.check([{"id": "p-a", "type": "statement", "headline": "短い", "talk": ["始めます 🎉"]}])
        self.assertEqual(len(found.ng), 1)
        self.assertIn("話す台本（talk の1つ目）", found.ng[0])

    def test_clean_deck_has_nothing(self) -> None:
        found = jlint.check([{"id": "p-a", "type": "statement", "headline": "会議は短くできる", "body": "25分を基本にする",
                              "talk": ["会議は短くできます。"]}])
        self.assertEqual((found.ng, found.notes), ([], []))

    def test_only_limits_to_the_changed_pages(self) -> None:
        found = jlint.check(deck(), only={"p-talk"})
        self.assertEqual(len(found.ng), 1)
        self.assertIn("p-talk", found.ng[0])

    def test_last_round_records_without_blocking(self) -> None:
        ng, notes = jlint.problems_and_notes(deck(), enforce=False)
        self.assertEqual(ng, [])
        self.assertEqual(len([n for n in notes if "絵文字" in n]), 1)
        self.assertTrue(all(n.startswith("警告: ") for n in notes))

    def test_a_crash_does_not_stop_the_writer(self) -> None:
        from unittest import mock
        with mock.patch.object(jlint, "_lint", side_effect=RuntimeError("boom")), self.assertLogs("quick_slide.jlint", "ERROR"):
            found = jlint.check(deck())
        self.assertEqual((found.ng, found.notes), ([], []))

    def test_markers_of_protected_regions_do_not_hide_text(self) -> None:
        for text in ("`✅` 採用", "![✅](x)", "[✅](http://a.b) 採用", "<!-- ✅ -->", "https://a.b/✅ 採用", "    ✅ 採用", "_www.example.com/✅"):   # 最後は Codex r12
            found = jlint.check([{"id": "p-a", "type": "statement", "headline": text}])
            self.assertEqual(len(found.ng), 1, (text, found.ng))

    def test_line_starts_do_not_become_markdown(self) -> None:
        for text in ("# 見出しの形", "- 箇条の形", "1. 番号の形", "> 引用の形", "---"):
            self.assertNotEqual(jlint._one_line(text), text)
        found = jlint.check([{"id": "p-a", "type": "statement", "headline": "- ✅ 採用"}])
        self.assertEqual(len(found.ng), 1)


class PipelineTests(unittest.TestCase):
    """台本係のループへの組み込み（偽物の台本係）。`test_worker.FlowTests` の部品（起動と投入）だけ借りる。"""

    start = worker_tests.FlowTests.start
    make = worker_tests.FlowTests.make

    def test_emoji_script_is_rewritten_once(self) -> None:
        fx, log = self.start(mode="emoji_first")
        body, run = self.make(fx)
        self.assertEqual(run["state"], "done")
        calls = writer_calls(log)
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("絵文字" in ln and "3枚目（id p-rules）" in ln for ln in calls[1]["ng"]), calls[1]["ng"])
        with fx.server.store_lock:
            rows = fx.store._all("SELECT version,check_rc FROM scripts WHERE deck_id=? ORDER BY version", (body["deck_id"],))
        self.assertEqual([(r["version"], r["check_rc"]) for r in rows], [(1, 2), (2, 0)])

    def test_last_round_does_not_stop_on_emoji(self) -> None:
        fx, log = self.start(mode="always_emoji", cfg=worker_tests.settings(max_check_rounds=2))
        body, run = self.make(fx)
        self.assertEqual(run["state"], "done", run.get("message"))
        self.assertEqual(len(writer_calls(log)), 2)
        with fx.server.store_lock:
            rows = fx.store._all("SELECT version,check_rc,check_notes_json FROM scripts WHERE deck_id=? ORDER BY version",
                                 (body["deck_id"],))
        self.assertEqual([(r["version"], r["check_rc"]) for r in rows], [(1, 2), (2, 0)])
        self.assertIn("絵文字", rows[1]["check_notes_json"], "最後の回は NG にせず、警告として残す")
        self.assertIn("警告", rows[1]["check_notes_json"])


if __name__ == "__main__":
    unittest.main()
