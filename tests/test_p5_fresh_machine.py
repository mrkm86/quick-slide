"""P5: 「配った機体で動く」の通し（任意。`QUICK_SLIDE_FRESH_E2E=1` のときだけ走る。ネットワークから puppeteer-core と
Chrome の headless shell を落とすので、普段の `tests/run.sh` では飛ばす）。

プラグインを入れたときと同じく、リポジトリの丸写し（`.git` 抜き）を使い捨てのフォルダに置き、何も入っていない HOME
（`~/.quick-slide`・`~/.cache/html-video`・`~/.cache/puppeteer` が無い）と、本物の python・node・npm だけの PATH で:

1. `--doctor`（puppeteer-core と Chrome が足りないと言う）
2. `--setup --yes`（Quick Slide の置き場に puppeteer-core と Chrome を入れる）→ もう一度 `--doctor`
3. 写しのスキルの入口で、プレゼンを 1 冊（`--no-images`・偽物の台本係・`--wait`）→ API で PPTX と PDF を書き出す
4. ピッチを 1 冊（絵を描く codex は偽物）→ PPTX を書き出す
5. 使い捨ての HOME と写しの外を読んだ・書いた跡が無いか（記録・ログ・DB に本物の HOME の場所が出ないか、
   html-slide が Quick Slide の置き場の道具を使ったか）

かかった秒数と大きさは標準エラーに出し、`QUICK_SLIDE_FRESH_E2E_REPORT` を渡せばそのファイルに JSON で書く。
ポートは自動割当（`settings.json` の `server.port: 0`）にする（この機械で動いているほかのサーバとぶつけないため）。
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

from helpers import MANUSCRIPT, REPO, fake_codex_home, runnable, temp_dir, windows_base_env

TEXT_SUFFIXES = {".json", ".md", ".txt", ".yaml", ".yml", ".html", ".log", ".out", ".jsonl", ".db", ".pptx", ".pdf"}


def du(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            pass
    return total


@unittest.skipUnless(os.environ.get("QUICK_SLIDE_FRESH_E2E") == "1", "QUICK_SLIDE_FRESH_E2E=1 のときだけ（ネットワークを使う）")
class FreshMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temp_dir(self, "qs-fresh-")
        self.plugin = self.tmp / "plugin" / "quick-slide"
        shutil.copytree(REPO, self.plugin, ignore=shutil.ignore_patterns(".git", "__pycache__"), symlinks=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        (self.tmp / "tmp").mkdir()
        self.qs = self.home / ".quick-slide"
        (self.qs / "config").mkdir(parents=True)
        (self.qs / "config" / "settings.json").write_text(json.dumps({"server": {"port": 0},
                                                                      "browser": {"mode": "manual"}}), encoding="utf-8")
        dirs: list[str] = []
        for name in ("python3", "node", "npm"):
            p = shutil.which(name)
            self.assertIsNotNone(p, f"{name} が無い")
            if str(Path(p).parent) not in dirs:
                dirs.append(str(Path(p).parent))
        fakes = self.plugin / "tests" / "fakes"
        self.env = {
            **windows_base_env(),
            "HOME": str(self.home), "USERPROFILE": str(self.home), "PATH": os.pathsep.join(dirs + ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
            "LANG": "ja_JP.UTF-8", "TMPDIR": str(self.tmp / "tmp"),
            # 台本係と絵を描く codex は偽物（本物の LLM・Codex は呼ばない）
            "QUICK_SLIDE_CLAUDE": runnable(fakes / "fake_claude.py"), "QUICK_SLIDE_CODEX_TEXT": runnable(fakes / "refuse.py"),
            "FAKE_WRITER_LOG": str(self.tmp / "writer-calls"),
            # 偽物の台本係は --no-images を読まないので、画像の枠を置かせない（置くと絵を描く codex を探しに行く）
            "FAKE_WRITER_IMAGES": "0",
            "IMAGE_SLIDE_CODEX": runnable(self.plugin / "skills" / "image-slide" / "tests" / "fake_codex.py"),
            "FAKE_CODEX_LOG": str(self.tmp / "codex-calls"), "CODEX_HOME": str(fake_codex_home(self.home)),
            # PATH の外のよくある置き場は探させない（そこにある本物の claude・codex を見つけてしまう）
            "QUICK_SLIDE_CLI_DIRS": "",
        }
        self.setup_py = self.plugin / "skills" / "quick-slide" / "scripts" / "quick_slide_setup.py"
        self.start_py = self.plugin / "skills" / "quick-slide" / "scripts" / "quick_slide_start.py"
        self.report: dict = {"plugin_bytes": du(self.plugin)}
        self.addCleanup(self.stop)

    def stop(self) -> None:
        subprocess.run(["python3", "-m", "quick_slide.server", "--home", str(self.qs), "stop"], cwd=str(self.plugin),
                       env={**self.env, "PYTHONPATH": str(self.plugin / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
                       capture_output=True, timeout=60)

    def run_py(self, script: Path, *args: str, timeout: float = 1800) -> tuple[int, dict, str, float]:
        t0 = time.monotonic()
        r = subprocess.run(["python3", str(script), *args], env=self.env, capture_output=True, text=True,
                           timeout=timeout, cwd=str(self.tmp), stdin=subprocess.DEVNULL, encoding="utf-8")
        took = time.monotonic() - t0
        lines = r.stdout.strip().splitlines()
        return r.returncode, json.loads(lines[-1]) if lines else {}, r.stdout + r.stderr, took

    def api(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict, bytes, dict]:
        record = json.loads((self.qs / "runtime" / "server.json").read_text(encoding="utf-8"))
        conn = http.client.HTTPConnection(record["host"], record["port"], timeout=900)
        headers = {"Host": f"{record['host']}:{record['port']}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers.update({"Content-Type": "application/json", "Content-Length": str(len(data)),
                            "X-Quick-Slide-Session": record["session_token"]})
        conn.request(method, path, body=data, headers=headers)
        r = conn.getresponse()
        raw = r.read()
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        conn.close()
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            parsed = {}
        return r.status, parsed, raw, hdrs

    def export(self, deck_id: str, fmt: str) -> dict:
        t0 = time.monotonic()
        status, body, raw, _ = self.api("POST", f"/api/decks/{deck_id}/exports", {"format": fmt})
        self.assertEqual(status, 201, raw[:500])
        ex = body["export"]
        status, _, data, headers = self.api("GET", ex["download_url"])
        self.assertEqual(status, 200, data[:300])
        self.assertTrue(headers["content-disposition"].startswith("attachment;"))
        return {"bytes": len(data), "seconds": round(time.monotonic() - t0, 2), "head": data[:4],
                "filename": ex["filename"], "run_id": ex["run_id"]}

    def test_fresh_machine(self) -> None:
        real_home = str(Path.home())
        # 1. 足りないものを言う
        rc, out, text, took = self.run_py(self.setup_py, "--doctor", timeout=120)
        checks = {c["id"]: c for c in out["checks"]}
        self.assertEqual(rc, 4, text)
        self.assertEqual((checks["puppeteer-core"]["level"], checks["chrome"]["level"]), ("ng", "ng"), text)
        self.report["doctor_before"] = {"seconds": round(took, 2), "ng": sorted(k for k, c in checks.items() if c["level"] == "ng")}
        # 2. 入れる
        rc, out, text, took = self.run_py(self.setup_py, "--setup", "--yes")
        self.report["setup"] = {"seconds": round(took, 1), "rc": rc, "installed": out.get("installed")}
        engine = self.qs / "engines" / "html-slide"
        self.report["sizes"] = {"node_modules": du(engine / "node"), "chrome": du(engine / "chrome")}
        sys.stderr.write(text[-3000:] + "\n")
        self.assertTrue(all(d["ok"] for d in out["installed"]), text[-3000:])
        rc, out, text, took = self.run_py(self.setup_py, "--doctor", timeout=120)
        checks = {c["id"]: c for c in out["checks"]}
        self.report["doctor_after"] = {"seconds": round(took, 2), "rc": rc, "ready": out["ready"],
                                       "ng": sorted(k for k, c in checks.items() if c["level"] == "ng")}
        self.assertEqual((checks["puppeteer-core"]["source"], checks["chrome"]["source"]), ("quick-slide", "quick-slide"))
        self.assertTrue(out["ready"]["presentation"], text)
        # 3. プレゼンを 1 冊（画像なし）→ 書き出す
        manuscript = self.tmp / "manuscript.md"
        manuscript.write_text(MANUSCRIPT, encoding="utf-8")
        rc, out, text, took = self.run_py(self.start_py, "--manuscript-file", str(manuscript), "--title", "会議を半分にする",
                                          "--no-images", "--wait", "--wait-timeout", "900", "--no-open", "--runtime", "claude_code",
                                          timeout=1000)
        self.assertEqual((rc, out.get("result")), (0, "done"), text[-2000:])
        self.report["presentation"] = {"seconds": round(took, 1), "pages": len(out["pages"])}
        deck = out["deck_id"]
        pptx = self.export(deck, "pptx")
        pdf = self.export(deck, "pdf")
        self.assertEqual(pptx["head"], b"PK\x03\x04")
        self.assertEqual(pdf["head"], b"%PDF")
        self.report["presentation"].update(pptx={k: pptx[k] for k in ("bytes", "seconds", "filename")},
                                           pdf={k: pdf[k] for k in ("bytes", "seconds", "filename")})
        run_dir = self.qs / "decks" / deck / "runs" / pptx["run_id"]
        info = json.loads((run_dir / "deck-info.json").read_text(encoding="utf-8"))
        self.report["presentation"]["cache"] = info.get("cache")
        # 3b. 見本（docs/adr/0007-bundled-samples.md）: 最初の起動で 3 冊入っている。プレゼンの見本の PPTX は同梱して
        # いないので、書き出すときにこの機械で描いて作る。モーションの見本の HTML 1枚はそのまま配る
        status, body, raw, _ = self.api("GET", "/api/samples")
        self.assertEqual(status, 200, raw[:300])
        samples = {i["mode"]: i for i in body["items"]}
        self.assertEqual(sorted(samples), ["motion", "pitch", "presentation"])
        self.assertEqual({i["state"] for i in samples.values()}, {"present"})
        sample_pptx = self.export(samples["presentation"]["decks"][0]["id"], "pptx")
        self.assertEqual(sample_pptx["head"], b"PK\x03\x04")
        sample_html = self.export(samples["motion"]["decks"][0]["id"], "html")
        self.report["samples"] = {"presentation_pptx": {k: sample_pptx[k] for k in ("bytes", "seconds")},
                                  "motion_html": {k: sample_html[k] for k in ("bytes", "seconds")}}
        # 4. ピッチを 1 冊（絵は偽物の codex）→ 書き出す
        rc, out, text, took = self.run_py(self.start_py, "--manuscript-file", str(manuscript), "--title", "会議を半分にする（絵）",
                                          "--mode", "pitch", "--wait", "--wait-timeout", "900", "--no-open", "--new-deck", "--runtime", "claude_code",
                                          timeout=1000)
        self.assertEqual((rc, out.get("result")), (0, "done"), text[-2000:])
        self.report["pitch"] = {"seconds": round(took, 1), "pages": len(out["pages"])}
        pitch_pptx = self.export(out["deck_id"], "pptx")
        self.assertEqual(pitch_pptx["head"], b"PK\x03\x04")
        self.report["pitch"]["pptx"] = {k: pitch_pptx[k] for k in ("bytes", "seconds")}
        self.stop()
        # 5. 外を読んだ・書いた跡
        leaks = []
        for base in (self.home, self.plugin, self.tmp / "tmp"):
            # 写しの文書・コードは前からの置き場の名前（~/.cache/…）を書いているので、写しは本物の HOME の場所だけを見る
            needles = (real_home.encode(),) if base == self.plugin else (
                real_home.encode(), b"/.cache/html-video", b"/.cache/puppeteer")
            for p in base.rglob("*"):
                if not p.is_file() or p.is_symlink() or p.suffix not in TEXT_SUFFIXES:
                    continue
                if any(part in ("node_modules", "chrome") for part in p.relative_to(base).parts):
                    continue
                try:
                    data = p.read_bytes()
                except OSError:
                    continue
                for needle in needles:
                    if needle in data:
                        leaks.append(f"{p.relative_to(self.tmp)}: {needle.decode()}")
        self.report["leaks"] = leaks
        self.report["home_top"] = sorted(p.name for p in self.home.iterdir())
        self.report["caches"] = sorted(str(p.relative_to(self.home)) for p in (self.home / "Library" / "Caches").glob("*/*")) \
            if (self.home / "Library" / "Caches").is_dir() else []
        self.report["plugin_pycache"] = sorted(str(p.relative_to(self.plugin)) for p in self.plugin.rglob("__pycache__"))
        self.report["plugin_changed_bytes"] = du(self.plugin) - self.report["plugin_bytes"]
        sys.stderr.write("\nFRESH-E2E " + json.dumps(self.report, ensure_ascii=False, default=str) + "\n")
        if os.environ.get("QUICK_SLIDE_FRESH_E2E_REPORT"):
            Path(os.environ["QUICK_SLIDE_FRESH_E2E_REPORT"]).write_text(
                json.dumps(self.report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        self.assertEqual(leaks, [])
        self.assertTrue(str(info.get("cache", "")).startswith(str(self.home)), info.get("cache"))
        self.assertFalse((self.home / ".cache" / "html-video").exists())
        self.assertFalse((self.home / ".cache" / "puppeteer").exists())


if __name__ == "__main__":
    unittest.main()
