"""P4: 設定をエンジンへ。

- 頭: 台本係の LLM・ワーカーの数・検査の回数は、仕事のたびに settings.json を読み直す（再起動は要らない）。
  待ち受けだけは再起動が要り、手で直したときも `restart_required` が立つ
- 本体: ブランド → テーマの YAML（`palette.engine_theme`）・`--logo`・`--logo-white`・`--logo-position`・`--watermark`・
  台本の `footer`・`page_numbers`。スタイル → `--style`・`--image-look`・`--style-note`・台本係への方針と使ってよい型
- 設定を変えたら次に描く回から効く（デッキはブランド・スタイルの新しい改訂へ進む）
- ブランドの編集画面の保存で、ロゴ以外の役割の画像を消さない

エンジンはここでは偽物（`tests/fakes/fake_engine.py`。受けた引数を記録する）。本物のエンジンを通すのは
`test_p4_engines.py`。
"""

from __future__ import annotations

import copy
import io
import json
import time
import unittest
from pathlib import Path

from helpers import fake_tools, intake_payload, png_bytes, start_server, wait_until_idle, writer_calls
from PIL import Image
from test_worker import fake_skills_dir

from quick_slide import palette
from quick_slide.engines import ENGINES
from quick_slide.ids import PRESET_STYLE_INFOGRAPHIC_ID, PRESET_STYLE_STANDARD_ID, PRESET_STYLE_VISUAL_ID
from quick_slide.paths import SKILLS_DIR
from quick_slide.render import look as LOOK
from quick_slide.settings import Settings, WorkerSettings
from quick_slide.writer.prompt import build_prompt


def engine_calls(log: Path) -> list[dict]:
    d = log / "engine"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))] if d.is_dir() else []


def arg(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


class Base(unittest.TestCase):
    def start(self, *, mode: str = "ok", cfg: Settings | None = None, real_engines: bool = False):
        tools, log = fake_tools(self, mode=mode, skills_dir=None if real_engines else fake_skills_dir(self))
        tools.extra_env.update({"FAKE_ENGINE_SLEEP": "0.01", "FAKE_ENGINE_LOG": str(log / "engine")})
        self.fx = start_server(self, tools=tools, settings=cfg or Settings(worker=WorkerSettings(parallel=1)))
        self.log = log
        return self.fx, log

    def upload(self, size=(400, 100), color=(20, 40, 200, 255), kind="PNG") -> str:
        if kind == "PNG":
            data = png_bytes(*size, color=color)
        else:
            buf = io.BytesIO()
            Image.new("RGB", size, color[:3]).save(buf, format=kind)
            data = buf.getvalue()
        r = self.fx.request("POST", "/api/settings/images", raw_body=data, session=True)
        self.assertEqual(r.status, 201, r.body)
        return r.json()["asset_id"]

    def brand(self, *, primary="#C8102E", accent="#1D4ED8", corner="bottom-left", footer="社外秘・試験の資料",
              page_numbers=True, watermark="配布禁止", logo=True, white=True, extra_assets=()) -> dict:
        assets = []
        if logo:
            assets.append({"asset_id": self.upload(), "role": "logo_transparent", "label": "ロゴ"})
        if white:
            assets.append({"asset_id": self.upload(color=(255, 255, 255, 255)), "role": "logo_white", "label": "白いロゴ"})
        assets += list(extra_assets)
        config = {"schema_version": 1, "colors": {"primary": primary, "accent": accent},
                  "logo": {"variant": "transparent" if logo else "none", "placement": {"corner": corner}},
                  "footer": {"text": footer, "style": {"position": "footer_left"}},
                  "format": {"page_numbers": page_numbers, "watermark": watermark}}
        r = self.fx.post("/api/settings/brands", {"name": "試験のブランド", "config": config, "assets": assets})
        self.assertEqual(r.status, 201, r.body)
        return r.json()["item"]

    def copy_style(self, source_id: str, name: str, **config) -> dict:
        catalog = self.fx.get("/api/settings").json()
        src = next(s for s in catalog["styles"] if s["id"] == source_id)
        r = self.fx.post("/api/settings/styles", {"source_id": source_id, "name": name, "config": config,
                                                  "expected_revision_id": src["current_revision_id"]})
        self.assertEqual(r.status, 201, r.body)
        return r.json()["item"]

    def make(self, **attrs) -> tuple[dict, dict]:
        r = self.fx.post("/api/intakes", intake_payload(**attrs))
        self.assertEqual(r.status, 201, r.body)
        body = r.json()
        run = wait_until_idle(self.fx, body["deck_id"])
        return body, run

    def write_settings(self, data: dict) -> None:
        path = self.fx.root.settings_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")


# --- 頭: 設定の読み直し ------------------------------------------------------------------------------------------

class HotSettingsTests(Base):
    def test_the_writer_setting_is_read_again_for_each_job(self) -> None:
        self.start()
        self.write_settings({"writer": "codex"})
        _, run = self.make(runtime="claude_code")
        self.assertEqual(run["state"], "done", run["message"])
        self.assertEqual((run["record"]["settings"]["writer_setting"], run["args"]["writer"]), ("codex", "codex"))
        self.write_settings({"writer": "claude"})
        _, run = self.make(runtime="codex", title="2 冊目")
        self.assertEqual((run["record"]["settings"]["writer_setting"], run["args"]["writer"]), ("claude", "claude"))
        self.assertEqual([c["kind"] for c in writer_calls(self.log)], ["codex", "claude"])

    def test_worker_settings_take_effect_without_a_restart(self) -> None:
        self.start(mode="always_invalid", cfg=Settings(worker=WorkerSettings(parallel=1, max_check_rounds=3)))
        self.assertEqual(self.fx.get("/api/health").json()["worker"]["threads"], 1)
        self.write_settings({"worker": {"parallel": 3, "max_check_rounds": 1}})
        h = self.fx.get("/api/health").json()
        self.assertEqual((h["worker"]["parallel"], h["worker"]["threads"], h["settings"]["parallel"]), (3, 3, 3))
        _, run = self.make()
        self.assertEqual(run["state"], "failed")
        self.assertEqual(len(writer_calls(self.log)), 1, "検査の回数も読み直す（1 回で止める）")
        self.write_settings({"worker": {"parallel": 1}})
        h = self.fx.get("/api/health").json()
        self.assertEqual((h["worker"]["parallel"], h["worker"]["threads"]), (1, 3), "減らすときはスレッドを休ませる")
        self.assertFalse(h["settings"]["restart_required"], "待ち受けのほかは再起動が要らない")

    def test_a_broken_file_keeps_the_previous_settings(self) -> None:
        self.start()
        self.write_settings({"writer": "codex"})
        self.fx.get("/api/health")
        self.fx.root.settings_path.write_text("{ 壊れた", encoding="utf-8")
        h = self.fx.get("/api/health").json()
        self.assertEqual(h["settings"]["writer"], "codex")
        self.assertIn("settings.json", h["settings"]["error"])
        server = self.fx.get("/api/settings").json()["server"]
        self.assertFalse(server["restart_required"])
        self.assertIn("settings.json", server["settings_error"])

    def test_editing_the_listen_settings_by_hand_asks_for_a_restart(self) -> None:
        self.start()
        self.assertFalse(self.fx.get("/api/health").json()["settings"]["restart_required"])
        self.write_settings({"server": {"host": "100.100.1.23", "host_names": ["my-mac"]}})
        server = self.fx.get("/api/settings").json()["server"]
        self.assertTrue(server["restart_required"])
        self.assertEqual((server["host"], server["expected_url"]), ("100.100.1.23", "http://my-mac:7801"))
        self.assertTrue(self.fx.get("/api/health").json()["settings"]["restart_required"])
        self.write_settings({"server": {"host": "127.0.0.1"}, "writer": "codex"})
        self.assertFalse(self.fx.get("/api/health").json()["settings"]["restart_required"], "台本係だけなら要らない")


# --- 本体: ブランド → テーマ -------------------------------------------------------------------------------------

class ThemeMappingTests(unittest.TestCase):
    def theme(self, colors: dict) -> dict:
        return palette.engine_theme(palette.snapshot_colors(colors))

    def test_the_default_brand_is_the_engine_default_theme(self) -> None:
        from quick_slide.store.presets import PRESET_BRAND_CONFIG

        engine_default = (SKILLS_DIR / "html-slide" / "themes" / "default.yaml").read_text(encoding="utf-8")
        theme = self.theme(PRESET_BRAND_CONFIG["colors"])
        self.assertEqual(theme, {"background": "#FFFFFF", "ink": "#404040", "primary": "#0DB4F2"})
        for key, value in theme.items():
            self.assertIn(f'{key}: "{value.lower()}"', engine_default.lower(), key)

    def test_accent_is_passed_only_when_it_differs_and_info_is_not_passed(self) -> None:
        theme = self.theme({"primary": "#C8102E", "accent": "#1D4ED8", "info": "#0E7490"})
        self.assertEqual(theme, {"background": "#FFFFFF", "ink": "#404040", "primary": "#C8102E", "accent": "#1D4ED8"})
        self.assertNotIn("info", palette.theme_yaml(theme))
        self.assertNotIn("accent", self.theme({"primary": "#C8102E", "accent": "#c8102e"}))

    def test_overridden_base_and_surface(self) -> None:
        theme = self.theme({"primary": "#1F6B4A", "accent": "#1F6B4A", "base": "#FAF7F0", "surface": "#F1ECE1"})
        self.assertEqual((theme["background"], theme["surface"]), ("#FAF7F0", "#F1ECE1"))
        dark = self.theme({"primary": "#F5A623", "accent": "#399DD1", "base": "#101418"})
        self.assertEqual(dark["background"], "#101418")
        self.assertGreaterEqual(palette._contrast_ratio(dark["ink"], dark["background"]), 7.0, "暗い地には明るい文字")
        self.assertNotIn("surface", self.theme({"primary": "#1F6B4A", "accent": "#1F6B4A"}), "算出の面の色は渡さない")

    def test_the_yaml_is_accepted_by_both_engines(self) -> None:
        import subprocess
        import sys
        import tempfile

        with tempfile.TemporaryDirectory(prefix="quick-slide-theme-") as tmp:
            tmp = Path(tmp)
            (tmp / "theme.yaml").write_text(palette.theme_yaml(self.theme(
                {"primary": "#C8102E", "accent": "#1D4ED8", "surface": "#FFF7F7"})), encoding="utf-8")
            (tmp / "deck.json").write_text(json.dumps({"slides": [
                {"type": "statement", "headline": "テーマを確かめる"}]}, ensure_ascii=False), encoding="utf-8")
            for skill, entry in (("html-slide", "html_slide.py"), ("image-slide", "image_slide.py")):
                r = subprocess.run([sys.executable, str(SKILLS_DIR / skill / "scripts" / entry), "check", "--script",
                                    str(tmp / "deck.json"), "--theme", str(tmp / "theme.yaml")],
                                   capture_output=True, text=True, timeout=120,
                                   env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"})
                self.assertEqual(r.returncode, 0, f"{skill}: {r.stderr[-800:]}")


# --- 本体: エンジンの引数・台本の一番外 -----------------------------------------------------------------------------

class EngineArgsTests(Base):
    def test_presentation_gets_theme_logo_corner_watermark_and_the_footer_goes_into_engine_json(self) -> None:
        self.start()
        brand = self.brand()
        body, run = self.make(mode="presentation", brand_id=brand["id"])
        self.assertEqual(run["state"], "done", run["message"])
        calls = engine_calls(self.log)
        deck = next(c["argv"] for c in calls if c["argv"][0] == "deck")
        check = next(c["argv"] for c in calls if c["argv"][0] == "check")
        look = run["args"]["look"]
        self.assertEqual(arg(deck, "--theme"), look["theme"])
        self.assertTrue(Path(look["theme"]).is_file())
        self.assertIn('primary: "#C8102E"', Path(look["theme"]).read_text(encoding="utf-8"))
        self.assertEqual(arg(deck, "--logo-position"), "bottom-left")
        self.assertEqual(Path(arg(deck, "--logo")).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(arg(deck, "--watermark"), "配布禁止")
        self.assertNotIn("--logo-white", deck, "白いロゴはピッチだけ")
        self.assertNotIn("--logo", check, "html-slide の検査はロゴを受けない")
        self.assertEqual(arg(check, "--theme"), look["theme"], "検査もテーマで（読めない色は検査で分かる）")
        # フッタ・ページ番号はエンジンに渡す台本に入れ、台本の版（台本係が書いたもの）は変えない
        engine_json = json.loads(self.fx.root.engine_script_path(body["deck_id"]).read_text(encoding="utf-8"))
        self.assertEqual((engine_json["footer"], engine_json["page_numbers"]), ("社外秘・試験の資料", True))
        with self.fx.server.store_lock:
            stored = self.fx.store.load_script(self.fx.store.script(run["script_id"]))
        self.assertNotIn("footer", stored)
        self.assertNotIn("page_numbers", stored)
        # 再現の記録: どのブランドのどの改訂・テーマの中身・ロゴの sha256・エンジンの引数
        rec = run["record"]["look"]
        self.assertEqual((rec["brand"]["id"], rec["brand"]["revision"]), (brand["id"], 1))
        self.assertEqual(rec["theme"]["primary"], "#C8102E")
        self.assertEqual(len(rec["logo"]["sha256"]), 64)
        self.assertEqual(run["record"]["argv"][0][2:], deck, "記録の argv はエンジンが受けた引数そのもの")

    def test_pitch_gets_the_white_logo_and_the_style_note_and_checks_with_the_brand(self) -> None:
        self.start()
        brand = self.brand(corner="top-right")
        style = self.copy_style(PRESET_STYLE_VISUAL_ID, "試験のビジュアル", style_note="落ち着いた色で", image_look="illustration")
        body, run = self.make(mode="pitch", brand_id=brand["id"], style_id=style["id"], style_note="余白を広く")
        self.assertEqual(run["state"], "done", run["message"])
        calls = engine_calls(self.log)
        deck = next(c["argv"] for c in calls if c["argv"][0] == "deck")
        check = next(c["argv"] for c in calls if c["argv"][0] == "check")
        self.assertEqual(arg(deck, "--logo-position"), "top-right")
        self.assertTrue(arg(deck, "--logo-white"))
        self.assertEqual(arg(deck, "--style"), "visual")
        self.assertEqual(arg(deck, "--image-look"), "illustration")
        self.assertEqual(arg(deck, "--style-note"), "余白を広く。落ち着いた色で", "会話の一言が先・スタイルの一言を足す")
        self.assertEqual(arg(check, "--logo"), arg(deck, "--logo"), "image-slide の検査はロゴを受ける（帯の頼み方が変わる）")

    def test_a_white_logo_with_another_shape_is_not_passed(self) -> None:
        self.start()
        brand = self.brand(white=False, extra_assets=[
            {"asset_id": self.upload(size=(100, 100), color=(255, 255, 255, 255)), "role": "logo_white", "label": "白"}])
        _, run = self.make(mode="pitch", brand_id=brand["id"])
        deck = next(c["argv"] for c in engine_calls(self.log) if c["argv"][0] == "deck")
        self.assertNotIn("--logo-white", deck)
        self.assertIn("白いロゴは使いません（白いロゴの縦横比がロゴと違う）", run["record"]["look"]["notes"])

    def test_no_logo_no_watermark_no_footer(self) -> None:
        self.start()
        brand = self.brand(logo=False, white=False, footer="", watermark="", page_numbers=False)
        body, run = self.make(brand_id=brand["id"])
        deck = next(c["argv"] for c in engine_calls(self.log) if c["argv"][0] == "deck")
        for flag in ("--logo", "--logo-position", "--watermark", "--logo-white"):
            self.assertNotIn(flag, deck)
        engine_json = json.loads(self.fx.root.engine_script_path(body["deck_id"]).read_text(encoding="utf-8"))
        self.assertNotIn("footer", engine_json)
        self.assertIs(engine_json["page_numbers"], False)


# --- 本体: スタイル -----------------------------------------------------------------------------------------------

class StyleTests(Base):
    def test_presets_map_to_the_engine_styles(self) -> None:
        self.start()
        catalog = self.fx.get("/api/settings").json()
        presets = {s["id"]: s for s in catalog["styles"] if s["kind"] == "preset"}
        self.assertEqual({k: (v["name"], v["config"]["engine_style"]) for k, v in presets.items()}, {
            PRESET_STYLE_STANDARD_ID: ("説明・報告（standard）", {"presentation": "standard", "pitch": "standard"}),
            PRESET_STYLE_VISUAL_ID: ("ビジュアル（visual）", {"presentation": "visual", "pitch": "visual"}),
            PRESET_STYLE_INFOGRAPHIC_ID: ("インフォグラフィック（ピッチ）", {"presentation": "standard", "pitch": "infographic"}),
        })
        self.assertEqual([s["value"] for s in catalog["engine_styles"]["pitch"]], ["visual", "standard", "infographic"])
        _, run = self.make(mode="pitch", style_id=PRESET_STYLE_INFOGRAPHIC_ID)
        self.assertEqual(run["args"]["style"], "infographic")
        _, run = self.make(mode="presentation", style_id=PRESET_STYLE_INFOGRAPHIC_ID, title="プレゼン")
        self.assertEqual(run["args"]["style"], "standard", "プレゼンに infographic は無い")

    def test_allowed_types_and_the_writing_policy_reach_the_writer(self) -> None:
        self.start()
        style = self.copy_style(PRESET_STYLE_STANDARD_ID, "4 つの型だけ", tone="firm",
                                allowed_layouts=["cover", "statement", "cards", "closing"])
        _, run = self.make(style_id=style["id"])
        self.assertEqual(run["state"], "done", run["message"])
        lines = writer_calls(self.log)[0]["style_lines"]
        self.assertIn("- 使ってよい型: cover・statement・cards・closing（ほかの型は使わない）", lines)
        self.assertTrue(any(ln.startswith("- 書き方の方針（スタイル）: 報告・提案を読む人に向け") for ln in lines), lines)
        self.assertTrue(any(ln.startswith("- 文章の調子: 硬め") for ln in lines), lines)

    def test_a_type_outside_the_style_is_sent_back_to_the_writer(self) -> None:
        self.start(cfg=Settings(worker=WorkerSettings(parallel=1, max_check_rounds=2)))
        style = self.copy_style(PRESET_STYLE_STANDARD_ID, "カードを使わない",
                                allowed_layouts=["cover", "statement", "closing"])
        _, run = self.make(style_id=style["id"])
        self.assertEqual(run["state"], "failed")
        calls = writer_calls(self.log)
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("型 cards は、このスタイルで使わない型" in ln for ln in calls[1]["ng"]), calls[1]["ng"])

    def test_bad_type_lists_are_refused(self) -> None:
        self.start()
        catalog = self.fx.get("/api/settings").json()
        src = next(s for s in catalog["styles"] if s["id"] == PRESET_STYLE_STANDARD_ID)
        for layouts in (["auto", "cover"], ["no-such-type"], []):
            r = self.fx.post("/api/settings/styles", {"source_id": src["id"], "name": "だめ",
                                                      "config": {"allowed_layouts": layouts},
                                                      "expected_revision_id": src["current_revision_id"]})
            self.assertEqual((r.status, r.error_code), (400, "invalid_setting"), layouts)
        for bad in ({"engine_style": {"presentation": "infographic"}}, {"image_look": "oil"}, {"style_note": "x\ny"}):
            r = self.fx.post("/api/settings/styles", {"source_id": src["id"], "name": "だめ", "config": bad,
                                                      "expected_revision_id": src["current_revision_id"]})
            self.assertEqual((r.status, r.error_code), (400, "invalid_setting"), bad)

    def test_the_writer_prompt_lists_types_only_when_the_style_narrows_them(self) -> None:
        engine = ENGINES["presentation"]
        base = {"mode": "presentation", "style": "standard", "images": True}
        auto = build_prompt(engine, {**base, "look": {"allowed_types": None, "emphasis": "", "tone": "standard"}},
                            manuscript="原稿", title_hint=None, skills_dir=SKILLS_DIR)
        self.assertNotIn("使ってよい型", auto)
        self.assertIn("theme・footer・page_numbers は書かない", auto)
        narrow = build_prompt(engine, {**base, "look": {"allowed_types": ["cover", "closing"]}},
                              manuscript="原稿", title_hint=None, skills_dir=SKILLS_DIR)
        self.assertIn("- 使ってよい型: cover・closing（ほかの型は使わない）", narrow)
        self.assertEqual(LOOK.type_problems([{"id": "a", "type": "cover"}, {"id": "b", "type": "kpi"}],
                                            {"look": {"allowed_types": ["cover"]}}),
                         ["NG: 2枚目（id=b）の型 kpi は、このスタイルで使わない型。使ってよい型（cover）で書き直す"])


# --- 設定を変えたら次の回から ---------------------------------------------------------------------------------------

class NextRunTests(Base):
    def test_a_changed_brand_and_style_apply_to_the_next_run_of_the_deck(self) -> None:
        self.start()
        brand = self.brand(primary="#C8102E")
        style = self.copy_style(PRESET_STYLE_STANDARD_ID, "変えるスタイル")
        body, first = self.make(brand_id=brand["id"], style_id=style["id"])
        self.assertEqual(first["state"], "done", first["message"])
        self.assertEqual(first["args"]["style"], "standard")
        # 描いたあとでブランドの色とスタイルの見せ方を変える（描き直しは勝手に始めない）
        cfg = copy.deepcopy(brand["config"])
        cfg["colors"] = {"primary": "#0B6E4F", "accent": "#1D4ED8"}
        r = self.fx.post(f"/api/settings/brands/{brand['id']}", {"name": brand["name"], "config": cfg,
                                                                 "assets": brand["assets"],
                                                                 "expected_revision_id": brand["current_revision_id"]})
        self.assertEqual(r.status, 200, r.body)
        r = self.fx.post(f"/api/settings/styles/{style['id']}", {"name": style["name"],
                                                                 "config": {"engine_style": {"presentation": "visual", "pitch": "visual"}},
                                                                 "expected_revision_id": style["current_revision_id"]})
        self.assertEqual(r.status, 200, r.body)
        time.sleep(0.3)
        with self.fx.server.store_lock:
            self.assertFalse(self.fx.store.deck_working(body["deck_id"]), "保存しただけでは描き直さない")
        # 次の回（画面からの「描き直し」）で効く
        with self.fx.server.store_lock:
            self.fx.store.create_edit(body["deck_id"], {"op": "render", "source": "screen"})
        self.fx.server.wake_worker()
        second = wait_until_idle(self.fx, body["deck_id"])
        self.assertEqual(second["state"], "done", second["message"])
        self.assertEqual(second["args"]["style"], "visual")
        self.assertEqual(second["record"]["look"]["theme"]["primary"], "#0B6E4F")
        self.assertEqual(second["record"]["look"]["brand"]["revision"], 2)
        with self.fx.server.store_lock:
            deck = self.fx.store.deck(body["deck_id"])
            self.assertEqual(self.fx.store.brand_revision(deck["brand_revision_id"])["revision"], 2)
            self.assertEqual(self.fx.store.style_revision(deck["style_revision_id"])["revision"], 2)

    def test_an_infographic_deck_stays_one_page(self) -> None:
        self.start()
        style = self.copy_style(PRESET_STYLE_INFOGRAPHIC_ID, "ポスター")
        body, first = self.make(mode="pitch", style_id=style["id"])
        self.assertEqual(first["args"]["style"], "infographic")
        r = self.fx.post(f"/api/settings/styles/{style['id']}", {"name": style["name"],
                                                                 "config": {"engine_style": {"presentation": "standard", "pitch": "visual"}},
                                                                 "expected_revision_id": style["current_revision_id"]})
        self.assertEqual(r.status, 200, r.body)
        with self.fx.server.store_lock:
            self.fx.store.create_edit(body["deck_id"], {"op": "render", "source": "screen"})
        self.fx.server.wake_worker()
        second = wait_until_idle(self.fx, body["deck_id"])
        self.assertEqual(second["args"]["style"], "infographic")
        self.assertTrue(any("最初の回で決まる" in n for n in second["record"]["look"]["notes"]))


# --- ブランドの画像 ----------------------------------------------------------------------------------------------

class BrandAssetTests(Base):
    def test_the_editor_save_keeps_other_role_assets_and_drops_removed_logos(self) -> None:
        self.start()
        extra = [{"asset_id": self.upload(size=(300, 300)), "role": "optional", "label": "差し絵"},
                 {"asset_id": self.upload(size=(320, 200)), "role": "source", "label": "元の資料"}]
        brand = self.brand(extra_assets=extra)
        roles = sorted(a["role"] for a in brand["assets"])
        self.assertEqual(roles, ["logo_transparent", "logo_white", "optional", "source"])
        # 編集画面と同じ形（P1 の画面はロゴだけを送っていた）: 白いロゴを外し、ロゴだけを送る
        logo = [a for a in brand["assets"] if a["role"] == "logo_transparent"]
        cfg = copy.deepcopy(brand["config"])
        r = self.fx.post(f"/api/settings/brands/{brand['id']}", {"name": brand["name"], "config": cfg, "assets": logo,
                                                                 "expected_revision_id": brand["current_revision_id"]})
        self.assertEqual(r.status, 200, r.body)
        after = {a["role"]: a["asset_id"] for a in r.json()["item"]["assets"]}
        self.assertEqual(sorted(after), ["logo_transparent", "optional", "source"], "ロゴ以外は持ち越し、外したロゴは外す")
        self.assertEqual(after["optional"], extra[0]["asset_id"])

    def test_a_webp_logo_is_refused_with_a_reason(self) -> None:
        self.start()
        webp = self.upload(kind="WEBP")
        cfg = {"schema_version": 1, "colors": {"primary": "#C8102E", "accent": "#1D4ED8"},
               "logo": {"variant": "transparent", "placement": {"corner": "bottom-right"}},
               "footer": {"text": "", "style": {"position": "footer_left"}}}
        r = self.fx.post("/api/settings/brands", {"name": "WebP", "config": cfg,
                                                  "assets": [{"asset_id": webp, "role": "logo_transparent"}]})
        self.assertEqual((r.status, r.error_code), (400, "logo_type"))

    def test_preview_places_the_footer_page_number_and_watermark_like_the_engine(self) -> None:
        self.start()
        brand = self.brand(corner="bottom-left")
        r = self.fx.post("/api/settings/brands/preview", {"config": brand["config"], "assets": brand["assets"]})
        self.assertEqual(r.status, 200, r.body)
        p = r.json()
        x, y, w, h = p["logo_xywh"]
        self.assertEqual(x, 120, "左下のロゴは本文の左の余白")
        self.assertEqual(p["chrome"]["footer"][0], x + w + 28, "フッタはロゴの右へ寄せる")
        self.assertEqual(p["chrome"]["footer"][0] + p["chrome"]["footer"][2], 912, "ページ番号の手前まで")
        self.assertEqual(p["chrome"]["page_number"][:2], [900, 1006])
        self.assertEqual(p["format"], {"page_numbers": True, "watermark": "配布禁止"})
        self.assertEqual(p["theme"]["primary"], "#C8102E")
        r = self.fx.post("/api/settings/brands/preview", {"config": {**brand["config"], "logo": {
            "variant": "transparent", "placement": {"corner": "top-right"}}}, "assets": brand["assets"]})
        x, y, w, h = r.json()["logo_xywh"]
        self.assertEqual((x + w, round(y + h / 2)), (1800, 46), "右上は右の余白と上の余白の中")

    def test_format_fields_are_validated(self) -> None:
        self.start()
        for fmt in ({"page_numbers": "yes"}, {"watermark": "x" * 61}, {"watermark": "a\nb"}, {"unknown": 1}):
            cfg = {"schema_version": 1, "colors": {"primary": "#C8102E", "accent": "#1D4ED8"},
                   "logo": {"variant": "none"}, "footer": {"text": ""}, "format": fmt}
            r = self.fx.post("/api/settings/brands", {"name": "書式", "config": cfg, "assets": []})
            self.assertEqual(r.status, 400, fmt)


if __name__ == "__main__":
    unittest.main()
