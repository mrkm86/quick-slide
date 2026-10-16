"""会話から呼ぶテンプレートの CLI（`skills/quick-slide/scripts/quick_slide_template.py`）。

使い捨ての置き場で本物のサーバを起こし（CLI が自分で起こす）、CLI を子プロセスで走らせる。保存の判断（同じ会話の上書き・
同文・競合）はサーバの仕事で、ここでは CLI が正しい口を正しい形で呼ぶことと、出力（最終行の JSON・終了コード・秘匿）を確かめる。
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import REPO

SCRIPT = REPO / "skills" / "quick-slide" / "scripts" / "quick_slide_template.py"
CONVERSATION_VARS = ("CLAUDE_CODE_SESSION_ID", "CLAUDECODE", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_HOME",
                     "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "QUICK_SLIDE_REPO", "PYTHONPATH")
BASE = "/api/settings/design-templates"


def markdown(name: str = "週報", structure: str = "## 1. 結論\n先に結論を書く", instructions: str = "図は少なめにする",
             description: str = "毎週の報告") -> str:
    return (f"---\nformat: quick-slide-template\nversion: 1\nname: {name}\ndescription: {description}\nmodes:\n  - presentation\n---\n\n"
            f"# 構成\n\n{structure}\n\n# 追加指示\n\n{instructions}\n")


class TemplateCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="qs-template-cli-")).resolve()
        cls.home = cls.tmp / "home"
        (cls.home / "config").mkdir(parents=True)
        (cls.home / "config" / "settings.json").write_text(json.dumps({"server": {"port": 0}}), encoding="utf-8")
        cls.env = {k: v for k, v in os.environ.items() if k not in CONVERSATION_VARS}
        cls.env.update({"QUICK_SLIDE_HOME": str(cls.home), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"})
        cls.outputs: list[str] = []
        rc, out, _, err = cls.cli("list")   # サーバを起こす（CLI が自分で起こすところも確かめる）
        assert rc == 0, (out, err)
        cls.record = json.loads((cls.home / "runtime" / "server.json").read_text(encoding="utf-8"))
        config = cls.api("GET", "/api/settings")["brand_default"]
        for name in ("同名", "同名", "ブランドA"):   # 最後に作ったブランドが既定になる
            cls.api("POST", "/api/settings/brands", {"name": name, "config": config, "assets": []})
        cls.catalog = cls.api("GET", "/api/settings")

    @classmethod
    def tearDownClass(cls) -> None:
        subprocess.run([sys.executable, "-m", "quick_slide.server", "--home", str(cls.home), "stop"],
                       env={**cls.env, "PYTHONPATH": str(REPO / "src")}, capture_output=True, timeout=60)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # --- 道具 ---
    @classmethod
    def api(cls, method: str, path: str, body: dict | None = None):
        host, port = cls.record["host"], cls.record["port"]
        conn = http.client.HTTPConnection(host, port, timeout=30)
        headers = {"Host": f"{host}:{port}", "X-Quick-Slide-Session": cls.record["session_token"]}
        raw = json.dumps(body).encode("utf-8") if body is not None else None
        if raw:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=raw, headers=headers)
        r = conn.getresponse()
        data = json.loads(r.read())
        conn.close()
        assert r.status < 300, (r.status, data)
        return data

    @classmethod
    def cli(cls, *args: str, conv: str | None = None, extra_env: dict | None = None):
        env = dict(cls.env)
        if conv:
            env["CLAUDE_CODE_SESSION_ID"] = conv
        env.update(extra_env or {})
        r = subprocess.run([sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=180,
                           cwd=str(cls.tmp), encoding="utf-8")
        cls.outputs.append(r.stdout + r.stderr)
        lines = r.stdout.strip().splitlines()
        return r.returncode, json.loads(lines[-1]) if lines else {}, r.stdout, r.stderr

    def file(self, name: str, text: str) -> str:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8", newline="\n")
        return str(path)

    def add(self, name: str, conv: str | None = "conv-1", **kw) -> dict:
        rc, out, _, err = self.cli("add", "--brand", "ブランドA", "--from", self.file(f"{name}.md", markdown(name, **kw)), conv=conv)
        self.assertEqual(rc, 0, (out, err))
        return out

    def versions(self, template_id: str) -> list:
        return self.api("GET", f"{BASE}/{template_id}/revisions")["revisions"]

    # --- 試験 ---
    def test_add_list_show_use_the_default_brand_and_the_last_line_is_json(self) -> None:
        out = self.add("一覧の確認")
        self.assertEqual((out["status"], out["action"], out["version"], out["based_on"]), ("ok", "created", 1, None))
        self.assertEqual(set(out) >= {"template_id", "revision_id", "changed_sections", "message"}, True)
        rc, listed, stdout, _ = self.cli("list")   # --brand を省く = 既定（ブランドA）
        self.assertEqual(rc, 0)
        self.assertEqual(len(stdout.strip().splitlines()), 1)
        self.assertIn(out["template_id"], [t["template_id"] for t in listed["templates"]])
        rc, shown, stdout, _ = self.cli("show", "--template", "一覧の確認")
        self.assertEqual(rc, 0)
        self.assertIn("# 構成", stdout)
        self.assertIn("先に結論を書く", stdout)
        self.assertNotIn("先に結論を書く", json.dumps(shown, ensure_ascii=False), "本文は JSON に入れない")
        self.assertEqual(shown["version"], 1)

    def test_the_default_brand_only_is_searched(self) -> None:
        other = next(b for b in self.catalog["brands"] if b["name"] == "同名")
        self.assertNotEqual(self.catalog["default_brand_id"], other["id"])
        rc, out, _, _ = self.cli("add", "--brand", other["id"], "--from", self.file("x.md", markdown("他のブランドの")))
        self.assertEqual(rc, 0, out)
        rc, out, _, _ = self.cli("show", "--template", "他のブランドの")   # 既定のブランドの中だけ
        self.assertEqual((rc, out["code"]), (2, "template_not_found"))
        rc, out, _, _ = self.cli("show", "--template", "他のブランドの", "--brand", other["id"])
        self.assertEqual(rc, 0)

    def test_export_refuses_to_overwrite_and_force_does(self) -> None:
        self.add("書き出し")
        dest = self.tmp / "out.md"
        dest.unlink(missing_ok=True)
        rc, out, _, _ = self.cli("export", "--template", "書き出し", "--out", str(dest))
        self.assertEqual((rc, out["status"], out["action"]), (0, "ok", "exported"))
        text = dest.read_bytes().decode("utf-8")
        self.assertIn("format: quick-slide-template", text)
        self.assertNotIn("\r", text)
        dest.write_text("手で直した", encoding="utf-8")
        rc, out, _, _ = self.cli("export", "--template", "書き出し", "--out", str(dest))
        self.assertEqual((rc, out["code"]), (2, "file_exists"))
        self.assertEqual(dest.read_text(encoding="utf-8"), "手で直した")
        rc, out, _, _ = self.cli("export", "--template", "書き出し", "--out", str(dest), "--force", "--version", "1")
        self.assertEqual((rc, out["version"]), (0, 1))
        self.assertIn("quick-slide-template", dest.read_text(encoding="utf-8"))
        # 書き出したものは、そのまま別のテンプレートとして登録し直せる
        rc, again, _, _ = self.cli("add", "--brand", "ブランドA", "--from", str(dest))
        self.assertEqual((rc, again["version"]), (0, 1))

    def test_update_new_version_same_conversation_overwrite_and_unchanged(self) -> None:
        t = self.add("更新", conv="conv-1")
        tid = t["template_id"]
        v2 = self.cli("update", "--template", "更新", "--from", self.file("u2.md", markdown("更新", instructions="図を増やす")), conv="conv-2")
        rc, out, _, err = v2
        self.assertEqual(rc, 0, (out, err))
        self.assertEqual((out["action"], out["version"], out["based_on"]), ("new_version", 2, 1), "別の会話の直しは新しい版")
        self.assertEqual(out["changed_sections"], ["追加指示"])
        self.assertIn("v2", out["message"])
        rc, out, _, _ = self.cli("update", "--template", tid, "--from", self.file("u3.md", markdown("更新", instructions="図をもっと増やす")), conv="conv-2")
        self.assertEqual((rc, out["action"], out["version"]), (0, "overwritten", 2), "同じ会話が作った版は上書き")
        self.assertEqual(len(self.versions(tid)), 2, "上書きは版を増やさない")
        rc, out, _, _ = self.cli("update", "--template", tid, "--from", self.file("u4.md", markdown("更新", instructions="図を減らす")), conv="conv-1")
        self.assertEqual((rc, out["action"], out["version"]), (0, "new_version", 3), "別の会話の直しは新しい版")
        rc, out, _, _ = self.cli("update", "--template", tid, "--from", self.file("u4.md", markdown("更新", instructions="図を減らす")), conv="conv-1")
        self.assertEqual((rc, out["action"], out["version"], out["changed_sections"]), (0, "unchanged", 3, []))
        self.assertEqual(len(self.versions(tid)), 3)
        rc, out, _, _ = self.cli("update", "--template", tid, "--from", self.file("u5.md", markdown("更新", instructions="図は使わない")),
                                 "--overwrite", conv="conv-9")
        self.assertEqual((rc, out["action"], out["version"]), (0, "overwritten", 3), "--overwrite は最新を必ず上書き")
        self.assertEqual(len(self.versions(tid)), 3)
        self.assertEqual(self.api("GET", f"{BASE}/{tid}")["document"]["instructions"], "図は使わない")

    def test_update_without_a_conversation_makes_a_new_version(self) -> None:
        t = self.add("識別子なし", conv=None)
        rc, out, _, _ = self.cli("update", "--template", t["template_id"], "--from", self.file("n.md", markdown("識別子なし", instructions="直す")))
        self.assertEqual((rc, out["action"], out["version"]), (0, "new_version", 2))

    def test_dry_run_writes_nothing(self) -> None:
        t = self.add("試し", conv="conv-1")
        tid = t["template_id"]
        before = json.dumps(self.versions(tid), sort_keys=True)
        src = self.file("d.md", markdown("試し", instructions="試しの直し"))
        for conv, want in (("conv-1", "overwritten"), ("conv-2", "new_version")):
            rc, out, _, _ = self.cli("update", "--template", tid, "--from", src, "--dry-run", conv=conv)
            self.assertEqual((rc, out["status"], out["action"]), (0, "dry_run", want))
            self.assertTrue(out["dry_run"])
            self.assertEqual(out["changed_sections"], ["追加指示"])
            self.assertEqual(json.dumps(self.versions(tid), sort_keys=True), before)
        rc, out, _, _ = self.cli("update", "--template", tid, "--from", self.file("same.md", markdown("試し")), "--dry-run", conv="conv-2")
        self.assertEqual((rc, out["action"]), (0, "unchanged"))
        self.assertEqual(json.dumps(self.versions(tid), sort_keys=True), before)

    def test_copy_and_delete(self) -> None:
        t = self.add("複製元")
        rc, out, _, _ = self.cli("copy", "--template", "複製元")
        self.assertEqual((rc, out["action"], out["version"]), (0, "copied", 1))
        self.assertNotEqual(out["template_id"], t["template_id"])
        names = [x["name"] for x in self.cli("list")[1]["templates"]]
        self.assertIn("複製元のコピー", names)
        rc, named, _, _ = self.cli("copy", "--template", t["template_id"], "--name", "別名の複製")
        self.assertEqual((rc, named["version"]), (0, 1))
        self.assertEqual(self.api("GET", f"{BASE}/{named['template_id']}")["document"]["name"], "別名の複製")
        self.assertEqual(len(self.versions(named["template_id"])), 1, "複製に名前を渡しても版は増えない")
        rc, out, _, _ = self.cli("delete", "--template", out["template_id"])
        self.assertEqual((rc, out["action"]), (0, "deleted"))
        self.assertNotIn("複製元のコピー", [x["name"] for x in self.cli("list")[1]["templates"]])
        rc, out, _, _ = self.cli("update", "--template", out["template_id"], "--from", self.file("z.md", markdown("複製元のコピー")))
        self.assertEqual((rc, out["code"]), (2, "template_deleted"))

    def test_ambiguous_names_stop_with_candidate_ids(self) -> None:
        same = [b["id"] for b in self.catalog["brands"] if b["name"] == "同名"]
        self.assertEqual(len(same), 2)
        rc, out, _, _ = self.cli("list", "--brand", "同名")
        self.assertEqual((rc, out["status"], out["code"]), (2, "error", "brand_ambiguous"))
        self.assertEqual(sorted(c["id"] for c in out["candidates"]), sorted(same))
        self.add("重複")
        self.add("重複")
        rc, out, _, _ = self.cli("show", "--template", "重複")
        self.assertEqual((rc, out["code"]), (2, "template_ambiguous"))
        self.assertEqual(len(out["candidates"]), 2)
        rc, out, _, _ = self.cli("update", "--template", "重複", "--from", self.file("a.md", markdown("重複", instructions="直す")))
        self.assertEqual((rc, out["code"]), (2, "template_ambiguous"))
        rc, out, _, _ = self.cli("show", "--template", out["candidates"][0]["id"])
        self.assertEqual(rc, 0)
        rc, out, _, _ = self.cli("list", "--brand", "そんなブランドはない")
        self.assertEqual((rc, out["code"]), (2, "brand_not_found"))

    def test_bad_input_and_exit_codes(self) -> None:
        rc, out, _, _ = self.cli("add", "--brand", "ブランドA", "--from", self.file("bad.md", "ただの文章"))
        self.assertEqual((rc, out["status"], out["code"]), (2, "error", "validation_error"))
        rc, out, _, _ = self.cli("add", "--brand", "ブランドA", "--from", str(self.tmp / "missing.md"))
        self.assertEqual((rc, out["code"]), (2, "file_unreadable"))
        rc, out, _, _ = self.cli("show", "--template", "名前のない", "--version", "1")
        self.assertEqual((rc, out["code"]), (2, "template_not_found"))
        self.add("版の確認")
        rc, out, _, _ = self.cli("show", "--template", "版の確認", "--version", "9")
        self.assertEqual((rc, out["status"]), (2, "error"))
        rc, out, _, _ = self.cli("list", "--repo", str(self.tmp / "no-such-repo"))
        self.assertEqual((rc, out["code"]), (3, "repo_invalid"))

    def test_output_never_carries_the_token_or_the_conversation_key(self) -> None:
        self.add("秘匿", conv="conv-secret-value")
        self.cli("update", "--template", "秘匿", "--from", self.file("s.md", markdown("秘匿", instructions="直す")), conv="conv-secret-value")
        self.cli("list")
        joined = "\n".join(self.outputs)
        self.assertNotIn(self.record["session_token"], joined)
        self.assertNotIn("conv-secret-value", joined)
        self.assertNotIn("conv-1", joined)

    def test_the_cli_runs_without_site_packages(self) -> None:
        """`python -S`（site-packages なし）でも動く = 標準ライブラリだけ。共通部品に非標準の import が入ると、ここで落ちる。"""
        r = subprocess.run([sys.executable, "-S", str(SCRIPT), "list"], env=self.env, capture_output=True, text=True,
                           timeout=120, cwd=str(self.tmp), encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["status"], "ok")

    def test_argument_errors_are_json_and_never_quote_the_values(self) -> None:
        secret = "SECRET-VALUE-123"
        cases = [("list", "--runtime", secret), ("list", "--conversation-key", secret, "--bogus"), (secret,),
                 ("show", "--template", "x", "--version", secret), ("show",), ("update", "--template", "x", secret),
                 ("list", "--server-timeout", secret), ()]
        for argv in cases:
            rc, out, stdout, stderr = self.cli(*argv)
            self.assertEqual((rc, out.get("status"), out.get("code")), (2, "error", "bad_arguments"), argv)
            self.assertNotIn(secret, stdout + stderr, argv)

    def test_dry_run_then_another_save_makes_the_real_update_conflict(self) -> None:
        t = self.add("計画のあと", conv="conv-1")
        tid = t["template_id"]
        src = self.file("p.md", markdown("計画のあと", instructions="計画の直し"))
        rc, plan, _, _ = self.cli("update", "--template", tid, "--from", src, "--dry-run", conv="conv-1")
        self.assertEqual(rc, 0)
        self.assertEqual(plan["expected_revision_id"], t["revision_id"])
        # 計画のあとに、別の画面（API）が保存する
        self.api("POST", f"{BASE}/{tid}", {"markdown": markdown("計画のあと", instructions="割り込み"), "mode": "overwrite",
                                           "expected_revision_id": t["revision_id"]})
        before = json.dumps(self.versions(tid), sort_keys=True)
        for extra in ((), ("--overwrite",)):
            rc, out, _, _ = self.cli("update", "--template", tid, "--from", src, "--expected-revision", plan["expected_revision_id"],
                                     *extra, conv="conv-1")
            self.assertEqual((rc, out["status"], out["code"]), (4, "error", "conflict"), extra)
        self.assertEqual(json.dumps(self.versions(tid), sort_keys=True), before)
        self.assertEqual(self.api("GET", f"{BASE}/{tid}")["document"]["instructions"], "割り込み")
        rc, out, _, _ = self.cli("delete", "--template", tid, "--expected-revision", plan["expected_revision_id"])
        self.assertEqual((rc, out["code"]), (4, "conflict"))
        rc, out, _, _ = self.cli("copy", "--template", tid, "--expected-revision", plan["expected_revision_id"])
        self.assertEqual((rc, out["code"]), (4, "conflict"))

    def test_another_session_overwriting_ends_the_conversations_overwrite_right(self) -> None:
        t = self.add("資格", conv="conv-1")
        tid = t["template_id"]
        self.api("POST", f"{BASE}/{tid}", {"markdown": markdown("資格", instructions="別の画面の上書き"), "mode": "overwrite",
                                           "expected_revision_id": t["revision_id"]})   # 会話の識別子なし
        rc, out, _, _ = self.cli("update", "--template", tid, "--from", self.file("q.md", markdown("資格", instructions="会話の直し")),
                                 conv="conv-1")
        self.assertEqual((rc, out["action"], out["version"]), (0, "new_version", 2))

    def test_names_and_ids_stay_inside_the_brand(self) -> None:
        other = next(b for b in self.catalog["brands"] if b["name"] == "同名")
        default_id = self.catalog["default_brand_id"]
        mine = self.add("二重")
        rc, theirs, _, _ = self.cli("add", "--brand", other["id"], "--from", self.file("t.md", markdown("二重", instructions="他")))
        self.assertEqual(rc, 0, theirs)
        # 名前
        rc, out, _, _ = self.cli("update", "--template", "二重", "--from", self.file("t2.md", markdown("二重", instructions="既定側")), conv="conv-2")
        self.assertEqual((rc, out["template_id"]), (0, mine["template_id"]))
        rc, out, _, _ = self.cli("update", "--template", "二重", "--brand", other["id"],
                                 "--from", self.file("t3.md", markdown("二重", instructions="他側")), conv="conv-2")
        self.assertEqual((rc, out["template_id"]), (0, theirs["template_id"]))
        # ID
        rc, out, _, _ = self.cli("update", "--template", theirs["template_id"], "--from", self.file("t4.md", markdown("二重", instructions="x")))
        self.assertEqual((rc, out["code"]), (2, "template_other_brand"))
        rc, out, _, _ = self.cli("delete", "--template", theirs["template_id"])
        self.assertEqual((rc, out["code"]), (2, "template_other_brand"))
        rc, out, _, _ = self.cli("update", "--template", mine["template_id"], "--brand", other["id"],
                                 "--from", self.file("t5.md", markdown("二重", instructions="x")))
        self.assertEqual((rc, out["code"]), (2, "template_not_found"))
        rc, out, _, _ = self.cli("show", "--template", theirs["template_id"], "--brand", other["id"])
        self.assertEqual(rc, 0)
        rc, out, _, _ = self.cli("show", "--template", mine["template_id"], "--brand", default_id)
        self.assertEqual(rc, 0)
        self.assertEqual(self.api("GET", f"{BASE}/{theirs['template_id']}")["document"]["instructions"], "他側")

    def test_copy_name_reaches_the_server_even_when_empty(self) -> None:
        t = self.add("名前の確認")
        for bad in ("", "   "):
            rc, out, _, _ = self.cli("copy", "--template", t["template_id"], "--name", bad)
            self.assertEqual((rc, out["status"], out["code"]), (2, "error", "validation_error"), repr(bad))
        rc, out, _, _ = self.cli("copy", "--template", t["template_id"], "--name", "ふつうの名前")
        self.assertEqual(rc, 0)
        self.assertEqual(self.api("GET", f"{BASE}/{out['template_id']}")["document"]["name"], "ふつうの名前")

    def test_the_start_script_still_reuses_the_same_helpers(self) -> None:
        sys.path.insert(0, str(SCRIPT.parent))
        keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True   # 配る物の中に __pycache__ を残さない
        try:
            import quick_slide_common
            import quick_slide_start
        finally:
            sys.dont_write_bytecode = keep
            sys.path.remove(str(SCRIPT.parent))
        self.assertIs(quick_slide_start.request, quick_slide_common.request)
        self.assertIs(quick_slide_start.Failure, quick_slide_common.Failure)


if __name__ == "__main__":
    unittest.main()
