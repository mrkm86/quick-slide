"""ブランドキット。改訂は不変。preset は製品更新以外で改訂・削除できない。

logo.placement の形と角の語彙は logo.py が正本。ここには持たない。
描く回のたびに、この設定をエンジンのテーマ（色。`palette.engine_theme`）・`--logo`・`--logo-white`・`--logo-position`・
台本の `footer`・`page_numbers`・`--watermark` へ写す（`render/look.py`）。

書式（ページ番号・透かし）はブランドの中の `format` に持つ（P4。フッタの文字はもともとブランドの `footer`）。
書式だけの表と改訂は作らない（ブランドの改訂がそのまま「どの書式で描いたか」の控えになる）。"""

from __future__ import annotations

from typing import Any

from ..enums import BRAND_ASSET_ROLES, LOGO_ROLES, LOGO_VARIANTS, require
from ..errors import NotFoundError, StateError, ValidationError
from ..logo import validate_placement
from .base import (
    StoreBase,
    dump_json,
    load_json,
    reject_unknown_keys,
    require_hex_color,
    require_number,
    require_str,
)

MAX_WATERMARK_CHARS = 60
MAX_FOOTER_CHARS = 120

VARIANT_TO_ROLE = {
    "normal": "logo_normal",
    "white": "logo_white",
    "transparent": "logo_transparent",
    "symbol": "logo_symbol",
}


def validate_brand_config(config: dict[str, Any], assets: list[dict[str, Any]]) -> None:
    """設定 JSON と改訂内 asset の整合を検証する。"""
    if not isinstance(config, dict):
        raise ValidationError("brand config はオブジェクト")
    reject_unknown_keys(config, {"schema_version", "colors", "fonts", "logo", "footer", "format"}, "brand")
    if config.get("schema_version") != 1:
        raise ValidationError("brand.schema_version は 1")

    colors = config.get("colors")
    if not isinstance(colors, dict):
        raise ValidationError("brand.colors は必須のオブジェクト")
    reject_unknown_keys(
        colors,
        {
            "primary",
            "accent",
            "info",
            "semantic",
            "gray",
            "primary_light",
            "primary_dark",
            "border",
            "surface",
            "base",
            "ink",
            "muted",
        },
        "brand.colors",
    )
    require_hex_color(colors.get("primary"), "brand.colors.primary")
    require_hex_color(colors.get("accent"), "brand.colors.accent")
    if "info" in colors:
        require_hex_color(colors["info"], "brand.colors.info")
    # 拡張役割（すべて省略可）。値があるときだけ検証する。
    for key in ("primary_light", "primary_dark", "border", "surface", "base", "ink", "muted"):
        if key in colors:
            require_hex_color(colors[key], f"brand.colors.{key}")
    if "semantic" in colors:
        semantic = colors["semantic"]
        if not isinstance(semantic, dict):
            raise ValidationError("brand.colors.semantic はオブジェクト")
        reject_unknown_keys(semantic, {"success", "warning", "error", "info"}, "brand.colors.semantic")
        for k in ("success", "warning", "error", "info"):
            require_hex_color(semantic.get(k), f"brand.colors.semantic.{k}")
    if "gray" in colors:
        gray = colors["gray"]
        if not isinstance(gray, list) or not gray:
            raise ValidationError("brand.colors.gray は 1 段以上の配列")
        for i, g in enumerate(gray):
            require_hex_color(g, f"brand.colors.gray[{i}]")

    if "fonts" in config:
        fonts = config["fonts"]
        if not isinstance(fonts, dict):
            raise ValidationError("brand.fonts はオブジェクト")
        reject_unknown_keys(fonts, {"heading", "body"}, "brand.fonts")
        for role in ("heading", "body"):
            f = fonts.get(role)
            if not isinstance(f, dict):
                raise ValidationError(f"brand.fonts.{role} は必須のオブジェクト")
            reject_unknown_keys(f, {"requested_family", "weight", "fallback_families"}, f"brand.fonts.{role}")
            require_str(f, "requested_family", f"brand.fonts.{role}")
            require_number(f, "weight", f"brand.fonts.{role}", minimum=100, maximum=900)
            fb = f.get("fallback_families", [])
            if not isinstance(fb, list) or not all(isinstance(x, str) for x in fb):
                raise ValidationError(f"brand.fonts.{role}.fallback_families は文字列配列")

    logo = config.get("logo")
    if not isinstance(logo, dict):
        raise ValidationError("brand.logo は必須のオブジェクト")
    reject_unknown_keys(logo, {"variant", "placement"}, "brand.logo")
    variant = require("" if logo.get("variant") is None else logo.get("variant"), LOGO_VARIANTS, "brand.logo.variant")
    # 角だけ（省略は既定の右下）。知らないキーは断る
    validate_placement(logo.get("placement"), "brand.logo.placement")

    footer = config.get("footer")
    if not isinstance(footer, dict):
        raise ValidationError("brand.footer は必須のオブジェクト")
    reject_unknown_keys(footer, {"text", "style"}, "brand.footer")
    require_str(footer, "text", "brand.footer", allow_empty=True)
    if "style" in footer and footer["style"] is not None and not isinstance(footer["style"], dict):
        raise ValidationError("brand.footer.style はオブジェクトか null")
    if isinstance(footer.get("style"), dict) and footer["style"].get("position") is not None:
        require(footer["style"]["position"], ("footer_left", "footer_center", "footer_right"), "brand.footer.style.position")

    if "format" in config:
        fmt = config["format"]
        if not isinstance(fmt, dict):
            raise ValidationError("brand.format はオブジェクト")
        reject_unknown_keys(fmt, {"page_numbers", "watermark"}, "brand.format")
        if "page_numbers" in fmt and not isinstance(fmt["page_numbers"], bool):
            raise ValidationError("brand.format.page_numbers は真偽値")
        if "watermark" in fmt:
            wm = require_str(fmt, "watermark", "brand.format", allow_empty=True)
            if len(wm) > MAX_WATERMARK_CHARS or "\n" in wm:
                raise ValidationError(f"brand.format.watermark は {MAX_WATERMARK_CHARS} 字以内の 1 行")
    if len(config["footer"]["text"]) > MAX_FOOTER_CHARS or "\n" in config["footer"]["text"]:
        raise ValidationError(f"brand.footer.text は {MAX_FOOTER_CHARS} 字以内の 1 行")

    # asset 側の整合
    roles = [a["role"] for a in assets]
    for a in assets:
        require(a["role"], BRAND_ASSET_ROLES, "brand_assets.role")
    for r in LOGO_ROLES:
        if roles.count(r) > 1:
            raise ValidationError(f"brand_assets: {r} は改訂内で最大 1 個")
    if variant != "none" and VARIANT_TO_ROLE[variant] not in roles:
        raise ValidationError("選択したロゴ variant に対応する role の asset が登録されていません（自動で別ロゴに置換しない）")


def merge_brand_assets(previous: list[dict[str, Any]], submitted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """改訂を足すときの画像。**送られた画像が勝ち、送られなかった役割（ロゴ以外）は前の改訂から持ち越す。**

    編集画面が扱うのはロゴ（`LOGO_ROLES`）だけ。ロゴの役割は送られたものがすべて（送らない＝外した）。
    ロゴでない役割（`optional`・`source`）は画面が送らないので、前の改訂のものを持ち越す（P1 のレビューの指摘。
    前は画面がロゴだけを送り、ほかの役割の画像が保存のたびに消えていた）。
    """
    if not isinstance(submitted, list):
        return submitted
    sent_roles = {a.get("role") for a in submitted if isinstance(a, dict)}
    out = [dict(a) for a in submitted]
    for a in previous or []:
        role = a.get("role")
        if role in LOGO_ROLES or role in sent_roles:
            continue
        out.append({"asset_id": a["asset_id"], "role": role, "label": a.get("label", "")})
    return out


class BrandsMixin(StoreBase):
    def create_brand(
        self,
        name: str,
        config: dict[str, Any],
        *,
        assets: list[dict[str, Any]] | None = None,
        brand_id: str | None = None,
        kind: str = "user",
    ) -> dict[str, Any]:
        """ブランドと改訂 1 を同一トランザクションで作る。assets は [{asset_id, role, label?}]。"""
        require(kind, ("preset", "user"), "brands.kind")
        if kind == "preset" and brand_id is None:
            raise ValidationError("preset ブランドは固定 UUID を指定する")
        if not name or not name.strip():
            raise ValidationError("brands.name は空にできない")
        assets = assets or []
        validate_brand_config(config, assets)
        with self.transaction():
            now = self.now()
            bid = brand_id or self.new_id()
            rev_id = self.new_id()
            self._exec(
                "INSERT INTO brands(id,name,current_revision_id,kind,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (bid, name, rev_id, kind, now, now),
            )
            self._insert_brand_revision(bid, rev_id, 1, config, assets, now)
            self.append_event("brand_created", {"brand_id": bid, "revision_id": rev_id, "kind": kind})
            return self.brand(bid)

    def add_brand_revision(self, brand_id: str, config: dict[str, Any], *, assets: list[dict[str, Any]] | None = None,
                           _product_update: bool = False) -> dict[str, Any]:
        assets = assets or []
        validate_brand_config(config, assets)
        with self.transaction():
            b = self._get("brands", brand_id)
            if b["deleted_at"] is not None:
                raise StateError("削除済みブランドに改訂を足せません")
            if b["kind"] == "preset" and not _product_update:
                raise StateError("preset ブランドは編集できません（製品更新だけが改訂を足せる）")
            now = self.now()
            rev_id = self.new_id()
            next_rev = int(self._scalar("SELECT COALESCE(MAX(revision),0)+1 FROM brand_revisions WHERE brand_id=?", (brand_id,)))
            self._insert_brand_revision(brand_id, rev_id, next_rev, config, assets, now)
            self._exec("UPDATE brands SET current_revision_id=?,updated_at=? WHERE id=?", (rev_id, now, brand_id))
            self.append_event("brand_revision_added", {"brand_id": brand_id, "revision_id": rev_id, "revision": next_rev})
            return self.brand_revision(rev_id)

    def _insert_brand_revision(self, brand_id: str, rev_id: str, revision: int, config: dict, assets: list[dict], now: str) -> None:
        self._exec(
            "INSERT INTO brand_revisions(id,brand_id,revision,config_json,created_at) VALUES(?,?,?,?,?)",
            (rev_id, brand_id, revision, dump_json(config), now),
        )
        for a in assets:
            self._require_ready_asset(a["asset_id"], "brand_assets")
            self._exec(
                "INSERT INTO brand_assets(revision_id,asset_id,role,label) VALUES(?,?,?,?)",
                (rev_id, a["asset_id"], a["role"], a.get("label", "")),
            )

    def rename_brand(self, brand_id: str, name: str) -> None:
        if not name or not name.strip():
            raise ValidationError("brands.name は空にできない")
        with self.transaction():
            b = self._get("brands", brand_id)
            if b["kind"] == "preset":
                raise StateError("preset ブランドは編集できません")
            if b["deleted_at"] is not None:
                raise StateError("削除済みブランドは編集できません")
            self._exec("UPDATE brands SET name=?,updated_at=? WHERE id=?", (name, self.now(), brand_id))

    def delete_brand(self, brand_id: str) -> None:
        """論理削除。新規選択から除くだけで、過去のデッキ・版の参照は保持する。"""
        with self.transaction():
            b = self._get("brands", brand_id)
            if b["kind"] == "preset":
                raise StateError("preset ブランドは削除できません")
            if b["deleted_at"] is not None:
                return
            now = self.now()
            self._exec("UPDATE brands SET deleted_at=?,updated_at=? WHERE id=?", (now, now, brand_id))
            self.append_event("brand_deleted", {"brand_id": brand_id})

    def brand(self, brand_id: str) -> dict[str, Any]:
        b = self._get("brands", brand_id)
        b["current_revision"] = self.brand_revision(b["current_revision_id"]) if b["current_revision_id"] else None
        return b

    def brand_revision(self, revision_id: str) -> dict[str, Any]:
        r = self._get("brand_revisions", revision_id)
        r["config"] = load_json(r.pop("config_json"))
        r["assets"] = self._all(
            "SELECT ba.asset_id,ba.role,ba.label,a.relative_path,a.sha256,a.width,a.height,a.state"
            " FROM brand_assets ba JOIN assets a ON a.id=ba.asset_id WHERE ba.revision_id=? ORDER BY ba.role",
            (revision_id,),
        )
        return r

    def brand_revisions(self, brand_id: str) -> list[dict[str, Any]]:
        return [self.brand_revision(r["id"]) for r in self._all(
            "SELECT id FROM brand_revisions WHERE brand_id=? ORDER BY revision", (brand_id,))]

    def list_brands(self, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM brands"
        if not include_deleted:
            sql += " WHERE deleted_at IS NULL"
        # 同着（updated_at が同値）は挿入順 = rowid で決める。乱数 id で決めると並びが実行ごとに変わる
        sql += " ORDER BY kind DESC, updated_at DESC, rowid DESC"
        return self._all(sql)

    def _require_live_brand_revision(self, revision_id: str) -> dict[str, Any]:
        row = self._one(
            "SELECT br.*, b.deleted_at AS brand_deleted_at FROM brand_revisions br JOIN brands b ON b.id=br.brand_id WHERE br.id=?",
            (revision_id,),
        )
        if row is None:
            raise NotFoundError("brand_revision が存在しません")
        if row["brand_deleted_at"] is not None:
            raise StateError("削除済みブランドは新規に選べません")
        return row
