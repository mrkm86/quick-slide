"""取り込み（会話からの投入）。デッキ・取り込み・起動・描いた回・仕事を 1 つのトランザクションで作る。

payload の形は v1 と同じ（`schema_version`・`origin{kind,runtime,conversation_key}`・`manuscript`・`title_hint`・
`deck_attributes`・`attachments`・`append_to_deck_id`・`force_new_deck`）。v2 で足したのは
`deck_attributes` の `mode`（presentation / pitch）・`style`（エンジンのスタイル）・`image_look`・`images`、P3 で
`outline_fixed`（原稿は会話で合意した構成。台本係はページ割りを守る）。

秘匿の契約（v1 と同じ）:
- `origin.conversation_key` の生値はこのモジュールのローカル変数にだけ存在し、HMAC 化したら捨てる
- 原稿は `intakes.manuscript` にだけ置く。payload_json・events・ログに書かない

まだ受けないもの（`NotReadyError`。サーバは 400 `not_ready_in_v2` にする）: 既存のデッキへの追加
（`append_to_deck_id`）・会話抜粋・添付・書式。黙って捨てずに断る。

**送り直しは同じデッキを返す**（`RESEND_WINDOW_SECONDS` 以内に、同じ会話の識別子で、同じ原稿・題・属性が
もう一度届いたとき）。会話の側が応答を取りこぼして送り直しても、デッキが 2 冊にならない。返すのは前の仕事と、
そのデッキを開くための新しい起動（URL のトークンは 1 回しか使えないため）。`force_new_deck` なら必ず作る。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from ..engines import ENGINES, IMAGE_LOOKS
from ..enums import ORIGIN_KINDS, ORIGIN_RUNTIMES, require
from ..errors import QuickSlideError, StateError, ValidationError
from ..ids import PRESET_BRAND_ID, PRESET_STYLE_STANDARD_ID, PRESET_STYLE_VISUAL_ID
from ..security.hmac_key import conversation_hash
from ..security.secret_scan import find_secret_like
from .base import StoreBase, assert_no_forbidden_keys, dump_json, load_json, reject_unknown_keys

log = logging.getLogger("quick_slide.store.intakes")

PAYLOAD_KEYS = {"schema_version", "origin", "title_hint", "manuscript", "transcript_excerpt", "deck_attributes",
                "attachments", "append_to_deck_id", "force_new_deck"}
ORIGIN_KEYS = {"kind", "runtime", "conversation_key"}
DECK_ATTRIBUTE_KEYS = {"purpose", "audience", "style_note", "page_count", "brand_id", "mode", "style", "image_look",
                       "images", "outline_fixed", "style_id", "format_id", "format_values", "format_options"}
# 形は v1 のまま残し、v2 ではまだ受けない項目（値が入っていたら断る）
NOT_READY_ATTRIBUTES = ("style_id", "format_id", "format_values", "format_options")
MAX_PAGE_COUNT = 40
RESEND_WINDOW_SECONDS = 10 * 60
MAX_TEXT_ATTRIBUTE_CHARS = 2000


class NotReadyError(QuickSlideError):
    """形は正しいが、v2 ではまだ受けない項目。サーバは 400 `not_ready_in_v2` にする。"""

    exit_code = 2


class IntakesMixin(StoreBase):
    # --- 投入 ---
    def create_intake(self, payload: dict[str, Any], *, launch_requested_via: str | None = "start") -> dict[str, Any]:
        """payload を検査し、デッキ（モードを固定）・取り込み・起動・描いた回・仕事を作る。

        戻り値: {intake_id, deck_id, deck_title, mode, style, launch_id, launch_token, job_id, run_id, resolution,
        appended, resolution_reason}。`launch_token` は平文で、戻り値以外に残らない。
        """
        p = self._validate_intake(payload)
        attrs = p["deck_attributes"]
        runtime = p["origin"]["runtime"]
        raw_key = p["origin"].get("conversation_key")  # 生値。この関数のローカルだけ
        conv_hash = conversation_hash(self._hmac_key, runtime, raw_key) if raw_key is not None else None
        del raw_key
        stored = {k: v for k, v in p.items() if k not in ("manuscript", "transcript_excerpt")}
        stored["origin"] = {k: v for k, v in p["origin"].items() if k != "conversation_key"}
        assert_no_forbidden_keys(stored, "intakes.payload_json")
        request_sha = hashlib.sha256(json.dumps(
            [p["manuscript"], p.get("title_hint"), attrs], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

        with self.transaction():
            now = self.now()
            if conv_hash is not None and not p["force_new_deck"]:
                again = self._resent_intake(conv_hash, request_sha, now)
                if again is not None:
                    return self._answer_resent(again, launch_requested_via, p["origin"]["kind"])
            title = (p.get("title_hint") or "").strip() or f"無題のスライド（{now[:10]} {now[11:16]} UTC）"
            deck = self.create_deck(
                title, mode=attrs["mode"],
                brand_revision_id=self._intake_brand_revision(attrs.get("brand_id")),
                style_revision_id=self.preset_style_revision_id(
                    PRESET_STYLE_VISUAL_ID if attrs["style"] in ("visual", "infographic") else PRESET_STYLE_STANDARD_ID),
                purpose=attrs.get("purpose") or "", audience=attrs.get("audience") or "",
            )
            deck_id = deck["id"]
            launch_id = launch_token = None
            if launch_requested_via is not None and p["origin"]["kind"] != "headless":
                launch = self.create_launch(requested_via=launch_requested_via, deck_id=deck_id)
                launch_id, launch_token = launch["id"], launch["token"]
            intake_id = self.new_id()
            self._exec(
                "INSERT INTO intakes(id,deck_id,origin_kind,origin_runtime,conversation_hash,request_sha256,title_hint,"
                " manuscript,payload_json,launch_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (intake_id, deck_id, p["origin"]["kind"], runtime, conv_hash, request_sha, p.get("title_hint"),
                 p["manuscript"], dump_json(stored), launch_id, now))
            run = self.create_run(deck_id, args=self.run_args_from_attributes(attrs, origin_runtime=runtime))
            job = self.create_job(deck_id, run_id=run["id"], intake_id=intake_id)
            self.append_event("intake_created", {
                "intake_id": intake_id, "origin_kind": p["origin"]["kind"], "origin_runtime": runtime,
                "launch_id": launch_id, "job_id": job["id"], "run_id": run["id"], "mode": attrs["mode"],
                "style": attrs["style"], "manuscript_chars": len(p["manuscript"]),
                "has_conversation_key": conv_hash is not None,
            }, deck_id=deck_id)
        log.info("intake %s -> deck %s (%s/%s)", intake_id, deck_id, attrs["mode"], attrs["style"])
        return {"intake_id": intake_id, "deck_id": deck_id, "deck_title": title, "mode": attrs["mode"],
                "style": attrs["style"], "launch_id": launch_id, "launch_token": launch_token,
                "job_id": job["id"], "run_id": run["id"], "resolution": "new", "appended": False,
                "resolution_reason": None}

    def _resent_intake(self, conv_hash: str, request_sha: str, now: str) -> dict[str, Any] | None:
        from ..clock import add_seconds

        since = add_seconds(now, -RESEND_WINDOW_SECONDS)
        return self._one(
            "SELECT i.id,i.deck_id,d.title FROM intakes i JOIN decks d ON d.id=i.deck_id"
            " WHERE i.conversation_hash=? AND i.request_sha256=? AND i.created_at>=? AND d.deleted_at IS NULL"
            " ORDER BY i.created_at DESC,i.rowid DESC LIMIT 1", (conv_hash, request_sha, since))

    def _answer_resent(self, again: dict[str, Any], launch_requested_via: str | None, kind: str) -> dict[str, Any]:
        self._require_tx()
        job = self._one("SELECT id,run_id FROM jobs WHERE intake_id=? ORDER BY created_at,rowid LIMIT 1", (again["id"],))
        run = self.run(job["run_id"]) if job else None
        launch_id = launch_token = None
        if launch_requested_via is not None and kind != "headless":
            launch = self.create_launch(requested_via=launch_requested_via, deck_id=again["deck_id"])
            launch_id, launch_token = launch["id"], launch["token"]
        log.info("intake resent -> deck %s", again["deck_id"])
        return {"intake_id": again["id"], "deck_id": again["deck_id"], "deck_title": again["title"],
                "mode": run["mode"] if run else None, "style": run["args"].get("style") if run else None,
                "launch_id": launch_id, "launch_token": launch_token, "job_id": job["id"] if job else None,
                "run_id": run["id"] if run else None, "resolution": "resent", "appended": False,
                "resolution_reason": None}

    @staticmethod
    def run_args_from_attributes(attrs: dict[str, Any], *, origin_runtime: str) -> dict[str, Any]:
        """描いた回に残す「何をどう頼まれたか」（台本係とエンジンに渡す引数の元）。原稿は入れない。"""
        return {
            "mode": attrs["mode"], "style": attrs["style"], "image_look": attrs.get("image_look"),
            "images": attrs["images"], "page_count": attrs.get("page_count"),
            "purpose": attrs.get("purpose") or "", "audience": attrs.get("audience") or "",
            "style_note": attrs.get("style_note") or "", "origin_runtime": origin_runtime,
            "outline_fixed": bool(attrs.get("outline_fixed")),
        }

    # --- 検査 ---
    def _validate_intake(self, payload: dict[str, Any]) -> dict[str, Any]:
        cfg = self.settings.intake
        if not isinstance(payload, dict):
            raise ValidationError("intake の payload はオブジェクト")
        reject_unknown_keys(payload, PAYLOAD_KEYS, "intake")
        if payload.get("schema_version") != 1:
            raise ValidationError("intake.schema_version は 1")
        origin = payload.get("origin")
        if not isinstance(origin, dict):
            raise ValidationError("intake.origin は必須")
        reject_unknown_keys(origin, ORIGIN_KEYS, "intake.origin")
        require(origin.get("kind"), ORIGIN_KINDS, "intake.origin.kind")
        require(origin.get("runtime"), ORIGIN_RUNTIMES, "intake.origin.runtime")
        key = origin.get("conversation_key")
        if key is not None:
            if not isinstance(key, str):
                raise ValidationError("intake.origin.conversation_key は文字列か null")
            if len(key) < cfg.min_conversation_key_chars:
                # 値は決してメッセージに入れない
                raise ValidationError(f"intake.origin.conversation_key は {cfg.min_conversation_key_chars} 文字以上")
        title = payload.get("title_hint")
        if title is not None and not isinstance(title, str):
            raise ValidationError("intake.title_hint は文字列か null")
        if isinstance(title, str) and len(title) > 200:
            raise ValidationError("タイトルは 200 文字以内にしてください")
        manuscript = payload.get("manuscript")
        if not isinstance(manuscript, str) or not manuscript.strip():
            raise ValidationError("原稿（manuscript）が空です。会話から整えた原稿を入れてください")
        if len(manuscript) > cfg.max_manuscript_chars:
            raise ValidationError(f"原稿が上限 {cfg.max_manuscript_chars} 字を超えています（黙って切りません）")
        if find_secret_like(manuscript, cfg.extra_secret_patterns):
            raise ValidationError("原稿に秘密情報らしい文字列が含まれています。除いて再投入してください")
        if payload.get("transcript_excerpt") is not None:
            raise NotReadyError("会話抜粋（transcript_excerpt）は v2 ではまだ受け付けません。原稿だけで送ってください。")
        atts = payload.get("attachments")
        if atts is not None and not isinstance(atts, list):
            raise ValidationError("intake.attachments は配列")
        if atts:
            raise NotReadyError("画像の添付（attachments）は v2 ではまだ受け付けません。画像なしで送ってください。")
        if payload.get("append_to_deck_id") is not None:
            raise NotReadyError("既存のスライドへの追加（append_to_deck_id）は v2 ではまだできません。新しいスライドとして送ってください。")
        force_new = payload.get("force_new_deck", False)
        if not isinstance(force_new, bool):
            raise ValidationError("intake.force_new_deck は真偽値")
        attrs = self._validate_attributes(payload.get("deck_attributes"), manuscript=manuscript)
        out = dict(payload)
        out["deck_attributes"] = attrs
        out["attachments"] = []
        out["force_new_deck"] = force_new
        return out

    def _validate_attributes(self, raw: Any, *, manuscript: str = "") -> dict[str, Any]:
        cfg = self.settings.intake
        attrs = {} if raw is None else raw
        if not isinstance(attrs, dict):
            raise ValidationError("intake.deck_attributes はオブジェクト")
        reject_unknown_keys(attrs, DECK_ATTRIBUTE_KEYS, "intake.deck_attributes")
        for k in NOT_READY_ATTRIBUTES:
            if attrs.get(k) not in (None, "", "auto", {}, []):
                raise NotReadyError(f"deck_attributes.{k} は v2 ではまだ使えません。外して送ってください。")
        out = {k: v for k, v in attrs.items() if k not in NOT_READY_ATTRIBUTES}
        for k in ("purpose", "audience", "style_note"):
            v = out.get(k)
            if v is None:
                continue
            if not isinstance(v, str):
                raise ValidationError(f"intake.deck_attributes.{k} は文字列")
            limit = cfg.max_style_chars if k == "style_note" else MAX_TEXT_ATTRIBUTE_CHARS
            if len(v) > limit:
                raise ValidationError(f"deck_attributes.{k} が上限 {limit} 字を超えています（黙って切りません）")
            if find_secret_like(v, cfg.extra_secret_patterns):
                raise ValidationError(f"deck_attributes.{k} に秘密情報らしい文字列が含まれています。除いて再投入してください")
        mode = out.get("mode") or "presentation"
        if mode not in ENGINES:
            raise ValidationError("deck_attributes.mode は presentation（編集できて動く PPTX）か pitch（1枚ずつ絵で描く）")
        engine = ENGINES[mode]
        style = out.get("style") or engine.default_style
        if style not in engine.styles:
            raise ValidationError(f"deck_attributes.style はこのモードでは {' / '.join(engine.styles)} のいずれか")
        look = out.get("image_look")
        if look is not None and look not in IMAGE_LOOKS:
            raise ValidationError(f"deck_attributes.image_look は {' / '.join(IMAGE_LOOKS)} のいずれか")
        images = out.get("images", True)
        if not isinstance(images, bool):
            raise ValidationError("deck_attributes.images は真偽値（画像の枠を置くか）")
        outline_fixed = out.get("outline_fixed", False)
        if not isinstance(outline_fixed, bool):
            raise ValidationError("deck_attributes.outline_fixed は真偽値（原稿は合意した構成か）")
        if outline_fixed:
            from ..scriptdoc import outline_headings

            heads = outline_headings(manuscript)
            if not heads:
                raise ValidationError("構成を固定するときは、原稿にページごとの見出し（`## 1. 見出し`）を書いてください")
            if [n for n, _ in heads] != list(range(1, len(heads) + 1)):
                raise ValidationError("構成の見出しの番号は 1 から順に書いてください（`## 1.`・`## 2.` …）")
            if len(heads) > MAX_PAGE_COUNT:
                raise ValidationError(f"構成のページは {MAX_PAGE_COUNT} 枚までです")
        pc = out.get("page_count")
        if pc is not None and (isinstance(pc, bool) or not isinstance(pc, int) or not (1 <= pc <= MAX_PAGE_COUNT)):
            raise ValidationError(f"deck_attributes.page_count は 1〜{MAX_PAGE_COUNT} の整数")
        brand_id = out.get("brand_id")
        if brand_id is not None and not isinstance(brand_id, str):
            raise ValidationError("deck_attributes.brand_id は文字列")
        out.update(mode=mode, style=style, image_look=look, images=images, outline_fixed=outline_fixed)
        return out

    def _intake_brand_revision(self, brand_id: str | None) -> str:
        """明示の brand_id → 設定の既定 → 利用者のブランド（最新）→ 既定（無彩色）。P4 で描く回に効かせる。"""
        if brand_id is not None:
            b = self._one("SELECT current_revision_id FROM brands WHERE id=? AND deleted_at IS NULL", (brand_id,))
            if b is None:
                raise ValidationError("deck_attributes.brand_id のブランドが見つかりません")
            return b["current_revision_id"]
        if self.settings.default_brand_id:
            b = self._one("SELECT current_revision_id FROM brands WHERE id=? AND deleted_at IS NULL",
                          (self.settings.default_brand_id,))
            if b is not None:
                return b["current_revision_id"]
        user = self._one("SELECT current_revision_id FROM brands WHERE kind='user' AND deleted_at IS NULL"
                         " ORDER BY updated_at DESC,rowid DESC LIMIT 1")
        if user is not None:
            return user["current_revision_id"]
        rev = self._scalar("SELECT current_revision_id FROM brands WHERE id=?", (PRESET_BRAND_ID,))
        if rev is None:
            raise StateError("既定ブランドが初期化されていません")
        return rev

    # --- 参照 ---
    def intake(self, intake_id: str) -> dict[str, Any]:
        row = self._get("intakes", intake_id)
        row["payload"] = load_json(row.pop("payload_json"))
        return row
