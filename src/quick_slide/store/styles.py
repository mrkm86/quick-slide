"""スライドスタイル。自己完結 JSON、preset 編集拒否、複写だけで新規作成。

エンジンへの写し方（P4。描く回のたびに `render/look.py` が読む）:

| キー | エンジン・台本係 |
|---|---|
| `engine_style` `{presentation, pitch}` | エンジンの `--style`（プレゼン: standard・visual／ピッチ: visual・standard・infographic） |
| `image_look` | `--image-look`（photo・illustration・handdrawn。省けばエンジンのスタイルの既定） |
| `style_note` | ピッチの `--style-note`（作風の一言）。プレゼンは台本係への書き方の注文に添える |
| `emphasis_prompt`・`tone` | 台本係への書き方の方針 |
| `allowed_layouts` | 台本係が使ってよい型（`["auto"]` はおまかせ＝全部。型の目録は `contenttypes.py`） |

`engine_style` を書いていない古いスタイルは、モードの既定（プレゼン standard・ピッチ visual）。"""

from __future__ import annotations

import re
from typing import Any

from .. import contenttypes
from ..enums import require
from ..errors import NotFoundError, StateError, ValidationError
from ..ids import is_uuid
from .base import StoreBase, dump_json, load_json, reject_unknown_keys, require_number, require_str

TONES = ("firm", "standard", "soft")
TYPEFACE_OPTIONS = (("rounded", "丸ゴシック"), ("gothic", "ゴシック"), ("serif", "明朝"))
DENSITY_OPTIONS = (("airy", "ゆったり"), ("standard", "標準"), ("tight", "詰める"))
VISUAL_RATIO_OPTIONS = (("illustration", "イラスト多め"), ("balanced", "バランス"), ("data", "図表中心"))
TYPEFACES = tuple(value for value, _ in TYPEFACE_OPTIONS)
DENSITIES = tuple(value for value, _ in DENSITY_OPTIONS)
VISUAL_RATIOS = tuple(value for value, _ in VISUAL_RATIO_OPTIONS)
STYLE_DIAL_OPTIONS = {
    "typeface": TYPEFACE_OPTIONS,
    "density": DENSITY_OPTIONS,
    "visual_ratio": VISUAL_RATIO_OPTIONS,
}
CANVAS_SIZES = {(1920, 1080), (3840, 2160)}
# 「使うパターン」の「おまかせ」（型の目録そのものはエンジンの文書から読む: contenttypes.engine_types）
AUTO_PATTERN = {"value": contenttypes.AUTO, "label": "おまかせ（すべて）", "use": "ページの型は台本を書くときに選びます。"}
# エンジンのスタイル（モードごと）と画風。値の正本はエンジン（engines.ENGINES・engines.IMAGE_LOOKS）
ENGINE_STYLES = {"presentation": ("standard", "visual"), "pitch": ("visual", "standard", "infographic")}
IMAGE_LOOK_CHOICES = ("photo", "illustration", "handdrawn")
MAX_STYLE_NOTE_CHARS = 400
# 画面から変えられるキー（保存の口が受ける）
EDITABLE_KEYS = ("emphasis_prompt", "tone", "typeface", "density", "visual_ratio", "allowed_layouts", "style_recipe",
                 "margins", "canvas", "engine_style", "image_look", "style_note")
STYLE_KEYS = {
    "schema_version", "id", "revision", "name", "origin", *EDITABLE_KEYS,
}
_ENV_DEPENDENT_RE = re.compile(r"(^|[\s\"'(])(/Users/|/home/|~/|[A-Za-z]:\\\\|file://)")


def _scan_environment_dependency(value: Any, where: str) -> None:
    if isinstance(value, str):
        if _ENV_DEPENDENT_RE.search(value):
            raise ValidationError(f"{where}: スタイルにローカルパス等の環境依存を書けません")
    elif isinstance(value, dict):
        for k, v in value.items():
            if k in {"brand_id", "brand_revision_id", "asset_id", "path", "logo_override"}:
                raise ValidationError(f"{where}: スタイルは '{k}' を持てません（自己完結・配置非関与）")
            _scan_environment_dependency(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _scan_environment_dependency(v, f"{where}[{i}]")


def validate_style_config(config: dict[str, Any], *, expected_id: str, expected_revision: int, expected_name: str) -> None:
    if not isinstance(config, dict):
        raise ValidationError("style config はオブジェクト")
    reject_unknown_keys(config, STYLE_KEYS, "style")
    if config.get("schema_version") != 1:
        raise ValidationError("style.schema_version は 1")
    if config.get("id") != expected_id or not is_uuid(config.get("id")):
        raise ValidationError("style.id は表の ID と一致する UUID")
    if config.get("revision") != expected_revision:
        raise ValidationError("style.revision は表の改訂番号と一致する")
    if config.get("name") != expected_name:
        raise ValidationError("style.name は表の表示名と一致する")
    origin = config.get("origin")
    if origin is not None:
        if not isinstance(origin, dict):
            raise ValidationError("style.origin は null か {id, revision}")
        reject_unknown_keys(origin, {"id", "revision"}, "style.origin")
        if not is_uuid(origin.get("id")):
            raise ValidationError("style.origin.id は UUID")
        rev = origin.get("revision")
        if isinstance(rev, bool) or not isinstance(rev, int) or rev <= 0:
            raise ValidationError("style.origin.revision は正の整数")
    require_str(config, "emphasis_prompt", "style")
    if "style_recipe" in config:
        require_str(config, "style_recipe", "style", allow_empty=True)
    require(config.get("tone"), TONES, "style.tone")
    for key, vocabulary in (("typeface", TYPEFACES), ("density", DENSITIES), ("visual_ratio", VISUAL_RATIOS)):
        if key in config:
            require(config[key], vocabulary, f"style.{key}")
    layouts = config.get("allowed_layouts")
    if not isinstance(layouts, list) or not layouts or not all(isinstance(x, str) and x for x in layouts):
        raise ValidationError("style.allowed_layouts は非空の文字列配列")
    if len(set(layouts)) != len(layouts):
        raise ValidationError("style.allowed_layouts に重複があります")
    bad = contenttypes.problems(layouts)
    if bad:
        raise ValidationError("style.allowed_layouts: " + bad[0])
    if "engine_style" in config:
        es = config["engine_style"]
        if not isinstance(es, dict):
            raise ValidationError("style.engine_style は {presentation, pitch} のオブジェクト")
        reject_unknown_keys(es, set(ENGINE_STYLES), "style.engine_style")
        for mode, value in es.items():
            require(value, ENGINE_STYLES[mode], f"style.engine_style.{mode}")
    if config.get("image_look") is not None:
        require(config["image_look"], IMAGE_LOOK_CHOICES, "style.image_look")
    if "style_note" in config:
        require_str(config, "style_note", "style", allow_empty=True)
        if len(config["style_note"]) > MAX_STYLE_NOTE_CHARS or "\n" in config["style_note"]:
            raise ValidationError(f"style.style_note は {MAX_STYLE_NOTE_CHARS} 字以内の 1 行")
    # 余白と画面の大きさは省略できる（エンジンが決める）。書いてあるときだけ形を見る
    if "margins" in config:
        margins = config["margins"]
        if not isinstance(margins, dict) or not margins:
            raise ValidationError("style.margins は非空のオブジェクト")
        for k in margins:
            require_number(margins, k, "style.margins", minimum=0, maximum=0.5)
    if "canvas" in config:
        canvas = config["canvas"]
        if not isinstance(canvas, dict):
            raise ValidationError("style.canvas はオブジェクト")
        reject_unknown_keys(canvas, {"aspect", "width", "height", "fit"}, "style.canvas")
        if canvas.get("aspect") != [16, 9]:
            raise ValidationError("style.canvas.aspect は [16, 9]")
        if (canvas.get("width"), canvas.get("height")) not in CANVAS_SIZES:
            raise ValidationError("style.canvas は 1920×1080 か 3840×2160")
        if canvas.get("fit") != "crop":
            raise ValidationError("style.canvas.fit は crop")
    _scan_environment_dependency({k: v for k, v in config.items() if k != "origin"}, "style")


class StylesMixin(StoreBase):
    def save_style_settings(self, style_id: str, name: str, changes: dict[str, Any]) -> dict[str, Any]:
        """名前と新改訂を原子的に保存。過去の文書内の名前・出所も保持する。"""
        with self.transaction():
            row = self.style(style_id)
            if row["kind"] == "preset" or row["deleted_at"] is not None:
                raise StateError("このスタイルは編集できません")
            if not isinstance(name, str) or not name.strip():
                raise ValidationError("名前を入力してください")
            reject_unknown_keys(changes, set(EDITABLE_KEYS), "changes")
            config = dict(row["current_revision"]["config"])
            config.update(changes)
            config.update(name=name, revision=row["current_revision"]["revision"] + 1)
            self._exec("UPDATE styles SET name=? WHERE id=?", (name, style_id))
            return self.add_style_revision(style_id, config)

    def create_style_copy(self, origin_style_id: str, name: str, *, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """複写でだけ新規作成する（白紙作成 API は提供しない）。新 UUID・版 1・origin に複写元 ID/版。"""
        if not name or not name.strip():
            raise ValidationError("styles.name は空にできない")
        overrides = overrides or {}
        reject_unknown_keys(overrides, set(EDITABLE_KEYS), "overrides")
        with self.transaction():
            origin = self._get("styles", origin_style_id)
            if origin["deleted_at"] is not None:
                raise StateError("削除済みスタイルは複写元にできません")
            origin_rev = self._get("style_revisions", origin["current_revision_id"])
            base = load_json(origin_rev["config_json"])
            new_id = self.new_id()
            config = dict(base)
            config.update(overrides)
            config.update({"id": new_id, "revision": 1, "name": name,
                           "origin": {"id": origin_style_id, "revision": origin_rev["revision"]}})
            validate_style_config(config, expected_id=new_id, expected_revision=1, expected_name=name)
            now = self.now()
            rev_id = self.new_id()
            self._exec(
                "INSERT INTO styles(id,name,current_revision_id,kind,origin_style_id,origin_revision,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (new_id, name, rev_id, "user", origin_style_id, origin_rev["revision"], now, now),
            )
            self._exec(
                "INSERT INTO style_revisions(id,style_id,revision,config_json,created_at) VALUES(?,?,?,?,?)",
                (rev_id, new_id, 1, dump_json(config), now),
            )
            self.append_event("style_created", {"style_id": new_id, "revision_id": rev_id,
                                                   "origin": {"id": origin_style_id, "revision": origin_rev["revision"]}})
            return self.style(new_id)

    def _create_preset_style(self, style_id: str, name: str, config: dict[str, Any]) -> dict[str, Any]:
        """製品同梱プリセット。presets.ensure_presets からだけ呼ぶ。"""
        validate_style_config(config, expected_id=style_id, expected_revision=1, expected_name=name)
        self._require_tx()
        now = self.now()
        rev_id = self.new_id()
        self._exec(
            "INSERT INTO styles(id,name,current_revision_id,kind,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (style_id, name, rev_id, "preset", now, now),
        )
        self._exec(
            "INSERT INTO style_revisions(id,style_id,revision,config_json,created_at) VALUES(?,?,?,?,?)",
            (rev_id, style_id, 1, dump_json(config), now),
        )
        self.append_event("style_created", {"style_id": style_id, "revision_id": rev_id, "kind": "preset"})
        return self.style(style_id)

    def add_style_revision(self, style_id: str, config: dict[str, Any], *, _product_update: bool = False) -> dict[str, Any]:
        with self.transaction():
            t = self._get("styles", style_id)
            if t["deleted_at"] is not None:
                raise StateError("削除済みスタイルに改訂を足せません")
            if t["kind"] == "preset" and not _product_update:
                raise StateError("preset スタイルは編集できません（製品更新だけが改訂を足せる）")
            next_rev = int(self._scalar("SELECT COALESCE(MAX(revision),0)+1 FROM style_revisions WHERE style_id=?", (style_id,)))
            validate_style_config(config, expected_id=style_id, expected_revision=next_rev, expected_name=t["name"])
            if t["kind"] == "user":
                origin = config.get("origin")
                if origin is None or origin.get("id") != t["origin_style_id"] or origin.get("revision") != t["origin_revision"]:
                    raise ValidationError("利用者スタイルの origin は複写元 ID/版を維持する")
            now = self.now()
            rev_id = self.new_id()
            self._exec(
                "INSERT INTO style_revisions(id,style_id,revision,config_json,created_at) VALUES(?,?,?,?,?)",
                (rev_id, style_id, next_rev, dump_json(config), now),
            )
            self._exec("UPDATE styles SET current_revision_id=?,updated_at=? WHERE id=?", (rev_id, now, style_id))
            self.append_event("style_revision_added", {"style_id": style_id, "revision_id": rev_id, "revision": next_rev})
            return self.style_revision(rev_id)

    def delete_style(self, style_id: str) -> None:
        with self.transaction():
            t = self._get("styles", style_id)
            if t["kind"] == "preset":
                raise StateError("preset スタイルは削除できません")
            if t["deleted_at"] is not None:
                return
            now = self.now()
            self._exec("UPDATE styles SET deleted_at=?,updated_at=? WHERE id=?", (now, now, style_id))
            self.append_event("style_deleted", {"style_id": style_id})

    def style(self, style_id: str) -> dict[str, Any]:
        t = self._get("styles", style_id)
        t["current_revision"] = self.style_revision(t["current_revision_id"]) if t["current_revision_id"] else None
        return t

    def style_revision(self, revision_id: str) -> dict[str, Any]:
        r = self._get("style_revisions", revision_id)
        r["config"] = load_json(r.pop("config_json"))
        return r

    def style_revisions(self, style_id: str) -> list[dict[str, Any]]:
        return [self.style_revision(r["id"]) for r in self._all(
            "SELECT id FROM style_revisions WHERE style_id=? ORDER BY revision", (style_id,))]

    def list_styles(self, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM styles"
        if not include_deleted:
            sql += " WHERE deleted_at IS NULL"
        # 同着（updated_at が同値）は挿入順 = rowid で決める。乱数 id で決めると並びが実行ごとに変わる
        sql += " ORDER BY kind DESC, updated_at DESC, rowid DESC"
        return self._all(sql)

    def export_style_document(self, revision_id: str) -> dict[str, Any]:
        """1 ファイルに持ち出す投影。config_json がそのまま完全な文書。"""
        return self.style_revision(revision_id)["config"]

    def _require_live_style_revision(self, revision_id: str) -> dict[str, Any]:
        row = self._one(
            "SELECT tr.*, t.deleted_at AS style_deleted_at FROM style_revisions tr JOIN styles t ON t.id=tr.style_id WHERE tr.id=?",
            (revision_id,),
        )
        if row is None:
            raise NotFoundError("style_revision が存在しません")
        if row["style_deleted_at"] is not None:
            raise StateError("削除済みスタイルは新規に選べません")
        row["config"] = load_json(row.pop("config_json"))
        return row
