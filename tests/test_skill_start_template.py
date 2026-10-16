"""会話の入口（`quick_slide_start.py`）のデザインテンプレートの指定（PR 5）。

形の誤り（版だけ・style-id との併用・モーション）は、サーバを起こす前に CLI が断る。名前・ID の解決の最終の判断はサーバで、
曖昧なときは候補の ID を返して止まる。受け付けた答えは「生成できる状態」と言わず、台本を書く前に止まる理由まで返す。
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

from helpers import FAKES, MANUSCRIPT, REPO, runnable

from quick_slide import design_templates as dt

SCRIPT = REPO / "skills" / "quick-slide" / "scripts" / "quick_slide_start.py"
CONVERSATION_VARS = ("CLAUDE_CODE_SESSION_ID", "CLAUDECODE", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_HOME",
                     "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "QUICK_SLIDE_REPO", "PYTHONPATH")


class StartTemplateCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="qs-start-template-")).resolve()
        cls.home = cls.tmp / "home"
        (cls.home / "config").mkdir(parents=True)
        (cls.home / "config" / "settings.json").write_text(json.dumps({"server": {"port": 0}}), encoding="utf-8")
        cls.manuscript = cls.tmp / "manuscript.md"
        cls.manuscript.write_text(MANUSCRIPT, encoding="utf-8")
        refuse = runnable(FAKES / "refuse.py")
        cls.env = {k: v for k, v in os.environ.items() if k not in CONVERSATION_VARS}
        cls.env.update({"QUICK_SLIDE_HOME": str(cls.home), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1",
                        "QUICK_SLIDE_CLAUDE": refuse, "QUICK_SLIDE_CODEX_TEXT": refuse})
        rc, out, _, err = cls.cli("--start-server")
        assert rc == 0, (out, err)
        cls.record = json.loads((cls.home / "runtime" / "server.json").read_text(encoding="utf-8"))
        config = cls.api("GET", "/api/settings")["brand_default"]
        cls.brand = cls.api("POST", "/api/settings/brands", {"name": "試験のブランド", "config": config, "assets": []})["item"]
        for name in ("週報", "重複", "重複"):
            cls.api("POST", "/api/settings/design-templates",
                    {"brand_id": cls.brand["id"], "document": cls.document(name), "origin_runtime": "cli"})
        cls.catalog = cls.api("GET", f"/api/settings/design-templates?brand_id={cls.brand['id']}")["templates"]

    @classmethod
    def tearDownClass(cls) -> None:
        subprocess.run([sys.executable, "-m", "quick_slide.server", "--home", str(cls.home), "stop"],
                       env={**cls.env, "PYTHONPATH": str(REPO / "src")}, capture_output=True, timeout=60)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def document(name: str) -> dict:
        d = dt.new_document("placeholder", "placeholder", name)
        d.update(structure="## 1. 結論\n先に結論を書く", instructions="図は少なめにする", modes=["presentation"])
        return d

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
    def cli(cls, *args: str):
        r = subprocess.run([sys.executable, str(SCRIPT), *args], env=cls.env, capture_output=True, text=True, timeout=180,
                           cwd=str(cls.tmp), encoding="utf-8")
        lines = r.stdout.strip().splitlines()
        return r.returncode, json.loads(lines[-1]) if lines else {}, r.stdout, r.stderr

    def start(self, *extra: str):
        return self.cli("--manuscript-file", str(self.manuscript), "--runtime", "claude_code", "--no-open", *extra)

    def test_malformed_selections_are_refused_before_the_server_is_called(self) -> None:
        for extra, code in ((("--design-template-version", "2"), "design_template_required"),
                            (("--design-template", "週報", "--style-id", "x"), "design_template_style_conflict"),
                            (("--design-template", "週報", "--mode", "motion"), "template_mode_unsupported"),
                            (("--design-template", "週報", "--design-template-version", "0"), "design_template_version_invalid")):
            rc, out, _, _ = self.start(*extra)
            self.assertEqual((rc, out["status"], out["code"]), (2, "error", code), extra)

    def test_dry_run_carries_the_selection_and_never_the_manuscript(self) -> None:
        rc, out, stdout, stderr = self.start("--design-template", "週報", "--design-template-version", "1", "--dry-run")
        self.assertEqual(rc, 0, (out, stderr))
        attrs = out["payload"]["deck_attributes"]
        self.assertEqual((attrs["design_template"], attrs["design_template_version"]), ("週報", 1))
        self.assertNotIn("11.5", stdout + stderr)

    def test_an_accepted_template_run_is_not_shown_as_ready(self) -> None:
        rc, out, stdout, stderr = self.start("--design-template", "週報")
        self.assertEqual(rc, 0, (out, stderr))
        self.assertEqual((out["status"], out["generation_ready"]), ("accepted", False))
        self.assertEqual(out["design_template"]["name"], "週報")
        self.assertIn("生成できる状態にはなっていません", out["message"])
        self.assertNotIn("11.5", stdout + stderr, "原稿の本文を出さない")

    def test_wait_reports_the_reason_it_stopped(self) -> None:
        rc, out, _, _ = self.start("--design-template", "週報", "--wait", "--wait-timeout", "120", "--new-deck")
        self.assertEqual((rc, out["status"], out["generation_ready"]), (4, "failed", False))
        self.assertIn("素材の準備がまだ使えない", out["result_message"])
        self.assertEqual(out["pages"], [])

    def test_same_names_stop_with_candidate_ids_and_an_id_decides(self) -> None:
        rc, out, _, _ = self.start("--design-template", "重複")
        self.assertEqual((rc, out["status"], out["code"]), (2, "error", "template_ambiguous"))
        ids = sorted(r["template_id"] for r in self.catalog if r["name"] == "重複")
        self.assertEqual(sorted(c["id"] for c in out["details"]["candidates"]), ids)
        rc, out, _, _ = self.start("--design-template", ids[0])
        self.assertEqual((rc, out["status"]), (0, "accepted"))

    def test_an_unknown_name_or_version_is_a_clear_input_error(self) -> None:
        rc, out, _, _ = self.start("--design-template", "ない名前")
        self.assertEqual((rc, out["code"]), (2, "template_not_found"))
        rc, out, _, _ = self.start("--design-template", "週報", "--design-template-version", "9")
        self.assertEqual((rc, out["code"]), (2, "template_version_not_found"))


if __name__ == "__main__":
    unittest.main()
