"""製品同梱プリセット（既定ブランド 1、スタイル 3）。DB 初期化時に作り、内容が変わったら製品更新として改訂を足す。

- 既定のブランドは**エンジンの既定の色**（白の地・チャコールの文字・シアンの主役の色。html-slide の
  `themes/default.yaml` と同じ）。描く回はこのブランドからもテーマの YAML を作るので、何も選ばなければ
  エンジンの既定の見た目になる（P4 までの 1 冊と同じ色）
- スタイルの 3 つは、エンジンのスタイルの standard（説明・報告）・visual（ビジュアル）・infographic（ピッチだけ。
  プレゼンでは standard）にあたる
- 名前を変えたときは、表の名前も製品更新で替える（利用者の複写は触らない）
"""

from __future__ import annotations

from typing import Any

from ..ids import PRESET_BRAND_ID, PRESET_STYLE_INFOGRAPHIC_ID, PRESET_STYLE_STANDARD_ID, PRESET_STYLE_VISUAL_ID
from ..logo import DEFAULT_PLACEMENT
from .base import StoreBase, dump_json, load_json

PRESET_BRAND_NAME = "既定"

PRESET_BRAND_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "colors": {
        "primary": "#0DB4F2",
        "accent": "#0DB4F2",
        # 本文の文字（2 段目）は DADS の Gray-800（#333333）。ほかの段は算出の値のまま（エンジンの既定のテーマの ink と同じにする）
        "gray": ["#1A1A1A", "#333333", "#666666", "#8C8C8C", "#B2B2B2", "#D6D6D6", "#EDEDED", "#F7F7F7"],
    },
    "fonts": {
        "heading": {"requested_family": "Noto Sans JP", "weight": 700, "fallback_families": ["Hiragino Sans", "sans-serif"]},
        "body": {"requested_family": "Noto Sans JP", "weight": 400, "fallback_families": ["Hiragino Sans", "sans-serif"]},
    },
    "logo": {"variant": "none", "placement": dict(DEFAULT_PLACEMENT)},
    "footer": {"text": "", "style": {"position": "footer_left"}},
    "format": {"page_numbers": False, "watermark": ""},
}


def _style(style_id: str, name: str, emphasis: str, *, typeface: str, density: str, visual_ratio: str,
           engine_style: dict[str, str]) -> dict[str, Any]:
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
        "engine_style": engine_style,
    }


STANDARD_NAME = "説明・報告（standard）"
VISUAL_NAME = "ビジュアル（visual）"
INFOGRAPHIC_NAME = "インフォグラフィック（ピッチ）"

PRESET_STYLES: dict[str, tuple[str, dict[str, Any]]] = {
    PRESET_STYLE_STANDARD_ID: (STANDARD_NAME, _style(
        PRESET_STYLE_STANDARD_ID, STANDARD_NAME,
        "報告・提案を読む人に向け、結論と数値を先に示す。比較は同じ観点で並べ、単位と前年比を添える。",
        typeface="gothic", density="standard", visual_ratio="data",
        engine_style={"presentation": "standard", "pitch": "standard"})),
    PRESET_STYLE_VISUAL_ID: (VISUAL_NAME, _style(
        PRESET_STYLE_VISUAL_ID, VISUAL_NAME,
        "ピッチや登壇資料を読む人に向け、概念を絵や図解で端的に伝える。文字は見出しと短い補足にとどめ、1ページで1つの印象を残す。",
        typeface="rounded", density="airy", visual_ratio="illustration",
        engine_style={"presentation": "visual", "pitch": "visual"})),
    PRESET_STYLE_INFOGRAPHIC_ID: (INFOGRAPHIC_NAME, _style(
        PRESET_STYLE_INFOGRAPHIC_ID, INFOGRAPHIC_NAME,
        "資料全体を1枚のポスターに凝縮する。要点は3〜5つにしぼり、数字と短い見出しで流れが一目で追えるようにする。",
        typeface="gothic", density="tight", visual_ratio="data",
        engine_style={"presentation": "standard", "pitch": "infographic"})),
}


def preset_style_for(mode: str, engine_style: str) -> str:
    """エンジンのスタイルの名前 → それにあたる付属スタイルの id（会話が `style` だけを言ったとき）。"""
    for sid, (_, config) in PRESET_STYLES.items():
        if config["engine_style"].get(mode) == engine_style:
            return sid
    return PRESET_STYLE_STANDARD_ID


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
                if b["name"] != PRESET_BRAND_NAME:
                    self._exec("UPDATE brands SET name=?,updated_at=? WHERE id=?", (PRESET_BRAND_NAME, self.now(), PRESET_BRAND_ID))
                    result["brand_updated"] = True
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
                if row["name"] != name:
                    # 名前を替えた（製品更新）。改訂の検査は表の名前と照らすので、先に表の名前を替える
                    self._exec("UPDATE styles SET name=?,updated_at=? WHERE id=?", (name, self.now(), sid))
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
