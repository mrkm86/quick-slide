"""製品同梱プリセット（既定ブランド 1、スタイル 2）。DB 初期化時に作り、内容が変わったら製品更新として改訂を足す。

スタイルの 2 つは、エンジンのスタイルの standard（説明・報告向け）と visual（プレゼン・ピッチ向け）にあたる。
エンジンへ写すのは P4。文面は暫定で、育てるのは P4 以降。利用者の複写は触らない。
"""

from __future__ import annotations

from typing import Any

from ..ids import PRESET_BRAND_ID, PRESET_STYLE_STANDARD_ID, PRESET_STYLE_VISUAL_ID
from ..logo import DEFAULT_PLACEMENT
from .base import StoreBase, dump_json, load_json

PRESET_BRAND_NAME = "既定（無彩色）"

PRESET_BRAND_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "colors": {
        "primary": "#1f1f1f",
        "accent": "#4a4a4a",
        "semantic": {"success": "#3b6b45", "warning": "#8a6d2f", "error": "#8b3a3a", "info": "#3d5a73"},
        "gray": ["#111111", "#333333", "#555555", "#777777", "#999999", "#bbbbbb", "#dddddd", "#f5f5f5"],
    },
    "fonts": {
        "heading": {"requested_family": "Noto Sans JP", "weight": 700, "fallback_families": ["Hiragino Sans", "sans-serif"]},
        "body": {"requested_family": "Noto Sans JP", "weight": 400, "fallback_families": ["Hiragino Sans", "sans-serif"]},
    },
    "logo": {"variant": "none", "placement": dict(DEFAULT_PLACEMENT)},
    "footer": {"text": "", "style": {"position": "footer_left"}},
}


def _style(style_id: str, name: str, emphasis: str, *, typeface: str, density: str, visual_ratio: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": style_id,
        "revision": 1,
        "name": name,
        "origin": None,
        "emphasis_prompt": emphasis,
        "tone": "standard",
        "typeface": typeface,
        "density": density,
        "visual_ratio": visual_ratio,
        "allowed_layouts": ["auto"],
    }


PRESET_STYLES: dict[str, tuple[str, dict[str, Any]]] = {
    PRESET_STYLE_STANDARD_ID: ("スタンダード", _style(
        PRESET_STYLE_STANDARD_ID, "スタンダード",
        "報告・提案を読む人に向け、結論と数値を先に示す。比較は同じ観点で並べ、単位と前年比を添える。",
        typeface="gothic", density="standard", visual_ratio="data")),
    PRESET_STYLE_VISUAL_ID: ("ビジュアル", _style(
        PRESET_STYLE_VISUAL_ID, "ビジュアル",
        "ピッチや登壇資料を読む人に向け、概念を絵や図解で端的に伝える。文字は見出しと短い補足にとどめ、1ページで1つの印象を残す。",
        typeface="rounded", density="airy", visual_ratio="illustration")),
}


class PresetsMixin(StoreBase):
    def ensure_presets(self) -> dict[str, Any]:
        """冪等。無ければ作り、同梱内容が変わっていれば製品更新として改訂を足す（利用者の複写は触らない）。"""
        result: dict[str, Any] = {"brand_created": False, "brand_updated": False, "styles_created": [], "styles_updated": []}
        with self.transaction():
            b = self._one("SELECT * FROM brands WHERE id=?", (PRESET_BRAND_ID,))
            if b is None:
                self.create_brand(PRESET_BRAND_NAME, PRESET_BRAND_CONFIG, brand_id=PRESET_BRAND_ID, kind="preset")
                result["brand_created"] = True
            else:
                current = load_json(self._get("brand_revisions", b["current_revision_id"])["config_json"])
                if dump_json(current) != dump_json(PRESET_BRAND_CONFIG):
                    self.add_brand_revision(PRESET_BRAND_ID, PRESET_BRAND_CONFIG, _product_update=True)
                    result["brand_updated"] = True
            for sid, (name, config) in PRESET_STYLES.items():
                row = self._one("SELECT * FROM styles WHERE id=?", (sid,))
                if row is None:
                    self._create_preset_style(sid, name, config)
                    result["styles_created"].append(sid)
                    continue
                current = load_json(self._get("style_revisions", row["current_revision_id"])["config_json"])
                bundled = {**config, "revision": current["revision"]}
                if dump_json(current) != dump_json(bundled):
                    next_rev = int(self._scalar(
                        "SELECT COALESCE(MAX(revision),0)+1 FROM style_revisions WHERE style_id=?", (sid,)))
                    self.add_style_revision(sid, {**config, "revision": next_rev}, _product_update=True)
                    result["styles_updated"].append(sid)
        return result

    def preset_brand_revision_id(self) -> str:
        return str(self._scalar("SELECT current_revision_id FROM brands WHERE id=?", (PRESET_BRAND_ID,)))

    def preset_style_revision_id(self, style_id: str = PRESET_STYLE_STANDARD_ID) -> str:
        return str(self._scalar("SELECT current_revision_id FROM styles WHERE id=?", (style_id,)))
